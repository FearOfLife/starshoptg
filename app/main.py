from __future__ import annotations

import asyncio
import logging
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, Message
from aiogram.types.inline_keyboard_markup import InlineKeyboardMarkup
from aiogram.types.reply_keyboard_markup import ReplyKeyboardMarkup

from app.config import Settings, get_settings
from app.database import Database
from app.keyboards import (
    ADMIN_PANEL,
    ADMIN_PENDING_PAYMENTS,
    ADMIN_PRODUCTS,
    ADMIN_STATS,
    BACK,
    BUY_STARS,
    BUY_STARS_TEXTS,
    HELP,
    HELP_TEXTS,
    INFO,
    INFO_TEXTS,
    BUY_MENU_BACK_CALLBACK,
    MAIN_MENU,
    MAIN_BACK_CALLBACK,
    MONEY_OPTION_LABELS,
    MONEY_TO_STARS,
    ORDER_OPTION_LABELS,
    PROFILE,
    PROFILE_BACK_CALLBACK,
    PROFILE_TOP_UP,
    PROFILE_TEXTS,
    STAR_OPTION_LABELS,
    STARS_TO_MONEY,
    TOP_UP_OPTION_LABELS,
    admin_menu,
    back_menu,
    buy_menu,
    main_menu,
    money_amount_menu,
    payment_admin_keyboard,
    profile_menu,
    stars_amount_menu,
    top_up_amount_menu,
)
from app.states import ConvertStates, TopUpStates


router = Router()
SETTINGS: Settings | None = None
DB: Database | None = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IMG_DIR = PROJECT_ROOT / "img"
WELCOME_IMAGE = IMG_DIR / "Приветствую.png"
BUY_CATEGORY_IMAGE = IMG_DIR / "Выберите категорию.png"
HELP_IMAGE = IMG_DIR / "Помощь.png"
PROFILE_IMAGE = IMG_DIR / "Профиль.png"

ORDER_PAY_PREFIX = "order_pay:"
ORDER_METHODS_PREFIX = "order_methods:"
ORDER_CRYPTO_PREFIX = "order_crypto:"
ORDER_BACK_PREFIX = "order_back:"
PAY_WAIT_PREFIX = "pay_wait:"
PAY_DONE_PREFIX = "pay_done:"
TOPUP_METHODS_PREFIX = "topup_methods:"
TOPUP_CRYPTO_PREFIX = "topup_crypto:"
TOPUP_PAY_WAIT_PREFIX = "topup_wait:"
TOPUP_PAY_DONE_PREFIX = "topup_done:"
ORDER_MENU_BACK = "order_menu_back"
CRYPTO_ASSETS = "USDT,TON,BTC,ETH,LTC,BNB,TRX,USDC"
STAR_PRICE_RUB = Decimal("1.46")


def money(value: Decimal | int | float) -> str:
    amount = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{amount:,.2f}".replace(",", " ")


