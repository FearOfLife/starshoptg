from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


BUY_STARS = "К покупкам"
PROFILE = "Профиль"
HELP = "Помощь"
INFO = "Информация"
BACK = "Назад"

STARS_TO_MONEY = "По кол-ву ⭐️"
MONEY_TO_STARS = "⭐️ По кол-ву"

PROFILE_TOP_UP = "Пополнить баланс"

ADMIN_PANEL = "👮‍♀️ Админ панель"
ADMIN_STATS = "📝 Статистика"
ADMIN_PENDING_PAYMENTS = "⏳ Ожидают оплаты"
ADMIN_PRODUCTS = "📋 Список товаров"
MAIN_MENU = "🏠 Главное меню"

MENU_BUTTON_ICON_IDS = {
    BUY_STARS: "5947363097353130662",
    PROFILE: "5195140682590722632",
    HELP: "5289733171166862088",
    INFO: "5193018431875587270",
}

INLINE_BUTTON_ICON_IDS = {
    STARS_TO_MONEY: "5201873447554145566",
    MONEY_TO_STARS: "5201873447554145566",
    PROFILE_TOP_UP: "5415594207068822547",
    BACK: "5255703720078879038",
}

INLINE_BUTTON_STYLES = {
    STARS_TO_MONEY: "primary",
    MONEY_TO_STARS: "primary",
    PROFILE_TOP_UP: "success",
    BACK: "danger",
}

BUY_STARS_TEXTS = {BUY_STARS, "🌟 К покупкам"}
PROFILE_TEXTS = {PROFILE, "🏡 Профиль"}
HELP_TEXTS = {HELP, "🆘 Помощь"}
INFO_TEXTS = {INFO, "💳 Информация"}

MAIN_BACK_CALLBACK = "main_back"
BUY_MENU_BACK_CALLBACK = "buy_menu_back"
PROFILE_BACK_CALLBACK = "profile_back"

STAR_OPTIONS: tuple[tuple[int, int], ...] = (
    (50, 73),
    (75, 110),
    (100, 146),
    (125, 183),
    (150, 219),
    (175, 256),
    (200, 292),
    (225, 329),
    (250, 365),
    (275, 402),
    (300, 438),
    (325, 475),
    (350, 511),
    (375, 548),
    (400, 584),
    (450, 657),
    (500, 730),
    (600, 876),
    (700, 1022),
    (800, 1168),
    (900, 1314),
    (1000, 1450),
)

MONEY_OPTIONS: tuple[tuple[int, int], ...] = (
    (69, 100),
    (103, 150),
    (137, 200),
    (171, 250),
    (206, 300),
    (240, 350),
    (274, 400),
    (308, 450),
    (343, 500),
    (377, 550),
    (411, 600),
    (445, 650),
    (480, 700),
    (514, 750),
    (548, 800),
    (582, 850),
    (616, 900),
    (651, 950),
    (685, 1000),
)

TOP_UP_OPTIONS: tuple[int, ...] = (100, 250, 500, 1000, 2000, 5000)


def star_option_label(stars: int, price_rub: int) -> str:
    return f"{stars} • {price_rub} р."


def money_option_label(stars: int, price_rub: int) -> str:
    return f"🌟 {stars} • {price_rub} р."


STAR_OPTION_LABELS = {star_option_label(stars, price): (stars, price) for stars, price in STAR_OPTIONS}
MONEY_OPTION_LABELS = {money_option_label(stars, price): (stars, price) for stars, price in MONEY_OPTIONS}
ORDER_OPTION_LABELS = STAR_OPTION_LABELS | MONEY_OPTION_LABELS
TOP_UP_OPTION_LABELS = {f"💰 {amount} р.": amount for amount in TOP_UP_OPTIONS}


def _reply_keyboard(rows: list[list[str]]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=text,
                    icon_custom_emoji_id=MENU_BUTTON_ICON_IDS.get(text),
                    style="success" if text == BUY_STARS else None,
                )
                for text in row
            ]
            for row in rows
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def _inline_keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=callback_data,
                    icon_custom_emoji_id=INLINE_BUTTON_ICON_IDS.get(text),
                    style=INLINE_BUTTON_STYLES.get(text),
                )
                for text, callback_data in row
            ]
            for row in rows
        ]
    )


def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [BUY_STARS, PROFILE],
        [HELP, INFO],
    ]
    if is_admin:
        rows.append([ADMIN_PANEL])
    return _reply_keyboard(rows)


def buy_menu() -> InlineKeyboardMarkup:
    return _inline_keyboard(
        [
            [(STARS_TO_MONEY, STARS_TO_MONEY), (MONEY_TO_STARS, MONEY_TO_STARS)],
            [(BACK, MAIN_BACK_CALLBACK)],
        ]
    )


def stars_amount_menu() -> InlineKeyboardMarkup:
    labels = [star_option_label(stars, price) for stars, price in STAR_OPTIONS]
    rows = [[(label, label) for label in labels[index : index + 2]] for index in range(0, len(labels), 2)]
    rows.append([(BACK, BUY_MENU_BACK_CALLBACK)])
    return _inline_keyboard(rows)


def money_amount_menu() -> InlineKeyboardMarkup:
    labels = [money_option_label(stars, price) for stars, price in MONEY_OPTIONS]
    rows = [[(label, label) for label in labels[index : index + 2]] for index in range(0, len(labels), 2)]
    rows.append([(BACK, BUY_MENU_BACK_CALLBACK)])
    return _inline_keyboard(rows)


def profile_menu() -> InlineKeyboardMarkup:
    return _inline_keyboard([[(PROFILE_TOP_UP, PROFILE_TOP_UP)]])


def top_up_amount_menu() -> InlineKeyboardMarkup:
    labels = list(TOP_UP_OPTION_LABELS)
    rows = [[(label, label) for label in labels[index : index + 2]] for index in range(0, len(labels), 2)]
    rows.append([(BACK, PROFILE_BACK_CALLBACK)])
    return _inline_keyboard(rows)


def back_menu(callback_data: str = MAIN_BACK_CALLBACK) -> InlineKeyboardMarkup:
    return _inline_keyboard([[(BACK, callback_data)]])


def admin_menu() -> InlineKeyboardMarkup:
    return _inline_keyboard(
        [
            [(ADMIN_STATS, ADMIN_STATS), (ADMIN_PENDING_PAYMENTS, ADMIN_PENDING_PAYMENTS)],
            [(ADMIN_PRODUCTS, ADMIN_PRODUCTS), (MAIN_MENU, MAIN_BACK_CALLBACK)],
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
