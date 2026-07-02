#!/usr/bin/env python3
"""
Liyu Kids Mart — Native Telegram Bot with Full E-Commerce Capabilities
"""
import os
import sys
import asyncio
import logging
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
APP_URL = os.getenv('APP_URL', 'http://localhost:5000')
BOT_MODE = os.getenv('BOT_MODE', 'polling')
DRIVER_TG_IDS = [d.strip() for d in os.getenv('DRIVER_TG_IDS', '851785627,7733651914').split(',') if d.strip()]
MANAGER_TG_IDS = [m.strip() for m in os.getenv('MANAGER_TG_IDS', '').split(',') if m.strip()]

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, InputMediaPhoto
    from telegram.ext import (Application, CommandHandler, MessageHandler,
                              CallbackQueryHandler, filters, ContextTypes, ConversationHandler)
except ImportError:
    print("ERROR: python-telegram-bot not installed. Run: pip install python-telegram-bot==21.6")
    sys.exit(1)

import telegram_bot.db_wrapper as db

# --- CONVERSATION STATES ---
PHONE, LOCATION = range(2)

# --- KEYBOARDS ---
def store_btn(text, path):
    full_url = f"{APP_URL}{path}"
    if full_url.startswith("https://"):
        return InlineKeyboardButton(text, web_app=WebAppInfo(url=full_url))
    return InlineKeyboardButton(text, url=full_url)

def main_keyboard(show_driver=False, show_manager=False):
    rows = [
        [InlineKeyboardButton("🛒 Native Shop", callback_data="shop_cats"),
         store_btn("🌐 Open Mini App", "/telegram/mini-app")],
        [InlineKeyboardButton("🛍️ My Cart", callback_data="cart_view"),
         InlineKeyboardButton("📦 My Orders", callback_data="my_orders")],
        [InlineKeyboardButton("🔍 Track Order", callback_data="track_init"),
         InlineKeyboardButton("📞 Support", callback_data="support")],
    ]
    if show_driver:
        rows.append([store_btn("🛵 Driver Dashboard", "/telegram/driver-app")])
    if show_manager:
        rows.append([store_btn("🏪 Store Management", "/telegram/store-app")])
    return InlineKeyboardMarkup(rows)

def back_menu_btn():
    return InlineKeyboardButton("⬅️ Main Menu", callback_data="main_menu")

# --- HANDLERS ---

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()

    # Register user in DB
    await db.run_in_db(db.get_or_create_user, user.id, user.username, full_name)
    show_driver = await db.run_in_db(db.is_driver_user, user.id)
    show_manager = str(user.id) in MANAGER_TG_IDS or await db.run_in_db(db.is_manager_user, user.id)

    welcome_text = (
        f"🌟 *Welcome to Liyu Kids Mart, {user.first_name}!* 🌟\n\n"
        "Ethiopia's premier destination for Montessori materials & educational toys. 🇪🇹\n\n"
        "💡 *What would you like to do today?*\n"
        "You can browse products directly here in Telegram or open our beautiful Mini App!"
    )
    
    if update.message:
        await update.message.reply_text(welcome_text, parse_mode='Markdown', reply_markup=main_keyboard(show_driver, show_manager))
    elif update.callback_query:
        await update.callback_query.message.reply_text(welcome_text, parse_mode='Markdown', reply_markup=main_keyboard(show_driver, show_manager))

async def menu_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = "🌟 *Liyu Kids Mart Main Menu*\n\nHow can we help you today?"
    show_driver = await db.run_in_db(db.is_driver_user, update.effective_user.id)
    show_manager = str(update.effective_user.id) in MANAGER_TG_IDS or await db.run_in_db(db.is_manager_user, update.effective_user.id)
    try:
        await query.message.edit_text(text, parse_mode='Markdown', reply_markup=main_keyboard(show_driver, show_manager))
    except Exception:
        # If it's a photo message, delete and send new
        await query.message.delete()
        await query.message.reply_text(text, parse_mode='Markdown', reply_markup=main_keyboard(show_driver, show_manager))

# --- NATIVE SHOPPING ---

