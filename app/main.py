from __future__ import annotations

import asyncio
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.types import ReplyKeyboardRemove
from aiogram.types.reply_keyboard_markup import ReplyKeyboardMarkup
from aiogram.types.inline_keyboard_markup import InlineKeyboardMarkup

from app.config import Settings, get_settings
from app.database import Database
from app.keyboards import (
    ADMIN_PANEL,
    ADMIN_PENDING_PAYMENTS,
    ADMIN_PRODUCTS,
    ADMIN_STATS,
    BACK,
    BUY_1000,
    BUY_10000,
    BUY_STARS,
    HELP,
    MAIN_MENU,
    MONEY_TO_STARS,
    PROFILE,
    STARS_TO_MONEY,
    TOP_UP,
    admin_menu,
    back_menu,
    buy_menu,
    main_menu,
    payment_admin_keyboard,
    support_keyboard,
)
from app.states import ConvertStates


router = Router()
SETTINGS: Settings | None = None
DB: Database | None = None


def money(value: Decimal | int | float) -> str:
    amount = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{amount:,.2f}".replace(",", " ")


def user_link(username: str | None) -> str:
    return f"@{username}" if username else "не указан"


def parse_positive_decimal(text: str) -> Decimal | None:
    normalized = text.replace(",", ".").strip()
    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        return None
    if amount <= 0:
        return None
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def get_db(_: Message | CallbackQuery) -> Database:
    if DB is None:
        raise RuntimeError("Database is not initialized")
    return DB


def get_price_per_star(_: Message | CallbackQuery) -> Decimal:
    if SETTINGS is None:
        raise RuntimeError("Settings are not initialized")
    return Decimal(str(SETTINGS.price_per_star_rub))


