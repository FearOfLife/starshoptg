from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


BUY_STARS = "Купить звезды"
TOP_UP = "Пополнить баланс"
PROFILE = "Профиль"
HELP = "Помощь"
BACK = "НАЗАД"

MONEY_TO_STARS = "⭐ по количеству 💰"
STARS_TO_MONEY = "💰 по количеству ⭐"
BUY_1000 = "1000 ⭐"
BUY_10000 = "10000 ⭐"
ADMIN_PANEL = "Админ панель"
ADMIN_STATS = "Статистика"
ADMIN_PENDING_PAYMENTS = "Ожидают оплаты"
ADMIN_PRODUCTS = "Список товаров"
MAIN_MENU = "Главное меню"


def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BUY_STARS), KeyboardButton(text=TOP_UP)],
        [KeyboardButton(text=PROFILE), KeyboardButton(text=HELP)],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=ADMIN_PANEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def buy_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=STARS_TO_MONEY), KeyboardButton(text=MONEY_TO_STARS)],
            [KeyboardButton(text=BUY_1000), KeyboardButton(text=BUY_10000)],
            [KeyboardButton(text=BACK)],
        ],
        resize_keyboard=True,
    )


def back_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BACK)]], resize_keyboard=True)


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ADMIN_STATS), KeyboardButton(text=ADMIN_PENDING_PAYMENTS)],
            [KeyboardButton(text=ADMIN_PRODUCTS), KeyboardButton(text=MAIN_MENU)],
        ],
        resize_keyboard=True,
    )


def support_keyboard(username: str) -> InlineKeyboardMarkup:
    clean_username = username.lstrip("@")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Написать в поддержку", url=f"https://t.me/{clean_username}")]
        ]
    )


def payment_admin_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"payment:approve:{payment_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"payment:cancel:{payment_id}"),
            ]
        ]
    )
