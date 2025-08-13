# plantbot/handlers.py
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, Update
from telegram.ext import (
    ConversationHandler, CallbackQueryHandler, MessageHandler, CommandHandler, ContextTypes, filters
)

from .photos import download_file_bytes
from .resolvers import identify_from_image_bytes, parse_identify_response, search_name
from .db import conn
from .care import get_care_for  # якщо немає такої — нижче є фолбек

from datetime import date
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters, ConversationHandler
)

from .config import TOKEN
from .db import conn, migrate_legacy_rows_to_user
from .keyboards import main_kb, plants_list_kb, plant_card_kb, per_task_buttons
from .care import care_and_intervals_for
from .photos import plantid_name_and_image
from .resolvers import care_and_photo_by_name, resolve_plant_name, wikidata_image_by_qid
from .schedule import (
    ensure_week_tasks_for_user, week_overview_text, today_tasks_markup_and_text,
    mark_task_done, move_task_to_next_care_day, mark_task_skipped
)
ADD_CHOOSE, ADD_WAIT_PHOTO, ADD_WAIT_NAME, ADD_CONFIRM = range(4)

def _base_care_text(name: str) -> str:
    return ("Світло: яскраве розсіяне.\n"
            "Полив: після підсихання верхнього шару ґрунту.\n"
            "Підживлення: раз на 2–4 тижні в сезон.\n")

def _insert_plant(user_id: int, name: str, care_text: str) -> int:
    c = conn()
    cur = c.cursor()
    cur.execute(
        "INSERT INTO plants(user_id, name, care, water_int, feed_int, mist_int) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, name, care_text, 7, 30, 0)
    )
    plant_id = cur.lastrowid
    c.commit()
    return plant_id

# ---- Conversation states
SELECT_ADD_MODE, ADD_NAME, ADD_PHOTO_NEW, ADD_PHOTO_EXIST, RENAME_WAIT = range(5)

# ---- Utils
def iso_today() -> str:
    return date.today().isoformat()

# =======================
#       COMMANDS
# =======================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Привʼязує legacy-рядки до користувача, створює задачі та показує головне меню."""
    uid = update.effective_user.id
    migrate_legacy_rows_to_user(uid)
    ensure_week_tasks_for_user(uid)
    await update.message.reply_text("Привіт! Я бот догляду за рослинами 🌱", reply_markup=main_kb())

