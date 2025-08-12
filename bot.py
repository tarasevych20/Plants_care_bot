# bot.py
import os, json, sqlite3, requests
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters, ConversationHandler
)

# ========= CONFIG =========
TOKEN = os.environ["TELEGRAM_TOKEN"]
PLANT_ID_API_KEY = os.environ.get("PLANT_ID_API_KEY", "")
TZ = ZoneInfo("Europe/Kyiv")

DB_PATH = "plants.db"
CARE_DAYS = [1, 4]  # 0=Mon ... 6=Sun  → 1=Tue, 4=Fri (max 2 care days/week)

# ========= DB & MIGRATIONS =========
def db():
    c = sqlite3.connect(DB_PATH)
    c.execute("""
    CREATE TABLE IF NOT EXISTS plants(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER,                 -- multi-user (legacy rows may be NULL)
      name TEXT NOT NULL,
      care TEXT NOT NULL,
      photo BLOB,
      water_int INTEGER,
      feed_int INTEGER,
      mist_int INTEGER,
      last_watered TEXT,
      last_fed TEXT,
      last_misted TEXT
    );
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS tasks(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      plant_id INTEGER NOT NULL,
      kind TEXT NOT NULL,             -- 'water' | 'feed' | 'mist'
      due_date TEXT NOT NULL,         -- YYYY-MM-DD
      status TEXT NOT NULL,           -- 'due' | 'done' | 'deferred' | 'skipped'
      created_at TEXT NOT NULL
    );
    """)
    return c

def migrate_legacy_rows_to_user(user_id: int):
    """If legacy rows have NULL user_id and this user has none yet, assign them to this user."""
    c = db()
    have_user = c.execute("SELECT 1 FROM plants WHERE user_id=?", (user_id,)).fetchone()
    legacy = c.execute("SELECT 1 FROM plants WHERE user_id IS NULL").fetchone()
    if (not have_user) and legacy:
        c.execute("UPDATE plants SET user_id=? WHERE user_id IS NULL", (user_id,))
        c.execute("UPDATE tasks  SET user_id=? WHERE user_id IS NULL", (user_id,))
        c.commit()
    c.close()

def iso_today() -> str:
    return date.today().isoformat()

# ========= CARE MAP & UTILITIES =========
def care_and_intervals_for(name: str):
    n = name.lower()
    # Zamioculcas
    if any(k in n for k in ["zamioculcas", "zz", "заміокулькас"]):
        return (
            "Світло: яскраве розсіяне/півтінь; вечірнє сонце допустиме.\n"
            "Полив: тільки після повного просихання ґрунту (~10–14 днів влітку).\n"
            "Підживлення: слабким добривом раз на 4–6 тижнів.\n"
            "Примітка: не переставляти під час росту нового пагона.",
            14, 42, None
        )
    # Dracaena
    if any(k in n for k in ["dracaena", "драцена"]):
        return (
            "Світло: яскраве розсіяне або півтінь; легке вечірнє сонце ок.\n"
            "Полив: після підсихання 2–3 см зверху.\n"
            "Після пересадки: 2–3 тижні без добрив; стежити за дренажем.\n"
            "Догляд: обприскування/протирання листя.",
            14, None, 7
        )
    # Chamaedorea (parlor palm)
    if any(k in n for k in ["chamaedorea", "parlor palm", "хамаедорея"]):
        return (
            "Світло: розсіяне, без прямого сонця.\n"
            "Полив: рівномірно вологий ґрунт (без застою).\n"
            "Догляд: регулярне обприскування.",
            5, 30, 3
        )
    # Spathiphyllum (peace lily)
    if any(k in n for k in ["spathiphyllum", "peace lily", "спатіфілум"]):
        return (
            "Світло: півтінь/розсіяне; пряме сонце уникати.\n"
            "Полив: ґрунт злегка вологий (влітку перевіряй кожні 3–4 дні).\n"
            "Підживлення: раз на 2 тижні.\n"
            "Догляд: обприскування та очищення листя.",
            4, 14, 3
        )
    # Calamondin / Citrus × microcarpa
    if any(k in n for k in ["calamondin", "citrus × microcarpa", "citrofortunella", "каламондин", "citrus"]):
        return (
            "Світло: дуже яскраве, 4–6 год вечірнього.\n"
            "Полив: злегка вологий ґрунт, без застою; влітку перевіряй частіше.\n"
            "Підживлення: цитрус-раз на 14 днів.\n"
            "Догляд: провітрювання; обприскування листя в спеку.",
            3, 14, 7
        )
    # Avocado (часто додають)
    if any(k in n for k in ["avocado", "persea americana", "авокадо"]):
        return (
            "Світло: яскраве, без жорсткого полуденного.\n"
            "Полив: після просихання 2–3 см зверху.\n"
            "Підживлення: раз на 3–4 тижні у період росту.",
            6, 28, None
        )
    # Обережний дефолт
    return (
        "Світло: яскраве розсіяне.\n"
        "Полив: після просихання верхнього шару ґрунту.\n"
        "Підживлення: за сезоном (кожні 3–4 тижні у період росту).",
        7, 28, None
    )

