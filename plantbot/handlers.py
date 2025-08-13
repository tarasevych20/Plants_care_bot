# plantbot/handlers.py
from __future__ import annotations

from datetime import date
from typing import Tuple, Optional

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

from .config import TOKEN, ADMIN_ID, DB_PATH  # ADMIN_ID/DB_PATH можуть не знадобитись прямо тут
from .db import conn, migrate_legacy_rows_to_user
from .keyboards import main_kb, plants_list_kb, plant_card_kb, per_task_buttons
from .schedule import (
    ensure_week_tasks_for_user,
    week_overview_text,
    today_tasks_markup_and_text,
    mark_task_done,
    move_task_to_next_care_day,
    mark_task_skipped,
)
from .resolvers import (
    identify_from_image_bytes,
    parse_identify_response,
    search_name,
    resolve_plant_name,
    wikidata_image_by_qid,
)
from .care import care_and_intervals_for  # очікується у твоєму care.py

# -------------------------
#  STATE CONSTANTS (PTB v20)
# -------------------------
ADD_CHOOSE, ADD_WAIT_PHOTO, ADD_WAIT_NAME, ADD_CONFIRM, ADD_PHOTO_EXIST, RENAME_WAIT = range(6)

# -------------------------
#  UTILS
# -------------------------
def iso_today() -> str:
    return date.today().isoformat()

def _base_care_text(name: str) -> str:
    return (
        "Світло: яскраве розсіяне.\n"
        "Полив: після підсихання верхнього шару ґрунту.\n"
        "Підживлення: раз на 2–4 тижні в сезон.\n"
    )

def _care_for_with_intervals(name: str) -> Tuple[str, int, int, int]:
    """
    Обгортає care_and_intervals_for(name) і дає фолбек.
    Очікується, що care_and_intervals_for повертає (care_text, water_int, feed_int, mist_int).
    """
    try:
        care, wi, fi, mi = care_and_intervals_for(name)
        if not care:
            care = _base_care_text(name)
        wi = wi or 7
        fi = fi or 30
        mi = mi or 0
        return care, int(wi), int(fi), int(mi)
    except Exception:
        return _base_care_text(name), 7, 30, 0