async def shop_categories(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    categories = await db.run_in_db(db.get_categories)
    
    if not categories:
        await query.message.edit_text("No categories available at the moment.", reply_markup=InlineKeyboardMarkup([[back_menu_btn()]]))
        return

    keyboard = []
    # 2 categories per row
    row = []
    for cat in categories:
        row.append(InlineKeyboardButton(f"{cat.icon or '📁'} {cat.name}", callback_data=f"cat_{cat.id}_0"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    keyboard.append([back_menu_btn()])
    
    text = "📂 *Browse Categories*\n\nSelect a category to view our products:"
    
    try:
        await query.message.edit_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        await query.message.delete()
        await query.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def category_products(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    _, cat_id, page = query.data.split('_')
    cat_id = int(cat_id)
    page = int(page)
    limit = 5
    offset = page * limit
    
    products = await db.run_in_db(db.get_products, category_id=cat_id, limit=limit+1, offset=offset)
    has_next = len(products) > limit
    products = products[:limit]
    
    if not products:
        text = "No products found in this category."
        kb = [[InlineKeyboardButton("🔙 Back to Categories", callback_data="shop_cats")], [back_menu_btn()]]
    else:
        cat_name = products[0].category.name if products[0].category else "Products"
        text = f"📦 *{cat_name}* (Page {page+1})\n\nSelect a product to view details:"
        kb = []
        for p in products:
            kb.append([InlineKeyboardButton(f"▪️ {p.name} - ETB {p.current_price():,.0f}", callback_data=f"prod_{p.id}")])
            
        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"cat_{cat_id}_{page-1}"))
        if has_next:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"cat_{cat_id}_{page+1}"))
        if nav_row:
            kb.append(nav_row)
            
        kb.append([InlineKeyboardButton("🔙 Categories", callback_data="shop_cats"), back_menu_btn()])

    try:
        await query.message.edit_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
    except Exception:
        await query.message.delete()
        await query.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))

