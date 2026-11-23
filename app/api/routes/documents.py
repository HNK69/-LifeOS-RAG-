"""
documents.py

Document management endpoints for LifeOS Phase 11.
Wraps existing ingestion, validation, and removal lifecycles.
"""

from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, status
from fastapi.responses import JSONResponse

from app.api.dependencies import (
    RequestContext,
    get_request_context,
    require_admin,
    require_auth,
)
from app.api.schemas import (
    DocumentDeleteResponse,
    DocumentInfo,
    DocumentListResponse,
    DocumentUploadResponse,
    IngestionJobCreateResponse,
    IngestionJobResponse,
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
async def list_documents(
    ctx: RequestContext = Depends(get_request_context),
    _user=Depends(require_auth),
):
    docs = get_all_documents(workspace_id=ctx.workspace_id)
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
    description="Uploads a document, verifies size/path guardrails, and triggers ingestion (or queues if async=true).",
)
async def upload_document(
    file: UploadFile = File(...),
    async_ingest: bool = Query(False, alias="async"),
    ctx: RequestContext = Depends(get_request_context),
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
    try:
        valid_path = validate_document_path(target_path, base_dir=docs_dir, must_exist=False)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid document target path: {exc}",
        )

    max_bytes = get_max_file_size_bytes(valid_path)

    # Stream file to disk while enforcing max_bytes limit
    total_bytes = 0
    valid_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(valid_path, "wb") as out_file:
            while chunk := await file.read(64 * 1024):
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    out_file.close()
                    valid_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds maximum allowed size for its type ({max_bytes} bytes)",
                    )
                out_file.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        valid_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save uploaded file: {exc}",
        )

    # Opt-in asynchronous queueing
    if async_ingest:
        from app.ingestion.queue import enqueue_job
        job_id = enqueue_job(valid_path, workspace_id=ctx.workspace_id)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "job_id": job_id,
                "filename": safe_name,
                "status": "queued",
                "message": f"Document '{safe_name}' queued for ingestion.",
                "chunks": None,
            },
        )

    # Default synchronous ingestion pipeline (HTTP 201)
    try:
        import inspect
        sig = inspect.signature(ingest_file)
        params = sig.parameters
        has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        kwargs = {}
        if "workspace_id" in params or has_varkw:
            kwargs["workspace_id"] = ctx.workspace_id
        if "owner_id" in params or has_varkw:
            kwargs["owner_id"] = ctx.user_id
        result = ingest_file(valid_path, **kwargs)
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


@router.post(
    "/jobs",
    response_model=IngestionJobCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload & Queue Document for Asynchronous Ingestion",
    description="Uploads a document, verifies size/path guardrails, and places it into the persistent ingestion queue.",
)
async def upload_and_queue_document(
    file: UploadFile = File(...),
    priority: int = Query(0),
    ctx: RequestContext = Depends(get_request_context),
    _user=Depends(require_auth),
):
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file must have a valid filename",
        )

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
    try:
        valid_path = validate_document_path(target_path, base_dir=docs_dir, must_exist=False)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid document target path: {exc}",
        )

    max_bytes = get_max_file_size_bytes(valid_path)
    total_bytes = 0
    valid_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(valid_path, "wb") as out_file:
            while chunk := await file.read(64 * 1024):
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    out_file.close()
                    valid_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds maximum allowed size for its type ({max_bytes} bytes)",
                    )
                out_file.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        valid_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save uploaded file: {exc}",
        )

    from app.ingestion.queue import enqueue_job
    job_id = enqueue_job(valid_path, priority=priority, workspace_id=ctx.workspace_id)
    return IngestionJobCreateResponse(
        job_id=job_id,
        status="queued",
        filename=safe_name,
        message=f"Document '{safe_name}' queued for ingestion.",
    )


@router.get(
    "/jobs/{job_id}",
    response_model=IngestionJobResponse,
    summary="Get Ingestion Job Status",
    description="Polls the status of an asynchronous document ingestion job.",
)
async def get_job_status(
    job_id: str,
    _user=Depends(require_auth),
):
    from app.ingestion.queue import get_job
    job = get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ingestion job '{job_id}' not found",
        )
    return IngestionJobResponse(**job)


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