# =======================
#         ROUTER
# =======================
async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    uid = update.effective_user.id
    await q.answer()

    # ---- План на сьогодні
    if data == "today_plan":
        ensure_week_tasks_for_user(uid)
        text, kb_rows = today_tasks_markup_and_text(uid, per_task_buttons)
        kb = InlineKeyboardMarkup(kb_rows) if kb_rows else None
        await q.message.reply_text(text, reply_markup=kb or main_kb())
        return

    # ---- План на тиждень
    if data == "week_plan":
        ensure_week_tasks_for_user(uid)
        await q.message.reply_text(week_overview_text(uid), reply_markup=main_kb())
        return

    # ---- Мої рослини
    if data == "my_plants":
        c = conn()
        rows = c.execute("SELECT id, name FROM plants WHERE user_id=? ORDER BY name", (uid,)).fetchall()
        c.close()
        if not rows:
            await q.message.reply_text("У тебе поки немає рослин. Додай першу 🌱", reply_markup=main_kb())
            return
        await q.message.reply_text("Твої рослини:", reply_markup=plants_list_kb(rows))
        return

    # ---- Карточка рослини
    if data.startswith("plant_"):
        pid = int(data.split("_")[1])
        c = conn()
        row = c.execute("SELECT name, care, photo FROM plants WHERE id=? AND user_id=?", (pid, uid)).fetchone()
        c.close()
        if not row:
            await q.message.reply_text("Не знайшов цю рослину 🤔", reply_markup=main_kb())
            return
        name, care, photo = row
        caption = f"*{name}*\n{care}"
        if photo:
            await q.message.reply_photo(photo=photo, caption=caption, parse_mode="Markdown", reply_markup=plant_card_kb(pid))
        else:
            await q.message.reply_text(caption, parse_mode="Markdown", reply_markup=plant_card_kb(pid))
        return

    # ---- Показати догляд
    if data.startswith("care_"):
        pid = int(data.split("_")[1])
        c = conn()
        row = c.execute("SELECT name FROM plants WHERE id=? AND user_id=?", (pid, uid)).fetchone()
        c.close()
        if not row:
            await q.message.reply_text("Не знайшов.", reply_markup=main_kb())
            return
        care, *_ = care_and_intervals_for(row[0])
        await q.message.reply_text(care, reply_markup=plant_card_kb(pid))
        return

    # ---- Редагування назви
    if data.startswith("rename_"):
        pid = int(data.split("_")[1])
        context.user_data["rename_pid"] = pid
        await q.message.reply_text("Введи нову назву для цієї рослини одним повідомленням:")
        return RENAME_WAIT

    # ---- Видалення (меню)
    if data == "delete_plant":
        c = conn()
        rows = c.execute("SELECT id, name FROM plants WHERE user_id=? ORDER BY name", (uid,)).fetchall()
        c.close()
        if not rows:
            await q.message.reply_text("Список порожній.", reply_markup=main_kb())
            return
        buttons = [[InlineKeyboardButton(f"🗑 {nm}", callback_data=f"del_{pid}")] for pid, nm in rows]
        buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_home")])
        await q.message.reply_text("Оберіть рослину для видалення:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    # ---- Видалити конкретну
    if data.startswith("del_"):
        pid = int(data.split("_")[1])
        c = conn()
        c.execute("DELETE FROM plants WHERE id=? AND user_id=?", (pid, uid))
        c.execute("DELETE FROM tasks WHERE plant_id=? AND user_id=?", (pid, uid))
        c.commit(); c.close()
        await q.message.reply_text("Видалив ✅", reply_markup=main_kb())
        return

    async def add_plant_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старт додавання — показує вибір способу."""
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Ввести назву вручну", callback_data="add_by_name")],
        [InlineKeyboardButton("Фото (авто-розпізнавання)", callback_data="add_by_photo")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_menu")]
    ])
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Як додаємо рослину?", reply_markup=kb)
    else:
        await update.message.reply_text("Як додаємо рослину?", reply_markup=kb)
    return ADD_CHOOSE

async def add_choose_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Роутер вибору способу."""
    q = update.callback_query
    data = q.data if q else ""
    if data == "add_by_photo":
        await q.answer()
        await q.edit_message_text("Надішли фото рослини одним повідомленням (jpg/png).")
        return ADD_WAIT_PHOTO
    elif data == "add_by_name":
        await q.answer()
        await q.edit_message_text("Введи назву рослини одним повідомленням:")
        return ADD_WAIT_NAME
    elif data == "back_to_menu":
        await q.answer()
        await q.edit_message_text("Окей, повернувся до меню.")
        return ConversationHandler.END
    else:
        await q.answer()
        return ADD_CHOOSE

async def add_receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отримує фото, перевіряє Plant.id, просить підтвердження."""
    if not update.message or not update.message.photo:
        await update.message.reply_text("Треба саме фото 🌿")
        return ADD_WAIT_PHOTO

    img_id = update.message.photo[-1].file_id
    img_bytes = await download_file_bytes(context.bot, img_id)

    try:
        resp = identify_from_image_bytes(img_bytes)
    except Exception as e:
        await update.message.reply_text(f"Помилка розпізнавання: {e}")
        return ADD_WAIT_PHOTO

    is_plant, conf, name, extra = parse_identify_response(resp)
    if not is_plant or not name:
        await update.message.reply_text("Схоже, на фото не рослина або не вдалося впізнати. Спробуй інше фото.")
        return ADD_WAIT_PHOTO

    context.user_data["pending_plant"] = {"name": name, "confidence": conf, "extra": extra}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Додати", callback_data="confirm_add")],
        [InlineKeyboardButton("❌ Скасувати", callback_data="cancel_add")]
    ])
    await update.message.reply_text(
        f"Я думаю, що це **{name}** (впевненість {conf:.1f}%). Додати у список?",
        reply_markup=kb
    )
    return ADD_CONFIRM

async def add_receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отримує текстову назву, перевіряє через name_search, просить підтвердження."""
    query = (update.message.text or "").strip()
    if not query:
        await update.message.reply_text("Введи щось схоже на назву рослини 🙂")
        return ADD_WAIT_NAME

    ok, conf, name, extra = search_name(query)
    if not ok or not name:
        await update.message.reply_text("Не знайшов такої рослини. Спробуй іншу назву або додай за фото.")
        return ADD_WAIT_NAME

    context.user_data["pending_plant"] = {"name": name, "confidence": conf, "extra": extra}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Додати", callback_data="confirm_add")],
        [InlineKeyboardButton("❌ Скасувати", callback_data="cancel_add")]
    ])
    await update.message.reply_text(
        f"Знайшов: **{name}** (впевненість {conf:.1f}%). Додати у список?",
        reply_markup=kb
    )
    return ADD_CONFIRM

async def add_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = context.user_data.get("pending_plant") or {}
    name = data.get("name")
    if not name:
        await q.edit_message_text("Немає даних для збереження 🤷‍♂️")
        return ConversationHandler.END

    # Формуємо текст догляду
    try:
        care_text = get_care_for(name)  # якщо є в твоєму care.py
        if not care_text:
            care_text = _base_care_text(name)
    except Exception:
        care_text = _base_care_text(name)

    user_id = q.from_user.id
    plant_id = _insert_plant(user_id, name, care_text)

    await q.edit_message_text(f"Додав **{name}** ✅ (id: {plant_id})")
    context.user_data.pop("pending_plant", None)
    return ConversationHandler.END