def api_amount(value: Decimal | int | float) -> str:
    return format(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def premium_emoji(emoji_id: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def user_link(username: str | None) -> str:
    return f"@{username}" if username else "не указан"


def parse_positive_int(text: str) -> int | None:
    normalized = text.replace(" ", "").strip()
    if not normalized.isdigit():
        return None
    amount = int(normalized)
    if amount <= 0:
        return None
    return amount


def parse_positive_decimal(text: str) -> Decimal | None:
    normalized = text.replace(",", ".").strip()
    try:
        amount = Decimal(normalized)
    except InvalidOperation:
        return None
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount <= 0:
        return None
    return amount


def price_for_stars(stars: int) -> Decimal:
    return (Decimal(stars) * STAR_PRICE_RUB).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def stars_for_money(amount: Decimal) -> int:
    return int((amount / STAR_PRICE_RUB).to_integral_value(rounding=ROUND_DOWN))


def get_db(_: Message | CallbackQuery | None = None) -> Database:
    if DB is None:
        raise RuntimeError("Database is not initialized")
    return DB


def get_settings_ready() -> Settings:
    if SETTINGS is None:
        raise RuntimeError("Settings are not initialized")
    return SETTINGS


def encode_order(stars: int, amount: Decimal | int) -> str:
    cents = int(Decimal(amount) * 100)
    return f"{stars}:{cents}"


def decode_order(data: str) -> tuple[int, Decimal]:
    raw_stars, raw_cents = data.split(":", 1)
    stars = int(raw_stars)
    amount = (Decimal(int(raw_cents)) / Decimal(100)).quantize(Decimal("0.01"))
    if stars <= 0 or amount <= 0:
        raise ValueError("Bad order data")
    return stars, amount


async def ensure_current_user(message: Message):
    settings = get_settings_ready()
    from_user = message.from_user
    return await get_db(message).ensure_user(
        telegram_id=from_user.id,
        username=from_user.username,
        full_name=from_user.full_name,
        is_admin=from_user.id in settings.admin_id_set,
    )


async def ensure_callback_user(callback: CallbackQuery):
    settings = get_settings_ready()
    from_user = callback.from_user
    return await get_db(callback).ensure_user(
        telegram_id=from_user.id,
        username=from_user.username,
        full_name=from_user.full_name,
        is_admin=from_user.id in settings.admin_id_set,
    )


async def send_page(
    message: Message,
    state: FSMContext,
    text: str,
    reply_markup: ReplyKeyboardMarkup | InlineKeyboardMarkup | None = None,
) -> Message:
    # Reply-keyboard buttons are static: pressing them should add a new bot message, not clean chat history.
    sent = await message.answer(text, reply_markup=reply_markup)
    await state.update_data(page_message_id=sent.message_id)
    return sent


async def send_photo_page(
    message: Message,
    state: FSMContext,
    text: str,
    photo_path: Path,
    reply_markup: ReplyKeyboardMarkup | InlineKeyboardMarkup | None = None,
) -> Message:
    if not photo_path.exists():
        logging.warning("Page image not found: %s", photo_path)
        return await send_page(message, state, text, reply_markup=reply_markup)

    if len(text) > 1024:
        await message.answer_photo(FSInputFile(photo_path))
        return await send_page(message, state, text, reply_markup=reply_markup)

    sent = await message.answer_photo(FSInputFile(photo_path), caption=text, reply_markup=reply_markup)
    await state.update_data(page_message_id=sent.message_id)
    return sent


async def clear_flow_state(state: FSMContext) -> None:
    await state.clear()


async def edit_inline_message(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if callback.message:
        try:
            if callback.message.photo and len(text) <= 1024:
                await callback.message.edit_caption(caption=text, reply_markup=reply_markup)
            else:
                await callback.message.edit_text(text, reply_markup=reply_markup)
        except TelegramBadRequest:
            await callback.message.answer(text, reply_markup=reply_markup)


def welcome_text() -> str:
    return (
        f"{premium_emoji('5343984088493599366', '👋')} <b>Fear, привет!</b> Бот работает в автоматическом режиме!\n\n"
        f"Рекомендуем сначала заглянуть в раздел «{premium_emoji('5289733171166862088', '🆘')}Помощь», "
        "а потом совершать покупки в любое время суток!"
    )


def buy_stars_text() -> str:
    return (
        "» <b>\"По количеству 🕯\"</b>:\n"
        "- выбрать ровное количество звезд, например 100, 200, 350 и т.д.\n"
        "» <b>\"По количеству 💸\"</b>:\n"
        "- выбрать звезд на ровную сумму рублей, например 100, 250, 500 и т.д."
    )


def stars_menu_text() -> str:
    return "✨ <b>ВЫБРАТЬ КОЛИЧЕСТВО ЗВЁЗД</b>\n\nВыберите подходящий вариант ниже."


def money_menu_text() -> str:
    return "✨ <b>ВЫБРАТЬ КОЛИЧЕСТВО ЗВЁЗД</b>\n\nВыберите сумму, на которую хотите купить звёзды."


def order_text(stars: int, amount: Decimal) -> str:
    return (
        f"» Вы покупаете: <b>{stars} • {money(amount)} р.</b>\n\n"
        f"💵 Цена: <b>{money(amount)} р.</b>\n\n"
        "⚠️ После оплаты дождитесь уведомления "
        "\"✅ Оплата прошла успешно!\" и укажите @username аккаунта, "
        "которому будут отправлены звёзды / premium"
    )


def payment_methods_text() -> str:
    return (
        f"{premium_emoji('5447644880824181073', '⚠️')} Оплачивая, вы автоматически подтверждаете, что ознакомились с Публичной Офертой. "
        "Также подтверждаете, что покупаете данный товар в боте @Xbuystars_bot для себя или "
        "в подарок своим знакомым и не оплачиваете товары на других сайтах/сервисах "
        "в пользу незнакомых лиц!\n\n"
        f"Выберите способ оплаты {premium_emoji('5456327792868220208', '➡️')}"
    )


def top_up_text() -> str:
    return f"{premium_emoji('5415594207068822547', '💰')} <b>Пополнение баланса</b>\n\nВведите сумму пополнения:"


def top_up_order_text(amount: Decimal) -> str:
    return (
        f"» Вы пополняете баланс на: <b>{money(amount)} р.</b>\n\n"
        f"💵 Сумма: <b>{money(amount)} р.</b>\n\n"
        "⚠️ После оплаты дождитесь уведомления "
        "\"✅ Оплата прошла успешно!\" и нажмите кнопку «Я оплатил»."
    )


def top_up_invoice_text(amount: Decimal, invoice_id: int) -> str:
    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Пополнение баланса: <b>{money(amount)} р.</b>\n"
        f"#️⃣ Номер заказа: <code>{invoice_id}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Для оплаты перейдите по ссылке!\n"
        "🕘 Время на оплату: <b>30 минут</b>\n"
        "⚠️ Необходимо оплатить до окончания таймера\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


def invoice_text(stars: int, amount: Decimal, invoice_id: int) -> str:
    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{premium_emoji('5312361253610475399', '🎖️')} Товар: <b>{stars} • {money(amount)} р.</b>\n"
        f"{premium_emoji('5458501939673192257', '💵')} Цена: <b>{money(amount)} р.</b>\n"
        f"{premium_emoji('5393380168062485238', '💎')} Номер заказа: <code>{invoice_id}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Для оплаты перейдите по ссылке!\n"
        f"{premium_emoji('5397677845482849272', '🕘')} Время на оплату: <b>30 минут</b>\n"
        f"{premium_emoji('5447644880824181073', '⚠️')} Необходимо оплатить до окончания таймера\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


def help_text() -> str:
    return (
        "<b>ИНСТРУКЦИЯ ПОКУПКИ:</b>\n"
        "» Жмите «К покупкам»\n"
        "» Выбираете категорию\n"
        "» Выбираете количество\n"
        "» Жмите «Перейти к оплате»\n"
        "» Выбираете метод оплаты\n"
        "» Оплачиваете по ссылке\n"
        "» Ждёте уведомления в боте - \"✅ Оплата прошла успешно!\"\n"
        "➡️ Кому отправить? Укажите @username!\n"
        "» Укажите @username аккаунта, которому будут отправлены звёзды!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🧰 При возникновении проблем обратитесь в поддержку бота - @{get_settings_ready().support_username.lstrip('@')}\n\n"
        "⏱️ <b>Время работы поддержки: ежедневно с 10.00 до 22.00 по Мск</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🗣️ Посмотреть или оставить отзывы, можно тут - @l000rep\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🗞️ Актуальные новости, промокоды и розыгрыши, на нашем канале - @plzstars"
    )


def info_text() -> str:
    return (
        "⭐ <b>ИНФОРМАЦИЯ</b>\n\n"
        "Политика в отношении обработки персональных данных -\n"
        "https://telegra.ph/Politika-v-otnoshenii-obrabotki-personalnyh-dannyh-11-01-12\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Пользовательское соглашение -\n"
        "https://telegra.ph/Polzovatelskoe-soglashenie-11-01-23\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Оферта и Политика возврата -\n"
        "https://telegra.ph/Oferta-i-Politika-vozvrata-11-10\n\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


def welcome_text() -> str:
    return (
        f"{premium_emoji('5343984088493599366', '👋')} <b>Fear, привет!</b> Бот работает в автоматическом режиме!\n\n"
        f"Рекомендуем сначала заглянуть в раздел «{premium_emoji('5289733171166862088', '🆘')}Помощь», "
        "а потом совершать покупки в любое время суток!"
    )


def buy_stars_text() -> str:
    return (
        "———————\n\n"
        f"{premium_emoji('5456327792868220208', '➡️')} <b>По количеству {premium_emoji('5463289097336405244', '⭐')}</b>:\n"
        "- Указать, какое количество звёзд вы хотите приобрести.\n"
        f"{premium_emoji('5456327792868220208', '➡️')} <b>По количеству {premium_emoji('5201873447554145566', '💵')}</b>:\n"
        "- Указать на какую сумму вы хотите приобрести звёзд."
    )


def stars_menu_text() -> str:
    return (
        f"{premium_emoji('5463289097336405244', '⭐')} <b>Укажите количество звёзд</b>\n\n"
        "Введите количество звёзд числом, например: <code>800</code>."
    )


def money_menu_text() -> str:
    return (
        f"{premium_emoji('5201873447554145566', '💵')} <b>Укажите сумму</b>\n\n"
        "Введите сумму в рублях числом, например: <code>1168</code>."
    )


def order_text(stars: int, amount: Decimal) -> str:
    return (
        f"{premium_emoji('5456327792868220208', '➡️')} Вы покупаете: <b>{stars} • {money(amount)} р.</b>\n\n"
        f"{premium_emoji('5201873447554145566', '💵')} Цена: <b>{money(amount)} р.</b>\n\n"
        f"{premium_emoji('5447644880824181073', '⚠️')} После оплаты дождитесь уведомления "
        f"\"{premium_emoji('5364035134725043602', '✅')} Оплата прошла успешно!\" и укажите @username аккаунта, "
        "которому будут отправлены звёзды / premium"
    )


def profile_text(user: dict[str, Any], stats: dict[str, Any]) -> str:
    return (
        f"{premium_emoji('5321227547873124806', '🧾')} <b>Ваш профиль</b>\n"
        f"{premium_emoji('5226772700113935347', '✈️')} Юзернейм: <b>{user_link(user['username'])}</b>\n"
        f"{premium_emoji('5393380168062485238', '💎')} ID: <code>{user['telegram_id']}</code>\n"
        f"{premium_emoji('5461139036708033234', '😎')} Ваш баланс: <b>{money(user['balance'])} ₽</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{premium_emoji('5298614648138919107', '📊')} Количество заказов: <b>{stats['orders_count']}</b>\n"
        f"{premium_emoji('5390945046159699137', '📈')} Всего потрачено: <b>{money(stats['total_spent'])} ₽</b>"
    )


def help_text() -> str:
    return (
        "<b>ИНСТРУКЦИЯ ПОКУПКИ:</b>\n"
        "» Жмите «К покупкам»\n"
        "» Выбираете категорию\n"
        "» Выбираете количество\n"
        "» Жмите «Перейти к оплате»\n"
        "» Выбираете метод оплаты\n"
        "» Оплачиваете по ссылке\n"
        f"» Ждёте уведомления в боте - \"{premium_emoji('5364035134725043602', '✅')} Оплата прошла успешно!\"\n"
        f"{premium_emoji('5300991719263715937', '➡️')} Кому отправить? Укажите @username!\n"
        "» Укажите @username аккаунта, которому будут отправлены звёзды!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{premium_emoji('5201990176175299013', '🧰')} При возникновении проблем обратитесь в поддержку бота - @123\n\n"
        f"{premium_emoji('5300991719263715937', '➡️')} <b>Время работы поддержки: ежедневно с 10.00 до 22.00 по Мск</b>"
    )


def info_text() -> str:
    return (
        f"{premium_emoji('5436202057155491174', 'ℹ️')} <b>ИНФОРМАЦИЯ</b>\n\n"
        "Политика в отношении обработки персональных данных -\n"
        "https://telegra.ph/Politika-v-otnoshenii-obrabotki-personalnyh-dannyh-11-01-12\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Пользовательское соглашение -\n"
        "https://telegra.ph/Polzovatelskoe-soglashenie-11-01-23\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Оферта и Политика возврата -\n"
        "https://telegra.ph/Oferta-i-Politika-vozvrata-11-10\n\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


def order_keyboard(stars: int, amount: Decimal) -> InlineKeyboardMarkup:
    encoded = encode_order(stars, amount)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Перейти к оплате",
                    callback_data=f"{ORDER_METHODS_PREFIX}{encoded}",
                    icon_custom_emoji_id="5317013291602553603",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=f"{ORDER_BACK_PREFIX}{encoded}",
                    icon_custom_emoji_id="5255703720078879038",
                    style="danger",
                )
            ],
        ]
    )


def payment_methods_keyboard(stars: int, amount: Decimal) -> InlineKeyboardMarkup:
    encoded = encode_order(stars, amount)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="CryptoBot",
                    callback_data=f"{ORDER_CRYPTO_PREFIX}{encoded}",
                    icon_custom_emoji_id="5361836987642815474",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=f"{ORDER_PAY_PREFIX}{encoded}",
                    icon_custom_emoji_id="5255703720078879038",
                    style="danger",
                )
            ],
        ]
    )


