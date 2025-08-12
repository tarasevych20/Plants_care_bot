import os
import requests
import datetime as dt
import time

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
OWM_KEY = os.environ["OPENWEATHER_API_KEY"]
CITY = "Kyiv"

# Графік догляду
SCHEDULE = {
    "Драцена": {"poliv": 14, "pidzh": None, "notes": "щойно пересаджена; без добрив 2–3 тижні після 12 серпня"},
    "Каламондин": {"poliv": 3, "pidzh": 14, "notes": "яскраве світло збоку від вікна; у спеку перевіряй частіше"},
    "Хамаедорея": {"poliv": 5, "pidzh": 30, "notes": "розсіяне світло; обприскування"},
    "Заміокулькас": {"poliv": 14, "pidzh": 42, "notes": "полив тільки після повного просихання"},
    "Спатіфілум": {"poliv": 4, "pidzh": 14, "notes": "ґрунт злегка вологий постійно"},
}

# Останні дати робіт (можна потім виносити в БД)
BASELINE = dt.date(2025, 8, 12)
LAST = {p: {"poliv": BASELINE, "pidzh": BASELINE if sch["pidzh"] else None}
        for p, sch in SCHEDULE.items()}

def get_weather():
    url = f"https://api.openweathermap.org/data/2.5/weather?q={CITY}&appid={OWM_KEY}&units=metric&lang=ua"
    r = requests.get(url, timeout=15).json()
    t = round(r["main"]["temp"])
    desc = r["weather"][0]["description"]
    hum = r["main"]["humidity"]
    return t, desc, hum

def plan_for_today():
    today = dt.date.today()
    t, desc, hum = get_weather()

    to_water, to_feed, to_mist = [], [], []

    for plant, sch in SCHEDULE.items():
        # Полив
        if sch["poliv"]:
            last = LAST[plant]["poliv"]
            due = (today - last).days >= sch["poliv"]
            if t >= 30 or hum <= 35:  # у спеку частіше
                due = due or (today - last).days >= max(2, sch["poliv"] - 1)
            if due:
                to_water.append(plant)

        # Підживлення
        if sch["pidzh"]:
            lastf = LAST[plant]["pidzh"]
            if lastf is None or (today - lastf).days >= sch["pidzh"]:
                to_feed.append(plant)

    # Обприскування
    if t >= 28 or hum < 40:
        to_mist = ["Хамаедорея", "Спатіфілум", "Драцена"]

    if to_water or to_feed or to_mist:
        blocks = [
            f"Погода в Києві: {t}°C, {desc}, вологість {hum}%",
            f"Полий: {', '.join(to_water)}" if to_water else None,
            f"Підживи: {', '.join(to_feed)}" if to_feed else None,
            f"Обприскай: {', '.join(to_mist)}" if to_mist else None
        ]
        notes = []
        for p in set(to_water + to_feed):
            n = SCHEDULE[p].get("notes")
            if n:
                notes.append(f"• {p}: {n}")
        if notes:
            blocks.append("Нотатки:\n" + "\n".join(notes))
        return "Доброго ранку! 🌱\n" + "\n".join([b for b in blocks if b])
    return None

def send(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": msg}, timeout=15)

if __name__ == "__main__":
    while True:
        now = dt.datetime.now()
        if now.hour == 9 and now.minute == 0:  # щодня о 09:00
            msg = plan_for_today()
            if msg:
                send(msg)
            time.sleep(60)  # щоб не дублювався
        time.sleep(20)
