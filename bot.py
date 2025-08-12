# bot.py
import os, json, sqlite3, requests, datetime as dt
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters, ConversationHandler
)

# ====== CONFIG ======
TOKEN = os.environ["TELEGRAM_TOKEN"]
PLANT_ID_API_KEY = os.environ.get("PLANT_ID_API_KEY", "")
TZ = ZoneInfo("Europe/Kyiv")
DB_PATH = "plants.db"

# ====== DB ======
def db():
    c = sqlite3.connect(DB_PATH)
    c.execute("""
    CREATE TABLE IF NOT EXISTS plants(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      care TEXT NOT NULL,
      photo BLOB,
      water_int INTEGER,      -- днів між поливами
      feed_int INTEGER,       -- днів між підживленнями (NULL якщо не треба)
      mist_int INTEGER,       -- днів між обприскуваннями (NULL якщо не треба)
      last_watered TEXT,      -- ISO дата
      last_fed TEXT,
      last_misted TEXT
    );
    """)
    return c

def seed_if_empty():
    c = db()
    n = c.execute("SELECT COUNT(*) FROM plants").fetchone()[0]
    if n == 0:
        rows = [
            ("Драцена",
             "Світло: яскраве розсіяне або півтінь; легке вечірнє сонце ок.\n"
             "Полив: після підсихання 2–3 см зверху.\n"
             "Після пересадки: 2–3 тижні без добрив; слідкуй за дренажем.\n"
             "Догляд: обприскування/протирання листя.",
             None, 14, None, 7, iso_today(), None, iso_today()),
            ("Каламондин",
             "Світло: дуже яскраве, 4–6 год вечірнього.\n"
             "Полив: злегка вологий ґрунт, без застою (влітку перевіряй кожні 2–3 дні).\n"
             "Підживлення: цитрус-раз на 14 днів.",
             None, 3, 14, 7, iso_today(), iso_today(), iso_today()),
            ("Хамаедорея",
             "Світло: розсіяне, без прямого сонця.\n"
             "Полив: рівномірно вологий ґрунт (без застою).\n"
             "Догляд: регулярне обприскування.",
             None, 5, 30, 3, iso_today(), iso_today(), iso_today()),
            ("Заміокулькас",
             "Світло: яскраве розсіяне/півтінь; вечірнє сонце допустиме.\n"
             "Полив: тільки після повного просихання ґрунту (~10–14 днів влітку).\n"
             "Підживлення: слабким добривом раз на 4–6 тижнів.\n"
             "Примітка: не переставляти під час росту нового пагона.",
             None, 14, 42, None, iso_today(), iso_today(), None),
            ("Спатіфілум",
             "Світло: півтінь/розсіяне; пряме сонце уникати.\n"
             "Полив: ґрунт злегка вологий (влітку перевіряй кожні 3–4 дні).\n"
             "Підживлення: раз на 2 тижні.\n"
             "Догляд: обприскування та очищення листя.",
             None, 4, 14, 3, iso_today(), iso_today(), iso_today()),
        ]
        c.executemany("""INSERT INTO plants
            (name, care, photo, water_int, feed_int, mist_int, last_watered, last_fed, last_misted)
            VALUES(?,?,?,?,?,?,?,?,?)""", rows)
        c.commit()
    c.close()

def iso_today():
    return dt.date.today().isoformat()

seed_if_empty()

