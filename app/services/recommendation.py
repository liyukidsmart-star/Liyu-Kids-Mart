"""
app/services/recommendation.py
Hybrid recommendation engine for Liyu Kids Mart.
Combines collaborative filtering, content-based, and popularity signals.
"""
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class RecommendationEngine:
    """
    Hybrid recommendation engine.

    Signal weights:
      - 40% collaborative filtering  (users who bought X also bought Y)
      - 30% content-based            (same category, similar age range)
      - 30% popularity               (trending / bestseller)
    """

    # ---------- Public API ----------

    def get_recommendations_for_user(self, user_id: int, limit: int = 8) -> list:
        """
        Personalised recommendations for an authenticated user.
        Returns list of Product objects.
        """
        try:
            from app.models import Product, Order, OrderItem, ActivityLog
            from app.extensions import db

            # --- signals ---
            purchased_ids = self._get_purchased_product_ids(user_id)
            viewed_ids = self._get_viewed_product_ids(user_id)
            child_ages_months = self._get_child_ages_months(user_id)

            if not purchased_ids and not viewed_ids:
                return self.get_cold_start_recommendations(limit=limit)

            candidate_scores: dict[int, float] = {}

            # 40%: collaborative filtering
            collab = self._collaborative_candidates(purchased_ids, exclude_ids=purchased_ids)
            for pid, score in collab.items():
                candidate_scores[pid] = candidate_scores.get(pid, 0.0) + score * 0.40

            # 30%: content-based
            content = self._content_based_candidates(
                purchased_ids + viewed_ids,
                exclude_ids=purchased_ids,
                child_ages_months=child_ages_months,
            )
            for pid, score in content.items():
                candidate_scores[pid] = candidate_scores.get(pid, 0.0) + score * 0.30

            # 30%: popularity
            popular = self._popularity_candidates(exclude_ids=purchased_ids)
            for pid, score in popular.items():
                candidate_scores[pid] = candidate_scores.get(pid, 0.0) + score * 0.30

            # Rank and fetch
            ranked_ids = sorted(candidate_scores, key=lambda k: candidate_scores[k], reverse=True)
            top_ids = ranked_ids[:limit]

            products = Product.query.filter(
                Product.id.in_(top_ids), Product.is_active == True
            ).all()
            id_to_product = {p.id: p for p in products}
            return [id_to_product[pid] for pid in top_ids if pid in id_to_product]

        except Exception as exc:
            logger.error("get_recommendations_for_user error: %s", exc)
            return self.get_cold_start_recommendations(limit=limit)

    def get_similar_products(self, product_id: int, limit: int = 6) -> list:
        """
        Products in the same category with a similar age range,
        sorted by sales_count descending.
        """
        try:
            from app.models import Product
            source = Product.query.get(product_id)
            if not source:
                return []

            q = Product.query.filter(
                Product.id != product_id,
                Product.category_id == source.category_id,
                Product.is_active == True,
            )

            if source.age_min_months is not None and source.age_max_months is not None:
                # Overlap: source_min <= candidate_max AND source_max >= candidate_min
                q = q.filter(
                    Product.age_min_months <= source.age_max_months,
                    Product.age_max_months >= source.age_min_months,
                )

            return q.order_by(Product.sales_count.desc()).limit(limit).all()

        except Exception as exc:
            logger.error("get_similar_products error: %s", exc)
            return []

    def get_frequently_bought_together(self, product_id: int, limit: int = 4) -> list:
        """
        Products that frequently appear in the same orders as product_id.
        Uses a self-join on order_items.
        """
        try:
            from app.models import Product, OrderItem
            from app.extensions import db
            from sqlalchemy import func

            oi1 = db.aliased(OrderItem)
            oi2 = db.aliased(OrderItem)

            results = (
                db.session.query(oi2.product_id, func.count().label("cnt"))
                .join(oi1, oi1.order_id == oi2.order_id)
                .filter(oi1.product_id == product_id)
                .filter(oi2.product_id != product_id)
                .group_by(oi2.product_id)
                .order_by(func.count().desc())
                .limit(limit)
                .all()
            )

            if not results:
                return []

            ids = [r.product_id for r in results]
            products = Product.query.filter(
                Product.id.in_(ids), Product.is_active == True
            ).all()
            id_map = {p.id: p for p in products}
            return [id_map[pid] for pid in ids if pid in id_map]

        except Exception as exc:
            logger.error("get_frequently_bought_together error: %s", exc)
            return []

    def get_trending_products(self, limit: int = 8, days: int = 7) -> list:
        """
        Products with the most activity (orders + views) in the last N days.
        """
        try:
            from app.models import Product, OrderItem, Order, ActivityLog
            from app.extensions import db
            from sqlalchemy import func

            cutoff = datetime.now(timezone.utc) - timedelta(days=days)

            # Order-based trending
            order_scores = (
                db.session.query(OrderItem.product_id, func.count().label("orders"))
                .join(Order, Order.id == OrderItem.order_id)
                .filter(Order.created_at >= cutoff)
                .group_by(OrderItem.product_id)
                .all()
            )
            score_map: dict[int, float] = {}
            for pid, cnt in order_scores:
                score_map[pid] = score_map.get(pid, 0.0) + cnt * 2.0  # orders weighted higher

            # View-based trending
            try:
                view_scores = (
                    db.session.query(ActivityLog.product_id, func.count().label("views"))
                    .filter(
                        ActivityLog.action == "view_product",
                        ActivityLog.product_id.isnot(None),
                        ActivityLog.created_at >= cutoff,
                    )
                    .group_by(ActivityLog.product_id)
                    .all()
                )
                for pid, cnt in view_scores:
                    score_map[pid] = score_map.get(pid, 0.0) + cnt * 0.5
            except Exception:
                pass  # ActivityLog may not exist yet

            if not score_map:
                return self.get_cold_start_recommendations(limit=limit)

            ranked_ids = sorted(score_map, key=lambda k: score_map[k], reverse=True)[:limit]
            products = Product.query.filter(
                Product.id.in_(ranked_ids), Product.is_active == True
            ).all()
            id_map = {p.id: p for p in products}
            return [id_map[pid] for pid in ranked_ids if pid in id_map]

        except Exception as exc:
            logger.error("get_trending_products error: %s", exc)
            return self.get_cold_start_recommendations(limit=limit)

    def get_age_appropriate_products(self, age_months: int, limit: int = 8) -> list:
        """Filter by age range (months) and sort by sales_count."""
        try:
            from app.models import Product
            return (
                Product.query
                .filter(
                    Product.is_active == True,
                    Product.age_min_months <= age_months,
                    Product.age_max_months >= age_months,
                )
                .order_by(Product.sales_count.desc())
                .limit(limit)
                .all()
            )
        except Exception as exc:
            logger.error("get_age_appropriate_products error: %s", exc)
            return []

    def get_cold_start_recommendations(self, limit: int = 8) -> list:
        """
        For new users with no history.
        Returns a mix of featured products and bestsellers.
        """
        try:
            from app.models import Product
            # Featured products first
            featured = (
                Product.query
                .filter(Product.is_active == True, Product.is_featured == True)
                .order_by(Product.sales_count.desc())
                .limit(limit // 2 + 1)
                .all()
            )
            # Bestsellers
            bestsellers = (
                Product.query
                .filter(Product.is_active == True)
                .order_by(Product.sales_count.desc())
                .limit(limit)
                .all()
            )
            # Merge, deduplicate
            seen = set()
            result = []
            for p in featured + bestsellers:
                if p.id not in seen:
                    seen.add(p.id)
                    result.append(p)
                if len(result) >= limit:
                    break
            return result
        except Exception as exc:
            logger.error("get_cold_start_recommendations error: %s", exc)
            return []

    def get_bundle_suggestions(self, cart_product_ids: list, limit: int = 3) -> list:
        """
        Suggest products that complement items already in the cart.
        Based on frequently-bought-together data.
        """
        try:
            from app.models import Product
            seen_ids = set(cart_product_ids)
            candidates: dict[int, int] = {}

            for pid in cart_product_ids:
                fbt = self.get_frequently_bought_together(pid, limit=limit * 2)
                for p in fbt:
                    if p.id not in seen_ids:
                        candidates[p.id] = candidates.get(p.id, 0) + 1

            if not candidates:
                return []

            ranked_ids = sorted(candidates, key=lambda k: candidates[k], reverse=True)[:limit]
            products = Product.query.filter(
                Product.id.in_(ranked_ids), Product.is_active == True
            ).all()
            id_map = {p.id: p for p in products}
            return [id_map[pid] for pid in ranked_ids if pid in id_map]

        except Exception as exc:
            logger.error("get_bundle_suggestions error: %s", exc)
            return []

    def log_product_view(self, user_id, product_id: int, session_id: str):
        """
        Log a product view to activity_logs and increment product.view_count.
        """
        try:
            from app.extensions import db
            from app.models import ActivityLog, Product

            log = ActivityLog(
                user_id=user_id,
                product_id=product_id,
                session_id=session_id,
                action="view_product",
                created_at=datetime.now(timezone.utc),
            )
            db.session.add(log)

            product = Product.query.get(product_id)
            if product:
                product.view_count = (product.view_count or 0) + 1

            db.session.commit()
        except Exception as exc:
            logger.warning("log_product_view error: %s", exc)
            try:
                from app.extensions import db
                db.session.rollback()
            except Exception:
                pass

    def get_recommendations_for_child_age(self, age_years: int, limit: int = 8) -> list:
        """
        Convert age_years to a months range and return age-appropriate products.
        Uses a +/- 3-month buffer to find good matches.
        """
        age_months = age_years * 12
        # Buffer: show products for age_years-1 to age_years+1
        min_months = max(0, age_months - 12)
        max_months = age_months + 12
        try:
            from app.models import Product
            return (
                Product.query
                .filter(
                    Product.is_active == True,
                    Product.age_min_months <= max_months,
                    Product.age_max_months >= min_months,
                )
                .order_by(Product.sales_count.desc())
                .limit(limit)
                .all()
            )
        except Exception as exc:
            logger.error("get_recommendations_for_child_age error: %s", exc)
            return []

    # ---------- Private helpers ----------

    def _get_purchased_product_ids(self, user_id: int) -> list:
        try:
            from app.models import Order, OrderItem
            from app.extensions import db
            rows = (
                db.session.query(OrderItem.product_id)
                .join(Order, Order.id == OrderItem.order_id)
                .filter(Order.user_id == user_id)
                .distinct()
                .all()
            )
            return [r.product_id for r in rows]
        except Exception:
            return []

    def _get_viewed_product_ids(self, user_id: int) -> list:
        try:
            from app.models import ActivityLog
            from app.extensions import db
            rows = (
                db.session.query(ActivityLog.product_id)
                .filter(
                    ActivityLog.user_id == user_id,
                    ActivityLog.action == "view_product",
                    ActivityLog.product_id.isnot(None),
                )
                .distinct()
                .all()
            )
            return [r.product_id for r in rows]
        except Exception:
            return []

    def _get_child_ages_months(self, user_id: int) -> list:
        try:
            from app.models import User
            user = User.query.get(user_id)
            if user and user.child_ages:
                return [int(a) * 12 for a in user.child_ages]
        except Exception:
            pass
        return []

    def _collaborative_candidates(
        self, source_product_ids: list, exclude_ids: list
    ) -> dict:
        """
        Find products bought by users who also bought the source products.
        Returns {product_id: score}.
        """
        if not source_product_ids:
            return {}
        try:
            from app.models import Order, OrderItem
            from app.extensions import db
            from sqlalchemy import func

            # Users who bought at least one source product
            user_subq = (
                db.session.query(Order.user_id)
                .join(OrderItem, OrderItem.order_id == Order.id)
                .filter(OrderItem.product_id.in_(source_product_ids))
                .distinct()
                .subquery()
            )
            # Products those users also bought
            rows = (
                db.session.query(OrderItem.product_id, func.count().label("cnt"))
                .join(Order, Order.id == OrderItem.order_id)
                .filter(Order.user_id.in_(user_subq))
                .filter(OrderItem.product_id.notin_(exclude_ids or [0]))
                .group_by(OrderItem.product_id)
                .all()
            )
            max_cnt = max((r.cnt for r in rows), default=1)
            return {r.product_id: r.cnt / max_cnt for r in rows}
        except Exception as exc:
            logger.debug("_collaborative_candidates error: %s", exc)
            return {}

    def _content_based_candidates(
        self,
        source_product_ids: list,
        exclude_ids: list,
        child_ages_months: list,
    ) -> dict:
        """
        Products in the same categories and similar age ranges.
        Returns {product_id: score}.
        """
        if not source_product_ids:
            return {}
        try:
            from app.models import Product
            from app.extensions import db
            from sqlalchemy import func

            sources = Product.query.filter(Product.id.in_(source_product_ids)).all()
            cat_ids = list({p.category_id for p in sources if p.category_id})
            age_ranges = [
                (p.age_min_months, p.age_max_months)
                for p in sources
                if p.age_min_months is not None and p.age_max_months is not None
            ]

            q = Product.query.filter(
                Product.is_active == True,
                Product.id.notin_(exclude_ids or [0]),
            )
            if cat_ids:
                q = q.filter(Product.category_id.in_(cat_ids))

            candidates = q.order_by(Product.sales_count.desc()).limit(50).all()
            scored = {}
            for p in candidates:
                score = 0.5  # base
                if child_ages_months:
                    for age in child_ages_months:
                        if (
                            p.age_min_months is not None
                            and p.age_max_months is not None
                            and p.age_min_months <= age <= p.age_max_months
                        ):
                            score += 0.5
                scored[p.id] = min(score, 1.0)
            return scored
        except Exception as exc:
            logger.debug("_content_based_candidates error: %s", exc)
            return {}

    def _popularity_candidates(self, exclude_ids: list) -> dict:
        """Return popularity scores for top-selling products."""
        try:
            from app.models import Product
            products = (
                Product.query
                .filter(Product.is_active == True, Product.id.notin_(exclude_ids or [0]))
                .order_by(Product.sales_count.desc())
                .limit(20)
                .all()
            )
            if not products:
                return {}
            max_sales = max((p.sales_count or 0 for p in products), default=1)
            return {
                p.id: (p.sales_count or 0) / max(max_sales, 1)
                for p in products
            }
        except Exception as exc:
            logger.debug("_popularity_candidates error: %s", exc)
            return {}
