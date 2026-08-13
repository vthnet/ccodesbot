import os
import urllib.parse
import json
import re
import asyncio
import aiohttp
from aiogram import BaseMiddleware
from typing import Callable, Dict, Any, Awaitable
from aiogram.types import TelegramObject
import html
from html import escape
from bson import ObjectId
from aiogram.types import CopyTextButton
from telethon.tl.functions.account import GetAuthorizationsRequest, ResetAuthorizationRequest
from aiogram.fsm.context import FSMContext
from datetime import datetime, timezone, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, StateFilter
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from pymongo import MongoClient
from telethon import TelegramClient
from telethon.sessions import StringSession
from aiogram.utils.deep_linking import create_start_link
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, PasswordHashInvalidError
import re
from aiogram import types
import random
from aiogram.types import InputMediaVideo
from .recharge_flow import register_recharge_handlers
from .mustjoin import check_join

import time
from html import escape
from datetime import datetime, timezone
from bson import ObjectId
from aiogram import F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, CopyTextButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


from  config import BOT_TOKEN, ADMIN_IDS, DATABASE_URL
# ================= MongoDB Setup =================

client = MongoClient(DATABASE_URL)
db = client["SellingBot"]
users_col = db["users"]
orders_col = db["orders"]
countries_col = db["countries"]
numbers_col = db["numbers"]
crypto_col = db["crypto_invoices"]
withdrawals_col = db["withdrawals"]
settings_col = db["settings"]

#--------- Config : don't use @
BOTUSER = "ccodes_bot"
SUPPORT = "vthnetsupport"
USAGE = "vthnet"
OWNER = "valriks"
UPDATES= "vthnet"
CHANNEL="vthchannel"
SALESLOG = "-1003349993686"
ADMINLOG = "-1003208353049"
# ================= API Configuration (Server 2) =================
TGLION_API_KEY = "api key"
TGLION_ID = "7888066934"
TGPVA_API_KEY = " key "

# ================= Ban Middleware =================
class BanCheckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if user:
            user_doc = await asyncio.to_thread(users_col.find_one, {"_id": user.id})
            if user_doc and user_doc.get("banned", False):
                # If event is a message, reply. If callback, answer alert.
                if isinstance(event, Message):
                    await event.answer("🚫 <b>You are banned from using this bot.</b>", parse_mode="HTML")
                elif isinstance(event, CallbackQuery):
                    await event.answer("🚫 You are banned from using this bot.", show_alert=True)
                return # Stop processing
        return await handler(event, data)
        
# ================= Bot Setup =================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

@dp.callback_query(F.data == "s3tg_open")
async def s3tg_open(cq: CallbackQuery):
    await cq.answer()
    cfg = await asyncio.to_thread(settings_col.find_one, {"_id": "server_config"}) or {}

    if not cfg.get("lzt_enabled", False):
        return await cq.answer(
            "⚠️ Currently unavailable.",
            show_alert=True
        )

    terms_text = (
        "<b>⚠️ Server 3 — High-Risk Accounts</b>\n\n"
        "<blockquote>"
        "These accounts are offered at very low prices and may carry "
        "a higher risk of unexpected logout or loss of access because they are phishin... acc owner device is already active in most of the accounts ."
        "</blockquote>\n\n"
        "⚠️ <b>Please read before continuing:</b>\n\n"
        "• Accounts may stop working or become unavailable at any time.\n"
        "• Purchase only if you understand and accept this risk.\n"
        "• No refund or support is guaranteed for account logout or "
        "loss of access caused by the account's condition.\n"
        "• Tips and updates may be posted in the updates channel to "
        "help reduce common problems, but they do not guarantee that "
        "an account will remain active.\n\n"
        "<b>By continuing, you acknowledge these risks and agree to "
        "the applicable purchase terms.</b>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    style="primary",
                    text="✅ Agree & Continue",
                    callback_data="s3tg_terms_agree"
                )
            ],
            [
                InlineKeyboardButton(
                    style="primary",
                    text="🔙 Back",
                    callback_data="buy"
                )
            ]
        ]
    )

    await cq.message.edit_text(
        terms_text,
        parse_mode="HTML",
        reply_markup=kb
    )

    await cq.answer()


# ================= FSM =================
class AddSession(StatesGroup):
    waiting_country = State()
    waiting_number = State()
    waiting_otp = State()
    waiting_password = State()
    waiting_next_action = State()   # ✅ REQUIRED

class SellSession(StatesGroup):
    # ... existing states ...
    waiting_sell_number = State()

class WithdrawState(StatesGroup):
    waiting_upi = State()
    waiting_amount = State()

class AdminTxnState(StatesGroup):
    waiting_txn = State()

class ServerManage(StatesGroup):
    waiting_profit_margin = State()

    
# ================ Helpers =================
def get_or_create_user(user_id: int, username: str | None):
    user = users_col.find_one({"_id": user_id})
    if not user:
        user = {"_id": user_id, "username": username or None, "balance": 0.0}
        users_col.insert_one(user)
    return user

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS
def _mongo_id(value):
    """Resolve callback/admin string IDs to Mongo ObjectId when possible."""
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    return value

def _id_candidates(value):
    mid = _mongo_id(value)
    return [mid, str(mid)] if isinstance(mid, ObjectId) else [mid]

def _find_by_id(collection, value, extra=None):
    for candidate in _id_candidates(value):
        q = {"_id": candidate}
        if extra:
            q.update(extra)
        doc = collection.find_one(q)
        if doc:
            return doc
    return None

def _update_by_id(collection, value, update, extra=None):
    for candidate in _id_candidates(value):
        q = {"_id": candidate}
        if extra:
            q.update(extra)
        r = collection.update_one(q, update)
        if r.matched_count:
            return r
    return None


def get_user_balance(user_id):
    user = users_col.find_one({"_id": user_id})
    return user.get("balance", 0) if user else 0
# ================ Automatic OTP Listener =================
# ================ Automatic OTP Listener =================
async def otp_listener(number_doc, user_id, message_id):
    string_session = number_doc.get("string_session")
    if not string_session:
        return

    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")

    client = TelegramClient(StringSession(string_session), api_id, api_hash)
    await client.connect()

    try:
        if not await client.is_user_authorized():
            return

        pattern = re.compile(r"\b\d{5}\b")  # OTP pattern

        async for msg in client.iter_messages(777000, limit=10):
            if not msg.message:
                continue

            match = pattern.search(msg.message)
            if not match:
                continue

            # ===== OTP FOUND =====
            code = match.group(0)
            password_text = number_doc.get("password") or "None"

            

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(style="primary", 
                            text="Copy OTP",
                            copy_text=CopyTextButton(text=code)
                        ),
                        InlineKeyboardButton(style="primary", 
                            text="Copy Pass",
                            copy_text=CopyTextButton(text=password_text)
                        )
                    ],
                    [
                        InlineKeyboardButton(style="primary", 
                            text="• Get Code Again •",
                            callback_data=f"get_otp:{number_doc['number']}"
                        )
                    ],
                    [
                        InlineKeyboardButton(style="primary", 
                            text="Remove Bot Session",
                            callback_data=f"logout_bot:{number_doc['number']}"
                        )
                    ],
                    [
                        InlineKeyboardButton(style="primary", 
                            text="📱 Manage Devices",
                            callback_data=f"manage_devices:{number_doc['number']}"
                        )
                    ],
                    
                    
                    
                ]
            )

            await bot.edit_message_text(
                chat_id=user_id,
                message_id=message_id,
                text=(
                    "<pre>Order Completed ✅</pre>\n"
                    f"✅ 𝐍𝗨𝐌𝐁𝐄𝐑 - <code>+{number_doc['number']}</code>\n"
                    f"💬 𝐂𝐎𝐃𝐄 - <code>{code}</code>\n"
                    f"💬 𝐏𝐀𝐒𝐒 - <code>{password_text}</code>\n"
                ),
                parse_mode="HTML",
                reply_markup=kb
            )

            # ===== USER & LOGGING =====
            user = users_col.find_one({"_id": user_id}) or {}
            buyer_name = user.get("username") or f"User {user_id}"
            balance = user.get("balance", "N/A")

            country = number_doc.get("country", "Unknown")
            price = number_doc.get("price", "N/A")
            number = str(number_doc.get("number", "Unknown"))

            if number != "Unknown":
                if not number.startswith("+"):
                    number = f"+{number}"
                masked_number = number[:6] + "•••••"
            else:
                masked_number = "Hidden"

            channel_message = (
                f"<pre><u>✅ <b>New Number Purchase Successful</b></u></pre>\n\n"
                f"➖ <b><u>Country:</u></b> {country}\n"
                f"➖ <b><u>Application:</u> Теlegгам 🍷</b>\n\n"
                f"➕ <b>Number: {masked_number} 📞</b>\n"
                f"➕ <b>OTP:</b> <span class='tg-spoiler'>{code}</span> 💬\n"
                f"➕ <b>Server:</b> (1) 🥂\n"
                f"➕ <b>Password:</b> <span class='tg-spoiler'>{password_text}</span> 🔐\n\n"
                f"<b>• @{BOTUSER}|| @{CHANNEL}</b>"
            )

            buy_button = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(style="primary", 
                            text="• Buy Now •",
                            url=f"https://t.me/{BOTUSER}?start=starting"
                        )
                    ]
                ]
            )

            await bot.send_message(
                SALESLOG,
                channel_message,
                parse_mode="HTML",
                reply_markup=buy_button
            )

            admin_message = (
                f"<pre>📢 New Purchase Alert</pre>\n\n"
                f"<b>• Application:</b> Telegram\n"
                f"<b>• Country:</b> {country}\n"
                f"<b>• Number:</b> {number}\n"
                f"<b>• OTP:</b> <code>{code}</code>\n"
                f"➖ <b>Password:</b> <span class='tg-spoiler'>{password_text}</span> 🔐\n\n"
                f"<b>👤 User:</b> @{buyer_name} (<code>{user_id}</code>)\n"
                f"<b>💰 Balance:</b> ₹{balance}"
            )
            userbutton = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(style="primary", 
                            text="USER ID",
                            url=f"tg://openmessage?user_id={user_id}"
                        )
                    ]
                ]
            )

            await bot.send_message(
                ADMINLOGS,
                admin_message,
                parse_mode="HTML",
                reply_markup=userbutton
            )

            # ===== SAVE OTP =====
            numbers_col.update_one(
                {"_id": number_doc["_id"]},
                {
                    "$set": {
                        "last_otp": code,
                        "otp_fetched_at": datetime.now(timezone.utc)
                    }
                }
            )

            break  # stop after OTP

        await asyncio.sleep(1)
    except Exception as e:
        await callback.answer(
            user_id, "💬 No new code Received, Try sending new code first ")

    finally:
        await client.disconnect()
        

  

@dp.callback_query(F.data.startswith("manage_devices:"))
async def manage_devices(call: CallbackQuery):
    number = call.data.split(":", 1)[1]
    doc = numbers_col.find_one({"number": number})

    if not doc or not doc.get("string_session"):
        return await call.answer("❌ No active session", show_alert=True)

    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")

    client = TelegramClient(
        StringSession(doc["string_session"]),
        api_id,
        api_hash
    )
    await client.connect()

    try:
        sessions = await client(GetAuthorizationsRequest())
    except Exception:
        await client.disconnect()
        return await call.answer("❌ Failed to fetch sessions", show_alert=True)

    kb = InlineKeyboardMarkup(inline_keyboard=[])

    for s in sessions.authorizations:
        if s.current:
            continue  # cannot remove current via hash

        kb.inline_keyboard.append([
            InlineKeyboardButton(style="primary", 
                text=f"{s.device_model} | {s.platform}",
                callback_data=f"kill_session:{number}:{s.hash}"
            )
        ])

    await client.disconnect()

    if not kb.inline_keyboard:
        return await call.message.answer("✅ No removable sessions")

    await call.message.answer(
        "📱 Click any session to remove:",
        reply_markup=kb
    )


#-----Temrinate sessuon
@dp.callback_query(F.data.startswith("kill_session:"))
async def kill_session(call: CallbackQuery):
    _, number, session_hash = call.data.split(":")
    doc = numbers_col.find_one({"number": number})

    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")

    client = TelegramClient(
        StringSession(doc["string_session"]),
        api_id,
        api_hash
    )
    await client.connect()

    try:
        await client(ResetAuthorizationRequest(hash=int(session_hash)))
        await call.answer("✅ Session removed", show_alert=True)
    except Exception:
        await call.answer("❌ Cannot remove session", show_alert=True)
    finally:
        await client.disconnect()

    #&------Logout bot

@dp.callback_query(F.data.startswith("logout_bot:"))
async def logout_bot(call: CallbackQuery):
    number = call.data.split(":", 1)[1]
    doc = numbers_col.find_one({"number": number})

    if not doc or not doc.get("string_session"):
        return await call.answer("❌ No active session", show_alert=True)

    try:
        await call.answer("🧹 Removing old sessions...", show_alert=False)
    except TelegramBadRequest:
        pass

    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    client = TelegramClient(StringSession(doc["string_session"]), api_id, api_hash)
    removed = blocked = 0

    try:
        await client.connect()
        removed, blocked = await cleanup_old_telegram_sessions(client)
    except Exception as exc:
        await client.disconnect()
        return await call.message.answer(
            f"❌ <b>Session cleanup failed.</b>\n\n<code>{escape(str(exc))}</code>",
            parse_mode="HTML"
        )
    finally:
        if client.is_connected():
            await client.disconnect()

    await call.message.answer(
        "🧹 <b>Old Sessions Cleanup Complete</b>\n\n"
        f"📱 Number: <code>{escape(number)}</code>\n\n"
        f"✅ Removed: <b>{removed}</b>\n"
        f"⚠️ Not removed: <b>{blocked}</b>\n\n"
        "🛡️ <b>Current/login device was protected.</b>\n"
        "🔐 Saved StringSession remains intact.\n\n"
        "If Telegram enforces its 24-hour restriction, affected old sessions cannot be removed until Telegram allows it.",
        parse_mode="HTML"
    )

# ================ START =================
@dp.message(Command("start"))
async def cmd_start(m: Message):
    args = m.text.split()
    referred_by = None
    if len(args) > 1 and args[1].startswith("ref"):
        try: referred_by = int(args[1][3:])
        except Exception: referred_by = None

    user = users_col.find_one({"_id": m.from_user.id})
    is_new_user = user is None
    if is_new_user:
        user_data = {
            "_id": m.from_user.id,
            "username": m.from_user.username or None,
            "balance": 0.0,
            "joined_at": datetime.now(timezone.utc),
            "referral_credited": False,
        }
        if referred_by and referred_by != m.from_user.id and users_col.find_one({"_id": referred_by}):
            user_data["pending_referrer"] = referred_by
        users_col.insert_one(user_data)
    else:
        users_col.update_one({"_id": m.from_user.id}, {"$set": {"username": m.from_user.username or None}})

    if not await check_join(bot, m):
        return

    # Referral credit is ONLY for genuinely new users and only after force-join verification.
    current = users_col.find_one({"_id": m.from_user.id})
    pending_referrer = current.get("pending_referrer") if current else None
    if is_new_user and pending_referrer and not current.get("referral_credited"):
        referrer = users_col.find_one({"_id": int(pending_referrer)})
        if referrer:
            users_col.update_one({"_id": int(pending_referrer)}, {"$inc": {"balance": 0.50, "valid_referrals": 1, "referral_earnings": 0.50}})
            users_col.update_one({"_id": m.from_user.id}, {"$set": {"referred_by": int(pending_referrer), "referral_credited": True}, "$unset": {"pending_referrer": ""}})
            try:
                await bot.send_message(int(pending_referrer), f"🎉 <b>Valid Referral!</b>\n{escape(m.from_user.full_name)} completed the required join verification.\n\n💰 <b>₹0.50</b> has been added to your wallet.", parse_mode="HTML")
            except Exception:
                pass

    # Ensure user exists in DB
    get_or_create_user(m.from_user.id, m.from_user.username)
    user_id = m.from_user.id
    full_name = m.from_user.full_name  # always use the name
    safe_name = escape(full_name)
    user_mention = f"<a href='tg://user?id={user_id}'>{safe_name}</a>"
    user = users_col.find_one({"_id": user_id})
    balance = f"₹{user['balance']:.2f} " if user else "₹0 "
    
# ================= Main Start Menu =================
    # Calculate Balance
    balance_inr = user.get("balance", 0.0) if user else 0.0
    balance_usdt = balance_inr / 95.0

    caption = (
        f"<blockquote> Hey, {user_mention}!</blockquote>\n"
        f"<b>𝖶𝖾𝗅𝖼𝗈𝗆𝖾 𝖳𝗈 Account Robot- 𝖥𝖺𝗌𝗍𝖾𝗌𝗍 𝖳𝖾𝗅𝖾𝗀𝗋𝖺𝗆 𝖠𝖼𝖼𝗈𝗎𝗇𝗍 𝖲𝖾𝗅𝗅𝖾𝗋 𝖡𝗈𝗍🥂</b>\n\n"
        f"<b>🚀 𝖤𝗇𝗃𝗈𝗒 𝖥𝖺𝗌𝗍 𝖠𝖼𝖼𝗈𝗎𝗇𝗍 𝖻𝗎𝗒𝗂𝗇𝗀 𝖤𝗑𝗉𝖾𝗋𝗂𝖾𝗇𝖼𝖾 !\n------------------------------------------------\n"
        f"• Your ID - <code>{user_id}</code>\n"
        f"• Your Balance - ₹{balance_inr:.2f} | ${balance_usdt:.2f}</b>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(style="primary", text="🛒 Buy Telegram Account", callback_data="buy")
        ],
        [
            InlineKeyboardButton(style="primary", text="💸 Balance", callback_data="balance")
        ],
        [
            InlineKeyboardButton(style="primary", text="💳 Recharge", callback_data="recharge"),
            InlineKeyboardButton(style="primary", text="👤 Account", callback_data="stats")
        ],
        [
            InlineKeyboardButton(style="primary", text="➕ More..", callback_data="more_menu"),
            InlineKeyboardButton(style="primary", text="⚡ Refer & Earn", callback_data="refer")
        ],
        [
            InlineKeyboardButton(style="primary", text="💬 Feedback", callback_data="feedback")
        ]
    ])
    await m.answer(caption, parse_mode="HTML", reply_markup=kb)



# ================= Digital Content Store =================
content_categories = db["content_categories"]
content_products = db["content_products"]
content_sales = db["content_sales"]
smm_apps = db["smm_apps"]
smm_services = db["smm_services"]
smm_orders = db["smm_orders"]
feedback_col = db["feedback"]

class ContentAdminState(StatesGroup):
    waiting_category = State()
    waiting_product_category = State()
    waiting_product_data = State()
    waiting_content_product = State()
    waiting_content = State()

class ModernAdminState(StatesGroup):
    action = State()
    parent = State()
    name = State()

    # SMM service fields
    service_id = State()
    cost = State()
    minimum = State()
    maximum = State()
    min = State()
    max = State()
    description = State()
    margin_percent = State()

    # Content/product fields
    product_name = State()
    product_price = State()
    product_description = State()
    product_notes = State()
    content_target = State()

    # Editing
    edit_field = State()

class SMMAdminState(StatesGroup):
    waiting_app = State()
    waiting_service = State()
    waiting_edit = State()

class SMMOrderState(StatesGroup):
    waiting_link = State()
    waiting_amount = State()

class FeedbackState(StatesGroup):
    waiting_text = State()

async def _send_content_item(chat_id, item, prefix=""):
    kind=item.get("kind")
    try:
        if kind == "document": await bot.send_document(chat_id, item["file_id"], caption=prefix + item.get("caption", ""))
        elif kind == "photo": await bot.send_photo(chat_id, item["file_id"], caption=prefix + item.get("caption", ""))
        elif kind == "video": await bot.send_video(chat_id, item["file_id"], caption=prefix + item.get("caption", ""))
        elif kind == "audio": await bot.send_audio(chat_id, item["file_id"], caption=prefix + item.get("caption", ""))
        elif kind == "animation": await bot.send_animation(chat_id, item["file_id"], caption=prefix + item.get("caption", ""))
        elif kind == "link": await bot.send_message(chat_id, prefix + f"🔗 <a href=\"{escape(item['value'])}\">Open content</a>", parse_mode="HTML")
        else: await bot.send_message(chat_id, prefix + escape(item.get("value", "")), parse_mode="HTML")
    except Exception as e:
        print("Content delivery error:", e)

@dp.callback_query(F.data == "content_store")
async def content_store(cq: CallbackQuery):
    cats=list(content_categories.find({"active": True}).sort("name",1))
    kb=InlineKeyboardBuilder()
    for c in cats: kb.button(style="primary", text=c["name"], callback_data=f"content_cat:{c['_id']}")
    kb.adjust(2)
    kb.row(InlineKeyboardButton(style="primary", text="🔙 Back", callback_data="back_main"))
    await cq.message.edit_text("<b>📦 Other Content</b>\n\nSelect a category:", parse_mode="HTML", reply_markup=kb.as_markup())
    await cq.answer()

@dp.callback_query(F.data.startswith("content_cat:"))
async def content_category(cq: CallbackQuery):
    cat_id=cq.data.split(":",1)[1]
    children=list(content_categories.find({"parent_id": {"$in": _id_candidates(cat_id)}, "active":True}).sort("name",1))
    products=list(content_products.find({"category_id": {"$in": _id_candidates(cat_id)}, "active":True}).sort("name",1))
    kb=InlineKeyboardBuilder()
    for c in children: kb.button(style="primary", text=c["name"], callback_data=f"content_cat:{c['_id']}")
    for p in products: kb.button(style="primary", text=f"{p['name']} — ₹{p['price']:.2f}", callback_data=f"content_product:{p['_id']}")
    kb.adjust(1)
    parent=_find_by_id(content_categories, cat_id)
    back=f"content_cat:{parent['parent_id']}" if parent and parent.get("parent_id") else "content_store"
    kb.row(InlineKeyboardButton(style="primary", text="🔙 Back", callback_data=back))
    await cq.message.edit_text(f"<b>📦 {escape(parent['name']) if parent else 'Content'}</b>\n\nChoose content:", parse_mode="HTML", reply_markup=kb.as_markup())
    await cq.answer()

@dp.callback_query(F.data.startswith("content_product:"))
async def content_product_preview(cq: CallbackQuery):
    pid=cq.data.split(":",1)[1]; p=_find_by_id(content_products, pid, {"active":True})
    if not p: return await cq.answer("Product unavailable.",show_alert=True)
    text=(f"<b>📦 {escape(p['name'])}</b>\n\n{escape(p.get('description',''))}\n\n💰 <b>Price:</b> ₹{p['price']:.2f}\n\n⚠️ You will receive the files/links and notes immediately after purchase.")
    kb=InlineKeyboardBuilder(); kb.row(InlineKeyboardButton(style="primary", text=f"💳 Buy — ₹{p['price']:.2f}",callback_data=f"content_buy:{pid}")); kb.row(InlineKeyboardButton(style="primary", text="🔙 Back",callback_data=f"content_cat:{p['category_id']}"))
    await cq.message.edit_text(text,parse_mode="HTML",reply_markup=kb.as_markup()); await cq.answer()

@dp.callback_query(F.data.startswith("content_buy:"))
async def content_buy(cq: CallbackQuery):
    pid=cq.data.split(":",1)[1]; p=_find_by_id(content_products, pid, {"active":True}); uid=cq.from_user.id
    if not p: return await cq.answer("Product unavailable.",show_alert=True)
    user=get_or_create_user(uid,cq.from_user.username); price=float(p["price"])
    if user.get("balance",0)<price: return await cq.answer("❌ Insufficient balance. Please recharge.",show_alert=True)
    users_col.update_one({"_id":uid},{"$inc":{"balance":-price}})
    sale={"user_id":uid,"product_id":pid,"product_name":p["name"],"price":price,"created_at":datetime.now(timezone.utc),"status":"completed"}
    content_sales.insert_one(sale)
    await cq.message.edit_text("<b>✅ Purchase successful!</b>\n\nDelivering your content...",parse_mode="HTML")
    if p.get("notes"): await bot.send_message(uid, f"📝 <b>Notes</b>\n{escape(p['notes'])}",parse_mode="HTML")
    for item in p.get("content",[]): await _send_content_item(uid,item)
    log=(f"🛍️ <b>NEW CONTENT SALE</b>\n\n📦 <b>Product:</b> {escape(p['name'])}\n👤 <b>Buyer:</b> <a href='tg://user?id={uid}'>{escape(cq.from_user.full_name)}</a>\n🆔 <code>{uid}</code>\n💰 <b>Price:</b> ₹{price:.2f}")
    try: await bot.send_message(SALESLOG,log,parse_mode="HTML")
    except Exception: pass
    try: await bot.send_message(ADMINLOG,log+f"\n🧾 <b>Order:</b> <code>{sale['_id']}</code>",parse_mode="HTML")
    except Exception: pass
    await cq.answer("Delivered!",show_alert=False)

@dp.callback_query(F.data == "feedback")
async def feedback_start(cq: CallbackQuery,state:FSMContext):
    await state.set_state(FeedbackState.waiting_text); await cq.message.edit_text("💬 <b>Send your feedback</b>\n\nWrite your message below. It will be forwarded to the owner.",parse_mode="HTML"); await cq.answer()

@dp.message(StateFilter(FeedbackState.waiting_text))
async def feedback_save(msg:Message,state:FSMContext):
    await state.clear(); text=msg.text or msg.caption or "[non-text feedback]"
    feedback_col.insert_one({"user_id":msg.from_user.id,"username":msg.from_user.username,"text":text,"created_at":datetime.now(timezone.utc)})
    try: await bot.send_message(OWNER,f"💬 <b>New Feedback</b>\n\n👤 {escape(msg.from_user.full_name)}\n🆔 <code>{msg.from_user.id}</code>\n\n{escape(text)}",parse_mode="HTML")
    except Exception: pass
    await msg.answer("✅ Thanks! Your feedback has been forwarded to the owner.")


@dp.callback_query(F.data.in_({"cadm:add_cat","cadm:add_subcat","cadm:edit_cat","cadm:remove_cat","cadm:cats","cadm:add_product","cadm:edit_product","cadm:remove_product","cadm:add_content","cadm:products","cadm:back"}))
async def content_admin_callback(cq:CallbackQuery,state:FSMContext):
    try:
        await cq.answer()
    except TelegramBadRequest:
        pass
    if not is_admin(cq.from_user.id): return await cq.answer("Unauthorized",show_alert=True)
    action=cq.data.split(":",1)[1]; mid=cq.message.message_id; chat=cq.message.chat.id
    if action=="back": return await _content_admin_panel(chat,mid)
    if action=="add_cat":
        await state.update_data(panel_mid=mid,action="category"); await state.set_state(ModernAdminState.name)
        await cq.message.edit_text("➕ <b>Add Category</b>\n\nSend category name:",parse_mode="HTML"); return await cq.answer()
    if action=="add_subcat":
        cats=list(content_categories.find({"active":True,"parent_id":None}).sort("name",1))
        rows=[[InlineKeyboardButton(style="primary",text=x["name"],callback_data=f"cadm:addsubcat:{x['_id']}")] for x in cats]
        rows.append([InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="cadm:back")])
        return await cq.message.edit_text("➕ <b>Add Subcategory</b>\n\nChoose the parent category:",parse_mode="HTML",reply_markup=_panel_kb(rows))
    if action in ("cats","products"):
        coll=content_categories if action=="cats" else content_products
        docs=list(coll.find({}).sort("name",1))
        text=f"<b>📋 {'Categories' if action=='cats' else 'Products'}</b>\n\n"+("\n".join(f"• <code>{x['_id']}</code> — {escape(x.get('name',''))}" for x in docs) or "Empty.")
        return await cq.message.edit_text(text,parse_mode="HTML",reply_markup=_panel_kb([[InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="cadm:back")]]))
    if action=="edit_cat" or action=="remove_cat":
        docs=list(content_categories.find({"active":True}).sort("name",1))
        rows=[[InlineKeyboardButton(style="primary",text=x["name"],callback_data=f"cadm:{'editcat' if action=='edit_cat' else 'removecat'}:{x['_id']}")] for x in docs]
        rows.append([InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="cadm:back")])
        return await cq.message.edit_text("Select category:",reply_markup=_panel_kb(rows))
    if action=="add_product":
        cats=list(content_categories.find({"active":True}).sort("name",1))
        rows=[[InlineKeyboardButton(style="primary",text=x["name"],callback_data=f"cadm:addprod:{x['_id']}")] for x in cats]
        rows.append([InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="cadm:back")])
        return await cq.message.edit_text("Choose product category:",reply_markup=_panel_kb(rows))
    if action in ("edit_product","remove_product"):
        docs=list(content_products.find({"active":True}).sort("name",1))
        rows=[[InlineKeyboardButton(style="primary",text=x["name"],callback_data=f"cadm:{'editprod' if action=='edit_product' else 'removeprod'}:{x['_id']}")] for x in docs]
        rows.append([InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="cadm:back")])
        return await cq.message.edit_text("Select product:",reply_markup=_panel_kb(rows))
    if action=="add_content":
        docs=list(content_products.find({"active":True}).sort("name",1))
        rows=[[InlineKeyboardButton(style="primary",text=x["name"],callback_data=f"cadm:addcontent:{x['_id']}")] for x in docs]
        rows.append([InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="cadm:back")])
        return await cq.message.edit_text("Choose product:",reply_markup=_panel_kb(rows))

@dp.callback_query(F.data.regexp(r"^cadm:(addprod|addsubcat|editcat|removecat|editprod|removeprod|addcontent):"))
async def content_admin_dynamic(cq:CallbackQuery,state:FSMContext):
    try:
        await cq.answer()
    except TelegramBadRequest:
        pass
    if not is_admin(cq.from_user.id): return await cq.answer("Unauthorized",show_alert=True)
    action=cq.data.split(":",1)[1]
    if action.startswith("addsubcat:"):
        parent_id=action.split(":",1)[1]
        parent=_find_by_id(content_categories,parent_id,{"active":True})
        if not parent:
            return await cq.answer("Parent category not found.",show_alert=True)
        await state.update_data(parent_id=parent_id, action="subcategory")
        await state.set_state(ModernAdminState.name)
        return await cq.message.edit_text(f"➕ <b>Add Subcategory</b>\n\nParent: <b>{escape(parent.get('name',''))}</b>\n\nSend subcategory name:",parse_mode="HTML")
    if action.startswith("addprod:"):
        cid=action.split(":",1)[1]; await state.update_data(category_id=cid); await state.set_state(ModernAdminState.product_name)
        return await cq.message.edit_text("Send product name:")
    if action.startswith("editcat:"):
        cid=action.split(":",1)[1]; await state.update_data(target=cid,target_kind="category"); await state.set_state(ModernAdminState.name)
        return await cq.message.edit_text("Send new category name:")
    if action.startswith("removecat:"):
        cid=action.split(":",1)[1]; _update_by_id(content_categories,cid,{"$set":{"active":False}}); return await _content_admin_panel(cq.message.chat.id,cq.message.message_id)
    if action.startswith("editprod:"):
        pid=action.split(":",1)[1]; await state.update_data(target=pid,target_kind="product"); await state.set_state(ModernAdminState.product_name)
        return await cq.message.edit_text("Send new product name:")
    if action.startswith("removeprod:"):
        pid=action.split(":",1)[1]; _update_by_id(content_products,pid,{"$set":{"active":False}}); return await _content_admin_panel(cq.message.chat.id,cq.message.message_id)
    if action.startswith("addcontent:"):
        pid=action.split(":",1)[1]; await state.update_data(content_target=pid); await state.set_state(ModernAdminState.content_target)
        return await cq.message.edit_text("📎 Send file/ZIP/photo/video/audio/link/text.\nSend /done when finished.")

@dp.message(ModernAdminState.product_name)
async def modern_product_name(msg:Message,state:FSMContext):
    d=await state.get_data()
    await state.update_data(product_name=msg.text.strip())
    if d.get("target"):
        _update_by_id(content_products,d["target"],{"$set":{"name":msg.text.strip()}})
        await state.clear(); return await msg.answer("✅ Product name updated.")
    await state.set_state(ModernAdminState.product_price); await msg.answer("Send product price in ₹:")

@dp.message(ModernAdminState.product_price)
async def modern_product_price(msg:Message,state:FSMContext):
    try: p=float(msg.text.strip())
    except: return await msg.answer("❌ Invalid price.")
    await state.update_data(product_price=p); await state.set_state(ModernAdminState.product_description); await msg.answer("Send description:")