# ====== CARE + INTERVALS (мапа за видами) ======
def care_and_intervals_for(name: str):
    n = name.lower()
    # заміокулькас
    if any(k in n for k in ["zamioculcas", "zz", "заміокулькас"]):
        return ( "Світло: яскраве розсіяне/півтінь; вечірнє сонце допустиме.\n"
                 "Полив: тільки після повного просихання ґрунту (~10–14 днів влітку).\n"
                 "Підживлення: слабким добривом раз на 4–6 тижнів.\n"
                 "Примітка: не переставляти під час росту нового пагона.",
                 14, 42, None )
    # драцена
    if any(k in n for k in ["dracaena", "драцена"]):
        return ( "Світло: яскраве розсіяне або півтінь; легке вечірнє сонце ок.\n"
                 "Полив: після підсихання 2–3 см зверху.\n"
                 "Після пересадки: 2–3 тижні без добрив; слідкуй за дренажем.\n"
                 "Догляд: обприскування/протирання листя.",
                 14, None, 7 )
    # хамаедорея
    if any(k in n for k in ["chamaedorea", "parlor palm", "хамаедорея"]):
        return ( "Світло: розсіяне, без прямого сонця.\n"
                 "Полив: рівномірно вологий ґрунт (без застою).\n"
                 "Догляд: регулярне обприскування.",
                 5, 30, 3 )
    # спатіфілум
    if any(k in n for k in ["spathiphyllum", "peace lily", "спатіфілум"]):
        return ( "Світло: півтінь/розсіяне; пряме сонце уникати.\n"
                 "Полив: ґрунт злегка вологий (влітку перевіряй кожні 3–4 дні).\n"
                 "Підживлення: раз на 2 тижні.\n"
                 "Догляд: обприскування та очищення листя.",
                 4, 14, 3 )
    # каламондин/цитрус
    if any(k in n for k in ["calamondin", "citrus × microcarpa", "citrofortunella", "каламондин", "citrus"]):
        return ( "Світло: дуже яскраве, 4–6 год вечірнього.\n"
                 "Полив: злегка вологий ґрунт, без застою; влітку перевіряй частіше.\n"
                 "Підживлення: цитрус-раз на 14 днів.\n"
                 "Догляд: провітрювання; обприскування листя в спеку.",
                 3, 14, 7 )
    # авокадо (часто додають)
    if any(k in n for k in ["avocado", "persea americana", "авокадо"]):
        return ( "Світло: яскраве, без жорсткого полуденного.\n"
                 "Полив: після просихання 2–3 см зверху.\n"
                 "Підживлення: раз на 3–4 тижні у період росту.",
                 6, 28, None )
    # дефолт (акуратний)
    return ( "Світло: яскраве розсіяне.\n"
             "Полив: після просихання верхнього шару ґрунту.\n"
             "Підживлення: за сезоном (кожні 3–4 тижні у період росту).",
             7, 28, None )

# ====== Plant.id: назва + similar image (за фото) ======
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
        if not sug: return (None, None)
        name = sug[0].get("plant_name") or (sug[0].get("plant_details",{}).get("common_names") or [None])[0]
        sim = (sug[0].get("similar_images") or [])
        img = requests.get(sim[0]["url"], timeout=25).content if sim else None
        return (name, img)
    except Exception:
        return (None, None)

# ====== UI ======
def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌿 Мої рослини", callback_data="my_plants")],
        [InlineKeyboardButton("📅 Розклад на тиждень", callback_data="week_plan")],
        [InlineKeyboardButton("➕ Додати рослину", callback_data="add_plant"),
         InlineKeyboardButton("🗑 Видалити", callback_data="delete_plant")],
    ])

def plants_list_kb():
    c = db()
    rows = c.execute("SELECT id, name FROM plants ORDER BY name").fetchall()
    c.close()
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

# ====== STATES ======
SELECT_ADD_MODE, ADD_NAME, ADD_PHOTO_NEW, ADD_PHOTO_EXIST, ADD_PHOTO_PLANTID = range(5)

# ====== HELPERS ======
def today(): return dt.date.today()
def add_days(d, n): return d + dt.timedelta(days=n)

def next_due(last_iso, interval):
    if not interval: return None
    base = dt.date.fromisoformat(last_iso) if last_iso else today()
    return add_days(base, interval)

def week_schedule():
    """Повертає тексти на 7 днів уперед, згруповані по датах."""
    c = db()
    rows = c.execute("""SELECT id,name,water_int,feed_int,mist_int,last_watered,last_fed,last_misted
                        FROM plants""").fetchall()
    c.close()
    events = {}
    for pid,name,wi,fi,mi,lw,lf,lm in rows:
        w = next_due(lw, wi) if wi else None
        f = next_due(lf, fi) if fi else None
        m = next_due(lm, mi) if mi else None
        for when, kind in [(w,"Полив"), (f,"Підживлення"), (m,"Обприскування")]:
            if when and 0 <= (when - today()).days <= 7:
                events.setdefault(when, {}).setdefault(kind, []).append(name)
    if not events:
        return "На найближчий тиждень завдань немає — все під контролем ✨"
    lines = []
    for day in sorted(events.keys()):
        head = day.strftime("%d %B (%a)")
        lines.append(f"• {head}")
        for kind, names in events[day].items():
            lines.append(f"  – {kind}: {', '.join(sorted(names))}")
    return "📅 Розклад на тиждень:\n" + "\n".join(lines)

