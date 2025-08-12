# bot.py
import os, io, json, requests, sqlite3
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters, ConversationHandler
)

# ====== CONFIG ======
TOKEN = os.environ["TELEGRAM_TOKEN"]
OWM_KEY = os.environ.get("OPENWEATHER_API_KEY", "")
PLANT_ID_API_KEY = os.environ.get("PLANT_ID_API_KEY", "")  # опціонально
CITY = "Kyiv"
TZ = ZoneInfo("Europe/Kyiv")
DB_PATH = "plants.db"
WIKI_LANG_PRIMARY = os.environ.get("WIKIMEDIA_LANG", "uk")
WIKI_LANG_FALLBACK = "en"

# ====== DB ======
def db():
    c = sqlite3.connect(DB_PATH)
    c.execute("""
    CREATE TABLE IF NOT EXISTS plants(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      care TEXT NOT NULL,
      photo BLOB
    );""")
    return c

def seed_if_empty():
    c = db()
    n = c.execute("SELECT COUNT(*) FROM plants").fetchone()[0]
    if n == 0:
        rows = [
            ("Драцена", "Світло: яскраве розсіяне. Полив: після просихання 2–3 см. Після пересадки — без добрив 2–3 тижні.", None),
            ("Каламондин", "Світло: дуже яскраве, трохи вечірнього сонця збоку від вікна. Полив: ґрунт злегка вологий, перевіряй кожні 2–3 дні влітку. Підживлення: раз на 2 тижні цитрус.", None),
            ("Хамаедорея", "Світло: розсіяне, без прямого. Полив: рівномірно вологий ґрунт. Обприскування.", None),
            ("Заміокулькас", "Світло: яскраве розсіяне/півтінь. Полив: тільки після повного просихання (10–14 днів влітку). Підживлення: слабке раз на 4–6 тижнів.", None),
            ("Спатіфілум", "Світло: півтінь/розсіяне. Полив: ґрунт злегка вологий, перевіряй кожні 3–4 дні влітку. Підживлення: раз на 2 тижні.", None)
        ]
        c.executemany("INSERT INTO plants(name, care, photo) VALUES(?,?,?)", rows)
        c.commit()
    c.close()

seed_if_empty()

# ====== CARE TEMPLATES ======
def care_for(name: str) -> str:
    n = name.lower()
    # можливі синоніми
    if any(k in n for k in ["zamioculcas", "zz", "заміокулькас"]):
        return "Світло: яскраве розсіяне/півтінь. Полив: лише після повного просихання. Підживлення: слабке раз на 4–6 тижнів."
    if any(k in n for k in ["dracaena", "драцена"]):
        return "Світло: яскраве розсіяне. Полив: після просихання 2–3 см. Обприскування листя."
    if any(k in n for k in ["chamaedorea", "хам", "parlor palm", "хамаедорея"]):
        return "Світло: розсіяне, без прямого. Полив: рівномірно вологий ґрунт. Обприскування."
    if any(k in n for k in ["spathiphyllum", "спаті", "peace lily", "спатіфілум"]):
        return "Світло: півтінь/розсіяне. Полив: ґрунт злегка вологий. Підживлення: раз на 2 тижні."
    if any(k in n for k in ["calamondin", "citrus", "каламондин", "citrus × microcarpa", "кумкват"]):
        return "Світло: дуже яскраве, трохи прямого вечірнього. Полив: злегка вологий ґрунт, без застою. Підживлення: цитрус раз на 2 тижні."
    if any(k in n for k in ["avocado", "persea americana", "авокадо"]):
        return "Світло: яскраве, без жорсткого полуденного. Полив: після просихання верхніх 2–3 см. Підживлення: раз на 3–4 тижні у період росту."
    # дефолт
    return "Світло: яскраве розсіяне. Полив: після просихання верхнього шару. Підживлення: за сезоном."

