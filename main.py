#!/usr/bin/env python
# pylint: disable=unused-argument
# This program is dedicated to the public domain under the CC0 license.

"""
Beauty Salon Booking Bot with date + paginated timeslot picker and admin approval.
"""
import os
# from dotenv import load_dotenv
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import OrderedDict
from html import escape

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    filters,
    MessageHandler,
)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Conversation stages
# … up at the top …
# Conversation stages
SELECT_CATEGORY, SELECT_SERVICE, SELECT_DATE, SELECT_SLOT, AWAIT_REJECT_REASON = range(5)


# Callback data identifiers
WOMEN, MEN, \
W_HAIRCUT, W_COLORING, \
M_HAIRCUT, M_SHAVE = range(6)


# Booking storage
BOOKINGS = OrderedDict()
NEXT_BOOKING_ID = 1

# load_dotenv()
TOKEN = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID = int(os.environ["ADMIN_CHAT_ID"])

SERVICE_MAP = {
    W_HAIRCUT: "Женские Волосы",
    W_COLORING: "Женский Макияж",
    M_HAIRCUT: "Мужская Стрижка",
    M_SHAVE:   "Мужские Барберские Услуги и Борода",
}
# Russian month names in nominative
MONTH_NAMES = {
    1: "Январь",   2: "Февраль", 3: "Март",     4: "Апрель",
    5: "Май",      6: "Июнь",   7: "Июль",     8: "Август",
    9: "Сентябрь", 10: "Октябрь",11: "Ноябрь",  12: "Декабрь",
}
LOCAL_TZ = ZoneInfo("Asia/Yerevan")

