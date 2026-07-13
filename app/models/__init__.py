from app.models.loyalty import (  # noqa: F401  — must be imported FIRST so FKs resolve
    LoyaltyLevel, SpendingThreshold, QuantityDiscount, CartIncentive,
    Achievement, UserAchievement, RewardTransaction, LoyaltySettings,
    CustomerStatus, RewardTransactionType,
)
from app.models.user import User, UserRole
from app.models.product import Product, Category, ProductImage, ProductTag, ProductEmbedding
from app.models.order import Order, OrderItem, Cart, Wishlist, Address, Coupon, Review
from app.models.delivery import Driver, Delivery, DeliveryStatus
from app.models.ai_conversation import (
    Payment, AIConversation, ProductRecommendation, ActivityLog, Notification
)
from app.models.marketing import ProductDiscount, TelegramChannelPost, TelegramChannelPostImage  # noqa: F401
from app.models.inventory import StockTransaction, StockTransactionType, POSSale, POSSaleItem, POSSaleStatus  # noqa: F401

__all__ = [
    'LoyaltyLevel', 'SpendingThreshold', 'QuantityDiscount', 'CartIncentive',
    'Achievement', 'UserAchievement', 'RewardTransaction', 'LoyaltySettings',
    'CustomerStatus', 'RewardTransactionType',
    'User', 'UserRole',
    'Product', 'Category', 'ProductImage', 'ProductTag', 'ProductEmbedding',
    'ProductDiscount', 'TelegramChannelPost', 'TelegramChannelPostImage',
    'StockTransaction', 'StockTransactionType', 'POSSale', 'POSSaleItem', 'POSSaleStatus',
    'Order', 'OrderItem', 'Cart', 'Wishlist', 'Address', 'Coupon', 'Review',
    'Driver', 'Delivery', 'DeliveryStatus',
    'Payment', 'AIConversation', 'ProductRecommendation', 'ActivityLog', 'Notification',
]