def _insert_plant_full(uid: int, name: str, care_text: str,
                       wi: int, fi: int, mi: int,
                       photo: Optional[bytes] = None) -> int:
    """
    Вставляє рослину у таблицю plants. Схема очікується така ж, як ми раніше використовували:
    (id, user_id, name, care, photo, water_int, feed_int, mist_int, last_watered, last_fed, last_misted, ...)
    Photo може бути None.
    """
    c = conn()
    cur = c.cursor()
    cur.execute(
        """INSERT INTO plants(user_id, name, care, photo, water_int, feed_int, mist_int, last_watered, last_fed, last_misted)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (uid, name, care_text, photo, wi, fi, mi, iso_today(), iso_today(), iso_today())
    )
    plant_id = cur.lastrowid
    c.commit()
    c.close()
    return plant_id

# -------------------------
#  /start
# -------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    migrate_legacy_rows_to_user(uid)
    ensure_week_tasks_for_user(uid)
    await update.message.reply_text("Привіт! Я бот догляду за рослинами 🌱", reply_markup=main_kb())

# -------------------------
#  ROUTER FOR INLINE BTNS
# -------------------------
async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    uid = update.effective_user.id
    await q.answer()

    # План на сьогодні
    if data == "today_plan":
        ensure_week_tasks_for_user(uid)
        text, kb_rows = today_tasks_markup_and_text(uid, per_task_buttons)
        kb = InlineKeyboardMarkup(kb_rows) if kb_rows else None
        await q.message.reply_text(text, reply_markup=kb or main_kb())
        return

    # План на тиждень
    if data == "week_plan":
        ensure_week_tasks_for_user(uid)
        await q.message.reply_text(week_overview_text(uid), reply_markup=main_kb())
        return

    # Список рослин
    if data == "my_plants":
        c = conn()
        rows = c.execute("SELECT id, name FROM plants WHERE user_id=? ORDER BY name", (uid,)).fetchall()
        c.close()
        if not rows:
            await q.message.reply_text("У тебе поки немає рослин. Додай першу 🌱", reply_markup=main_kb())
            return
        await q.message.reply_text("Твої рослини:", reply_markup=plants_list_kb(rows))
        return

    # Картка рослини
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

    # Показати догляд (перерахунок)
    if data.startswith("care_"):
        pid = int(data.split("_")[1])
        c = conn()
        row = c.execute("SELECT name FROM plants WHERE id=? AND user_id=?", (pid, uid)).fetchone()
        c.close()
        if not row:
            await q.message.reply_text("Не знайшов.", reply_markup=main_kb())
            return
        care_text, *_ints = _care_for_with_intervals(row[0])
        await q.message.reply_text(care_text, reply_markup=plant_card_kb(pid))
        return

    # Редагування назви — запит
    if data.startswith("rename_"):
        pid = int(data.split("_")[1])
        context.user_data["rename_pid"] = pid
        await q.message.reply_text("Введи нову назву для цієї рослини одним повідомленням:")
        return RENAME_WAIT

    # Видалення (меню)
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

    # Видалити конкретну
    if data.startswith("del_"):
        pid = int(data.split("_")[1])
        c = conn()
        c.execute("DELETE FROM plants WHERE id=? AND user_id=?", (pid, uid))
        c.execute("DELETE FROM tasks WHERE plant_id=? AND user_id=?", (pid, uid))
        c.commit()
        c.close()
        await q.message.reply_text("Видалив ✅", reply_markup=main_kb())
        return

    # Оновити фото за назвою (Wikidata P18)
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
        c = conn()
        c.execute("UPDATE plants SET photo=? WHERE id=? AND user_id=?", (img, pid, uid))
        c.commit()
        c.close()
        await q.message.reply_text("Фото оновив за назвою ✅", reply_markup=plant_card_kb(pid))
        return

    # Швидкі кнопки на карточці (миттєва відмітка)
    if any(data.startswith(p) for p in ["done_water_", "done_feed_", "done_mist_"]):
        pid = int(data.split("_")[2])
        kind = 'water' if "water" in data else ('feed' if "feed" in data else 'mist')
        c = conn()
        c.execute(
            """INSERT INTO tasks(user_id, plant_id, kind, due_date, status, created_at)
               VALUES(?,?,?,?,?,?)""",
            (uid, pid, kind, iso_today(), 'due', iso_today())
        )
        tid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.commit()
        c.close()
        mark_task_done(tid)
        await q.message.reply_text("Записав ✅", reply_markup=plant_card_kb(pid))
        return

    # Додати фото вручну для існуючої рослини
    if data.startswith("addphoto_"):
        pid = int(data.split("_")[1])
        context.user_data["target_pid"] = pid
        await q.message.reply_text("Надішли одне фото цієї рослини (jpg/png).")
        return ADD_PHOTO_EXIST

    # Назад у головне меню
    if data == "back_home":
        await q.message.reply_text("Головне меню:", reply_markup=main_kb())
        return

# -------------------------
#  RENAME: текст нової назви
# -------------------------
async def on_rename_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    pid = context.user_data.get("rename_pid")
    new_raw = (update.message.text or "").strip()
    if not pid or not new_raw:
        await update.message.reply_text("Порожня назва. Спробуй ще раз.")
        return RENAME_WAIT

    # Вирішуємо канонічну назву і оновлюємо догляд/інтервали
    r = resolve_plant_name(new_raw)
    canonical = r.get("canonical") or new_raw
    care_text, wi, fi, mi = _care_for_with_intervals(canonical)

    c = conn()
    c.execute(
        """UPDATE plants SET name=?, care=?, water_int=?, feed_int=?, mist_int=?
           WHERE id=? AND user_id=?""",
        (new_raw, care_text, wi, fi, mi, pid, uid)
    )
    c.commit()
    c.close()

    # Спроба підтягти фото (якщо є QID)
    img = wikidata_image_by_qid(r.get("qid")) if r.get("qid") else None
    if img:
        c = conn()
        c.execute("UPDATE plants SET photo=? WHERE id=? AND user_id=?", (img, pid, uid))
        c.commit()
        c.close()

    await update.message.reply_text(
        f"Оновив назву на «{new_raw}». Розпізнав як: {canonical} ({r.get('source','')}). Догляд оновлено.",
        reply_markup=main_kb()
    )
    return ConversationHandler.END

# -------------------------
#  ADD FLOW (перевірка → підтвердження)
# -------------------------
async def add_plant_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старт додавання — показує вибір способу."""
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Ввести назву вручну", callback_data="add_by_name")],
        [InlineKeyboardButton("Фото (авто-розпізнавання)", callback_data="add_by_photo")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_home")],
    ])
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Як додаємо рослину?", reply_markup=kb)
    else:
        await update.message.reply_text("Як додаємо рослину?", reply_markup=kb)
    return ADD_CHOOSE

async def add_choose_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data
    await q.answer()
    if data == "add_by_photo":
        await q.edit_message_text("Надішли фото рослини одним повідомленням (jpg/png).")
        return ADD_WAIT_PHOTO
    if data == "add_by_name":
        await q.edit_message_text("Введи назву рослини одним повідомленням:")
        return ADD_WAIT_NAME
    if data == "back_home":
        await q.edit_message_text("Головне меню:", reply_markup=main_kb())
        return ConversationHandler.END
    return ADD_CHOOSE