async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("Скасовано ❌")
    context.user_data.pop("pending_plant", None)
    return ConversationHandler.END

    # ---- Додати рослину (меню)
    if data == "add_plant":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Ввести назву вручну", callback_data="mode_name")],
            [InlineKeyboardButton("Фото (авто-розпізнавання)", callback_data="mode_photo")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back_home")]
        ])
        await q.message.reply_text("Як додамо рослину?", reply_markup=kb)
        return SELECT_ADD_MODE

    # ---- Назад у головне меню
    if data == "back_home":
        await q.message.reply_text("Головне меню:", reply_markup=main_kb())
        return

    # ---- Швидкі кнопки з картки (миттєва відмітка)
    if any(data.startswith(p) for p in ["done_water_", "done_feed_", "done_mist_"]):
        pid = int(data.split("_")[2])
        kind = 'water' if "water" in data else 'feed' if "feed" in data else 'mist'
        c = conn()
        c.execute("""INSERT INTO tasks(user_id,plant_id,kind,due_date,status,created_at)
                     VALUES(?,?,?,?,?,?)""",
                  (uid, pid, kind, iso_today(), 'due', iso_today()))
        tid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.commit(); c.close()
        mark_task_done(tid)
        await q.message.reply_text("Записав ✅", reply_markup=plant_card_kb(pid))
        return

    # ---- Фото за назвою (Wikidata P18, без завантаження фото)
    if data.startswith("plantidphoto_"):
        pid = int(data.split("_")[1])
        c = conn()
        row = c.execute("SELECT name FROM plants WHERE id=? AND user_id=?", (pid, uid)).fetchone()
        c.close()
        if not row:
            await q.message.reply_text("Не знайшов.", reply_markup=main_kb())
            return
        r = resolve_plant_name(row[0])
        img = wikidata_image_by_qid(r["qid"]) if r.get("qid") else None
        if not img:
            await q.message.reply_text("Не вийшло знайти фото за цією назвою. Спробуй уточнити назву або додай фото вручну.")
            return
        c = conn(); c.execute("UPDATE plants SET photo=? WHERE id=? AND user_id=?", (img, pid, uid))
        c.commit(); c.close()
        await q.message.reply_text("Фото оновив за назвою ✅", reply_markup=plant_card_kb(pid))
        return

# =======================
#    CONVERSATIONS
# =======================
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
    uid = update.effective_user.id
    raw = (update.message.text or "").strip()
    if not raw:
        await update.message.reply_text("Порожня назва. Спробуй ще раз.")
        return ADD_NAME

    care, wi, fi, mi, photo, canonical, source = care_and_photo_by_name(raw)
    c = conn()
    c.execute("""INSERT INTO plants(user_id,name,care,photo,water_int,feed_int,mist_int,
                 last_watered,last_fed,last_misted)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (uid, raw, care, photo, wi, fi, mi, iso_today(), iso_today(), iso_today()))
    c.commit(); c.close()

    ensure_week_tasks_for_user(uid)
    note = f"(розпізнано як: {canonical}, джерело: {source})"
    await update.message.reply_text(f"Додав «{raw}» ✅ {note}\nРозклад оновлено.", reply_markup=main_kb())
    return ConversationHandler.END

async def on_add_photo_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not update.message.photo:
        await update.message.reply_text("Це не фото 🙃 Надішли зображення.")
        return ADD_PHOTO_NEW

    tgfile = await update.message.photo[-1].get_file()
    img_bytes = await tgfile.download_as_bytearray()

    # Plant.id → назва + реф.зображення (якщо є ключ)
    name, ref_img = plantid_name_and_image(bytes(img_bytes))
    canonical = name or "Нова рослина"
    care, wi, fi, mi = care_and_intervals_for(canonical)
    photo = ref_img or bytes(img_bytes)

    c = conn()
    c.execute("""INSERT INTO plants(user_id,name,care,photo,water_int,feed_int,mist_int,
                 last_watered,last_fed,last_misted)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (uid, canonical, care, photo, wi, fi, mi, iso_today(), iso_today(), iso_today()))
    c.commit(); c.close()

    ensure_week_tasks_for_user(uid)
    await update.message.reply_text(f"Додав «{canonical}» ✅\nРозклад оновлено.", reply_markup=main_kb())
    return ConversationHandler.END