@dp.message(ModernAdminState.product_description)
async def modern_product_desc(msg:Message,state:FSMContext):
    await state.update_data(product_description=msg.text.strip()); await state.set_state(ModernAdminState.product_notes); await msg.answer("Send buyer notes:")

@dp.message(ModernAdminState.product_notes)
async def modern_product_notes(msg:Message,state:FSMContext):
    d=await state.get_data()
    r=content_products.insert_one({"category_id":str(d["category_id"]),"name":d["product_name"],"price":float(d["product_price"]),"description":d["product_description"],"notes":msg.text.strip(),"content":[],"active":True,"created_at":datetime.now(timezone.utc)})
    await state.clear(); await msg.answer(f"✅ Product added. ID: <code>{r.inserted_id}</code>",parse_mode="HTML")

@dp.message(ModernAdminState.content_target, Command("done"))
async def modern_content_done(msg:Message,state:FSMContext):
    await state.clear(); await msg.answer("✅ Content update finished.")

@dp.message(ModernAdminState.content_target)
async def modern_content_receive(msg:Message,state:FSMContext):
    d=await state.get_data(); pid=d["content_target"]; item=None
    if msg.document: item={"kind":"document","file_id":msg.document.file_id,"caption":msg.caption or ""}
    elif msg.photo: item={"kind":"photo","file_id":msg.photo[-1].file_id,"caption":msg.caption or ""}
    elif msg.video: item={"kind":"video","file_id":msg.video.file_id,"caption":msg.caption or ""}
    elif msg.audio: item={"kind":"audio","file_id":msg.audio.file_id,"caption":msg.caption or ""}
    elif msg.animation: item={"kind":"animation","file_id":msg.animation.file_id,"caption":msg.caption or ""}
    elif msg.text and re.match(r"^https?://",msg.text.strip()): item={"kind":"link","value":msg.text.strip()}
    elif msg.text: item={"kind":"text","value":msg.text}
    if item: _update_by_id(content_products,pid,{"$push":{"content":item}}); await msg.answer("✅ Added. Send another item or /done.")

@dp.message(Command("contentadmin"))
async def content_admin(msg:Message):
    if not is_admin(msg.from_user.id):
        return await msg.answer("❌ Unauthorized.")
    await _content_admin_panel(msg.chat.id, None)

@dp.message(Command("addcategory"))
async def addcategory(msg:Message):
    if not is_admin(msg.from_user.id): return
    name=msg.text.partition(" ")[2].strip()
    if not name: return await msg.answer("Usage: /addcategory Category Name")
    parent=None
    content_categories.insert_one({"name":name,"parent_id":parent,"active":True,"created_at":datetime.now(timezone.utc)})
    await msg.answer(f"✅ Category <b>{escape(name)}</b> added.",parse_mode="HTML")

@dp.message(Command("addsubcategory"))
async def addsubcategory(msg:Message):
    if not is_admin(msg.from_user.id): return
    raw=msg.text.partition(" ")[2]
    try: parent_id,name=raw.split("|",1); parent_id=parent_id.strip(); name=name.strip()
    except: return await msg.answer("Usage: /addsubcategory CategoryID | Name")
    content_categories.insert_one({"name":name,"parent_id":parent_id,"active":True,"created_at":datetime.now(timezone.utc)})
    await msg.answer("✅ Subcategory added.")

@dp.message(Command("addproduct"))
async def addproduct(msg:Message):
    if not is_admin(msg.from_user.id): return
    raw=msg.text.partition(" ")[2]
    parts=[x.strip() for x in raw.split("|",4)]
    if len(parts)<5: return await msg.answer("Usage: /addproduct CategoryID | Name | Price | Description | Notes")
    cid,name,price,desc,notes=parts
    try: price=float(price)
    except: return await msg.answer("❌ Invalid price.")
    r=content_products.insert_one({"category_id":cid,"name":name,"price":price,"description":desc,"notes":notes,"content":[],"active":True,"created_at":datetime.now(timezone.utc)})
    await msg.answer(f"✅ Product created.\nID: <code>{r.inserted_id}</code>\nUse /addcontent {r.inserted_id}",parse_mode="HTML")

@dp.message(Command("addcontent"))
async def addcontent_start(msg:Message,state:FSMContext):
    if not is_admin(msg.from_user.id): return
    pid=msg.text.partition(" ")[2].strip()
    if not pid or not _find_by_id(content_products, pid): return await msg.answer("❌ Invalid Product ID.")
    await state.update_data(content_product=pid); await state.set_state(ContentAdminState.waiting_content)
    await msg.answer("📤 Send a document/photo/video/audio/animation, or send a URL/text. It will be added to the product. Send /done when finished.")

@dp.message(StateFilter(ContentAdminState.waiting_content), Command("done"))
async def addcontent_done(msg:Message,state:FSMContext): await state.clear(); await msg.answer("✅ Content update finished.")

@dp.message(StateFilter(ContentAdminState.waiting_content))
async def addcontent_receive(msg:Message,state:FSMContext):
    d=await state.get_data(); pid=d["content_product"]; item=None
    if msg.document: item={"kind":"document","file_id":msg.document.file_id,"caption":msg.caption or ""}
    elif msg.photo: item={"kind":"photo","file_id":msg.photo[-1].file_id,"caption":msg.caption or ""}
    elif msg.video: item={"kind":"video","file_id":msg.video.file_id,"caption":msg.caption or ""}
    elif msg.audio: item={"kind":"audio","file_id":msg.audio.file_id,"caption":msg.caption or ""}
    elif msg.animation: item={"kind":"animation","file_id":msg.animation.file_id,"caption":msg.caption or ""}
    elif msg.text and re.match(r"^https?://",msg.text.strip()): item={"kind":"link","value":msg.text.strip()}
    elif msg.text: item={"kind":"text","value":msg.text}
    if item: content_products.update_one({"_id":pid},{"$push":{"content":item}}); await msg.answer("✅ Added. Send another item or /done.")

@dp.message(Command("removeproduct"))
async def removeproduct(msg:Message):
    if not is_admin(msg.from_user.id): return
    pid=msg.text.partition(" ")[2].strip(); content_products.update_one({"_id":pid},{"$set":{"active":False}}); await msg.answer("✅ Product disabled.")

@dp.message(Command("toggleproduct"))
async def toggleproduct(msg:Message):
    if not is_admin(msg.from_user.id): return
    pid=msg.text.partition(" ")[2].strip(); p=_find_by_id(content_products, pid);
    if not p: return await msg.answer("❌ Not found.")
    content_products.update_one({"_id":pid},{"$set":{"active":not p.get("active",True)}}); await msg.answer("✅ Product status updated.")

@dp.message(Command("removecontent"))
async def removecontent(msg:Message):
    if not is_admin(msg.from_user.id): return
    pid=msg.text.partition(" ")[2].strip(); content_products.update_one({"_id":pid},{"$set":{"content":[]}}); await msg.answer("✅ Product content cleared.")

@dp.message(Command("editproduct"))
async def editproduct(msg:Message):
    if not is_admin(msg.from_user.id): return
    parts=[x.strip() for x in msg.text.partition(" ")[2].split("|",2)]
    if len(parts)<3: return await msg.answer("Usage: /editproduct ProductID | field | value")
    pid,field,val=parts
    allowed={"name","price","description","notes","category_id"}
    if field not in allowed: return await msg.answer("❌ Editable: "+", ".join(sorted(allowed)))
    if field=="price":
        try: val=float(val)
        except: return await msg.answer("❌ Invalid price.")
    content_products.update_one({"_id":pid},{"$set":{field:val}})
    await msg.answer("✅ Product updated.")

@dp.message(Command("editcategory"))
async def editcategory(msg:Message):
    if not is_admin(msg.from_user.id): return
    parts=[x.strip() for x in msg.text.partition(" ")[2].split("|",1)]
    if len(parts)<2: return await msg.answer("Usage: /editcategory CategoryID | New Name")
    content_categories.update_one({"_id":parts[0]},{"$set":{"name":parts[1]}}); await msg.answer("✅ Category updated.")

@dp.message(Command("removecategory"))
async def removecategory(msg:Message):
    if not is_admin(msg.from_user.id): return
    cid=msg.text.partition(" ")[2].strip(); content_categories.update_one({"_id":cid},{"$set":{"active":False}}); content_products.update_many({"category_id":cid},{"$set":{"active":False}}); await msg.answer("✅ Category disabled.")

@dp.message(Command("contentlist"))
async def contentlist(msg:Message):
    if not is_admin(msg.from_user.id): return
    cats=list(content_categories.find({})); products=list(content_products.find({}))
    text="<b>📦 Content Catalog</b>\n\n"
    for c in cats: text+=f"📁 <code>{c['_id']}</code> — {escape(c['name'])}\n"
    text+="\n<b>Products</b>\n"
    for p in products: text+=f"📦 <code>{p['_id']}</code> — {escape(p['name'])} — ₹{p['price']:.2f} — {'ON' if p.get('active',True) else 'OFF'}\n"
    await msg.answer(text,parse_mode="HTML")

# ================= SMM Services =================
SMM_API_URL=os.getenv("SMM_API_URL","https://cheapestsmmpanels.com/api/v2")
SMM_API_KEY=os.getenv("SMM_API_KEY","")
SMM_MARGIN=float(os.getenv("SMM_MARGIN_PERCENT","25"))

def get_smm_key():
    return (settings_col.find_one({"_id":"smm_config"}) or {}).get("api_key") or SMM_API_KEY

def get_smm_url():
    return (settings_col.find_one({"_id":"smm_config"}) or {}).get("api_url") or SMM_API_URL

async def smm_api(action, **params):
    api_key=get_smm_key()
    if not api_key: return {"error":"SMM API key is not configured"}
    payload={"key":api_key,"action":action,**params}
    try:
        timeout=aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(get_smm_url(),data=payload) as r:
                try: return await r.json(content_type=None)
                except: return {"error":await r.text()}
    except Exception as e: return {"error":str(e)}

@dp.callback_query(F.data == "smm_home")
async def smm_home(cq:CallbackQuery):
    apps=list(smm_apps.find({"active":True}).sort("name",1)); kb=InlineKeyboardBuilder()
    for a in apps: kb.button(style="primary", text=a["name"],callback_data=f"smm_app:{a['_id']}")
    kb.adjust(2); kb.row(InlineKeyboardButton(style="primary", text="🔙 Back",callback_data="back_main"))
    await cq.message.edit_text("<b>📱 Social Media Services</b>\n\nSelect a platform:",parse_mode="HTML",reply_markup=kb.as_markup()); await cq.answer()

@dp.callback_query(F.data.startswith("smm_app:"))
async def smm_app(cq:CallbackQuery):
    aid=cq.data.split(":",1)[1]; app=_find_by_id(smm_apps, aid, {"active":True});
    if not app: return await cq.answer("App unavailable.",show_alert=True)
    services=list(smm_services.find({"app_id": {"$in": _id_candidates(aid)}, "active":True}).sort("name",1)); kb=InlineKeyboardBuilder()
    for x in services: kb.button(style="primary", text=x["name"],callback_data=f"smm_service:{x['_id']}")
    kb.adjust(1); kb.row(InlineKeyboardButton(style="primary", text="🔙 Back",callback_data="smm_home"))
    await cq.message.edit_text(f"<b>📱 {escape(app['name'])}</b>\n\nSelect a service:",parse_mode="HTML",reply_markup=kb.as_markup()); await cq.answer()

@dp.callback_query(F.data.startswith("smm_service:"))
async def smm_service(cq:CallbackQuery,state:FSMContext):
    try:
        await cq.answer()
    except TelegramBadRequest:
        pass
    sid=cq.data.split(":",1)[1]
    s=_find_by_id(smm_services, sid, {"active":True})
    if not s:
        return await cq.message.edit_text("❌ Service unavailable.", parse_mode="HTML")

    sell=float(s.get("sell_rate", s.get("cost_rate", 0)) or 0)
    text=(
        f"<b>📱 {escape(s['name'])}</b>\n\n"
        f"{escape(s.get('description',''))}\n\n"
        f"💰 <b>Price:</b> ₹{sell:.4f} per 1000\n"
        f"📊 <b>Min:</b> {s.get('min',1)} | <b>Max:</b> {s.get('max',100000)}\n\n"
        f"🔗 <b>First, send the post/profile link:</b>\n"
        f"<i>Example: https://t.me/channel/123</i>"
    )
    await state.set_state(SMMOrderState.waiting_link)
    await state.update_data(service_id=sid)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(style="primary", text="🔙 Back",callback_data=f"smm_app:{s['app_id']}")]])
    await cq.message.edit_text(text,parse_mode="HTML",reply_markup=kb)

@dp.message(StateFilter(SMMOrderState.waiting_link))
async def smm_order_link(msg:Message,state:FSMContext):
    link=(msg.text or "").strip()
    if not re.match(r"^https?://\S+$", link, re.I):
        return await msg.answer("❌ Invalid link. Please send a valid http/https link.")
    data=await state.get_data(); sid=data.get("service_id")
    s=_find_by_id(smm_services,sid,{"active":True})
    if not s:
        await state.clear(); return await msg.answer("❌ Service unavailable.")
    await state.update_data(link=link)
    await state.set_state(SMMOrderState.waiting_amount)
    await msg.answer(
        f"🔗 Link saved.\n\n"
        f"📊 Quantity allowed: <b>{s.get('min',1)}</b> - <b>{s.get('max',100000)}</b>\n\n"
        f"🔢 <b>Now send the amount/quantity:</b>",
        parse_mode="HTML"
    )

@dp.message(StateFilter(SMMOrderState.waiting_amount))
async def smm_order_amount(msg:Message,state:FSMContext):
    try:
        qty=int((msg.text or "").strip())
    except (TypeError,ValueError):
        return await msg.answer("❌ Invalid amount. Send a whole number, e.g. 1000.")

    data=await state.get_data(); sid=data.get("service_id"); link=data.get("link")
    s=_find_by_id(smm_services,sid,{"active":True})
    if not s:
        await state.clear(); return await msg.answer("❌ Service unavailable.")
    min_q=int(s.get("min",1)); max_q=int(s.get("max",100000))
    if qty < min_q or qty > max_q:
        return await msg.answer(f"❌ Quantity must be between {min_q} and {max_q}.")

    # sell_rate is already the final rate per 1000, including margin.
    rate=float(s.get("sell_rate",s.get("cost_rate",0)) or 0)
    total=round(rate*qty/1000,2)
    user=get_or_create_user(msg.from_user.id,msg.from_user.username)
    balance=float(user.get("balance",0) or 0)

    await state.update_data(quantity=qty,total=total)
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(style="primary",text=f"🛒 Buy — ₹{total:.2f}",callback_data=f"smm_buy_confirm:{sid}")],
        [InlineKeyboardButton(style="primary",text="🔄 Change Amount",callback_data=f"smm_change_amount:{sid}")],
        [InlineKeyboardButton(style="primary",text="❌ Cancel",callback_data="smm_home")]
    ])
    balance_warning = "⚠️ Insufficient balance." if balance < total else ""

    await msg.answer(
        f"<b>🧾 ORDER SUMMARY</b>\n\n"
        f"📱 <b>Service:</b> {escape(s['name'])}\n"
        f"🔗 <b>Link:</b> <code>{escape(link)}</code>\n"
        f"🔢 <b>Amount:</b> {qty}\n"
        f"💰 <b>Rate:</b> ₹{rate:.4f} / 1000\n"
        f"💵 <b>Total:</b> ₹{total:.2f}\n"
        f"💳 <b>Balance:</b> ₹{balance:.2f}\n"
        f"{balance_warning}\n\n"
        f"Press <b>Buy</b> to place the order.",
        parse_mode="HTML", reply_markup=kb
    )

@dp.callback_query(F.data.startswith("smm_change_amount:"))
async def smm_change_amount(cq:CallbackQuery,state:FSMContext):
    try: await cq.answer()
    except TelegramBadRequest: pass
    sid=cq.data.split(":",1)[1]
    data=await state.get_data()
    if data.get("service_id") != sid:
        return await cq.message.edit_text("❌ Order session expired. Please select the service again.",parse_mode="HTML")
    await state.set_state(SMMOrderState.waiting_amount)
    await cq.message.edit_text("🔢 <b>Send the new amount/quantity:</b>",parse_mode="HTML")

@dp.callback_query(F.data.startswith("smm_buy_confirm:"))
async def smm_buy_confirm(cq:CallbackQuery,state:FSMContext):
    try: await cq.answer("⏳ Placing order...",show_alert=False)
    except TelegramBadRequest: pass
    sid=cq.data.split(":",1)[1]
    data=await state.get_data()
    if data.get("service_id") != sid:
        return await cq.message.edit_text("❌ Order session expired. Please select the service again.",parse_mode="HTML")
    link=data.get("link"); qty=int(data.get("quantity",0)); quoted_total=float(data.get("total",0))
    s=_find_by_id(smm_services,sid,{"active":True})
    if not s:
        await state.clear(); return await cq.message.edit_text("❌ Service unavailable.",parse_mode="HTML")
    min_q=int(s.get("min",1)); max_q=int(s.get("max",100000))
    if not link or qty < min_q or qty > max_q:
        await state.clear(); return await cq.message.edit_text("❌ Invalid order details.",parse_mode="HTML")
    rate=float(s.get("sell_rate",s.get("cost_rate",0)) or 0)
    price=round(rate*qty/1000,2)
    if abs(price-quoted_total)>0.01:
        quoted_total=price
    user=get_or_create_user(cq.from_user.id,cq.from_user.username)
    balance=float(user.get("balance",0) or 0)
    if balance < price:
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(style="primary",text="💳 Recharge",callback_data="recharge")],[InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="smm_home")]])
        return await cq.message.edit_text(f"❌ <b>Insufficient balance</b>\n\nRequired: ₹{price:.2f}\nAvailable: ₹{balance:.2f}",parse_mode="HTML",reply_markup=kb)

    await cq.message.edit_text("⏳ <i>Placing your SMM order with the provider...</i>",parse_mode="HTML")
    result=await smm_api("add",service=str(s["service_id"]),link=link,quantity=qty)
    if result.get("error") or result.get("order") is None:
        err=escape(str(result.get("error",result))[:700])
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(style="primary",text="🔙 Back",callback_data=f"smm_app:{s['app_id']}")]])
        return await cq.message.edit_text(f"❌ <b>Provider rejected the order.</b>\n\n<code>{err}</code>",parse_mode="HTML",reply_markup=kb)

    provider_order=str(result.get("order"))
    # Deduct only after provider accepted the order.
    users_col.update_one({"_id":cq.from_user.id},{"$inc":{"balance":-price}})
    order={"user_id":cq.from_user.id,"service_id":sid,"service_name":s["name"],"provider_order":provider_order,"link":link,"quantity":qty,"price":price,"status":"pending","created_at":datetime.now(timezone.utc)}
    r=smm_orders.insert_one(order)
    text=(f"📱 <b>SMM Order Placed</b>\n\nService: {escape(s['name'])}\n"
          f"Quantity: {qty}\nPrice: ₹{price:.2f}\nOrder ID: <code>{r.inserted_id}</code>")
    await state.clear()
    await cq.message.edit_text("✅ <b>Order placed successfully!</b>\n\n"+text,parse_mode="HTML",reply_markup=_panel_kb([[InlineKeyboardButton(style="primary",text="📱 More Services",callback_data="smm_home")],[InlineKeyboardButton(style="primary",text="🏠 Home",callback_data="back_main")]]))
    try: await bot.send_message(SALESLOG,"🛒 <b>NEW SMM SALE</b>\n\n"+text+f"\nBuyer: <code>{cq.from_user.id}</code>",parse_mode="HTML")
    except Exception: pass
    try: await bot.send_message(ADMINLOG,"🛒 <b>NEW SMM SALE</b>\n\n"+text+f"\nBuyer: <code>{cq.from_user.id}</code>\nProvider: <code>{provider_order}</code>\nLink: {escape(link)}",parse_mode="HTML")
    except Exception: pass

@dp.message(Command("setsmmkey"))
async def setsmmkey(msg:Message):
    if not is_admin(msg.from_user.id): return
    key=msg.text.partition(" ")[2].strip()
    if not key: return await msg.answer("Usage: /setsmmkey YOUR_API_KEY")
    settings_col.update_one({"_id":"smm_config"},{"$set":{"api_key":key}},upsert=True)
    try: await msg.delete()
    except Exception: pass
    await bot.send_message(msg.from_user.id,"✅ SMM API key saved securely in bot settings.")

@dp.message(Command("setsmmurl"))
async def setsmmurl(msg:Message):
    if not is_admin(msg.from_user.id): return
    url=msg.text.partition(" ")[2].strip()
    if not url: return await msg.answer("Usage: /setsmmurl https://cheapestsmmpanels.com/api/v2")
    settings_col.update_one({"_id":"smm_config"},{"$set":{"api_url":url}},upsert=True)
    await msg.answer("✅ SMM API URL updated.")


# ================= Modern SMM / Content Admin Panels =================


async def _edit_or_send(chat_id, message_id, text, kb):
    try:
        if message_id:
            return await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass
    return await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)

def _panel_kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def _smm_panel(chat_id, mid):
    kb=_panel_kb([
        [InlineKeyboardButton(style="primary",text="➕ Add App",callback_data="smmadm:add_app"),InlineKeyboardButton(style="primary",text="✏️ Edit App",callback_data="smmadm:edit_app")],
        [InlineKeyboardButton(style="primary",text="🗑️ Remove App",callback_data="smmadm:remove_app"),InlineKeyboardButton(style="primary",text="📋 Apps",callback_data="smmadm:apps")],
        [InlineKeyboardButton(style="primary",text="➕ Add Service",callback_data="smmadm:add_service")],
        [InlineKeyboardButton(style="primary",text="✏️ Edit Service",callback_data="smmadm:edit_service"),InlineKeyboardButton(style="primary",text="🗑️ Remove Service",callback_data="smmadm:remove_service")],
        [InlineKeyboardButton(style="primary",text="📋 Services",callback_data="smmadm:services")],
        [InlineKeyboardButton(style="primary",text="🔑 API Settings",callback_data="smmadm:api"),InlineKeyboardButton(style="primary",text="🧪 Test API",callback_data="smmadm:test")],
        [InlineKeyboardButton(style="primary",text="🧾 SMM Logs",callback_data="smmadm:logs")],
    ])
    return await _edit_or_send(chat_id,mid,"<b>📱 SMM ADMIN PANEL</b>\n\nChoose an action:",kb)

async def _content_admin_panel(chat_id, mid):
    kb=_panel_kb([
        [InlineKeyboardButton(style="primary",text="➕ Add Category",callback_data="cadm:add_cat"),InlineKeyboardButton(style="primary",text="✏️ Edit Category",callback_data="cadm:edit_cat")],
        [InlineKeyboardButton(style="primary",text="🗑️ Remove Category",callback_data="cadm:remove_cat"),InlineKeyboardButton(style="primary",text="📋 Categories",callback_data="cadm:cats")],
        [InlineKeyboardButton(style="primary",text="➕ Add Subcategory",callback_data="cadm:add_subcat")],
        [InlineKeyboardButton(style="primary",text="➕ Add Product",callback_data="cadm:add_product")],
        [InlineKeyboardButton(style="primary",text="✏️ Edit Product",callback_data="cadm:edit_product"),InlineKeyboardButton(style="primary",text="🗑️ Remove Product",callback_data="cadm:remove_product")],
        [InlineKeyboardButton(style="primary",text="📎 Add Content",callback_data="cadm:add_content")],
        [InlineKeyboardButton(style="primary",text="📋 Products",callback_data="cadm:products")],
    ])
    return await _edit_or_send(chat_id,mid,"<b>📦 OTHER CONTENT ADMIN PANEL</b>\n\nChoose an action:",kb)


@dp.callback_query(F.data.regexp(r"^smmadm:(addsvc|editapp|removeapp|editsvc|field|removesvc):"))
async def smm_admin_dynamic(cq:CallbackQuery,state:FSMContext):
    if not is_admin(cq.from_user.id): return await cq.answer("Unauthorized",show_alert=True)
    action=cq.data.split(":",1)[1]
    if action.startswith("addsvc:"):
        aid=action.split(":",1)[1]; await state.update_data(app_id=aid,panel_mid=cq.message.message_id); await state.set_state(ModernAdminState.service_id)
        await cq.message.edit_text("Send the <b>provider service ID</b>:",parse_mode="HTML"); return await cq.answer()
    if action.startswith("editapp:"):
        aid=action.split(":",1)[1]
        if not _find_by_id(smm_apps,aid): return await cq.answer("Not found",show_alert=True)
        await state.update_data(target=aid,target_kind="app"); await state.set_state(ModernAdminState.name)
        await cq.message.edit_text("Send the new app name:",parse_mode="HTML"); return await cq.answer()
    if action.startswith("removeapp:"):
        aid=action.split(":",1)[1]
        if not _find_by_id(smm_apps,aid): return await cq.answer("Not found",show_alert=True)
        for cand in _id_candidates(aid):
            smm_apps.update_one({"_id":cand},{"$set":{"active":False}})
            smm_services.update_many({"app_id":str(cand)},{"$set":{"active":False}})
        await cq.answer("App removed"); return await _smm_panel(cq.message.chat.id,cq.message.message_id)
    if action.startswith("editsvc:"):
        sid=action.split(":",1)[1]; s=_find_by_id(smm_services,sid)
        if not s: return await cq.answer("Not found",show_alert=True)
        kb=_panel_kb([[InlineKeyboardButton(style="primary",text=f"Edit {f}",callback_data=f"smmadm:field:{sid}:{f}")] for f in ("name","service_id","cost_rate","min","max","description","margin_percent")]+[[InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="smmadm:back")]])
        return await cq.message.edit_text("Choose field:",parse_mode="HTML",reply_markup=kb)
    if action.startswith("field:"):
        _,sid,field=action.split(":",2); await state.update_data(target=sid,edit_field=field); await state.set_state(ModernAdminState.edit_field)
        return await cq.message.edit_text(f"Send new value for <b>{field}</b>:",parse_mode="HTML")
    if action.startswith("removesvc:"):
        sid=action.split(":",1)[1]; _update_by_id(smm_services,sid,{"$set":{"active":False}})
        await cq.answer("Service removed"); return await _smm_panel(cq.message.chat.id,cq.message.message_id)

@dp.callback_query(F.data.in_({"smmadm:add_app","smmadm:edit_app","smmadm:remove_app","smmadm:apps","smmadm:add_service","smmadm:edit_service","smmadm:remove_service","smmadm:services","smmadm:api","smmadm:test","smmadm:logs","smmadm:back"}))
async def smm_admin_callback(cq:CallbackQuery,state:FSMContext):
    if not is_admin(cq.from_user.id): return await cq.answer("Unauthorized",show_alert=True)
    action=cq.data.split(":",1)[1]; mid=cq.message.message_id; chat=cq.message.chat.id
    if action=="back": return await _smm_panel(chat,mid)
    if action=="add_app":
        await state.set_state(ModernAdminState.name); await state.update_data(panel_mid=mid)
        await cq.message.edit_text("➕ <b>Add SMM App</b>\n\nSend the app name:",parse_mode="HTML"); return await cq.answer()
    if action in ("edit_app","remove_app"):
        apps=list(smm_apps.find({"active":True}).sort("name",1))
        rows=[[InlineKeyboardButton(style="primary",text=a["name"],callback_data=f"smmadm:{'editapp' if action=='edit_app' else 'removeapp'}:{a['_id']}")] for a in apps]
        rows.append([InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="smmadm:back")])
        return await cq.message.edit_text("Select an app:",parse_mode="HTML",reply_markup=_panel_kb(rows))
    if action=="apps":
        apps=list(smm_apps.find({}).sort("name",1)); text="<b>📋 Apps</b>\n\n"+("\n".join(f"• <code>{a['_id']}</code> — {escape(a['name'])} — {'ON' if a.get('active',True) else 'OFF'}" for a in apps) or "No apps.")
        kb=_panel_kb([[InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="smmadm:back")]])
        return await cq.message.edit_text(text,parse_mode="HTML",reply_markup=kb)
    if action=="add_service":
        apps=list(smm_apps.find({"active":True}).sort("name",1))
        rows=[[InlineKeyboardButton(style="primary",text=a["name"],callback_data=f"smmadm:addsvc:{a['_id']}")] for a in apps]
        rows.append([InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="smmadm:back")])
        return await cq.message.edit_text("➕ <b>Add Service</b>\n\nChoose the app:",parse_mode="HTML",reply_markup=_panel_kb(rows))
    if action in ("edit_service","remove_service"):
        services=list(smm_services.find({}).sort("name",1))
        rows=[[InlineKeyboardButton(style="primary",text=f"{s['name']} ({s.get('service_id','')})",callback_data=f"smmadm:{'editsvc' if action=='edit_service' else 'removesvc'}:{s['_id']}")] for s in services]
        rows.append([InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="smmadm:back")])
        return await cq.message.edit_text("Select a service:",parse_mode="HTML",reply_markup=_panel_kb(rows))
    if action=="services":
        services=list(smm_services.find({})); text="<b>📋 Services</b>\n\n"+("\n".join(f"• <code>{s['_id']}</code> — {escape(s['name'])} — ₹{float(s.get('sell_rate',s.get('cost_rate',0))):.2f}/1000 — {'ON' if s.get('active',True) else 'OFF'}" for s in services) or "No services.")
        return await cq.message.edit_text(text,parse_mode="HTML",reply_markup=_panel_kb([[InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="smmadm:back")]]))
    if action=="api":
        cfg=settings_col.find_one({"_id":"smm_config"}) or {}
        text=f"<b>🔑 SMM API</b>\n\nURL: <code>{escape(str(cfg.get('api_url','not set')))}</code>\nKey: {'configured' if cfg.get('api_key') else 'not configured'}"
        return await cq.message.edit_text(text,parse_mode="HTML",reply_markup=_panel_kb([[InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="smmadm:back")]]))
    if action=="test":
        result=await smm_api("balance")
        return await cq.message.edit_text(f"<b>🧪 SMM API Test</b>\n\n<pre>{escape(str(result)[:3000])}</pre>",parse_mode="HTML",reply_markup=_panel_kb([[InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="smmadm:back")]]))
    if action=="logs":
        logs=list(smm_orders.find({}).sort("created_at",-1).limit(10))
        text="<b>🧾 Recent SMM Orders</b>\n\n"+("\n".join(f"• <code>{x['_id']}</code> — {x.get('service_name','')} — ₹{float(x.get('price',0)):.2f} — {x.get('status','')}" for x in logs) or "No orders.")
        return await cq.message.edit_text(text,parse_mode="HTML",reply_markup=_panel_kb([[InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="smmadm:back")]]))
    if action.startswith("addsvc:"):
        pass

@dp.callback_query(F.data.startswith("smmadm:addsvc:"))
async def smm_add_service_pick(cq:CallbackQuery,state:FSMContext):
    if not is_admin(cq.from_user.id): return
    aid=cq.data.split(":",2)[2]
    await state.update_data(app_id=aid,panel_mid=cq.message.message_id)
    await state.set_state(ModernAdminState.service_id)
    await cq.message.edit_text("Send the <b>provider service ID</b>:",parse_mode="HTML")
    await cq.answer()

@dp.message(ModernAdminState.service_id)
async def smm_add_service_id(msg:Message,state:FSMContext):
    if not is_admin(msg.from_user.id): return
    await state.update_data(service_id=msg.text.strip()); await state.set_state(ModernAdminState.name)
    await msg.answer("Send the service <b>name</b>:",parse_mode="HTML")

@dp.message(ModernAdminState.name)
async def modern_admin_name(msg:Message,state:FSMContext):
    if not is_admin(msg.from_user.id): return
    d=await state.get_data()
    if d.get("app_id") and d.get("service_id"):
        await state.update_data(service_name=msg.text.strip()); await state.set_state(ModernAdminState.cost); return await msg.answer("Send <b>provider cost per 1000</b>:")
    if d.get("action")=="subcategory":
        parent_id=d.get("parent_id")
        r=content_categories.insert_one({"name":msg.text.strip(),"parent_id":str(parent_id),"active":True,"created_at":datetime.now(timezone.utc)})
        await state.clear(); return await msg.answer(f"✅ Subcategory added: <b>{escape(msg.text.strip())}</b>",parse_mode="HTML")
    if d.get("action")=="category":
        r=content_categories.insert_one({"name":msg.text.strip(),"parent_id":None,"active":True,"created_at":datetime.now(timezone.utc)})
        await state.clear(); return await msg.answer(f"✅ Category added: <b>{escape(msg.text.strip())}</b>",parse_mode="HTML")
    if d.get("target"):
        if d.get("target_kind") == "category":
            _update_by_id(content_categories,d["target"],{"$set":{"name":msg.text.strip()}})
            await state.clear(); return await msg.answer("✅ Category updated.")
        _update_by_id(smm_apps,d["target"],{"$set":{"name":msg.text.strip()}})
        await state.clear()
        return await msg.answer(f"✅ App updated: <b>{escape(msg.text.strip())}</b>",parse_mode="HTML")
    await state.update_data(app_name=msg.text.strip())
    await state.clear()
    r=smm_apps.insert_one({"name":msg.text.strip(),"active":True,"created_at":datetime.now(timezone.utc)})
    await msg.answer(f"✅ App added: <b>{escape(msg.text.strip())}</b>",parse_mode="HTML")

