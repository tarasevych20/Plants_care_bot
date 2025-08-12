# plantbot/handlers.py
from telegram import Update, InlineKeyboardMarkup
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

SELECT_ADD_MODE, ADD_NAME, ADD_PHOTO_NEW, ADD_PHOTO_EXIST, RENAME_WAIT = range(5)

# ===== Core Handlers =====
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    migrate_legacy_rows_to_user(uid)
    ensure_week_tasks_for_user(uid)
    await update.message.reply_text("Привіт! Я бот догляду за рослинами 🌱", reply_markup=main_kb())

async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; data = q.data
    uid = update.effective_user.id
    await q.answer()

    if data == "today_plan":
        ensure_week_tasks_for_user(uid)
        text, kb_rows = today_tasks_markup_and_text(uid, per_task_buttons)
        kb = InlineKeyboardMarkup(kb_rows) if kb_rows else None
        await q.message.reply_text(text, reply_markup=kb or main_kb()); return

    if data == "week_plan":
        ensure_week_tasks_for_user(uid)
        await q.message.reply_text(week_overview_text(uid), reply_markup=main_kb()); return

    if data == "my_plants":
        c = conn()
        rows = c.execute("SELECT id,name FROM plants WHERE user_id=? ORDER BY name", (uid,)).fetchall()
        c.close()
        if not rows:
            await q.message.reply_text("У тебе поки немає рослин. Додай першу 🌱", reply_markup=main_kb()); return
        await q.message.reply_text("Твої рослини:", reply_markup=plants_list_kb(rows)); return

    if data.startswith("plant_"):
        pid = int(data.split("_")[1])
        c = conn()
        row = c.execute("SELECT name, care, photo FROM plants WHERE id=? AND user_id=?", (pid, uid)).fetchone()
        c.close()
        if not row: await q.message.reply_text("Не знайшов цю рослину 🤔", reply_markup=main_kb()); return
        name, care, photo = row
        caption = f"*{name}*\n{care}"
        if photo:
            await q.message.reply_photo(photo=photo, caption=caption, parse_mode="Markdown", reply_markup=plant_card_kb(pid))
        else:
            await q.message.reply_text(caption, parse_mode="Markdown", reply_markup=plant_card_kb(pid))
        return

    if data.startswith("care_"):
        pid = int(data.split("_")[1])
        c = conn(); name = c.execute("SELECT name FROM plants WHERE id=? AND user_id=?", (pid, uid)).fetchone(); c.close()
        if not name: await q.message.reply_text("Не знайшов.", reply_markup=main_kb()); return
        care, *_ = care_and_intervals_for(name[0])
        await q.message.reply_text(care, reply_markup=plant_card_kb(pid)); return

    if data.startswith("rename_"):
        pid = int(data.split("_")[1])
        context.user_data["rename_pid"] = pid
        await q.message.reply_text("Введи нову назву для цієї рослини одним повідомленням:")
        return RENAME_WAIT

    if data == "delete_plant":
        c = conn()
        rows = c.execute("SELECT id,name FROM plants WHERE user_id=? ORDER BY name", (uid,)).fetchall()
        c.close()
        if not rows:
            await q.message.reply_text("Список порожній.", reply_markup=main_kb()); return
        from telegram import InlineKeyboardButton
        buttons = [[InlineKeyboardButton(f"🗑 {nm}", callback_data=f"del_{pid}")] for pid,nm in rows]
        buttons.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_home")])
        await q.message.reply_text("Оберіть рослину для видалення:", reply_markup=InlineKeyboardMarkup(buttons)); return

    if data.startswith("del_"):
        pid = int(data.split("_")[1])
        c = conn(); c.execute("DELETE FROM plants WHERE id=? AND user_id=?", (pid, uid)); c.commit(); c.close()
        await q.message.reply_text("Видалив ✅", reply_markup=main_kb()); return

    if data == "add_plant":
        from telegram import InlineKeyboardButton
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Ввести назву вручну", callback_data="mode_name")],
            [InlineKeyboardButton("Фото (авто-розпізнавання)", callback_data="mode_photo")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back_home")]
        ])
        await q.message.reply_text("Як додамо рослину?", reply_markup=kb); return SELECT_ADD_MODE

    if data == "back_home":
        await q.message.reply_text("Головне меню:", reply_markup=main_kb()); return

    # швидкі кнопки з картки (миттєва відмітка дії)
    if any(data.startswith(p) for p in ["done_water_", "done_feed_", "done_mist_"]):
        pid = int(data.split("_")[2])
        kind = 'water' if "water" in data else 'feed' if "feed" in data else 'mist'
        c = conn()
        # due_date = сьогодні
        from datetime import date
        c.execute("""INSERT INTO tasks(user_id,plant_id,kind,due_date,status,created_at)
                     VALUES(?,?,?,?,?,?)""", (uid, pid, kind, date.today().isoformat(), 'due', date.today().isoformat()))
        tid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.commit(); c.close()
        mark_task_done(tid)
        await q.message.reply_text("Записав ✅", reply_markup=plant_card_kb(pid)); return

    # фото за назвою (Wikidata P18)
    if data.startswith("plantidphoto_"):
        pid = int(data.split("_")[1])
        c = conn()
        row = c.execute("SELECT name FROM plants WHERE id=? AND user_id=?", (pid, uid)).fetchone()
        c.close()
        if not row: await q.message.reply_text("Не знайшов.", reply_markup=main_kb()); return
        r = resolve_plant_name(row[0])
        img = wikidata_image_by_qid(r["qid"]) if r.get("qid") else None
        if not img:
            await q.message.reply_text("Не вийшло знайти фото за цією назвою. Спробуй уточнити назву або додати фото вручну.")
            return
        c = conn(); c.execute("UPDATE plants SET photo=? WHERE id=? AND user_id=?", (img, pid, uid)); c.commit(); c.close()
        await q.message.reply_text("Фото оновив за назвою ✅", reply_markup=plant_card_kb(pid)); return