async def add_receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отримує фото, перевіряє Plant.id (is_plant/conf), просить підтвердження."""
    if not update.message or not update.message.photo:
        await update.message.reply_text("Треба саме фото 🌿")
        return ADD_WAIT_PHOTO

    tg_file = await update.message.photo[-1].get_file()
    img_bytes = await tg_file.download_as_bytearray()
    img_bytes = bytes(img_bytes)

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
        [InlineKeyboardButton("❌ Скасувати", callback_data="cancel_add")],
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
        [InlineKeyboardButton("❌ Скасувати", callback_data="cancel_add")],
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

    uid = q.from_user.id
    care_text, wi, fi, mi = _care_for_with_intervals(name)
    plant_id = _insert_plant_full(uid, name, care_text, wi, fi, mi, photo=None)

    ensure_week_tasks_for_user(uid)
    await q.edit_message_text(f"Додав **{name}** ✅ (id: {plant_id}). Розклад оновлено.")
    context.user_data.pop("pending_plant", None)
    return ConversationHandler.END

async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("Скасовано ❌")
    context.user_data.pop("pending_plant", None)
    return ConversationHandler.END

# -------------------------
#  UPDATE PHOTO for existing by upload
# -------------------------
async def on_add_photo_exist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not update.message or not update.message.photo:
        await update.message.reply_text("Це не фото 🙃 Надішли зображення.")
        return ADD_PHOTO_EXIST
    pid = context.user_data.get("target_pid")
    if not pid:
        await update.message.reply_text("Не вибрано рослину для оновлення фото.")
        return ConversationHandler.END

    tgfile = await update.message.photo[-1].get_file()
    img_bytes = await tgfile.download_as_bytearray()

    c = conn()
    c.execute("UPDATE plants SET photo=? WHERE id=? AND user_id=?", (bytes(img_bytes), pid, uid))
    c.commit()
    c.close()
    await update.message.reply_text("Фото оновив ✅", reply_markup=main_kb())
    return ConversationHandler.END

# -------------------------
#  TASK ACTIONS (пер-рослинно)
# -------------------------
async def on_task_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        _, tid_str, action = q.data.split(":")
        tid = int(tid_str)
    except Exception:
        await q.answer("Помилка callback")
        return

    if action == "done":
        mark_task_done(tid)
        await q.answer("Готово ✅")
    elif action == "defer":
        move_task_to_next_care_day(tid)
        await q.answer("Перенесено ⏩")
    elif action == "skip":
        mark_task_skipped(tid)
        await q.answer("Пропущено 🚫")

    uid = update.effective_user.id
    text, kb_rows = today_tasks_markup_and_text(uid, per_task_buttons)
    kb = InlineKeyboardMarkup(kb_rows) if kb_rows else None
    # edit_text може впасти, якщо стара розмітка не співпала — тоді просто надішлемо нове
    try:
        await q.message.edit_text(text, reply_markup=kb or None)
    except Exception:
        await q.message.reply_text(text, reply_markup=kb or None)

# -------------------------
#  BUILD APP
# -------------------------
def build_app() -> Application:
    app = ApplicationBuilder().token(TOKEN).build()

    # Команди
    app.add_handler(CommandHandler("start", cmd_start))

    # Додавання рослин (конверсейшн із перевіркою)
    add_flow = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_plant_entry),                             # /add
            CallbackQueryHandler(add_plant_entry, pattern=r"^add_plant$"),     # кнопка "Додати рослину"
        ],
        states={
            ADD_CHOOSE: [
                CallbackQueryHandler(add_choose_router, pattern=r"^(add_by_photo|add_by_name|back_home)$"),
            ],
            ADD_WAIT_PHOTO: [
                MessageHandler(filters.PHOTO, add_receive_photo),
            ],
            ADD_WAIT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_receive_name),
            ],
            ADD_CONFIRM: [
                CallbackQueryHandler(add_confirm, pattern=r"^confirm_add$"),
                CallbackQueryHandler(add_cancel, pattern=r"^cancel_add$"),
            ],
            ADD_PHOTO_EXIST: [
                MessageHandler(filters.PHOTO, on_add_photo_exist),
            ],
            RENAME_WAIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_rename_text),
            ],
        },
        fallbacks=[CallbackQueryHandler(add_cancel, pattern=r"^cancel_add$")],
        allow_reentry=True,
    )
    app.add_handler(add_flow)

    # Окремі callback-и
    app.add_handler(CallbackQueryHandler(on_task_action, pattern=r"^task:\d+:(done|defer|skip)$"))

    # Catch-all роутер (повинен бути останнім)
    app.add_handler(CallbackQueryHandler(router))

    return app
