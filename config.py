"""Настройки бота. Значения берутся из переменных окружения или из файла .env."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent


def _load_env_file() -> None:
    env = BASE_DIR / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Часовой пояс для напоминаний. Махачкала = Europe/Moscow.
TZ = ZoneInfo(os.getenv("BOT_TZ", "Europe/Moscow"))
DB_PATH = os.getenv("BOT_DB", str(BASE_DIR / "bot.sqlite3"))
# Необязательно: список Telegram ID через запятую. Если задан, бот отвечает только им.
ALLOWED_USERS = {int(x) for x in os.getenv("ALLOWED_USERS", "").replace(" ", "").split(",") if x}