# ===== Conversations =====
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
    uid = update.effective_user.id
    raw = (update.message.text or "").strip()
    if not raw:
        await update.message.reply_text("Порожня назва. Спробуй ще раз."); return ADD_NAME
    care, wi, fi, mi, photo, canonical, source = care_and_photo_by_name(raw)
    from datetime import date
    c = conn()
    c.execute("""INSERT INTO plants(user_id,name,care,photo,water_int,feed_int,mist_int,
                 last_watered,last_fed,last_misted)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (uid, raw, care, photo, wi, fi, mi, date.today().isoformat(), date.today().isoformat(), date.today().isoformat()))
    c.commit(); c.close()
    ensure_week_tasks_for_user(uid)
    note = f"(розпізнано як: {canonical}, джерело: {source})"
    await update.message.reply_text(f"Додав «{raw}» ✅ {note}\nРозклад оновлено.", reply_markup=main_kb())
    return ConversationHandler.END

async def on_add_photo_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not update.message.photo:
        await update.message.reply_text("Це не фото 🙃 Надішли зображення."); return ADD_PHOTO_NEW
    file = await update.message.photo[-1].get_file()
    img = await file.download_as_bytearray()
    name, ref_img = plantid_name_and_image(bytes(img))
    canonical = name or "Нова рослина"
    care, wi, fi, mi = care_and_intervals_for(canonical)
    photo = ref_img or bytes(img)
    from datetime import date
    c = conn()
    c.execute("""INSERT INTO plants(user_id,name,care,photo,water_int,feed_int,mist_int,
                 last_watered,last_fed,last_misted)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (uid, canonical, care, photo, wi, fi, mi, date.today().isoformat(), date.today().isoformat(), date.today().isoformat()))
    c.commit(); c.close()
    ensure_week_tasks_for_user(uid)
    await update.message.reply_text(f"Додав «{canonical}» ✅\nРозклад оновлено.", reply_markup=main_kb())
    return ConversationHandler.END