def top_up_order_keyboard(amount: Decimal) -> InlineKeyboardMarkup:
    cents = int(amount * 100)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Перейти к оплате",
                    callback_data=f"{TOPUP_METHODS_PREFIX}{cents}",
                    icon_custom_emoji_id="5317013291602553603",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=PROFILE_TOP_UP,
                    icon_custom_emoji_id="5255703720078879038",
                    style="danger",
                )
            ],
        ]
    )


def top_up_payment_methods_keyboard(amount: Decimal) -> InlineKeyboardMarkup:
    cents = int(amount * 100)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="CryptoBot",
                    callback_data=f"{TOPUP_CRYPTO_PREFIX}{cents}",
                    icon_custom_emoji_id="5361836987642815474",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=PROFILE_TOP_UP,
                    icon_custom_emoji_id="5255703720078879038",
                    style="danger",
                )
            ],
        ]
    )


def invoice_keyboard(
    pay_url: str,
    invoice_id: int,
    paid: bool = False,
    wait_prefix: str = PAY_WAIT_PREFIX,
    done_prefix: str = PAY_DONE_PREFIX,
) -> InlineKeyboardMarkup:
    status_button = (
        InlineKeyboardButton(text="Я оплатил", callback_data=f"{done_prefix}{invoice_id}", style="success")
        if paid
        else InlineKeyboardButton(text="---", callback_data=f"{wait_prefix}{invoice_id}")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Перейти к оплате ↗", url=pay_url)],
            [status_button],
        ]
    )


