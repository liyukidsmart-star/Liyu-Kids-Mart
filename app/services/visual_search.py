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
import urllib.request
import urllib.error
import socket

# --- PATCH FOR VERCEL/AWS LAMBDA PYTHON 3.12 EBUSY ERROR ---
# Python 3.12 on Lambda sometimes throws [Errno 16] Device or resource busy 
# during getaddrinfo for dual-stack (IPv6) domains. Forcing IPv4 prevents this.
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
socket.getaddrinfo = _ipv4_getaddrinfo
# -----------------------------------------------------------

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


_pinecone_client = None
_pinecone_index = None


def _get_pinecone_index():
    """Return a Pinecone Index object using a globally cached client and index."""
    global _pinecone_client, _pinecone_index

    api_key = _pinecone_api_key()
    index_name = _pinecone_index_name()
    if not api_key:
        raise RuntimeError("PINECONE_API_KEY is not set")
    if not index_name:
        raise RuntimeError("PINECONE_INDEX is not set")

    try:
        from pinecone import Pinecone  # lazy import so missing package doesn't crash startup
    except Exception as exc:
        raise RuntimeError(f"Pinecone package is unavailable: {exc}") from exc

    if _pinecone_client is None:
        _pinecone_client = Pinecone(api_key=api_key)
    if _pinecone_index is None:
        _pinecone_index = _pinecone_client.Index(index_name)
    return _pinecone_index


def _urllib_request(url: str, data: bytes = None, headers: dict = None, timeout: int = 30) -> tuple[int, bytes, dict]:
    """Simple HTTP request using stdlib urllib — works reliably in Vercel serverless."""
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


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

    result = None
    # HF cold-starts take up to 20 s — retry once after a brief wait
    for attempt in range(3):
        status, body, _ = _urllib_request(
            HF_FEATURE_EXTRACTION_URL,
            data=image_bytes,
            headers=headers,
            timeout=60,
        )
        if status == 503 and attempt < 2:
            logger.info("HF model loading (503) — retrying in 15 s… (attempt %d)", attempt + 1)
            time.sleep(15)
            continue
        if status != 200:
            raise RuntimeError(
                f"HF embedding failed ({status}): {body[:300].decode('utf-8', errors='replace')}"
            )
        result = json.loads(body)
        break

    if result is None:
        raise RuntimeError("HF embedding failed after all retries")

    # The API returns either [[float, ...]] or [float, ...]
    if isinstance(result, list):
        if result and isinstance(result[0], list):
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
    req = urllib.request.Request(image_url, headers={"User-Agent": "LiyuKidsMart/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            image_bytes = resp.read()
            content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Could not download image from {image_url}: HTTP {e.code}")
    except Exception as e:
        raise RuntimeError(f"Could not download image from {image_url}: {e}")
    return embed_image_bytes(image_bytes, content_type or "image/jpeg")


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


import httpx
import json

class HFRateLimitError(Exception):
    pass

class RetryBatchError(Exception):
    pass

_http_client = None
def _get_http_client():
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(timeout=30.0, limits=httpx.Limits(max_keepalive_connections=5, max_connections=10))
    return _http_client

def bulk_index_all_products(app, offset: int = 0, limit: int = 5, batch_delay: float = 0.5) -> dict:
    """
    Index a batch of active products that have at least one image.
    Call inside a Flask app context.
    Returns a summary dict with pagination state.
    """
    from app.models.product import Product
    import time

    total_products = Product.query.filter_by(is_active=True).count()
    products = Product.query.filter_by(is_active=True).offset(offset).limit(limit).all()
    
    ok = 0
    skipped = 0
    errors = []

    try:
        index = _get_pinecone_index()
    except Exception as exc:
        logger.exception("Pinecone init error")
        return {"indexed": 0, "skipped": 0, "errors": [{"product_id": 0, "error": f"Pinecone init error: {exc}"}], "total": total_products, "done": True, "next_offset": offset}

    token = _hf_token()
    client = _get_http_client()
    
    for p in products:
        img_url = p.primary_image()
        if not img_url or "placeholder" in img_url:
            skipped += 1
            continue
        
        try:
            # Handle relative URLs (like /api/v1/image/...)
            if img_url.startswith('/'):
                from flask import request
                img_url = request.host_url.rstrip('/') + img_url

            # 1. Download image with retries
            resp = None
            for attempt in range(3):
                try:
                    resp = client.get(img_url, follow_redirects=True)
                    if resp.status_code == 200:
                        break
                    raise RuntimeError(f"HTTP {resp.status_code}")
                except Exception as e:
                    if attempt == 2:
                        raise RetryBatchError(f"Image download failed: {e}")
                    time.sleep(1.0)
            
            content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]

            # 2. Embed via Hugging Face with retries
            headers = {"Authorization": f"Bearer {token}", "Content-Type": content_type}
            result = None
            for attempt in range(3):
                try:
                    hf_resp = client.post(HF_FEATURE_EXTRACTION_URL, content=resp.content, headers=headers)
                    if hf_resp.status_code == 200:
                        result = hf_resp.json()
                        break
                    if hf_resp.status_code == 503:
                        logger.info("HF 503 Service Unavailable (Model Loading) - aborting batch")
                        raise HFRateLimitError("AI is warming up.")
                    
                    raise RuntimeError(f"HTTP {hf_resp.status_code}: {hf_resp.text[:100]}")
                except HFRateLimitError:
                    raise
                except Exception as e:
                    if attempt == 2:
                        raise RetryBatchError(f"HF embedding request failed: {e}")
                    time.sleep(1.0)
            
            if isinstance(result, list):
                embedding = result[0] if isinstance(result[0], list) else result
            else:
                # If we get a weird shape, it's a code bug, just record error and skip product
                raise RuntimeError(f"Unexpected HF response shape: {type(result)}")
            
            embedding = [float(v) for v in embedding]
            
            # 3. Upsert to Pinecone with retries
            sku = p.sku or f"P-{p.id}"
            for attempt in range(3):
                try:
                    index.upsert(vectors=[{
                        "id": str(p.id),
                        "values": embedding,
                        "metadata": {"product_id": p.id, "sku": sku},
                    }])
                    break
                except Exception as e:
                    if attempt == 2:
                        raise RetryBatchError(f"Pinecone upsert failed: {e}")
                    time.sleep(1.0)
            
            ok += 1
            time.sleep(batch_delay)
            
        except HFRateLimitError as exc:
            return {
                "indexed": ok,
                "skipped": skipped,
                "errors": errors,
                "total": total_products,
                "done": False,
                "next_offset": offset + ok + skipped,
                "retry_after": 15,
                "message": str(exc)
            }
        except RetryBatchError as exc:
            logger.error(f"RetryBatchError: {exc}")
            return {
                "indexed": ok,
                "skipped": skipped,
                "errors": errors,
                "total": total_products,
                "done": False,
                "next_offset": offset + ok + skipped,
                "retry_after": 2,
                "message": f"Temporary network hiccup ({str(exc)[:50]}), resuming..."
            }
        except Exception as exc:
            logger.exception("Failed to index product %d", p.id)
            errors.append({"product_id": p.id, "error": str(exc)})

    is_done = (offset + limit) >= total_products
    return {
        "indexed": ok,
        "skipped": skipped,
        "errors": errors,
        "total": total_products,
        "done": is_done,
        "next_offset": offset + limit
    }
