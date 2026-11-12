"""
documents.py

Document management endpoints for LifeOS Phase 11.
Wraps existing ingestion, validation, and removal lifecycles.
"""

from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status

from app.api.dependencies import require_admin, require_auth
from app.api.schemas import (
    DocumentDeleteResponse,
    DocumentInfo,
    DocumentListResponse,
    DocumentUploadResponse,
)

try:
    from config import DOCUMENTS_DIR, SUPPORTED_EXTENSIONS, get_max_file_size_bytes
except ImportError:
    from app.config import DOCUMENTS_DIR, SUPPORTED_EXTENSIONS, get_max_file_size_bytes

from app.ingestion.guardrails import validate_document_path
from app.ingestion.ingest import ingest_file, remove_file
from app.knowledge.document_registry import get_all_documents

router = APIRouter(prefix="/api/documents", tags=["Documents"])


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List Registered Documents",
    description="Returns metadata for all documents registered in the LifeOS registry.",
)
async def list_documents(_user=Depends(require_auth)):
    docs = get_all_documents()
    formatted = [
        DocumentInfo(
            filename=d.get("filename", ""),
            file_size=d.get("file_size"),
            file_hash=d.get("file_hash"),
            last_modified=d.get("last_modified"),
            status=d.get("status"),
        )
        for d in docs
    ]
    return DocumentListResponse(documents=formatted, count=len(formatted))


@router.post(
    "",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload & Ingest Document",
    description="Uploads a document, verifies size/path guardrails, and triggers ingestion.",
)
async def upload_document(
    file: UploadFile = File(...),
    _user=Depends(require_auth),
):
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file must have a valid filename",
        )

    # Sanitize filename (strictly basename, no directory components)
    safe_name = Path(file.filename).name
    if not safe_name or safe_name in {".", ".."}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename",
        )

    ext = Path(safe_name).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format '{ext}'. Supported formats: {sorted(SUPPORTED_EXTENSIONS)}",
        )

    try:
        import config
        docs_dir = Path(getattr(config, "DOCUMENTS_DIR", DOCUMENTS_DIR))
    except ImportError:
        docs_dir = Path(DOCUMENTS_DIR)

    target_path = docs_dir / safe_name
    max_bytes = get_max_file_size_bytes(target_path)

    # Stream file to disk while enforcing max_bytes limit
    total_bytes = 0
    target_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(target_path, "wb") as out_file:
            while chunk := await file.read(64 * 1024):
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    out_file.close()
                    target_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds maximum allowed size for its type ({max_bytes} bytes)",
                    )
                out_file.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        target_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save uploaded file: {exc}",
        )

    # Execute existing ingestion pipeline
    try:
        result = ingest_file(target_path)
        chunks_count = result.get("chunks") if isinstance(result, dict) else None
        return DocumentUploadResponse(
            filename=safe_name,
            status="ingested",
            message=f"Document '{safe_name}' successfully ingested into LifeOS.",
            chunks=chunks_count,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ingestion failed: {exc}",
        )


@router.delete(
    "/{filename}",
    response_model=DocumentDeleteResponse,
    summary="Delete Document",
    description="Removes document from ChromaDB, face embeddings, registry, and disk. Requires admin privileges.",
)
async def delete_document(
    filename: str,
    _admin=Depends(require_admin),
):
    safe_name = Path(filename).name
    try:
        import config
        docs_dir = Path(getattr(config, "DOCUMENTS_DIR", DOCUMENTS_DIR))
    except ImportError:
        docs_dir = Path(DOCUMENTS_DIR)

    target_path = docs_dir / safe_name

    # Validate path containment
    try:
        valid_path = validate_document_path(target_path, base_dir=docs_dir, must_exist=False)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if not valid_path.exists():
        # Check if it exists in document registry
        from app.knowledge.document_registry import get_document
        doc = get_document(valid_path)
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document '{safe_name}' not found",
            )

    # Perform existing coordinated removal
    removed = remove_file(valid_path)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to remove document '{safe_name}' from one or more stores",
        )

    # Remove physical file if still present
    try:
        valid_path.unlink(missing_ok=True)
    except Exception:
        pass

    return DocumentDeleteResponse(
        filename=safe_name,
        status="deleted",
        message=f"Document '{safe_name}' successfully removed from all LifeOS stores.",
    )