async def product_detail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    _, prod_id = query.data.split('_')
    product = await db.run_in_db(db.get_product_by_id, int(prod_id))
    
    if not product:
        await query.message.reply_text("Product not found.")
        return
        
    text = (
        f"🧸 *{product.name}*\n\n"
        f"💰 *Price:* ETB {product.current_price():,.0f}\n"
        f"👶 *Age:* {product.age_label()}\n\n"
        f"📝 {product.short_description or 'No description available.'}\n\n"
    )
    
    kb = [
        [InlineKeyboardButton("➕ Add to Cart", callback_data=f"addcart_{product.id}")],
        [InlineKeyboardButton("🔙 Back to Category", callback_data=f"cat_{product.category_id}_0") if product.category_id else InlineKeyboardButton("🔙 Shop", callback_data="shop_cats")],
        [InlineKeyboardButton("🛍️ Go to Cart", callback_data="cart_view")]
    ]
    
    img_url = product.primary_image()
    # In a real app, img_url should be absolute. If it's relative, append APP_URL.
    if img_url and img_url.startswith('/'):
        img_url = f"{APP_URL}{img_url}"
        
    try:
        # If the current message has a photo, we edit media, else we delete and send photo
        if query.message.photo:
            await query.message.edit_media(InputMediaPhoto(media=img_url, caption=text, parse_mode='Markdown'), reply_markup=InlineKeyboardMarkup(kb))
        else:
            await query.message.delete()
            await query.message.reply_photo(photo=img_url, caption=text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        # Fallback to text if photo fails
        try:
            await query.message.delete()
            await query.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
        except:
            pass

# --- CART MANAGEMENT ---

async def add_to_cart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, prod_id = query.data.split('_')
    
    success, msg = await db.run_in_db(db.add_to_cart, update.effective_user.id, int(prod_id))
    if success:
        await query.answer("✅ Added to your cart!")
    else:
        await query.answer(f"❌ {msg}", show_alert=True)

async def cart_view(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    items = await db.run_in_db(db.get_cart_items, update.effective_user.id)
    
    if not items:
        text = "🛒 *Your Cart is Empty*\n\nBrowse our store to find something amazing!"
        kb = [[InlineKeyboardButton("🛍️ Shop Now", callback_data="shop_cats")], [back_menu_btn()]]
    else:
        text = "🛒 *Your Cart*\n\n"
        subtotal = 0
        kb = []
        for i, item in enumerate(items, 1):
            prod = item.product
            line_total = prod.current_price() * item.quantity
            subtotal += line_total
            text += f"*{i}. {prod.name}*\n"
            text += f"   {item.quantity} x ETB {prod.current_price():,.0f} = *ETB {line_total:,.0f}*\n\n"
            
            # Plus / Minus buttons
            kb.append([
                InlineKeyboardButton(f"➖ {prod.name[:15]}", callback_data=f"cartupd_{item.id}_-1"),
                InlineKeyboardButton(f"➕", callback_data=f"cartupd_{item.id}_1")
            ])
            
        delivery = 50 if subtotal <= 1000 else 0
        total = subtotal + delivery
        
        text += f"💵 *Subtotal:* ETB {subtotal:,.0f}\n"
        text += f"🚚 *Delivery:* {'Free' if delivery == 0 else f'ETB {delivery:,.0f}'}\n"
        text += f"💲 *Total:* ETB {total:,.0f}\n"
        
        kb.append([InlineKeyboardButton("✅ Checkout", callback_data="checkout_start")])
        kb.append([InlineKeyboardButton("🗑️ Clear Cart", callback_data="cart_clear")])
        kb.append([InlineKeyboardButton("🛍️ Continue Shopping", callback_data="shop_cats"), back_menu_btn()])

    try:
        if query.message.photo:
            await query.message.delete()
            await query.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
        else:
            await query.message.edit_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
    except Exception:
        pass

async def cart_update(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    _, cart_id, change = query.data.split('_')
    
    await db.run_in_db(db.update_cart_item, update.effective_user.id, int(cart_id), int(change))
    await cart_view(update, ctx)

async def cart_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await db.run_in_db(db.clear_cart, update.effective_user.id)
    await query.answer("Cart cleared!", show_alert=True)
    await cart_view(update, ctx)

# --- CHECKOUT CONVERSATION ---

async def checkout_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    items = await db.run_in_db(db.get_cart_items, update.effective_user.id)
    if not items:
        await query.message.reply_text("Your cart is empty!")
        return ConversationHandler.END

    if query.message.photo:
        await query.message.delete()
        await query.message.reply_text(
            "📝 *Checkout: Step 1/2*\n\nPlease reply with your *phone number* (e.g. 0911...):",
            parse_mode='Markdown'
        )
    else:
        await query.message.edit_text(
            "📝 *Checkout: Step 1/2*\n\nPlease reply with your *phone number* (e.g. 0911...):",
            parse_mode='Markdown'
        )
    return PHONE

async def checkout_phone(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data['phone'] = update.message.text.strip()
    await update.message.reply_text(
        "📍 *Checkout: Step 2/2*\n\nPlease enter your *delivery address/location* details (e.g. Bole, around Medhanealem):",
        parse_mode='Markdown'
    )
    return LOCATION

async def checkout_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    location = update.message.text.strip()
    phone = ctx.user_data.get('phone', 'Unknown')
    telegram_id = update.effective_user.id
    
    await update.message.reply_text("⏳ Processing your order...")
    
    success, result = await db.run_in_db(db.place_order, telegram_id, phone, location)
    
    if success:
        text = (
            f"🎉 *Order Placed Successfully!*\n\n"
            f"📋 *Order Number:* `{result}`\n\n"
            f"We will call you shortly on {phone} to confirm delivery.\n"
            f"Thank you for shopping at Liyu Kids Mart! 💚"
        )
    else:
        text = f"❌ Order failed: {result}"
        
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=main_keyboard())
    return ConversationHandler.END

async def checkout_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Checkout cancelled.", reply_markup=main_keyboard())
    return ConversationHandler.END

# --- ORDERS & TRACKING ---

async def my_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    orders = await db.run_in_db(db.get_user_orders, update.effective_user.id)
    
    if not orders:
        text = "📦 You don't have any orders yet."
    else:
        text = "📦 *Your Recent Orders:*\n\n"
        for o in orders:
            emoji = {'pending': '⏳', 'confirmed': '✅', 'packed': '📦',
                     'out_for_delivery': '🚚', 'delivered': '🎉', 'cancelled': '❌'}.get(o.status.value, '📋')
            text += f"{emoji} *#{o.order_number}* — ETB {o.total:,.0f}\n"
            text += f"   Status: {o.status.name.title()}\n"
            text += f"   Date: {o.created_at.strftime('%Y-%m-%d')}\n\n"
            
    kb = [[InlineKeyboardButton("🛍️ Shop Now", callback_data="shop_cats"), back_menu_btn()]]
    
    try:
        if query.message.photo:
            await query.message.delete()
            await query.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
        else:
            await query.message.edit_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(kb))
    except Exception:
        pass

async def track_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = "🔍 To track your order, please reply with your *Order Number* (e.g. LKM-2024-12345):"
    
    try:
        if query.message.photo:
            await query.message.delete()
            await query.message.reply_text(text, parse_mode='Markdown')
        else:
            await query.message.edit_text(text, parse_mode='Markdown')
    except Exception:
        pass
        
    ctx.user_data['awaiting_track'] = True

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    if ctx.user_data.get('awaiting_track'):
        ctx.user_data.pop('awaiting_track', None)
        
        order = await db.run_in_db(db.get_order_by_number, text.upper())
        if order:
            emoji = {'pending': '⏳', 'confirmed': '✅', 'packed': '📦',
                     'out_for_delivery': '🚚', 'delivered': '🎉', 'cancelled': '❌'}.get(order.status.value, '📋')
            reply = (
                f"🔍 *Order Tracking*\n\n"
                f"📋 Order: `{order.order_number}`\n"
                f"{emoji} Status: *{order.status.name.title()}*\n"
                f"💲 Total: ETB {order.total:,.0f}\n"
            )
        else:
            reply = f"❌ Order `{text}` not found."
            
        await update.message.reply_text(reply, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[back_menu_btn()]]))
        return

    # Fallback response for generic text
    await update.message.reply_text(
        "Hello! I am the Liyu Kids Mart bot. 🤖\nPlease use the menu below to navigate.",
        reply_markup=main_keyboard()
    )