async def on_add_photo_exist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not update.message.photo:
        await update.message.reply_text("Це не фото 🙃 Надішли зображення."); return ADD_PHOTO_EXIST
    pid = context.user_data.get("target_pid")
    file = await update.message.photo[-1].get_file()
    img = await file.download_as_bytearray()
    c = conn(); c.execute("UPDATE plants SET photo=? WHERE id=? AND user_id=?", (bytes(img), pid, uid)); c.commit(); c.close()
    await update.message.reply_text("Фото оновив ✅", reply_markup=main_kb())
    return ConversationHandler.END

async def on_rename_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    pid = context.user_data.get("rename_pid")
    new_raw = (update.message.text or "").strip()
    if not pid or not new_raw:
        await update.message.reply_text("Порожня назва. Спробуй ще раз."); return RENAME_WAIT
    r = resolve_plant_name(new_raw)
    care, wi, fi, mi = care_and_intervals_for(r["canonical"])
    c = conn()
    c.execute("""UPDATE plants SET name=?, care=?, water_int=?, feed_int=?, mist_int=?
                 WHERE id=? AND user_id=?""",
              (new_raw, care, wi, fi, mi, pid, uid))
    c.commit(); c.close()
    img = wikidata_image_by_qid(r.get("qid")) if r.get("qid") else None
    if img:
        c = conn(); c.execute("UPDATE plants SET photo=? WHERE id=? AND user_id=?", (img, pid, uid)); c.commit(); c.close()
    await update.message.reply_text(
        f"Оновив назву на «{new_raw}». Розпізнав як: {r['canonical']} ({r['source']}). Догляд оновлено.",
        reply_markup=main_kb()
    )
    return ConversationHandler.END

# ===== Utils =====
def iso_today():
    from datetime import date
    return date.today().isoformat()

# ===== App Builder =====
def build_app():
    app = ApplicationBuilder().token(TOKEN).build()

    # conversations
    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_choose, pattern="^(mode_name|mode_photo|back_home)$")],
        states={
            SELECT_ADD_MODE: [CallbackQueryHandler(add_choose, pattern="^(mode_name|mode_photo|back_home)$")],
            ADD_NAME:        [MessageHandler(filters.TEXT & ~filters.COMMAND, on_add_name)],
            ADD_PHOTO_NEW:   [MessageHandler(filters.PHOTO, on_add_photo_new)],
            RENAME_WAIT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, on_rename_text)],
        },
        fallbacks=[],
        map_to_parent={},
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(add_conv)
    app.add_handler(CallbackQueryHandler(router))  # catch-all
    app.add_handler(CallbackQueryHandler(on_task_action, pattern=r"^task:\d+:(done|defer|skip)$"))
    app.add_handler(CallbackQueryHandler(start_update_photo_manual, pattern=r"^addphoto_\d+$"))
    return app

# quick helpers for buttons
async def start_update_photo_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    pid = int(q.data.split("_")[1]); context.user_data["target_pid"] = pid
    await q.message.reply_text("Надішли одне фото цієї рослини (jpg/png).")
    return ADD_PHOTO_EXIST

async def on_task_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        _, tid_str, action = q.data.split(":")
        tid = int(tid_str)
    except Exception:
        await q.answer("Помилка callback"); return
    if action == "done":   mark_task_done(tid);   await q.answer("Готово ✅")
    if action == "defer":  move_task_to_next_care_day(tid); await q.answer("Перенесено ⏩")
    if action == "skip":   mark_task_skipped(tid); await q.answer("Пропущено 🚫")

    # refresh today's view
    uid = update.effective_user.id
    text, kb_rows = today_tasks_markup_and_text(uid, per_task_buttons)
    kb = InlineKeyboardMarkup(kb_rows) if kb_rows else None
    await q.message.edit_text(text, reply_markup=kb or None)