# ========= Plant.id: назва + similar image (для додавання за фото / оновлення фото) =========
def plantid_name_and_image(image_bytes: bytes):
    if not PLANT_ID_API_KEY:
        return (None, None)
    try:
        url = "https://api.plant.id/v2/identify"
        headers = {"Api-Key": PLANT_ID_API_KEY}
        files = {"images": image_bytes}
        data = {
            "modifiers": ["crops_fast", "similar_images"],
            "plant_language": "en",
            "plant_details": ["common_names", "url", "wiki_description"]
        }
        r = requests.post(url, headers=headers, files=files, data={"data": json.dumps(data)}, timeout=45).json()
        sug = (r.get("suggestions") or [])
        if not sug:
            return (None, None)
        name = sug[0].get("plant_name") or (sug[0].get("plant_details",{}).get("common_names") or [None])[0]
        sim = (sug[0].get("similar_images") or [])
        img = requests.get(sim[0]["url"], timeout=25).content if sim else None
        return (name, img)
    except Exception:
        return (None, None)

# ========= DATES / SCHEDULING (2 дні/тиждень) =========
def d_today(): return date.today()
def d_fromiso(s): return date.fromisoformat(s)
def d_toiso(d): return d.isoformat()

def next_care_day(from_day: date) -> date:
    wd = from_day.weekday()
    candidates = []
    for cd in CARE_DAYS:
        delta = (cd - wd) % 7
        candidates.append(from_day + timedelta(days=delta))
    return sorted(candidates)[0]

def following_care_day(after_day: date) -> date:
    wd = after_day.weekday()
    deltas = [((cd - wd) % 7) or 7 for cd in CARE_DAYS]
    return after_day + timedelta(days=min(deltas))

def ensure_week_tasks_for_user(user_id: int):
    """Створює/оновлює задачі на найближчі 7 днів, маплячи due на найближчі 'доглядові' дні."""
    c = db()
    rows = c.execute("""SELECT id,name,water_int,feed_int,mist_int,last_watered,last_fed,last_misted
                        FROM plants WHERE user_id=?""", (user_id,)).fetchall()
    today = d_today()
    horizon = today + timedelta(days=7)

    def schedule_if_due(plant_id, kind, interval, last_iso):
        if not interval:
            return
        last = d_fromiso(last_iso) if last_iso else today
        due = last + timedelta(days=interval)
        anchor = next_care_day(due)
        if anchor > horizon:
            return
        exists = c.execute("""SELECT 1 FROM tasks
                              WHERE user_id=? AND plant_id=? AND kind=? AND due_date=? AND status='due'""",
                           (user_id, plant_id, kind, d_toiso(anchor))).fetchone()
        if not exists:
            c.execute("""INSERT INTO tasks(user_id,plant_id,kind,due_date,status,created_at)
                         VALUES(?,?,?,?,?,?)""",
                      (user_id, plant_id, kind, d_toiso(anchor), 'due', d_toiso(today)))

    for pid,name,wi,fi,mi,lw,lf,lm in rows:
        schedule_if_due(pid,'water',wi,lw)
        schedule_if_due(pid,'feed', fi,lf)
        schedule_if_due(pid,'mist', mi,lm)

    c.commit(); c.close()

