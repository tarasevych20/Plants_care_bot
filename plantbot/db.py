# plantbot/db.py
import sqlite3
from .config import DB_PATH

# --- авто-міграція БД у volume ---
import os, shutil
from .config import DB_PATH

LEGACY_PATHS = ["plants.db", "/app/plants.db"]  # де могла лежати стара БД

def ensure_db_on_volume():
    target = DB_PATH
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.exists(target):
        return  # у volume вже є файл
    for p in LEGACY_PATHS:
        if os.path.exists(p):
            shutil.copy2(p, target)  # переносимо старий файл
            break

ensure_db_on_volume()
# --- кінець блоку авто-міграції ---

def conn():
    c = sqlite3.connect(DB_PATH)
    c.execute("""
    CREATE TABLE IF NOT EXISTS plants(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER,                 -- multi-user (NULL legacy допустимо)
      name TEXT NOT NULL,
      care TEXT NOT NULL,
      photo BLOB,
      water_int INTEGER,
      feed_int INTEGER,
      mist_int INTEGER,
      last_watered TEXT,
      last_fed TEXT,
      last_misted TEXT
    );""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS tasks(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      plant_id INTEGER NOT NULL,
      kind TEXT NOT NULL,         -- 'water'|'feed'|'mist'
      due_date TEXT NOT NULL,     -- 'YYYY-MM-DD'
      status TEXT NOT NULL,       -- 'due'|'done'|'deferred'|'skipped'
      created_at TEXT NOT NULL
    );""")
    return c

def migrate_legacy_rows_to_user(user_id: int):
    c = conn()
    have_user = c.execute("SELECT 1 FROM plants WHERE user_id=?", (user_id,)).fetchone()
    legacy    = c.execute("SELECT 1 FROM plants WHERE user_id IS NULL").fetchone()
    if (not have_user) and legacy:
        c.execute("UPDATE plants SET user_id=? WHERE user_id IS NULL", (user_id,))
        c.execute("UPDATE tasks  SET user_id=? WHERE user_id IS NULL", (user_id,))
        c.commit()
    c.close()
