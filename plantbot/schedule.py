# plantbot/schedule.py
from datetime import date, timedelta
from .db import conn
from .config import CARE_DAYS

def iso(d: date): return d.isoformat()
def today(): return date.today()
def fromiso(s: str): return date.fromisoformat(s)

def next_care_day(from_day: date) -> date:
    wd = from_day.weekday()
    cands = [from_day + timedelta(days=((cd - wd) % 7)) for cd in CARE_DAYS]
    return sorted(cands)[0]

def following_care_day(after_day: date) -> date:
    wd = after_day.weekday()
    deltas = [((cd - wd) % 7) or 7 for cd in CARE_DAYS]
    return after_day + timedelta(days=min(deltas))

def ensure_week_tasks_for_user(user_id: int):
    c = conn()
    rows = c.execute("""SELECT id,name,water_int,feed_int,mist_int,last_watered,last_fed,last_misted
                        FROM plants WHERE user_id=?""", (user_id,)).fetchall()
    t = today(); horizon = t + timedelta(days=7)

    def schedule_if_due(plant_id, kind, interval, last_iso):
        if not interval: return
        last = fromiso(last_iso) if last_iso else t
        due = last + timedelta(days=interval)
        anchor = next_care_day(due)
        if anchor > horizon: return
        exists = c.execute("""SELECT 1 FROM tasks
                              WHERE user_id=? AND plant_id=? AND kind=? AND due_date=? AND status='due'""",
                           (user_id, plant_id, kind, iso(anchor))).fetchone()
        if not exists:
            c.execute("""INSERT INTO tasks(user_id,plant_id,kind,due_date,status,created_at)
                         VALUES(?,?,?,?,?,?)""",
                      (user_id, plant_id, kind, iso(anchor), 'due', iso(t)))

    for pid,_,wi,fi,mi,lw,lf,lm in rows:
        schedule_if_due(pid,'water',wi,lw)
        schedule_if_due(pid,'feed', fi,lf)
        schedule_if_due(pid,'mist', mi,lm)

    c.commit(); c.close()

def mark_task_done(task_id: int):
    c = conn()
    row = c.execute("SELECT user_id,plant_id,kind FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row: c.close(); return
    user_id, plant_id, kind = row
    t = iso(today())
    c.execute("UPDATE tasks SET status='done' WHERE id=?", (task_id,))
    field = 'last_watered' if kind=='water' else 'last_fed' if kind=='feed' else 'last_misted'
    c.execute(f"UPDATE plants SET {field}=? WHERE id=? AND user_id=?", (t, plant_id, user_id))
    c.commit(); c.close()

def move_task_to_next_care_day(task_id: int):
    c = conn()
    row = c.execute("SELECT user_id,plant_id,kind,due_date FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not row: c.close(); return
    user_id, plant_id, kind, due_iso = row
    new_due = following_care_day(fromiso(due_iso))
    c.execute("UPDATE tasks SET status='deferred' WHERE id=?", (task_id,))
    c.execute("""INSERT INTO tasks(user_id,plant_id,kind,due_date,status,created_at)
                 VALUES(?,?,?,?,?,?)""",
              (user_id, plant_id, kind, iso(new_due), 'due', iso(today())))
    c.commit(); c.close()

def mark_task_skipped(task_id: int):
    c = conn(); c.execute("UPDATE tasks SET status='skipped' WHERE id=?", (task_id,)); c.commit(); c.close()

def week_overview_text(user_id: int):
    c = conn()
    rows = c.execute("""
        SELECT t.due_date, t.kind, p.name
        FROM tasks t JOIN plants p ON p.id=t.plant_id
        WHERE t.user_id=? AND t.status='due'
          AND date(t.due_date) BETWEEN date('now') AND date('now','+7 day')
        ORDER BY t.due_date, p.name
    """, (user_id,)).fetchall()
    c.close()
    if not rows: return "На найближчий тиждень завдань немає — все під контролем ✨"
    kinds = {'water':'Полив', 'feed':'Підживлення', 'mist':'Обприскування'}
    by_day = {}
    for due_iso, kind, name in rows:
        by_day.setdefault(due_iso, {}).setdefault(kind, []).append(name)

    lines = ["📅 Розклад на тиждень:"]
    from datetime import date as dcls
    for due_iso in sorted(by_day.keys()):
        d = dcls.fromisoformat(due_iso)
        lines.append(f"• {d.strftime('%d %B (%a)')}")
        for kind, names in by_day[due_iso].items():
            lines.append(f"  – {kinds[kind]}: {', '.join(sorted(names))}")
    return "\n".join(lines)

def today_tasks_markup_and_text(user_id: int, kb_factory):
    c = conn()
    rows = c.execute("""
        SELECT t.id, t.kind, p.name
        FROM tasks t JOIN plants p ON p.id=t.plant_id
        WHERE t.user_id=? AND t.due_date=date('now') AND t.status='due'
        ORDER BY p.name
    """, (user_id,)).fetchall()
    c.close()
    if not rows: return "Сьогодні завдань немає — відпочиваємо ✨", None

    kinds = {'water':'Полив', 'feed':'Підживлення', 'mist':'Обприскування'}
    lines = ["План на сьогодні 🌱"]
    kb_rows = []
    for tid, kind, name in rows:
        lines.append(f"• {kinds[kind]}: {name}")
        kb_rows.append(kb_factory(tid, name))
    return "\n".join(lines), kb_rows
