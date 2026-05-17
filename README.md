# Telegram Stars Shop Bot

Python-бот на aiogram 3 для магазина Telegram Stars с PostgreSQL.

## Возможности

- приветствие по `/start`;
- главное меню 2x2: купить звезды, пополнить баланс, профиль, помощь;
- меню покупки звезд с раскладкой 2x2+1;
- расчет звезд по сумме и суммы по количеству звезд;
- профиль пользователя: ID, username, баланс, купленные звезды;
- заявки на пополнение баланса;
- админ-панель: статистика, ожидающие оплаты, список товаров;
- PostgreSQL-таблицы `users`, `payments`, `buyers`, `products`.

## Настройка

1. Установите зависимости:

```bash
pip install -r requirements.txt
```

2. Проверьте `.env`:

```env
BOT_TOKEN=...
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/starshop
ADMIN_IDS=123456789
SUPPORT_USERNAME=123
PRICE_PER_STAR_RUB=1.50
```

`ADMIN_IDS` должен содержать Telegram ID администраторов через запятую.

3. Запустите PostgreSQL:

```bash
docker compose up -d
```

4. Запустите бота:

```bash
python -m app.main
```

При старте бот сам создаст таблицы и добавит товары на 1000 и 10000 звезд.
