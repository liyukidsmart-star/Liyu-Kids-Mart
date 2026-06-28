"""
app/services/ai_service.py
AI Shopping Assistant powered by Grok (xAI) for Liyu Kids Mart.
"""
import os
import json
import logging
from datetime import datetime, timezone
from openai import OpenAI

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an AI shopping assistant for Liyu Kids Mart, Ethiopia's premier educational toy store in Addis Ababa.

Your personality: Warm, knowledgeable, helpful. You speak English and can understand basic Amharic phrases.

Your expertise:
- Educational toys and Montessori materials
- Child development stages and appropriate toys by age
- Ethiopian parents needs and preferences
- Product recommendations from our catalog

When recommending products:
- Always mention the child age suitability
- Explain developmental benefits
- Suggest complementary products
- Mention prices in Ethiopian Birr (ETB)
- Be concise but helpful

Our store details:
- Location: Addis Ababa, Ethiopia
- Delivery: Cash on delivery, free delivery on orders over 1000 ETB
- Telegram: 4170+ subscribers
- We sell: Wooden toys, Montessori materials, puzzles, educational books, building blocks, art supplies

If asked about order tracking, ask for the order number.
If asked about a specific product not in context, say you will check and suggest alternatives.
Keep responses under 300 words unless asked for detail.
"""

_INTENT_PATTERNS = {
    "product_search": [
        "buy","find","search","looking for","show me","recommend","suggest",
        "toy","product","montessori","wooden","puzzle","blocks","book",
        "art","musical","amharic","educational","what do you have","do you sell",
    ],
    "order_tracking": [
        "order","tracking","track","where is","shipped","delivery",
        "deliver","arrived","status","when will",
    ],
    "recommendation": [
        "age","years old","months old","gift","birthday","child","kid",
        "toddler","baby","infant","best for","good for","appropriate",
        "developmental","learning",
    ],
    "faq": [
        "return","refund","exchange","payment","cod","cash","address",
        "location","hours","contact","phone","telegram","policy","warranty","authentic",
    ],
}


class AIService:
    """Core AI service wrapping Grok (xAI) API for Liyu Kids Mart."""

    def __init__(self):
        api_key = os.getenv("GROK_API_KEY", "")
        self.client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1") if api_key else None
        self.model = "grok-3-mini"
        self.system_prompt = SYSTEM_PROMPT
        self._history_limit = 10

    # ---------- Public API ----------

    def chat(self, user_message: str, session_id: str, user=None, context_products=None):
        """
        Main chat method.
        Returns: dict {message, products, intent, session_id}
        """
        intent = self.detect_intent(user_message)
        system = self._build_system_prompt(user)
        history = self._load_history(session_id)

        injected_products = []
        if context_products:
            injected_products = context_products
        elif intent in ("product_search", "recommendation"):
            try:
                from app.services.vector_search import VectorSearchService
                vs = VectorSearchService()
                injected_products = vs.combined_search(user_message, limit=5)
            except Exception as exc:
                logger.warning("Vector search unavailable: %s", exc)

        messages = self._build_messages(system, history, user_message, injected_products)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=600,
                temperature=0.7,
            )
            assistant_text = response.choices[0].message.content.strip()
        except Exception as exc:
            logger.error("Grok API error: %s", exc)
            assistant_text = (
                "I am having a little trouble right now. "
                "Please try again in a moment or reach us on Telegram!"
            )

        self._save_turn(session_id, user_message, assistant_text, user)
        return {
            "message": assistant_text,
            "products": self._serialise_products(injected_products),
            "intent": intent,
            "session_id": session_id,
        }

    def detect_intent(self, message: str) -> str:
        """
        Fast rule-based intent detection.
        Returns: product_search | order_tracking | recommendation | faq | general
        """
        lowered = message.lower()
        scores = {intent: 0 for intent in _INTENT_PATTERNS}
        for intent, keywords in _INTENT_PATTERNS.items():
            for kw in keywords:
                if kw in lowered:
                    scores[intent] += 1
        best = max(scores, key=lambda k: scores[k])
        return "general" if scores[best] == 0 else best

    def get_product_aware_response(self, message: str, products: list, user=None) -> str:
        """Generate a response that explicitly references supplied products."""
        system = self._build_system_prompt(user)
        product_json = json.dumps(
            [self._product_to_dict(p) for p in products],
            ensure_ascii=False, indent=2
        )
        enhanced_system = (
            f"{system}\n\nAVAILABLE PRODUCTS (reference these in your answer):\n{product_json}"
        )
        messages = [
            {"role": "system", "content": enhanced_system},
            {"role": "user", "content": message},
        ]
        try:
            resp = self.client.chat.completions.create(
                model=self.model, messages=messages, max_tokens=600, temperature=0.7
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            logger.error("Grok API error in product_aware_response: %s", exc)
            return "I am having trouble responding right now. Please try again shortly."

    def generate_product_embeddings_text(self, product) -> str:
        """Build plain-text representation of a product for embedding."""
        parts = [product.name]
        if product.description:
            parts.append(product.description)
        age_parts = []
        if product.age_min_months is not None:
            age_parts.append(str(product.age_min_months))
        if product.age_max_months is not None:
            age_parts.append(str(product.age_max_months))
        if age_parts:
            parts.append(f"Age range in months: {'-'.join(age_parts)}")
        try:
            if product.category:
                parts.append(f"Category: {product.category.name}")
        except Exception:
            pass
        if product.tags:
            tags = product.tags if isinstance(product.tags, str) else ", ".join(product.tags)
            parts.append(f"Tags: {tags}")
        if product.current_price():
            parts.append(f"Price: {product.current_price()} ETB")
        return ". ".join(parts) + "."

    def summarize_for_bot(self, response: str, max_length: int = 4096) -> str:
        """Trim response to fit Telegram message limit (4096 chars)."""
        if len(response) <= max_length:
            return response
        truncated = response[: max_length - 3]
        for sep in (".\n", ". ", "!\n", "! ", "?\n", "? "):
            idx = truncated.rfind(sep)
            if idx > max_length // 2:
                return truncated[: idx + 1] + "..."
        return truncated + "..."

    # ---------- Private helpers ----------

    def _build_system_prompt(self, user=None) -> str:
        base = self.system_prompt
        if user is None:
            return base
        try:
            child_ages = getattr(user, "child_ages", None)
            if child_ages:
                ages_str = ", ".join(
                    f"{a} year{'s' if int(a) != 1 else ''}" for a in child_ages
                )
                base += (
                    f"\n\nCUSTOMER CONTEXT: This parent has children aged {ages_str}. "
                    "Tailor recommendations accordingly."
                )
        except Exception:
            pass
        return base

    def _load_history(self, session_id: str) -> list:
        try:
            from app.models import AIConversation
            records = (
                AIConversation.query
                .filter_by(session_id=session_id)
                .order_by(AIConversation.created_at.desc())
                .limit(self._history_limit)
                .all()
            )
            records = list(reversed(records))
            messages = []
            for rec in records:
                messages.append({"role": "user", "content": rec.user_message})
                messages.append({"role": "assistant", "content": rec.assistant_response})
            return messages
        except Exception as exc:
            logger.warning("Could not load conversation history: %s", exc)
            return []

    def _build_messages(self, system, history, user_message, products) -> list:
        messages = [{"role": "system", "content": system}]
        messages.extend(history)
        if products:
            product_json = json.dumps(
                [self._product_to_dict(p) for p in products],
                ensure_ascii=False, indent=2
            )
            combined = (
                f"{user_message}\n\n[RELEVANT PRODUCTS FROM OUR CATALOG]\n{product_json}"
            )
            messages.append({"role": "user", "content": combined})
        else:
            messages.append({"role": "user", "content": user_message})
        return messages

    def _save_turn(self, session_id, user_message, assistant_text, user=None):
        try:
            from app.extensions import db
            from app.models import AIConversation
            turn = AIConversation(
                session_id=session_id,
                user_id=getattr(user, "id", None),
                user_message=user_message,
                assistant_response=assistant_text,
                created_at=datetime.now(timezone.utc),
            )
            db.session.add(turn)
            db.session.commit()
        except Exception as exc:
            logger.warning("Could not save conversation turn: %s", exc)
            try:
                from app.extensions import db
                db.session.rollback()
            except Exception:
                pass

    def _product_to_dict(self, product) -> dict:
        try:
            return {
                "id": product.id,
                "name": product.name,
                "price": float(product.current_price()) if product.current_price() else None,
                "description": (product.description or "")[:200],
                "age_min_months": product.age_min_months,
                "age_max_months": product.age_max_months,
                "category": product.category.name if product.category else "",
                "in_stock": (product.stock_quantity > 0) if product.stock_quantity is not None else True,
                "image_url": product.image_url or "",
            }
        except Exception as exc:
            logger.debug("product_to_dict error: %s", exc)
            return {"id": getattr(product, "id", None), "name": str(product)}

    def _serialise_products(self, products: list) -> list:
        if not products:
            return []
        return [self._product_to_dict(p) for p in products]
