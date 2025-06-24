#!/usr/bin/env python
# pylint: disable=unused-argument
# This program is dedicated to the public domain under the CC0 license.

"""
Beauty Salon Booking Bot with date + paginated timeslot picker and admin approval.
"""
import logging
from datetime import datetime, timedelta
from collections import OrderedDict

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

ADMIN_CHAT_ID = -4717273516  # your admin/group chat

# Booking storage
BOOKINGS = OrderedDict()
NEXT_BOOKING_ID = 1

SERVICE_MAP = {
    W_HAIRCUT: "Женская Стрижка",
    W_COLORING: "Женское Окрашивание",
    M_HAIRCUT: "Мужская Стрижка",
    M_SHAVE:   "Мужские Барберские Услуги и Борода",
}


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


async def women_services(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [
        [
            InlineKeyboardButton("💇 Стрижка",  callback_data=str(W_HAIRCUT)),
            InlineKeyboardButton("🎨 Окрашивание", callback_data=str(W_COLORING)),
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
    today = datetime.now().date()
    keyboard = []
    for offset in range(3):
        d = today + timedelta(days=offset)
        keyboard.append([InlineKeyboardButton(d.isoformat(), callback_data=f"date_{d}")])

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

    # generate all slots for that date at .0 and .5
    slots = []
    for hour in range(9, 24):
        slots.append(f"{date_str} {hour:02d}:00")
        slots.append(f"{date_str} {hour:02d}:30")

    per_page = 9
    start = page * per_page
    chunk = slots[start:start + per_page]

    keyboard = []
    for slot in chunk:
        keyboard.append([InlineKeyboardButton(slot, callback_data=f"slot_{slot}")])

    # pagination buttons
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("← Back", callback_data="page_prev"))
    if start + per_page < len(slots):
        nav.append(InlineKeyboardButton("Next →", callback_data="page_next"))
    if nav:
        keyboard.append(nav)

    await query.edit_message_text(
        "Select a timeslot:",
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

    # build admin message
    admin_text = (
        f"🆕 *New Booking Request*\n"
        f"*Booking ID:* `{booking_id}`\n"
        f"*Name:* {user.full_name}\n"
        f"*Username:* @{user.username or '—'}\n"
        f"*Service:* {service}\n"
        f"*Timeslot:* {slot}"
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
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    await query.edit_message_text("👌 Ваш запрос рассматривается, вам скоро ответят :)")
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
        return await query.edit_message_text("⚠️ Запрос не найден или закрыт.")

    # Prepare the common footer of booking details
    footer = (
        f"*Booking ID:* `{booking_id}`\n"
        f"*Name:* {booking['user_name']}\n"
        f"*Username:* @{booking['username']}\n"
        f"*Service:* {booking['service']}\n"
        f"*Timeslot:* {booking['timeslot']}"
    )

    if action == "accept":
        # 1) Notify user
        await context.bot.send_message(
            chat_id=booking["user_chat_id"],
            text=(
                f"✅ Ваш запрос *принят*!\n\n{footer}"
            ),
            parse_mode="Markdown",
        )
        # 2) Remove booking
        BOOKINGS.pop(booking_id, None)
        # 3) Update admin’s message
        return await query.edit_message_text(
            text=f"✅ *Запрос Принят!*\n\n{footer}",
            parse_mode="Markdown",
        )

    # else: action == "reject" → ask for a reason
    context.user_data['pending_reject'] = booking_id
    await query.edit_message_text(
        text=(
            f"❌ *Booking pending rejection*\n\n{footer}\n\n"
            "Пожалуйста напишите *причину отклонения* (или отправьте `/skip` для отклонения без комментариев):"
        ),
        parse_mode="Markdown"
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

    # Build footer again
    footer = (
        f"*Booking ID:* `{booking_id}`\n"
        f"*Name:* {booking['user_name']}\n"
        f"*Username:* @{booking['username']}\n"
        f"*Service:* {booking['service']}\n"
        f"*Timeslot:* {booking['timeslot']}"
    )

    # Determine reason text
    reason = "" if text.strip() == "/skip" else f"\n\n_Reason:_ {text}"

    # 1) Notify the user
    await context.bot.send_message(
        chat_id=booking["user_chat_id"],
        text=(
            f"❌ Ваш запрос был *отлконён*. {reason}\n\n{footer}"
        ),
        parse_mode="Markdown",
    )

    # 2) Send confirmation back to the admin (as a new message)
    await update.message.reply_text(
        text=f"❌ *Запрос Отлконён.*{reason}\n\n{footer}",
        parse_mode="Markdown",
    )

    return ConversationHandler.END

def main() -> None:
    """Run the bot."""
    application = Application.builder().token("7583080664:AAFdP9aIgFPf5Di4n9CIVvicXGpfG376ryU").build()

    # Main user booking flow
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
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
        fallbacks=[CommandHandler("start", start)],
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
