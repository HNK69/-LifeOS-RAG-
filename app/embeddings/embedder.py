"""
embedder.py

Generates vector embeddings for LifeOS documents.
Lazy-loads the embedding model using the canonical EMBEDDING_MODEL setting
from config.py to avoid heavy import side effects or network calls during startup/testing.
"""

try:
    from config import EMBEDDING_MODEL
except ImportError:
    from app.config import EMBEDDING_MODEL


_model = None


def get_embedding_model():
    """Lazily load and return the SentenceTransformer model instance."""
    global _model
    if _model is None:
        try:
            import config
            model_name = getattr(config, "EMBEDDING_MODEL", EMBEDDING_MODEL)
        except ImportError:
            try:
                from app import config
                model_name = getattr(config, "EMBEDDING_MODEL", EMBEDDING_MODEL)
            except ImportError:
                model_name = EMBEDDING_MODEL

        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(model_name)
    return _model


def generate_embeddings(chunks):
    """Convert text chunks into vector embeddings."""
    model = get_embedding_model()
    embeddings = model.encode(chunks)
    return embeddings