async def crypto_request(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings_ready()
    if not settings.crypto_bot_token:
        raise RuntimeError("CRYPTOBOT_TOKEN is not configured")

    url = f"{settings.crypto_bot_api_url.rstrip('/')}/api/{method}"
    headers = {"Crypto-Pay-API-Token": settings.crypto_bot_token}
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload, headers=headers) as response:
            data = await response.json(content_type=None)
    if not data.get("ok"):
        raise RuntimeError(str(data.get("error") or data))
    return data["result"]


async def create_crypto_invoice(user_id: int, amount: Decimal, description: str, payload: str) -> dict[str, Any]:
    return await crypto_request(
        "createInvoice",
        {
            "currency_type": "fiat",
            "fiat": "RUB",
            "accepted_assets": CRYPTO_ASSETS,
            "amount": api_amount(amount),
            "description": description,
            "payload": payload,
            "allow_comments": False,
            "allow_anonymous": False,
            "expires_in": 1800,
        },
    )


async def get_crypto_invoice_status(invoice_id: int) -> str:
    result = await crypto_request("getInvoices", {"invoice_ids": str(invoice_id)})
    items = result.get("items") or result
    if isinstance(items, list) and items:
        return str(items[0].get("status", "active"))
    return "active"


async def health_handler(_: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "starshoptg"})


async def istar_webhook_handler(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)

    logging.info("iStar webhook received: %s", payload)
    return web.json_response({"ok": True})


async def start_web_server(settings: Settings) -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/health", health_handler)
    app.router.add_post("/istar/webhook", istar_webhook_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.web_host, settings.web_port)
    await site.start()
    logging.info("Local webhook server listening on http://%s:%s", settings.web_host, settings.web_port)
    if settings.public_base_url:
        logging.info("Public webhook URL: %s/istar/webhook", settings.public_base_url.rstrip("/"))
    return runner


async def mark_invoice_button_when_paid(bot: Bot, chat_id: int, message_id: int, invoice_id: int) -> None:
    for _ in range(60):
        await asyncio.sleep(30)
        try:
            status = await get_crypto_invoice_status(invoice_id)
            order = await get_db().get_crypto_order(invoice_id)
            if status == "paid" and order:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=invoice_keyboard(order["pay_url"], invoice_id, paid=True),
                )
                return
            if status in {"expired", "deleted"}:
                return
        except Exception:
            logging.exception("Failed to poll CryptoBot invoice %s", invoice_id)


