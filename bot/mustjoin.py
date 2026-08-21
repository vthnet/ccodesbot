from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramForbiddenError
from config import MUST_JOIN_CHANNEL


# Private channel details
PRIVATE_CHANNEL_ID = -1003283874092
PRIVATE_CHANNEL_LINK = "https://t.me/+V5EkDONAlLZmYzE1"
PRIVATE_CHANNEL_ID2 = -1003211594672

BOTUSER = "ccodes_bot"


WELCOME_TEXT = (
    "❌ 𝖸𝗈𝗎 𝗆𝗎𝗌𝗍 𝗌𝗎𝖻𝗌𝖼𝗋𝗂𝖻𝖾 𝗍𝗈 𝗍𝗁𝖾 𝗈𝖿𝖿𝗂𝖼𝗂𝖺𝗅 𝖻𝗈𝗍 𝖼𝗁𝖺𝗇𝗇𝖾𝗅 𝗍𝗈 𝗎𝗌𝖾 𝗍𝗁𝖾 𝖻𝗈𝗍...\n\n"
    "<blockquote>- <b>𝖢𝗁𝖺𝗇𝗇𝖾𝗅</b> - @vthchannel</blockquote>\n\n"
    "<b>• Press Verify Button below to check</b>"
)


async def check_join(client, message: types.Message):
    """
    Check if the user has joined both required channels.
    If not, send the join message and return False.

    If the user has blocked the bot, silently ignore the error.
    """
    try:
        user_id = message.from_user.id

        # Public channel check
        member1 = await client.get_chat_member(
            PRIVATE_CHANNEL_ID2,
            user_id
        )

        # Private channel check
        member2 = await client.get_chat_member(
            PRIVATE_CHANNEL_ID,
            user_id
        )

        if (
            member1.status in ["left", "kicked"]
            or member2.status in ["left", "kicked"]
        ):
            await send_join_message(message)
            return False

        return True

    except TelegramForbiddenError:
        # User blocked the bot — stay completely silent
        return False

    except Exception:
        # Other membership/check errors
        try:
            await send_join_message(message)
        except TelegramForbiddenError:
            # User blocked the bot
            pass

        return False


async def send_join_message(message: types.Message):
    """
    Send the must-join message.
    Silently ignore TelegramForbiddenError if the user blocked the bot.
    """
    kb = InlineKeyboardBuilder()

    kb.row(
        types.InlineKeyboardButton(
            text="𝖴𝗉𝖽𝖺𝗍𝖾𝗌",
            url="https://t.me/vthchannel",
            icon_custom_emoji_id="6129889801454754893"
        ),
        types.InlineKeyboardButton(
            text="updates",
            url="https://t.me/vthnet",
            icon_custom_emoji_id="6129889801454754893"
        )
    )

    kb.row(
        types.InlineKeyboardButton(
            text="Verify Join",
            callback_data="back_main",
            style="success",
            icon_custom_emoji_id="5951982867255924070"
        )
    )

    try:
        await message.answer(
            WELCOME_TEXT,
            reply_markup=kb.as_markup(),
            parse_mode="HTML"
        )
    except TelegramForbiddenError:
        # User blocked the bot — silently ignore
        pass