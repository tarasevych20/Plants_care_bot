# plantbot/config.py
import os
from zoneinfo import ZoneInfo

# Токени/ключі з Variables на Railway
TOKEN = os.environ["TELEGRAM_TOKEN"]
PLANT_ID_API_KEY = os.environ.get("PLANT_ID_API_KEY", "")

# Шлях до SQLite
DB_PATH = os.environ.get("DB_PATH", "plants.db")

# Часова зона (на майбутнє)
TZ = ZoneInfo("Europe/Kyiv")

# Дні догляду: максимум два дні на тиждень (0=Пн ... 6=Нд)
CARE_DAYS = [1, 4]  # Вівторок і П’ятниця