@dp.message(ModernAdminState.cost)
async def smm_add_service_cost(msg:Message,state:FSMContext):
    try: cost=float(msg.text.strip())
    except: return await msg.answer("❌ Send a number, e.g. 1.25")
    await state.update_data(cost=cost); await state.set_state(ModernAdminState.minimum); await msg.answer("Send minimum quantity:")

@dp.message(ModernAdminState.minimum)
async def smm_add_service_min(msg:Message,state:FSMContext):
    try: v=int(msg.text.strip())
    except: return await msg.answer("❌ Send an integer.")
    await state.update_data(minimum=v); await state.set_state(ModernAdminState.maximum); await msg.answer("Send maximum quantity:")

@dp.message(ModernAdminState.maximum)
async def smm_add_service_max(msg:Message,state:FSMContext):
    try: v=int(msg.text.strip())
    except: return await msg.answer("❌ Send an integer.")
    await state.update_data(maximum=v); await state.set_state(ModernAdminState.description); await msg.answer("Send service description:")

@dp.message(ModernAdminState.description)
async def smm_add_service_desc(msg:Message,state:FSMContext):
    d=await state.get_data()
    cost=float(d["cost"]); mi=int(d["minimum"]); ma=int(d["maximum"]); aid=d["app_id"]
    if mi<1 or ma<mi: return await msg.answer("❌ Invalid min/max.")
    margin=float(settings_col.find_one({"_id":"smm_config"}).get("margin_percent",SMM_MARGIN) if settings_col.find_one({"_id":"smm_config"}) else SMM_MARGIN)
    sell=cost+(cost*margin/100)
    r=smm_services.insert_one({"app_id":str(aid),"service_id":d["service_id"],"name":d["service_name"],"cost_rate":cost,"sell_rate":sell,"min":mi,"max":ma,"description":msg.text.strip(),"margin_percent":margin,"active":True,"created_at":datetime.now(timezone.utc)})
    await state.clear(); await msg.answer(f"✅ <b>Service added</b>\nID: <code>{r.inserted_id}</code>\nSell: ₹{sell:.2f}/1000",parse_mode="HTML")

@dp.callback_query(F.data.startswith("smmadm:editapp:"))
async def smm_edit_app_pick(cq:CallbackQuery,state:FSMContext):
    aid=cq.data.split(":",2)[2]; a=_find_by_id(smm_apps,aid)
    if not a: return await cq.answer("Not found",show_alert=True)
    await state.update_data(target=aid,target_kind="app"); await state.set_state(ModernAdminState.name)
    await cq.message.edit_text("Send the new app name:",parse_mode="HTML"); await cq.answer()

@dp.callback_query(F.data.startswith("smmadm:removeapp:"))
async def smm_remove_app_pick(cq:CallbackQuery):
    aid=cq.data.split(":",2)[2]
    if not _find_by_id(smm_apps,aid): return await cq.answer("Not found",show_alert=True)
    for cand in _id_candidates(aid):
        smm_apps.update_one({"_id":cand},{"$set":{"active":False}})
        smm_services.update_many({"app_id":str(cand)},{"$set":{"active":False}})
    await cq.answer("App removed"); await _smm_panel(cq.message.chat.id,cq.message.message_id)

@dp.callback_query(F.data.startswith("smmadm:editsvc:"))
async def smm_edit_service_pick(cq:CallbackQuery,state:FSMContext):
    sid=cq.data.split(":",2)[2]
    s=_find_by_id(smm_services,sid)
    if not s: return await cq.answer("Not found",show_alert=True)
    kb=_panel_kb([[InlineKeyboardButton(style="primary",text=f"Edit {f}",callback_data=f"smmadm:field:{sid}:{f}")] for f in ("name","service_id","cost_rate","min","max","description","margin_percent")]+[[InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="smmadm:back")]])
    await cq.message.edit_text("Choose field:",parse_mode="HTML",reply_markup=kb); await cq.answer()

@dp.callback_query(F.data.startswith("smmadm:field:"))
async def smm_edit_field(cq:CallbackQuery,state:FSMContext):
    _,_,sid,field=cq.data.split(":",3); await state.update_data(target=sid,edit_field=field)
    await state.set_state(ModernAdminState.edit_field); await cq.message.edit_text(f"Send new value for <b>{field}</b>:",parse_mode="HTML"); await cq.answer()

@dp.message(ModernAdminState.edit_field)
async def smm_edit_field_value(msg:Message,state:FSMContext):
    d=await state.get_data(); field=d["edit_field"]; val=msg.text.strip()
    try:
        if field in ("cost_rate","margin_percent"): val=float(val)
        elif field in ("min","max"): val=int(val)
    except: return await msg.answer("❌ Invalid value.")
    if field=="margin_percent":
        s=_find_by_id(smm_services,d["target"]); val=float(val); sell=float(s.get("cost_rate",0))*(1+val/100) if s else 0
        _update_by_id(smm_services,d["target"],{"$set":{"margin_percent":val,"sell_rate":sell}})
    else: _update_by_id(smm_services,d["target"],{"$set":{field:val}})
    await state.clear(); await msg.answer("✅ Service updated.")

@dp.callback_query(F.data.startswith("smmadm:removesvc:"))
async def smm_remove_service_pick(cq:CallbackQuery):
    sid=cq.data.split(":",2)[2]; _update_by_id(smm_services,sid,{"$set":{"active":False}})
    await cq.answer("Service removed"); await _smm_panel(cq.message.chat.id,cq.message.message_id)


@dp.message(Command("smmadmin"))
async def smm_admin(msg:Message):
    if not is_admin(msg.from_user.id):
        return await msg.answer("❌ Unauthorized.")
    text = "<b>📱 SMM ADMIN PANEL</b>\n\nSelect an action:"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(style="primary", text="➕ Add App", callback_data="smmadm:add_app"),
         InlineKeyboardButton(style="primary", text="✏️ Edit App", callback_data="smmadm:edit_app")],
        [InlineKeyboardButton(style="primary", text="🗑️ Remove App", callback_data="smmadm:remove_app"),
         InlineKeyboardButton(style="primary", text="📋 Apps", callback_data="smmadm:apps")],
        [InlineKeyboardButton(style="primary", text="➕ Add Service", callback_data="smmadm:add_service")],
        [InlineKeyboardButton(style="primary", text="✏️ Edit Service", callback_data="smmadm:edit_service"),
         InlineKeyboardButton(style="primary", text="🗑️ Remove Service", callback_data="smmadm:remove_service")],
        [InlineKeyboardButton(style="primary", text="📋 Services", callback_data="smmadm:services")],
        [InlineKeyboardButton(style="primary", text="🔑 API Settings", callback_data="smmadm:api"),
         InlineKeyboardButton(style="primary", text="🧪 Test API", callback_data="smmadm:test")],
        [InlineKeyboardButton(style="primary", text="🧾 SMM Logs", callback_data="smmadm:logs")],
    ])
    await msg.answer(text, parse_mode="HTML", reply_markup=kb)

@dp.message(Command("addsmmapp"))
async def addsmmapp(msg:Message):
    if not is_admin(msg.from_user.id): return
    name=msg.text.partition(" ")[2].strip()
    if not name: return await msg.answer("Usage: /addsmmapp Instagram")
    r=smm_apps.insert_one({"name":name,"active":True,"created_at":datetime.now(timezone.utc)}); await msg.answer(f"✅ App added. ID: <code>{r.inserted_id}</code>",parse_mode="HTML")

@dp.message(Command("addsmmservice"))
async def addsmmservice(msg:Message):
    if not is_admin(msg.from_user.id): return
    parts=[x.strip() for x in msg.text.partition(" ")[2].split("|",6)]
    if len(parts)<7: return await msg.answer("Usage: /addsmmservice AppID | ServiceID | Name | CostPer1000 | Min | Max | Description")
    aid,provider_id,name,cost,mi,ma,desc=parts
    try: cost=float(cost); mi=int(mi); ma=int(ma)
    except: return await msg.answer("❌ Invalid numeric value.")
    r=smm_services.insert_one({"app_id":aid,"service_id":provider_id,"name":name,"cost_rate":cost,"min":mi,"max":ma,"description":desc,"margin_percent":SMM_MARGIN,"active":True,"created_at":datetime.now(timezone.utc)})
    await msg.answer(f"✅ Service added. ID: <code>{r.inserted_id}</code>",parse_mode="HTML")

@dp.message(Command("removesmmapp"))
async def removesmmapp(msg:Message):
    if not is_admin(msg.from_user.id): return
    aid=msg.text.partition(" ")[2].strip(); smm_apps.update_one({"_id":aid},{"$set":{"active":False}}); smm_services.update_many({"app_id":aid},{"$set":{"active":False}}); await msg.answer("✅ App disabled.")

@dp.message(Command("removesmmservice"))
async def removesmmservice(msg:Message):
    if not is_admin(msg.from_user.id): return
    sid=msg.text.partition(" ")[2].strip(); smm_services.update_one({"_id":sid},{"$set":{"active":False}}); await msg.answer("✅ Service disabled.")

@dp.message(Command("togglesmmservice"))
async def togglesmmservice(msg:Message):
    if not is_admin(msg.from_user.id): return
    sid=msg.text.partition(" ")[2].strip(); s=_find_by_id(smm_services, sid);
    if not s: return await msg.answer("❌ Not found.")
    smm_services.update_one({"_id":sid},{"$set":{"active":not s.get("active",True)}}); await msg.answer("✅ Service status updated.")

@dp.message(Command("editsmmservice"))
async def editsmmservice(msg:Message):
    if not is_admin(msg.from_user.id): return
    parts=[x.strip() for x in msg.text.partition(" ")[2].split("|",2)]
    if len(parts)<3: return await msg.answer("Usage: /editsmmservice ServiceID | field | value")
    sid,field,val=parts
    allowed={"name","description","cost_rate","min","max","margin_percent","service_id"}
    if field not in allowed: return await msg.answer("❌ Editable fields: "+", ".join(sorted(allowed)))
    if field in {"cost_rate","margin_percent"}: val=float(val)
    if field in {"min","max"}: val=int(val)
    smm_services.update_one({"_id":sid},{"$set":{field:val}}); await msg.answer("✅ Service updated.")

@dp.message(Command("smmlist"))
async def smmlist(msg:Message):
    if not is_admin(msg.from_user.id): return
    text="<b>📱 SMM Catalog</b>\n\n"
    for a in smm_apps.find({}):
        text+=f"📁 <code>{a['_id']}</code> — {escape(a['name'])}\n"
        for s in smm_services.find({"app_id":a['_id']}): text+=f"  • <code>{s['_id']}</code> {escape(s['name'])} — provider {s['service_id']} — {'ON' if s.get('active',True) else 'OFF'}\n"
    await msg.answer(text,parse_mode="HTML")

@dp.message(Command("smmtest"))
async def smmtest(msg:Message):
    if not is_admin(msg.from_user.id): return
    result=await smm_api("balance"); await msg.answer(f"<pre>{escape(str(result)[:3000])}</pre>",parse_mode="HTML")

# ================= Referral 24h Reward =================
async def referral_reward_worker():
    while True:
        try:
            now=datetime.now(timezone.utc); start=now-timedelta(hours=24)
            lock=settings_col.find_one({"_id":"referral_24h_lock"}) or {}
            last=lock.get("last_run")
            if last and last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if not last or (now-last).total_seconds()>=86400:
                totals={}
                for tx in db["transactions"].find({"status":"paid","created_at":{"$gte":start}}):
                    u=users_col.find_one({"_id":tx.get("user_id")})
                    if u and u.get("referred_by"):
                        rid=int(u["referred_by"]); totals[rid]=totals.get(rid,0)+float(tx.get("amount",0))
                if totals:
                    winner=max(totals,key=totals.get); amount=totals[winner]
                    users_col.update_one({"_id":winner},{"$inc":{"balance":10.0},"$push":{"referral_rewards":{"amount":10.0,"recharge_total":amount,"period_end":now}}})
                    try: await bot.send_message(winner,f"🏆 <b>24-Hour Referral Reward!</b>\nYour referred users generated the highest recharge total: ₹{amount:.2f}.\n\n🎁 <b>₹10</b> has been added to your wallet.",parse_mode="HTML")
                    except Exception: pass
                settings_col.update_one({"_id":"referral_24h_lock"},{"$set":{"last_run":now}},upsert=True)
        except Exception as e: print("Referral reward worker:",e)
        await asyncio.sleep(900)

async def start_extra_workers(dispatcher):
    asyncio.create_task(referral_reward_worker())

dp.startup.register(start_extra_workers)

# ================= More.. Menu =================
@dp.callback_query(lambda cq: cq.data == "more_menu")
async def more_menu(cq: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(style="primary", text="🥂 Sell Account", callback_data="sell"),
            InlineKeyboardButton(style="primary", text="🎉 Redeem", callback_data="redeem")
        ],
        [
            InlineKeyboardButton(style="primary", text="📑 History", callback_data="history"),
            InlineKeyboardButton(style="primary", text="Sales Log", url=f"https://t.me/{SALESLOG}")
        ],
        [
            InlineKeyboardButton(style="primary", text="How to Buy", url=f"https://t.me/{USAGE}"),
            InlineKeyboardButton(style="primary", text="How to Sell", url=f"https://t.me/{USAGE}")
        ],
        [
            InlineKeyboardButton(style="primary", text="How to Recharge", url=f"https://t.me/{USAGE}"),
            InlineKeyboardButton(style="primary", text="Support", url=f"https://t.me/{SUPPORT}")
        ],
        [
            InlineKeyboardButton(style="primary", text="⬅️ Back", callback_data="back_main")
        ]
    ])

    await cq.message.edit_text(
        "<b>View more services and help :</b>",
        parse_mode="HTML",
        reply_markup=kb
    )
    await cq.answer()


#=============== Back Button =================
@dp.callback_query(lambda cq: cq.data == "back_main")
async def back_main(cq: CallbackQuery):
    if not await check_join(bot, cq):
        await cq.answer("❗ Join the channel first", show_alert=True)
        return
    
    user_id = cq.from_user.id
    full_name = cq.from_user.full_name
    safe_name = escape(full_name)
    user_mention = f"<a href='tg://user?id={user_id}'>{safe_name}</a>"
    
    user = users_col.find_one({"_id": user_id})
    balance_inr = user.get("balance", 0.0) if user else 0.0
    balance_usdt = balance_inr / 95.0
    
    caption = (
        f"<blockquote> Hey, {user_mention}!</blockquote>\n"
        f"<b>𝖶𝖾𝗅𝖼𝗈𝗆𝖾 𝖳𝗈 Account Robot- 𝖥𝖺𝗌𝗍𝖾𝗌𝗍 𝖳𝖾𝗅𝖾𝗀𝗋𝖺𝗆 𝖠𝖼𝖼𝗈𝗎𝗇𝗍 𝖲𝖾𝗅𝗅𝖾𝗋 𝖡𝗈𝗍🥂</b>\n\n"
        f"<b>🚀 𝖤𝗇𝗃𝗈𝗒 𝖥𝖺𝗌𝗍 𝖠𝖼𝖼𝗈𝗎𝗇𝗍 𝖻𝗎𝗒𝗂𝗇𝗀 𝖤𝗑𝗉𝖾𝗋𝗂𝖾𝗇𝖼𝖾 !\n------------------------------------------------\n"
        f"• Your ID - <code>{user_id}</code>\n"
        f"• Your Balance - ₹{balance_inr:.2f} | ${balance_usdt:.2f}</b>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(style="primary", text="🛒 Buy Telegram Account", callback_data="buy")
        ],
        [
            InlineKeyboardButton(style="primary", text="💸 Balance", callback_data="balance")
        ],
        [
            InlineKeyboardButton(style="primary", text="💳 Recharge", callback_data="recharge"),
            InlineKeyboardButton(style="primary", text="👤 Account", callback_data="stats")
        ],
        [
            InlineKeyboardButton(style="primary", text="➕ More..", callback_data="more_menu"),
            InlineKeyboardButton(style="primary", text="⚡ Refer & Earn", callback_data="refer")
        ],
        [
            InlineKeyboardButton(style="primary", text="💬 Feedback", callback_data="feedback")
        ]
    ])
    
    await cq.message.edit_text(caption, parse_mode="HTML", reply_markup=kb)
    await cq.answer()

#================ Balance =================
@dp.callback_query(F.data == "balance")
async def show_balance(cq: CallbackQuery):
    user = users_col.find_one({"_id": cq.from_user.id})
    balance_inr = user.get("balance", 0.0) if user else 0.0
    balance_usdt = balance_inr / 95.0
    
    await cq.answer(f"💰 Balance: ₹{balance_inr:.2f} | ${balance_usdt:.2f}", show_alert=True)

@dp.message(Command("balance"))
async def cmd_balance(msg: Message):
    user = users_col.find_one({"_id": msg.from_user.id})
    balance_inr = user.get("balance", 0.0) if user else 0.0
    balance_usdt = balance_inr / 95.0
    
    await msg.answer(f"💰 Balance: ₹{balance_inr:.2f} | ${balance_usdt:.2f}")

# --- MASTER COUNTRY MAPPER (250+ COUNTRIES & TERRITORIES) ---
ISO_COUNTRY_MAP = {
    "AF": "Afghanistan 🇦🇫", "AX": "Åland Islands 🇦🇽", "AL": "Albania 🇦🇱", "DZ": "Algeria 🇩🇿",
    "AS": "American Samoa 🇦🇸", "AD": "Andorra 🇦🇩", "AO": "Angola 🇦🇴", "AI": "Anguilla 🇦🇮",
    "AQ": "Antarctica 🇦🇶", "AG": "Antigua & Barbuda 🇦🇬", "AR": "Argentina 🇦🇷", "AM": "Armenia 🇦🇲",
    "AW": "Aruba 🇦🇼", "AU": "Australia 🇦🇺", "AT": "Austria 🇦🇹", "AZ": "Azerbaijan 🇦🇿",
    "BS": "Bahamas 🇧🇸", "BH": "Bahrain 🇧🇭", "BD": "Bangladesh 🇧🇩", "BB": "Barbados 🇧🇧",
    "BY": "Belarus 🇧🇾", "BE": "Belgium 🇧🇪", "BZ": "Belize 🇧🇿", "BJ": "Benin 🇧🇯",
    "BM": "Bermuda 🇧🇲", "BT": "Bhutan 🇧🇹", "BO": "Bolivia 🇧🇴", "BQ": "Caribbean Netherlands 🇧🇶",
    "BA": "Bosnia & Herzegovina 🇧🇦", "BW": "Botswana 🇧🇼", "BV": "Bouvet Island 🇧🇻", "BR": "Brazil 🇧🇷",
    "IO": "British Indian Ocean Territory 🇮🇴", "VG": "British Virgin Islands 🇻🇬", "BN": "Brunei 🇧🇳",
    "BG": "Bulgaria 🇧🇬", "BF": "Burkina Faso 🇧🇫", "BI": "Burundi 🇧🇮", "CV": "Cabo Verde 🇨🇻",
    "KH": "Cambodia 🇰🇭", "CM": "Cameroon 🇨🇲", "CA": "Canada 🇨🇦", "KY": "Cayman Islands 🇰🇾",
    "CF": "Central African Republic 🇨🇫", "TD": "Chad 🇹🇩", "CL": "Chile 🇨🇱", "CN": "China 🇨🇳",
    "CX": "Christmas Island 🇨🇽", "CC": "Cocos (Keeling) Islands 🇨🇨", "CO": "Colombia 🇨🇴",
    "KM": "Comoros 🇰🇲", "CD": "Congo (DRC) 🇨🇩", "CG": "Congo (Republic) 🇨🇬", "CK": "Cook Islands 🇨🇰",
    "CR": "Costa Rica 🇨🇷", "HR": "Croatia 🇭🇷", "CU": "Cuba 🇨🇺", "CW": "Curaçao 🇨🇼",
    "CY": "Cyprus 🇨🇾", "CZ": "Czechia 🇨🇿", "CI": "Côte d'Ivoire 🇨🇮", "DK": "Denmark 🇩🇰",
    "DJ": "Djibouti 🇩🇯", "DM": "Dominica 🇩🇲", "DO": "Dominican Republic 🇩🇴", "EC": "Ecuador 🇪🇨",
    "EG": "Egypt 🇪🇬", "SV": "El Salvador 🇸🇻", "GQ": "Equatorial Guinea 🇬🇶", "ER": "Eritrea 🇪🇷",
    "EE": "Estonia 🇪🇪", "SZ": "Eswatini 🇸🇿", "ET": "Ethiopia 🇪🇹", "FK": "Falkland Islands 🇫🇰",
    "FO": "Faroe Islands 🇫🇴", "FJ": "Fiji 🇫🇯", "FI": "Finland 🇫🇮", "FR": "France 🇫🇷",
    "GF": "French Guiana 🇬🇫", "PF": "French Polynesia 🇵🇫", "TF": "French Southern Territories 🇹🇫",
    "GA": "Gabon 🇬🇦", "GM": "Gambia 🇬🇲", "GE": "Georgia 🇬🇪", "DE": "Germany 🇩🇪",
    "GH": "Ghana 🇬🇭", "GI": "Gibraltar 🇬🇮", "GR": "Greece 🇬🇷", "GL": "Greenland 🇬🇱",
    "GD": "Grenada 🇬🇩", "GP": "Guadeloupe 🇬🇵", "GU": "Guam 🇬🇺", "GT": "Guatemala 🇬🇹",
    "GG": "Guernsey 🇬🇬", "GN": "Guinea 🇬🇳", "GW": "Guinea-Bissau 🇬🇼", "GY": "Guyana 🇬🇾",
    "HT": "Haiti 🇭🇹", "HM": "Heard & McDonald Islands 🇭🇲", "VA": "Vatican City 🇻🇦",
    "HN": "Honduras 🇭🇳", "HK": "Hong Kong 🇭🇰", "HU": "Hungary 🇭🇺", "IS": "Iceland 🇮🇸",
    "IN": "India 🇮🇳", "ID": "Indonesia 🇮🇩", "IR": "Iran 🇮🇷", "IQ": "Iraq 🇮🇶",
    "IE": "Ireland 🇮🇪", "IM": "Isle of Man 🇮🇲", "IL": "Israel 🇮🇱", "IT": "Italy 🇮🇹",
    "JM": "Jamaica 🇯🇲", "JP": "Japan 🇯🇵", "JE": "Jersey 🇯🇪", "JO": "Jordan 🇯🇴",
    "KZ": "Kazakhstan 🇰🇿", "KE": "Kenya 🇰🇪", "KI": "Kiribati 🇰🇮", "KP": "North Korea 🇰🇵",
    "KR": "South Korea 🇰🇷", "KW": "Kuwait 🇰🇼", "KG": "Kyrgyzstan 🇰🇬", "LA": "Laos 🇱🇦",
    "LV": "Latvia 🇱🇻", "LB": "Lebanon 🇱🇧", "LS": "Lesotho 🇱🇸", "LR": "Liberia 🇱🇷",
    "LY": "Libya 🇱🇾", "LI": "Liechtenstein 🇱🇮", "LT": "Lithuania 🇱🇹", "LU": "Luxembourg 🇱🇺",
    "MO": "Macau 🇲🇴", "MG": "Madagascar 🇲🇬", "MW": "Malawi 🇲🇼", "MY": "Malaysia 🇲🇾",
    "MV": "Maldives 🇲🇻", "ML": "Mali 🇲🇱", "MT": "Malta 🇲🇹", "MH": "Marshall Islands 🇲🇭",
    "MQ": "Martinique 🇲🇶", "MR": "Mauritania 🇲🇷", "MU": "Mauritius 🇲🇺", "YT": "Mayotte 🇾🇹",
    "MX": "Mexico 🇲🇽", "FM": "Micronesia 🇫🇲", "MD": "Moldova 🇲🇩", "MC": "Monaco 🇲🇨",
    "MN": "Mongolia 🇲🇳", "ME": "Montenegro 🇲🇪", "MS": "Montserrat 🇲🇸", "MA": "Morocco 🇲🇦",
    "MZ": "Mozambique 🇲🇿", "MM": "Myanmar 🇲🇲", "NA": "Namibia 🇳🇦", "NR": "Nauru 🇳🇷",
    "NP": "Nepal 🇳🇵", "NL": "Netherlands 🇳🇱", "NC": "New Caledonia 🇳🇨", "NZ": "New Zealand 🇳🇿",
    "NI": "Nicaragua 🇳🇮", "NE": "Niger 🇳🇪", "NG": "Nigeria 🇳🇬", "NU": "Niue 🇳🇺",
    "NF": "Norfolk Island 🇳🇫", "MK": "North Macedonia 🇲🇰", "MP": "Northern Mariana Islands 🇲🇵",
    "NO": "Norway 🇳🇴", "OM": "Oman 🇴🇲", "PK": "Pakistan 🇵🇰", "PW": "Palau 🇵🇼",
    "PS": "Palestine 🇵🇸", "PA": "Panama 🇵🇦", "PG": "Papua New Guinea 🇵🇬", "PY": "Paraguay 🇵🇾",
    "PE": "Peru 🇵🇪", "PH": "Philippines 🇵🇭", "PN": "Pitcairn Islands 🇵🇳", "PL": "Poland 🇵🇱",
    "PT": "Portugal 🇵🇹", "PR": "Puerto Rico 🇵🇷", "QA": "Qatar 🇶🇦", "RE": "Réunion 🇷🇪",
    "RO": "Romania 🇷🇴", "RU": "Russia 🇷🇺", "RW": "Rwanda 🇷🇼", "BL": "St. Barthélemy 🇧🇱",
    "SH": "St. Helena 🇸🇭", "KN": "St. Kitts & Nevis 🇰🇳", "LC": "St. Lucia 🇱🇨", "MF": "St. Martin 🇲🇫",
    "PM": "St. Pierre & Miquelon 🇵🇲", "VC": "St. Vincent & Grenadines 🇻🇨", "WS": "Samoa 🇼🇸",
    "SM": "San Marino 🇸🇲", "ST": "Sao Tome & Principe 🇸🇹", "SA": "Saudi Arabia 🇸🇦",
    "SN": "Senegal 🇸🇳", "RS": "Serbia 🇷🇸", "SC": "Seychelles 🇸🇨", "SL": "Sierra Leone 🇸🇱",
    "SG": "Singapore 🇸🇬", "SX": "Sint Maarten 🇸🇽", "SK": "Slovakia 🇸🇰", "SI": "Slovenia 🇸🇮",
    "SB": "Solomon Islands 🇸🇧", "SO": "Somalia 🇸🇴", "ZA": "South Africa 🇿🇦", "GS": "South Georgia 🇬🇸",
    "SS": "South Sudan 🇸🇸", "ES": "Spain 🇪🇸", "LK": "Sri Lanka 🇱🇰", "SD": "Sudan 🇸🇩",
    "SR": "Suriname 🇸🇷", "SJ": "Svalbard & Jan Mayen 🇸🇯", "SE": "Sweden 🇸🇪", "CH": "Switzerland 🇨🇭",
    "SY": "Syria 🇸🇾", "TW": "Taiwan 🇹🇼", "TJ": "Tajikistan 🇹🇯", "TZ": "Tanzania 🇹🇿",
    "TH": "Thailand 🇹🇭", "TL": "Timor-Leste 🇹🇱", "TG": "Togo 🇹🇬", "TK": "Tokelau 🇹🇰",
    "TO": "Tonga 🇹🇴", "TT": "Trinidad & Tobago 🇹🇹", "TN": "Tunisia 🇹🇳", "TR": "Türkiye 🇹🇷",
    "TM": "Turkmenistan 🇹🇲", "TC": "Turks & Caicos Islands 🇹🇨", "TV": "Tuvalu 🇹🇻",
    "UG": "Uganda 🇺🇬", "UA": "Ukraine 🇺🇦", "AE": "UAE 🇦🇪", "GB": "UK 🇬🇧",
    "US": "USA 🇺🇸", "UM": "U.S. Outlying Islands 🇺🇲", "UY": "Uruguay 🇺🇾", "UZ": "Uzbekistan 🇺🇿",
    "VU": "Vanuatu 🇻🇺", "VE": "Venezuela 🇻🇪", "VN": "Vietnam 🇻🇳", "VI": "U.S. Virgin Islands 🇻🇮",
    "WF": "Wallis & Futuna 🇼🇫", "EH": "Western Sahara 🇪🇭", "YE": "Yemen 🇾🇪", "ZM": "Zambia 🇿🇲",
    "ZW": "Zimbabwe 🇿🇼"
}

# --- MASTER PREFIX MAPPER (250+ PHONE CODES FOR SEARCH) ---
ISO_TO_PREFIX = {
    "AF": "93", "AX": "358", "AL": "355", "DZ": "213", "AS": "1684", "AD": "376", "AO": "244", 
    "AI": "1264", "AQ": "672", "AG": "1268", "AR": "54", "AM": "374", "AW": "297", "AU": "61", 
    "AT": "43", "AZ": "994", "BS": "1242", "BH": "973", "BD": "880", "BB": "1246", "BY": "375", 
    "BE": "32", "BZ": "501", "BJ": "229", "BM": "1441", "BT": "975", "BO": "591", "BQ": "599", 
    "BA": "387", "BW": "267", "BV": "47", "BR": "55", "IO": "246", "VG": "1284", "BN": "673", 
    "BG": "359", "BF": "226", "BI": "257", "CV": "238", "KH": "855", "CM": "237", "CA": "1", 
    "KY": "1345", "CF": "236", "TD": "235", "CL": "56", "CN": "86", "CX": "61", "CC": "61", 
    "CO": "57", "KM": "269", "CD": "243", "CG": "242", "CK": "682", "CR": "506", "HR": "385", 
    "CU": "53", "CW": "599", "CY": "357", "CZ": "420", "CI": "225", "DK": "45", "DJ": "253", 
    "DM": "1767", "DO": "1809", "EC": "593", "EG": "20", "SV": "503", "GQ": "240", "ER": "291", 
    "EE": "372", "SZ": "268", "ET": "251", "FK": "500", "FO": "298", "FJ": "679", "FI": "358", 
    "FR": "33", "GF": "594", "PF": "689", "TF": "262", "GA": "241", "GM": "220", "GE": "995", 
    "DE": "49", "GH": "233", "GI": "350", "GR": "30", "GL": "299", "GD": "1473", "GP": "590", 
    "GU": "1671", "GT": "502", "GG": "44", "GN": "224", "GW": "245", "GY": "592", "HT": "509", 
    "HM": "672", "VA": "379", "HN": "504", "HK": "852", "HU": "36", "IS": "354", "IN": "91", 
    "ID": "62", "IR": "98", "IQ": "964", "IE": "353", "IM": "44", "IL": "972", "IT": "39", 
    "JM": "1876", "JP": "81", "JE": "44", "JO": "962", "KZ": "7", "KE": "254", "KI": "686", 
    "KP": "850", "KR": "82", "KW": "965", "KG": "996", "LA": "856", "LV": "371", "LB": "961", 
    "LS": "266", "LR": "231", "LY": "218", "LI": "423", "LT": "370", "LU": "352", "MO": "853", 
    "MG": "261", "MW": "265", "MY": "60", "MV": "960", "ML": "223", "MT": "356", "MH": "692", 
    "MQ": "596", "MR": "222", "MU": "230", "YT": "262", "MX": "52", "FM": "691", "MD": "373", 
    "MC": "377", "MN": "976", "ME": "382", "MS": "1664", "MA": "212", "MZ": "258", "MM": "95", 
    "NA": "264", "NR": "674", "NP": "977", "NL": "31", "NC": "687", "NZ": "64", "NI": "505", 
    "NE": "227", "NG": "234", "NU": "683", "NF": "672", "MK": "389", "MP": "1670", "NO": "47", 
    "OM": "968", "PK": "92", "PW": "680", "PS": "970", "PA": "507", "PG": "675", "PY": "595", 
    "PE": "51", "PH": "63", "PN": "870", "PL": "48", "PT": "351", "PR": "1787", "QA": "974", 
    "RE": "262", "RO": "40", "RU": "7", "RW": "250", "BL": "590", "SH": "290", "KN": "1869", 
    "LC": "1758", "MF": "590", "PM": "508", "VC": "1784", "WS": "685", "SM": "378", "ST": "239", 
    "SA": "966", "SN": "221", "RS": "381", "SC": "248", "SL": "232", "SG": "65", "SX": "1721", 
    "SK": "421", "SI": "386", "SB": "677", "SO": "252", "ZA": "27", "GS": "500", "SS": "211", 
    "ES": "34", "LK": "94", "SD": "249", "SR": "597", "SJ": "47", "SE": "46", "CH": "41", 
    "SY": "963", "TW": "886", "TJ": "992", "TZ": "255", "TH": "66", "TL": "670", "TG": "228", 
    "TK": "690", "TO": "676", "TT": "1868", "TN": "216", "TR": "90", "TM": "993", "TC": "1649", 
    "TV": "688", "UG": "256", "UA": "380", "AE": "971", "GB": "44", "US": "1", "UM": "1", 
    "UY": "598", "UZ": "998", "VU": "678", "VE": "58", "VN": "84", "VI": "1340", "WF": "681", 
    "EH": "212", "YE": "967", "ZM": "260", "ZW": "263"
}