async def mark_topup_invoice_button_when_paid(bot: Bot, chat_id: int, message_id: int, invoice_id: int) -> None:
    for _ in range(60):
        await asyncio.sleep(30)
        try:
            status = await get_crypto_invoice_status(invoice_id)
            topup = await get_db().get_crypto_topup(invoice_id)
            if status == "paid" and topup:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=invoice_keyboard(
                        topup["pay_url"],
                        invoice_id,
                        paid=True,
                        wait_prefix=TOPUP_PAY_WAIT_PREFIX,
                        done_prefix=TOPUP_PAY_DONE_PREFIX,
                    ),
                )
                return
            if status in {"expired", "deleted"}:
                return
        except Exception:
            logging.exception("Failed to poll CryptoBot top-up invoice %s", invoice_id)


async def show_main_menu(message: Message, state: FSMContext) -> None:
    await clear_flow_state(state)
    user = await ensure_current_user(message)
    await send_photo_page(message, state, welcome_text(), WELCOME_IMAGE, reply_markup=main_menu(user["is_admin"]))


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await show_main_menu(message, state)


@router.message(F.text.in_(BUY_STARS_TEXTS))
async def buy_stars_menu(message: Message, state: FSMContext) -> None:
    await clear_flow_state(state)
    await ensure_current_user(message)
    await send_photo_page(message, state, buy_stars_text(), BUY_CATEGORY_IMAGE, reply_markup=buy_menu())


@router.callback_query(F.data == STARS_TO_MONEY)
async def choose_by_stars(callback: CallbackQuery, state: FSMContext) -> None:
    await clear_flow_state(state)
    await edit_inline_message(callback, stars_menu_text(), reply_markup=stars_amount_menu())
    await callback.answer()


@router.callback_query(F.data == MONEY_TO_STARS)
async def choose_by_money(callback: CallbackQuery, state: FSMContext) -> None:
    await clear_flow_state(state)
    await edit_inline_message(callback, money_menu_text(), reply_markup=money_amount_menu())
    await callback.answer()


@router.callback_query(F.data.in_(set(ORDER_OPTION_LABELS)))
async def show_order_summary(callback: CallbackQuery) -> None:
    stars, amount_rub = ORDER_OPTION_LABELS[callback.data]
    amount = Decimal(amount_rub)
    await edit_inline_message(callback, order_text(stars, amount), reply_markup=order_keyboard(stars, amount))
    await callback.answer()


@router.callback_query(F.data == BUY_MENU_BACK_CALLBACK)
async def back_to_buy_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await clear_flow_state(state)
    await edit_inline_message(callback, buy_stars_text(), reply_markup=buy_menu())
    await callback.answer()


@router.callback_query(F.data == MAIN_BACK_CALLBACK)
async def inline_back_to_main(callback: CallbackQuery, state: FSMContext) -> None:
    await clear_flow_state(state)
    await edit_inline_message(callback, welcome_text())
    await callback.answer()


@router.callback_query(F.data == PROFILE_BACK_CALLBACK)
async def inline_back_to_profile(callback: CallbackQuery, state: FSMContext) -> None:
    await clear_flow_state(state)
    await edit_inline_message(callback, "Действие отменено.", reply_markup=profile_menu())
    await callback.answer()


@router.callback_query(F.data.startswith(ORDER_BACK_PREFIX))
async def order_back(callback: CallbackQuery) -> None:
    try:
        stars, amount = decode_order(callback.data.removeprefix(ORDER_BACK_PREFIX))
    except (AttributeError, ValueError, InvalidOperation):
        await callback.answer("Некорректный заказ", show_alert=True)
        return

    option = (stars, int(amount))
    if option in MONEY_OPTION_LABELS.values():
        await edit_inline_message(callback, money_menu_text(), reply_markup=money_amount_menu())
    elif option in STAR_OPTION_LABELS.values():
        await edit_inline_message(callback, stars_menu_text(), reply_markup=stars_amount_menu())
    else:
        await edit_inline_message(callback, buy_stars_text(), reply_markup=buy_menu())
    await callback.answer()


@router.callback_query(F.data.startswith(ORDER_PAY_PREFIX))
async def back_to_order_summary(callback: CallbackQuery) -> None:
    try:
        stars, amount = decode_order(callback.data.removeprefix(ORDER_PAY_PREFIX))
    except (AttributeError, ValueError, InvalidOperation):
        await callback.answer("Некорректный заказ", show_alert=True)
        return
    await edit_inline_message(callback, order_text(stars, amount), reply_markup=order_keyboard(stars, amount))
    await callback.answer()


@router.callback_query(F.data.startswith(ORDER_METHODS_PREFIX))
async def show_payment_methods(callback: CallbackQuery) -> None:
    try:
        stars, amount = decode_order(callback.data.removeprefix(ORDER_METHODS_PREFIX))
    except (AttributeError, ValueError, InvalidOperation):
        await callback.answer("Некорректный заказ", show_alert=True)
        return
    await edit_inline_message(callback, payment_methods_text(), reply_markup=payment_methods_keyboard(stars, amount))
    await callback.answer()


