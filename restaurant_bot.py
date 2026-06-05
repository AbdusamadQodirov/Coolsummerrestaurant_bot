"""
╔══════════════════════════════════════════════════════════════╗
║         SAMARKAND KITCHEN — TELEGRAM BOT                     ║
║         Powered by Claude AI + Yandex Delivery API           ║
║                                                              ║
║  RENDER DEPLOYMENT — READY TO USE                            ║
║  Add these in Render → Environment Variables:                ║
║    TELEGRAM_TOKEN        → from @BotFather                   ║
║    CLAUDE_API_KEY        → from console.anthropic.com        ║
║    YANDEX_DELIVERY_TOKEN → from yandex.com/dev/logistics     ║
║    OWNER_TELEGRAM_ID     → your Telegram ID (from @userinfobot) ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import telebot
import anthropic
import requests
import json
import time
import threading
from datetime import datetime
from flask import Flask
from telebot import types

# ════════════════════════════════════════════
#  🔑  API KEYS — from Render Environment Variables
# ════════════════════════════════════════════

TELEGRAM_TOKEN        = os.environ.get("TELEGRAM_TOKEN", "")
CLAUDE_API_KEY        = os.environ.get("CLAUDE_API_KEY", "")
YANDEX_DELIVERY_TOKEN = os.environ.get("YANDEX_DELIVERY_TOKEN", "")
# Get Yandex Delivery token at: https://yandex.com/dev/logistics/api-go-delivery/

# ════════════════════════════════════════════
#  🍽️  RESTAURANT CONFIG
# ════════════════════════════════════════════

RESTAURANT = {
    "name":        "Samarkand Kitchen",
    "phone":       "+998 94 077 5372",
    "address":     "Toshkent, O'zbekiston",
    "lat":         41.466538,
    "lon":         69.468689,
    "hours":       "10:00 - 01:00",
    "min_order":   80000,   # UZS — minimum for delivery
    "currency":    "UZS",
}

MENU = {
    "🍚 Asosiy taomlar": [
        {"id": 1,  "name": "Osh (Plov)",   "price": 35000, "emoji": "🍚"},
        {"id": 2,  "name": "Shashlik",     "price": 45000, "emoji": "🍢"},
        {"id": 3,  "name": "Lagman",       "price": 28000, "emoji": "🍜"},
        {"id": 4,  "name": "Dimlama",      "price": 32000, "emoji": "🥘"},
        {"id": 5,  "name": "Manti",        "price": 30000, "emoji": "🥟"},
        {"id": 6,  "name": "So'msa",       "price": 15000, "emoji": "🥐"},
    ],
    "🥗 Salatlar": [
        {"id": 7,  "name": "Achichuk",     "price": 12000, "emoji": "🍅"},
        {"id": 8,  "name": "Toshkent salati", "price": 15000, "emoji": "🥗"},
    ],
    "☕ Ichimliklar": [
        {"id": 9,  "name": "Ko'k choy",   "price": 8000,  "emoji": "🍵"},
        {"id": 10, "name": "Ayron",        "price": 10000, "emoji": "🥛"},
        {"id": 11, "name": "Limonad",      "price": 12000, "emoji": "🍋"},
    ],
}

# Flat menu lookup by id
MENU_BY_ID = {item["id"]: item for cat in MENU.values() for item in cat}

# ════════════════════════════════════════════
#  🤖  CLAUDE AI SETUP
# ════════════════════════════════════════════

SYSTEM_PROMPT = f"""
You are a friendly restaurant chatbot for "{RESTAURANT['name']}" in Tashkent, Uzbekistan.

Restaurant info:
- Phone: {RESTAURANT['phone']}
- Address: {RESTAURANT['address']}
- Hours: {RESTAURANT['hours']} (every day)
- Minimum delivery order: {RESTAURANT['min_order']:,} UZS

Menu:
{chr(10).join(f"  {item['emoji']} {item['name']} — {item['price']:,} UZS" for cat in MENU.values() for item in cat)}

