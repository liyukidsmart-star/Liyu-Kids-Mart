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
import urllib3
import socket

# --- PATCH FOR VERCEL DNS RATE LIMITING ---
# Vercel's DNS resolver returns [Errno -5] EAI_NODATA if we make too many lookups.
# We cache the DNS resolution to prevent hitting this limit.
_orig_getaddrinfo = socket.getaddrinfo
_dns_cache = {}

def _cached_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    cache_key = (host, port, family, type, proto, flags)
    if cache_key in _dns_cache:
        return _dns_cache[cache_key]
    
    res = _orig_getaddrinfo(host, port, family, type, proto, flags)
    _dns_cache[cache_key] = res
    return res

socket.getaddrinfo = _cached_getaddrinfo
# ------------------------------------------

logger = logging.getLogger(__name__)

# Global connection pool to prevent TIME_WAIT socket exhaustion in serverless
_http_pool = None

def _get_http_pool():
    global _http_pool
    if _http_pool is None:
        _http_pool = urllib3.PoolManager(
            maxsize=10, 
            retries=urllib3.Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        )
    return _http_pool

# HuggingFace CLIP model for image embeddings (512-dim)
HF_CLIP_MODEL = "openai/clip-vit-base-patch32"
HF_DEFAULT_INFERENCE_API_URL = "https://api-inference.huggingface.co/models"
HF_DEFAULT_FALLBACK_INFERENCE_API_URLS = [
    "https://router.huggingface.co/hf-inference/models",
]
CONFIDENCE_THRESHOLD = 0.50  # minimum cosine similarity to accept a match


def _hf_token() -> str:
    return os.environ.get("HF_TOKEN", "").strip()


def _hf_model() -> str:
    return os.environ.get("HF_CLIP_MODEL", HF_CLIP_MODEL).strip()


def _hf_inference_urls() -> list[str]:
    primary = os.environ.get("HF_INFERENCE_API_URL", HF_DEFAULT_INFERENCE_API_URL).rstrip("/")
    fallback_urls = os.environ.get("HF_INFERENCE_FALLBACK_URLS", "").split(",")
    fallback_urls = [url.strip().rstrip("/") for url in fallback_urls if url.strip()]
    if not fallback_urls:
        fallback_urls = HF_DEFAULT_FALLBACK_INFERENCE_API_URLS
    return [primary] + fallback_urls


def _hf_inference_url() -> str:
    return f"{_hf_inference_urls()[0]}/{_hf_model()}"


def _prepare_image_url_for_fetch(image_url: str) -> str:
    """
    Rewrite media URLs for server-side fetching.
    Prefer the local app media proxy instead of an external CDN/worker URL.
    Resolve relative URLs to APP_URL when needed.
    """
    image_url = (image_url or '').strip()
    if not image_url:
        return image_url

    rewritten = image_url
    try:
        from app.services.image_delivery import rewrite_media_url
        rewritten = rewrite_media_url(image_url, prefer_cdn=False)
    except Exception:
        rewritten = image_url

    if rewritten.startswith('/') and not rewritten.startswith('//'):
        try:
            from flask import has_request_context, request
            if has_request_context():
                return request.host_url.rstrip('/') + rewritten
        except Exception:
            pass

        app_url = os.environ.get('APP_URL', '').strip().rstrip('/')
        if app_url:
            return app_url + rewritten

    return rewritten


def _is_dns_failure(exc: Exception) -> bool:
    msg = str(exc).lower()
    if "failed to resolve" in msg or "name resolution" in msg or "gaierror" in msg:
        return True

    if hasattr(exc, '__cause__') and isinstance(exc.__cause__, socket.gaierror):
        return True

    return False


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
    """HTTP request using global urllib3 connection pool to prevent socket exhaustion."""
    pool = _get_http_pool()
    method = 'POST' if data else 'GET'
    try:
        resp = pool.request(method, url, body=data, headers=headers or {}, timeout=timeout)
        return resp.status, resp.data, dict(resp.headers)
    except urllib3.exceptions.HTTPError as e:
        raise RuntimeError(f"HTTP request failed: {e}")


def _download_image_bytes(image_url: str) -> tuple[bytes, str]:
    image_url = _prepare_image_url_for_fetch(image_url)
    pool = _get_http_pool()

    last_error = None
    for attempt in range(3):
        try:
            resp = pool.request('GET', image_url, headers={"User-Agent": "LiyuKidsMart/1.0"}, timeout=20)
            if resp.status == 200:
                content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
                return resp.data, content_type or "image/jpeg"
            raise RuntimeError(f"Could not download image from {image_url}: HTTP {resp.status}")
        except Exception as exc:
            last_error = exc
            if attempt == 2:
                raise RuntimeError(f"Could not download image from {image_url}: {exc}") from exc
            time.sleep(1.0)

    raise RuntimeError(f"Could not download image from {image_url}: {last_error}")


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
    last_error = None
    for base_url in _hf_inference_urls():
        url = f"{base_url}/{_hf_model()}"
        backoff = 1.0
        for attempt in range(4):
            try:
                status, body, _ = _urllib_request(
                    url,
                    data=image_bytes,
                    headers=headers,
                    timeout=60,
                )
                if status == 503:
                    if attempt < 3:
                        logger.info("HF model loading (503) — retrying in 15 s… (attempt %d)", attempt + 1)
                        time.sleep(15)
                        continue
                    payload = body[:300].decode('utf-8', errors='replace')
                    raise RuntimeError(f"HF embedding failed ({status}): {payload}")
                if status != 200:
                    payload = body[:300].decode('utf-8', errors='replace')
                    if 'Model not supported by provider' in payload or 'not supported by provider' in payload:
                        raise HFEmbeddingUnavailableError(
                            "HF embedding provider does not support this model"
                        )
                    raise RuntimeError(f"HF embedding failed ({status}): {payload}")
                result = json.loads(body)
                break
            except HFEmbeddingUnavailableError:
                raise
            except Exception as exc:
                last_error = exc
                if _is_dns_failure(exc):
                    logger.warning("HF DNS resolution failed for %s: %s", url, exc)
                    break
                if attempt == 3:
                    raise RuntimeError(f"HF embedding request failed: {exc}") from exc
                time.sleep(backoff)
                backoff = min(backoff * 2, 8.0)

        if result is not None:
            break

    if result is None:
        if last_error is not None and _is_dns_failure(last_error):
            raise RuntimeError(
                f"HF embedding failed due to DNS resolution errors; tried {', '.join(_hf_inference_urls())}"
            ) from last_error
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
    Downloads the image from the given URL and returns the embedding.
    Uses the global urllib3 pool to reuse connections.
    """
    image_bytes, content_type = _download_image_bytes(image_url)
    return embed_image_bytes(image_bytes, content_type)


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

class HFEmbeddingUnavailableError(Exception):
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

    for p in products:
        img_url = p.primary_image()
        if not img_url or "placeholder" in img_url:
            skipped += 1
            continue
        
        try:
            image_bytes, content_type = _download_image_bytes(img_url)
            embedding = embed_image_bytes(image_bytes, content_type or "image/jpeg")

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