def get_country_name(code):
    return ISO_COUNTRY_MAP.get(code.upper(), f"{code.upper()} 🏳️")

async def fetch_server2_countries():
    """Fetches and sorts countries from the active API (TGPVA or TG-Lion)"""
    settings = settings_col.find_one({"_id": "server_config"}) or {}
    active_api = settings.get("s2_api", "tgpva")
    profit_margin = float(settings.get("s2_profit", 0.0))
    
    countries_list = []
    
    async with aiohttp.ClientSession() as session:
        if active_api == "tgpva":
            url = f"https://tgpva.com/api/user/getCountries?apiKey={TGPVA_API_KEY}"
            try:
                async with session.get(url, timeout=10) as resp:
                    data = await resp.json()
                    if data.get("ok") and "result" in data:
                        res = data["result"]
                        
                        # Safely find the service key ("1", "tg", "telegram", or first available dict)
                        countries_dict = res.get("countries", {})
                        avail_dict = res.get("availability", {})
                        
                        srv_key = "1" if "1" in countries_dict else ("tg" if "tg" in countries_dict else None)
                        
                        # If standard keys aren't found, try grabbing the first available service key
                        if not srv_key and isinstance(countries_dict, dict) and len(countries_dict) > 0:
                            srv_key = list(countries_dict.keys())[0]
                            
                        if srv_key and srv_key in countries_dict:
                            prices = countries_dict[srv_key]
                            qtys = avail_dict.get(srv_key, {})
                            
                            for code, base_price in prices.items():
                                try:
                                    # Safe type casting to handle string outputs from API
                                    qty = int(float(qtys.get(code, 0)))
                                    price_val = float(base_price)
                                    
                                    if qty > 0 and price_val > 0:
                                        final_usd = price_val + (price_val * profit_margin / 100.0)
                                        full_name = get_country_name(code)
                                        countries_list.append({"code": code, "name": full_name, "price": final_usd, "qty": qty})
                                except (ValueError, TypeError):
                                    continue
            except Exception as e:
                print(f"❌ TGPVA Fetch Error: {e}")
                                
        elif active_api == "tglion":
            url = f"https://TG-Lion.net?action=available_countries&apiKey={TGLION_API_KEY}&YourID={TGLION_ID}"
            try:
                async with session.get(url, timeout=10) as resp:
                    data = await resp.json()
                    if data.get("status") == "ok" and "countries" in data:
                        for c in data["countries"].values():
                            try:
                                base_price = float(c["price"])
                                qty = int(float(c.get("qty", 0)))
                                if qty > 0:
                                    final_usd = base_price + (base_price * profit_margin / 100.0)
                                    countries_list.append({"code": c["code"], "name": c["name"], "price": final_usd, "qty": qty})
                            except (ValueError, TypeError):
                                continue
            except Exception as e:
                print(f"❌ TG-Lion Fetch Error: {e}")

    countries_list.sort(key=lambda x: x["price"])
    return countries_list, active_api


# ================= Buy Flow (A to Z Routing) =================

@dp.callback_query(lambda c: c.data == "buy")
async def callback_buy_main(cq: CallbackQuery):
    await cq.answer()

    user = get_or_create_user(cq.from_user.id, cq.from_user.username)
    balance_inr = user.get("balance", 0.0)
    balance_usdt = balance_inr / 95.0

    settings = settings_col.find_one({"_id": "server_config"}) or {}

    s1 = settings.get("s1", "active")
    s2 = settings.get("s2", "active")
    lzt = settings.get("lzt_enabled", True)

    text = (
        f"🍷 <b>Buy Ready Telegram Accounts</b>:\n"
        f"––––––—————––––——–––•\n"
        f"<u>• One-click Telegram account purchase\n"
        f"• 100% activation & code delivery\n"
        f"• All accounts are clean [100% No Spam]\n"
        f"• Request multiple codes for free</u>\n"
        f"<b>• Total balance -</b> ₹{balance_inr:.2f} | ${balance_usdt:.2f}"
    )

    kb = InlineKeyboardBuilder()

    # Server 1
    if s1 == "active":
        kb.row(
            InlineKeyboardButton(style="primary", 
                text="◍ Server- 1 (Cheap)",
                callback_data="buy_server1_route"
            )
        )
    else:
        kb.row(
            InlineKeyboardButton(style="primary", 
                text="◍ Server- 1 [Maintenance 🛠️]",
                callback_data="server_maintenance"
            )
        )

    # Server 2
    if s2 == "active":
        kb.row(
            InlineKeyboardButton(style="primary", 
                text="◍ Server- 2 (Good Quality)",
                callback_data="buy_server2:0"
            )
        )
    else:
        kb.row(
            InlineKeyboardButton(style="primary", 
                text="◍ Server- 2 [Maintenance 🛠️]",
                callback_data="server_maintenance"
            )
        )

    # Server 3 - LZT Telegram Accounts
    if lzt:
        kb.row(
            InlineKeyboardButton(style="primary", 
                text="◍ Server-3(Cheap phishing Accounts)",
                callback_data="s3tg_open"
            )
        )

    # Back
    kb.row(
        InlineKeyboardButton(style="primary", 
            text="▪️ Previous",
            callback_data="back_main"
        )
    )

    await cq.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )
    
# Server 1 Route: Redirect to your existing Pagination flow
@dp.callback_query(F.data == "buy_server1_route")
async def callback_route_server1(cq: CallbackQuery):
    await cq.answer()
    await send_country_menu(cq)

# Maintenance Handler
@dp.callback_query(F.data == "server_maintenance")
async def server_maintenance_alert(cq: CallbackQuery):
    await cq.answer("⚠️ This server is currently under maintenance or temporarily disabled by admin.", show_alert=True)

#Server 1 continues to normal country menu
@dp.callback_query(lambda c: c.data == "buy_server1")
async def callback_buy_server1(cq: CallbackQuery):
    await cq.answer()
    await send_country_menu(cq)  # Use the same country menu function

# --- MASTER PREFIX MAPPER (For Number Search) ---
ISO_TO_PREFIX = {
    "AF": "93", "AL": "355", "DZ": "213", "AD": "376", "AO": "244", "AG": "1268", "AR": "54", 
    "AM": "374", "AU": "61", "AT": "43", "AZ": "994", "BS": "1242", "BH": "973", "BD": "880", 
    "BB": "1246", "BY": "375", "BE": "32", "BZ": "501", "BJ": "229", "BT": "975", "BO": "591", 
    "BA": "387", "BW": "267", "BR": "55", "BG": "359", "BF": "226", "BI": "257", "CV": "238", 
    "KH": "855", "CM": "237", "CA": "1", "CF": "236", "TD": "235", "CL": "56", "CN": "86", 
    "CO": "57", "KM": "269", "CD": "243", "CG": "242", "CR": "506", "HR": "385", "CU": "53", 
    "CY": "357", "CZ": "420", "CI": "225", "DK": "45", "DJ": "253", "DO": "1809", "EC": "593", 
    "EG": "20", "SV": "503", "GQ": "240", "EE": "372", "SZ": "268", "ET": "251", "FI": "358", 
    "FR": "33", "GA": "241", "GM": "220", "GE": "995", "DE": "49", "GH": "233", "GR": "30", 
    "GT": "502", "GN": "224", "HT": "509", "HN": "504", "HU": "36", "IS": "354", "IN": "91", 
    "ID": "62", "IR": "98", "IQ": "964", "IE": "353", "IL": "972", "IT": "39", "JM": "1876", 
    "JP": "81", "JO": "962", "KZ": "7", "KE": "254", "KW": "965", "KG": "996", "LA": "856", 
    "LV": "371", "LB": "961", "LS": "266", "LR": "231", "LY": "218", "LT": "370", "LU": "352", 
    "MG": "261", "MW": "265", "MY": "60", "MV": "960", "ML": "223", "MT": "356", "MR": "222", 
    "MU": "230", "MX": "52", "MD": "373", "MN": "976", "ME": "382", "MA": "212", "MZ": "258", 
    "MM": "95", "NA": "264", "NP": "977", "NL": "31", "NZ": "64", "NI": "505", "NE": "227", 
    "NG": "234", "MK": "389", "NO": "47", "OM": "968", "PK": "92", "PA": "507", "PY": "595", 
    "PE": "51", "PH": "63", "PL": "48", "PT": "351", "QA": "974", "RO": "40", "RU": "7", 
    "RW": "250", "SA": "966", "SN": "221", "RS": "381", "SL": "232", "SG": "65", "SK": "421", 
    "SI": "386", "SO": "252", "ZA": "27", "ES": "34", "LK": "94", "SD": "249", "SE": "46", 
    "CH": "41", "SY": "963", "TJ": "992", "TZ": "255", "TH": "66", "TG": "228", "TN": "216", 
    "TR": "90", "TM": "993", "UG": "256", "UA": "380", "AE": "971", "GB": "44", "US": "1", 
    "UY": "598", "UZ": "998", "VE": "58", "VN": "84", "YE": "967", "ZM": "260", "ZW": "263",
    "HK": "852", "MO": "853", "TW": "886", "PR": "1", "PF": "689", "GP": "590"
}

# --- FSM State for Search ---
class Server2Search(StatesGroup):
    waiting_query = State()

# --- Helper: Safe Float Extractor from String (e.g., "3.2 USD" -> 3.2) ---
def safe_extract_float(val):
    if not val: return 0.0
    try:
        return float(val)
    except ValueError:
        match = re.search(r"[-+]?\d*\.\d+|\d+", str(val))
        return float(match.group()) if match else 0.0

# --- Helper: API Balance Check & Admin Low Balance Alert ---
async def check_s2_api_balance():
    settings = settings_col.find_one({"_id": "server_config"}) or {}
    active_api = settings.get("s2_api", "tgpva")
    
    try:
        async with aiohttp.ClientSession() as session:
            if active_api == "tgpva":
                url = f"https://tgpva.com/api/user/getBalance?apiKey={TGPVA_API_KEY}"
                async with session.get(url, timeout=5) as resp:
                    data = await resp.json()
                    if data.get("ok") and "result" in data:
                        bal = safe_extract_float(data["result"].get("balance", 0))
                        if bal < 1.0:
                            try: await bot.send_message(ADMINLOG, f"⚠️ <b>Low Balance Alert:</b> TGPVA balance is {bal} USD!")
                            except Exception: pass
                        return bal > 0.1, active_api
                        
            elif active_api == "tglion":
                url = f"https://TG-Lion.net?action=get_balance&apiKey={TGLION_API_KEY}&YourID={TGLION_ID}"
                async with session.get(url, timeout=5) as resp:
                    data = await resp.json()
                    if data.get("status") == "ok":
                        # Safe extract use kiya taaki "3.2 USD" se sirf 3.2 nikle
                        bal = safe_extract_float(data.get("balance", 0))
                        if bal < 1.0:
                            try: await bot.send_message(ADMINLOG, f"⚠️ <b>Low Balance Alert:</b> TG-Lion balance is {bal} USD!")
                            except Exception: pass
                        return bal > 0.1, active_api
    except Exception as e:
        print(f"❌ Balance Check Error ({active_api}): {e}")
        
    return False, active_api
    
# ================= SERVER 2 (API AUTO FLOW) =================
@dp.callback_query(F.data.startswith("buy_server2:"))
async def callback_buy_server2(cq: CallbackQuery):
    page = int(cq.data.split(":")[1])
    await cq.message.edit_text("🔄 <i>Fetching live stock from Server 2...</i>", parse_mode="HTML")
    
    countries, active_api = await fetch_server2_countries()
    
    if not countries:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(style="primary", text="🔙 Back", callback_data="buy")]])
        return await cq.message.edit_text("❌ No stock available or API is down right now.", reply_markup=kb)
        
    ITEMS_PER_PAGE = 20
    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    page_items = countries[start:end]
    
    kb = InlineKeyboardBuilder()
    
    for c in page_items:
        inr_price = c['price'] * 95.0
        btn_text = f"{c['name']} (₹{inr_price:.2f})"
        kb.button(style="primary", text=btn_text, callback_data=f"srv2_info:{c['code']}:{c['price']:.4f}")
    kb.adjust(2)
    
    nav_row = []
    if page > 0: 
        nav_row.append(InlineKeyboardButton(style="primary", text="⬅️ Previous", callback_data=f"buy_server2:{page-1}"))
    if end < len(countries): 
        nav_row.append(InlineKeyboardButton(style="primary", text="Next ➡️", callback_data=f"buy_server2:{page+1}"))
    if nav_row: 
        kb.row(*nav_row)
        
    kb.row(InlineKeyboardButton(style="primary", text="🔍 Search", callback_data="srv2_search_init"))
    
    kb.row(InlineKeyboardButton(style="primary", text="🔙 Back", callback_data="buy"))
    
    text = (
        f"🌍 <b>Telegram Server 2 (Good Quality) - Live Stock:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Showing cheapest countries first. Total: {len(countries)} available.</i>"
    )
    await cq.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())

# --- SEARCH LOGIC (With Country, Code, and Prefix) ---
@dp.callback_query(F.data == "srv2_search_init")
async def srv2_search_init(cq: CallbackQuery, state: FSMContext):
    await cq.message.answer(
        "🔍 <b>Enter Country Name, Code, or Phone Prefix:</b>\n"
        "<i>Ex: India, IN, ZM, +1, 880...</i>", 
        parse_mode="HTML"
    )
    await state.set_state(Server2Search.waiting_query)
    await cq.answer()

@dp.message(StateFilter(Server2Search.waiting_query))
async def srv2_do_search(msg: Message, state: FSMContext):
    query = msg.text.strip().lower()
    clean_query = query.replace("+", "")
    await state.clear()
    
    status_msg = await msg.answer("🔄 <i>Searching...</i>", parse_mode="HTML")
    countries, _ = await fetch_server2_countries()
    
    results = []
    for c in countries:
        code = c['code'].upper()
        name = c['name'].lower()
        prefix = ISO_TO_PREFIX.get(code, "")
        
        if query in name or query == code.lower() or clean_query == prefix:
            results.append(c)
    
    if not results:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(style="primary", text="🔍 Search Again", callback_data="srv2_search_init")],
            [InlineKeyboardButton(style="primary", text="🔙 Back to List", callback_data="buy_server2:0")]
        ])
        return await status_msg.edit_text(f"❌ No results found for '<b>{msg.text}</b>'.", parse_mode="HTML", reply_markup=kb)
        
    kb = InlineKeyboardBuilder()
    for c in results[:20]:
        inr_price = c['price'] * 95.0
        kb.button(style="primary", text=f"{c['name']} (₹{inr_price:.2f})", callback_data=f"srv2_info:{c['code']}:{c['price']:.4f}")
    kb.adjust(2)
    
    kb.row(InlineKeyboardButton(style="primary", text="🔍 Search Again", callback_data="srv2_search_init"))
    kb.row(InlineKeyboardButton(style="primary", text="🔙 Back", callback_data="buy_server2:0"))
    
    await status_msg.edit_text(f"🔎 <b>Search Results for '{msg.text}':</b>", parse_mode="HTML", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("srv2_info:"))
async def srv2_country_info(cq: CallbackQuery):
    parts = cq.data.split(":")
    code = parts[1]
    usd_price = float(parts[2])
    inr_price = usd_price * 95.0
    
    await cq.message.edit_text("🔄 <i>Loading country details...</i>", parse_mode="HTML")
    countries, _ = await fetch_server2_countries()
    c_data = next((c for c in countries if c['code'] == code), None)
    
    if not c_data:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(style="primary", text="🔙 Back", callback_data="buy_server2:0")]])
        return await cq.message.edit_text("❌ This country is currently out of stock.", reply_markup=kb)

    text = (
        f"🌍 <b>Country Details</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <b>Country:</b> {c_data['name']}\n"
        f"🏷️ <b>Price:</b> ₹{inr_price:.2f}\n"
        f"📦 <b>Stock Available:</b> {c_data['qty']} pcs\n"
        f"━━━━━━━━━━━━━━━━━━━━━"
    )
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(style="primary", text="🛒 Buy Now", callback_data=f"srv2_terms:{code}:{usd_price:.4f}"))
    kb.row(
        InlineKeyboardButton(style="primary", text="🔙 Back", callback_data="buy_server2:0"),
        InlineKeyboardButton(style="primary", text="🔄 Change Country", callback_data="buy_server2:0")
    )
    await cq.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())

# --- TERMS AND CONDITIONS ---
@dp.callback_query(F.data.startswith("srv2_terms:"))
async def srv2_show_terms(cq: CallbackQuery):
    _, code, usd_price = cq.data.split(":")
    usd_price = float(usd_price)
    inr_price = usd_price * 95.0
    
    settings = settings_col.find_one({"_id": "server_config"}) or {}
    active_api = settings.get("s2_api", "tgpva")
    country_display_name = get_country_name(code) if active_api == "tgpva" else code
    
    terms_text = (
        f"⚠️ <b>Server 2 (Good Quality) T&C</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌐 <b>Country:</b> {country_display_name}\n"
        f"🚫 <b>No Refunds:</b> All sales are final.\n"
        f"📨 <b>OTP Policy:</b> Infinite codes supported.\n"
        f"⏳ <b>Warranty:</b> Active session ends after logout.\n\n"
        f"🏷️ <b>Price:</b> ₹{inr_price:.2f}\n"
        f"❓ <b>Accept these terms to continue?</b>"
    )
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(style="primary", text="✅ Accept", callback_data=f"srv2_buy:{code}:{usd_price:.4f}"))
    kb.row(InlineKeyboardButton(style="primary", text="🔙 Back", callback_data=f"srv2_info:{code}:{usd_price:.4f}"))
    await cq.message.edit_text(terms_text, parse_mode="HTML", reply_markup=kb.as_markup())

# ================= API DEBUG LOGGER =================
async def log_api_response(api_name: str, action: str, url: str, raw_response: str, is_error: bool = False):
    """Sends raw API responses to the Admin Log group for debugging."""
    try:
        status_icon = "❌ ERROR / FAIL" if is_error else "✅ SUCCESS"
        # API Keys ko URL se hide kar diya taaki logs me key leak na ho
        safe_url = re.sub(r'(api_key|apiKey)=[^&]+', r'\1=HIDDEN_KEY', url)
        # Message lamba hone par Telegram block na kare, isliye 1000 characters limit
        short_resp = str(raw_response)[:1000] 
        
        log_text = (
            f"🛠 <b>API DEBUG LOG</b> | <code>{api_name}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Action:</b> {action}\n"
            f"<b>Status:</b> {status_icon}\n"
            f"<b>Request:</b> <code>{safe_url}</code>\n\n"
            f"<b>Raw Response:</b>\n<pre>{html.escape(short_resp)}</pre>"
        )
        await bot.send_message(ADMINLOG, log_text, parse_mode="HTML")
    except Exception as e:
        print(f"API Logger Failed: {e}")

# --- PURCHASE EXECUTION (SERVER 2) ---
@dp.callback_query(F.data.startswith("srv2_buy:"))
async def srv2_buy_number(cq: CallbackQuery):
    code = cq.data.split(":")[1]
    usd_price = float(cq.data.split(":")[2])
    inr_price = usd_price * 95.0
    user_id = cq.from_user.id
    user = get_or_create_user(user_id, cq.from_user.username)
    
    if user.get("balance", 0.0) < inr_price:
        return await cq.answer("❌ Insufficient Balance! Please recharge.", show_alert=True)
        
    status_msg = await cq.message.edit_text("⏳ <i>Purchasing number... Waiting for response (Please do not click anything)...</i>", parse_mode="HTML")
    
    is_ok, active_api = await check_s2_api_balance()
    if not is_ok:
        try: await bot.send_message(ADMINLOG, f"🚨 <b>CRITICAL ALERT:</b> Server 2 API (<code>{active_api.upper()}</code>) Balance is LOW or ZERO!")
        except Exception: pass
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(style="primary", text="🔙 Back", callback_data="buy_server2:0")]])
        return await status_msg.edit_text("❌ <b>Failed to purchase. Server API Balance is low.</b>", parse_mode="HTML", reply_markup=kb)
    
    phone_number = None
    order_hash = "None"
    last_error = "Unknown Error"
    
    async with aiohttp.ClientSession() as session:
        try:
            if active_api == "tgpva":
                url = f"https://tgpva.com/api/user/getNumber?apiKey={TGPVA_API_KEY}&country={code}"
                async with session.get(url, timeout=35) as resp:
                    text_resp = await resp.text()
                    is_error = '"ok":true' not in text_resp.replace(" ", "")
                    await log_api_response("TGPVA", "getNumber (Buy)", url, text_resp, is_error)
                    
                    data = json.loads(text_resp)
                    if data.get("ok"):
                        phone_number = str(data["result"]["phone"]).strip()
                        order_hash = str(data["result"]["hash"]).strip()
                    else:
                        last_error = str(data)
                        
            elif active_api == "tglion":
                safe_code = str(code).lower().strip()
                url = f"https://TG-Lion.net?action=getNumber&apiKey={TGLION_API_KEY}&YourID={TGLION_ID}&country_code={safe_code}"
                
                async with session.get(url, timeout=35) as resp:
                    text_resp = await resp.text()
                    is_error = "ok" not in text_resp.lower()
                    await log_api_response("TG-Lion", "getNumber (Buy)", url, text_resp, is_error)
                    
                    try:
                        data = json.loads(text_resp)
                        if data.get("status") == "ok":
                            raw_num = data.get("Number") or data.get("number")
                            phone_number = str(raw_num).strip() if raw_num else None
                        else:
                            last_error = str(data.get("msg", text_resp))
                    except Exception:
                        if "ok" in text_resp.lower():
                            num_match = re.search(r'"[nN]umber"\s*:\s*"?(\+?\d+)"?', text_resp)
                            if num_match: phone_number = num_match.group(1).strip()
                        else:
                            msg_match = re.search(r'"msg"\s*:\s*"([^"]+)"', text_resp)
                            last_error = msg_match.group(1) if msg_match else "Non-JSON API Error."
                            
        except asyncio.TimeoutError:
            last_error = "API Timeout: Server took too long to respond."
        except Exception as e:
            last_error = f"Network Error: {e}"

    if not phone_number:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(style="primary", text="🔙 Back", callback_data="buy_server2:0")]])
        return await status_msg.edit_text(f"❌ <b>Purchase failed.</b>\n\nPlease try again later.", parse_mode="HTML", reply_markup=kb)
        
    new_balance = user.get("balance", 0.0) - inr_price
    users_col.update_one({"_id": user_id}, {"$set": {"balance": new_balance}})
    country_display_name = get_country_name(code)

    orders_col.insert_one({
        "user_id": user_id, "country": country_display_name, "number": phone_number,
        "price": inr_price, "server": 2, "status": "purchased", "created_at": datetime.now(timezone.utc)
    })
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(style="primary", text="📩 Get Code", callback_data=f"srv2_otp:{phone_number}:{order_hash}"),
           InlineKeyboardButton(style="primary", text="Copy Num", copy_text=CopyTextButton(text=str(phone_number))))
    kb.row(InlineKeyboardButton(style="primary", text="♻️ Buy Again", callback_data=f"srv2_info:{code}:{usd_price:.4f}"))
    # YAHAN SE LOGOUT BUTTON HATA DIYA GAYA HAI (Purchase Screen)
    kb.row(InlineKeyboardButton(style="primary", text="• Support •", url=f"https://t.me/{SUPPORT}"))

    await status_msg.edit_text(
        f"<pre>✅ Purchased Successfully!</pre>\n"
        f"➖ <b><u>Server</u></b>: Server 2 (Mixed)\n"
        f"➖ <b><u>Country</u></b>: {country_display_name}\n"
        f"📞 <b>Number:</b> <code>{phone_number}</code>\n"
        f"🏷️ <b>Price:</b> ₹{inr_price:.2f}\n"
        f"💸<b> Balance:</b> ₹{new_balance:.2f}",
        parse_mode="HTML", reply_markup=kb.as_markup()
    )


# --- GET OTP (SERVER 2) ---
@dp.callback_query(F.data.startswith("srv2_otp:"))
async def srv2_get_otp(cq: CallbackQuery):
    parts = cq.data.split(":")
    phone_number = parts[1]
    order_hash = parts[2] if len(parts) > 2 else "None"
    
    await cq.answer("🔄 Requesting OTP...", show_alert=False)
    
    settings = settings_col.find_one({"_id": "server_config"}) or {}
    active_api = settings.get("s2_api", "tgpva")
    code, password = None, "None"
    
    async with aiohttp.ClientSession() as session:
        if active_api == "tgpva":
            url = f"https://tgpva.com/api/user/getCode?apiKey={TGPVA_API_KEY}&number={phone_number}&hash={order_hash}"
            try:
                async with session.get(url, timeout=15) as resp:
                    text_resp = await resp.text()
                    await log_api_response("TGPVA", "getCode (OTP)", url, text_resp)
                    data = json.loads(text_resp)
                    if data.get("ok") and data["result"]["status"] == "Received":
                        code = str(data["result"]["code"]).strip()
                        password = str(data["result"].get("password", "None")).strip()
            except Exception: pass
            
        elif active_api == "tglion":
            safe_num = urllib.parse.quote(str(phone_number).strip())
            url = f"https://TG-Lion.net?action=getCode&number={safe_num}&apiKey={TGLION_API_KEY}&YourID={TGLION_ID}"
            try:
                async with session.get(url, timeout=15) as resp:
                    text_resp = await resp.text()
                    is_error = "ok" not in text_resp.lower() or "code" not in text_resp.lower()
                    await log_api_response("TG-Lion", "getCode (OTP)", url, text_resp, is_error)
                    try:
                        data = json.loads(text_resp)
                        if data.get("status") == "ok" and "code" in data:
                            code = str(data.get("code")).strip()
                            password = str(data.get("pass", "None")).strip()
                    except Exception:
                        if "ok" in text_resp.lower() and '"code"' in text_resp:
                            c_match = re.search(r'"code"\s*:\s*"?(\d+)"?', text_resp)
                            p_match = re.search(r'"pass"\s*:\s*"([^"]*)"', text_resp)
                            if c_match: code = c_match.group(1).strip()
                            if p_match: password = p_match.group(1).strip()
            except Exception: pass

    if code:
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(style="primary", text="Copy OTP", copy_text=CopyTextButton(text=str(code))),
               InlineKeyboardButton(style="primary", text="Copy Pass", copy_text=CopyTextButton(text=str(password))))
        kb.row(InlineKeyboardButton(style="primary", text="🔄 Get New Code", callback_data=f"srv2_otp:{phone_number}:{order_hash}"))
        
        # LOGOUT BUTTON SIRF YAHAN DIKHEGA (OTP Milne ke baad)
        if active_api == "tgpva":
            kb.row(InlineKeyboardButton(style="primary", text="🚪 Logout Device", callback_data=f"srv2_logout:{phone_number}"))
            
        await cq.message.edit_text(
            f"<pre>Order Completed ✅</pre>\n"
            f"✅ 𝐍𝗨𝐌𝐁𝐄𝐑 - <code>{phone_number}</code>\n"
            f"💬 𝐂𝐎𝐃𝐄 - <code>{code}</code>\n"
            f"💬 𝐏𝐀𝐒𝐒 - <code>{password}</code>",
            parse_mode="HTML", reply_markup=kb.as_markup()
        )
        
        user_id = cq.from_user.id
        user = users_col.find_one({"_id": user_id}) or {}
        buyer_name = user.get("username") or f"User {user_id}"
        balance = user.get("balance", "N/A")
        order_doc = orders_col.find_one({"number": phone_number, "status": "purchased"})
        country_name = order_doc.get("country", "Unknown") if order_doc else "Unknown"
        masked_number = str(phone_number)[:6] + "•••••" if phone_number else "Hidden"

        channel_message = (
            f"<pre><u>✅ <b>New Number Purchase Successful</b></u></pre>\n\n"
            f"➖ <b><u>Country:</u></b> {country_name}\n"
            f"➖ <b><u>Application:</u> Теlegгам 🍷</b>\n\n"
            f"➕ <b>Number: {masked_number} 📞</b>\n"
            f"➕ <b>Code</b> <span class='tg-spoiler'>{code}</span> 💬\n"
            f"➕ <b>Server:</b> (2) 🥂\n"
            f"➕ <b>Password:</b> <span class='tg-spoiler'>{password}</span> 🔐\n\n"
            f"<b>• @{BOTUSER} || @{CHANNEL}</b>"
        )
        buy_button = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(style="primary", text="• Buy Now •", url=f"https://t.me/{BOTUSER}?start=starting")]])
        try: await bot.send_message(SALES, channel_message, parse_mode="HTML", reply_markup=buy_button)
        except Exception: pass

        admin_message = (
            f"<pre>📢 New Purchase Alert</pre>\n\n"
            f"<b>• Application:</b> Telegram\n"
            f"<b>• Country:</b> {country_name}\n"
            f"<b>• Number:</b> {phone_number}\n"
            f"<b>• OTP:</b> <code>{code}</code>\n"
            f"➖ <b>Password:</b> <span class='tg-spoiler'>{password}</span> 🔐\n\n"
            f"<b>👤 User:</b> @{buyer_name} (<code>{user_id}</code>)\n"
            f"<b>💰 Balance:</b> ₹{balance}"
        )
        userbutton = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(style="primary", text="USER ID", url=f"tg://openmessage?user_id={user_id}")]])
        try: await bot.send_message(ADMINLOG, admin_message, parse_mode="HTML", reply_markup=userbutton)
        except Exception: pass

    else:
        await cq.answer("💬 No code received yet. Try pressing 'Get Code' again after 5 seconds.", show_alert=True)


# --- LOGOUT DEVICE (SERVER 2) ---
@dp.callback_query(F.data.startswith("srv2_logout:"))
async def srv2_logout_device(cq: CallbackQuery):
    phone_number = cq.data.split(":")[1]
    await cq.answer("🔄 Logging out device...", show_alert=False)
    url = f"https://tgpva.com/api/user/getLogout?apiKey={TGPVA_API_KEY}&number={phone_number}"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=15) as resp:
                text_resp = await resp.text()
                
                # Admin Log tak ki tumhe pata chale API kya bol rahi hai
                is_error = '"ok":true' not in text_resp.replace(" ", "")
                await log_api_response("TGPVA", "getLogout (Logout Device)", url, text_resp, is_error)
                
                data = json.loads(text_resp)
                if data.get("ok"):
                    # Success Msg & Polling Closed Message
                    await cq.message.answer(
                        "✅ <b>Bot session has been logged out successfully!</b>\n"
                        "🚫 OTP polling closed for this number.",
                        parse_mode="HTML"
                    )
                    new_kb = InlineKeyboardBuilder()
                    new_kb.row(InlineKeyboardButton(style="primary", text="• Support •", url=f"https://t.me/{SUPPORT}"))
                    try: await cq.message.edit_reply_markup(reply_markup=new_kb.as_markup())
                    except Exception: pass
                else:
                    await cq.message.answer(f"❌ <b>Failed to logout:</b> {data.get('error', 'Unknown Error')}", parse_mode="HTML")
        except Exception:
            await cq.answer("❌ Error connecting to logout.", show_alert=True)