# ========= TASKS ACTIONS =========
def move_task_to_next_care_day(task_id: int):
    c = db()
    row = c.execute("SELECT user_id,plant_id,kind,due_date FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        c.close(); return
    user_id, plant_id, kind, due_iso = row
    new_due = following_care_day(d_fromiso(due_iso))
    c.execute("UPDATE tasks SET status='deferred' WHERE id=?", (task_id,))
    c.execute("""INSERT INTO tasks(user_id,plant_id,kind,due_date,status,created_at)
                 VALUES(?,?,?,?,?,?)""",
              (user_id, plant_id, kind, d_toiso(new_due), 'due', d_toiso(d_today())))
    c.commit(); c.close()

def mark_task_done(task_id: int):
    c = db()
    row = c.execute("""SELECT t.user_id,t.plant_id,t.kind
                       FROM tasks t WHERE t.id=?""", (task_id,)).fetchone()
    if not row:
        c.close(); return
    user_id, plant_id, kind = row
    today_iso = d_toiso(d_today())
    c.execute("UPDATE tasks SET status='done' WHERE id=?", (task_id,))
    field = 'last_watered' if kind=='water' else 'last_fed' if kind=='feed' else 'last_misted'
    c.execute(f"UPDATE plants SET {field}=? WHERE id=? AND user_id=?", (today_iso, plant_id, user_id))
    c.commit(); c.close()

def mark_task_skipped(task_id: int):
    c = db()
    c.execute("UPDATE tasks SET status='skipped' WHERE id=?", (task_id,))
    c.commit(); c.close()

# ========= KEYBOARDS / UI =========
def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 План на сьогодні", callback_data="today_plan")],
        [InlineKeyboardButton("📅 Розклад на тиждень", callback_data="week_plan")],
        [InlineKeyboardButton("🌿 Мої рослини", callback_data="my_plants")],
        [InlineKeyboardButton("➕ Додати рослину", callback_data="add_plant"),
         InlineKeyboardButton("🗑 Видалити", callback_data="delete_plant")],
    ])

def plants_list_kb(user_id: int):
    c = db()
    rows = c.execute("SELECT id,name FROM plants WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
    c.close()
    if not rows:
        btns = [[InlineKeyboardButton("➕ Додати першу рослину", callback_data="add_plant")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="back_home")]]
        return InlineKeyboardMarkup(btns)
    btns = [[InlineKeyboardButton(name, callback_data=f"plant_{pid}")] for (pid, name) in rows]
    btns.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_home")])
    return InlineKeyboardMarkup(btns)

def plant_card_kb(pid:int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Догляд", callback_data=f"care_{pid}")],
        [InlineKeyboardButton("📷 Додати/оновити фото", callback_data=f"addphoto_{pid}")],
        [InlineKeyboardButton("🔎 Фото з Plant.id", callback_data=f"plantidphoto_{pid}")],
        [InlineKeyboardButton("✅ Полив зроблено", callback_data=f"done_water_{pid}")],
        [InlineKeyboardButton("✅ Підживлення зроблено", callback_data=f"done_feed_{pid}")],
        [InlineKeyboardButton("✅ Обприскування зроблено", callback_data=f"done_mist_{pid}")],
        [InlineKeyboardButton("⬅️ До списку", callback_data="my_plants")]
    ])

# ========= RENDER: Today plan with per-task buttons =========
def today_tasks_markup_and_text(user_id: int):
    c = db()
    rows = c.execute("""
        SELECT t.id, t.kind, p.name
        FROM tasks t
        JOIN plants p ON p.id=t.plant_id
        WHERE t.user_id=? AND t.due_date=? AND t.status='due'
        ORDER BY p.name
    """, (user_id, d_toiso(d_today()))).fetchall()
    c.close()
    if not rows:
        return "Сьогодні завдань немає — відпочиваємо ✨", None

    kinds_map = {'water':'Полив', 'feed':'Підживлення', 'mist':'Обприскування'}
    grouped = {'water': [], 'feed': [], 'mist': []}
    kb_rows = []
    lines = ["План на сьогодні 🌱"]
    for tid, kind, name in rows:
        grouped[kind].append((tid, name))

    for kind in ['water','feed','mist']:
        if not grouped[kind]: continue
        lines.append(f"{kinds_map[kind]}:")
        for tid, name in grouped[kind]:
            lines.append(f"  • {name}")
            kb_rows.append([
                InlineKeyboardButton(f"✅ {name}", callback_data=f"task:{tid}:done"),
                InlineKeyboardButton("⏩ Відкласти", callback_data=f"task:{tid}:defer"),
                InlineKeyboardButton("🚫 Пропустити", callback_data=f"task:{tid}:skip"),
            ])
    return "\n".join(lines), InlineKeyboardMarkup(kb_rows)

