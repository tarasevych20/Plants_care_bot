# bot.py
import os, requests, datetime as dt, time
from zoneinfo import ZoneInfo  # стандартно в Python 3.9+

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
OWM_KEY = os.environ["OPENWEATHER_API_KEY"]
CITY = "Kyiv"
TZ = ZoneInfo("Europe/Kyiv")

SCHEDULE = {
    "Драцена":     {"poliv": 14, "pidzh": None, "notes": "щойно пересаджена; без добрив 2–3 тижні після 12 серпня"},
    "Каламондин":  {"poliv": 3,  "pidzh": 14,   "notes": "яскраве світло збоку від вікна; у спеку перевіряй частіше"},
    "Хамаедорея":  {"poliv": 5,  "pidzh": 30,   "notes": "розсіяне світло; обприскування"},
    "Заміокулькас":{"poliv": 14, "pidzh": 42,   "notes": "полив тільки після повного просихання"},
    "Спатіфілум":  {"poliv": 4,  "pidzh": 14,   "notes": "ґрунт злегка вологий постійно"},
}

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
    today = dt.datetime.now(TZ).date()
    t, desc, hum = get_weather()

    to_water, to_feed, to_mist = [], [], []

    for plant, sch in SCHEDULE.items():
        if sch["poliv"]:
            last = LAST[plant]["poliv"]
            due = (today - last).days >= sch["poliv"]
            if t >= 30 or hum <= 35:
                due = due or (today - last).days >= max(2, sch["poliv"] - 1)
            if due:
                to_water.append(plant)

        if sch["pidzh"]:
            lastf = LAST[plant]["pidzh"]
            if lastf is None or (today - lastf).days >= sch["pidzh"]:
                to_feed.append(plant)

    if t >= 28 or hum < 40:
        to_mist = ["Хамаедорея", "Спатіфілум", "Драцена"]

    if to_water or to_feed or to_mist:
        blocks = [f"Погода в Києві: {t}°C, {desc}, вологість {hum}%"]
        if to_water: blocks.append("Полий: " + ", ".join(to_water))
        if to_feed:  blocks.append("Підживи: " + ", ".join(to_feed))
        if to_mist:  blocks.append("Обприскай: " + ", ".join(to_mist))
        notes = []
        for p in set(to_water + to_feed):
            n = SCHEDULE[p].get("notes")
            if n: notes.append(f"• {p}: {n}")
        if notes: blocks.append("Нотатки:\n" + "\n".join(notes))
        return "Доброго ранку! 🌱\n" + "\n".join(blocks)
    return None

def send(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": msg}, timeout=15)

if __name__ == "__main__":
    print("Bot started, waiting for 09:00 Europe/Kyiv…", flush=True)
    while True:
        now = dt.datetime.now(TZ)
        if now.hour == 9 and now.minute == 0:   # щодня о 09:00 за Києвом
            msg = plan_for_today()
            if msg:
                send(msg)
                print("Plan sent.", flush=True)
            time.sleep(65)  # пауза, щоб не відправляти двічі в ту ж хвилину
        time.sleep(20)
