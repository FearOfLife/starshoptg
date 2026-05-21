from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env(name: str, file_values: dict[str, str], default: str | None = None) -> str:
    value = os.getenv(name, file_values.get(name, default))
    if value is None or (value == "" and default is None):
        raise RuntimeError(f"Environment variable {name} is required")
    return value


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    admin_ids: str = ""
    support_username: str = "123"
    price_per_star_rub: float = 1.5
    crypto_bot_token: str = ""
    crypto_bot_api_url: str = "https://pay.crypt.bot"
    istar_api_key: str = ""
    istar_api_url: str = "https://v1.fragmentapi.com/api/v1/partner"
    istar_wallet_type: str = "TON"
    istar_webhook_secret: str = ""
    premium_3_months_rub: float = 1259
    premium_6_months_rub: float = 1679
    premium_12_months_rub: float = 3044
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    public_base_url: str = ""

    @property
    def admin_id_set(self) -> set[int]:
        ids: set[int] = set()
        for raw_id in self.admin_ids.split(","):
            raw_id = raw_id.strip()
            if raw_id:
                ids.add(int(raw_id))
        return ids


@lru_cache
def get_settings() -> Settings:
    file_values = _read_env_file(Path(".env"))
    return Settings(
        bot_token=_env("BOT_TOKEN", file_values),
        database_url=_env("DATABASE_URL", file_values),
        admin_ids=_env("ADMIN_IDS", file_values, ""),
        support_username=_env("SUPPORT_USERNAME", file_values, "123"),
        price_per_star_rub=float(_env("PRICE_PER_STAR_RUB", file_values, "1.5")),
        crypto_bot_token=_env("CRYPTOBOT_TOKEN", file_values, ""),
        crypto_bot_api_url=_env("CRYPTOBOT_API_URL", file_values, "https://pay.crypt.bot"),
        istar_api_key=_env("ISTAR_API_KEY", file_values, ""),
        istar_api_url=_env("ISTAR_API_URL", file_values, "https://v1.fragmentapi.com/api/v1/partner"),
        istar_wallet_type=_env("ISTAR_WALLET_TYPE", file_values, "TON"),
        istar_webhook_secret=_env("ISTAR_WEBHOOK_SECRET", file_values, ""),
        premium_3_months_rub=float(_env("PREMIUM_3_MONTHS_RUB", file_values, "1259")),
        premium_6_months_rub=float(_env("PREMIUM_6_MONTHS_RUB", file_values, "1679")),
        premium_12_months_rub=float(_env("PREMIUM_12_MONTHS_RUB", file_values, "3044")),
        web_host=_env("WEB_HOST", file_values, "127.0.0.1"),
        web_port=int(_env("WEB_PORT", file_values, "8080")),
        public_base_url=_env("PUBLIC_BASE_URL", file_values, ""),
    )