# ========= RENDER: Week overview from tasks =========
def week_overview_text(user_id: int):
    c = db()
    rows = c.execute("""
        SELECT t.due_date, t.kind, p.name
        FROM tasks t JOIN plants p ON p.id=t.plant_id
        WHERE t.user_id=? AND t.status='due'
          AND date(t.due_date) BETWEEN date('now') AND date('now','+7 day')
        ORDER BY t.due_date, p.name
    """, (user_id,)).fetchall()
    c.close()
    if not rows:
        return "На найближчий тиждень завдань немає — все під контролем ✨"

    kinds_map = {'water':'Полив', 'feed':'Підживлення', 'mist':'Обприскування'}
    by_day = {}
    for due_iso, kind, name in rows:
        by_day.setdefault(due_iso, {}).setdefault(kind, []).append(name)

    lines = ["📅 Розклад на тиждень:"]
    for due_iso in sorted(by_day.keys()):
        d = d_fromiso(due_iso)
        lines.append(f"• {d.strftime('%d %B (%a)')}")
        for kind, names in by_day[due_iso].items():
            lines.append(f"  – {kinds_map[kind]}: {', '.join(sorted(names))}")
    return "\n".join(lines)

# ========= HANDLERS =========
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    migrate_legacy_rows_to_user(user_id)
    ensure_week_tasks_for_user(user_id)
    await update.message.reply_text("Привіт! Я бот догляду за рослинами 🌱", reply_markup=main_kb())

async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; data = q.data
    user_id = update.effective_user.id
    await q.answer()

    if data == "today_plan":
        ensure_week_tasks_for_user(user_id)
        text, kb = today_tasks_markup_and_text(user_id)
        await q.message.reply_text(text, reply_markup=kb or main_kb()); return

    if data == "week_plan":
        ensure_week_tasks_for_user(user_id)
        await q.message.reply_text(week_overview_text(user_id), reply_markup=main_kb()); return

    if data == "my_plants":
        await q.message.reply_text("Твої рослини:", reply_markup=plants_list_kb(user_id)); return

    if data.startswith("plant_"):
        pid = int(data.split("_")[1])
        c = db()
        row = c.execute("SELECT name, care, photo FROM plants WHERE id=? AND user_id=?", (pid, user_id)).fetchone()
        c.close()
        if not row:
            await q.message.reply_text("Не знайшов цю рослину 🤔", reply_markup=plants_list_kb(user_id)); return
        name, care, photo = row
        caption = f"*{name}*\n{care}"
        if photo:
            await q.message.reply_photo(photo=photo, caption=caption, parse_mode="Markdown", reply_markup=plant_card_kb(pid))
        else:
            await q.message.reply_text(caption, parse_mode="Markdown", reply_markup=plant_card_kb(pid))
        return

    if data.startswith("care_"):
        pid = int(data.split("_")[1])
        c = db(); name = c.execute("SELECT name FROM plants WHERE id=? AND user_id=?", (pid, user_id)).fetchone()
        c.close()
        if not name: await q.message.reply_text("Не знайшов.", reply_markup=plants_list_kb(user_id)); return
        care, *_ = care_and_intervals_for(name[0])
        await q.message.reply_text(care, reply_markup=plant_card_kb(pid)); return

    if data == "delete_plant":
        c = db()
        rows = c.execute("SELECT id,name FROM plants WHERE user_id=? ORDER BY name", (user_id,)).fetchall()
        c.close()
        btns = [[InlineKeyboardButton(f"🗑 {nm}", callback_data=f"del_{pid}")] for pid,nm in rows]
        btns.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_home")])
        await q.message.reply_text("Оберіть рослину для видалення:", reply_markup=InlineKeyboardMarkup(btns)); return

    if data.startswith("del_"):
        pid = int(data.split("_")[1])
        c = db(); c.execute("DELETE FROM plants WHERE id=? AND user_id=?", (pid, user_id)); c.commit(); c.close()
        await q.message.reply_text("Видалив ✅", reply_markup=plants_list_kb(user_id)); return

    if data == "add_plant":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Ввести назву вручну", callback_data="mode_name")],
            [InlineKeyboardButton("Фото (авто-розпізнавання)", callback_data="mode_photo")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back_home")]
        ])
        await q.message.reply_text("Як додамо рослину?", reply_markup=kb); return SELECT_ADD_MODE

    if data == "back_home":
        await q.message.reply_text("Головне меню:", reply_markup=main_kb()); return

    # Quick “done” buttons on plant card (creates instant task for today and closes it)
    if any(data.startswith(p) for p in ["done_water_", "done_feed_", "done_mist_"]):
        pid = int(data.split("_")[2])
        kind = 'water' if "water" in data else 'feed' if "feed" in data else 'mist'
        c = db()
        c.execute("""INSERT INTO tasks(user_id,plant_id,kind,due_date,status,created_at)
                     VALUES(?,?,?,?,?,?)""", (user_id, pid, kind, iso_today(), 'due', iso_today()))
        tid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.commit(); c.close()
        mark_task_done(tid)
        await q.message.reply_text("Записав ✅", reply_markup=plant_card_kb(pid)); return