# ====== WIKIMEDIA ======
ALIASES = {
    "Драцена": ["Драцена", "Dracaena"],
    "Каламондин": ["Каламондин", "Calamondin", "Citrus × microcarpa"],
    "Хамаедорея": ["Хамаедорея", "Chamaedorea elegans", "Parlor palm"],
    "Заміокулькас": ["Заміокулькас", "Zamioculcas zamiifolia", "ZZ plant"],
    "Спатіфілум": ["Спатіфілум", "Spathiphyllum", "Peace lily"],
}

def _http_bytes(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=20)
        if r.ok: return r.content
    except Exception:
        pass
    return None

def _wiki_search_one(query: str, lang: str) -> bytes | None:
    try:
        s = requests.get(
            f"https://{lang}.wikipedia.org/w/api.php",
            params={"action":"query","list":"search","srsearch":query,"format":"json","srlimit":1},
            timeout=20
        ).json()
        hits = s.get("query",{}).get("search",[])
        if not hits: return None
        pid = hits[0]["pageid"]
        info = requests.get(
            f"https://{lang}.wikipedia.org/w/api.php",
            params={"action":"query","pageids":pid,"prop":"pageimages","pithumbsize":800,"format":"json"},
            timeout=20
        ).json()
        thumb = info.get("query",{}).get("pages",{}).get(str(pid),{}).get("thumbnail",{}).get("source")
        if thumb: return _http_bytes(thumb)
    except Exception:
        return None
    return None

def fetch_wiki_image_by_name(name: str) -> bytes | None:
    # 1) пробуємо напряму на primary lang
    img = _wiki_search_one(name, WIKI_LANG_PRIMARY)
    if img: return img
    # 2) пробуємо alias-и на primary lang
    for q in ALIASES.get(name, []):
        img = _wiki_search_one(q, WIKI_LANG_PRIMARY)
        if img: return img
    # 3) fallback на EN
    img = _wiki_search_one(name, WIKI_LANG_FALLBACK)
    if img: return img
    for q in ALIASES.get(name, []):
        img = _wiki_search_one(q, WIKI_LANG_FALLBACK)
        if img: return img
    return None

# ====== Plant.id (назва + similar image) ======
def plantid_name_and_image(image_bytes: bytes) -> tuple[str|None, bytes|None]:
    if not PLANT_ID_API_KEY:
        return (None, None)
    try:
        url = "https://api.plant.id/v2/identify"
        headers = {"Api-Key": PLANT_ID_API_KEY}
        files = {"images": image_bytes}
        data = {
            "modifiers": ["crops_fast", "similar_images"],
            "plant_language": "en",  # стабільніше для назв
            "plant_details": ["common_names", "url", "wiki_description"]
        }
        resp = requests.post(url, headers=headers, files=files, data={"data": json.dumps(data)}, timeout=45)
        j = resp.json()
        sug = (j.get("suggestions") or [])
        if not sug: return (None, None)
        # назва
        name = sug[0].get("plant_name") or (sug[0].get("plant_details",{}).get("common_names") or [None])[0]
        # similar image
        sim = (sug[0].get("similar_images") or [])
        img_bytes = _http_bytes(sim[0]["url"]) if sim else None
        return (name, img_bytes)
    except Exception:
        return (None, None)

# ====== UI ======
def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌿 Мої рослини", callback_data="my_plants")],
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
        [InlineKeyboardButton("📷 Додати/оновити фото", callback_data=f"addphoto_{pid}")],
        [InlineKeyboardButton("⬅️ До списку", callback_data="my_plants")]
    ])

def delete_list_kb():
    c = db()
    rows = c.execute("SELECT id, name FROM plants ORDER BY name").fetchall()
    c.close()
    btns = [[InlineKeyboardButton(f"🗑 {name}", callback_data=f"del_{pid}")] for (pid, name) in rows]
    btns.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_home")])
    return InlineKeyboardMarkup(btns)

# ====== STATES ======
SELECT_ADD_MODE, ADD_NAME, ADD_PHOTO_NEW, ADD_PHOTO_EXIST = range(4)

# ====== HANDLERS ======
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привіт! Я бот догляду за рослинами 🌱", reply_markup=main_kb())