@router.callback_query(F.data.startswith(ORDER_CRYPTO_PREFIX))
async def show_crypto_invoice(callback: CallbackQuery) -> None:
    try:
        stars, amount = decode_order(callback.data.removeprefix(ORDER_CRYPTO_PREFIX))
    except (AttributeError, ValueError, InvalidOperation):
        await callback.answer("Некорректный заказ", show_alert=True)
        return

    if not get_settings_ready().crypto_bot_token:
        await edit_inline_message(
            callback,
            "CryptoBot пока не подключён.\n\n"
            "Добавьте токен в `.env`:\n"
            "<code>CRYPTOBOT_TOKEN=ваш_токен</code>\n\n"
            "После этого перезапустите бота.",
            reply_markup=payment_methods_keyboard(stars, amount),
        )
        await callback.answer("Нужен CRYPTOBOT_TOKEN", show_alert=True)
        return

    user = await get_db(callback).ensure_user(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
        is_admin=callback.from_user.id in get_settings_ready().admin_id_set,
    )
    try:
        invoice = await create_crypto_invoice(
            user["telegram_id"],
            amount,
            f"{stars} Telegram Stars",
            f"stars:{user['telegram_id']}:{stars}:{int(amount * 100)}",
        )
    except Exception as exc:
        logging.exception("CryptoBot invoice creation failed")
        await edit_inline_message(
            callback,
            f"Не удалось создать счёт CryptoBot.\n\n<code>{exc}</code>",
            reply_markup=payment_methods_keyboard(stars, amount),
        )
        await callback.answer("Ошибка CryptoBot", show_alert=True)
        return

    invoice_id = int(invoice["invoice_id"])
    pay_url = invoice.get("bot_invoice_url") or invoice.get("pay_url") or invoice.get("web_app_invoice_url") or ""
    await get_db(callback).create_crypto_order(user["telegram_id"], invoice_id, stars, amount, pay_url)
    await edit_inline_message(
        callback,
        invoice_text(stars, amount, invoice_id),
        reply_markup=invoice_keyboard(pay_url, invoice_id),
    )
    if callback.message:
        asyncio.create_task(
            mark_invoice_button_when_paid(callback.bot, callback.message.chat.id, callback.message.message_id, invoice_id)
        )
    await callback.answer()


@router.callback_query(F.data.startswith(PAY_WAIT_PREFIX))
async def wait_payment(callback: CallbackQuery) -> None:
    try:
        invoice_id = int(callback.data.removeprefix(PAY_WAIT_PREFIX))
        status = await get_crypto_invoice_status(invoice_id)
        order = await get_db(callback).get_crypto_order(invoice_id)
    except Exception:
        await callback.answer("Не удалось проверить оплату", show_alert=True)
        return
    if status != "paid" or not order:
        await callback.answer("Оплата пока не найдена", show_alert=True)
        return
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=invoice_keyboard(order["pay_url"], invoice_id, paid=True))
    await callback.answer("Оплата найдена")


@router.callback_query(F.data.startswith(PAY_DONE_PREFIX))
async def finish_paid_order(callback: CallbackQuery) -> None:
    try:
        invoice_id = int(callback.data.removeprefix(PAY_DONE_PREFIX))
        status = await get_crypto_invoice_status(invoice_id)
        order = await get_db(callback).get_crypto_order(invoice_id)
    except Exception:
        await callback.answer("Не удалось проверить оплату", show_alert=True)
        return
    if status != "paid" or not order:
        await callback.answer("Оплата пока не найдена", show_alert=True)
        return

    completed, updated_user = await get_db(callback).complete_crypto_order(invoice_id)
    if not completed:
        await callback.answer("Заказ уже был обработан", show_alert=True)
        return
    await edit_inline_message(
        callback,
        "✅ Оплата прошла успешно!\n\n"
        f"Начислено: <b>{order['stars']}</b> ⭐\n"
        f"Сумма: <b>{money(order['amount_rub'])} ₽</b>\n"
        f"Ваш баланс: <b>{money(updated_user['balance'])} ₽</b>\n\n"
        "Укажите @username аккаунта, которому будут отправлены звёзды / premium."
    )
    await callback.answer("Готово")


@router.callback_query(F.data.startswith(TOPUP_PAY_WAIT_PREFIX))
async def wait_top_up_payment(callback: CallbackQuery) -> None:
    try:
        invoice_id = int(callback.data.removeprefix(TOPUP_PAY_WAIT_PREFIX))
        status = await get_crypto_invoice_status(invoice_id)
        topup = await get_db(callback).get_crypto_topup(invoice_id)
    except Exception:
        await callback.answer("Не удалось проверить оплату", show_alert=True)
        return
    if status != "paid" or not topup:
        await callback.answer("Оплата пока не найдена", show_alert=True)
        return
    if callback.message:
        await callback.message.edit_reply_markup(
            reply_markup=invoice_keyboard(
                topup["pay_url"],
                invoice_id,
                paid=True,
                wait_prefix=TOPUP_PAY_WAIT_PREFIX,
                done_prefix=TOPUP_PAY_DONE_PREFIX,
            )
        )
    await callback.answer("Оплата найдена")