# ====== ADD FLOW ======
SELECT_ADD_MODE, ADD_NAME, ADD_PHOTO_NEW, ADD_PHOTO_EXIST, ADD_PHOTO_PLANTID = range(5)

async def add_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; data = q.data
    await q.answer()
    if data == "mode_name":
        await q.message.reply_text("Введи назву рослини одним повідомленням:"); return ADD_NAME
    if data == "mode_photo":
        await q.message.reply_text("Надішли фото рослини одним повідомленням."); return ADD_PHOTO_NEW
    if data == "back_home":
        await q.message.reply_text("Головне меню:", reply_markup=main_kb()); return ConversationHandler.END

async def on_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("Порожня назва. Спробуй ще раз."); return ADD_NAME
    care, wi, fi, mi = care_and_intervals_for(name)
    c = db()
    c.execute("""INSERT INTO plants(user_id,name,care,photo,water_int,feed_int,mist_int,
                 last_watered,last_fed,last_misted)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (user_id, name, care, None, wi, fi, mi, iso_today(), iso_today(), iso_today()))
    c.commit(); c.close()
    ensure_week_tasks_for_user(user_id)
    await update.message.reply_text(f"Додав «{name}» ✅\nРозклад оновлено.", reply_markup=main_kb())
    return ConversationHandler.END

async def on_add_photo_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not update.message.photo:
        await update.message.reply_text("Це не фото 🙃 Надішли зображення."); return ADD_PHOTO_NEW
    file = await update.message.photo[-1].get_file()
    img = await file.download_as_bytearray()
    name, ref_img = plantid_name_and_image(bytes(img))
    name = name or "Нова рослина"
    care, wi, fi, mi = care_and_intervals_for(name)
    photo = ref_img or bytes(img)
    c = db()
    c.execute("""INSERT INTO plants(user_id,name,care,photo,water_int,feed_int,mist_int,
                 last_watered,last_fed,last_misted)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (user_id, name, care, photo, wi, fi, mi, iso_today(), iso_today(), iso_today()))
    c.commit(); c.close()
    ensure_week_tasks_for_user(user_id)
    await update.message.reply_text(f"Додав «{name}» ✅\nРозклад оновлено.", reply_markup=main_kb())
    return ConversationHandler.END

# ====== UPDATE PHOTO (manual & Plant.id) ======
async def start_update_photo_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    pid = int(q.data.split("_")[1]); context.user_data["target_pid"] = pid
    await q.message.reply_text("Надішли одне фото цієї рослини (jpg/png)."); return ADD_PHOTO_EXIST

async def on_add_photo_exist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not update.message.photo:
        await update.message.reply_text("Це не фото 🙃 Надішли зображення."); return ADD_PHOTO_EXIST
    pid = context.user_data.get("target_pid")
    file = await update.message.photo[-1].get_file()
    img = await file.download_as_bytearray()
    c = db(); c.execute("UPDATE plants SET photo=? WHERE id=? AND user_id=?", (bytes(img), pid, user_id)); c.commit(); c.close()
    await update.message.reply_text("Фото оновив ✅", reply_markup=main_kb())
    return ConversationHandler.END