# ================= Country Menu with Pagination =================
COUNTRIES_PER_PAGE = 10

async def send_country_menu(cq: CallbackQuery, page: int = 0):
    countries = await asyncio.to_thread(lambda: list(countries_col.find({})))
    total = len(countries)

    if total == 0:
        return await cq.message.edit_text("❌ No countries available. Admin must add stock first.")
    user_id = cq.from_user.id
    full_name = cq.from_user.full_name  # always use the name
    user_mention = f"<a href='tg://user?id={user_id}'>{full_name}</a>"
    user = users_col.find_one({"_id": user_id})
    balance = f"₹{user['balance']:.2f} " if user else "₹0 "

    # Pagination logic
    start = page * COUNTRIES_PER_PAGE
    end = start + COUNTRIES_PER_PAGE
    paginated = countries[start:end]

    kb = InlineKeyboardBuilder()
    for c in paginated:
        kb.button(style="primary", text=html.escape(c["name"]), callback_data=f"country:{c['name']}")
    kb.adjust(2)

    # Navigation buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(style="primary", text="▪️Previous", callback_data=f"countries_page:{page-1}"))
    if end < total:
        nav_buttons.append(InlineKeyboardButton(style="primary", text="Next▪️", callback_data=f"countries_page:{page+1}"))
    if nav_buttons:
        kb.row(*nav_buttons)

    # Return to main menu
    kb.row(InlineKeyboardButton(style="primary", text="▪️Home", callback_data="back_main"))

    text = f"<b><u>Buy SpamFree Telegram accounts:</u></b>\n––––––––––––––————––•\n◍ <u><b>Total balance:</b></u> {balance}  \n<u>◍ Server:</u> Server (1)\n◍ <b>Page </b>{page+1} of {(total - 1)//COUNTRIES_PER_PAGE + 1}\n✅ <a href=\"https://t.me/{LOGS}\">Sucessful Purchases</a>\n➖➖➖➖➖➖➖➖➖➖➖"
    await cq.message.edit_text(text, reply_markup=kb.as_markup(),parse_mode="HTML", disable_web_page_preview=True)


# ================= Country Pagination Callback =================
@dp.callback_query(lambda c: c.data.startswith("countries_page:"))
async def paginate_countries(cq: CallbackQuery):
    _, page_str = cq.data.split(":")
    try:
        page = int(page_str)
    except ValueError:
        page = 0
    await send_country_menu(cq, page)
    await cq.answer()

# =============== Country Selection =================
@dp.callback_query(lambda c: c.data.startswith("country:"))
async def callback_country(cq: CallbackQuery):
    await cq.answer()
    _, country_name = cq.data.split(":", 1)

    country = await asyncio.to_thread(lambda: countries_col.find_one({"name": country_name}))
    if not country:
        return await cq.answer("❌ Country not found", show_alert=True)

    text = (
        f"<b>Click Buy to Purchase an account:</b>\n––––––––––––—————–•\n"
        f"<blockquote> <b>Country: {html.escape(country['name'])}</b> </blockquote>\n"
        f"◍ <b><u>Price</u></b>: ₹{country['price']}\n"
        f"◍ <b><u>Stock</u></b>: {country['stock']}\n"
        f"◍ <b><u>Server</u></b> - (1)\n––––––––––––—————–•"
    )

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(style="primary", text="▪️ Buy", callback_data=f"buy_now:{country_name}")
    )
    kb.row(
        InlineKeyboardButton(style="primary", text="▪️ Back", callback_data="buy_server1")
    )

    await cq.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())
# ================= Buy Now Flow =================



# ================= Buy Now: Validation & Terms =================
@dp.callback_query(F.data.startswith("buy_now:"))
async def callback_buy_start(cq: CallbackQuery, state: FSMContext):
    # Clear any previous states to avoid conflicts
    await state.clear()
    
    _, country_name = cq.data.split(":", 1)
    
    # Fetch Data Asynchronously
    country, user = await asyncio.to_thread(lambda: (
        countries_col.find_one({"name": country_name}),
        get_or_create_user(cq.from_user.id, cq.from_user.username)
    ))

    # 1. Validate Country Existence
    if not country:
        return await cq.answer("❌ Country data not found.", show_alert=True)

    country_price = country["price"]
    country_stock = country["stock"]
    user_balance = user.get("balance", 0.0)

    # 2. Validate Stock
    if country_stock < 1:
        return await cq.answer(f"⚠️ Out of Stock! No accounts left for {country_name}.", show_alert=True)

    # 3. Validate Balance (If low, show Recharge prompt immediately)
    if user_balance < country_price:
        text = (
            f"🚫 <b>Insufficient Balance</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 <b>Required:</b> ₹{country_price:.2f}\n"
            f"💰 <b>Your Balance:</b> ₹{user_balance:.2f}\n"
            f"📉 <b>Shortage:</b> ₹{country_price - user_balance:.2f}\n\n"
            f"<i>Please recharge your wallet to continue.</i>"
        )
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(style="primary", text="💳 Recharge Now", callback_data="recharge"))
        kb.row(InlineKeyboardButton(style="primary", text="🔙 Back", callback_data=f"country:{country_name}"))
        
        return await cq.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())

    # 4. Show Terms & Confirmation (Balance & Stock are OK)
    terms_text = (
        f"⚠️ <b>Account Buying Terms</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Please review the rules before purchasing <b>1 {country_name}</b> account:\n\n"
        f"🚫 <b>No Refunds:</b> All sales are final.\n"
        f"📨 <b>OTP Policy:</b> Once OTP is received, no return allowed.\n"
        f"❄️ <b>Freeze/Limit:</b> Accounts are fresh; we are not responsible for limits after use.\n"
        f"⏳ <b>Warranty:</b> 10 Minutes to check the account.\n\n"
        f"🏷️ <b>Price:</b> ₹{country_price:.2f}\n"
        f"❓ <b>Do you accept these terms?</b>"
    )

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(style="primary", text="✅ Accept", callback_data=f"confirm_buy:{country_name}"),
        InlineKeyboardButton(style="primary", text="❌ Decline", callback_data=f"country:{country_name}")
    )
    kb.row(InlineKeyboardButton(style="primary", text="🔙 Back", callback_data=f"country:{country_name}"))

    await cq.message.edit_text(terms_text, parse_mode="HTML", reply_markup=kb.as_markup())


# ================= Purchase Execution (Quantity = 1) =================
@dp.callback_query(F.data.startswith("confirm_buy:"))
async def callback_process_purchase(cq: CallbackQuery, state: FSMContext):
    _, country_name = cq.data.split(":", 1)
    user_id = cq.from_user.id
    quantity = 1  # Hardcoded as per requirement

    # Re-fetch data to ensure stock/balance didn't change while reading terms
    country = countries_col.find_one({"name": country_name})
    user = users_col.find_one({"_id": user_id})

    # --- Safety Checks ---
    if not country:
        return await cq.answer("❌ Error: Country missing.", show_alert=True)
    
    if country["stock"] < 1:
        await cq.answer("⚠️ Just sold out! Stock is now 0.", show_alert=True)
        return await send_country_menu(cq) # Redirect to menu

    if user["balance"] < country["price"]:
        await cq.answer("❌ Insufficient balance.", show_alert=True)
        return # Optionally redirect to recharge

    # --- Fetch Unused Number ---
    # We use find_one_and_update (Atomic operation) or find then update
    # Here we fetch one unused number
    number_doc = numbers_col.find_one({"country": country_name, "used": False})
    
    if not number_doc:
        # Stock count mismatch safety
        countries_col.update_one({"name": country_name}, {"$set": {"stock": 0}})
        return await cq.answer("⚠️ System Error: Stock mismatch. Contact Admin.", show_alert=True)

    # --- Calculate New Balance ---
    price = country["price"]
    new_balance = user["balance"] - price

    # --- EXECUTE DB TRANSACTION ---
    try:
        # 1. Deduct Balance
        users_col.update_one({"_id": user_id}, {"$set": {"balance": new_balance}})
        
        # 2. Mark Number as Used
        numbers_col.update_one({"_id": number_doc["_id"]}, {"$set": {"used": True}})
        
        # 3. Create Order Log
        orders_col.insert_one({
            "user_id": user_id,
            "country": country_name,
            "number": number_doc["number"],
            "price": price,
            "status": "purchased",
            "created_at": datetime.now(timezone.utc)
        })
        
        # 4. Decrease Stock
        countries_col.update_one({"name": country_name}, {"$inc": {"stock": -1}})
        
    except Exception as e:
        print(f"Transaction Error: {e}")
        return await cq.answer("❌ Transaction failed. Please try again.", show_alert=True)

    # --- Send Success Message ---
    text_to_copy = str(number_doc["number"])
    
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(style="primary", 
                    text="• Get OTP",
                    callback_data=f"get_otp:{number_doc['number']}"
                ),
                InlineKeyboardButton(style="primary", 
                    text="Copy Num",
                    copy_text=CopyTextButton(text=text_to_copy)
                )
            ],
            [
                InlineKeyboardButton(style="primary", 
                    text="• Support •",                    
                    url=f"https://t.me/{SUPPORT}"
                )
            ]
        ]
    )

    success_msg = (
        f"<pre>✅ Purchased Successfully!</pre>\n"
        f"➖ <b><u>Server</u></b>:  Server (1)\n"
        f"➖<b> <u>Country:</u></b> {country_name}\n"
        f"📞 <b>Number:</b> <code>+{number_doc['number']}</code>\n"
        f"🏷️ <b>Price:</b> ₹{price}\n"
        f"💸<b> Balance:</b> ₹{new_balance:.2f}"
    )

    await cq.message.edit_text(
        success_msg,
        parse_mode="HTML",
        reply_markup=kb
    )    