# ====== HANDLERS ======
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привіт! Я бот догляду за рослинами 🌱", reply_markup=main_kb())

async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; data = q.data
    await q.answer()

    if data == "my_plants":
        await q.message.reply_text("Твої рослини:", reply_markup=plants_list_kb())
        return

    if data == "week_plan":
        await q.message.reply_text(week_schedule(), reply_markup=main_kb())
        return

    if data.startswith("plant_"):
        pid = int(data.split("_")[1])
        c = db()
        row = c.execute("SELECT name, care, photo FROM plants WHERE id=?", (pid,)).fetchone()
        c.close()
        if not row:
            await q.message.reply_text("Не знайшов цю рослину 🤔", reply_markup=plants_list_kb()); return
        name, care, photo = row
        caption = f"*{name}*\n{care}"
        if photo:
            await q.message.reply_photo(photo=photo, caption=caption, parse_mode="Markdown", reply_markup=plant_card_kb(pid))
        else:
            await q.message.reply_text(caption, parse_mode="Markdown", reply_markup=plant_card_kb(pid))
        return

    if data.startswith("care_"):
        pid = int(data.split("_")[1])
        c = db()
        name = c.execute("SELECT name FROM plants WHERE id=?", (pid,)).fetchone()[0]
        c.close()
        await q.message.reply_text(care_and_intervals_for(name)[0], reply_markup=plant_card_kb(pid))
        return

    if data == "delete_plant":
        c = db()
        rows = c.execute("SELECT id,name FROM plants ORDER BY name").fetchall()
        c.close()
        btns = [[InlineKeyboardButton(f"🗑 {nm}", callback_data=f"del_{pid}")] for pid,nm in rows]
        btns.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_home")])
        await q.message.reply_text("Оберіть рослину для видалення:", reply_markup=InlineKeyboardMarkup(btns))
        return

    if data.startswith("del_"):
        pid = int(data.split("_")[1])
        c = db(); c.execute("DELETE FROM plants WHERE id=?", (pid,)); c.commit(); c.close()
        await q.message.reply_text("Видалив ✅", reply_markup=plants_list_kb()); return

    if data == "add_plant":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Ввести назву вручну", callback_data="mode_name")],
            [InlineKeyboardButton("Фото (авто-розпізнавання)", callback_data="mode_photo")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back_home")]
        ])
        await q.message.reply_text("Як додамо рослину?", reply_markup=kb)
        return SELECT_ADD_MODE

    if data == "back_home":
        await q.message.reply_text("Головне меню:", reply_markup=main_kb())
        return

    # відмітки виконання
    if any(data.startswith(p) for p in ["done_water_", "done_feed_", "done_mist_"]):
        pid = int(data.split("_")[2])
        field = "last_watered" if "water" in data else "last_fed" if "feed" in data else "last_misted"
        c = db(); c.execute(f"UPDATE plants SET {field}=? WHERE id=?", (iso_today(), pid)); c.commit(); c.close()
        await q.message.reply_text("Записав ✅", reply_markup=plant_card_kb(pid))
        return

    # фото вручну
    if data.startswith("addphoto_"):
        pid = int(data.split("_")[1]); context.user_data["target_pid"] = pid
        await q.message.reply_text("Надішли одне фото цієї рослини (jpg/png).")
        return ADD_PHOTO_EXIST

    # фото з Plant.id
    if data.startswith("plantidphoto_"):
        pid = int(data.split("_")[1]); context.user_data["target_pid_pid"] = pid
        await q.message.reply_text("Надішли фото цієї рослини — підтягну зображення з Plant.id.")
        return ADD_PHOTO_PLANTID

# ---- ADD FLOW (назва / фото) ----
async def add_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; data = q.data
    await q.answer()
    if data == "mode_name":
        await q.message.reply_text("Введи назву рослини одним повідомленням:")
        return ADD_NAME
    if data == "mode_photo":
        await q.message.reply_text("Надішли фото рослини одним повідомленням.")
        return ADD_PHOTO_NEW
    if data == "back_home":
        await q.message.reply_text("Головне меню:", reply_markup=main_kb())
        return ConversationHandler.END