async def support_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "📞 *Contact Liyu Kids Mart*\n\n"
        "📍 Addis Ababa, Ethiopia\n"
        "💬 Telegram: @LiyuKidsMartAdmin\n"
        "📱 Phone: +251 911 234 567\n"
        "🚚 Delivery: Free for orders over ETB 1000\n\n"
        "We are here to help your child learn through play! 🌿"
    )
    
    try:
        if query.message.photo:
            await query.message.delete()
            await query.message.reply_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[back_menu_btn()]]))
        else:
            await query.message.edit_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[back_menu_btn()]]))
    except Exception:
        pass

# --- MAIN RUNNER ---

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.location:
        return
    lat = update.message.location.latitude
    lng = update.message.location.longitude
    user_id = update.effective_user.id
    
    # Try to update driver location
    is_driver = await db.run_in_db(db.update_driver_location, user_id, lat, lng)
    if is_driver:
        # We only send a reply if it's a static location. If it's a live location, 
        # Telegram sends multiple updates silently, we don't want to spam the driver.
        # But for confirmation, we might just log it or send one message if we wanted.
        logger.info(f"Updated driver {user_id} location to {lat}, {lng}")
    # If not driver, ignore location outside of checkout conversation


def register_handlers(app):
    """Attach all Telegram handlers to a PTB Application instance."""
    # Commands
    app.add_handler(CommandHandler("start", cmd_start))

    # Checkout Conversation
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(checkout_start, pattern="^checkout_start$")],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, checkout_phone)],
            LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, checkout_location)]
        },
        fallbacks=[CommandHandler("cancel", checkout_cancel)]
    )
    app.add_handler(conv_handler)

    # Callbacks
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(shop_categories, pattern="^shop_cats$"))
    app.add_handler(CallbackQueryHandler(category_products, pattern="^cat_"))
    app.add_handler(CallbackQueryHandler(product_detail, pattern="^prod_"))
    app.add_handler(CallbackQueryHandler(add_to_cart, pattern="^addcart_"))
    app.add_handler(CallbackQueryHandler(cart_view, pattern="^cart_view$"))
    app.add_handler(CallbackQueryHandler(cart_update, pattern="^cartupd_"))
    app.add_handler(CallbackQueryHandler(cart_clear, pattern="^cart_clear$"))
    app.add_handler(CallbackQueryHandler(my_orders, pattern="^my_orders$"))
    app.add_handler(CallbackQueryHandler(track_init, pattern="^track_init$"))
    app.add_handler(CallbackQueryHandler(support_info, pattern="^support$"))

    # Messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Live Location Updates
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))


def build_application():
    """Create a configured PTB Application."""
    application = Application.builder().token(TOKEN).build()
    register_handlers(application)
    return application


_application = None


def get_application():
    """Return a cached PTB Application for webhook dispatch."""
    global _application
    if _application is None:
        _application = build_application()
    return _application


async def process_webhook_update(payload):
    """Process a raw Telegram webhook payload."""
    app = get_application()
    if not getattr(app, "_initialized", False):
        await app.initialize()

    update = Update.de_json(payload, app.bot)
    if update is None:
        return False

    if not getattr(app, "_running", False):
        await app.start()
    try:
        await app.process_update(update)
    finally:
        if getattr(app, "_running", False):
            await app.stop()
    return True

def run_bot():
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        return

    logger.info(f"Starting Liyu Kids Mart Native Bot...")
    app = build_application()

    if BOT_MODE == 'polling':
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    else:
        webhook_url = os.getenv('TELEGRAM_WEBHOOK_URL')
        app.run_webhook(
            listen='0.0.0.0',
            port=8443,
            url_path=TOKEN,
            webhook_url=f"{webhook_url}/{TOKEN}",
        )
if __name__ == '__main__':
    run_bot()