# ================= Admin Add Number Flow =================
@dp.message(Command("add"))
async def cmd_add_start(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.answer("❌ Not authorized.")
    countries = list(countries_col.find({}))
    if not countries:
        return await msg.answer("❌ No countries found. Add some countries first in DB.")
    kb = InlineKeyboardBuilder()
    for c in countries:
        kb.button(style="primary", text=c["name"], callback_data=f"add_country:{c['name']}")
    kb.adjust(2)
    await msg.answer("🌍 Select the country you want to add a number for:", reply_markup=kb.as_markup())
    await state.set_state(AddSession.waiting_country)

@dp.callback_query(F.data.startswith("add_country:"))
async def callback_add_country(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    _, country_name = cq.data.split(":", 1)
    await state.update_data(country=country_name)
    await cq.message.answer(f"📞 Enter the phone number for {country_name} (e.g., +14151234567):")
    await state.set_state(AddSession.waiting_number)

@dp.message(AddSession.waiting_number)
async def add_number_get_code(msg: Message, state: FSMContext):
    data = await state.get_data()
    country = data["country"]
    phone = msg.text.strip()
    await state.update_data(number=phone)

    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")

    session = StringSession()
    client = TelegramClient(session, api_id, api_hash)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        await msg.answer("📩 Code sent! Please enter the OTP you received on Telegram or SMS:")
        await state.update_data(session=session.save(), phone_code_hash=sent.phone_code_hash)
        await client.disconnect()
        await state.set_state(AddSession.waiting_otp)
    except Exception as e:
        await client.disconnect()
        await msg.answer(f"❌ Failed to send code: {e}")


async def cleanup_old_telegram_sessions(client):
    """Remove old Telegram authorizations while protecting the current login device."""
    removed = 0
    blocked = 0
    try:
        authorizations = await client(GetAuthorizationsRequest())
        for auth in authorizations.authorizations:
            # Telegram marks the device used by this client as current.
            if auth.current:
                continue
            try:
                await client(ResetAuthorizationRequest(hash=int(auth.hash)))
                removed += 1
            except Exception as exc:
                blocked += 1
                print(f"⚠️ Could not remove old Telegram session {auth.hash}: {exc}")
    except Exception as exc:
        print(f"⚠️ Could not inspect Telegram sessions: {exc}")
    return removed, blocked

@dp.message(AddSession.waiting_otp)
async def add_number_verify_code(msg: Message, state: FSMContext):
    data = await state.get_data()
    country = data["country"]
    phone = data["number"]
    session_str = data["session"]
    phone_code_hash = data.get("phone_code_hash")

    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")

    client = TelegramClient(StringSession(session_str), api_id, api_hash)
    await client.connect()

    try:
        await client.sign_in(
            phone=phone,
            code=msg.text.strip(),
            phone_code_hash=phone_code_hash
        )

        # Protect the device that just logged in; remove only older sessions.
        # Telegram may enforce its 24-hour restriction; failures are left untouched.
        await cleanup_old_telegram_sessions(client)

        string_session = client.session.save()
        await client.disconnect()

        # 🔹 SAVE / UPDATE NUMBER
        numbers_col.update_one(
            {"number": phone},
            {
                "$set": {
                    "country": country,
                    "string_session": string_session,
                    "password": None,
                    "used": False
                }
            },
            upsert=True
        )

        # 🔥 ADD TO STOCK (YOU MISSED THIS)
        countries_col.update_one(
            {"name": country},
            {"$inc": {"stock": 1}},
            upsert=True
        )

        await msg.answer(
            f"✅ Session Added Successfully\n\n"
            f"🌍 Country: {country}\n"
            f"📱 Number: <code>{phone}</code>\n"
            f"🔐 Password: <code>None</code>\n\n"
            f"🔑 String Session:\n"
            f"<blockquote expandable><code>{string_session}</code></blockquote>",
            parse_mode="HTML"
        )

        await msg.answer(
            "➕ Send another phone number for this country\n"
            "❌ Or type <b>cancel</b> to stop.",
            parse_mode="HTML"
        )
        await state.set_state(AddSession.waiting_next_action)
    except Exception as e:
        if "PASSWORD" in str(e).upper():
            await msg.answer("🔐 Two-step verification enabled. Send password:")
            await state.set_state(AddSession.waiting_password)
        else:
            await client.disconnect()
            await msg.answer(f"❌ Error verifying OTP: {e}")
            


@dp.message(AddSession.waiting_password)
async def add_number_with_password(msg: Message, state: FSMContext):
    data = await state.get_data()
    country = data["country"]
    phone = data["number"]
    session_str = data["session"]
    password = msg.text.strip()

    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")

    client = TelegramClient(StringSession(session_str), api_id, api_hash)
    await client.connect()

    try:
        await client.sign_in(password=password)

        # Protect the device that just logged in; remove only older sessions.
        await cleanup_old_telegram_sessions(client)

        string_session = client.session.save()
        await client.disconnect()

        # 🔹 SAVE / UPDATE NUMBER
        numbers_col.update_one(
            {"number": phone},
            {
                "$set": {
                    "country": country,
                    "string_session": string_session,
                    "password": password,
                    "used": False
                }
            },
            upsert=True
        )

        # 🔥 ADD TO STOCK (CRITICAL)
        countries_col.update_one(
            {"name": country},
            {"$inc": {"stock": 1}},
            upsert=True
        )

        await msg.answer(
            f"✅ Session Added Successfully (2FA)\n\n"
            f"🌍 Country: {country}\n"
            f"📱 Number: <code>{phone}</code>\n"
            f"🔐 Password: <code>{password}</code>\n\n"
            f"🔑 String Session:\n"
            f"<blockquote expandable><code>{string_session}</code></blockquote>",
            parse_mode="HTML"
        )

        await msg.answer(
            "➕ Send another phone number for this country\n"
            "❌ Or type <b>cancel</b> to stop.",
            parse_mode="HTML"
        )
        await state.set_state(AddSession.waiting_next_action)
    except Exception as e:
        await client.disconnect()
        await msg.answer(f"❌ Error signing in with password: {e}")

@dp.message(AddSession.waiting_next_action)
async def add_next_number_or_cancel(msg: Message, state: FSMContext):
    text = msg.text.strip()

    if text.lower() == "cancel":
        await state.clear()
        return await msg.answer("✅ Add number process cancelled.")

    # otherwise assume phone number
    phone = text
    await state.update_data(number=phone)

    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")

    session = StringSession()
    client = TelegramClient(session, api_id, api_hash)
    await client.connect()

    try:
        sent = await client.send_code_request(phone)
        await state.update_data(
            session=session.save(),
            phone_code_hash=sent.phone_code_hash
        )
        await msg.answer("📩 Code sent! Enter OTP:")
        await state.set_state(AddSession.waiting_otp)
        await client.disconnect()

    except Exception as e:
        await client.disconnect()
        await msg.answer(f"❌ Failed to send code: {e}")
        
        
class RemoveSession(StatesGroup):
    waiting_country = State()
    waiting_number = State()
#Remove
@dp.message(Command("remove"))
async def cmd_remove_start(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.answer("❌ Not authorized.")

    countries = list(countries_col.find({}))
    if not countries:
        return await msg.answer("❌ No countries found.")

    kb = InlineKeyboardBuilder()
    for c in countries:
        kb.button(style="primary", text=c["name"], callback_data=f"remove_country:{c['name']}")
    kb.adjust(2)

    await msg.answer("🌍 Select country to remove number from:", reply_markup=kb.as_markup())
    await state.set_state(RemoveSession.waiting_country)

@dp.callback_query(F.data.startswith("remove_country:"))
async def callback_remove_country(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    _, country = cq.data.split(":", 1)
    await state.update_data(country=country)
    await cq.message.answer(f"📱 Send the phone number to remove from {country}:")
    await state.set_state(RemoveSession.waiting_number)

@dp.message(RemoveSession.waiting_number)
async def remove_number(msg: Message, state: FSMContext):
    data = await state.get_data()
    country = data["country"]
    phone = msg.text.strip()

    doc = numbers_col.find_one({"number": phone, "country": country})
    if not doc:
        await msg.answer("❌ Number not found. Use /remove again.")
        await state.clear()
        return

    numbers_col.delete_one({"_id": doc["_id"]})
    countries_col.update_one(
        {"name": country},
        {"$inc": {"stock": -1}}
    )

    await msg.answer(
        f"✅ Number removed successfully\n\n"
        f"🌍 Country: {country}\n"
        f"📱 Number: <code>{phone}</code>",
        parse_mode="HTML"
    )

    await state.clear()

# ===== Admin Country Commands =====
@dp.message(Command("addcountry"))
async def cmd_add_country(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.answer("❌ Not authorized.")
    await msg.answer("🌍 Send the country name and price separated by a comma (e.g., India,50):")
    await state.set_state("adding_country")

@dp.message(StateFilter("adding_country"))
async def handle_add_country(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return
    if "," not in msg.text:
        return await msg.answer("❌ Invalid format. Example: India,50")
    name, price = msg.text.split(",", 1)
    try:
        price = float(price.strip())
    except ValueError:
        return await msg.answer("❌ Invalid price format.")
    countries_col.update_one({"name": name.strip()}, {"$set": {"price": price, "stock": 0}}, upsert=True)
    await msg.answer(f"✅ Country {name.strip()} added/updated with price {price}.")
    await state.clear()

# ================= Admin: Remove Country =================
@dp.message(Command("removecountry"))
async def cmd_remove_country(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.answer("❌ Not authorized.")

    countries = list(countries_col.find({}))
    if not countries:
        return await msg.answer("📭 No countries to remove.")

    kb = InlineKeyboardBuilder()
    for c in countries:
        kb.button(style="primary", text=c["name"], callback_data=f"removecountry:{c['name']}")
    kb.adjust(2)
    await msg.answer("🌍 Select a country to remove:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("removecountry:"))
async def callback_remove_country(cq: CallbackQuery):
    await cq.answer()
    _, country_name = cq.data.split(":", 1)

    result = countries_col.delete_one({"name": country_name})
    if result.deleted_count == 0:
        await cq.message.edit_text(f"❌ Country <b>{country_name}</b> not found.", parse_mode="HTML")
    else:
        await cq.message.edit_text(f"✅ Country <b>{country_name}</b> removed successfully.", parse_mode="HTML")

@dp.message(Command("db"))
async def cmd_db(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.answer("❌ Not authorized.")

    countries = list(countries_col.find({}))
    if not countries:
        return await msg.answer("❌ No countries found in DB.")

    text = "📚 <b>Numbers in Database by Country:</b>\n\n"

    for c in countries:
        country_name = c["name"]
        numbers = list(numbers_col.find({"country": country_name}))
        text += f"🌍 <b>{country_name}:</b>\n"
        if numbers:
            for num in numbers:
                text += f"• {num['number']} {'✅' if num.get('used') else ''}\n"
        else:
            text += "No number\n"
        text += "\n"

    await msg.answer(text, parse_mode="HTML")



# --- Admin Command: Set Sell Prices ---

# ====================== SELL ACCOUNT FEATURE (FIXED & FULL) ======================

sell_prices_col = db["sell_prices"]

# --- FSM States ---
class SetPrices(StatesGroup):
    waiting_list = State()

class SellSession(StatesGroup):
    waiting_sell_number = State()
    waiting_sell_otp = State()
    waiting_sell_password = State()


# --- Admin Command: Set Sell Prices ---
@dp.message(Command("setprices"))
async def cmd_set_prices(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.answer("❌ Not authorized.")
    
    await msg.answer(
        "📋 <b>Send the price list in this format:</b>\n\n"
        "<code>+1 USA 🇺🇸 - ₹10</code>\n"
        "<code>+91 India 🇮🇳 - ₹29</code>\n"
        "<code>+232 Sierra Leone 🇸🇱 - ₹13</code>\n\n"
        "⚠️ <i>Sending a new list will overwrite the old one.</i>",
        parse_mode="HTML"
    )
    await state.set_state(SetPrices.waiting_list)


@dp.message(StateFilter(SetPrices.waiting_list))
async def handle_set_prices(msg: Message, state: FSMContext):
    text = msg.text.strip()
    
    # IMPROVED REGEX EXPLANATION:
    # (\+\d{1,4})  -> Captures country code (e.g., +1, +232)
    # \s+          -> Matches spaces
    # (.*?)        -> Captures ANY text/emoji (Country Name + Flag) non-greedily until the hyphen
    # \s*-\s* -> Matches the hyphen separator
    # ₹?           -> Matches optional Rupee symbol
    # \s* -> Optional space
    # (\d+)        -> Captures the price number
    pattern = re.compile(r"(\+\d{1,4})\s+(.*?)\s*-\s*₹?\s*(\d+)", re.MULTILINE)

    entries = pattern.findall(text)

    # 1. Validation: Don't delete old data if the new list is empty/invalid
    if not entries:
        return await msg.answer(
            "❌ <b>Invalid format detected.</b>\n\n"
            "Make sure you use the format:\n"
            "<code>+Code CountryName Flag - ₹Price</code>\n"
            "Example:\n<code>+232 Sierra Leone 🇸🇱 - 13</code>", 
            parse_mode="HTML"
        )

    # 2. Database Update: clear old data ONLY after validation passes
    sell_prices_col.delete_many({})
    
    new_data = []
    response_lines = []

    for code, name, price in entries:
        clean_name = name.strip()
        clean_price = int(price)
        
        new_data.append({
            "code": code.strip(),
            "name": clean_name,
            "price": clean_price
        })
        
        response_lines.append(f"{code} {clean_name} - ₹{clean_price}")

    # Bulk insert is faster and safer
    if new_data:
        sell_prices_col.insert_many(new_data)

    # 3. Confirmation
    formatted_list = "\n".join(response_lines)
    await msg.answer(
        f"✅ <b>Price list updated successfully!</b>\n"
        f"<i>Added {len(new_data)} countries.</i>\n\n"
        f"<pre>{formatted_list}</pre>", 
        parse_mode="HTML"
    )
    await state.clear()

# --- Callback for Sell Button ---
# ==========================================
# 💸 SELL ACCOUNT LOGIC (REWRITTEN & FIXED)
# ==========================================

# --- 1. Sell Menu ---
@dp.callback_query(F.data == "sell")
async def callback_sell(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    prices = list(sell_prices_col.find({}))
    
    if not prices:
        return await cq.message.answer("❌ <b>Sales are currently closed.</b>\nNo price list available.")

    # High UI Price List
    price_list_text = ""
    for p in prices:
        price_list_text += f"🏳️ <code>{p['code']}</code> <b>{p['name']}</b> ➜ ₹{p['price']}\n"

    text = (
        "<b>💸 SELL YOUR TELEGRAM ACCOUNT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>📊 Current Buying Rates:</b>\n"
        f"<blockquote expandable>{price_list_text}</blockquote>\n"
        "<b>📝 Instructions:</b>\n"
        "1. Enter your number with country code.\n"
        "2. Send the OTP received.\n"
        "3. If you have a 2FA password, enter it.\n\n"
        "👇 <b>Send your number now:</b>\n"
        "<i>(Example: +14151234567)</i>"
    )

    await cq.message.answer(text, parse_mode="HTML")
    await state.set_state(SellSession.waiting_sell_number)
    
    # --- 2. User Sends Number ---
@dp.message(StateFilter(SellSession.waiting_sell_number))
async def user_sells_number(msg: Message, state: FSMContext):
    phone = msg.text.strip().replace(" ", "")
    
    if not phone.startswith("+") or not phone[1:].isdigit():
        return await msg.answer("❌ <b>Invalid Format!</b>\nPlease start with '+' followed by digits.\n<i>Ex: +14155550199</i>")

    # Match Country and Price
    all_prices = list(sell_prices_col.find({}))
    matched = None
    for p in all_prices:
        if phone.startswith(p["code"]):
            matched = p
            break

    if not matched:
        return await msg.answer("⚠️ <b>Sorry!</b>\nWe are not buying numbers from this country at the moment.")

    country_name = matched["name"]
    price = matched["price"]

    status_msg = await msg.answer(
        f"🌍 <b>Country:</b> {country_name}\n"
        f"💰 <b>Offer Price:</b> ₹{price}\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🔄 <i>Connecting to Telegram Servers...</i>"
    )

    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")

    session = StringSession()
    client = TelegramClient(session, api_id, api_hash)
    
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
        
        # Save session immediately to maintain context
        await state.update_data(
            session=session.save(), # Critical for session continuity
            phone=phone,
            phone_code_hash=sent.phone_code_hash,
            price=price,
            country_name=country_name,
            password_needed=False # Default false
        )
        
        await client.disconnect()
        
        await status_msg.edit_text(
            f"🌍 <b>Country:</b> {country_name}\n"
            f"💰 <b>Offer Price:</b> ₹{price}\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📩 <b>OTP Sent!</b>\n\n"
            "Please check your Telegram Service notifications or SMS and enter the code below:\n"
            "<i>(Format: 12345)</i>"
        )
        await state.set_state(SellSession.waiting_sell_otp)

    except Exception as e:
        await client.disconnect()
        await status_msg.edit_text(f"❌ <b>Connection Failed:</b>\n<code>{str(e)}</code>")


# --- 3. User Sends OTP ---
@dp.message(StateFilter(SellSession.waiting_sell_otp))
async def user_sells_otp(msg: Message, state: FSMContext):
    otp_code = msg.text.strip()
    
    # Basic validation
    if not otp_code.isdigit():
        return await msg.answer("❌ <b>Invalid OTP.</b> Send numbers only.")

    data = await state.get_data()
    phone = data["phone"]
    session_str = data["session"]
    phone_code_hash = data["phone_code_hash"]

    status_msg = await msg.answer("🔄 <i>Verifying Code...</i>")

    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    client = TelegramClient(StringSession(session_str), api_id, api_hash)

    try:
        await client.connect()
        
        try:
            # Try logging in
            await client.sign_in(phone=phone, code=otp_code, phone_code_hash=phone_code_hash)
            
            # --- SCENARIO A: Login Successful (No 2FA) ---
            final_string = client.session.save() # CAPTURE FINAL STRING
            await client.disconnect()
            
            await state.update_data(string_session=final_string, password=None)
            
            # Skip password step, go directly to finalize logic
            await finalize_sell(msg, state, phone, final_string, None)
            
        except SessionPasswordNeededError:
            # --- SCENARIO B: 2FA Required ---
            await client.disconnect()
            await state.update_data(password_needed=True)
            await status_msg.delete()
            await msg.answer(
                "🔐 <b>Two-Step Verification Detected</b>\n\n"
                "Please enter your <b>Password</b> to complete the login.\n"
                "<i>We need this to verify the account.</i>"
            )
            await state.set_state(SellSession.waiting_sell_password)

    except PhoneCodeInvalidError:
        await client.disconnect()
        await status_msg.edit_text("❌ <b>Wrong OTP!</b>\nPlease check and send again.")
    except Exception as e:
        await client.disconnect()
        await status_msg.edit_text(f"❌ <b>Error:</b> {e}")
        
        
# --- 4. User Sends Password (If 2FA) ---
@dp.message(StateFilter(SellSession.waiting_sell_password))
async def user_sell_password(msg: Message, state: FSMContext):
    password = msg.text.strip()
    data = await state.get_data()
    
    phone = data["phone"]
    session_str = data["session"] # Use the initial session to resume
    
    status_msg = await msg.answer("🔄 <i>Verifying Password...</i>")

    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    client = TelegramClient(StringSession(session_str), api_id, api_hash)

    try:
        await client.connect()
        await client.sign_in(password=password)
        
        # --- Login Successful (With 2FA) ---
        final_string = client.session.save() # CAPTURE FINAL STRING
        await client.disconnect()
        
        # Proceed to finalize
        await status_msg.delete()
        await finalize_sell(msg, state, phone, final_string, password)

    except PasswordHashInvalidError:
        await client.disconnect()
        await status_msg.edit_text("❌ <b>Wrong Password!</b>\nPlease try again.")
    except Exception as e:
        await client.disconnect()
        await status_msg.edit_text(f"❌ <b>Error:</b> {e}")


# --- 5. Finalize Sell (Save to DB & Notify Admin) ---
async def finalize_sell(msg: Message, state: FSMContext, phone, string_session, password):
    data = await state.get_data()
    country_name = data["country_name"]
    price = data["price"]
    user_id = msg.from_user.id
    username = msg.from_user.username

    # 1. Update Database
    numbers_col.update_one(
        {"number": phone},
        {
            "$set": {
                "country": country_name,
                "number": phone,
                "string_session": string_session, # The valid authenticated session
                "password": password if password else "None",
                "used": False,
                "added_by": user_id,
                "added_at": datetime.now(timezone.utc)
            }
        },
        upsert=True
    )

    # 2. Notify Admin
    # Using the specific Admin ID provided in your prompt
    ADMIN_CHAT_ID = -1003723243833 

    kb = InlineKeyboardBuilder()
    # Unique callback for selling OTPs
    kb.button(style="primary", text="📩 Get OTP (Sell)", callback_data=f"get_sell_otp:{phone}")
    kb.button(style="primary", text=f"✅ Approve ₹{price}", callback_data=f"approve_sell:{user_id}:{phone}:{price}")
    kb.button(style="primary", text=f"Reject", callback_data=f"reject_sell:{user_id}:{phone}")
    
    kb.adjust(1)

    admin_text = (
        f"<b>📤 NEW ACCOUNT FOR SALE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Seller:</b> @{username or 'N/A'} (<code>{user_id}</code>)\n"
        f"🌍 <b>Country:</b> {country_name}\n"
        f"📞 <b>Number:</b> <code>{phone}</code>\n"
        f"💰 <b>Payout:</b> ₹{price}\n"
        f"🔐 <b>2FA Pass:</b> <code>{password if password else 'None'}</code>\n\n"
        f"🔑 <b>Session String:</b>\n"
        f"<blockquote expandable><code>{string_session}</code></blockquote>"
    )

    try:
        await bot.send_message(
            ADMIN_CHAT_ID,
            admin_text,
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Failed to send to admin: {e}")

    # 3. Notify User
    await msg.answer(
        f"✅ <b>Submission Successful!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📞 <b>Number:</b> {phone}\n"
        f"💰 <b>Pending:</b> ₹{price}\n\n"
        f"<i>Your account is under review. Balance will be credited after admin verification (usually 1-10 mins).</i>"
    )
    
    await state.clear()
    
    # ==========================================
# 📩 DEDICATED SELL OTP LISTENER (FIXED)
# ==========================================

@dp.callback_query(F.data.startswith("get_sell_otp:"))
async def callback_get_sell_otp(cq: CallbackQuery):
    phone = cq.data.split(":")[1]
    
    # 1. Fetch Session from DB
    number_doc = numbers_col.find_one({"number": phone})
    if not number_doc or not number_doc.get("string_session"):
        return await cq.answer("❌ Session not found in Database.", show_alert=True)

    await cq.answer("🔄 Accessing Account...", show_alert=False)
    
    # High UI status message
    status_msg = await cq.message.answer(f"🔍 <b>Searching for OTP on {phone}...</b>")

    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    string_session = number_doc.get("string_session")
    password_text = number_doc.get("password") or "None"

    client = TelegramClient(StringSession(string_session), api_id, api_hash)
    
    try:
        await client.connect()
        
        if not await client.is_user_authorized():
            await client.disconnect()
            await status_msg.edit_text(f"❌ <b>Session Expired</b>\nAccount {phone} has been logged out.")
            return

        # Use the logic from your working otp_listener
        # Matches any 5-digit number in the message
        pattern = re.compile(r"\b\d{5}\b")
        found_code = None

        # Iterate messages from Telegram Service (777000)
        # Increased limit slightly to ensure we don't miss it
        async for msg in client.iter_messages(777000, limit=15):
            if not msg.message:
                continue

            match = pattern.search(msg.message)
            if match:
                found_code = match.group(0)
                # We stop at the very first (newest) 5-digit code found
                break 
        
        await client.disconnect()

        if found_code:
            # High UI Result Format
            response_text = (
                f"<b>✅ OTP RECEIVED</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Code -</b> <code>{found_code}</code>\n"
                f"<b>Number -</b> <code>{phone}</code>\n"
                f"<b>Pass -</b> <code>{password_text}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━"
            )
            
            await status_msg.delete()
            await bot.send_message(
                chat_id=cq.message.chat.id,
                text=response_text,
                parse_mode="HTML"
            )
        else:
            await status_msg.edit_text(
                f"⚠️ <b>OTP Not Found</b>\n"
                f"No 5-digit code found in the last 15 messages from Telegram on {phone}.\n\n"
                f"<i>Try clicking the button again in a few seconds.</i>"
            )

    except Exception as e:
        if client.is_connected():
            await client.disconnect()
        await status_msg.edit_text(f"❌ <b>Error:</b>\n<code>{str(e)}</code>")


# --- Admin: Get OTP Button ---
@dp.callback_query(F.data.startswith("get_otp:"))
async def callback_get_otp(cq: CallbackQuery):
    phone = cq.data.split(":")[1]

    number_doc = numbers_col.find_one({"number": phone})
    if not number_doc:
        return await cq.answer("❌ Number session not found.", show_alert=True)

    await cq.answer("Waiting for OTP.....")

    # 👇 pass message_id of SAME message
    asyncio.create_task(
        otp_listener(
            number_doc=number_doc,
            user_id=cq.from_user.id,
            message_id=cq.message.message_id
        )
    )
        # --- 2. Admin: Approve Sell ---
@dp.callback_query(F.data.startswith("approve_sell:"))
async def callback_approve_sell(cq: CallbackQuery):
    if not is_admin(cq.from_user.id):
        return await cq.answer("❌ Not authorized.", show_alert=True)

    _, user_id, phone, price = cq.data.split(":")
    user_id, price = int(user_id), int(price)

    # Add Balance
    users_col.update_one({"_id": user_id}, {"$inc": {"balance": price}})

    # Edit Admin Message
    await cq.message.edit_text(
        cq.message.text + f"\n\n✅ <b>Approved by {cq.from_user.first_name}</b>",
        parse_mode="HTML"
    )

    # Notify User with Withdraw Button
    kb = InlineKeyboardBuilder()
    kb.button(style="primary", text="💸 Withdraw Now", callback_data="init_withdraw")
    
    await bot.send_message(
        user_id,
        f"🎉 <b>Account Approved!</b>\n\n"
        f"✅ Account: <code>{phone}</code>\n"
        f"💰 Added: ₹{price}\n\n"
        f"You can withdraw this amount to your UPI immediately.",
        reply_markup=kb.as_markup(),
        parse_mode="HTML"
    )
    await cq.answer("✅ Approved & Balance Added.")

# --- 3. Admin: Reject Sell ---
@dp.callback_query(F.data.startswith("reject_sell:"))
async def callback_reject_sell(cq: CallbackQuery):
    if not is_admin(cq.from_user.id):
        return await cq.answer("❌ Not authorized.")

    _, user_id, phone = cq.data.split(":")
    user_id = int(user_id)

    await cq.message.edit_text(
        cq.message.text + f"\n\n❌ <b>Rejected by {cq.from_user.first_name}</b>",
        parse_mode="HTML"
    )
    
    await bot.send_message(
        user_id,
        f"⚠️ <b>Account Rejected</b>\n\nYour submission for <code>{phone}</code> was declined by the admin.",
        parse_mode="HTML"
    )
    await cq.answer("❌ Request Rejected.")
    
    
    # --- User clicks Withdraw ---
@dp.callback_query(F.data == "init_withdraw")
async def start_withdraw(cq: CallbackQuery, state: FSMContext):
    user_bal = get_user_balance(cq.from_user.id)
    if user_bal < 1:
        return await cq.answer("❌ Balance too low.", show_alert=True)

    await cq.message.answer(
        "🏦 <b>Withdrawal Setup</b>\n\nPlease enter your <b>UPI ID</b> (e.g., user@oksbi):",
        parse_mode="HTML"
    )
    await state.set_state(WithdrawState.waiting_upi)
    await cq.answer()

# --- User enters UPI ---
@dp.message(StateFilter(WithdrawState.waiting_upi))
async def process_withdraw_upi(msg: Message, state: FSMContext):
    upi_id = msg.text.strip()
    await state.update_data(upi_id=upi_id)
    
    user_bal = get_user_balance(msg.from_user.id)
    
    await msg.answer(
        f"✅ UPI set to: <code>{upi_id}</code>\n\n"
        f"💰 Your Balance: ₹{user_bal}\n"
        f"Enter the amount you want to withdraw:",
        parse_mode="HTML"
    )
    await state.set_state(WithdrawState.waiting_amount)

# --- User enters Amount ---
@dp.message(StateFilter(WithdrawState.waiting_amount))
async def process_withdraw_amount(msg: Message, state: FSMContext):
    try:
        amount = int(msg.text.strip())
    except ValueError:
        return await msg.answer("❌ Please enter a valid number.")

    data = await state.get_data()
    upi_id = data.get('upi_id')
    user_id = msg.from_user.id
    current_bal = get_user_balance(user_id)

    # Validation
    if amount > current_bal:
        return await msg.answer(f"❌ Insufficient funds. Your balance is ₹{current_bal}.")
    if amount < 1:
        return await msg.answer("❌ Minimum withdrawal is ₹1.")

    # Deduct Balance
    users_col.update_one({"_id": user_id}, {"$inc": {"balance": -amount}})
    
    # Save Request to DB
    withdraw_doc = {
        "user_id": user_id,
        "username": msg.from_user.username,
        "amount": amount,
        "upi": upi_id,
        "status": "pending"
    }
    result = withdrawals_col.insert_one(withdraw_doc)
    request_id = str(result.inserted_id)

    # Notify User
    await msg.answer(
        f"✅ <b>Withdrawal Request Submitted!</b>\n"
        f"💸 Amount: ₹{amount}\n"
        f"🏦 UPI: <code>{upi_id}</code>\n\n"
        f"You will receive the funds shortly.",
        parse_mode="HTML"
    )
    await state.clear()

    # Notify Admin Group
    kb = InlineKeyboardBuilder()
    kb.button(style="primary", text="✅ Approve Payment", callback_data=f"pay_wd:{request_id}")
    kb.adjust(1)

    admin_text = (
        f"<b>💸 New Withdrawal Request</b>\n\n"
        f"👤 User: {msg.from_user.full_name} (<code>{user_id}</code>)\n"
        f"💰 Amount: <b>₹{amount}</b>\n"
        f"🏦 UPI: <code>{upi_id}</code>\n"
        f"🆔 Req ID: <code>{request_id}</code>"
    )

    await bot.send_message(
        "-1003723243833", 
        admin_text, 
        reply_markup=kb.as_markup(), 
        parse_mode="HTML"
    )
    
# --- Admin clicks Approve Payment ---
@dp.callback_query(F.data.startswith("pay_wd:"))
async def admin_approve_withdraw(cq: CallbackQuery, state: FSMContext):
    req_id = cq.data.split(":")[1]
    
    # Store request details in FSM to use after getting TXN ID
    await state.update_data(req_id=req_id, message_id=cq.message.message_id, chat_id=cq.message.chat.id)
    
    await cq.message.answer(
        "✍️ <b>Send the Transaction ID (UTR) for this payment:</b>\n"
        "Or type /skip if you don't want to provide one.",
        parse_mode="HTML"
    )
    await state.set_state(AdminTxnState.waiting_txn)
    await cq.answer()

# --- Admin sends TXN ID ---
@dp.message(StateFilter(AdminTxnState.waiting_txn))
async def admin_finalize_withdraw(msg: Message, state: FSMContext):
    txn_id = msg.text.strip()
    data = await state.get_data()
    req_id = data.get('req_id')
    admin_msg_id = data.get('message_id')
    admin_chat_id = data.get('chat_id')

    # Get Request Details
    req_doc = withdrawals_col.find_one({"_id": ObjectId(req_id)})
    if not req_doc:
        await msg.answer("❌ Error: Request not found in DB.")
        return await state.clear()

    # Update DB Status
    withdrawals_col.update_one({"_id": ObjectId(req_id)}, {"$set": {"status": "paid", "txn": txn_id}})

    # 1. Notify User
    user_msg = (
        f"🎉 <b>Withdrawal Approved!</b>\n\n"
        f"💰 Amount: ₹{req_doc['amount']}\n"
        f"🏦 UPI: <code>{req_doc['upi']}</code>\n"
    )
    if txn_id != "/skip":
        user_msg += f"🆔 TXN ID: <code>{txn_id}</code>"
    
    try:
        await bot.send_message(req_doc['user_id'], user_msg, parse_mode="HTML")
    except:
        pass # User might have blocked bot

    # 2. Update Admin Group Message (Strikethrough)
    original_text = (
        f"<b>💸 New Withdrawal Request</b>\n\n"
        f"👤 User: {req_doc['username']} (<code>{req_doc['user_id']}</code>)\n"
        f"💰 Amount: <b>₹{req_doc['amount']}</b>\n"
        f"🏦 UPI: <code>{req_doc['upi']}</code>\n"
        f"🆔 Req ID: <code>{req_id}</code>"
    )

    strikethrough_text = f"<s>{original_text}</s>\n\n✅ <b>PAID by {msg.from_user.first_name}</b>"
    if txn_id != "/skip":
        strikethrough_text += f"\n🆔 Ref: {txn_id}"

    try:
        await bot.edit_message_text(
            chat_id=admin_chat_id,
            message_id=admin_msg_id,
            text=strikethrough_text,
            parse_mode="HTML"
        )
    except Exception as e:
        await msg.answer(f"⚠️ Could not edit original message: {e}")

    await msg.answer("✅ Withdrawal marked as paid.")
    await state.clear()


    

#============== Admin: Edit Country =================
class EditCountry(StatesGroup):
    waiting_new_name = State()
    waiting_new_price = State()

@dp.message(Command("editcountry"))
async def cmd_edit_country(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.answer("❌ Not authorized.")
    countries = list(countries_col.find({}))
    if not countries:
        return await msg.answer("📭 No countries to edit.")
    kb = InlineKeyboardBuilder()
    for c in countries:
        kb.button(style="primary", text=c["name"], callback_data=f"editcountry:{c['name']}")
    kb.adjust(2)
    await msg.answer("🌍 Select a country to edit:", reply_markup=kb.as_markup())

@dp.callback_query(F.data.startswith("editcountry:"))
async def callback_edit_country(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    _, country_name = cq.data.split(":", 1)
    country = countries_col.find_one({"name": country_name})
    if not country:
        return await cq.message.edit_text(f"❌ Country {country_name} not found.")

    await state.update_data(country_name=country_name)

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(style="primary", text="✏️ Change Name", callback_data="editcountry_change_name"),
        InlineKeyboardButton(style="primary", text="💰 Change Price", callback_data="editcountry_change_price")
    )
    kb.row(InlineKeyboardButton(style="primary", text="❌ Cancel", callback_data="editcountry_cancel"))
    await cq.message.edit_text(
        f"🛠️ Editing Country: <b>{country_name}</b>\n"
        f"💸 Current Price: ₹{country['price']}",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )

@dp.callback_query(F.data == "editcountry_change_name")
async def callback_edit_change_name(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    data = await state.get_data()
    country_name = data.get("country_name")
    await cq.message.answer(f"✏️ Send new name for <b>{country_name}</b>:", parse_mode="HTML")
    await state.set_state(EditCountry.waiting_new_name)

@dp.message(StateFilter(EditCountry.waiting_new_name))
async def handle_new_country_name(msg: Message, state: FSMContext):
    data = await state.get_data()
    old_name = data.get("country_name")
    new_name = msg.text.strip()

    countries_col.update_one({"name": old_name}, {"$set": {"name": new_name}})
    numbers_col.update_many({"country": old_name}, {"$set": {"country": new_name}})
    await msg.answer(f"✅ Country name changed from <b>{old_name}</b> → <b>{new_name}</b>", parse_mode="HTML")
    await state.clear()

@dp.callback_query(F.data == "editcountry_change_price")
async def callback_edit_change_price(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    data = await state.get_data()
    country_name = data.get("country_name")
    await cq.message.answer(f"💰 Send new price for <b>{country_name}</b>:", parse_mode="HTML")
    await state.set_state(EditCountry.waiting_new_price)

@dp.message(StateFilter(EditCountry.waiting_new_price))
async def handle_new_country_price(msg: Message, state: FSMContext):
    data = await state.get_data()
    country_name = data.get("country_name")
    try:
        price = float(msg.text.strip())
        if price <= 0:
            raise ValueError
    except ValueError:
        return await msg.answer("❌ Invalid price format. Please send a valid number.")

    countries_col.update_one({"name": country_name}, {"$set": {"price": price}})
    await msg.answer(f"✅ Updated price for <b>{country_name}</b> to ₹{price:.2f}", parse_mode="HTML")
    await state.clear()

@dp.callback_query(F.data == "editcountry_cancel")
async def callback_edit_cancel(cq: CallbackQuery, state: FSMContext):
    await cq.answer("❌ Cancelled")
    await state.clear()
    await cq.message.edit_text("❌ Edit cancelled.")


@dp.callback_query(F.data == "stats")
async def callback_howto(cq: CallbackQuery):
    user = users_col.find_one({"_id": cq.from_user.id})
    if not user:
        user = get_or_create_user(cq.from_user.id, cq.from_user.username)
    steps_text = (
        f"<b>◍ cheap codes Bot</b>\n––––––——–––————––––——–––•\n"
        f"<blockquote><b>👤 Name: </b>{cq.from_user.full_name}\n"
        f"<b>💻 Username: </b>@{cq.from_user.username if cq.from_user.username else 'N/A'}\n"
        f"<b>🆔 User ID:</b> {cq.from_user.id}\n"
        f"<b>💰 Balance:</b> ₹{user.get('balance', 0.0):.2f}</blockquote>\n"
        f"––––––——–––————––––——–––•\n •<b> Bot</b>: @{BOTUSER}\n• <b>Sales Log</b>: @{SALESLOG}"
        
    )

    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(style="primary", text="▪️ Support", url=f"https://t.me/{OWNER}"),
        InlineKeyboardButton(style="primary", text="▪️ 𝙃𝙤𝙬 𝙩𝙤 𝙪𝙨𝙚", url=f"https://t.me/valriks")
    )
    kb.row(
        InlineKeyboardButton(style="primary", text="▪️ Previous", callback_data="back_main")
    )
    
    await cq.message.edit_text(steps_text, parse_mode="HTML", reply_markup=kb.as_markup())
    await cq.answer()

@dp.callback_query(F.data == "howto")
async def callback_howto(cq: CallbackQuery):
    await cq.answer() # Answer first
    steps_text = ("📚 FᴀQ & Sᴜᴘᴘᴏʀᴛ 😊\n\n🔗 𝙃𝙤𝙬 𝙩𝙤 𝙪𝙨𝙚:👉 {USAGE}\n💬 Oғғɪᴄɪᴀʟ Sᴜᴘᴘᴏʀᴛ:   👉 {SUPPORT}\n🤖 Oғғɪᴄɪᴀʟ Bᴏᴛ:     👉 {BOT_USER}\n\n🛟 Fᴇᴇʟ Fʀᴇᴇ Tᴏ Rᴇᴀᴄʜ Oᴜ𝙩 Iғ Yᴏᴜ Nᴇᴇᴅ Aɴʏ Hᴇʟᴘ!")
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(style="primary", text="📲 Support", url=f"https://t.me/{SUPPORT}"),
        InlineKeyboardButton(style="primary", text="🔗 𝙃𝙤𝙬 𝙩𝙤 𝙪𝙨𝙚", url=f"https://t.me/valriks")
    )
    # Added back button
    kb.row(InlineKeyboardButton(style="primary", text="🔙 Main Menu", callback_data="main_menu")) 
    
    await cq.message.edit_text(steps_text, parse_mode="HTML", reply_markup=kb.as_markup())


@dp.callback_query(lambda c: c.data == "refer")
async def callback_refer(cq: CallbackQuery):
    user = get_or_create_user(cq.from_user.id, cq.from_user.username)
    bot_username = (await bot.get_me()).username
    refer_link = f"https://t.me/{bot_username}?start=ref{cq.from_user.id}"
    valid = int(user.get("valid_referrals", 0))
    earned = float(user.get("referral_earnings", 0.0))
    text = (
        "👥 <b>Refer & Earn</b>\n\n"
        "Invite a <b>new user</b> and earn <b>₹0.50</b> after they join all required channels and verification succeeds.\n\n"
        f"👤 <b>Valid Referrals:</b> {valid}\n"
        f"💰 <b>Referral Earnings:</b> ₹{earned:.2f}\n\n"
        f"🔗 <b>Your Referral Link:</b>\n<code>{refer_link}</code>\n\n"
        "🏆 Every 24 hours, the referrer whose valid referrals generated the highest recharge total in the previous 24-hour period receives an extra <b>₹10</b>."
    )
    kb=InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(style="primary", text="📤 Share Link", url=f"https://t.me/share/url?url={urllib.parse.quote(refer_link)}&text={urllib.parse.quote('Join and earn with this bot!')}"))
    kb.row(InlineKeyboardButton(style="primary", text="▪️ Back", callback_data="back_main"))
    await cq.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())
    await cq.answer()

@dp.message(Command("sales"))
async def cmd_sales(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.answer("❌ You are not authorized to view sales report.")

    now = datetime.utcnow()
    start_of_week = now - timedelta(days=now.weekday())
    start_of_day = datetime(now.year, now.month, now.day)
    # Collections assumed
    users_col = db["users"]
    orders_col = db["orders"]
    recharges_col = db["recharges"]  # If you track top-ups

    # Bot Status
    bot_status = "🟢 Active"

    # Total users
    total_users = users_col.count_documents({})

    # All sales
    all_orders = list(orders_col.find({"status": "purchased"}))
    total_numbers_sold = len(all_orders)
    total_earnings = sum(order.get("price", 0) for order in all_orders)
    avg_price = total_earnings / total_numbers_sold if total_numbers_sold else 0

    # Top Country overall
    from collections import Counter
    country_counts = Counter(order.get("country", "Unknown") for order in all_orders)
    top_country = country_counts.most_common(1)[0][0] if country_counts else "N/A"

    # Total Recharge
    total_recharge = sum(txn.get("amount", 0) for txn in recharges_col.find({}))

    # ================= WEEKLY =================
    week_orders = list(orders_col.find({
        "status": "purchased",
        "date": {"$gte": start_of_week}
    }))
    week_sales = sum(o.get("price", 0) for o in week_orders)
    week_count = len(week_orders)
    week_avg = week_sales / week_count if week_count else 0
    week_country_counts = Counter(o.get("country", "Unknown") for o in week_orders)
    week_top_country = week_country_counts.most_common(1)[0][0] if week_country_counts else "N/A"
    week_recharge = sum(txn.get("amount", 0) for txn in recharges_col.find({"date": {"$gte": start_of_week}}))

    # ================= DAILY =================
    day_orders = list(orders_col.find({
        "status": "purchased",
        "date": {"$gte": start_of_day}
    }))
    day_sales = sum(o.get("price", 0) for o in day_orders)
    day_count = len(day_orders)
    day_avg = day_sales / day_count if day_count else 0
    day_country_counts = Counter(o.get("country", "Unknown") for o in day_orders)
    day_top_country = day_country_counts.most_common(1)[0][0] if day_country_counts else "N/A"
    day_recharge = sum(txn.get("amount", 0) for txn in recharges_col.find({"date": {"$gte": start_of_day}}))

    # ================= REPORT =================
    report = (
        "📊 <b>Bot Profit Report</b>\n"
        f"<b>⚙️ Bot Status: </b>{bot_status}\n\n"
        f"<b>👥 Total Users: </b>{total_users}\n"
        f"<b>🔢 Total Numbers Sold: </b>{total_numbers_sold}\n"
        f"💰 Total Sales: ₹{total_earnings:.2f}\n"
        f"⚖️ Avg Price/Number: ₹{avg_price:.2f}\n"
        f"🌍 Top Country: {top_country}\n"
        f"💳 Total Recharge: ₹{total_recharge:.2f}\n\n"
        f"@quickcodes_bot •|• @valriks"
    )

    await msg.answer(report, parse_mode="HTML")

@dp.message(Command("sellcountry"))
async def add_sell_countries(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        return await msg.answer("Unauthorized ❌")

    # Remove the command itself and split lines
    lines = msg.text.split("\n")[1:]  # Skip the first line (the command)
    if not lines:
        return await msg.answer(
            "📋 Send like this:\n\n"
            "<code>/sellcountry\n+91 India ₹30\n+1 USA ₹32\n+62 Indonesia ₹28</code>",
            parse_mode="HTML"
        )

    updated = []
    errors = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        try:
            parts = line.split(" ")
            prefix = parts[0]
            if not prefix.startswith("+"):
                raise ValueError("Missing +country code")

            # Extract price (₹XX)
            match_price = [p for p in parts if "₹" in p]
            if not match_price:
                raise ValueError("Missing price (₹)")
            price = match_price[-1]  # Take last ₹ value
            country = " ".join(parts[1:parts.index(price)]).strip()

            db["sell_countries"].update_one(
                {"prefix": prefix},
                {"$set": {"country": country, "price": price}},
                upsert=True
            )

            updated.append(f"{prefix} {country} → {price}")
        except Exception as e:
            errors.append(f"❌ {line} ({e})")

    text = ""
    if updated:
        text += "✅ <b>Updated Successfully:</b>\n" + "\n".join(updated) + "\n\n"
    if errors:
        text += "⚠️ <b>Errors:</b>\n" + "\n".join(errors)

    await msg.answer(text or "⚙️ Nothing processed.", parse_mode="HTML")


# ================= Admin Credit/Debit Commands =================
@dp.message(Command("credit"))
async def cmd_credit(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.answer("❌ Not authorized.")
    
    await msg.answer("💰 Send user ID and amount to credit separated by a comma (e.g., 123456789,50):")
    await state.set_state("credit_waiting")

@dp.message(StateFilter("credit_waiting"))
async def handle_credit(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return

    if "," not in msg.text:
        return await msg.answer("❌ Invalid format. Example: 123456789,50")

    user_id_str, amount_str = msg.text.split(",", 1)
    try:
        user_id = int(user_id_str.strip())
        amount = float(amount_str.strip())
    except ValueError:
        return await msg.answer("❌ Invalid user ID or amount format.")

    user = users_col.find_one({"_id": user_id})
    if not user:
        return await msg.answer(f"❌ User with ID {user_id} not found.")

    new_balance = user.get("balance", 0.0) + amount
    users_col.update_one({"_id": user_id}, {"$set": {"balance": new_balance}})
    await msg.answer(f"✅ Credited ₹{amount:.2f} to {user.get('username') or user_id}\n💰 New Balance: ₹{new_balance:.2f}")
    await state.clear()


@dp.message(Command("debit"))
async def cmd_debit(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.answer("❌ Not authorized.")
    
    await msg.answer("💸 Send user ID and amount to debit separated by a comma (e.g., 123456789,50):")
    await state.set_state("debit_waiting")

@dp.message(StateFilter("debit_waiting"))
async def handle_debit(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return

    if "," not in msg.text:
        return await msg.answer("❌ Invalid format. Example: 123456789,50")

    user_id_str, amount_str = msg.text.split(",", 1)
    try:
        user_id = int(user_id_str.strip())
        amount = float(amount_str.strip())
    except ValueError:
        return await msg.answer("❌ Invalid user ID or amount format.")

    user = users_col.find_one({"_id": user_id})
    if not user:
        return await msg.answer(f"❌ User with ID {user_id} not found.")

    new_balance = max(user.get("balance", 0.0) - amount, 0.0)
    users_col.update_one({"_id": user_id}, {"$set": {"balance": new_balance}})
    await msg.answer(f"✅ Debited ₹{amount:.2f} from {user.get('username') or user_id}\n💰 New Balance: ₹{new_balance:.2f}")
    await state.clear()





    # ================= MongoDB Redeem Collection =================
redeem_col = db["redeem_codes"]  # Add this at top with other collections

# ================= Redeem FSM =================
class RedeemState(StatesGroup):
    # For auto-generated redeem codes
    waiting_amount = State()          # Admin enters amount
    waiting_limit = State()           # Admin selects max users via inline numeric keypad

    # For custom redeem codes
    waiting_code = State()            # Admin enters custom code (e.g. DIWALI100)
    waiting_amount_custom = State()   # Admin enters amount for custom code
    waiting_limit_custom = State()    # Admin selects max users for custom code

class UserRedeemState(StatesGroup):
    waiting_code = State()            # User enters redeem code
    
# ================= Helper =================
import random, string
def generate_code(length=8):
    """Generate code like HEIKE938"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))



    
        # ================= Admin: Create Custom Redeem =================
@dp.message(Command("cusredeem"))
async def cmd_custom_redeem(msg: Message, state: FSMContext):
    if not is_admin(msg.from_user.id):
        return await msg.answer("❌ Not authorized.")
    await msg.answer("🎟️ Enter the custom redeem code (e.g. DIWALI100):")
    await state.set_state(RedeemState.waiting_code)

# ================= Admin: Handle Custom Code =================
@dp.message(StateFilter(RedeemState.waiting_code))
async def handle_custom_code(msg: Message, state: FSMContext):
    code = msg.text.strip().upper()
    if redeem_col.find_one({"code": code}):
        return await msg.answer("⚠️ This code already exists. Try another one.")

    await state.update_data(custom_code=code)
    await msg.answer("💰 Enter the amount for this redeem code:")
    await state.set_state(RedeemState.waiting_amount_custom)

# ================= Admin: Handle Custom Amount =================
@dp.message(StateFilter(RedeemState.waiting_amount_custom))
async def handle_custom_amount(msg: Message, state: FSMContext):
    try:
        amount = float(msg.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        return await msg.answer("❌ Invalid amount. Send a number like 50 or 100.")

    await state.update_data(amount=amount, limit_str="")

    # Inline numeric keypad
    kb = InlineKeyboardBuilder()
    for row in (("1", "2", "3"), ("4", "5", "6"), ("7", "8", "9"), ("0", "❌", "✅")):
        kb.row(*[InlineKeyboardButton(style="primary", text=btn, callback_data=f"cusredeemnum:{btn}") for btn in row])

    await msg.answer(
        "👥 Select max number of users who can claim this custom code:\n<b>0</b>",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )
    await state.set_state(RedeemState.waiting_limit_custom)

# ================= Admin: Handle Custom Inline Number Pad =================
@dp.callback_query(F.data.startswith("cusredeemnum:"))
async def handle_custom_redeem_number(cq: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current = data.get("limit_str", "")
    value = cq.data.split(":")[1]

    if value == "❌":
        current = current[:-1]
    elif value == "✅":
        if not current:
            await cq.answer("❌ Please select at least one user.", show_alert=True)
            return
        try:
            limit = int(current)
        except ValueError:
            await cq.answer("❌ Invalid number.", show_alert=True)
            return

        code = data.get("custom_code")
        amount = data.get("amount")
        created_at = datetime.utcnow()

        # Insert redeem into MongoDB
        redeem_col.insert_one({
            "code": code,
            "amount": amount,
            "max_claims": limit,
            "claimed_count": 0,
            "claimed_users": [],
            "created_at": created_at
        })

        await cq.message.edit_text(
            f"✅ Custom redeem code created!\n\n"
            f"🎟️ Code: <code>{code}</code>\n"
            f"💰 Amount: ₹{amount:.2f}\n"
            f"👥 Max Claims: {limit}",
            parse_mode="HTML"
        )
        await state.clear()
        return
    else:
        current += value
        if len(current) > 6:
            current = current[:6]

    await state.update_data(limit_str=current)

    # Rebuild keypad dynamically
    kb = InlineKeyboardBuilder()
    for row in (("1", "2", "3"), ("4", "5", "6"), ("7", "8", "9"), ("0", "❌", "✅")):
        kb.row(*[InlineKeyboardButton(style="primary", text=btn, callback_data=f"cusredeemnum:{btn}") for btn in row])

    await cq.message.edit_text(
        f"👥 Select max number of users who can claim this custom code:\n<b>{current or '0'}</b>",
        parse_mode="HTML",
        reply_markup=kb.as_markup()
    )
    await cq.answer()
        


# ================= Admin: View Redeems =================
@dp.message(Command("redeemlist"))
async def cmd_redeem_list(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.answer("❌ Not authorized.")

    redeems = list(redeem_col.find())
    if not redeems:
        return await msg.answer("📭 No redeem codes found.")

    text = "🎟️ <b>Active Redeem Codes:</b>\n\n"
    for r in redeems:
        text += (
            f"Code: <code>{r['code']}</code>\n"
            f"💰 Amount: ₹{r['amount']}\n"
            f"👥 {r['claimed_count']} / {r['max_claims']} claimed\n\n"
        )
    await msg.answer(text, parse_mode="HTML")

# ================= User: Redeem Code =================
@dp.callback_query(F.data == "redeem")
async def callback_user_redeem(cq: CallbackQuery, state: FSMContext):
    await cq.answer("✅ Send your redeem code now!", show_alert=False)
    await cq.message.answer("🎟️ Send your redeem code below:")
    await state.set_state(UserRedeemState.waiting_code)

# Command /redeem
@dp.message(F.text == "/redeem")
async def command_user_redeem(message: Message, state: FSMContext):
    await message.answer("✅ Send your redeem code now!")
    await message.answer("🎟️ Send your redeem code below:")
    await state.set_state(UserRedeemState.waiting_code)

@dp.message(StateFilter(UserRedeemState.waiting_code))
async def handle_user_redeem(msg: Message, state: FSMContext):
    code = msg.text.strip().upper()
    redeem = redeem_col.find_one({"code": code})

    if not redeem:
        await msg.answer("❌ Invalid or expired redeem code.")
        return await state.clear()

    if redeem["claimed_count"] >= redeem["max_claims"]:
        await msg.answer("🚫 This code has reached its claim limit.")
        return await state.clear()

    user = users_col.find_one({"_id": msg.from_user.id})
    if not user:
        await msg.answer("⚠️ Please use /start first.")
        return await state.clear()

    if msg.from_user.id in redeem.get("claimed_users", []):
        await msg.answer("⚠️ You have already claimed this code.")
        return await state.clear()

    # Credit user balance
    users_col.update_one({"_id": msg.from_user.id}, {"$inc": {"balance": redeem["amount"]}})
    redeem_col.update_one(
        {"code": code},
        {"$inc": {"claimed_count": 1}, "$push": {"claimed_users": msg.from_user.id}}
    )

    await msg.answer(
        f"✅ Code <b>{code}</b> redeemed successfully!\n💰 You received ₹{redeem['amount']:.2f}",
        parse_mode="HTML"
    )
    await state.clear()

@dp.message(Command("editsell"))
async def cmd_editsell(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.answer("❌ Not authorized.")

    await msg.answer("📋 Send the list in format:\n\n<code>USA ₹50\nIndia ₹10\nUK ₹20</code>")

    @dp.message()  # Next message from admin
    async def handle_sell_edit(m: Message):
        sell_prices_col.delete_many({})
        for line in m.text.splitlines():
            try:
                parts = line.split("₹")
                country = parts[0].strip()
                price = float(parts[1].strip())
                code = "+1" if "USA" in country else "+91" if "India" in country else ""  # add more or editable
                sell_rates_col.insert_one({"country": country, "price": price, "code": code})
            except:
                continue
        await m.answer("✅ Sell rates updated.")

# ================= Admin Live Credits =================
@dp.message(Command("livecredits"))
async def cmd_livecredits(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    await show_live_credits(msg, page=0)

async def show_live_credits(msg_or_call, page: int):
    limit = 10
    skip = page * limit
    
    # Find users with balance > 0, sort DESC by balance
    cursor = users_col.find({"balance": {"$gt": 0}}).sort("balance", -1)
    total_users = users_col.count_documents({"balance": {"$gt": 0}})
    
    users_list = list(cursor.skip(skip).limit(limit))
    
    if not users_list:
        text = "📉 No users currently have a positive balance."
        kb = None
    else:
        text = f"💰 <b>Live Credits (Page {page+1})</b>\n\n"
        for u in users_list:
            u_link = f"<a href='tg://user?id={u['_id']}'>{u.get('username') or u['_id']}</a>"
            text += f"👤 {u_link} : <code>₹{u['balance']:.2f}</code>\n"
            
        kb = InlineKeyboardBuilder()
        if page > 0:
            kb.button(style="primary", text="⬅️ Prev", callback_data=f"livecredits:{page-1}")
        if (skip + limit) < total_users:
            kb.button(style="primary", text="Next ➡️", callback_data=f"livecredits:{page+1}")
        kb.adjust(2)
        kb.row(InlineKeyboardButton(style="primary", text="❌ Close", callback_data="delete_msg"))

    if isinstance(msg_or_call, Message):
        await msg_or_call.answer(text, parse_mode="HTML", reply_markup=kb.as_markup() if kb else None)
    else:
        await msg_or_call.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup() if kb else None)

@dp.callback_query(F.data.startswith("livecredits:"))
async def pagination_livecredits(cq: CallbackQuery):
    if not is_admin(cq.from_user.id):
        return await cq.answer("❌", show_alert=True)
    page = int(cq.data.split(":")[1])
    await show_live_credits(cq, page)
    await cq.answer()

@dp.callback_query(F.data == "delete_msg")
async def delete_this_msg(cq: CallbackQuery):
    await cq.message.delete()

# ================= User History & Logs =================

@dp.callback_query(F.data == "history")
async def show_user_history(cq: CallbackQuery):
    user_id = cq.from_user.id
    
    # 1. Calculate Total Recharged
    # Note: Assuming 'transactions' collection is used for recharges based on your file
    txns = list(db["transactions"].find({"user_id": user_id, "status": "paid"})) # Or "success" check your recharge_flow
    total_added = sum(t.get("amount", 0) for t in txns)
    
    # 2. Calculate Purchases
    orders = list(orders_col.find({"user_id": user_id, "status": "purchased"}))
    total_purchased = len(orders)
    total_spent = sum(o.get("price", 0) for o in orders)
    
    text = (
        f"📜 <b>User History</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🛍️ <b>Accounts Bought:</b> {total_purchased}\n"
        f"💸 <b>Total Spent:</b> ₹{total_spent:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(style="primary", text="📂 View Purchase Logs", callback_data="purchase_logs:0"))
    kb.row(InlineKeyboardButton(style="primary", text="🔙 Back", callback_data="back_main")) # Back to profile/stats
    
    await cq.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("purchase_logs:"))
async def show_purchase_logs(cq: CallbackQuery):
    user_id = cq.from_user.id
    page = int(cq.data.split(":")[1])
    limit = 10
    skip = page * limit
    
    # Fetch orders sorted by newest first
    cursor = orders_col.find({"user_id": user_id, "status": "purchased"}).sort("_id", -1)
    total_orders = orders_col.count_documents({"user_id": user_id, "status": "purchased"})
    
    my_orders = list(cursor.skip(skip).limit(limit))
    
    if not my_orders:
        return await cq.answer("❌ No purchase history found.", show_alert=True)
    
    text = f"📂 <b>Purchase Logs (Page {page+1})</b>\n\n"
    
    for order in my_orders:
        ph_number = order.get('number')
        
        # Try to find password in numbers_col
        # Note: If you delete numbers from DB after sell, this might return None.
        # But usually, 'used=True' numbers stay in DB.
        num_doc = numbers_col.find_one({"number": ph_number})
        password = num_doc.get("password") if num_doc else "N/A"
        if not password: password = "None"
        
        text += (
            f"📱 <b>{ph_number}</b>\n"
            f"🔐 Pass: <code>{password}</code>\n"
            f"-------------------\n"
        )
        
    kb = InlineKeyboardBuilder()
    
    # Navigation
    nav_btns = []
    if page > 0:
        nav_btns.append(InlineKeyboardButton(style="primary", text="⬅️", callback_data=f"purchase_logs:{page-1}"))
    if (skip + limit) < total_orders:
        nav_btns.append(InlineKeyboardButton(style="primary", text="➡️", callback_data=f"purchase_logs:{page+1}"))
        
    if nav_btns:
        kb.row(*nav_btns)
        
    kb.row(InlineKeyboardButton(style="primary", text="🔙 Back to History", callback_data="history"))
    
    await cq.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())
    
# ================= Admin Server Management =================
async def get_api_balances():
    """Fetches live balance from both TG-Lion and TGPVA"""
    tg_lion_bal = "Error"
    tgpva_bal = "Error"
    
    async with aiohttp.ClientSession() as session:
        # Fetch TG-Lion Balance
        try:
            url1 = f"https://TG-Lion.net?action=get_balance&apiKey={TGLION_API_KEY}&YourID={TGLION_ID}"
            async with session.get(url1, timeout=5) as resp:
                data = await resp.json()
                if data.get("status") == "ok":
                    tg_lion_bal = data.get("balance", "0 USD")
        except Exception:
            pass
            
        # Fetch TGPVA Balance
        try:
            url2 = f"https://tgpva.com/api/user/getBalance?apiKey={TGPVA_API_KEY}"
            async with session.get(url2, timeout=5) as resp:
                data = await resp.json()
                if data.get("ok") and "result" in data:
                    # Balance 'result' ke andar se nikalna hai
                    balance_val = data["result"].get("balance", "0")
                    # Agar currency api me nahi aati to default 'USD' lagayenge
                    currency_val = data.get("currency", data["result"].get("currency", "USD"))
                    tgpva_bal = f"{balance_val} {currency_val}"
        except Exception:
            pass
            
    return tg_lion_bal, tgpva_bal

@dp.message(Command("manageserver"))
async def cmd_manage_server(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.answer("❌ Not authorized.")
    
    status_msg = await msg.answer("🔄 <i>Fetching Live Server Status & API Balances...</i>", parse_mode="HTML")
    await send_server_dashboard(msg.from_user.id, status_msg.message_id)

async def send_server_dashboard(chat_id, message_id=None):
    # Fetch settings from DB (Defaults if not set)
    settings = settings_col.find_one({"_id": "server_config"}) or {}
    s1 = settings.get("s1", "active")
    s2 = settings.get("s2", "active")
    active_api = settings.get("s2_api", "tgpva") # tglion or tgpva
    profit = settings.get("s2_profit", 0)

    # Fetch live balances
    tg_lion_bal, tgpva_bal = await get_api_balances()

    text = (
        "🎛️ <b>Server Management Dashboard</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Server 1:</b> {s1.upper()}\n"
        f"<b>Server 2:</b> {s2.upper()}\n\n"
        f"<b>⚙️ Server 2 Active API:</b> <code>{active_api.upper()}</code>\n"
        f"<b>💰 Server 2 Profit Margin:</b> {profit}%\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "💳 <b>Live API Balances:</b>\n"
        f"🦁 TG-Lion: <code>{tg_lion_bal}</code>\n"
        f"🛡️ TGPVA: <code>{tgpva_bal}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )

    kb = InlineKeyboardBuilder()
    # Toggles for S1, S2 (S3 removed)
    kb.row(
        InlineKeyboardButton(style="primary", text=f"S1: {'🔴 Off' if s1=='active' else '🟢 On'}", callback_data="toggle_s1"),
        InlineKeyboardButton(style="primary", text=f"S2: {'🔴 Off' if s2=='active' else '🟢 On'}", callback_data="toggle_s2")
    )
    # API Switcher & Profit
    kb.row(
        InlineKeyboardButton(style="primary", text="🔄 Switch S2 API", callback_data="switch_s2_api"),
        InlineKeyboardButton(style="primary", text="📈 Set Profit %", callback_data="set_s2_profit")
    )
    kb.row(
        InlineKeyboardButton(style="primary", text="🔄 Refresh Balances", callback_data="refresh_dashboard"),
        InlineKeyboardButton(style="primary", text="❌ Close", callback_data="delete_msg")
    )

    if message_id:
        await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=kb.as_markup())
    else:
        await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb.as_markup())

# Callbacks for toggles
@dp.callback_query(F.data.in_(["toggle_s1", "toggle_s2", "switch_s2_api", "refresh_dashboard"]))
async def manage_toggles(cq: CallbackQuery):
    action = cq.data
    settings = settings_col.find_one({"_id": "server_config"}) or {"_id": "server_config"}
    
    if action == "toggle_s1":
        settings["s1"] = "maintenance" if settings.get("s1", "active") == "active" else "active"
    elif action == "toggle_s2":
        settings["s2"] = "maintenance" if settings.get("s2", "active") == "active" else "active"
    elif action == "switch_s2_api":
        settings["s2_api"] = "tglion" if settings.get("s2_api", "tgpva") == "tgpva" else "tgpva"
    
    if action != "refresh_dashboard":
        settings_col.update_one({"_id": "server_config"}, {"$set": settings}, upsert=True)
        
    await send_server_dashboard(cq.message.chat.id, cq.message.message_id)
    await cq.answer("Dashboard Updated!")

@dp.callback_query(F.data == "set_s2_profit")
async def set_profit_btn(cq: CallbackQuery, state: FSMContext):
    await cq.message.answer("📈 Send the new profit percentage for Server 2 (e.g., send 20 for 20%):")
    await state.set_state(ServerManage.waiting_profit_margin)
    await cq.answer()

@dp.message(StateFilter(ServerManage.waiting_profit_margin))
async def save_profit_margin(msg: Message, state: FSMContext):
    try:
        profit = float(msg.text.strip())
        settings_col.update_one({"_id": "server_config"}, {"$set": {"s2_profit": profit}}, upsert=True)
        await msg.answer(f"✅ Server 2 Profit Margin set to <b>{profit}%</b>", parse_mode="HTML")
        status_msg = await msg.answer("🔄 <i>Refreshing Dashboard...</i>", parse_mode="HTML")
        await send_server_dashboard(msg.chat.id, status_msg.message_id)
    except Exception:
        await msg.answer("❌ Invalid number. Please send a valid percentage like 15 or 20.5")
    await state.clear()


# ================= Admin Ban Commands (Upgraded) =================
async def get_target_id(msg: Message, args: list) -> int | None:
    """Helper to get user ID from reply, username, or manual ID"""
    # 1. Check if it's a reply
    if msg.reply_to_message:
        return msg.reply_to_message.from_user.id
    
    # 2. Check if an ID or @username was provided
    if len(args) < 2:
        return None
    
    target = args[1]
    
    # If it's a numeric ID
    if target.isdigit():
        return int(target)
    
    # If it's a username (this only works if the user is already in your DB)
    if target.startswith("@"):
        username = target.replace("@", "")
        user_doc = users_col.find_one({"username": username})
        if user_doc:
            return user_doc["_id"]
    
    return None

# ================= Admin: Top Users Command =================

@dp.message(Command("topusers"))
async def cmd_top_users(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    # Parse page number from command (e.g., /topusers 2)
    args = msg.text.split()
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    limit = 10
    skip = (page - 1) * limit

    # Aggregation to find top spenders
    pipeline = [
        {
            "$group": {
                "_id": "$user_id",
                "total_spend": {"$sum": "$price"}
            }
        },
        {"$sort": {"total_spend": -1}},
        {"$skip": skip},
        {"$limit": limit},
        {
            "$lookup": {
                "from": "users",
                "localField": "_id",
                "foreignField": "_id",
                "as": "user_info"
            }
        }
    ]

    top_spenders = list(orders_col.aggregate(pipeline))

    if not top_spenders:
        return await msg.answer("❌ No spending data found or page out of range.")

    response_text = f"🏆 <b>Top Spending Users (Page {page})</b>\n"
    response_text += "--------------------------------\n"

    for entry in top_spenders:
        user_id = entry["_id"]
        total_spend = entry["total_spend"]
        
        # Get username if available, else use ID
        user_data = entry["user_info"][0] if entry["user_info"] else {}
        username = user_data.get("username")
        name = f"@{username}" if username else f"ID: <code>{user_id}</code>"
        
        response_text += f"👤 {name}\n💰 Total spend: ₹{total_spend:.2f}\n"
        response_text += "---------\n"

    # Add navigation tip
    response_text += f"\n<i>Use <code>/topusers {page + 1}</code> for next page.</i>"
    
    await msg.answer(response_text, parse_mode="HTML")
    

@dp.message(Command("gban"))
async def cmd_gban(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    args = msg.text.split()
    target_id = await get_target_id(msg, args)
    
    if not target_id:
        return await msg.answer("⚠️ <b>Usage:</b>\n• Reply to a user with <code>/gban</code>\n• <code>/gban 12345678</code>\n• <code>/gban @username</code> (User must be in DB)", parse_mode="HTML")

    # Update or Create the user with banned status
    users_col.update_one(
        {"_id": target_id},
        {"$set": {"banned": True}},
        upsert=True # This ensures they are added to DB even if they never started the bot
    )
    
    await msg.answer(f"⛔ User <code>{target_id}</code> has been <b>BANNED</b> from the bot.", parse_mode="HTML")
    
    # Try to notify the user
    try:
        await bot.send_message(target_id, "🚫 <b>You have been banned from using this bot by the admin.</b>", parse_mode="HTML")
    except:
        pass

@dp.message(Command("ungban"))
async def cmd_ungban(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    args = msg.text.split()
    target_id = await get_target_id(msg, args)
    
    if not target_id:
        return await msg.answer("⚠️ <b>Usage:</b> Reply with <code>/ungban</code> or use ID/Username.")

    result = users_col.update_one({"_id": target_id}, {"$set": {"banned": False}})
    
    if result.matched_count > 0:
        await msg.answer(f"✅ User <code>{target_id}</code> has been <b>UNBANNED</b>.", parse_mode="HTML")
    else:
        await msg.answer("❌ User not found in database.")
        

# ================= Admin Broadcast (Forward Version - Aiogram Fix) =================
@dp.message(Command("broadcast"))
async def cmd_broadcast(msg: Message):
    if not is_admin(msg.from_user.id):
        return await msg.answer("❌ Not authorized.")

    if not msg.reply_to_message:
        return await msg.answer("⚠️ Reply to the message you want to broadcast with /broadcast.")

    broadcast_msg = msg.reply_to_message
    users = list(users_col.find({}))

    if not users:
        return await msg.answer("⚠️ No users found to broadcast.")

    sent_count = 0
    failed_count = 0

    for user in users:
        user_id = user["_id"]
        try:
            await bot.forward_message(
                chat_id=user_id,
                from_chat_id=broadcast_msg.chat.id,
                message_id=broadcast_msg.message_id
            )
            sent_count += 1
        except Exception as e:
            failed_count += 1
            print(f"Failed to send to {user_id}: {e}")

    await msg.answer(f"✅ Broadcast completed!\n\nSent: {sent_count}\nFailed: {failed_count}")
    

# ===== Register External Handlers =====
start_auto_sweeper = register_recharge_handlers(
    dp=dp,
    bot=bot,
    users_col=users_col,
    txns_col=db["transactions"],
    crypto_col=crypto_col,
    settings_col=settings_col,
)

    
#-----------------lzt---------------
LZT_BASE = "https://prod-api.lzt.market"
LZT_SEARCH_URL = f"{LZT_BASE}/telegram"
LZT_ME_URL = f"{LZT_BASE}/me"
LZT_STOCK_CHANNEL = -1003349993686
LZT_PER_PAGE = 10
COUNTRY_PER_PAGE = 20     
MAX_RETRY_REQUEST_ATTEMPTS = 15
RETRY_DELAY_SECONDS = 1.5
RATE_LIMIT_RETRY_DELAY = 3.0
LIVE_PRICE_CACHE = {}

def flag_emoji(alpha2):
    try:
        return "".join(chr(0x1F1E6 + ord(c) - ord('A')) for c in alpha2.upper())
    except Exception:
        return "🏳️"

LZT_COUNTRY_MAP = {
    "ABW": ("AW", "Aruba"), "AFG": ("AF", "Afghanistan"), "AGO": ("AO", "Angola"),
    "ALB": ("AL", "Albania"), "AND": ("AD", "Andorra"), "ARE": ("AE", "UAE"),
    "ARG": ("AR", "Argentina"), "ARM": ("AM", "Armenia"), "ATG": ("AG", "Antigua & Barbuda"),
    "AUS": ("AU", "Australia"), "AUT": ("AT", "Austria"), "AZE": ("AZ", "Azerbaijan"),
    "BDI": ("BI", "Burundi"), "BEL": ("BE", "Belgium"), "BEN": ("BJ", "Benin"),
    "BFA": ("BF", "Burkina Faso"), "BGD": ("BD", "Bangladesh"), "BGR": ("BG", "Bulgaria"),
    "BHR": ("BH", "Bahrain"), "BHS": ("BS", "Bahamas"), "BIH": ("BA", "Bosnia"),
    "BLR": ("BY", "Belarus"), "BLZ": ("BZ", "Belize"), "BOL": ("BO", "Bolivia"),
    "BRA": ("BR", "Brazil"), "BRB": ("BB", "Barbados"), "BRN": ("BN", "Brunei"),
    "BTN": ("BT", "Bhutan"), "BWA": ("BW", "Botswana"), "CAF": ("CF", "Central African Rep."),
    "CAN": ("CA", "Canada"), "CHE": ("CH", "Switzerland"), "CHL": ("CL", "Chile"),
    "CIV": ("CI", "Ivory Coast"), "CMR": ("CM", "Cameroon"), "COD": ("CD", "DR Congo"),
    "COG": ("CG", "Congo"), "COL": ("CO", "Colombia"), "COM": ("KM", "Comoros"),
    "CPV": ("CV", "Cape Verde"), "CRI": ("CR", "Costa Rica"), "CUB": ("CU", "Cuba"),
    "CYP": ("CY", "Cyprus"), "CZE": ("CZ", "Czech Republic"), "DEU": ("DE", "Germany"),
    "DJI": ("DJ", "Djibouti"), "DMA": ("DM", "Dominica"), "DNK": ("DK", "Denmark"),
    "DOM": ("DO", "Dominican Rep."), "DZA": ("DZ", "Algeria"), "ECU": ("EC", "Ecuador"),
    "EGY": ("EG", "Egypt"), "ERI": ("ER", "Eritrea"), "ESP": ("ES", "Spain"),
    "EST": ("EE", "Estonia"), "ETH": ("ET", "Ethiopia"), "FIN": ("FI", "Finland"),
    "FJI": ("FJ", "Fiji"), "FRA": ("FR", "France"), "FSM": ("FM", "Micronesia"),
    "GAB": ("GA", "Gabon"), "GBR": ("GB", "United Kingdom"), "GEO": ("GE", "Georgia"),
    "GHA": ("GH", "Ghana"), "GIB": ("GI", "Gibraltar"), "GIN": ("GN", "Guinea"),
    "GLP": ("GP", "Guadeloupe"), "GMB": ("GM", "Gambia"), "GNB": ("GW", "Guinea-Bissau"),
    "GNQ": ("GQ", "Equatorial Guinea"), "GRC": ("GR", "Greece"), "GRD": ("GD", "Grenada"),
    "GTM": ("GT", "Guatemala"), "GUF": ("GF", "French Guiana"), "GUM": ("GU", "Guam"),
    "GUY": ("GY", "Guyana"), "HKG": ("HK", "Hong Kong"), "HND": ("HN", "Honduras"),
    "HRV": ("HR", "Croatia"), "HTI": ("HT", "Haiti"), "HUN": ("HU", "Hungary"),
    "IDN": ("ID", "Indonesia"), "IMN": ("IM", "Isle of Man"), "IND": ("IN", "India"),
    "IRL": ("IE", "Ireland"), "IRN": ("IR", "Iran"), "IRQ": ("IQ", "Iraq"),
    "ISL": ("IS", "Iceland"), "ISR": ("IL", "Israel"), "ITA": ("IT", "Italy"),
    "JAM": ("JM", "Jamaica"), "JOR": ("JO", "Jordan"), "JPN": ("JP", "Japan"),
    "KAZ": ("KZ", "Kazakhstan"), "KEN": ("KE", "Kenya"), "KGZ": ("KG", "Kyrgyzstan"),
    "KHM": ("KH", "Cambodia"), "KIR": ("KI", "Kiribati"), "KNA": ("KN", "Saint Kitts"),
    "KOR": ("KR", "South Korea"), "KWT": ("KW", "Kuwait"), "LAO": ("LA", "Laos"),
    "LBN": ("LB", "Lebanon"), "LBR": ("LR", "Liberia"), "LBY": ("LY", "Libya"),
    "LCA": ("LC", "Saint Lucia"), "LIE": ("LI", "Liechtenstein"), "LKA": ("LK", "Sri Lanka"),
    "LSO": ("LS", "Lesotho"), "LTU": ("LT", "Lithuania"), "LUX": ("LU", "Luxembourg"),
    "LVA": ("LV", "Latvia"), "MAC": ("MO", "Macau"), "MAR": ("MA", "Morocco"),
    "MCO": ("MC", "Monaco"), "MDA": ("MD", "Moldova"), "MDG": ("MG", "Madagascar"),
    "MDV": ("MV", "Maldives"), "MEX": ("MX", "Mexico"), "MHL": ("MH", "Marshall Islands"),
    "MKD": ("MK", "North Macedonia"), "MLI": ("ML", "Mali"), "MLT": ("MT", "Malta"),
    "MMR": ("MM", "Myanmar"), "MNE": ("ME", "Montenegro"), "MNG": ("MN", "Mongolia"),
    "MNP": ("MP", "N. Mariana Isl."), "MOZ": ("MZ", "Mozambique"), "MRT": ("MR", "Mauritania"),
    "MTQ": ("MQ", "Martinique"), "MUS": ("MU", "Mauritius"), "MWI": ("MW", "Malawi"),
    "MYS": ("MY", "Malaysia"), "NAM": ("NA", "Namibia"), "NER": ("NE", "Niger"),
    "NGA": ("NG", "Nigeria"), "NIC": ("NI", "Nicaragua"), "NLD": ("NL", "Netherlands"),
    "NOR": ("NO", "Norway"), "NPL": ("NP", "Nepal"), "NRU": ("NR", "Nauru"),
    "NZL": ("NZ", "New Zealand"), "OMN": ("OM", "Oman"), "PAK": ("PK", "Pakistan"),
    "PAN": ("PA", "Panama"), "PER": ("PE", "Peru"), "PHL": ("PH", "Philippines"),
    "PLW": ("PW", "Palau"), "PNG": ("PG", "Papua New Guinea"), "POL": ("PL", "Poland"),
    "PRI": ("PR", "Puerto Rico"), "PRK": ("KP", "North Korea"), "PRT": ("PT", "Portugal"),
    "PRY": ("PY", "Paraguay"), "PSE": ("PS", "Palestine"), "QAT": ("QA", "Qatar"),
    "REU": ("RE", "Reunion"), "ROU": ("RO", "Romania"), "RUS": ("RU", "Russia"),
    "RWA": ("RW", "Rwanda"), "SAU": ("SA", "Saudi Arabia"), "SDN": ("SD", "Sudan"),
    "SEN": ("SN", "Senegal"), "SGP": ("SG", "Singapore"), "SLB": ("SB", "Solomon Islands"),
    "SLE": ("SL", "Sierra Leone"), "SLV": ("SV", "El Salvador"), "SMR": ("SM", "San Marino"),
    "SOM": ("SO", "Somalia"), "SRB": ("RS", "Serbia"), "SSD": ("SS", "South Sudan"),
    "STP": ("ST", "Sao Tome"), "SUR": ("SR", "Suriname"), "SVK": ("SK", "Slovakia"),
    "SVN": ("SI", "Slovenia"), "SWE": ("SE", "Sweden"), "SWZ": ("SZ", "Eswatini"),
    "SYC": ("SC", "Seychelles"), "SYR": ("SY", "Syria"), "TCD": ("TD", "Chad"),
    "TGO": ("TG", "Togo"), "THA": ("TH", "Thailand"), "TJK": ("TJ", "Tajikistan"),
    "TKM": ("TM", "Turkmenistan"), "TLS": ("TL", "Timor-Leste"), "TON": ("TO", "Tonga"),
    "TTO": ("TT", "Trinidad & Tobago"), "TUN": ("TN", "Tunisia"), "TUR": ("TR", "Turkey"),
    "TUV": ("TV", "Tuvalu"), "TWN": ("TW", "Taiwan"), "TZA": ("TZ", "Tanzania"),
    "UGA": ("UG", "Uganda"), "UKR": ("UA", "Ukraine"), "URY": ("UY", "Uruguay"),
    "USA": ("US", "USA"), "UZB": ("UZ", "Uzbekistan"), "VAT": ("VA", "Vatican City"),
    "VCT": ("VC", "St. Vincent & Grenadines"), "VEN": ("VE", "Venezuela"), "VNM": ("VN", "Vietnam"),
    "VUT": ("VU", "Vanuatu"), "WSM": ("WS", "Samoa"), "XKX": ("XK", "Kosovo"),
    "YEM": ("YE", "Yemen"), "ZAF": ("ZA", "South Africa"), "ZMB": ("ZM", "Zambia"),
    "ZWE": ("ZW", "Zimbabwe")
}

LZT_ALL_COUNTRIES = [
    {"code": a2, "flag": flag_emoji(a2), "name": name}
    for code, (a2, name) in sorted(LZT_COUNTRY_MAP.items(), key=lambda kv: kv[1][1])
]


class Server3Admin(StatesGroup):
    waiting_service_code = State()
    waiting_country_name = State()
    waiting_flag = State()
    waiting_price_inr = State()
    waiting_max_price = State()
    waiting_lzt_token = State()
    waiting_lzt_margin = State()


class UserSearch(StatesGroup):
    waiting_country_query = State()


class Server3Edit(StatesGroup):
    waiting_new_price = State()
    waiting_new_cap = State()


def register_server3_handlers(dp, bot, db, users_col, orders_col, settings_col,
                               admin_ids, ADMINLOG, SALES,
                               BOTUSER, CHANNEL, exchange_rate=95.0):

    lzt_orders = db["lzt_orders"]

    def is_admin(uid):
        return uid in admin_ids

    # Shared HTTP session + bounded concurrency. Creating a ClientSession per
    # request was a major source of latency and connection churn.
    _lzt_session = None
    _lzt_session_lock = asyncio.Lock()
    _lzt_sem = asyncio.Semaphore(10)
    _settings_cache = {"value": {}, "expires": 0.0}
    _settings_cache_lock = asyncio.Lock()

    async def get_settings_cached(force=False):
        now = time.monotonic()
        if not force and _settings_cache["value"] and now < _settings_cache["expires"]:
            return _settings_cache["value"]
        async with _settings_cache_lock:
            now = time.monotonic()
            if not force and _settings_cache["value"] and now < _settings_cache["expires"]:
                return _settings_cache["value"]
            cfg = await asyncio.to_thread(settings_col.find_one, {"_id": "server_config"}) or {}
            _settings_cache["value"] = cfg
            _settings_cache["expires"] = time.monotonic() + 30.0
            return cfg

    def get_settings():
        # Keep synchronous callers working; do not use this from async hot paths.
        return settings_col.find_one({"_id": "server_config"}) or {}

    def s3_debug_enabled():
        return _settings_cache["value"].get("s3_debug_mode", "on") == "on"

    async def get_lzt_session(headers):
        nonlocal _lzt_session
        async with _lzt_session_lock:
            if _lzt_session is None or _lzt_session.closed:
                timeout = aiohttp.ClientTimeout(total=12, connect=3, sock_connect=3, sock_read=8)
                connector = aiohttp.TCPConnector(limit=30, limit_per_host=15, ttl_dns_cache=300)
                _lzt_session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return _lzt_session

    async def lzt_request(method, url, params=None, json_body=None, action_label="LZT", silent=False):
        """Non-blocking centralized external API request."""
        settings = await get_settings_cached()
        token = settings.get("lzt_token") or os.getenv("LZT_API_KEY", "")
        if not token:
            return 401, {"errors": ["LZT API token is not configured. Use /manage3."]}

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "SellingBot/1.0",
        }
        session = await get_lzt_session(headers)
        kwargs = {}
        if params is not None:
            kwargs["params"] = params
        if json_body is not None:
            kwargs["json"] = json_body

        async with _lzt_sem:
            for attempt in range(3):
                try:
                    async with session.request(method.upper(), url, headers=headers, **kwargs) as resp:
                        raw = await resp.text()
                        try:
                            data = json.loads(raw) if raw else {}
                        except Exception:
                            data = {"raw": raw}
                        if not silent:
                            asyncio.create_task(log_admin_debug(action_label, str(url), data, token))
                        if resp.status == 429 and attempt < 2:
                            await asyncio.sleep(RATE_LIMIT_RETRY_DELAY)
                            continue
                        return resp.status, data
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if attempt == 2:
                        if not silent:
                            asyncio.create_task(log_admin(f"❌ <b>{escape(action_label)}</b> request failed: {escape(str(e))}"))
                        return 599, {"errors": [str(e)]}
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
        return 599, {"errors": ["LZT request failed"]}


    def mask_key(url, key):
        return url.replace(key, "•••" + key[-4:]) if key else url

    async def log_admin_debug(action_label, url, raw, key=""):
        if not s3_debug_enabled():
            return
        try:
            await bot.send_message(
                ADMINLOG,
                f"🛰️ <b>API Debug — {action_label}</b>\n\n"
                f"<b>Request:</b>\n<code>{escape(mask_key(str(url), key))}</code>\n\n"
                f"<b>Raw Response:</b>\n<pre>{escape(str(raw)[:1500])}</pre>",
                parse_mode="HTML"
            )
        except Exception:
            pass

    async def log_admin(text, raw=None):
        full = text
        if raw:
            full += f"\n\n📡 <b>Raw Response</b>\n<pre>{escape(str(raw)[:1500])}</pre>"
        try:
            await bot.send_message(ADMINLOG, full[:4090], parse_mode="HTML")
        except Exception:
            pass

    # ============================================================
    # ================== TG (LZT): USER BUY FLOW ==================
    # ============================================================

    def usd_to_sell_inr(usd_price: float) -> float:
        """Convert current LZT USD cost to INR using the live Server-3 margin."""
        try:
            usd_price = float(usd_price)
        except (TypeError, ValueError):
            return 0.0

        cfg = get_settings()
        try:
            margin = float(cfg.get("s3_profit_margin", 0) or 0)
        except (TypeError, ValueError):
            margin = 0.0

        base_inr = usd_price * 90.0
        return round(base_inr * (1.0 + margin / 100.0), 2)

    def build_country_page(page, query=None):
        global LIVE_PRICE_CACHE

        pool = []

        for c in LZT_ALL_COUNTRIES:
            usd_price = LIVE_PRICE_CACHE.get(c["code"])

            if usd_price is not None:
                inr_price = usd_to_sell_inr(usd_price)

                pool.append({
                    "code": c["code"],
                    "name": c["name"],
                    "flag": c["flag"],
                    "inr_price": inr_price
                })

        pool.sort(key=lambda x: x["inr_price"])

        if query:
            q = query.lower()
            pool = [
                c for c in pool
                if q in c["name"].lower()
                or q in c["code"].lower()
            ]

        total_pages = max(
            1,
            (len(pool) + COUNTRY_PER_PAGE - 1) // COUNTRY_PER_PAGE
        )

        page = max(0, min(page, total_pages - 1))

        start = page * COUNTRY_PER_PAGE
        page_items = pool[start:start + COUNTRY_PER_PAGE]

        kb = InlineKeyboardBuilder()

        if not page_items:
            kb.row(
                InlineKeyboardButton(
                    style="primary",
                    text="🔙 Back to Servers",
                    callback_data="server_list"
                )
            )

            return (
                "📭 <b>No countries matched your search or are in stock.</b>",
                kb.as_markup()
            )

        for c in page_items:
            btn_text = (
                f"{c['flag']} {c['name']} "
                f"(₹{c['inr_price']:.0f})"
            )

            kb.button(
                style="primary",
                text=btn_text,
                callback_data=f"s3tg_country:{c['code']}:0"
            )

        kb.adjust(2)

        nav = []

        if total_pages > 1:
            for p in range(total_pages):
                if p == page:
                    nav.append(
                        InlineKeyboardButton(
                            style="primary",
                            text=f"• {p + 1} •",
                            callback_data="noop"
                        )
                    )
                else:
                    nav.append(
                        InlineKeyboardButton(
                            style="primary",
                            text=f"{p + 1}",
                            callback_data=f"s3tg_cpage:{p}:{query or ''}"
                        )
                    )

            for i in range(0, len(nav), 8):
                kb.row(*nav[i:i + 8])

        kb.row(
            InlineKeyboardButton(
                style="primary",
                text="🔍 Search",
                callback_data="s3tg_search"
            )
        )

        kb.row(
            InlineKeyboardButton(
                style="primary",
                text="📋 My Orders",
                callback_data="s3tg_my_orders"
            )
        )

        kb.row(
            InlineKeyboardButton(
                style="primary",
                text="🔙 Back to Servers",
                callback_data="buy"
            )
        )

        text = (
            "<b>📱 Telegram Accounts — Server (3)</b>\n\n"
            "<blockquote>"
            "🌍 Available regions sorted by cheapest price:"
            "</blockquote>"
        )

        return text, kb.as_markup()

    async def send_lzt_countries_message(chat_id):
            text, markup = build_country_page(0)
            try: await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
            except Exception: pass

    @dp.callback_query(F.data == "s3tg_terms_agree")
    async def s3tg_terms_agree(cq: CallbackQuery):
        try:
            await cq.answer()
        except TelegramBadRequest:
            pass

        cfg = settings_col.find_one({"_id": "server_config"}) or {}

        if not cfg.get("lzt_enabled", False):
            return await cq.message.edit_text(
                "⚠️ Currently unavailable.",
                parse_mode="HTML"
            )

        text, markup = build_country_page(0)

        try:
            await cq.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=markup
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                raise




    @dp.callback_query(F.data.startswith("s3tg_cpage:"))
    async def s3tg_cpage(cq: CallbackQuery):
        try:
            await cq.answer()
        except TelegramBadRequest:
            pass
        _, page, query = cq.data.split(":", 2)
        text, markup = build_country_page(int(page), query or None)
        try:
            await cq.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                raise

    @dp.callback_query(F.data == "s3tg_search")
    async def s3tg_search(cq: CallbackQuery, state: FSMContext):
        await state.set_state(UserSearch.waiting_country_query)
        await cq.message.edit_text("🔍 <b>Type a country name to search:</b>", parse_mode="HTML")
        await cq.answer()

    @dp.message(StateFilter(UserSearch.waiting_country_query))
    async def s3tg_search_result(msg: Message, state: FSMContext):
        await state.clear()
        text, markup = build_country_page(0, msg.text.strip())
        await msg.answer(text, parse_mode="HTML", reply_markup=markup)

    @dp.callback_query(F.data == "noop")
    async def s3tg_noop(cq: CallbackQuery):
        await cq.answer()

    @dp.callback_query(F.data.startswith("s3tg_country:"))
    async def s3tg_listings(cq: CallbackQuery):
        _, country_code, vpage = cq.data.split(":")
        vpage = int(vpage)
        await cq.message.edit_text("🔎 <i>Fetching live listings... ⏳</i>", parse_mode="HTML")

        lzt_page = (vpage // 4) + 1

        status, data = await lzt_request(
            "GET", LZT_SEARCH_URL,
            params={"country[]": country_code, "order_by": "price_to_up", "currency": "usd", "spam": "no", "page": lzt_page},
            action_label=f"LZT: Search ({country_code} Page {lzt_page})"
        )

        if status != 200 or "items" not in data:
            errs = data.get("errors", ["Unknown error"]) if isinstance(data, dict) else ["Unknown error"]
            await log_admin(f"⚠️ <b>LZT Search Failed</b> (status {status}): {'; '.join(errs)}", data)
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(style="primary", text="🔙 Back", callback_data="s3tg_open")]])
            return await cq.message.edit_text(f"❌ <b>Could not fetch listings.</b>\n<i>{escape('; '.join(errs))}</i>", parse_mode="HTML", reply_markup=kb)

        all_items = data["items"]
        start_index = (vpage % 4) * LZT_PER_PAGE
        page_items = all_items[start_index : start_index + LZT_PER_PAGE]

        if not page_items and vpage == 0:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(style="primary", text="🔙 Back", callback_data="s3tg_open")]])
            return await cq.message.edit_text("📭 <b>No accounts available in this region right now.</b>", parse_mode="HTML", reply_markup=kb)

        country_meta = next((c for c in LZT_ALL_COUNTRIES if c['code'] == country_code), {"flag": "🌍", "name": country_code})

        kb = InlineKeyboardBuilder()
        for item in page_items:
            usd_price = item["price"]
            sell_price = usd_to_sell_inr(usd_price)
            kb.button(style="primary", text=f"{country_meta['flag']} ₹{sell_price:.0f}", callback_data=f"s3tg_view:{item['item_id']}:{usd_price}:{country_code}:{vpage}")
        kb.adjust(2)

        nav = []
        if vpage > 0:
            nav.append(InlineKeyboardButton(style="primary", text="⬅️ Prev", callback_data=f"s3tg_country:{country_code}:{vpage-1}"))
        if start_index + LZT_PER_PAGE < len(all_items):
            nav.append(InlineKeyboardButton(style="primary", text="Next ➡️", callback_data=f"s3tg_country:{country_code}:{vpage+1}"))
        if nav:
            kb.row(*nav)
        
        kb.row(InlineKeyboardButton(style="primary", text="🔄 Refresh", callback_data=f"s3tg_country:{country_code}:{vpage}"), InlineKeyboardButton(style="primary", text="🔙 Back", callback_data="s3tg_cpage:0:"))

        text = f"<b>📱 TG Accounts — {country_meta['name']}</b>\n\n<blockquote>🛒 Showing {len(page_items)} accounts. Tap to buy 👇</blockquote>"
        await cq.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())


    @dp.callback_query(F.data.startswith("s3tg_view:"))
    async def s3tg_view(cq: CallbackQuery):
        parts = cq.data.split(":")
        item_id = parts[1]
        usd_price = float(parts[2])
        country_code = parts[3]
        vpage = parts[4]
    
        sell_price = usd_to_sell_inr(usd_price)
    
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(style="primary", text="✅ Accept & Buy", callback_data=f"s3tg_buy:{item_id}:{usd_price}:{country_code}"))
        kb.row(InlineKeyboardButton(style="primary", text="🔙 Back", callback_data=f"s3tg_country:{country_code}:{vpage}"))
    
        text = (
            "<b>🛒 Order Confirmation & Terms</b>\n\n"
            "<blockquote>"
            f"🆔 <b>Item ID:</b> <code>{item_id}</code>\n"
            f"💰 <b>Price:</b> ₹{sell_price:.0f}\n"
            "</blockquote>\n\n"
            "🚨 <b>WARNING:</b> These are cheap phishing accounts. The bot is not responsible for any logouts. <b>Buy at your own risk!</b>\n\n"
            "🚫 <b>No Refunds:</b> All sales are final.\n"
            "📨 <b>OTP Policy:</b> Once the OTP is received, no returns are allowed.\n"
            "❄️ <b>Freeze/Limit:</b> Accounts are fresh; we are not responsible for any limitations after use.\n\n"
            "<i>⚠️ Tap 'Accept & Buy' only if you agree to these terms.</i>"
        )
        await cq.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())

    async def find_exact_replacement(country_code, exact_usd_price):
        params = {
            "country[]": country_code,
            "pmin": exact_usd_price,
            "pmax": exact_usd_price,
            "order_by": "price_to_up",
            "currency": "usd",
            "spam": "no",
            "page": 1
        }
        status, data = await lzt_request("GET", LZT_SEARCH_URL, params=params, action_label="LZT: Failover Search")
        if status == 200 and data.get("items"):
            return data["items"][0]["item_id"]
        return None

    @dp.callback_query(F.data.startswith("s3tg_buy_again:"))
    async def s3tg_buy_again(cq: CallbackQuery):
        try:
            await cq.answer("⏳ Searching...", show_alert=False)
        except TelegramBadRequest:
            pass
        _, country_code, usd_price = cq.data.split(":")
        usd_price = float(usd_price)
    
        status_msg = await cq.message.edit_text("⏳ <i>Searching for another account at the exact same price...</i>", parse_mode="HTML")
        new_item_id = await find_exact_replacement(country_code, usd_price)
    
        if not new_item_id:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(style="primary", text="🔙 Back to Menu", callback_data="s3tg_open")]])
            return await status_msg.edit_text("❌ <b>Out of stock!</b> No accounts left at this exact price for this country.", parse_mode="HTML", reply_markup=kb)
        
        cq.data = f"s3tg_buy:{new_item_id}:{usd_price}:{country_code}"
        await s3tg_buy(cq, external_status_msg=status_msg)

    @dp.callback_query(F.data.startswith("s3tg_buy:"))
    async def s3tg_buy(cq: CallbackQuery, external_status_msg=None):
        # Acknowledge immediately so Telegram never expires the callback.
        try:
            await cq.answer("⏳ Processing purchase...", show_alert=False)
        except TelegramBadRequest:
            pass

        parts = cq.data.split(":")
        if len(parts) < 4:
            return await cq.message.edit_text("❌ Invalid purchase request.", parse_mode="HTML")

        item_id = parts[1]
        try:
            usd_price = float(parts[2])
        except (TypeError, ValueError):
            return await cq.message.edit_text("❌ Invalid account price.", parse_mode="HTML")
        country_code = parts[3]

        sell_price = usd_to_sell_inr(usd_price)
        user_id = cq.from_user.id
        first_name = cq.from_user.first_name or "User"
        username_str = (
            f"@{cq.from_user.username}"
            if cq.from_user.username
            else f"<a href='tg://user?id={user_id}'>{escape(first_name)}</a>"
        )

        user = users_col.find_one({"_id": user_id}) or {"_id": user_id, "balance": 0.0}
        balance = float(user.get("balance", 0.0) or 0.0)

        if balance < sell_price:
            kb = InlineKeyboardBuilder()
            kb.button(style="primary", text="💳 Recharge", callback_data="recharge")
            kb.button(style="primary", text="🔙 Back", callback_data="s3tg_open")
            kb.adjust(1)
            msg_text = (
                f"<b>❌ Insufficient Balance!</b>\n\n"
                f"<blockquote>Required: ₹{sell_price:.2f}\nAvailable: ₹{balance:.2f}</blockquote>"
            )
            target = external_status_msg or cq.message
            return await target.edit_text(msg_text, parse_mode="HTML", reply_markup=kb.as_markup())

        target = external_status_msg or await cq.message.edit_text(
            "⏳ <i>Checking account health & processing...</i>", parse_mode="HTML"
        )

        target_item_id = item_id
        purchase_successful = False
        item_data = {}
        error_text = ""

        # LZT's fast-buy endpoint only requires the current account price.
        # Do NOT call the old/nonexistent get_lzt_balance_id() helper.
        for attempt in range(3):
            check_url = f"{LZT_BASE}/{target_item_id}/check-account"
            c_status, c_data = await lzt_request(
                "POST", check_url, json_body={},
                action_label=f"LZT: Check Account ({target_item_id})"
            )

            if c_status != 200:
                target_item_id = await find_exact_replacement(country_code, usd_price)
                if not target_item_id:
                    errs = c_data.get("errors", ["Account health check failed."]) if isinstance(c_data, dict) else ["Account health check failed."]
                    error_text = "; ".join(map(str, errs))
                    break
                await target.edit_text(
                    "⏳ <i>Current account failed health check. Finding another account at the same price...</i>",
                    parse_mode="HTML"
                )
                continue

            buy_url = f"{LZT_BASE}/{target_item_id}/fast-buy"
            body = {"price": usd_price}
            b_status, b_data = await lzt_request(
                "POST", buy_url, json_body=body,
                action_label=f"LZT: Fast Buy ({target_item_id})"
            )

            if b_status == 200 and isinstance(b_data, dict) and b_data.get("status", "ok") != "error":
                purchase_successful = True
                item_data = b_data.get("item", {}) or {}
                break

            target_item_id = await find_exact_replacement(country_code, usd_price)
            if not target_item_id:
                errs = b_data.get("errors", ["Unknown LZT purchase error"]) if isinstance(b_data, dict) else ["Unknown LZT purchase error"]
                error_text = "; ".join(map(str, errs))
                break

            await target.edit_text(
                "⏳ <i>That account could not be purchased. Trying another account at the same price...</i>",
                parse_mode="HTML"
            )

        if not purchase_successful:
            await log_admin(
                f"⚠️ <b>LZT Purchase Failed</b>\n\n"
                f"👤 <b>User:</b> {username_str} (<code>{user_id}</code>)\n"
                f"🆔 <b>Last Item:</b> <code>{target_item_id}</code>\n"
                f"❌ <b>Error:</b> {escape(error_text[:1200])}"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(style="primary", text="🔄 Try Again", callback_data=f"s3tg_open")],
                [InlineKeyboardButton(style="primary", text="🔙 Back", callback_data="buy")]
            ])
            return await target.edit_text(
                "❌ <b>Purchase failed.</b>\n\nPlease try another account or try again later.",
                parse_mode="HTML", reply_markup=kb
            )

        raw_phone = item_data.get("telegram_phone") or item_data.get("login") or "N/A"
        phone_number = str(raw_phone)
        if phone_number != "N/A" and not phone_number.startswith("+") and not phone_number.isalpha():
            phone_number = f"+{phone_number}"

        # Deduct only after LZT confirms the purchase succeeded.
        new_balance = balance - sell_price
        users_col.update_one({"_id": user_id}, {"$set": {"balance": new_balance}}, upsert=True)

        country_meta = next((c for c in LZT_ALL_COUNTRIES if c["code"] == country_code), {"name": country_code})
        country_display_name = country_meta["name"]

        lzt_orders.insert_one({
            "user_id": user_id, "item_id": target_item_id,
            "title": item_data.get("title_en") or item_data.get("title"),
            "price_usd": usd_price, "price_inr": sell_price,
            "number": phone_number, "country_code": country_code,
            "country_name": country_display_name, "server": 3,
            "status": "delivered", "created_at": datetime.utcnow()
        })

        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(style="primary", text="📋 Copy Number", copy_text=CopyTextButton(text=phone_number)))
        kb.row(InlineKeyboardButton(style="primary", text="📩 Get Login Code", callback_data=f"s3tg_getcode:{target_item_id}:{country_code}:{usd_price}"))

        success_text = (
            f"<pre>✅ Purchased Successfully!</pre>\n"
            f"➖ <b><u>Server</u></b>: Server 3 (cheap ph is hing) acc (Telegram)\n"
            f"➖ <b><u>Country</u></b>: {escape(country_display_name)}\n"
            f"📞 <b>Number:</b> <code>{escape(phone_number)}</code>\n"
            f"🏷️ <b>Price:</b> ₹{sell_price:.2f}\n"
            f"💸 <b>Balance:</b> ₹{new_balance:.2f}"
        )
        await target.edit_text(success_text, parse_mode="HTML", reply_markup=kb.as_markup())

        await log_admin(
            f"🆕 <b>cheap phishing Acc Sold (Server 3 - Telegram)</b>\n\n"
            f"👤 <b>User:</b> {username_str} (<code>{user_id}</code>)\n"
            f"🆔 <b>Final Item ID:</b> <code>{target_item_id}</code>\n"
            f"🌍 <b>Country:</b> {escape(country_display_name)} ({country_code})\n"
            f"💰 <b>Cost (LZT):</b> ${usd_price:.2f}\n"
            f"💵 <b>Sold For:</b> ₹{sell_price:.2f}\n"
            f"📞 <b>Number:</b> <code>{escape(phone_number)}</code>"
        )

    @dp.callback_query(F.data.startswith("s3tg_getcode:"))
    async def s3tg_getcode(cq: CallbackQuery):
        parts = cq.data.split(":")
        item_id = parts[1]
        country_code = parts[2]
        usd_price = float(parts[3])
    
        await cq.answer("🔄 Fetching login code...", show_alert=False)

        code_url = f"{LZT_BASE}/{item_id}/telegram-login-code"
        status, data = await lzt_request("GET", code_url, action_label=f"LZT: Telegram Login Code ({item_id})")

        if status != 200:
            errs = data.get("errors", ["Unknown"]) if isinstance(data, dict) else ["Unknown"]
            return await bot.send_message(cq.from_user.id, f"❌ Could not fetch login code yet: {'; '.join(errs)}\nTry again in a few seconds.")

        code = None
        if 'codes' in data:
            if isinstance(data['codes'], dict) and 'code' in data['codes']:
                code = str(data['codes']['code'])
            elif isinstance(data['codes'], list) and len(data['codes']) > 0:
                code = str(data['codes'][0].get('code'))
            
        if not code:
            raw_text = json.dumps(data) if isinstance(data, dict) else str(data)
            match = re.search(r"\b\d{4,6}\b", raw_text)
            code = match.group(0) if match else None

        if not code:
            await log_admin(f"⚠️ <b>Could not parse login code — check raw response</b>\nItem: {item_id}", data)
            return await bot.send_message(cq.from_user.id, "⚠️ Code received but couldn't be read automatically. Please contact support.")

        lzt_orders.update_one({"item_id": item_id}, {"$set": {"status": "completed", "otp": code}})
    
        order_info = lzt_orders.find_one({"item_id": item_id})
        phone_number = order_info.get("number", "Unknown") if order_info else "Unknown"
        country_display_name = order_info.get("country_name", country_code) if order_info else country_code
    
        masked_number = phone_number
        if len(phone_number) > 6:
            masked_number = phone_number[:-4] + "****" + phone_number[-2:]

        password_text = "None"
        try:
            if isinstance(data.get("item"), dict) and data["item"].get("telegram_password"):
                password_text = str(data["item"]["telegram_password"])
        except:
            pass

        channel_message = (
            f"<pre><u>✅ <b>New Number Purchase Successful</b></u></pre>\n\n"
            f"➖ <b><u>Country:</u></b> {country_display_name}\n"
            f"➖ <b><u>Application:</u> Теlegгам 🍷</b>\n\n"
            f"➕ <b>Number: {masked_number} 📞</b>\n"
            f"➕ <b>OTP:</b> <span class='tg-spoiler'>{code}</span> 💬\n"
            f"➕ <b>Server:</b> (3) 🥂\n"
            f"➕ <b>Password:</b> <span class='tg-spoiler'>{password_text}</span> 🔐\n\n"
            f"<b>• @{BOTUSER} || @{CHANNEL}</b>"
        )
    
        buy_button = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(style="primary", text="• Buy Now •", url=f"https://t.me/{BOTUSER}?start=s3tg")]]
        )
    
        try: await bot.send_message(SALES, channel_message, parse_mode="HTML", reply_markup=buy_button)
        except: pass

        success_text = (
            f"<pre>Order Completed ✅</pre>\n"
            f"✅ 𝐍𝗨𝐌𝐁𝐄𝐑 - <code>{phone_number}</code>\n"
            f"💬 𝐂𝐎𝐃𝐄 - <code>{code}</code>\n"
            f"💬 𝐏𝐀𝐒𝐒 - <code>{password_text}</code>\n"
        )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(style="primary", text="Copy OTP", copy_text=CopyTextButton(text=code)),
                    InlineKeyboardButton(style="primary", text="Copy Pass", copy_text=CopyTextButton(text=password_text))
                ],
                [
                    InlineKeyboardButton(style="primary", text="• Get Code Again •", callback_data=f"s3tg_getcode:{item_id}:{country_code}:{usd_price}")
                ],
                [
                    InlineKeyboardButton(style="primary", text="🚫 Remove Bot Devices", callback_data=f"s3tg_reset:{item_id}")
                ],
                [
                    InlineKeyboardButton(style="primary", text=f"🔄 Buy Again ({country_display_name})", callback_data=f"s3tg_buy_again:{country_code}:{usd_price}")
                ]
            ]
        )
    
        await bot.send_message(
            cq.from_user.id,
            success_text,
            parse_mode="HTML", 
            reply_markup=kb
        )

    @dp.callback_query(F.data.startswith("s3tg_reset:"))
    async def s3tg_reset_auth(cq: CallbackQuery):
        item_id = cq.data.split(":")[1]
    
        await cq.answer("🔄 Terminating other sessions, please wait...", show_alert=False)

        reset_url = f"{LZT_BASE}/{item_id}/telegram-reset-authorizations"
        status, data = await lzt_request("POST", reset_url, action_label=f"LZT: Reset Auth ({item_id})")

        if status == 200:
            success_text = "✅ <b>Bot session has been logged out!</b>\nOther devices kicked successfully."
            await cq.message.answer(success_text, parse_mode="HTML")
        else:
            err_msg = "Unknown Error"
            if isinstance(data, dict) and "errors" in data and len(data["errors"]) > 0:
                err_msg = data["errors"][0]
            
            if "Too new authorization" in err_msg:
                error_text = "⚠️ <b>Telegram Security Limit!</b>\n\nWait at least 24 hours after login before kicking other devices."
            else:
                error_text = f"❌ <b>Failed to logout:</b>\n<code>{err_msg}</code>"
            
            await cq.message.answer(error_text, parse_mode="HTML")

    @dp.callback_query(F.data == "s3tg_my_orders")
    async def s3tg_my_orders(cq: CallbackQuery):
        orders = list(lzt_orders.find({"user_id": cq.from_user.id, "server": 3}).sort("created_at", -1).limit(5))
    
        if not orders:
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(style="primary", text="🔙 Back", callback_data="s3tg_open")]])
            return await cq.message.edit_text("📭 <b>You don't have any recent Telegram orders.</b>", parse_mode="HTML", reply_markup=kb)
        
        kb = InlineKeyboardBuilder()
        text = "<b>📋 Your Recent Orders (Server 3):</b>\n<i>You can request OTP if the session is still active!</i>\n\n"
    
        for idx, o in enumerate(orders, 1):
            number = o.get("number", "Unknown")
            country = o.get("country_name", "Unknown")
            item_id = o.get("item_id")
            usd_price = o.get("price_usd", 0)
        
            text += f"<b>{idx}. {country}</b> ➔ <code>{number}</code>\n"
        
            # Har order ke liye alag OTP button
            kb.row(InlineKeyboardButton(style="primary", text=f"📩 Get OTP ({number})", callback_data=f"s3tg_getcode:{item_id}:{o.get('country_code')}:{usd_price}"))
        
        kb.row(InlineKeyboardButton(style="primary", text="🔙 Back", callback_data="s3tg_open"))
    
        await cq.message.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())

    LIVE_PRICE_CACHE = {c['code']: None for c in LZT_ALL_COUNTRIES}

    # ============================================================
    # ========== SMART PRICE UPDATER & AUTO-PROMO LOOP ===========
    # ============================================================

    async def smart_price_updater():
        """Ultra-Fast Chunked Updater with Pre-filled Cache"""
        global LIVE_PRICE_CACHE
        cycle_count = 0  
    
        sem = asyncio.Semaphore(5)

        async def fetch_price(country_code):
            async with sem:
                params = {
                    "country[]": country_code,
                    "order_by": "price_to_up", 
                    "currency": "usd",
                    "spam": "no",
                    "page": 1
                }
                status, data = await lzt_request("GET", LZT_SEARCH_URL, params=params, action_label=f"Cache: {country_code}", silent=True)
            
                if status == 200 and data.get("items"):
                    LIVE_PRICE_CACHE[country_code] = data["items"][0]["price"]
                else:
                    LIVE_PRICE_CACHE[country_code] = None 

        while True:
            cycle_count += 1
            print(f"🌍 [Cycle {cycle_count}] Updating ALL 198+ countries (Fast Chunked Sync)...")
            
            chunk1 = LZT_ALL_COUNTRIES[:100]
            chunk2 = LZT_ALL_COUNTRIES[100:]

            print("🚀 Fetching first 100 countries...")
            tasks1 = [fetch_price(c['code']) for c in chunk1]
            await asyncio.gather(*tasks1)
        
            print("⏳ First 100 done! Waiting 2 minutes to reset API limit...")
            await asyncio.sleep(120)

            print("🚀 Fetching remaining countries...")
            tasks2 = [fetch_price(c['code']) for c in chunk2]
            await asyncio.gather(*tasks2)

            if cycle_count % 4 == 0:
                try:
                    valid_items = {k: v for k, v in LIVE_PRICE_CACHE.items() if v is not None}
                    sorted_cache = sorted(valid_items.items(), key=lambda x: x[1])
                    top_cheapest = sorted_cache[:8]
                
                    promo_lines = []
                    for code, usd_price in top_cheapest:
                        inr_price = usd_to_sell_inr(usd_price)
                        country_meta = next((c for c in LZT_ALL_COUNTRIES if c['code'] == code), None)
                        if country_meta:
                            promo_lines.append(f"➖ {country_meta['flag']} <b>{country_meta['name']}</b> : ₹{inr_price:.0f}")
                
                    if promo_lines:
                        alert_text = (
                            "<b>📉 𝗧𝗲𝗹𝗲𝗴𝗿𝗮𝗺 𝗔𝗰𝗰𝗼𝘂𝗻𝘁𝘀 𝗣𝗿𝗶𝗰𝗲 𝗗𝗿𝗼𝗽 𝗔𝗹𝗲𝗿𝘁!</b>\n\n"
                            "<i>Fresh stock has arrived. Buy now before it's gone!</i>\n\n"
                            + "\n".join(promo_lines) +
                            "\n\n⚡️ <b>Instant Delivery | OTP via Bot</b>"
                        )
                    
                        kb = InlineKeyboardBuilder()
                        kb.row(InlineKeyboardButton(style="primary", text="🛒 Go to Server 3", url=f"https://t.me/{BOTUSER}?start=s3tg"))
                    
                        await bot.send_message("@TechnoAdsChat", alert_text, parse_mode="HTML", reply_markup=kb.as_markup())
                        print("📢 Successfully posted 4-hourly price drop alert to @TechnoAdsChat!")
                except Exception as e:
                    print(f"⚠️ Failed to send group alert: {e}")

            print("✅ Cycle complete. Sleeping for 1 hour...")
            await asyncio.sleep(3600)

    async def on_startup_s3(dispatcher):
        asyncio.create_task(smart_price_updater())

    dp.startup.register(on_startup_s3)

    async def on_shutdown_s3(dispatcher):
        nonlocal _lzt_session
        if _lzt_session is not None and not _lzt_session.closed:
            await _lzt_session.close()
            _lzt_session = None

    dp.shutdown.register(on_shutdown_s3)


class Manage3State(StatesGroup):
    token = State()
    margin = State()

# ================= LZT / SERVER-3 ADMIN PANEL =================
@dp.message(Command("manage3"))
async def manage3(msg:Message):
    if not is_admin(msg.from_user.id): return await msg.answer("❌ Unauthorized.")
    await _manage3_panel(msg.chat.id,msg.message_id)

async def _manage3_panel(chat_id, mid):
    cfg = settings_col.find_one({"_id": "server_config"}) or {}

    token = cfg.get("lzt_token") or os.getenv("LZT_API_KEY", "")
    margin = float(cfg.get("s3_profit_margin", 0))
    enabled = bool(cfg.get("lzt_enabled", False))

    status = "🟢 ENABLED" if enabled else "🔴 DISABLED"

    text = (
        "<b>⚙️ SERVER 3 — LZT ADMIN</b>\n\n"
        f"📡 Status: <b>{status}</b>\n"
        f"🔑 Token: <b>{'Configured' if token else 'Not configured'}</b>\n"
        f"💰 Margin: <b>{margin:.2f}%</b>"
    )

    toggle_text = "🔴 Disable Server 3" if enabled else "🟢 Enable Server 3"

    kb = _panel_kb([
        [
            InlineKeyboardButton(
                style="primary",
                text=toggle_text,
                callback_data="manage3:toggle"
            )
        ],
        [
            InlineKeyboardButton(
                style="primary",
                text="🔑 Set LZT Token",
                callback_data="manage3:settoken"
            )
        ],
        [
            InlineKeyboardButton(
                style="primary",
                text="💰 Set Margin %",
                callback_data="manage3:setmargin"
            )
        ],
        [
            InlineKeyboardButton(
                style="primary",
                text="🧪 Test LZT API",
                callback_data="manage3:test"
            )
        ],
        [
            InlineKeyboardButton(
                style="primary",
                text="🔄 Refresh",
                callback_data="manage3:home"
            )
        ],
    ])

    return await _edit_or_send(chat_id, mid, text, kb)

@dp.callback_query(F.data.startswith("manage3:"))
async def manage3_callback(cq:CallbackQuery,state:FSMContext):
    if not is_admin(cq.from_user.id): return await cq.answer("Unauthorized",show_alert=True)
    action=cq.data.split(":",1)[1]
    if action=="home": await _manage3_panel(cq.message.chat.id,cq.message.message_id); return await cq.answer()
    if action == "toggle":
       cfg = settings_col.find_one({"_id": "server_config"}) or {}
       current = bool(cfg.get("lzt_enabled", False))
       new_status = not current

       settings_col.update_one(
            {"_id": "server_config"},
            {"$set": {"lzt_enabled": new_status}},
            upsert=True
        )

       await cq.answer(
            "🟢 Server 3 enabled." if new_status
            else "🔴 Server 3 disabled.",
            show_alert=True
        )

       await _manage3_panel(
            cq.message.chat.id,
            cq.message.message_id
        )
       return
    if action=="settoken":
        await state.set_state(Manage3State.token); await state.update_data(manage3="token")
        await cq.message.edit_text("🔑 Send the LZT API token:",parse_mode="HTML"); return await cq.answer()
    if action=="setmargin":
        await state.set_state(Manage3State.margin); await state.update_data(manage3="margin")
        await cq.message.edit_text("💰 Send profit margin percentage:",parse_mode="HTML"); return await cq.answer()
    if action=="test":
        cfg=settings_col.find_one({"_id":"server_config"}) or {}; token=cfg.get("lzt_token") or os.getenv("LZT_API_KEY","")
        if not token: return await cq.answer("Set LZT token first.",show_alert=True)
        headers={"Authorization":f"Bearer {token}","Accept":"application/json"}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10),headers=headers) as s:
                async with s.get(LZT_ME_URL) as r: data=await r.text()
            await cq.message.edit_text(f"<b>🧪 LZT API</b>\nHTTP: <code>{r.status}</code>\n<pre>{escape(data[:2000])}</pre>",parse_mode="HTML",reply_markup=_panel_kb([[InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="manage3:home")]]))
        except Exception as e:
            await cq.message.edit_text(f"❌ <b>LZT test failed</b>\n<pre>{escape(str(e))}</pre>",parse_mode="HTML",reply_markup=_panel_kb([[InlineKeyboardButton(style="primary",text="🔙 Back",callback_data="manage3:home")]]))
        return await cq.answer()

@dp.message(Manage3State.token)
async def manage3_token_or_smm_service(msg:Message,state:FSMContext):
    d=await state.get_data()
    if d.get("manage3")=="token":
        if not is_admin(msg.from_user.id): return
        settings_col.update_one({"_id":"server_config"},{"$set":{"lzt_token":msg.text.strip()}},upsert=True)
        await state.clear(); return await msg.answer("✅ LZT token saved. Use /manage3 to test.")
    # fall through is handled by existing SMM service-id handler only if this state was entered there

@dp.message(Manage3State.margin)
async def manage3_margin_or_smm_cost(msg:Message,state:FSMContext):
    d=await state.get_data()
    if d.get("manage3")=="margin":
        try: margin=float(msg.text.strip())
        except: return await msg.answer("❌ Send a number.")
        settings_col.update_one({"_id":"server_config"},{"$set":{"s3_profit_margin":margin}},upsert=True)
        await state.clear(); return await msg.answer("✅ LZT margin saved.")

# ================= SERVER 3 REGISTRATION =================

register_server3_handlers(
    dp=dp,
    bot=bot,
    db=db,
    users_col=users_col,
    orders_col=orders_col,
    settings_col=settings_col,
    admin_ids=ADMIN_IDS,
    ADMINLOG=ADMINLOG,
    SALES=SALESLOG,
    BOTUSER=BOTUSER,
    CHANNEL=CHANNEL,
    exchange_rate=95.0,
)

print("Server 3 handlers registered successfully.")


# ================= BOT RUNNER =================

async def main():
    print("Bot started.")
    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types()
    )