async def start_update_photo_plantid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    pid = int(q.data.split("_")[1]); context.user_data["target_pid_pid"] = pid
    await q.message.reply_text("Надішли фото цієї рослини — підтягну зображення з Plant.id."); return ADD_PHOTO_PLANTID

async def on_add_photo_plantid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not update.message.photo:
        await update.message.reply_text("Це не фото 🙃 Надішли зображення."); return ADD_PHOTO_PLANTID
    if not PLANT_ID_API_KEY:
        await update.message.reply_text("PLANT_ID_API_KEY не заданий у Variables."); return ConversationHandler.END
    pid = context.user_data.get("target_pid_pid")
    file = await update.message.photo[-1].get_file()
    img = await file.download_as_bytearray()
    _, ref_img = plantid_name_and_image(bytes(img))
    if not ref_img:
        await update.message.reply_text("Не вдалося підтягнути фото з Plant.id. Залишаю без змін.")
        return ConversationHandler.END
    c = db(); c.execute("UPDATE plants SET photo=? WHERE id=? AND user_id=?", (ref_img, pid, user_id)); c.commit(); c.close()
    await update.message.reply_text("Замінено фото на зображення з Plant.id ✅", reply_markup=main_kb())
    return ConversationHandler.END

# ====== TASK CALLBACKS (✅/⏩/🚫) ======
async def on_task_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        _, tid_str, action = q.data.split(":")  # "task:<id>:done|defer|skip"
        tid = int(tid_str)
    except Exception:
        await q.answer("Помилка callback"); return
    if action == "done":
        mark_task_done(tid); await q.answer("Готово ✅")
    elif action == "defer":
        move_task_to_next_care_day(tid); await q.answer("Перенесено ⏩")
    elif action == "skip":
        mark_task_skipped(tid); await q.answer("Пропущено 🚫")
    # Refresh message
    user_id = update.effective_user.id
    text, kb = today_tasks_markup_and_text(user_id)
    await q.message.edit_text(text, reply_markup=kb or main_kb())

# ========= APP =========
def build_app():
    app = ApplicationBuilder().token(TOKEN).build()

    # add plant conversation
    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_choose, pattern="^(mode_name|mode_photo|back_home)$")],
        states={
            SELECT_ADD_MODE: [CallbackQueryHandler(add_choose, pattern="^(mode_name|mode_photo|back_home)$")],
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_add_name)],
            ADD_PHOTO_NEW: [MessageHandler(filters.PHOTO, on_add_photo_new)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.effective_message.reply_text("Скасовано.", reply_markup=main_kb()))],
        map_to_parent={ConversationHandler.END: ConversationHandler.END}
    )

    # update photo manual
    upd_photo_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_update_photo_manual, pattern=r"^addphoto_\d+$")],
        states={ ADD_PHOTO_EXIST: [MessageHandler(filters.PHOTO, on_add_photo_exist)] },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.effective_message.reply_text("Скасовано.", reply_markup=main_kb()))],
    )

    # update photo via Plant.id
    plantid_photo_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_update_photo_plantid, pattern=r"^plantidphoto_\d+$")],
        states={ ADD_PHOTO_PLANTID: [MessageHandler(filters.PHOTO, on_add_photo_plantid)] },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.effective_message.reply_text("Скасовано.", reply_markup=main_kb()))],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(add_conv)
    app.add_handler(upd_photo_conv)
    app.add_handler(plantid_photo_conv)
    app.add_handler(CallbackQueryHandler(on_task_action, pattern=r"^task:\d+:(done|defer|skip)$"))
    app.add_handler(CallbackQueryHandler(router))  # catch-all router last
    return app

if __name__ == "__main__":
    # start-up ping (to make sure deploy works)
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json={"chat_id": os.environ.get("TELEGRAM_CHAT_ID"),
                            "text": "✅ Бот оновлено. Доступні: «📋 План на сьогодні», «📅 Розклад на тиждень», «🌿 Мої рослини»"},
                      timeout=10)
    except Exception:
        pass

    build_app().run_polling(allowed_updates=Update.ALL_TYPES)
