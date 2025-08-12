# bot.py
import os, io, json, requests, sqlite3, datetime as dt
from PIL import Image
from zoneinfo import ZoneInfo
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters, ConversationHandler
)

# ---------- CONFIG ----------
TOKEN = os.environ["TELEGRAM_TOKEN"]
OWM_KEY = os.environ.get("OPENWEATHER_API_KEY", "")
PLANT_ID_API_KEY = os.environ.get("PLANT_ID_API_KEY", "")  # опц. для авто-розпізнавання
CITY = "Kyiv"
TZ = ZoneInfo("Europe/Kyiv")
DB_PATH = "plants.db"
WIKI_LANG = os.environ.get("WIKIMEDIA_LANG", "uk")  # мова для пошуку вікі

# ---------- DB ----------
def conn_db():
    c = sqlite3.connect(DB_PATH)
    c.execute("""
    CREATE TABLE IF NOT EXISTS plants(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        care TEXT NOT NULL,
        photo BLOB
    );
    """)
    return c

def seed_if_empty():
    c = conn_db()
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM plants;")
    if cur.fetchone()[0] == 0:
        plants = [
            ("Драцена",
             "Світло: яскраве розсіяне. Полив: після просихання 2–3 см. Після пересадки — без добрив 2–3 тижні.",
             None),
            ("Каламондин",
             "Світло: дуже яскраве, трохи вечірнього сонця збоку від вікна. Полив: ґрунт злегка вологий, перевіряй кожні 2–3 дні влітку. Підживлення: раз на 2 тижні цитрус.",
             None),
            ("Хамаедорея",
             "Світло: розсіяне, без прямого. Полив: рівномірно вологий ґрунт. Обприскування.",
             None),
            ("Заміокулькас",
             "Світло: яскраве розсіяне/півтінь. Полив: лише після повного просихання (10–14 днів влітку). Підживлення: слабке раз на 4–6 тижнів.",
             None),
            ("Спатіфілум",
             "Світло: півтінь/розсіяне. Полив: ґрунт злегка вологий, перевіряй кожні 3–4 дні влітку. Підживлення: раз на 2 тижні.",
             None),
        ]
        c.executemany("INSERT INTO plants(name, care, photo) VALUES(?,?,?);", plants)
        c.commit()
    c.close()

seed_if_empty()

# ---------- HELPERS: Wikimedia image search ----------
def _download_url_bytes(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=20)
        if r.status_code == 200:
            return r.content
    except Exception:
        pass
    return None

def fetch_wikimedia_image(name: str) -> bytes | None:
    """
    Шукаємо зображення через Wikimedia (без ключів).
    1) пошук сторінки по назві
    2) беремо прев’юшку (thumbnail / original)
    """
    try:
        # 1) search
        srch = requests.get(
            f"https://{WIKI_LANG}.wikipedia.org/w/api.php",
            params={"action":"query","list":"search","srsearch":name,"format":"json","srlimit":1},
            timeout=20
        ).json()
        hits = srch.get("query",{}).get("search",[])
        if not hits: return None
        pageid = hits[0]["pageid"]
        # 2) page image
        info = requests.get(
            f"https://{WIKI_LANG}.wikipedia.org/w/api.php",
            params={
                "action":"query","pageids":pageid,"prop":"pageimages|imageinfo|images",
                "pithumbsize":800,"format":"json"
            }, timeout=20
        ).json()
        pages = info.get("query",{}).get("pages",{})
        page = pages.get(str(pageid))
        thumb = page.get("thumbnail",{}).get("source")
        if thumb:
            return _download_url_bytes(thumb)
    except Exception:
        pass
    return None

# ---------- PLANT.ID (optional for photo-flow) ----------
def plantid_similar_image(image_bytes: bytes) -> bytes | None:
    if not PLANT_ID_API_KEY:  # немає ключа
        return None
    try:
        url = "https://api.plant.id/v2/identify"
        files = {"images": image_bytes}
        data = {
            "modifiers": ["crops_fast", "similar_images"],
            "plant_language": "uk",
            "plant_details": ["common_names", "wiki_description", "url"]
        }
        headers = {"Api-Key": PLANT_ID_API_KEY}
        resp = requests.post(url, headers=headers, files=files, data={"data": json.dumps(data)}, timeout=40)
        j = resp.json()
        sim = j.get("suggestions",[{}])[0].get("similar_images",[])
        if sim:
            url0 = sim[0].get("url")
            return _download_url_bytes(url0) if url0 else None
    except Exception:
        pass
    return None