async def delete_safely(bot: Bot, chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        pass


async def send_page(
    message: Message,
    state: FSMContext,
    text: str,
    reply_markup: ReplyKeyboardMarkup | InlineKeyboardMarkup | ReplyKeyboardRemove | None = None,
) -> Message:
    data = await state.get_data()
    await delete_safely(message.bot, message.chat.id, data.get("page_message_id"))
    await delete_safely(message.bot, message.chat.id, message.message_id)
    sent = await message.answer(text, reply_markup=reply_markup)
    await state.update_data(page_message_id=sent.message_id)
    return sent


async def clear_flow_state(state: FSMContext) -> None:
    data = await state.get_data()
    page_message_id = data.get("page_message_id")
    await state.clear()
    if page_message_id:
        await state.update_data(page_message_id=page_message_id)


def welcome_text() -> str:
    return (
        "✨ <b>Добро пожаловать в магазин Telegram Stars!</b>\n\n"
        "Здесь можно купить звезды Telegram, посмотреть профиль и быстро перейти к поддержке.\n"
        "Выберите нужный раздел в меню ниже 👇"
    )


async def show_main_menu(message: Message, state: FSMContext) -> None:
    await clear_flow_state(state)
    user = await ensure_current_user(message)
    await send_page(message, state, welcome_text(), reply_markup=main_menu(user["is_admin"]))


async def ensure_current_user(message: Message):
    if SETTINGS is None:
        raise RuntimeError("Settings are not initialized")
    from_user = message.from_user
    return await get_db(message).ensure_user(
        telegram_id=from_user.id,
        username=from_user.username,
        full_name=from_user.full_name,
        is_admin=from_user.id in SETTINGS.admin_id_set,
    )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    user = await ensure_current_user(message)
    await clear_flow_state(state)
    await send_page(message, state, welcome_text(), reply_markup=main_menu(user["is_admin"]))


@router.message(F.text == BUY_STARS)
async def buy_stars_menu(message: Message, state: FSMContext) -> None:
    await clear_flow_state(state)
    await ensure_current_user(message)
    await send_page(
        message,
        state,
        "⭐ <b>Выберите кол-во звезд, которое хотите купить</b>",
        reply_markup=buy_menu(),
    )


@router.message(F.text == MONEY_TO_STARS)
async def ask_money_to_stars(message: Message, state: FSMContext) -> None:
    await ensure_current_user(message)
    await state.set_state(ConvertStates.waiting_money)
    await send_page(
        message,
        state,
        "💰 Введите сумму в рублях, и я посчитаю, сколько ⭐ получится купить.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(ConvertStates.waiting_money)
async def convert_money_to_stars(message: Message, state: FSMContext) -> None:
    if message.text == BACK:
        await show_main_menu(message, state)
        return

    amount = parse_positive_decimal(message.text or "")
    if amount is None:
        await send_page(
            message,
            state,
            "Введите положительную сумму числом, например: <b>1500</b>.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    price = get_price_per_star(message)
    stars = int(amount / price)
    await clear_flow_state(state)
    await send_page(
        message,
        state,
        f"За <b>{money(amount)} ₽</b> получится купить примерно <b>{stars}</b> ⭐\n"
        f"Курс: <b>{money(price)} ₽</b> за 1 ⭐",
        reply_markup=buy_menu(),
    )


@router.message(F.text == STARS_TO_MONEY)
async def ask_stars_to_money(message: Message, state: FSMContext) -> None:
    await ensure_current_user(message)
    await state.set_state(ConvertStates.waiting_stars)
    await send_page(
        message,
        state,
        "⭐ Введите количество звезд, и я посчитаю стоимость в 💰.",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(ConvertStates.waiting_stars)
async def convert_stars_to_money(message: Message, state: FSMContext) -> None:
    if message.text == BACK:
        await show_main_menu(message, state)
        return

    try:
        stars = int((message.text or "").strip())
    except ValueError:
        await send_page(
            message,
            state,
            "Введите количество звезд целым числом, например: <b>1000</b>.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    if stars <= 0:
        await send_page(message, state, "Количество звезд должно быть больше нуля.", reply_markup=ReplyKeyboardRemove())
        return

    price = get_price_per_star(message)
    amount = Decimal(stars) * price
    await clear_flow_state(state)
    await send_page(
        message,
        state,
        f"<b>{stars}</b> ⭐ стоят <b>{money(amount)} ₽</b>\n"
        f"Курс: <b>{money(price)} ₽</b> за 1 ⭐",
        reply_markup=buy_menu(),
    )


@router.message(F.text.in_({BUY_1000, BUY_10000}))
async def buy_fixed_pack(message: Message, state: FSMContext) -> None:
    user = await ensure_current_user(message)
    stars = 1000 if message.text == BUY_1000 else 10000
    product = await get_db(message).get_product_by_stars(stars)
    if not product:
        await send_page(message, state, "Этот товар сейчас недоступен. Попробуйте позже.", reply_markup=buy_menu())
        return

    success, updated_user = await get_db(message).buy_stars(
        user_id=user["telegram_id"],
        stars=product["stars"],
        amount=product["price_rub"],
        product_id=product["id"],
    )
    if not success:
        current_balance = updated_user["balance"] if updated_user else Decimal("0")
        await send_page(
            message,
            state,
            "На балансе недостаточно средств.\n"
            f"Цена: <b>{money(product['price_rub'])} ₽</b>\n"
            f"Ваш баланс: <b>{money(current_balance)} ₽</b>",
            reply_markup=buy_menu(),
        )
        return

    await send_page(
        message,
        state,
        f"✅ Покупка выполнена!\n\n"
        f"Начислено: <b>{product['stars']}</b> ⭐\n"
        f"Списано: <b>{money(product['price_rub'])} ₽</b>\n"
        f"Баланс: <b>{money(updated_user['balance'])} ₽</b>",
        reply_markup=main_menu(user["is_admin"]),
    )


@router.message(F.text == TOP_UP)
async def top_up(message: Message, state: FSMContext) -> None:
    await clear_flow_state(state)
    await ensure_current_user(message)
    if SETTINGS is None:
        raise RuntimeError("Settings are not initialized")
    await send_page(
        message,
        state,
        "💰 Для пополнения баланса напишите в поддержку.",
        reply_markup=support_keyboard(SETTINGS.support_username),
    )


@router.message(F.text == PROFILE)
async def profile(message: Message, state: FSMContext) -> None:
    user = await ensure_current_user(message)
    await send_page(
        message,
        state,
        "👤 <b>Профиль</b>\n\n"
        f"ID: <code>{user['telegram_id']}</code>\n"
        f"Username: <b>{user_link(user['username'])}</b>\n"
        f"Баланс: <b>{money(user['balance'])} ₽</b>\n"
        f"Куплено звезд: <b>{user['purchased_stars']}</b>",
        reply_markup=main_menu(user["is_admin"]),
    )


@router.message(F.text == HELP)
async def help_handler(message: Message, state: FSMContext) -> None:
    await ensure_current_user(message)
    if SETTINGS is None:
        raise RuntimeError("Settings are not initialized")
    await send_page(
        message,
        state,
        f"🛟 Если нужна помощь, напишите в поддержку: <a href=\"https://t.me/{SETTINGS.support_username.lstrip('@')}\">@{SETTINGS.support_username.lstrip('@')}</a>",
        reply_markup=back_menu(),
    )


@router.message(F.text == BACK)
@router.message(F.text == MAIN_MENU)
async def back_to_main(message: Message, state: FSMContext) -> None:
    await show_main_menu(message, state)


@router.message(F.text == ADMIN_PANEL)
async def admin_panel(message: Message, state: FSMContext) -> None:
    await clear_flow_state(state)
    user = await ensure_current_user(message)
    if not user["is_admin"]:
        await send_page(message, state, "У вас нет доступа к админ-панели.", reply_markup=main_menu(False))
        return
    await send_page(message, state, "⚙️ <b>Админ панель</b>", reply_markup=admin_menu())


@router.message(F.text == ADMIN_STATS)
async def admin_stats(message: Message, state: FSMContext) -> None:
    user = await ensure_current_user(message)
    if not user["is_admin"]:
        await send_page(message, state, "У вас нет доступа к этому разделу.", reply_markup=main_menu(False))
        return

    stats = await get_db(message).stats()
    await send_page(
        message,
        state,
        "📊 <b>Статистика</b>\n\n"
        f"Пользователей: <b>{stats['users_count']}</b>\n"
        f"Ожидают оплаты: <b>{stats['pending_payments']}</b>\n"
        f"Пополнено: <b>{money(stats['paid_amount'])} ₽</b>\n"
        f"Продано звезд: <b>{stats['sold_stars']}</b>\n"
        f"Сумма продаж: <b>{money(stats['sold_amount'])} ₽</b>",
        reply_markup=admin_menu(),
    )


@router.message(F.text == ADMIN_PENDING_PAYMENTS)
async def admin_pending_payments(message: Message, state: FSMContext) -> None:
    user = await ensure_current_user(message)
    if not user["is_admin"]:
        await send_page(message, state, "У вас нет доступа к этому разделу.", reply_markup=main_menu(False))
        return

    payments = await get_db(message).list_pending_payments()
    if not payments:
        await send_page(message, state, "Ожидающих заявок нет.", reply_markup=admin_menu())
        return

    data = await state.get_data()
    await delete_safely(message.bot, message.chat.id, data.get("page_message_id"))
    await delete_safely(message.bot, message.chat.id, message.message_id)
    last_message_id: int | None = None
    for payment in payments:
        sent = await message.answer(
            f"Заявка <b>#{payment['id']}</b>\n"
            f"Пользователь: <b>{payment['user_id']}</b> ({user_link(payment['username'])})\n"
            f"Сумма: <b>{money(payment['amount_rub'])} ₽</b>",
            reply_markup=payment_admin_keyboard(payment["id"]),
        )
        last_message_id = sent.message_id
    await state.update_data(page_message_id=last_message_id)


@router.message(F.text == ADMIN_PRODUCTS)
async def admin_products(message: Message, state: FSMContext) -> None:
    user = await ensure_current_user(message)
    if not user["is_admin"]:
        await send_page(message, state, "У вас нет доступа к этому разделу.", reply_markup=main_menu(False))
        return

    products = await get_db(message).get_products()
    rows = [
        f"• <b>{product['title']}</b>: {product['stars']} ⭐, {money(product['price_rub'])} ₽"
        for product in products
    ]
    await send_page(message, state, "🛒 <b>Товары</b>\n\n" + "\n".join(rows), reply_markup=admin_menu())


@router.callback_query(F.data.startswith("payment:"))
async def payment_action(callback: CallbackQuery) -> None:
    if SETTINGS is None:
        raise RuntimeError("Settings are not initialized")
    if callback.from_user.id not in SETTINGS.admin_id_set:
        await callback.answer("Нет доступа", show_alert=True)
        return

    _, action, raw_payment_id = callback.data.split(":")
    status = "paid" if action == "approve" else "cancelled"
    payment = await get_db(callback).set_payment_status(int(raw_payment_id), status)
    if not payment:
        await callback.answer("Заявка уже обработана", show_alert=True)
        return

    if status == "paid":
        text = f"✅ Заявка #{payment['id']} подтверждена. Баланс пополнен на {money(payment['amount_rub'])} ₽."
        user_text = f"✅ Пополнение #{payment['id']} подтверждено.\nБаланс пополнен на <b>{money(payment['amount_rub'])} ₽</b>."
    else:
        text = f"❌ Заявка #{payment['id']} отклонена."
        user_text = f"❌ Пополнение #{payment['id']} отклонено. Если это ошибка, напишите в поддержку."

    if callback.message:
        await callback.message.edit_text(text)
    await callback.bot.send_message(payment["user_id"], user_text)
    await callback.answer("Готово")


@router.message()
async def fallback(message: Message, state: FSMContext) -> None:
    user = await ensure_current_user(message)
    await send_page(message, state, "Выберите действие в меню ниже.", reply_markup=main_menu(user["is_admin"]))


async def main() -> None:
    global DB, SETTINGS

    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    db = Database(settings)
    await db.connect()
    await db.init_schema()
    SETTINGS = settings
    DB = db

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()
    dp.include_router(router)

    try:
        await dp.start_polling(bot)
    finally:
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