@router.callback_query(F.data.startswith(TOPUP_PAY_DONE_PREFIX))
async def finish_paid_top_up(callback: CallbackQuery) -> None:
    try:
        invoice_id = int(callback.data.removeprefix(TOPUP_PAY_DONE_PREFIX))
        status = await get_crypto_invoice_status(invoice_id)
        topup = await get_db(callback).get_crypto_topup(invoice_id)
    except Exception:
        await callback.answer("Не удалось проверить оплату", show_alert=True)
        return
    if status != "paid" or not topup:
        await callback.answer("Оплата пока не найдена", show_alert=True)
        return

    completed, updated_user = await get_db(callback).complete_crypto_topup(invoice_id)
    if not completed:
        await callback.answer("Пополнение уже было обработано", show_alert=True)
        return
    await edit_inline_message(
        callback,
        "✅ Оплата прошла успешно!\n\n"
        f"Баланс пополнен на: <b>{money(topup['amount_rub'])} ₽</b>\n"
        f"Ваш баланс: <b>{money(updated_user['balance'])} ₽</b>",
        reply_markup=profile_menu(),
    )
    await callback.answer("Готово")


@router.message(F.text.in_(PROFILE_TEXTS))
async def profile(message: Message, state: FSMContext) -> None:
    await clear_flow_state(state)
    user = await ensure_current_user(message)
    stats = await get_db(message).user_purchase_stats(user["telegram_id"])
    await send_photo_page(message, state, profile_text(user, stats), PROFILE_IMAGE, reply_markup=profile_menu())


@router.callback_query(F.data == PROFILE_TOP_UP)
async def top_up(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TopUpStates.waiting_amount)
    await edit_inline_message(callback, top_up_text(), reply_markup=back_menu(PROFILE_BACK_CALLBACK))
    await callback.answer()


@router.callback_query(F.data.in_(set(TOP_UP_OPTION_LABELS)))
async def show_top_up_summary(callback: CallbackQuery) -> None:
    amount = Decimal(TOP_UP_OPTION_LABELS[callback.data])
    await edit_inline_message(callback, top_up_order_text(amount), reply_markup=top_up_order_keyboard(amount))
    await callback.answer()


@router.message(TopUpStates.waiting_amount)
async def handle_top_up_amount(message: Message, state: FSMContext) -> None:
    amount = parse_positive_decimal(message.text or "")
    if amount is None:
        await send_page(
            message,
            state,
            "Введите сумму пополнения числом, например: <code>500</code>.",
            reply_markup=back_menu(PROFILE_BACK_CALLBACK),
        )
        return

    await clear_flow_state(state)
    await ensure_current_user(message)
    await send_page(message, state, top_up_order_text(amount), reply_markup=top_up_order_keyboard(amount))


@router.callback_query(F.data.startswith(TOPUP_METHODS_PREFIX))
async def show_top_up_payment_methods(callback: CallbackQuery) -> None:
    try:
        amount = (Decimal(int(callback.data.removeprefix(TOPUP_METHODS_PREFIX))) / Decimal(100)).quantize(Decimal("0.01"))
    except (AttributeError, ValueError, InvalidOperation):
        await callback.answer("Некорректная сумма", show_alert=True)
        return
    await edit_inline_message(callback, payment_methods_text(), reply_markup=top_up_payment_methods_keyboard(amount))
    await callback.answer()


@router.callback_query(F.data.startswith(TOPUP_CRYPTO_PREFIX))
async def show_top_up_crypto_invoice(callback: CallbackQuery) -> None:
    try:
        amount = (Decimal(int(callback.data.removeprefix(TOPUP_CRYPTO_PREFIX))) / Decimal(100)).quantize(Decimal("0.01"))
    except (AttributeError, ValueError, InvalidOperation):
        await callback.answer("Некорректная сумма", show_alert=True)
        return

    if not get_settings_ready().crypto_bot_token:
        await edit_inline_message(
            callback,
            "CryptoBot пока не подключён.\n\n"
            "Добавьте токен в `.env`:\n"
            "<code>CRYPTOBOT_TOKEN=ваш_токен</code>\n\n"
            "После этого перезапустите бота.",
            reply_markup=top_up_payment_methods_keyboard(amount),
        )
        await callback.answer("Нужен CRYPTOBOT_TOKEN", show_alert=True)
        return

    user = await ensure_callback_user(callback)
    try:
        invoice = await create_crypto_invoice(
            user["telegram_id"],
            amount,
            f"Пополнение баланса на {money(amount)} RUB",
            f"topup:{user['telegram_id']}:{int(amount * 100)}",
        )
    except Exception as exc:
        logging.exception("CryptoBot top-up invoice creation failed")
        await edit_inline_message(
            callback,
            f"Не удалось создать счёт CryptoBot.\n\n<code>{exc}</code>",
            reply_markup=top_up_payment_methods_keyboard(amount),
        )
        await callback.answer("Ошибка CryptoBot", show_alert=True)
        return

    invoice_id = int(invoice["invoice_id"])
    pay_url = invoice.get("bot_invoice_url") or invoice.get("pay_url") or invoice.get("web_app_invoice_url") or ""
    await get_db(callback).create_crypto_topup(user["telegram_id"], invoice_id, amount, pay_url)
    await edit_inline_message(
        callback,
        top_up_invoice_text(amount, invoice_id),
        reply_markup=invoice_keyboard(
            pay_url,
            invoice_id,
            wait_prefix=TOPUP_PAY_WAIT_PREFIX,
            done_prefix=TOPUP_PAY_DONE_PREFIX,
        ),
    )
    if callback.message:
        asyncio.create_task(
            mark_topup_invoice_button_when_paid(callback.bot, callback.message.chat.id, callback.message.message_id, invoice_id)
        )
    await callback.answer()