def format_slot(iso_slot: str) -> str:
    """
    Convert "2025-06-26 12:00" → "Июнь 26 12:00"
    """
    # Parse the ISO datetime string
    dt = datetime.fromisoformat(iso_slot)
    # Month name + day
    month_day = f"{MONTH_NAMES[dt.month]} {dt.day}"
    # Hours:minutes zero-padded
    return f"{month_day} {dt.hour:02d}:{dt.minute:02d}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    keyboard = [
        [
            InlineKeyboardButton("👩 Для Женщин", callback_data=str(WOMEN)),
            InlineKeyboardButton("👨 Для Мужчин",   callback_data=str(MEN)),
        ]
    ]
    await update.message.reply_text(
        "Добро Пожаловать в Solo Beauty!\nПожалуйста выберите категорию:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SELECT_CATEGORY

# method for restart the booking process, after one is finished
async def restart_booking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()  # stop the spinner

    # Build the same keyboard as in /start
    keyboard = [
        [
            InlineKeyboardButton("👩 Для Женщин", callback_data=str(WOMEN)),
            InlineKeyboardButton("👨 Для Мужчин",   callback_data=str(MEN)),
        ]
    ]
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="Добро Пожаловать в Solo Beauty!\nПожалуйста выберите категорию:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SELECT_CATEGORY



async def women_services(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [
        [
            InlineKeyboardButton("💇 Волосы",  callback_data=str(W_HAIRCUT)),
            InlineKeyboardButton("🎨 Макияж", callback_data=str(W_COLORING)),
        ]
    ]
    await query.edit_message_text(
        "Пожалуйста Выберите Услугу:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SELECT_SERVICE


async def men_services(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [
        [
            InlineKeyboardButton("💈 Стрижка", callback_data=str(M_HAIRCUT)),
            InlineKeyboardButton("🪒 Барберские Услуги и Борода",   callback_data=str(M_SHAVE)),
        ]
    ]
    await query.edit_message_text(
        "Пожалуйста Выберите Услугу:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SELECT_SERVICE


async def service_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store service, then show date choices (today + next 2 days)."""
    query = update.callback_query
    await query.answer()
    choice = int(query.data)
    context.user_data['service'] = SERVICE_MAP.get(choice, "Unknown service")

    # build date buttons
    today = datetime.now(LOCAL_TZ).date()
    keyboard = []
    for offset in range(3):
        d = today + timedelta(days=offset)
        display = f"{MONTH_NAMES[d.month]} {d.day}"
        payload = d.isoformat()
        keyboard.append([InlineKeyboardButton(display, callback_data=f"date_{payload}")])

    await query.edit_message_text(
        "Пожалуйста выберите дату:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SELECT_DATE


async def date_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store chosen date and show first page of timeslots."""
    query = update.callback_query
    await query.answer()

    date_str = query.data.split("_", 1)[1]
    context.user_data['date'] = date_str
    context.user_data['slot_page'] = 0

    return await show_slot_page(query, context)


async def show_slot_page(query, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Helper to render a page of 9 timeslots with Next/Back."""
    date_str = context.user_data['date']
    page = context.user_data['slot_page']
    now      = datetime.now(LOCAL_TZ)                       # current local time

    # Parse it into a date for formatting
    d = datetime.fromisoformat(date_str).date()
    month_day = f"{MONTH_NAMES[d.month]} {d.day}"

    # generate all slots for that date at .0 and .5
    slots = []
    for hour in range(9, 20):
        slots.append(f"{hour:02d}:00")
        slots.append(f"{hour:02d}:30")
    if d == now.date():
        # compute “minutes since midnight” for right‐now
        current_minutes = now.hour * 60 + now.minute

        def slot_to_minutes(t: str) -> int:
            # t is "HH:MM"
            h, m = map(int, t.split(":"))
            return h * 60 + m

        # keep any slot whose minutes ≥ current_minutes
        slots = [t for t in slots if slot_to_minutes(t) >= current_minutes]

    per_page = 9
    start = page * per_page
    chunk = slots[start:start + per_page]

    keyboard = []
    for t in chunk:
        # display: "June 25 09:00"
        display = f"{month_day} {t}"
        # callback_data: keep ISO date + time so you can store it exactly
        payload = f"{date_str} {t}"
        keyboard.append([InlineKeyboardButton(display, callback_data=f"slot_{payload}")])

    # pagination buttons
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("← Back", callback_data="page_prev"))
    if start + per_page < len(slots):
        nav.append(InlineKeyboardButton("Next →", callback_data="page_next"))
    if nav:
        keyboard.append(nav)

    await query.edit_message_text(
        "Выберите время:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SELECT_SLOT


async def slot_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle Next/Back pagination clicks."""
    query = update.callback_query
    await query.answer()
    if query.data == "page_next":
        context.user_data['slot_page'] += 1
    else:
        context.user_data['slot_page'] -= 1
    return await show_slot_page(query, context)


async def slot_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Register booking, send to admin with full details + Accept/Reject buttons."""
    global NEXT_BOOKING_ID

    query = update.callback_query
    await query.answer()

    slot     = query.data.split("_", 1)[1]
    service  = context.user_data['service']
    user     = update.effective_user
    chat_id  = update.effective_chat.id

    # register
    booking_id = NEXT_BOOKING_ID
    NEXT_BOOKING_ID += 1
    BOOKINGS[booking_id] = {
        "user_chat_id": chat_id,
        "user_name":    user.full_name,
        "username":     user.username or "(no username)",
        "service":      service,
        "timeslot":     slot,
    }

    friendly = format_slot(slot)
    raw_username = user.username or '-'
    safe_username = escape(raw_username)
    # build admin message
    admin_text = (
        f"🆕 <b>New Booking Request</b>\n"
        f"<b>Booking ID:</b> <code>{booking_id}</code>\n"
        f"<b>Name:</b> {escape(user.full_name)}\n"
        f"<b>Username:</b> @{safe_username or '—'}\n"
        f"<b>Service:</b> {service}\n"
        f"<b>Timeslot:</b> {friendly}"
    )
    keyboard = [
        [
            InlineKeyboardButton("✅ Принять", callback_data=f"accept_{booking_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{booking_id}"),
        ]
    ]

    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=admin_text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    await query.edit_message_text("👌 Ваш запрос рассматривается, вам скоро ответят ❤️")
    context.user_data.clear()
    return ConversationHandler.END

async def getid(update, context):
    # replies with the current chat’s ID
    await update.message.reply_text(f"Chat ID is: {update.effective_chat.id}")




async def admin_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """On Accept: notify user & update admin msg.
       On Reject: prompt admin for a reason."""
    query = update.callback_query
    await query.answer()

    action, bid_str = query.data.split("_", 1)
    booking_id = int(bid_str)
    booking = BOOKINGS.get(booking_id)

    if not booking:
        await query.edit_message_text("⚠️ Запрос не найден или закрыт.")
        return  ConversationHandler.END


    slot_display = format_slot(booking["timeslot"])
    # Prepare the common footer of booking details
    raw_username = booking.get("username") or "—"  # never None
    safe_username = escape(raw_username)  # now safe_username is a string

    footer = (
        f"<b>Booking ID:</b> <code>{booking_id}</code>\n"
        f"<b>Name:</b> {escape(booking['user_name'])}\n"
        f"<b>Username:</b> @{safe_username}\n"
        f"<b>Service:</b> {booking['service']}\n"
        f"<b>Timeslot:</b> {slot_display}"
    )

    if action == "accept":
        # 1) Notify user
        await context.bot.send_message(
            chat_id=booking["user_chat_id"],
            text=(
                f"✅ Ваш запрос <b>принят</b>!\n\n{footer}"
            ),
            parse_mode="HTML",
        )
        await context.bot.send_message(
            chat_id=booking["user_chat_id"],
            text="🔄 Хотите сделать ещё один запрос?",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📅 Новый Запрос", callback_data="restart")
            ]])
        )
        # 2) Remove booking
        BOOKINGS.pop(booking_id, None)
        # 3) Update admin’s message
        await query.edit_message_text(
            text=f"✅ <b>Запрос Принят!</b>\n\n{footer}",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    # else: action == "reject" → ask for a reason
    context.user_data['pending_reject'] = booking_id
    await query.edit_message_text(
        text=(
            f"❌ <b>Booking pending rejection</b>\n\n{footer}\n\n"
            "Пожалуйста напишите <b>причину отклонения</b> (или отправьте <code>/skip</code> для отклонения без комментариев):"
        ),
        parse_mode="HTML"
    )
    return AWAIT_REJECT_REASON


async def handle_reject_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive admin’s rejection reason, notify user & finalize admin message."""
    text = update.message.text
    booking_id = context.user_data.pop('pending_reject', None)
    booking = BOOKINGS.pop(booking_id, None)

    if not booking:
        await update.message.reply_text("⚠️ Этот запрос больше не доступен.")
        return ConversationHandler.END

    raw_username = booking.get("username") or "—"  # never None
    safe_username = escape(raw_username)  # now safe_username is a string
    # Build footer again
    footer = (
        f"<b>Booking ID:</b> <code>{booking_id}</code>\n"
        f"<b>Name:</b> {escape(booking['user_name'])}\n"
        f"<b>Username:</b> @{safe_username}\n"
        f"<b>Service:</b> {booking['service']}\n"
        f"<b>Timeslot:</b> {booking['timeslot']}"
    )

    # Determine reason text
    reason = "" if text.strip() == "/skip" else f"\n\n<i>Причина:</i> {text}"

    # 1) Notify the user
    await context.bot.send_message(
        chat_id=booking["user_chat_id"],
        text=(
            f"❌ Ваш запрос был <b>отклонён</b>. {reason}\n\n{footer}"
        ),
        parse_mode="HTML",
    )

    # 2) Send confirmation back to the admin (as a new message)
    await update.message.reply_text(
        text=f"❌ <b>Запрос Отклонён.</b>{reason}\n\n{footer}",
        parse_mode="HTML",
    )
    await context.bot.send_message(
        chat_id=booking["user_chat_id"],
        text="🔄 Хотите сделать ещё один запрос?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📅 Новый Запрос", callback_data="restart")
        ]])
    )

    return ConversationHandler.END

def main() -> None:
    """Run the bot."""
    application = Application.builder().token(TOKEN).build()

    # Main user booking flow
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(restart_booking, pattern="^restart$")],
        states={
            SELECT_CATEGORY: [
                CallbackQueryHandler(women_services, pattern=f"^{WOMEN}$"),
                CallbackQueryHandler(men_services,   pattern=f"^{MEN}$"),
            ],
            SELECT_SERVICE: [
                CallbackQueryHandler(service_chosen, pattern=r"^[0-5]$"),
            ],
            SELECT_DATE: [
                CallbackQueryHandler(date_chosen,    pattern=r"^date_"),
            ],
            SELECT_SLOT: [
                CallbackQueryHandler(slot_pagination, pattern="^page_"),
                CallbackQueryHandler(slot_chosen,    pattern=r"^slot_"),
            ],
        },
        fallbacks=[CommandHandler("start", start),
                   CallbackQueryHandler(restart_booking,pattern="^restart$")],
    )
    application.add_handler(conv_handler)

    # Admin reject flow: ask for optional comment after pressing ❌
    reject_reason_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_response, pattern=r"^reject_\d+$")],
        states={
            AWAIT_REJECT_REASON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reject_reason),
                CommandHandler("skip", handle_reject_reason),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
        name="reject_reason_conv",
        persistent=False,
    )
    application.add_handler(reject_reason_conv)

    # Admin accept flow
    application.add_handler(
        CallbackQueryHandler(admin_response, pattern=r"^accept_\d+$")
    )

    # Utility
    application.add_handler(CommandHandler("getid", getid))

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
