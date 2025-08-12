# plantbot/keyboards.py
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 План на сьогодні", callback_data="today_plan")],
        [InlineKeyboardButton("📅 Розклад на тиждень", callback_data="week_plan")],
        [InlineKeyboardButton("🌿 Мої рослини", callback_data="my_plants")],
        [InlineKeyboardButton("➕ Додати рослину", callback_data="add_plant"),
         InlineKeyboardButton("🗑 Видалити", callback_data="delete_plant")],
    ])

def plants_list_kb(rows):
    btns = [[InlineKeyboardButton(name, callback_data=f"plant_{pid}")] for (pid, name) in rows]
    btns.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_home")])
    return InlineKeyboardMarkup(btns)

def plant_card_kb(pid:int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Догляд", callback_data=f"care_{pid}")],
        [InlineKeyboardButton("✏️ Редагувати назву", callback_data=f"rename_{pid}")],
        [InlineKeyboardButton("📷 Додати/оновити фото (вручну)", callback_data=f"addphoto_{pid}")],
        [InlineKeyboardButton("🖼 Оновити фото за назвою", callback_data=f"plantidphoto_{pid}")],
        [InlineKeyboardButton("✅ Полив зроблено", callback_data=f"done_water_{pid}")],
        [InlineKeyboardButton("✅ Підживлення зроблено", callback_data=f"done_feed_{pid}")],
        [InlineKeyboardButton("✅ Обприскування зроблено", callback_data=f"done_mist_{pid}")],
        [InlineKeyboardButton("⬅️ До списку", callback_data="my_plants")]
    ])

def per_task_buttons(task_id: int, plant_name: str):
    return [
        InlineKeyboardButton(f"✅ {plant_name}", callback_data=f"task:{task_id}:done"),
        InlineKeyboardButton("⏩ Відкласти", callback_data=f"task:{task_id}:defer"),
        InlineKeyboardButton("🚫 Пропустити", callback_data=f"task:{task_id}:skip"),
    ]
