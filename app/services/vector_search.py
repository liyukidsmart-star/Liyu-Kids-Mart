"""
app/services/vector_search.py
Vector Search Service with TF-IDF fallback for Liyu Kids Mart.
Provides semantic product search using AI embeddings or sklearn TF-IDF.
"""
import os
import json
import logging
import numpy as np
from openai import OpenAI

logger = logging.getLogger(__name__)


class VectorSearchService:
    """
    Semantic product search service.

    Primary:  xAI/Grok embedding API  (1536-dim vectors)
    Fallback: sklearn TF-IDF vectorizer (in-memory, no API)
    """

    # Class-level TF-IDF cache so we build it once per process
    _tfidf_vectorizer = None
    _tfidf_matrix = None
    _tfidf_product_ids = None

    def __init__(self):
        api_key = os.getenv("GROK_API_KEY", "")
        self.use_ai_embeddings = bool(api_key)
        self._client = None
        if self.use_ai_embeddings:
            try:
                self._client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
            except Exception as exc:
                logger.warning("Could not init OpenAI client: %s", exc)
                self.use_ai_embeddings = False

    # ---------- Public API ----------

    def get_embedding(self, text: str) -> np.ndarray:
        """
        Return a numpy embedding vector for the given text.
        Tries xAI API first; falls back to TF-IDF.
        """
        if self.use_ai_embeddings and self._client:
            try:
                # xAI supports OpenAI-compatible embedding endpoint
                resp = self._client.embeddings.create(
                    model="text-embedding-3-small",
                    input=text,
                )
                vec = np.array(resp.data[0].embedding, dtype=np.float32)
                return vec
            except Exception as exc:
                logger.warning("xAI embedding failed (%s), using TF-IDF fallback", exc)

        return self._tfidf_embed(text)

    def search_products(self, query: str, limit: int = 6) -> list:
        """
        Semantic search using stored ProductEmbedding vectors.
        Falls back to keyword_search if no embeddings are stored.
        """
        try:
            from app.models import Product, ProductEmbedding
            from app.extensions import db

            stored = ProductEmbedding.query.all()
            if not stored:
                logger.info("No stored embeddings; falling back to keyword search")
                return self.keyword_search(query, limit=limit)

            query_vec = self.get_embedding(query)
            product_ids = []
            similarities = []

            for emb_rec in stored:
                vec = np.array(json.loads(emb_rec.embedding_vector), dtype=np.float32)
                sim = self._cosine_similarity(query_vec, vec)
                product_ids.append(emb_rec.product_id)
                similarities.append(sim)

            # Sort by similarity descending
            ranked = sorted(zip(similarities, product_ids), key=lambda x: x[0], reverse=True)
            top_ids = [pid for _, pid in ranked[:limit]]

            products = (
                Product.query
                .filter(Product.id.in_(top_ids), Product.is_active == True)
                .all()
            )
            # Preserve ranked order
            id_to_product = {p.id: p for p in products}
            return [id_to_product[pid] for pid in top_ids if pid in id_to_product]

        except Exception as exc:
            logger.error("search_products error: %s", exc)
            return self.keyword_search(query, limit=limit)

    def build_all_embeddings(self) -> int:
        """
        Generate and store embeddings for every active product.
        Called by scripts/generate_embeddings.py.
        Returns count of embeddings created.
        """
        try:
            from app.models import Product, ProductEmbedding
            from app.extensions import db
            from app.services.ai_service import AIService

            ai = AIService()
            products = Product.query.filter_by(is_active=True).all()
            count = 0

            for product in products:
                text = ai.generate_product_embeddings_text(product)
                vec = self.get_embedding(text)
                vec_json = json.dumps(vec.tolist())

                existing = ProductEmbedding.query.filter_by(product_id=product.id).first()
                if existing:
                    existing.embedding_vector = vec_json
                    existing.embedding_text = text
                    existing.updated_at = __import__("datetime").datetime.utcnow()
                else:
                    rec = ProductEmbedding(
                        product_id=product.id,
                        embedding_vector=vec_json,
                        embedding_text=text,
                    )
                    db.session.add(rec)

                count += 1
                if count % 10 == 0:
                    db.session.commit()
                    logger.info("Embedded %d / %d products", count, len(products))

            db.session.commit()
            # Invalidate TF-IDF cache so it rebuilds on next search
            VectorSearchService._tfidf_vectorizer = None
            VectorSearchService._tfidf_matrix = None
            VectorSearchService._tfidf_product_ids = None

            logger.info("build_all_embeddings: created/updated %d embeddings", count)
            return count

        except Exception as exc:
            logger.error("build_all_embeddings error: %s", exc)
            return 0

    def keyword_search(self, query: str, limit: int = 10) -> list:
        """
        SQL ILIKE-based fallback search across name, description, and tags.
        """
        try:
            from app.models import Product
            q = f"%{query}%"
            results = (
                Product.query
                .filter(
                    Product.is_active == True,
                    (
                        Product.name.ilike(q) |
                        Product.description.ilike(q) |
                        Product.tags.ilike(q)
                    ),
                )
                .order_by(Product.sales_count.desc())
                .limit(limit)
                .all()
            )
            return results
        except Exception as exc:
            logger.error("keyword_search error: %s", exc)
            return []

    def combined_search(self, query: str, limit: int = 8) -> list:
        """
        Try vector search first; supplement with keyword results to reach limit.
        Deduplicates by product ID.
        """
        results = self.search_products(query, limit=limit)
        seen_ids = {p.id for p in results}

        if len(results) < limit:
            kw_results = self.keyword_search(query, limit=limit)
            for p in kw_results:
                if p.id not in seen_ids and len(results) < limit:
                    results.append(p)
                    seen_ids.add(p.id)

        return results[:limit]

    # ---------- Private: TF-IDF ----------

    def _tfidf_embed(self, text: str) -> np.ndarray:
        """Return a TF-IDF vector for the text, building the vectorizer if needed."""
        self._ensure_tfidf_built()
        if VectorSearchService._tfidf_vectorizer is None:
            # Absolute fallback: zero vector of length 128
            return np.zeros(128, dtype=np.float32)
        vec = VectorSearchService._tfidf_vectorizer.transform([text])
        # Return dense row as float32 array
        return np.array(vec.toarray()[0], dtype=np.float32)

    def _ensure_tfidf_built(self):
        """Build and cache the TF-IDF vectorizer from stored product texts."""
        if VectorSearchService._tfidf_vectorizer is not None:
            return
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from app.models import ProductEmbedding

            records = ProductEmbedding.query.with_entities(
                ProductEmbedding.product_id, ProductEmbedding.embedding_text
            ).all()

            if not records:
                logger.info("No ProductEmbedding records for TF-IDF; searching on-the-fly")
                # Build from products directly
                from app.models import Product
                from app.services.ai_service import AIService
                ai = AIService()
                products = Product.query.filter_by(is_active=True).all()
                if not products:
                    return
                records_data = [(p.id, ai.generate_product_embeddings_text(p)) for p in products]
            else:
                records_data = [(r.product_id, r.embedding_text) for r in records]

            ids, texts = zip(*records_data)
            vectorizer = TfidfVectorizer(
                max_features=4096,
                ngram_range=(1, 2),
                stop_words="english",
                sublinear_tf=True,
            )
            matrix = vectorizer.fit_transform(texts)

            VectorSearchService._tfidf_vectorizer = vectorizer
            VectorSearchService._tfidf_matrix = matrix
            VectorSearchService._tfidf_product_ids = list(ids)
            logger.info("TF-IDF vectorizer built with %d documents", len(texts))

        except Exception as exc:
            logger.error("Could not build TF-IDF vectorizer: %s", exc)

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity between two 1-D numpy arrays."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))