async def on_add_photo_exist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not update.message.photo:
        await update.message.reply_text("Це не фото 🙃 Надішли зображення.")
        return ADD_PHOTO_EXIST
    pid = context.user_data.get("target_pid")
    tgfile = await update.message.photo[-1].get_file()
    img_bytes = await tgfile.download_as_bytearray()
    c = conn()
    c.execute("UPDATE plants SET photo=? WHERE id=? AND user_id=?", (bytes(img_bytes), pid, uid))
    c.commit(); c.close()
    await update.message.reply_text("Фото оновив ✅", reply_markup=main_kb())
    return ConversationHandler.END

async def on_rename_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    pid = context.user_data.get("rename_pid")
    new_raw = (update.message.text or "").strip()
    if not pid or not new_raw:
        await update.message.reply_text("Порожня назва. Спробуй ще раз.")
        return RENAME_WAIT

    r = resolve_plant_name(new_raw)
    care, wi, fi, mi = care_and_intervals_for(r["canonical"])

    c = conn()
    c.execute("""UPDATE plants SET name=?, care=?, water_int=?, feed_int=?, mist_int=?
                 WHERE id=? AND user_id=?""",
              (new_raw, care, wi, fi, mi, pid, uid))
    c.commit(); c.close()

    img = wikidata_image_by_qid(r.get("qid")) if r.get("qid") else None
    if img:
        c = conn()
        c.execute("UPDATE plants SET photo=? WHERE id=? AND user_id=?", (img, pid, uid))
        c.commit(); c.close()

    await update.message.reply_text(
        f"Оновив назву на «{new_raw}». Розпізнав як: {r['canonical']} ({r['source']}). Догляд оновлено.",
        reply_markup=main_kb()
    )
    return ConversationHandler.END


def register_add_flow(app):
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_plant_entry),                # /add як швидкий вхід
            CallbackQueryHandler(add_plant_entry, pattern="^add_plant$"),  # якщо у тебе є кнопка з таким callback_data
        ],
        states={
            ADD_CHOOSE: [
                CallbackQueryHandler(add_choose_router, pattern="^(add_by_photo|add_by_name|back_to_menu)$"),
            ],
            ADD_WAIT_PHOTO: [
                MessageHandler(filters.PHOTO, add_receive_photo),
            ],
            ADD_WAIT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_receive_name),
            ],
            ADD_CONFIRM: [
                CallbackQueryHandler(add_confirm, pattern="^confirm_add$"),
                CallbackQueryHandler(add_cancel, pattern="^cancel_add$"),
            ],
        },
        fallbacks=[CallbackQueryHandler(add_cancel, pattern="^cancel_add$")],
        allow_reentry=True,
    )
    app.add_handler(conv)


# =======================
#  HELPERS / REGISTRATION
# =======================
async def start_update_photo_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    pid = int(q.data.split("_")[1])
    context.user_data["target_pid"] = pid
    await q.message.reply_text("Надішли одне фото цієї рослини (jpg/png).")
    return ADD_PHOTO_EXIST

async def on_task_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        _, tid_str, action = q.data.split(":")
        tid = int(tid_str)
    except Exception:
        await q.answer("Помилка callback")
        return

    if action == "done":
        mark_task_done(tid);  await q.answer("Готово ✅")
    elif action == "defer":
        move_task_to_next_care_day(tid); await q.answer("Перенесено ⏩")
    elif action == "skip":
        mark_task_skipped(tid); await q.answer("Пропущено 🚫")

    uid = update.effective_user.id
    text, kb_rows = today_tasks_markup_and_text(uid, per_task_buttons)
    kb = InlineKeyboardMarkup(kb_rows) if kb_rows else None
    await q.message.edit_text(text, reply_markup=kb or None)

def build_app():
    app = ApplicationBuilder().token(TOKEN).build()
    # ... інші реєстрації ...
register_add_flow(app)


    # Конверсейшн додавання/редагування
    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_choose, pattern="^(mode_name|mode_photo|back_home)$")],
        states={
            SELECT_ADD_MODE: [CallbackQueryHandler(add_choose, pattern="^(mode_name|mode_photo|back_home)$")],
            ADD_NAME:        [MessageHandler(filters.TEXT & ~filters.COMMAND, on_add_name)],
            ADD_PHOTO_NEW:   [MessageHandler(filters.PHOTO, on_add_photo_new)],
            ADD_PHOTO_EXIST: [MessageHandler(filters.PHOTO, on_add_photo_exist)],
            RENAME_WAIT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, on_rename_text)],
        },
        fallbacks=[],
    )

    # Команди
    app.add_handler(CommandHandler("start", cmd_start))

    # Inline-кнопки
    app.add_handler(add_conv)
    app.add_handler(CallbackQueryHandler(start_update_photo_manual, pattern=r"^addphoto_\d+$"))
    app.add_handler(CallbackQueryHandler(on_task_action, pattern=r"^task:\d+:(done|defer|skip)$"))
    app.add_handler(CallbackQueryHandler(router))  # catch-all наприкінці

    return app