Rules:
- Reply in the SAME language the user writes in (Uzbek, Russian, or English)
- Keep replies SHORT — 2-3 sentences max, this is a chat
- Use 1-2 emojis naturally
- For orders: tell them to use the /menu button
- Signature dish: Osh (Plov) — always recommend it first
- Never invent prices or dishes not on the menu
- For delivery: "We deliver in Tashkent via Yandex courier, min order {RESTAURANT['min_order']:,} UZS"
- Be warm, helpful, and enthusiastic about Uzbek food
"""

claude_client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

# ════════════════════════════════════════════
#  💬  USER SESSION STORAGE (in-memory)
# ════════════════════════════════════════════
# In production, replace with a real DB (SQLite, PostgreSQL, etc.)

sessions = {}  # user_id -> { cart, history, state, address, name, phone }

def get_session(user_id):
    if user_id not in sessions:
        sessions[user_id] = {
            "cart":    {},   # item_id -> qty
            "history": [],   # Claude conversation history
            "state":   "idle",
            "address": None,
            "name":    None,
            "phone":   None,
        }
    return sessions[user_id]

# ════════════════════════════════════════════
#  🚚  YANDEX DELIVERY API
# ════════════════════════════════════════════

YANDEX_API_BASE = "https://b2b.taxi.yandex.net/b2b/cargo/integration/v2"

def create_yandex_delivery(order_info):
    """
    Creates a Yandex Delivery order and returns tracking info.
    Docs: https://yandex.com/dev/logistics/api-go-delivery/
    """
    headers = {
        "Authorization": f"Bearer {YANDEX_DELIVERY_TOKEN}",
        "Content-Type": "application/json",
        "Accept-Language": "ru",
    }

    # Build order items description
    items_text = ", ".join(
        f"{MENU_BY_ID[iid]['name']} x{qty}"
        for iid, qty in order_info["cart"].items()
    )

    payload = {
        "callback_properties": {
            "callback_url": ""   # Optional: your webhook to receive status updates
        },
        "client_requirements": {
            "cargo_options": ["thermobag"],   # Thermal bag for food
            "pro_courier": False,
        },
        "comment": f"Restaurant order: {items_text}",
        "items": [
            {
                "cost_currency": "UZS",
                "cost_value":    str(order_info["total"]),
                "droppof_point": 1,
                "extra_id":      f"order_{int(time.time())}",
                "pickup_point":  0,
                "quantity":      1,
                "size": {"height": 0.2, "length": 0.3, "width": 0.3},
                "title":         f"{RESTAURANT['name']} order",
                "weight":        2.0,
            }
        ],
        "route_points": [
            {
                # PICKUP — your restaurant
                "address": {
                    "coordinates":   [RESTAURANT["lon"], RESTAURANT["lat"]],
                    "fullname":      RESTAURANT["address"],
                    "comment":       "Restaurant entrance",
                },
                "contact": {
                    "name":  RESTAURANT["name"],
                    "phone": RESTAURANT["phone"].replace(" ", ""),
                },
                "point_id":    0,
                "skip_confirmation": True,
                "type":        "source",
                "visit_order": 1,
            },
            {
                # DROPOFF — customer address
                "address": {
                    "coordinates":   order_info["coordinates"],
                    "fullname":      order_info["address"],
                },
                "contact": {
                    "name":  order_info["customer_name"],
                    "phone": order_info["customer_phone"],
                },
                "point_id":    1,
                "skip_confirmation": False,
                "type":        "destination",
                "visit_order": 2,
            },
        ],
        "skip_door_to_door": False,
    }

    try:
        response = requests.post(
            f"{YANDEX_API_BASE}/claims/create?request_id={int(time.time())}",
            headers=headers,
            json=payload,
            timeout=10,
        )
        data = response.json()
        if response.status_code == 200:
            claim_id = data.get("id", "N/A")
            # Accept the claim (confirm the order)
            requests.post(
                f"{YANDEX_API_BASE}/claims/accept?claim_id={claim_id}",
                headers=headers,
                json={"version": 1},
                timeout=10,
            )
            return {"success": True, "claim_id": claim_id}
        else:
            return {"success": False, "error": str(data)}
    except Exception as e:
        return {"success": False, "error": str(e)}

def check_delivery_status(claim_id):
    """Check the status of a Yandex Delivery order."""
    headers = {"Authorization": f"Bearer {YANDEX_DELIVERY_TOKEN}"}
    try:
        r = requests.get(
            f"{YANDEX_API_BASE}/claims/info?claim_id={claim_id}",
            headers=headers,
            timeout=10,
        )
        data = r.json()
        return data.get("status", "unknown")
    except:
        return "unknown"

# ════════════════════════════════════════════
#  🤖  BOT SETUP
# ════════════════════════════════════════════

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# ─── Keyboards ───────────────────────────────

def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("📋 Menu"),
        types.KeyboardButton("🛒 Korzinka"),
        types.KeyboardButton("📍 Manzil"),
        types.KeyboardButton("🕐 Ish vaqti"),
        types.KeyboardButton("📞 Qo'ng'iroq qilish"),
        types.KeyboardButton("🛵 Yetkazib berish"),
    )
    return kb

def menu_inline(category=None):
    """Build inline keyboard for menu browsing."""
    kb = types.InlineKeyboardMarkup(row_width=1)
    if category is None:
        # Show categories
        for cat in MENU:
            kb.add(types.InlineKeyboardButton(cat, callback_data=f"cat:{cat}"))
    else:
        items = MENU.get(category, [])
        for item in items:
            kb.add(types.InlineKeyboardButton(
                f"{item['emoji']} {item['name']} — {item['price']:,} UZS",
                callback_data=f"add:{item['id']}"
            ))
        kb.add(types.InlineKeyboardButton("⬅️ Orqaga", callback_data="cat:back"))
    return kb

def cart_inline(cart):
    kb = types.InlineKeyboardMarkup(row_width=3)
    for item_id, qty in cart.items():
        item = MENU_BY_ID[item_id]
        kb.row(
            types.InlineKeyboardButton(f"➖", callback_data=f"dec:{item_id}"),
            types.InlineKeyboardButton(f"{item['emoji']} {item['name']} x{qty}", callback_data="noop"),
            types.InlineKeyboardButton(f"➕", callback_data=f"inc:{item_id}"),
        )
    kb.add(types.InlineKeyboardButton("🗑 Tozalash", callback_data="cart:clear"))
    kb.add(types.InlineKeyboardButton("✅ Buyurtma berish", callback_data="cart:order"))
    return kb

def location_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("📍 Lokatsiyamni yuboring", request_location=True))
    kb.add(types.KeyboardButton("✍️ Manzilni yozish"))
    kb.add(types.KeyboardButton("❌ Bekor qilish"))
    return kb

def contact_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("📱 Raqamimni yuboring", request_contact=True))
    kb.add(types.KeyboardButton("❌ Bekor qilish"))
    return kb

# ─── Helpers ─────────────────────────────────

def is_open():
    now = datetime.now()
    hour = now.hour
    # Open 10:00 - 01:00 (next day)
    return hour >= 10 or hour < 1

def format_cart(cart):
    if not cart:
        return "🛒 Korzinka bo'sh"
    lines = []
    total = 0
    for item_id, qty in cart.items():
        item = MENU_BY_ID[item_id]
        subtotal = item["price"] * qty
        total += subtotal
        lines.append(f"{item['emoji']} {item['name']} x{qty} = {subtotal:,} UZS")
    lines.append(f"\n💰 Jami: {total:,} UZS")
    if total < RESTAURANT["min_order"]:
        diff = RESTAURANT["min_order"] - total
        lines.append(f"⚠️ Minimum buyurtma: {RESTAURANT['min_order']:,} UZS (yana {diff:,} UZS kerak)")
    return "\n".join(lines)

def cart_total(cart):
    return sum(MENU_BY_ID[iid]["price"] * qty for iid, qty in cart.items())

def ask_claude(user_id, text):
    session = get_session(user_id)
    session["history"].append({"role": "user", "content": text})
    # Keep last 10 messages
    history = session["history"][-10:]
    try:
        resp = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=history,
        )
        reply = resp.content[0].text
        session["history"].append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        return f"Kechirasiz, xatolik yuz berdi. Iltimos qayta urinib ko'ring 🙏"

# ════════════════════════════════════════════
#  📨  MESSAGE HANDLERS
# ════════════════════════════════════════════

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    session = get_session(msg.from_user.id)
    session["state"] = "idle"
    name = msg.from_user.first_name or "do'stim"
    text = (
        f"🏺 Assalomu alaykum, {name}!\n\n"
        f"*{RESTAURANT['name']}*ga xush kelibsiz!\n"
        f"Milliy o'zbek taomlari — to'g'ridan-to'g'ri uyingizga! 🚴\n\n"
        f"⏰ Ish vaqti: {RESTAURANT['hours']}\n"
        f"📍 {RESTAURANT['address']}\n\n"
        f"Quyidagi tugmalardan birini tanlang 👇"
    )
    bot.send_message(msg.chat.id, text, parse_mode="Markdown", reply_markup=main_keyboard())

@bot.message_handler(commands=["menu"])
def cmd_menu(msg):
    bot.send_message(msg.chat.id, "📋 *Menyu* — kategoriyani tanlang:", parse_mode="Markdown", reply_markup=menu_inline())

@bot.message_handler(commands=["cart"])
def cmd_cart(msg):
    show_cart(msg.chat.id, msg.from_user.id)

def show_cart(chat_id, user_id):
    session = get_session(user_id)
    cart = session["cart"]
    text = f"🛒 *Sizning korzinkangiz:*\n\n{format_cart(cart)}"
    if cart:
        bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=cart_inline(cart))
    else:
        bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=main_keyboard())

# ─── Reply keyboard handlers ──────────────

@bot.message_handler(func=lambda m: m.text == "📋 Menu")
def handle_menu(msg):
    bot.send_message(msg.chat.id, "📋 *Menyu* — kategoriyani tanlang:", parse_mode="Markdown", reply_markup=menu_inline())

@bot.message_handler(func=lambda m: m.text == "🛒 Korzinka")
def handle_cart(msg):
    show_cart(msg.chat.id, msg.from_user.id)

@bot.message_handler(func=lambda m: m.text == "📍 Manzil")
def handle_address(msg):
    text = (
        f"📍 *Manzilimiz:*\n{RESTAURANT['address']}\n\n"
        f"🗺 Koordinatlar: {RESTAURANT['lat']}, {RESTAURANT['lon']}\n\n"
        f"Yandex Xaritada ochish uchun:\n"
        f"https://yandex.uz/maps/?pt={RESTAURANT['lon']},{RESTAURANT['lat']}&z=16&l=map"
    )
    # Send location pin
    bot.send_location(msg.chat.id, RESTAURANT["lat"], RESTAURANT["lon"])
    bot.send_message(msg.chat.id, text, parse_mode="Markdown", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == "🕐 Ish vaqti")
def handle_hours(msg):
    status = "🟢 Hozir ochiq!" if is_open() else "🔴 Hozir yopiq."
    text = (
        f"🕐 *Ish vaqtimiz:*\n"
        f"Har kuni: {RESTAURANT['hours']}\n\n"
        f"{status}"
    )
    bot.send_message(msg.chat.id, text, parse_mode="Markdown", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == "📞 Qo'ng'iroq qilish")
def handle_call(msg):
    text = (
        f"📞 *Telefon raqam:*\n"
        f"{RESTAURANT['phone']}\n\n"
        f"Buyurtma berish, savollar yoki bron uchun qo'ng'iroq qiling!"
    )
    bot.send_message(msg.chat.id, text, parse_mode="Markdown", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: m.text == "🛵 Yetkazib berish")
def handle_delivery_info(msg):
    text = (
        f"🛵 *Yetkazib berish haqida:*\n\n"
        f"✅ Toshkent bo'ylab yetkazib beramiz\n"
        f"✅ Kuryerlar: Yandex Go\n"
        f"✅ Minimum buyurtma: {RESTAURANT['min_order']:,} UZS\n"
        f"✅ Yetkazib berish vaqti: 30-50 daqiqa\n\n"
        f"Buyurtma berish uchun 📋 *Menu* tugmasini bosing!"
    )
    bot.send_message(msg.chat.id, text, parse_mode="Markdown", reply_markup=main_keyboard())

# ─── Location received ────────────────────

@bot.message_handler(content_types=["location"])
def handle_location(msg):
    session = get_session(msg.from_user.id)
    if session["state"] == "waiting_location":
        lat = msg.location.latitude
        lon = msg.location.longitude
        session["address"] = f"Geo: {lat},{lon}"
        session["coordinates"] = [lon, lat]
        session["state"] = "waiting_name"
        bot.send_message(
            msg.chat.id,
            "✅ Lokatsiya qabul qilindi!\n\n👤 Ismingizni kiriting:",
            reply_markup=types.ReplyKeyboardRemove()
        )

# ─── Contact received ─────────────────────

@bot.message_handler(content_types=["contact"])
def handle_contact(msg):
    session = get_session(msg.from_user.id)
    if session["state"] == "waiting_phone":
        session["phone"] = msg.contact.phone_number
        session["state"] = "waiting_confirm"
        send_order_confirmation(msg.chat.id, msg.from_user.id)

# ─── Inline keyboard callbacks ────────────

@bot.callback_query_handler(func=lambda c: c.data.startswith("cat:"))
def cb_category(call):
    cat = call.data[4:]
    if cat == "back":
        bot.edit_message_text(
            "📋 *Menyu* — kategoriyani tanlang:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=menu_inline()
        )
    else:
        bot.edit_message_text(
            f"📋 *{cat}*\n\nTaom tanlang:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=menu_inline(cat)
        )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("add:"))
def cb_add_item(call):
    item_id = int(call.data[4:])
    session = get_session(call.from_user.id)
    cart = session["cart"]
    cart[item_id] = cart.get(item_id, 0) + 1
    item = MENU_BY_ID[item_id]
    bot.answer_callback_query(call.id, f"✅ {item['name']} korzinkaga qo'shildi!", show_alert=False)

@bot.callback_query_handler(func=lambda c: c.data.startswith("inc:"))
def cb_inc(call):
    item_id = int(call.data[4:])
    session = get_session(call.from_user.id)
    session["cart"][item_id] = session["cart"].get(item_id, 0) + 1
    update_cart_message(call)

@bot.callback_query_handler(func=lambda c: c.data.startswith("dec:"))
def cb_dec(call):
    item_id = int(call.data[4:])
    session = get_session(call.from_user.id)
    cart = session["cart"]
    if cart.get(item_id, 0) > 1:
        cart[item_id] -= 1
    else:
        cart.pop(item_id, None)
    update_cart_message(call)

def update_cart_message(call):
    session = get_session(call.from_user.id)
    cart = session["cart"]
    if not cart:
        bot.edit_message_text("🛒 Korzinka bo'sh", call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id)
        return
    text = f"🛒 *Sizning korzinkangiz:*\n\n{format_cart(cart)}"
    bot.edit_message_text(
        text, call.message.chat.id, call.message.message_id,
        parse_mode="Markdown", reply_markup=cart_inline(cart)
    )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "cart:clear")
def cb_clear_cart(call):
    session = get_session(call.from_user.id)
    session["cart"] = {}
    bot.edit_message_text("🛒 Korzinka tozalandi.", call.message.chat.id, call.message.message_id)
    bot.answer_callback_query(call.id, "Korzinka tozalandi")

@bot.callback_query_handler(func=lambda c: c.data == "cart:order")
def cb_start_order(call):
    session = get_session(call.from_user.id)
    cart = session["cart"]
    if not cart:
        bot.answer_callback_query(call.id, "Korzinka bo'sh!", show_alert=True)
        return
    total = cart_total(cart)
    if total < RESTAURANT["min_order"]:
        bot.answer_callback_query(
            call.id,
            f"Minimum buyurtma {RESTAURANT['min_order']:,} UZS. Yana {RESTAURANT['min_order']-total:,} UZS qo'shing!",
            show_alert=True
        )
        return
    if not is_open():
        bot.answer_callback_query(call.id, "Kechirasiz, hozir yopiqmiz! 😔", show_alert=True)
        return

    session["state"] = "waiting_location"
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        "📍 *Yetkazish manzilini yuboring:*\n\nLokatsiyangizni yuboring yoki manzilni yozing:",
        parse_mode="Markdown",
        reply_markup=location_keyboard()
    )

@bot.callback_query_handler(func=lambda c: c.data == "confirm:yes")
def cb_confirm_order(call):
    session = get_session(call.from_user.id)
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        "⏳ Buyurtmangiz qabul qilinmoqda, kuryer yuklanmoqda...",
        call.message.chat.id,
        call.message.message_id
    )

    # Send to Yandex Delivery
    order_info = {
        "cart":          session["cart"],
        "total":         cart_total(session["cart"]),
        "address":       session.get("address", "Toshkent"),
        "coordinates":   session.get("coordinates", [RESTAURANT["lon"], RESTAURANT["lat"]]),
        "customer_name": session.get("name", "Mijoz"),
        "customer_phone": session.get("phone", RESTAURANT["phone"]),
    }

    result = create_yandex_delivery(order_info)

    if result["success"]:
        claim_id = result["claim_id"]
        text = (
            f"✅ *Buyurtmangiz qabul qilindi!*\n\n"
            f"🆔 Buyurtma ID: `{claim_id}`\n"
            f"🛵 Yandex kuryer yo'lda!\n"
            f"⏰ Taxminiy vaqt: 30-50 daqiqa\n\n"
            f"📞 Savol bo'lsa: {RESTAURANT['phone']}\n\n"
            f"Ishtahangiz chog'li bo'lsin! 🍽️"
        )
    else:
        # Fallback: manual call when API fails
        text = (
            f"✅ *Buyurtmangiz qabul qilindi!*\n\n"
            f"📞 Tez orada siz bilan bog'lanamiz: {RESTAURANT['phone']}\n"
            f"⏰ Taxminiy yetkazish: 30-50 daqiqa\n\n"
            f"Ishtahangiz chog'li bo'lsin! 🍽️"
        )

    # Notify restaurant owner (send order details to your own Telegram)
    notify_owner(session)

    # Reset session
    session["cart"] = {}
    session["state"] = "idle"

    bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=main_keyboard())

@bot.callback_query_handler(func=lambda c: c.data == "confirm:no")
def cb_cancel_order(call):
    session = get_session(call.from_user.id)
    session["state"] = "idle"
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "❌ Buyurtma bekor qilindi.", reply_markup=main_keyboard())

@bot.callback_query_handler(func=lambda c: c.data == "noop")
def cb_noop(call):
    bot.answer_callback_query(call.id)

# ─── Text state machine ───────────────────

@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_text(msg):
    session = get_session(msg.from_user.id)
    state = session["state"]
    text = msg.text.strip()

    if text == "❌ Bekor qilish":
        session["state"] = "idle"
        bot.send_message(msg.chat.id, "Bekor qilindi.", reply_markup=main_keyboard())
        return

    if state == "waiting_location" and text == "✍️ Manzilni yozish":
        bot.send_message(msg.chat.id, "✍️ To'liq manzilni kiriting:\n(Masalan: Yunusobod 19-mavze, 5-uy):", reply_markup=types.ReplyKeyboardRemove())
        session["state"] = "waiting_address_text"
        return

    if state == "waiting_address_text":
        session["address"] = text
        # Geocode address using Yandex Geocoder (free)
        try:
            geo_url = f"https://geocode-maps.yandex.ru/1.x/?apikey=demos&geocode=Toshkent,{text}&format=json&results=1"
            geo_r = requests.get(geo_url, timeout=5).json()
            pos = geo_r["response"]["GeoObjectCollection"]["featureMember"][0]["GeoObject"]["Point"]["pos"]
            lon, lat = map(float, pos.split())
            session["coordinates"] = [lon, lat]
        except:
            session["coordinates"] = [RESTAURANT["lon"], RESTAURANT["lat"]]
        session["state"] = "waiting_name"
        bot.send_message(msg.chat.id, "👤 Ismingizni kiriting:")
        return

    if state == "waiting_name":
        session["name"] = text
        session["state"] = "waiting_phone"
        bot.send_message(
            msg.chat.id,
            "📱 Telefon raqamingizni yuboring:",
            reply_markup=contact_keyboard()
        )
        return

    if state == "waiting_phone":
        # Manual phone entry (if they don't use button)
        if text.startswith("+") or text.isdigit():
            session["phone"] = text
            session["state"] = "waiting_confirm"
            send_order_confirmation(msg.chat.id, msg.from_user.id)
        else:
            bot.send_message(msg.chat.id, "Iltimos, telefon raqamingizni kiriting (+998xxxxxxxxx):")
        return

    # Default: ask Claude AI
    typing_action = bot.send_chat_action(msg.chat.id, "typing")
    reply = ask_claude(msg.from_user.id, text)
    bot.send_message(msg.chat.id, reply, reply_markup=main_keyboard())

# ─── Order confirmation ───────────────────

def send_order_confirmation(chat_id, user_id):
    session = get_session(user_id)
    cart_text = format_cart(session["cart"])
    text = (
        f"📋 *Buyurtmangizni tasdiqlang:*\n\n"
        f"{cart_text}\n\n"
        f"👤 Ism: {session.get('name', '-')}\n"
        f"📱 Tel: {session.get('phone', '-')}\n"
        f"📍 Manzil: {session.get('address', '-')}\n\n"
        f"*Tasdiqlaysizmi?*"
    )
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("✅ Ha, buyurtma berish", callback_data="confirm:yes"),
        types.InlineKeyboardButton("❌ Yo'q", callback_data="confirm:no"),
    )
    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=kb)

def notify_owner(session):
    """Send order details to the restaurant owner's Telegram."""
    OWNER_TELEGRAM_ID = int(os.environ.get("OWNER_TELEGRAM_ID", "0"))
    if not OWNER_TELEGRAM_ID:
        return  # Skip if not configured

    cart_text = "\n".join(
        f"  • {MENU_BY_ID[iid]['name']} x{qty} = {MENU_BY_ID[iid]['price']*qty:,} UZS"
        for iid, qty in session["cart"].items()
    )
    total = cart_total(session["cart"])
    text = (
        f"🔔 *YANGI BUYURTMA!*\n\n"
        f"👤 {session.get('name', '-')}\n"
        f"📱 {session.get('phone', '-')}\n"
        f"📍 {session.get('address', '-')}\n\n"
        f"*Taomlar:*\n{cart_text}\n\n"
        f"💰 *Jami: {total:,} UZS*\n"
        f"🛵 Yandex kuryer chaqirildi"
    )
    try:
        bot.send_message(OWNER_TELEGRAM_ID, text, parse_mode="Markdown")
    except:
        pass  # Don't crash if owner notification fails

# ════════════════════════════════════════════
#  🌐  FLASK WEB SERVER (keeps Render awake)
#  UptimeRobot pings this URL every 5 min
#  so Render never sleeps — stays 24/7 free
# ════════════════════════════════════════════

flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return f"🍽️ {RESTAURANT['name']} bot is running! ✅"

@flask_app.route("/health")
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port)

# ════════════════════════════════════════════
#  🚀  START THE BOT
# ════════════════════════════════════════════

if __name__ == "__main__":
    print(f"🍽️  {RESTAURANT['name']} boti ishga tushdi!")
    print(f"📱 Telegram botingizni oching va /start yozing")
    print("=" * 50)

    # Start Flask in background thread (for Render keep-alive)
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("🌐 Web server started (for UptimeRobot keep-alive)")

    # Start Telegram bot
    bot.infinity_polling(timeout=30, long_polling_timeout=30)