@router.message(F.text.in_(HELP_TEXTS))
async def help_handler(message: Message, state: FSMContext) -> None:
    await clear_flow_state(state)
    await ensure_current_user(message)
    await send_photo_page(message, state, help_text(), HELP_IMAGE, reply_markup=back_menu())


@router.message(F.text.in_(INFO_TEXTS))
async def info_handler(message: Message, state: FSMContext) -> None:
    await clear_flow_state(state)
    await ensure_current_user(message)
    await send_page(message, state, info_text(), reply_markup=back_menu())


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


@router.callback_query(F.data == ADMIN_STATS)
async def admin_stats(callback: CallbackQuery) -> None:
    user = await ensure_callback_user(callback)
    if not user["is_admin"]:
        await callback.answer("Нет доступа", show_alert=True)
        return

    stats = await get_db(callback).stats()
    await edit_inline_message(
        callback,
        "📊 <b>Статистика</b>\n\n"
        f"Пользователей: <b>{stats['users_count']}</b>\n"
        f"Ожидают оплаты: <b>{stats['pending_payments']}</b>\n"
        f"Пополнено: <b>{money(stats['paid_amount'])} ₽</b>\n"
        f"Продано звезд: <b>{stats['sold_stars']}</b>\n"
        f"Сумма продаж: <b>{money(stats['sold_amount'])} ₽</b>",
        reply_markup=admin_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == ADMIN_PENDING_PAYMENTS)
async def admin_pending_payments(callback: CallbackQuery) -> None:
    user = await ensure_callback_user(callback)
    if not user["is_admin"]:
        await callback.answer("Нет доступа", show_alert=True)
        return

    payments = await get_db(callback).list_pending_payments()
    if not payments:
        await edit_inline_message(callback, "Ожидающих заявок нет.", reply_markup=admin_menu())
        await callback.answer()
        return

    await edit_inline_message(callback, "Ожидающие заявки:")
    if not callback.message:
        await callback.answer()
        return
    for payment in payments:
        await callback.message.answer(
            f"Заявка <b>#{payment['id']}</b>\n"
            f"Пользователь: <b>{payment['user_id']}</b> ({user_link(payment['username'])})\n"
            f"Сумма: <b>{money(payment['amount_rub'])} ₽</b>",
            reply_markup=payment_admin_keyboard(payment["id"]),
        )
    await callback.answer()


@router.callback_query(F.data == ADMIN_PRODUCTS)
async def admin_products(callback: CallbackQuery) -> None:
    user = await ensure_callback_user(callback)
    if not user["is_admin"]:
        await callback.answer("Нет доступа", show_alert=True)
        return

    products = await get_db(callback).get_products()
    rows = [f"• <b>{product['title']}</b>: {product['stars']} ⭐, {money(product['price_rub'])} ₽" for product in products]
    await edit_inline_message(callback, "🛒 <b>Товары</b>\n\n" + "\n".join(rows), reply_markup=admin_menu())
    await callback.answer()


@router.callback_query(F.data.startswith("payment:"))
async def payment_action(callback: CallbackQuery) -> None:
    settings = get_settings_ready()
    if callback.from_user.id not in settings.admin_id_set:
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
        user_text = (
            f"✅ Пополнение #{payment['id']} подтверждено.\n"
            f"Баланс пополнен на <b>{money(payment['amount_rub'])} ₽</b>."
        )
    else:
        text = f"❌ Заявка #{payment['id']} отклонена."
        user_text = f"❌ Пополнение #{payment['id']} отклонено. Если это ошибка, напишите в поддержку."

    await edit_inline_message(callback, text)
    try:
        await callback.bot.send_message(payment["user_id"], user_text)
    except (TelegramBadRequest, TelegramForbiddenError):
        await callback.answer("Готово, но пользователь недоступен", show_alert=True)
        return
    await callback.answer("Готово")


@router.message(ConvertStates.waiting_stars)
async def handle_manual_stars(message: Message, state: FSMContext) -> None:
    stars = parse_positive_int(message.text or "")
    if stars is None:
        await send_page(
            message,
            state,
            "Введите количество звёзд целым числом, например: <code>800</code>.",
            reply_markup=back_menu(BUY_MENU_BACK_CALLBACK),
        )
        return

    await clear_flow_state(state)
    amount = price_for_stars(stars)
    await ensure_current_user(message)
    await send_page(message, state, order_text(stars, amount), reply_markup=order_keyboard(stars, amount))


@router.message(ConvertStates.waiting_money)
async def handle_manual_money(message: Message, state: FSMContext) -> None:
    amount = parse_positive_decimal(message.text or "")
    if amount is None:
        await send_page(
            message,
            state,
            "Введите сумму в рублях числом, например: <code>1168</code>.",
            reply_markup=back_menu(BUY_MENU_BACK_CALLBACK),
        )
        return

    stars = stars_for_money(amount)
    if stars <= 0:
        await send_page(
            message,
            state,
            "Сумма слишком маленькая. Введите сумму, на которую можно купить хотя бы 1 звезду.",
            reply_markup=back_menu(BUY_MENU_BACK_CALLBACK),
        )
        return

    await clear_flow_state(state)
    await ensure_current_user(message)
    await send_page(message, state, order_text(stars, amount), reply_markup=order_keyboard(stars, amount))


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
    web_runner = await start_web_server(settings)

    try:
        await dp.start_polling(bot)
    finally:
        await web_runner.cleanup()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
