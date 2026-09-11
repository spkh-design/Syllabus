import json
import requests
from datetime import datetime

BASE = "https://surpk.ru"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://schedule.surpk.ru",
    "Referer": "https://schedule.surpk.ru/",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/152.0.0.0 Safari/537.36"),
}

# Номера пар и их время
LESSON_TIMES = {
    1: "08:30–09:50",
    2: "10:00–11:20",
    3: "11:30–12:50",
    4: "13:40–15:00",
    5: "15:10–16:30",
    6: "16:40–18:00",
    7: "18:10–19:30",
}


def fetch_base_info():
    """Загружает справочники: группы, преподаватели, дисциплины, аудитории."""
    r = requests.get(f"{BASE}/api/schedule/index", headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data["baseInfo"]["data"]


def fetch_schedule(date: datetime):
    """Загружает расписание на конкретный день."""
    ts = int(date.timestamp() * 1000)
    r = requests.get(f"{BASE}/api/schedule/schedule", params={"date": ts},
                     headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def build_lookup(base):
    """Строит словари UUID -> название для всех сущностей."""
    lookup = {}
    for key in ("disciplines", "teachers", "audithories", "lesson_Types",
                "divisions", "territories", "groups"):
        lookup[key] = {item["id"]: item.get("name", "—") for item in base.get(key, [])}
    lookup["audithories_short"] = {
        a["id"]: a.get("short_name") or a.get("name", "—")
        for a in base.get("audithories", [])
    }
    return lookup


def find_group(base, name: str, division_name_part: str = None):
    """Ищет группу по имени (и, опционально, по подразделению)."""
    # Сначала найдём UUID подразделения по частичному совпадению
    div_id = None
    if division_name_part:
        for d in base["divisions"]:
            if division_name_part.lower() in d["name"].lower():
                div_id = d["id"]
                break

    for g in base["groups"]:
        if g["name"].strip().lower() == name.strip().lower():
            if div_id is None or g["division"] == div_id:
                return g
    return None


def print_schedule_for_group(base, group_name: str, division_part: str, date: datetime):
    lookup = build_lookup(base)
    group = find_group(base, group_name, division_part)
    if not group:
        print(f"❌ Группа '{group_name}' не найдена")
        return

    group_id = group["id"]
    print(f"🔍 Группа: {group['name']} (курс {group['curse']}), UUID={group_id}")
    print(f"📅 Дата: {date.strftime('%d.%m.%Y')}\n")

    data = fetch_schedule(date)
    days = data.get("schedule", {}).get("data", {}).get("schedule", [])

    # Находим нужный день (по timestamp)
    ts_requested = int(date.timestamp() * 1000)
    target_day = None
    for day in days:
        # API иногда отдаёт все дни; сравниваем по дате из timestamp
        day_dt = datetime.fromtimestamp(day["date"] / 1000)
        if day_dt.date() == date.date():
            target_day = day
            break

    if not target_day:
        print("⚠️  В ответе нет данных на эту дату. Показываю первый день из ответа:")
        target_day = days[0] if days else None

    if not target_day:
        print("❌ Расписание пустое")
        return

    lessons = [l for l in target_day["lessons"] if l["group"] == group_id]
    lessons.sort(key=lambda l: (l["number_lesson"], l["subgroup"]))

    if not lessons:
        print("🎉 Пар нет — выходной или расписание не задано.")
        return

    for l in lessons:
        num = l["number_lesson"]
        time = LESSON_TIMES.get(num, f"пара {num}")
        disc = lookup["disciplines"].get(l["discipline"], "—")
        teacher = lookup["teachers"].get(l["teacher"], "—")
        aud = lookup["audithories_short"].get(l["auditoria"], "—")
        ltype = lookup["lesson_Types"].get(l["lesson_type"], "—")
        sub = f" (подгруппа {l['subgroup']})" if l["subgroup"] else ""
        print(f"  {num}. {time} | {disc}{sub}")
        print(f"     {ltype} | каб. {aud} | {teacher}")
        print()


if __name__ == "__main__":
    base = fetch_base_info()
    print(f"✅ Справочники загружены: "
          f"{len(base['groups'])} групп, "
          f"{len(base['teachers'])} преподавателей, "
          f"{len(base['disciplines'])} дисциплин, "
          f"{len(base['audithories'])} аудиторий\n")

    # Пример: группа 419, отделение СП-4, дата 11.09.2026
    print_schedule_for_group(base, group_name="419",
                             division_part="СП-4",
                             date=datetime(2026, 9, 11))