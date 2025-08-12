import os, io, json, requests, sqlite3, datetime as dt
from PIL import Image
from zoneinfo import ZoneInfo
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters, ConversationHandler
)

# ===== CONFIG =====
TOKEN = os.environ["TELEGRAM_TOKEN"]
OWM_KEY = os.environ.get("OPENWEATHER_API_KEY", "")
PLANT_ID_API_KEY = os.environ.get("PLANT_ID_API_KEY", "")   # <- опціонально, для авто-розпізнавання фото
CITY = "Kyiv"
TZ = ZoneInfo("Europe/Kyiv")
DB_PATH = "plants.db"

# ===== DB =====
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS plants(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        care TEXT NOT NULL,
        photo BLOB
    );
    """)
    return conn

def seed_if_empty():
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM plants;")
    if cur.fetchone()[0] == 0:
        plants = [
            ("Драцена", "Світло: яскраве розсіяне. Полив: після просихання 2–3 см. Після пересадки — без добрив 2–3 тижні.", None),
            ("Каламондин", "Світло: дуже яскраве, трохи вечірнього сонця збоку від вікна. Полив: ґрунт злегка вологий, перевіряй кожні 2–3 дні влітку. Підживлення: раз на 2 тижні цитрус.", None),
            ("Хамаедорея", "Світло: розсіяне, без прямого. Полив: рівномірно вологий ґрунт. Обприскування.", None),
            ("Заміокулькас", "Світло: яскраве розсіяне/півтінь. Полив: тільки після повного просихання (10–14 днів влітку). Підживлення: слабке раз на 4–6 тижнів.", None),
            ("Спатіфілум", "Світло: півтінь/розсіяне. Полив: ґрунт злегка вологий, перевіряй кожні 3–4 дні влітку. Підживлення: раз на 2 тижні.", None),
        ]
        conn.executemany("INSERT INTO plants(name, care, photo) VALUES(?,?,?);", plants)
        conn.commit()
    conn.close()

seed_if_empty()

# ===== WEATHER + PLAN (коротко, залишили на майбутні авто-нагадування) =====
def get_weather():
    if not OWM_KEY: return None
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={OWM_KEY}&units=metric&lang=ua"
        r = requests.get(url, timeout=15).json()
        return round(r["main"]["temp"]), r["weather"][0]["description"], r["main"]["humidity"]
    except Exception:
        return None

# ===== UI HELPERS =====
def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌿 Мої рослини", callback_data="my_plants")],
        [
            InlineKeyboardButton("➕ Додати рослину", callback_data="add_plant"),
            InlineKeyboardButton("🗑 Видалити", callback_data="delete_plant")
        ],
    ])

def plants_list_kb():
    conn = db()
    rows = conn.execute("SELECT id, name FROM plants ORDER BY name;").fetchall()
    conn.close()
    buttons = [[InlineKeyboardButton(name, callback_data=f"plant_{pid}")] for (pid, name) in rows]
    buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_home")])
    return InlineKeyboardMarkup(buttons)

# ===== RECOGNITION =====
def recognize_plant_by_photo(image_bytes: bytes) -> str | None:
    """
    Використовує Plant.id API (опційно). Якщо ключа немає – вертаємо None.
    """
    if not PLANT_ID_API_KEY:
        return None
    try:
        # їхній v3/v2 ендпоінт – приклад payload
        url = "https://api.plant.id/v2/identify"
        files = {"images": image_bytes}
        data = {
            "modifiers": ["crops_fast", "similar_images"],
            "plant_language": "uk",
            "plant_details": ["common_names", "url", "waterings", "wiki_description"]
        }
        headers = {"Api-Key": PLANT_ID_API_KEY}
        resp = requests.post(url, headers=headers, files=files, data={"data": json.dumps(data)}, timeout=30)
        j = resp.json()
        suggestions = j.get("suggestions", [])
        if not suggestions: return None
        # беремо першу назву
        name = suggestions[0].get("plant_name") or suggestions[0].get("plant_details", {}).get("common_names", [None])[0]
        return name
    except Exception:
        return None

# ===== CONVERSATIONS =====
(
    ADD_CHOOSE, ADD_NAME, ADD_PHOTO,
    DEL_CHOOSE
) = range(4)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привіт! Я бот догляду за рослинами 🌱", reply_markup=main_kb())

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    await q.answer()
    if data == "my_plants":
        await q.edit_message_text("Твої рослини:", reply_markup=plants_list_kb())
    elif data.startswith("plant_"):
        pid = int(data.split("_")[1])
        conn = db()
        row = conn.execute("SELECT name, care, photo FROM plants WHERE id=?;", (pid,)).fetchone()
        conn.close()
        if not row:
            await q.edit_message_text("Не знайшов цю рослину 🤔", reply_markup=plants_list_kb())
            return
        name, care, photo = row
        caption = f"**{name}**\n{care}"
        if photo:
            await q.message.reply_photo(photo=photo, caption=caption, parse_mode="Markdown")
        else:
            await q.message.reply_text(caption, parse_mode="Markdown")
    elif data == "add_plant":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Ввести назву вручну", callback_data="add_name")],
            [InlineKeyboardButton("Завантажити фото (авто-розпізнавання)", callback_data="add_photo")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back_home")]
        ])
        await q.edit_message_text("Як додамо рослину?", reply_markup=kb)
    elif data == "delete_plant":
        # покажемо список для видалення
        conn = db()
        rows = conn.execute("SELECT id, name FROM plants ORDER BY name;").fetchall()
        conn.close()
        buttons = [[InlineKeyboardButton(f"🗑 {name}", callback_data=f"del_{pid}")] for (pid, name) in rows]
        buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_home")])
        await q.edit_message_text("Оберіть рослину для видалення:", reply_markup=InlineKeyboardMarkup(buttons))
    elif data.startswith("del_"):
        pid = int(data.split("_")[1])
        conn = db()
        conn.execute("DELETE FROM plants WHERE id=?;", (pid,))
        conn.commit()
        conn.close()
        await q.edit_message_text("Готово. Видалив ✅", reply_markup=plants_list_kb())
    elif data == "add_name":
        await q.edit_message_text("Введи назву рослини одним повідомленням:")
        return ADD_NAME
    elif data == "add_photo":
        await q.edit_message_text("Надішли фото рослини одним повідомленням (одне фото).")
        return ADD_PHOTO
    elif data == "back_home":
        await q.edit_message_text("Головне меню:", reply_markup=main_kb())

async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Порожня назва. Спробуй ще раз.")
        return ADD_NAME
    # базовий шаблон догляду; можна редагувати
    care = "Світло: яскраве розсіяне. Полив: після просихання верхнього шару. Підживлення: за сезоном."
    conn = db()
    conn.execute("INSERT INTO plants(name, care, photo) VALUES(?,?,?);", (name, care, None))
    conn.commit(); conn.close()
    await update.message.reply_text(f"Додав «{name}» ✅", reply_markup=main_kb())
    return ConversationHandler.END

async def add_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Це не фото 🙃 Надішли зображення з галереї/камери.")
        return ADD_PHOTO
    file = await update.message.photo[-1].get_file()
    img_bytes = await file.download_as_bytearray()
    # авто-розпізнавання (якщо є ключ)
    guessed = recognize_plant_by_photo(bytes(img_bytes))
    guessed_name = guessed or "Нова рослина"
    care = "Світло: яскраве розсіяне. Полив: після просихання верхнього шару. Підживлення: за сезоном."
    conn = db()
    conn.execute("INSERT INTO plants(name, care, photo) VALUES(?,?,?);", (guessed_name, care, bytes(img_bytes)))
    conn.commit(); conn.close()
    msg = f"Додав «{guessed_name}» ✅"
    if not guessed:
        msg += "\n(Авто-розпізнавання недоступне або не впізнало — можна відредагувати назву пізніше)"
    await update.message.reply_text(msg, reply_markup=main_kb())
    return ConversationHandler.END

# Fallbacks / cancel
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скасовано.", reply_markup=main_kb())
    return ConversationHandler.END

# ===== ENTRY =====
def app():
    application = ApplicationBuilder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(on_button, pattern="^(add_name|add_photo)$")],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
            ADD_PHOTO:[MessageHandler(filters.PHOTO, add_photo)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        map_to_parent={ConversationHandler.END: ConversationHandler.END}
    )
    # Головні хендлери
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(on_button))
    application.add_handler(conv)
    return application

if __name__ == "__main__":
    # миттєвий пінг, щоб бачити що бот живий
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": os.environ.get("TELEGRAM_CHAT_ID"), "text": "✅ Бот запущено. Натисни «Мої рослини» ↓"},
            timeout=10
        )
    except Exception:
        pass
    app().run_polling(allowed_updates=Update.ALL_TYPES)
