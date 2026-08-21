import datetime
import io
from urllib.parse import quote
import aiohttp
from bson import ObjectId
from html import escape
from aiogram import F
from aiogram.types import (
    CallbackQuery,
    Message,
    FSInputFile,
    CopyTextButton
)
import qrcode
from aiogram.types import BufferedInputFile
from PIL import Image, ImageDraw, ImageFont
from .utils import fmt_curr

from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.filters import StateFilter, Command

# Import the fixed functions (Ensure these are async if they use Motor)
from .oxapay import create_invoice, check_invoice

# ADMINS

UPI_ID2 = "valrikchauhan@fam"
ADMIN_IDS = [8021449673]

class RechargeState(StatesGroup):
    choose_method = State()
    upi_amount = State()
    upi_screenshot = State()
    crypto_amount = State()
    manual_screenshot = State()
    manual_usdt_amount = State()
    auto_upi_amount = State()
    auto_upi_utr = State()
    # New Admin States
    waiting_for_qr = State()
    waiting_for_pay_value = State()
    
async def get_live_usdt_rate(fallback_rate):
    """Fetches real-time USDT to INR rate from CoinGecko."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=inr") as response:
                if response.status == 200:
                    data = await response.json()
                    return float(data["tether"]["inr"])
    except Exception as e:
        print(f"CoinGecko API Error: {e}")
    return fallback_rate

def get_settings(settings_col):
    """Fetches bot settings from MongoDB or creates defaults."""
    settings = settings_col.find_one({"_id": "bot_settings"})
    if not settings:
        settings = {
            "_id": "bot_settings",
            "USDT_TO_INR": 93.0,
            "MIN_USDT": 0.1,
            "UPI_ID": "hrishantxkaushik@fam",
            "BINANCE_PAY_ID": "1189183313",
            "CWALLET_ID": "62953105",
            "USDT_RATE": 90.0,
            "QR_FILE_ID": None, # Store Telegram file_id instead of local path
            "payments_active": True,
            "upi_active": True,
            "usdt_active": True
        }
        settings_col.insert_one(settings)
    return settings
    

# ==============================
# REFERRAL LOGIC (GLOBAL)
# ==============================

async def process_referral_bonus(bot, users_col, user_id, deposit_amount):
    """
    Checks if user was referred and adds 3% bonus to the referrer.
    """
    try:
        user = users_col.find_one({"_id": user_id})
        if not user or "referred_by" not in user:
            return

        referrer_id = user["referred_by"]
        bonus_amount = round(deposit_amount * 0.03, 2)
        if bonus_amount <= 0:
            return

        # Update Referrer Balance
        users_col.update_one({"_id": referrer_id}, {"$inc": {"balance": bonus_amount}})

        # Notify Referrer
        try:
            await bot.send_message(
                referrer_id,
                f"💰 <b>Referral Bonus Received!</b>\n\n"
                f"👤 Referral: {escape(user.get('full_name', 'User'))}\n"
                f"💵 Deposit: ₹{deposit_amount}\n"
                f"🎁 <b>Your Bonus: ₹{bonus_amount}</b>",
                parse_mode="HTML"
            )
        except:
            pass
    except Exception as e:
        print(f"Referral Error: {e}")


def register_recharge_handlers(dp, bot, users_col, txns_col, crypto_col, settings_col, ADMIN_IDS=None, ):


    # ==============================
    # 1. MENU
    # ==============================
    async def show_recharge_menu(target: Message, state: FSMContext, edit=False):
        settings = get_settings(settings_col)
        if not settings.get("payments_active", True):
            msg = "⚠️ <b>System Offline</b>\nAll payment methods are currently unavailable. Please try again later."
            if edit:
                return await target.edit_text(msg, parse_mode="HTML")
            return await target.answer(msg, parse_mode="HTML")

        await state.clear()
        kb = InlineKeyboardBuilder()
        if settings.get("upi_active", True):
            kb.button(text="UPI (Auto)", callback_data="recharge_auto_upi", icon_custom_emoji_id="6129680679497111287")
        if settings.get("usdt_active", True):
            kb.button(text="Crypto (Auto)", callback_data="recharge_crypto", icon_custom_emoji_id="6134309528960768658")
            kb.button(text="Crypto (Manual)", callback_data="recharge_manual_menu", icon_custom_emoji_id="6134309528960768658")
        kb.adjust(2)
        kb.button(text="Back", callback_data="back_main", icon_custom_emoji_id="5409284148491726576", style="danger")
        kb.adjust(1, 2, 1)

        text = '<tg-emoji emoji-id="5409078930659357770">❌</tg-emoji> <b>Add Balance</b>\n\nChoose a payment method:'
        if edit:
            await target.edit_text(text, parse_mode="HTML", reply_markup=kb.as_markup())
        else:
            await target.answer(text, parse_mode="HTML", reply_markup=kb.as_markup())
        await state.set_state(RechargeState.choose_method)
    

    @dp.callback_query(F.data == "recharge")
    async def recharge_btn(cq: CallbackQuery, state: FSMContext):
        await show_recharge_menu(cq.message, state, edit=True)

    @dp.message(Command("recharge"))
    async def recharge_cmd(message: Message, state: FSMContext):
        await show_recharge_menu(message, state, edit=False)

    # ==============================
    # 2. UPI FLOW
    # ==============================
    @dp.callback_query(F.data == "recharge_upi")
    async def recharge_upi(cq: CallbackQuery, state: FSMContext):
        settings = get_settings(settings_col)
        if not settings.get("upi_active", True):
            return await cq.answer("⚠️ UPI payments are currently unavailable.", show_alert=True)
            
        await cq.message.delete()
        upi_id = settings.get("UPI_ID", "Not Set")
        qr_file_id = settings.get("QR_FILE_ID")

        kb = InlineKeyboardBuilder()
        kb.button(text="Copy UPI", copy_text=CopyTextButton(text=upi_id))
        kb.button(text="Deposit Done", callback_data="upi_done", icon_custom_emoji_id="5409029658794537988", style="success")
        kb.adjust(1)
        kb.button(text="Cancel", callback_data="back_main", icon_custom_emoji_id="5409284148491726576", style="danger")
        kb.adjust(1)

        caption = (f"📲 <b>UPI Payment</b>\n\n"
                   f"ID: <code>{upi_id}</code>\n\n"
                   f"Pay and click 'Deposit Done'.")

        if qr_file_id:
            msg = await cq.message.answer_photo(qr_file_id, caption=caption, parse_mode="HTML", reply_markup=kb.as_markup())
        else:
            # Fallback if no QR is set yet
            msg = await cq.message.answer(caption, parse_mode="HTML", reply_markup=kb.as_markup())
            
        await state.update_data(last_msg=msg.message_id)
        

    @dp.callback_query(F.data == "upi_done")
    async def upi_done(cq: CallbackQuery, state: FSMContext):
        await cq.message.delete()
        msg = await cq.message.answer("💰 Enter amount sent (INR):")
        await state.update_data(last_msg=msg.message_id)
        await state.set_state(RechargeState.upi_amount)

    @dp.message(StateFilter(RechargeState.upi_amount))
    async def upi_amt(message: Message, state: FSMContext):
        await message.delete()
        data = await state.get_data()
        try:
            await bot.delete_message(message.chat.id, data.get("last_msg"))
        except:
            pass

        if not message.text.isdigit():
            msg = await message.answer("❌ Invalid amount.")
            await state.update_data(last_msg=msg.message_id)
            return

        amount = float(message.text)
        msg = await message.answer("📸 Send Screenshot:")
        await state.update_data(amount=amount, last_msg=msg.message_id)
        await state.set_state(RechargeState.upi_screenshot)

    @dp.message(StateFilter(RechargeState.upi_screenshot), F.photo)
    async def upi_screen(message: Message, state: FSMContext):
        data = await state.get_data()
        try:
            await bot.delete_message(message.chat.id, data.get("last_msg"))
        except:
            pass

        txn_id = txns_col.insert_one({
            "user_id": message.from_user.id,
            "username": message.from_user.username,
            "full_name": message.from_user.full_name,
            "amount": data["amount"],
            "method": "upi",
            "status": "pending",
            "created_at": datetime.datetime.utcnow()
        }).inserted_id

        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Approve", callback_data=f"approve_txn:{txn_id}")
        kb.button(text="❌ Decline", callback_data=f"decline_txn:{txn_id}")

        for admin in ADMIN_IDS:
            await bot.send_photo(
                admin,
                message.photo[-1].file_id,
                caption=f"🧾 <b>UPI</b>\n"
                        f"User: {escape(message.from_user.full_name)}\n"
                        f"🆔 ID: <code>{message.from_user.id}</code>\n"
                        f"🆔 Username: @{message.from_user.username}\n"
                        f"Amt: ₹{data['amount']}",
                reply_markup=kb.as_markup()
            )

        await message.answer(
            "✅ Deposit Request Submitted!\n\n"
            "⚡ Your proof is being verified.\n"
            "📝 Status: <code>Pending</code>\n"
            "⏳ Time: 3 Hours (Max)\n\n"
            "You will be notified automatically once funds are added."
        )
        await state.clear()

        

        # ==============================
    # ADMIN COMMANDS
    # ==============================
    @dp.message(Command("setqr"), F.from_user.id.in_(ADMIN_IDS))
    async def cmd_setqr(message: Message, state: FSMContext):
        await message.answer("📸 Send the new QR code image for UPI payments:")
        await state.set_state(RechargeState.waiting_for_qr)

    @dp.message(StateFilter(RechargeState.waiting_for_qr), F.photo, F.from_user.id.in_(ADMIN_IDS))
    async def process_new_qr(message: Message, state: FSMContext):
        file_id = message.photo[-1].file_id
        settings_col.update_one({"_id": "bot_settings"}, {"$set": {"QR_FILE_ID": file_id}}, upsert=True)
        await message.answer("✅ QR Code updated successfully!")
        await state.clear()

    @dp.message(Command("setpay"), F.from_user.id.in_(ADMIN_IDS))
    async def cmd_setpay(message: Message, state: FSMContext):
        kb = InlineKeyboardBuilder()
        keys = ["USDT_TO_INR", "MIN_USDT", "UPI_ID", "BINANCE_PAY_ID", "CWALLET_ID", "USDT_RATE"]
        for key in keys:
            kb.button(text=key, callback_data=f"editpay:{key}")
        kb.adjust(2)
        await message.answer("⚙️ <b>Select a variable to update:</b>", parse_mode="HTML", reply_markup=kb.as_markup())

    @dp.callback_query(F.data.startswith("editpay:"), F.from_user.id.in_(ADMIN_IDS))
    async def editpay_cb(cq: CallbackQuery, state: FSMContext):
        key = cq.data.split(":")[1]
        await state.update_data(edit_key=key)
        await cq.message.edit_text(f"✏️ Send the new value for <b>{key}</b>:", parse_mode="HTML")
        await state.set_state(RechargeState.waiting_for_pay_value)

    @dp.message(StateFilter(RechargeState.waiting_for_pay_value), F.from_user.id.in_(ADMIN_IDS))
    async def process_pay_value(message: Message, state: FSMContext):
        data = await state.get_data()
        key = data['edit_key']
        val = message.text

        # Try to convert to float if it's a number setting
        if key in ["USDT_TO_INR", "MIN_USDT", "USDT_RATE"]:
            try:
                val = float(val)
            except ValueError:
                return await message.answer("❌ This value must be a number. Try again:")
                
        settings_col.update_one({"_id": "bot_settings"}, {"$set": {key: val}}, upsert=True)
        await message.answer(f"✅ <b>{key}</b> updated to <code>{val}</code>", parse_mode="HTML")
        await state.clear()

    @dp.message(Command("funds"), F.from_user.id.in_(ADMIN_IDS))
    async def cmd_funds(message: Message):
        settings = get_settings(settings_col)
        kb = InlineKeyboardBuilder()
        
        p_status = "🟢 ON" if settings.get("payments_active", True) else "🔴 OFF"
        u_status = "🟢 ON" if settings.get("upi_active", True) else "🔴 OFF"
        c_status = "🟢 ON" if settings.get("usdt_active", True) else "🔴 OFF"

        kb.button(text=f"All Payments: {p_status}", callback_data="toggle_funds:payments_active")
        kb.button(text=f"UPI Payments: {u_status}", callback_data="toggle_funds:upi_active")
        kb.button(text=f"USDT Payments: {c_status}", callback_data="toggle_funds:usdt_active")
        kb.adjust(1)
        
        await message.answer("🛡️ <b>Payment Gateways Control</b>", parse_mode="HTML", reply_markup=kb.as_markup())

    @dp.callback_query(F.data.startswith("toggle_funds:"), F.from_user.id.in_(ADMIN_IDS))
    async def toggle_funds_cb(cq: CallbackQuery):
        key = cq.data.split(":")[1]
        settings = get_settings(settings_col)
        current = settings.get(key, True)
        settings_col.update_one({"_id": "bot_settings"}, {"$set": {key: not current}})
        await cq.answer(f"Toggled {key}")
        # Re-trigger the menu update
        await cmd_funds(cq.message)
        await cq.message.delete()
        


    
            # ==============================
    # AUTO UPI FLOW (QR + API CHECK)
    # ==============================
    
    @dp.callback_query(F.data == "recharge_auto_upi")
    async def recharge_auto_upi(cq: CallbackQuery, state: FSMContext):
        await cq.message.delete()
        msg = await cq.message.answer("💰 Enter amount to recharge (Min: ₹5):")
        await state.update_data(last_msg=msg.message_id)
        await state.set_state(RechargeState.auto_upi_amount)

    # 2. Generate QR and Wait for Payment

    @dp.message(StateFilter(RechargeState.auto_upi_amount))
    async def auto_upi_amt(message: Message, state: FSMContext):
        await message.delete()
        data = await state.get_data()
        try:
            await bot.delete_message(message.chat.id, data.get("last_msg"))
        except: 
            pass

        # Added .strip() so " 500 " doesn't fail isdigit()
        if not message.text.strip().isdigit():
            msg = await message.answer("❌ Invalid amount. Please enter a valid number (Min 5).")
            await state.update_data(last_msg=msg.message_id)
            return

        amount = int(message.text.strip())
        if amount < 5:
            msg = await message.answer("❌ Minimum recharge amount is ₹5.")
            await state.update_data(last_msg=msg.message_id)
            return

        user_id = message.from_user.id
        upi_url = f"upi://pay?pa={UPI_ID2}&am={amount}&cu=INR"

        # --- Generate Custom Image using Pillow ---
        
        # 1. Generate the base QR Code
        qr = qrcode.QRCode(box_size=12, border=2)
        qr.add_data(upi_url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert('RGB')
        qr_w, qr_h = qr_img.size

        # 2. Create the blank portrait canvas (width: 800, height: 1000)
        bg_w, bg_h = 800, 1000
        bg = Image.new('RGB', (bg_w, bg_h), 'white')
        draw = ImageDraw.Draw(bg)

        # 3. Load Fonts & Track Status
        try:
            font_title = ImageFont.truetype("arialbd.ttf", 45) # Bold
            font_sub = ImageFont.truetype("arial.ttf", 35)
            font_small = ImageFont.truetype("arial.ttf", 25)
            has_ttf = True
        except IOError:
            print("[DEBUG] TTF fonts not found, falling back to default bitmap font.")
            font_title = ImageFont.load_default()
            font_sub = ImageFont.load_default()
            font_small = ImageFont.load_default()
            has_ttf = False

        # 4. Draw the Top Text (Safe Anchor Check)
        if has_ttf:
            draw.text((bg_w/2, 150), f"Payment of ₹{amount}", fill="black", font=font_title, anchor="mm")
            draw.text((bg_w/2, 210), f"for {user_id}", fill="gray", font=font_sub, anchor="mm")
        else:
            # Fallback coordinates for default fonts (they don't support anchor)
            draw.text((bg_w/2 - 120, 150), f"Payment of ₹{amount}", fill="black", font=font_title)
            draw.text((bg_w/2 - 120, 210), f"for {user_id}", fill="gray", font=font_sub)

        # 5. Draw a navy blue border and paste the QR code in the center
        border_size = 10
        qr_x = (bg_w - qr_w) // 2
        qr_y = (bg_h - qr_h) // 2
        
        draw.rectangle(
            [qr_x - border_size, qr_y - border_size, qr_x + qr_w + border_size, qr_y + qr_h + border_size],
            fill="#000080" # Navy blue accent border
        )
        bg.paste(qr_img, (qr_x, qr_y))

        # 6. Draw the Bottom Text (Safe Anchor Check)
        if has_ttf:
            draw.text((bg_w/2, qr_y + qr_h + 80), "Scan to pay with any UPI app", fill="black", font=font_sub, anchor="mm")
            draw.text((bg_w/2, qr_y + qr_h + 140), "Only pay to this QR then send the UTR/TXN ID below.", fill="gray", font=font_small, anchor="mm")
        else:
            draw.text((bg_w/2 - 150, qr_y + qr_h + 80), "Scan to pay with any UPI app", fill="black", font=font_sub)
            draw.text((bg_w/2 - 200, qr_y + qr_h + 140), "Only pay to this QR then send the UTR/TXN ID below.", fill="gray", font=font_small)

        # ... (Keep the rest of your image saving and sending logic exactly the same)


        # 7. Save the image to an in-memory buffer
        img_buffer = io.BytesIO()
        bg.save(img_buffer, format="JPEG", quality=95)
        img_buffer.seek(0)
        
        # Create a Telegram-compatible file object
        photo_file = BufferedInputFile(img_buffer.read(), filename="payment_qr.jpg")

        # --- Send the Message ---
        
        kb = InlineKeyboardBuilder()
        kb.button(text="I've Paid", callback_data="auto_upi_paid", icon_custom_emoji_id="5409029658794537988", style="success")
        kb.button(text="Cancel", callback_data="back_main", icon_custom_emoji_id="5409284148491726576", style="danger")
        kb.adjust(1)

        msg = await message.answer_photo(
            photo=photo_file,
            caption=f"📲 <b>Auto UPI Payment</b>\n\nID: <code>{UPI_ID2}</code>\nAmount: <b>₹{amount}</b>\n\nPlease submit your UTR below after successful payment.",
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )
        await state.update_data(last_msg=msg.message_id, amount=amount)
        
    # 3. Ask for UTR / Txn ID
    @dp.callback_query(F.data == "auto_upi_paid")
    async def auto_upi_paid_btn(cq: CallbackQuery, state: FSMContext):
        await cq.message.delete()
        msg = await cq.message.answer(
            "📝 <b>Enter UTR / Transaction ID</b>\n\n"
            "• If you used <b>FamPay</b>, enter the ID starting with FMP (e.g., <code>FMPIB5505640099</code>).\n"
            "• For other apps (PhonePe, Paytm, GPay), enter the 12-digit <b>UTR</b> number.\n\n"
            "Send your UTR/Txn ID below:",
            parse_mode="HTML"
        )
        await state.update_data(last_msg=msg.message_id)
        await state.set_state(RechargeState.auto_upi_utr)

    # 4. Verify Payment via API
    @dp.message(StateFilter(RechargeState.auto_upi_utr))
    async def verify_auto_upi(message: Message, state: FSMContext):
        # Clean up previous messages
        await message.delete()
        data = await state.get_data()
        try:
            await bot.delete_message(message.chat.id, data.get("last_msg"))
        except: pass

        utr_input = message.text.strip()
        amount = data.get("amount")

        wait_msg = await message.answer("🔄 Verifying your payment, please wait...")

        # Security Check 1: Prevent double spending from input string
        existing_txn = txns_col.find_one({
            "$or": [{"utr": utr_input}, {"transaction_id": utr_input}],
            "status": "approved"
        })
        if existing_txn:
            await wait_msg.edit_text("❌ This UTR/Transaction ID has already been used.")
            await state.clear()
            return

        # Prepare API URL
        mail = "Mohinisharma456e4@gmail"
        apppass = "gxihciotklhjyblm"
        if utr_input.startswith("FMP"):
            api_url = f"https://subdict.qzz.io/check?mail={mail}&apppass={apppass}&txnid={utr_input}&amount={amount}"
        else:
            api_url = f"https://subdict.qzz.io/check?mail={mail}&apppass={apppass}&utr={utr_input}&amount={amount}"

        # Make the API call
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as resp:
                    res_json = await resp.json()
        except Exception as e:
            await wait_msg.edit_text("Payment Not found, Please try again later or pay manually.")
            await state.clear()
            return

        # Process the API Result
        if res_json.get("status") == "found" or res_json.get("result") == "Found":
            found_utr = str(res_json.get("utr", "")).strip()
            found_txnid = str(res_json.get("transaction_id", "")).strip()

            # Security Check 2: Bind UTR & TxnID and ensure NEITHER has been used
            # We build the query dynamically to ignore empty values from the API
            or_conditions = []
            if found_utr and found_utr != "None":
                or_conditions.append({"utr": found_utr, "status": "approved"})
            if found_txnid and found_txnid != "None":
                or_conditions.append({"transaction_id": found_txnid, "status": "approved"})

            if or_conditions:
                double_check = txns_col.find_one({"$or": or_conditions})
                if double_check:
                    await wait_msg.edit_text("❌ Scam Detected: This transaction has already been claimed.")
                    await state.clear()
                    return

            # Success: Mark as approved in DB
            txns_col.insert_one({
                "user_id": message.from_user.id,
                "username": message.from_user.username,
                "full_name": message.from_user.full_name,
                "amount": amount,
                "method": "auto_upi",
                "utr": found_utr,
                "transaction_id": found_txnid,
                "status": "approved",
                "created_at": datetime.datetime.utcnow()
            })

            # Add Balance
            users_col.update_one({"_id": message.from_user.id}, {"$inc": {"balance": amount}})
            await process_referral_bonus(bot, users_col, message.from_user.id, amount)

            # Get New Balance
            user_doc = users_col.find_one({"_id": message.from_user.id})
            new_bal = user_doc.get("balance", amount)

            # Notify User
            await wait_msg.edit_text(
                f"✅ <b>Payment Received!</b>\n\n"
                f"📝 <b>Txn ID:</b> <code>{utr_input}</code>\n"
                f"💰 <b>Added Amount:</b> ₹{amount}\n"
                f"💼 <b>New Balance:</b> ₹{new_bal:.2f}",
                parse_mode="HTML"
            )

            # Notify Admin
            import html
            safe_name = html.escape(message.from_user.full_name)
            tx_time = res_json.get("date_time", "Unknown Time")
            
            admin_text = (
                f"🟢 <b>New Auto UPI Payment</b>\n\n"
                f"👤 <b>Name:</b> {safe_name}\n"
                f"🆔 <b>User ID:</b> <code>{message.from_user.id}</code>\n"
                f"🔗 <b>Username:</b> @{message.from_user.username or 'N/A'}\n"
                f"💵 <b>Amount:</b> ₹{amount}\n"
                f"📝 <b>UTR/Txn:</b> <code>{utr_input}</code>\n"
                f"⏰ <b>Time:</b> {tx_time}"
            )

            for admin in ADMIN_IDS:
                try:
                    await bot.send_message(admin, admin_text, parse_mode="HTML")
                except:
                    pass
        else:
            await wait_msg.edit_text("❌ <b>Payment not found!</b>\n\nPlease check your UTR/Txn ID, try again, or use Manual UPI.")

        await state.clear()

    # ==============================
    # 5. CRYPTO MANUAL FLOW
    # ==============================
    # --- A. Selection Menu (Binance vs Cwallet) ---
    @dp.callback_query(F.data == "recharge_manual_menu")
    async def manual_menu(cq: CallbackQuery, state: FSMContext):
        settings = get_settings(settings_col)
        if not settings.get("usdt_active", True):
            return await cq.answer("⚠️ Crypto payments are currently unavailable.", show_alert=True)

        live_rate = await get_live_usdt_rate(settings.get("USDT_RATE", 90.0))
        await state.update_data(current_rate=live_rate)

        kb = InlineKeyboardBuilder()
        kb.button(text="Binance Pay", callback_data="manual_pay:Binance", icon_custom_emoji_id="5217811903685865303")
        kb.button(text="Cwallet", callback_data="manual_pay:Cwallet", icon_custom_emoji_id="6028517788606272241")
        kb.adjust(1)
        kb.button(text="Back", callback_data="recharge", icon_custom_emoji_id="5409284148491726576", style="danger")
        kb.adjust(2,1)

        await cq.message.edit_text(
            '<tg-emoji emoji-id="6134309528960768658">❌</tg-emoji> <b>Manual Crypto Deposit</b>\n\n'
            'Select the wallet you want to pay with:\n'
            f'<tg-emoji emoji-id="5409078930659357770">❌</tg-emoji> <b>Live Rate:</b> 1 USDT = ₹{live_rate:.2f}',
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )

    # --- B. Payment Details Display ---
    @dp.callback_query(F.data.startswith("manual_pay:"))
    async def manual_pay_details(cq: CallbackQuery, state: FSMContext):
        method = cq.data.split(":")[1]
        settings = get_settings(settings_col)
        data = await state.get_data()
        live_rate = data.get("current_rate", settings.get("USDT_RATE", 90.0))

        pay_id = settings.get("BINANCE_PAY_ID") if method == "Binance" else settings.get("CWALLET_ID")
        icon = "🔶" if method == "Binance" else "🧧"

        await state.update_data(manual_method=method)

        kb = InlineKeyboardBuilder()
        kb.button(text="I've Paid", callback_data="manual_paid_confirm", icon_custom_emoji_id="5409029658794537988", style="success")
        kb.button(text="Back", callback_data="recharge_manual_menu", icon_custom_emoji_id="5409284148491726576", style="danger")
        kb.button(text="❌ Cancel", callback_data="back_main")
        kb.adjust(1)

        await cq.message.edit_text(
            f"{icon} <b>Deposit via {method}</b>\n\n"
            f"🆔 <b>ID:</b> <code>{pay_id}</code>\n"
            f"💱 <b>Rate:</b> 1 USDT = ₹{live_rate:.2f}\n\n"
            f"⚠️ <b>Instructions:</b>\n"
            f"1. Copy the ID above.\n"
            f"2. Send <b>USDT (TRC20/BEP20)</b> only.\n"
            f"3. Click 'I've Paid' immediately after transfer.",
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )

        
    # --- C. Request Screenshot ---
    @dp.callback_query(F.data == "manual_paid_confirm")
    async def manual_ask_ss(cq: CallbackQuery, state: FSMContext):
        await cq.message.delete()
        msg = await cq.message.answer(
            "📸 <b>Upload Screenshot</b>\n\n"
            "Please send the payment screenshot/proof now.\n"
            "<i>(Send the image directly)</i>",
            parse_mode="HTML"
        )
        await state.update_data(last_msg=msg.message_id)
        await state.set_state(RechargeState.manual_screenshot)

    # --- D. Handle Screenshot & Ask Amount ---
    @dp.message(StateFilter(RechargeState.manual_screenshot))
    async def manual_get_ss(message: Message, state: FSMContext):
        # 1. Validation: If not a photo, reset flow (Anti-Spam logic)
        if not message.photo:
            await state.clear()
            await message.answer(
                "⚠️ <b>Invalid Format.</b>\n\n"
                "Please start the recharge process again the recharge process again.",
                parse_mode="HTML"
            )
            return

        # 2. Save Photo ID
        data = await state.get_data()
        try:
            await bot.delete_message(message.chat.id, data.get("last_msg"))
        except:
            pass

        photo_id = message.photo[-1].file_id
        await state.update_data(proof_photo=photo_id)

        # 3. Ask for USDT Amount
        msg = await message.answer(
            "💵 <b>Enter Amount</b>\n\n"
            "How much <b>USDT</b> did you send?\n"
            "<i>(Example: 10 or 50.5)</i>",
            parse_mode="HTML"
        )
        await state.update_data(last_msg=msg.message_id)
        await state.set_state(RechargeState.manual_usdt_amount)

    # --- E. Handle Amount, Calc INR & Notify Admin ---
    @dp.message(StateFilter(RechargeState.manual_usdt_amount))
    async def manual_finalize(message: Message, state: FSMContext):
        data = await state.get_data()
        try:
            await bot.delete_message(message.chat.id, data.get("last_msg"))
        except:
            pass

        # 1. Validation: If not a number, reset flow (Anti-Spam logic)
        try:
            usdt_amt = float(message.text)
            if usdt_amt <= 0:
                raise ValueError
        except ValueError:
            await state.clear()
            await message.answer(
                "⚠️ <b>Invalid Amount.</b>\n\n"
                "Please start the recharge process again.",
                parse_mode="HTML"
            )
            return

        # 2. Calculate INR
        current_rate = data.get("current_rate", 90.0)
        inr_amt = round(usdt_amt * current_rate, 2)
        method_name = data.get("manual_method", "Crypto")
        

        # 3. Save to DB
        # Note: We save 'amount' as INR because your admin approve function adds 'amount' to balance
        txn_id = txns_col.insert_one({
            "user_id": message.from_user.id,
            "username": message.from_user.username,
            "full_name": message.from_user.full_name,
            "amount": inr_amt,          # This will be added to balance
            "usdt_amount": usdt_amt,    # For reference
            "method": f"{method_name} Manual",
            "status": "pending",
            "created_at": datetime.datetime.utcnow()
        }).inserted_id

        # 4. Notify Admins
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Approve", callback_data=f"approve_txn:{txn_id}")
        kb.button(text="❌ Decline", callback_data=f"decline_txn:{txn_id}")

        admin_caption = (
            f"💎 Manual Crypto Request\n"
            f"👤 User: {escape(message.from_user.full_name)}\n"
            f"🆔 ID: {message.from_user.id}\n"
            f"🏦 Method: {method_name}\n"
            f"---------------------------\n"
            f"🪙 Sent: {usdt_amt} USDT\n"
            f"🇮🇳 Credit: {fmt_curr(inr_amt)} (Calc)\n"
            f"---------------------------"
        )

        for admin in ADMIN_IDS:
            try:
                await bot.send_photo(
                    admin,
                    photo=data.get("proof_photo"),
                    caption=admin_caption,
                    reply_markup=kb.as_markup()
                )
            except Exception as e:
                print(f"Admin Send Error: {e}")

        # 5. Success Message to User
        await message.answer(
            "✅ <b>Request Submitted</b>\n\n"
            f"Sent: {usdt_amt} USDT\n"
            f"Will Receive: {fmt_curr(inr_amt)}\n\n"
            "⏳ Please wait for admin approval.",
            parse_mode="HTML"
        )
        await state.clear()

    # ==============================
    # 3. CRYPTO FLOW (OXAPAY)
    # ==============================
    @dp.callback_query(F.data == "recharge_crypto")
    async def recharge_crypto(cq: CallbackQuery, state: FSMContext):
        settings = get_settings(settings_col)
        if not settings.get("usdt_active", True):
            return await cq.answer("⚠️ Crypto payments are currently unavailable.", show_alert=True)
            
        await cq.message.delete()
        min_usdt = settings.get("MIN_USDT", 0.1)
        msg = await cq.message.answer(f"🪙 Enter amount in USDT (Min: {min_usdt}):")
        await state.update_data(last_msg=msg.message_id)
        await state.set_state(RechargeState.crypto_amount)


    @dp.message(StateFilter(RechargeState.crypto_amount))
    async def crypto_amt(message: Message, state: FSMContext):
        # 1. Cleanup UI
        await message.delete()
        data = await state.get_data()
        try:
            await bot.delete_message(message.chat.id, data.get("last_msg"))
        except:
            pass

        # FETCH SETTINGS HERE TO FIX THE NAME ERROR
        settings = get_settings(settings_col)
        min_usdt = settings.get("MIN_USDT", 0.1)

        # 2. Validate
        try:
            # Added .strip() to handle accidental spaces
            usdt = float(message.text.strip()) 
            if usdt < min_usdt:
                raise ValueError(f"Amount {usdt} is less than minimum {min_usdt}")
        except Exception as e:
            # THIS LOGS THE ACTUAL ERROR TO YOUR CONSOLE
            print(f"[DEBUG] Crypto Amount Validation Error: {repr(e)}") 
            msg = await message.answer(f"❌ Invalid amount. Minimum is {min_usdt} USDT.")
            await state.update_data(last_msg=msg.message_id)
            return

        # 3. Generate Invoice
        wait_msg = await message.answer("⌛ Generating Invoice...")
        order_id = f"Oxa_{message.from_user.id}_{int(datetime.datetime.now().timestamp())}"
        res = create_invoice(usdt, order_id)


        if not res["success"]:
            await wait_msg.edit_text(f"❌ API Error: {res['error']}")
            await state.clear()
            return

                # 4. Extract Data (New API Structure)
        settings = get_settings(settings_col)
        live_rate = await get_live_usdt_rate(settings.get("USDT_TO_INR", 93.0))
        
        inv_data = res["data"]
        track_id = str(inv_data["track_id"])
        pay_url = inv_data["payment_url"]
        inr_val = round(usdt * live_rate, 2)


        # 5. Save to DB
        crypto_col.insert_one({
            "user_id": message.from_user.id,
            "track_id": track_id,
            "amount_usdt": usdt,
            "amount_inr": inr_val,
            "status": "pending",
            "created_at": datetime.datetime.utcnow()
        })

        # 6. Show Invoice
        kb = InlineKeyboardBuilder()
        kb.button(text="💳 Pay Now", url=pay_url)
        kb.button(text="✅ I Have Paid", callback_data=f"check_crypto:{track_id}")
        kb.button(text="❌ Cancel", callback_data=f"cancel_crypto:{track_id}")
        kb.adjust(1)

        await wait_msg.delete()
        await message.answer(
            f"📋 <b>Crypto Invoice</b>\n\n"
            f"💵 Amount: <b>{usdt} USDT</b>\n"
            f"💰 INR Value: {fmt_curr(inr_val)}\n"
            f"⏳ Expires in: 30 Mins\n\n"
            f"⚠️ Send EXACT amount shown on the link.",
            parse_mode="HTML",
            reply_markup=kb.as_markup()
        )
        await state.clear()

    @dp.callback_query(F.data.startswith("check_crypto"))
    async def check_crypto(cq: CallbackQuery):
        track_id = cq.data.split(":")[1]

        # Check DB
        inv = crypto_col.find_one({"track_id": track_id})
        if not inv:
            await cq.answer("Invoice not found.", show_alert=True)
            return

        if inv["status"] == "paid":
            await cq.answer("Already Paid!", show_alert=True)
            return

        # Check API
        api_res = check_invoice(track_id)

        # The Check Status endpoint usually returns "status": "Paid" or "Waiting" inside "data" or root
        # Let's inspect the common structure for check status:
        # { "result": 100, "message": "...", "data": { "status": "Paid", ... } }
        # OR simply { "status": "Paid" ... } depending on exact endpoint version.
        # We will check deeply.
        remote_status = "unknown"
        if "data" in api_res and "status" in api_res["data"]:
            remote_status = str(api_res["data"]["status"]).lower()
        elif "status" in api_res:
            remote_status = str(api_res["status"]).lower()

        if remote_status in ["paid", "complete", "confirmed"]:
            # Success
            crypto_col.update_one({"track_id": track_id}, {"$set": {"status": "paid"}})
            users_col.update_one({"_id": inv["user_id"]}, {"$inc": {"balance": inv["amount_inr"]}})
            await process_referral_bonus(bot, users_col, inv["user_id"], inv["amount_inr"])

            usdt = inv["amount_usdt"]
            inr = inv["amount_inr"]

            await cq.message.delete()
            await bot.send_message(
                inv["user_id"],
                f"✅ <b>Payment Received!</b>\n\n"
                f"💵 Amount Added: <b>{usdt} USDT</b>\n"
                f"💰 INR Value: ₹{inv['amount_inr']}\n"
                f"➕ Track ID - <code>{track_id}</code>",
            )

            admin_log_text = (
                f"🚀 <b>New Crypto Payment Received</b>\n\n"
                f"👤 <b>User:</b> {escape(cq.from_user.full_name)}\n"
                f"🆔 <b>User ID:</b> <code>{inv['user_id']}</code>\n"
                f"🔗 <b>Username:</b> @{cq.from_user.username or 'N/A'}\n"
                f"🪙 <b>Amount Added:</b> {usdt} USDT\n"
                f"🇮🇳 <b>INR Value:</b> ₹{inr}\n"
                f"🧾 <b>Track ID:</b> <code>{track_id}</code>"
            )

            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(admin_id, admin_log_text, parse_mode="HTML")
                except Exception as e:
                    print(f"Error sending log to admin {admin_id}: {e}")

        elif remote_status == "confirming":
            await cq.answer("⏳ Payment detected but confirming on blockchain. Please wait.", show_alert=True)
        else:
            await cq.answer(f"Status: {remote_status.capitalize()}\nPayment not confirmed yet.", show_alert=True)

    @dp.callback_query(F.data.startswith("cancel_crypto"))
    async def cancel_crypto(cq: CallbackQuery):
        await cq.message.delete()
        await cq.answer("Cancelled")

    # ==============================
    # 4. ADMIN
    # ==============================
    @dp.callback_query(F.data.startswith("approve_txn"))
    async def approve_txn(cq: CallbackQuery):
        txn_id = cq.data.split(":")[1]
        txn = txns_col.find_one({"_id": ObjectId(txn_id)})

        if txn and txn["status"] == "pending":
            users_col.update_one({"_id": txn["user_id"]}, {"$inc": {"balance": txn["amount"]}})
            txns_col.update_one({"_id": ObjectId(txn_id)}, {"$set": {"status": "approved"}})

            await bot.send_message(txn["user_id"], f"✅ Deposit Approved\n\n💸 Balance added: ₹{txn['amount']}")
            await process_referral_bonus(bot, users_col, txn["user_id"], txn["amount"])

            await cq.message.edit_caption(caption=cq.message.caption + "\n\n✅ APPROVED", parse_mode="None")
            await cq.answer("Done")

    @dp.callback_query(F.data.startswith("decline_txn"))
    async def decline_txn(cq: CallbackQuery):
        txn_id = cq.data.split(":")[1]
        txns_col.update_one({"_id": ObjectId(txn_id)}, {"$set": {"status": "declined"}})

        await cq.message.edit_caption(caption=cq.message.caption + "\n\n❌ DECLINED", parse_mode="None")
        await cq.answer("Declined")