async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; data = q.data
    await q.answer()
    if data == "my_plants":
        await q.message.reply_text("Твої рослини:", reply_markup=plants_list_kb())
    elif data.startswith("plant_"):
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
    elif data == "delete_plant":
        await q.message.reply_text("Оберіть рослину для видалення:", reply_markup=delete_list_kb())
    elif data.startswith("del_"):
        pid = int(data.split("_")[1])
        c = db(); c.execute("DELETE FROM plants WHERE id=?", (pid,)); c.commit(); c.close()
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

# ---- ADD FLOW (conversation #1) ----
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
    care = care_for(name)
    photo = fetch_wiki_image_by_name(name)
    c = db(); c.execute("INSERT INTO plants(name, care, photo) VALUES(?,?,?)", (name, care, photo)); c.commit(); c.close()
    await update.message.reply_text(f"Додав «{name}» ✅", reply_markup=main_kb())
    return ConversationHandler.END

async def on_add_photo_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Це не фото 🙃 Надішли зображення."); return ADD_PHOTO_NEW
    file = await update.message.photo[-1].get_file()
    img = await file.download_as_bytearray()
    # Витягаємо назву + similar image
    name, ref_img = plantid_name_and_image(bytes(img))
    name = name or "Нова рослина"
    care = care_for(name)
    photo = ref_img or bytes(img)
    c = db(); c.execute("INSERT INTO plants(name, care, photo) VALUES(?,?,?)", (name, care, photo)); c.commit(); c.close()
    await update.message.reply_text(f"Додав «{name}» ✅", reply_markup=main_kb())
    return ConversationHandler.END

# ---- UPDATE PHOTO for existing (conversation #2) ----
async def start_update_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    pid = int(q.data.split("_")[1])
    context.user_data["target_pid"] = pid
    await q.message.reply_text("Надішли одне фото цієї рослини (jpg/png).")
    return ADD_PHOTO_EXIST

async def on_add_photo_exist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Це не фото 🙃 Надішли зображення."); return ADD_PHOTO_EXIST
    pid = context.user_data.get("target_pid")
    file = await update.message.photo[-1].get_file()
    img = await file.download_as_bytearray()
    c = db(); c.execute("UPDATE plants SET photo=? WHERE id=?", (bytes(img), pid)); c.commit(); c.close()
    await update.message.reply_text("Фото оновив ✅", reply_markup=main_kb())
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скасовано.", reply_markup=main_kb())
    return ConversationHandler.END

# ====== BACKFILL PHOTOS ======
def backfill_photos():
    c = db()
    rows = c.execute("SELECT id, name FROM plants WHERE photo IS NULL").fetchall()
    updated = 0
    for pid, name in rows:
        img = fetch_wiki_image_by_name(name)
        if img:
            c.execute("UPDATE plants SET photo=? WHERE id=?", (img, pid)); updated += 1
    c.commit(); c.close()
    return updated

# ====== BOOTSTRAP ======
def build_app():
    app = ApplicationBuilder().token(TOKEN).build()

    # add plant flow
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

    # update photo flow (separate conversation so state точно ловиться)
    upd_photo_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_update_photo, pattern=r"^addphoto_\d+$")],
        states={
            ADD_PHOTO_EXIST: [MessageHandler(filters.PHOTO, on_add_photo_exist)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(add_conv)
    app.add_handler(upd_photo_conv)
    app.add_handler(CallbackQueryHandler(router))  # загальний наприкінці
    return app

if __name__ == "__main__":
    # разовий бекфіл фото
    try:
        n = backfill_photos()
        if n:
            requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                          json={"chat_id": os.environ.get("TELEGRAM_CHAT_ID"),
                                "text": f"🖼 Додав фото з бази для {n} рослин."},
                          timeout=10)
    except Exception:
        pass

    # пінг
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json={"chat_id": os.environ.get("TELEGRAM_CHAT_ID"),
                            "text": "✅ Бот запущено. Натисни «Мої рослини» ↓"},
                      timeout=10)
    except Exception:
        pass

    build_app().run_polling(allowed_updates=Update.ALL_TYPES)
