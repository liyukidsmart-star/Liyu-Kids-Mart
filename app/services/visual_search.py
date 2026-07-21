"""
Visual Search Service — Liyu Kids Mart
Handles CLIP embedding generation via HuggingFace and Pinecone vector search.

Environment variables required:
  HF_TOKEN        — HuggingFace API token (free account)
  PINECONE_API_KEY — Pinecone API key
  PINECONE_INDEX  — Name of Pinecone index (dimension=512, metric=cosine)
"""
import io
import json
import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

# HuggingFace CLIP model for image embeddings (512-dim)
HF_CLIP_MODEL = "openai/clip-vit-base-patch32"
HF_FEATURE_EXTRACTION_URL = (
    f"https://api-inference.huggingface.co/models/{HF_CLIP_MODEL}"
)
CONFIDENCE_THRESHOLD = 0.50  # minimum cosine similarity to accept a match


def _hf_token() -> str:
    return os.environ.get("HF_TOKEN", "").strip()


def _pinecone_api_key() -> str:
    return os.environ.get("PINECONE_API_KEY", "").strip()


def _pinecone_index_name() -> str:
    return os.environ.get("PINECONE_INDEX", "").strip()


def is_configured() -> bool:
    """Return True if all required environment variables are set."""
    return bool(_hf_token() and _pinecone_api_key() and _pinecone_index_name())


def _get_pinecone_index():
    """Return a Pinecone Index object."""
    from pinecone import Pinecone  # lazy import so missing package doesn't crash startup
    pc = Pinecone(api_key=_pinecone_api_key())
    return pc.Index(_pinecone_index_name())


def embed_image_bytes(image_bytes: bytes, content_type: str = "image/jpeg") -> list[float]:
    """
    Call HuggingFace feature-extraction API on raw image bytes.
    Returns a 512-dimensional embedding vector.
    Raises RuntimeError on failure.
    """
    token = _hf_token()
    if not token:
        raise RuntimeError("HF_TOKEN not set")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": content_type,
    }

    # HF cold-starts take up to 20 s — retry once after a brief wait
    for attempt in range(2):
        resp = httpx.post(
            HF_FEATURE_EXTRACTION_URL,
            content=image_bytes,
            headers=headers,
            timeout=30,
        )
        if resp.status_code == 503 and attempt == 0:
            logger.info("HF model loading (503) — retrying in 10 s…")
            time.sleep(10)
            continue
        if resp.status_code != 200:
            raise RuntimeError(
                f"HF embedding failed ({resp.status_code}): {resp.text[:300]}"
            )
        result = resp.json()
        break

    # The API returns either [[float, ...]] or [float, ...]
    if isinstance(result, list):
        if isinstance(result[0], list):
            embedding = result[0]
        else:
            embedding = result
    else:
        raise RuntimeError(f"Unexpected HF response shape: {type(result)}")

    return [float(v) for v in embedding]


def embed_image_url(image_url: str) -> list[float]:
    """
    Download an image from a URL and embed it.
    """
    resp = httpx.get(image_url, follow_redirects=True, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"Could not download image from {image_url}: {resp.status_code}")
    content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
    return embed_image_bytes(resp.content, content_type)


def upsert_product(product_id: int, sku: str, embedding: list[float]) -> None:
    """Upsert a single product vector into Pinecone."""
    index = _get_pinecone_index()
    vector_id = str(product_id)
    index.upsert(vectors=[{
        "id": vector_id,
        "values": embedding,
        "metadata": {
            "product_id": product_id,
            "sku": sku or vector_id,
        },
    }])
    logger.info("Upserted product %d (%s) into Pinecone", product_id, sku)


def delete_product(product_id: int) -> None:
    """Remove a product vector from Pinecone."""
    try:
        index = _get_pinecone_index()
        index.delete(ids=[str(product_id)])
    except Exception as exc:
        logger.warning("Could not delete product %d from Pinecone: %s", product_id, exc)


def query_image_bytes(image_bytes: bytes, content_type: str = "image/jpeg", top_k: int = 1):
    """
    Embed the image and query Pinecone.
    Returns list of (product_id, sku, score) tuples sorted by score desc.
    """
    embedding = embed_image_bytes(image_bytes, content_type)
    index = _get_pinecone_index()
    result = index.query(vector=embedding, top_k=top_k, include_metadata=True)
    matches = result.get("matches", [])
    out = []
    for m in matches:
        meta = m.get("metadata", {})
        pid_raw = meta.get("product_id") or m.get("id", "0")
        try:
            pid = int(pid_raw)
        except (ValueError, TypeError):
            pid = 0
        out.append({
            "product_id": pid,
            "sku": meta.get("sku", ""),
            "score": float(m.get("score", 0)),
        })
    return out


def index_product_from_url(product_id: int, sku: str, image_url: str) -> None:
    """Convenience helper: download → embed → upsert."""
    embedding = embed_image_url(image_url)
    upsert_product(product_id, sku, embedding)


def bulk_index_all_products(app, batch_delay: float = 0.5) -> dict:
    """
    Index every active product that has at least one image.
    Call inside a Flask app context.
    Returns a summary dict.
    """
    from app.models.product import Product

    products = Product.query.filter_by(is_active=True).all()
    ok = 0
    skipped = 0
    errors = []

    for p in products:
        img_url = p.primary_image()
        if not img_url or "placeholder" in img_url:
            skipped += 1
            continue
        try:
            index_product_from_url(p.id, p.sku or f"P-{p.id}", img_url)
            ok += 1
            time.sleep(batch_delay)  # be polite to HF rate limits
        except Exception as exc:
            logger.error("Failed to index product %d: %s", p.id, exc)
            errors.append({"product_id": p.id, "error": str(exc)})

    return {"indexed": ok, "skipped": skipped, "errors": errors, "total": len(products)}