async def on_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("Порожня назва. Спробуй ще раз."); return ADD_NAME
    care, wi, fi, mi = care_and_intervals_for(name)
    c = db()
    c.execute("""INSERT INTO plants(name, care, photo, water_int, feed_int, mist_int,
                last_watered, last_fed, last_misted)
                VALUES(?,?,?,?,?,?,?,?,?)""",
              (name, care, None, wi, fi, mi, iso_today(), iso_today(), iso_today()))
    c.commit(); c.close()
    await update.message.reply_text(f"Додав «{name}» ✅\nРозклад оновлено.", reply_markup=main_kb())
    return ConversationHandler.END

async def on_add_photo_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Це не фото 🙃 Надішли зображення."); return ADD_PHOTO_NEW
    file = await update.message.photo[-1].get_file()
    img = await file.download_as_bytearray()
    name, ref_img = plantid_name_and_image(bytes(img))
    name = name or "Нова рослина"
    care, wi, fi, mi = care_and_intervals_for(name)
    photo = ref_img or bytes(img)
    c = db()
    c.execute("""INSERT INTO plants(name, care, photo, water_int, feed_int, mist_int,
                last_watered, last_fed, last_misted)
                VALUES(?,?,?,?,?,?,?,?,?)""",
              (name, care, photo, wi, fi, mi, iso_today(), iso_today(), iso_today()))
    c.commit(); c.close()
    await update.message.reply_text(f"Додав «{name}» ✅\nРозклад оновлено.", reply_markup=main_kb())
    return ConversationHandler.END

# ---- UPDATE PHOTO (manual) ----
async def on_add_photo_exist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Це не фото 🙃 Надішли зображення."); return ADD_PHOTO_EXIST
    pid = context.user_data.get("target_pid")
    file = await update.message.photo[-1].get_file()
    img = await file.download_as_bytearray()
    c = db(); c.execute("UPDATE plants SET photo=? WHERE id=?", (bytes(img), pid)); c.commit(); c.close()
    await update.message.reply_text("Фото оновив ✅", reply_markup=main_kb())
    return ConversationHandler.END

# ---- UPDATE PHOTO via Plant.id ----
async def on_add_photo_plantid(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    c = db(); c.execute("UPDATE plants SET photo=? WHERE id=?", (ref_img, pid)); c.commit(); c.close()
    await update.message.reply_text("Замінено фото на зображення з Plant.id ✅", reply_markup=main_kb())
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скасовано.", reply_markup=main_kb())
    return ConversationHandler.END

# ====== BOOTSTRAP ======
def build_app():
    app = ApplicationBuilder().token(TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_choose, pattern="^(mode_name|mode_photo|back_home)$")],
        states={
            SELECT_ADD_MODE: [CallbackQueryHandler(add_choose, pattern="^(mode_name|mode_photo|back_home)$")],
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_add_name)],
            ADD_PHOTO_NEW: [MessageHandler(filters.PHOTO, on_add_photo_new)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        map_to_parent={ConversationHandler.END: ConversationHandler.END}
    )

    upd_photo_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(router, pattern=r"^addphoto_\d+$")],
        states={ ADD_PHOTO_EXIST: [MessageHandler(filters.PHOTO, on_add_photo_exist)] },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    plantid_photo_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(router, pattern=r"^plantidphoto_\d+$")],
        states={ ADD_PHOTO_PLANTID: [MessageHandler(filters.PHOTO, on_add_photo_plantid)] },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(add_conv)
    app.add_handler(upd_photo_conv)
    app.add_handler(plantid_photo_conv)
    app.add_handler(CallbackQueryHandler(router))  # загальний наприкінці

    return app

if __name__ == "__main__":
    # стартовий пінг (щоб бачити, що бот живий)
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json={"chat_id": os.environ.get("TELEGRAM_CHAT_ID"),
                            "text": "✅ Бот запущено. Натисни «📅 Розклад на тиждень» або «🌿 Мої рослини»"},
                      timeout=10)
    except Exception:
        pass

    build_app().run_polling(allowed_updates=Update.ALL_TYPES)