# ---------- UI ----------
def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌿 Мої рослини", callback_data="my_plants")],
        [
            InlineKeyboardButton("➕ Додати рослину", callback_data="add_plant"),
            InlineKeyboardButton("🗑 Видалити", callback_data="delete_plant")
        ],
    ])

def plants_list_kb():
    c = conn_db()
    rows = c.execute("SELECT id, name FROM plants ORDER BY name;").fetchall()
    c.close()
    buttons = [[InlineKeyboardButton(name, callback_data=f"plant_{pid}")] for (pid, name) in rows]
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_home")])
    return InlineKeyboardMarkup(buttons)

def plant_card_kb(pid: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Додати/оновити фото", callback_data=f"addphoto_{pid}")],
        [InlineKeyboardButton("⬅️ До списку", callback_data="my_plants")]
    ])

def delete_list_kb():
    c = conn_db()
    rows = c.execute("SELECT id, name FROM plants ORDER BY name;").fetchall()
    c.close()
    buttons = [[InlineKeyboardButton(f"🗑 {name}", callback_data=f"del_{pid}")] for (pid, name) in rows]
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_home")])
    return InlineKeyboardMarkup(buttons)

# ---------- CONVERSATION STATES ----------
SELECT_ADD_MODE, ADD_NAME, ADD_PHOTO_NEW, ADD_PHOTO_EXIST = range(4)

# ---------- HANDLERS ----------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привіт! Я бот догляду за рослинами 🌱", reply_markup=main_kb())

async def cb_main_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    await q.answer()

    if data == "my_plants":
        await q.message.reply_text("Твої рослини:", reply_markup=plants_list_kb())

    elif data.startswith("plant_"):
        pid = int(data.split("_")[1])
        c = conn_db()
        row = c.execute("SELECT name, care, photo FROM plants WHERE id=?;", (pid,)).fetchone()
        c.close()
        if not row:
            await q.message.reply_text("Не знайшов цю рослину 🤔", reply_markup=plants_list_kb())
            return
        name, care, photo = row
        caption = f"*{name}*\n{care}"
        if photo:
            await q.message.reply_photo(photo=photo, caption=caption, parse_mode="Markdown", reply_markup=plant_card_kb(pid))
        else:
            await q.message.reply_text(caption, parse_mode="Markdown", reply_markup=plant_card_kb(pid))

    elif data == "delete_plant":
        await q.message.reply_text("Оберіть рослину для видалення:", reply_markup=delete_list_kb())

    elif data.startswith("del_"):
        pid = int(data.split("_")[1])
        c = conn_db()
        c.execute("DELETE FROM plants WHERE id=?;", (pid,))
        c.commit(); c.close()
        await q.message.reply_text("Готово. Видалив ✅", reply_markup=plants_list_kb())

    elif data == "add_plant":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Ввести назву вручну", callback_data="mode_name")],
            [InlineKeyboardButton("Фото (авто-розпізнавання)", callback_data="mode_photo")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back_home")]
        ])
        await q.message.reply_text("Як додамо рослину?", reply_markup=kb)
        return SELECT_ADD_MODE

    elif data == "back_home":
        await q.message.reply_text("Головне меню:", reply_markup=main_kb())

    elif data.startswith("addphoto_"):
        pid = int(data.split("_")[1])
        context.user_data["target_pid"] = pid
        await q.message.reply_text("Надішли одне фото цієї рослини (jpg/png).")
        return ADD_PHOTO_EXIST

# ----- ADD FLOW (Conversation) -----
async def cb_add_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
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
        await update.message.reply_text("Порожня назва. Спробуй ще раз.")
        return ADD_NAME
    care = "Світло: яскраве розсіяне. Полив: після просихання верхнього шару. Підживлення: за сезоном."
    # спробуємо одразу знайти фото
    photo_bytes = fetch_wikimedia_image(name)
    c = conn_db()
    c.execute("INSERT INTO plants(name, care, photo) VALUES(?,?,?);", (name, care, photo_bytes))
    c.commit(); c.close()
    await update.message.reply_text(f"Додав «{name}» ✅", reply_markup=main_kb())
    return ConversationHandler.END

async def on_add_photo_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Це не фото 🙃 Надішли зображення.")
        return ADD_PHOTO_NEW
    file = await update.message.photo[-1].get_file()
    img_bytes = await file.download_as_bytearray()

    # пробуємо Plant.id similar image
    ref = plantid_similar_image(bytes(img_bytes))
    if ref is None:
        # fallback: спробуємо вгадати назву грубо через вики (часто українська/латинська назва збігається)
        guessed_name = "Нова рослина"
        ref = None
    else:
        guessed_name = "Нова рослина"

    # якщо є ref – збережемо його як фото; інакше — твоє фото
    photo_bytes = ref or bytes(img_bytes)
    care = "Світло: яскраве розсіяне. Полив: після просихання верхнього шару. Підживлення: за сезоном."
    c = conn_db()
    c.execute("INSERT INTO plants(name, care, photo) VALUES(?,?,?);", (guessed_name, care, photo_bytes))
    c.commit(); c.close()
    await update.message.reply_text(f"Додав «{guessed_name}» ✅", reply_markup=main_kb())
    return ConversationHandler.END

async def on_add_photo_exist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Це не фото 🙃 Надішли зображення.")
        return ADD_PHOTO_EXIST
    pid = context.user_data.get("target_pid")
    if not pid:
        await update.message.reply_text("Щось пішло не так. Спробуй ще раз з картки рослини.")
        return ConversationHandler.END
    file = await update.message.photo[-1].get_file()
    img_bytes = await file.download_as_bytearray()
    # можна замінити на Plant.id similar image, але тут логічно зберігати саме твоє фото
    c = conn_db()
    c.execute("UPDATE plants SET photo=? WHERE id=?;", (bytes(img_bytes), pid))
    c.commit(); c.close()
    await update.message.reply_text("Фото оновив ✅", reply_markup=main_kb())
    return ConversationHandler.END

# Fallbacks
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скасовано.", reply_markup=main_kb())
    return ConversationHandler.END

# ---------- BACKFILL PHOTOS FOR EXISTING ----------
def backfill_missing_photos():
    c = conn_db()
    rows = c.execute("SELECT id, name FROM plants WHERE photo IS NULL;").fetchall()
    updated = 0
    for pid, name in rows:
        img = fetch_wikimedia_image(name)
        if img:
            c.execute("UPDATE plants SET photo=? WHERE id=?;", (img, pid))
            updated += 1
    c.commit(); c.close()
    return updated

# ---------- APP ----------
def app():
    application = ApplicationBuilder().token(TOKEN).build()

    # окремий конверсейшн для додавання
    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_add_choose, pattern="^(mode_name|mode_photo|back_home)$")],
        states={
            SELECT_ADD_MODE: [CallbackQueryHandler(cb_add_choose, pattern="^(mode_name|mode_photo|back_home)$")],
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_add_name)],
            ADD_PHOTO_NEW: [MessageHandler(filters.PHOTO, on_add_photo_new)],
            ADD_PHOTO_EXIST: [MessageHandler(filters.PHOTO, on_add_photo_exist)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        map_to_parent={ConversationHandler.END: ConversationHandler.END}
    )

    # базові хендлери
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(add_conv)
    application.add_handler(CallbackQueryHandler(cb_main_router))

    return application

if __name__ == "__main__":
    # бекфіл фото для вже існуючих рослин (один раз при старті/редеплою)
    try:
        n = backfill_missing_photos()
        if n:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": os.environ.get("TELEGRAM_CHAT_ID"), "text": f"🖼 Додав фото з бази для {n} рослин."},
                timeout=10
            )
    except Exception:
        pass

    # пінг, що бот живий
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": os.environ.get("TELEGRAM_CHAT_ID"), "text": "✅ Бот запущено. Натисни «Мої рослини» ↓"},
            timeout=10
        )
    except Exception:
        pass

    app().run_polling(allowed_updates=Update.ALL_TYPES)
