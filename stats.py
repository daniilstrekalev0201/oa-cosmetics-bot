"""Статистика: сколько людей заходит в бота и что они смотрят.

Админы не учитываются. Хранится в DATA_DIR (на Bothost это /app/data —
постоянный диск, он не стирается при перезапуске и обновлении из Git).
Храним только Telegram ID и даты, без имён.
"""

import html
import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from catalog import LOCAL_TZ, admin_ids

DATA_DIR = Path(os.getenv("DATA_DIR") or Path(__file__).with_name("data"))
STATS_FILE = DATA_DIR / "stats.json"
KEEP_DAYS = 120  # дневные счётчики старше этого удаляются

_stats: dict | None = None


def _today() -> date:
    return datetime.now(LOCAL_TZ).date()


def _load() -> dict:
    global _stats
    if _stats is None:
        try:
            _stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            _stats = {}
        except (OSError, ValueError) as err:
            logging.warning("Не удалось прочитать статистику, начинаю заново: %s", err)
            _stats = {}
        _stats.setdefault("users", {})
        _stats.setdefault("days", {})
    return _stats


def _save(stats: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, STATS_FILE)
    except OSError as err:
        logging.warning("Не удалось сохранить статистику: %s", err)


def track(user_id: int, event: str, key: str = "") -> None:
    """Учитывает действие покупателя: start, category, product, about."""
    if user_id in admin_ids():
        return
    stats = _load()
    uid, today = str(user_id), _today().isoformat()

    user = stats["users"].setdefault(uid, {"first": today})
    user["last"] = today

    day = stats["days"].setdefault(today, {"active": [], "events": {}})
    if uid not in day["active"]:
        day["active"].append(uid)
    counters = day["events"].setdefault(event, {})
    counters[key] = counters.get(key, 0) + 1

    _prune_and_save(stats)


def track_site(event: str, key: str = "") -> None:
    """Учитывает событие на сайте: visit (открыли страницу), click (нажали кнопку-ссылку)."""
    stats = _load()
    day = stats["days"].setdefault(_today().isoformat(), {"active": [], "events": {}})
    counters = day.setdefault("site", {}).setdefault(event, {})
    counters[key] = counters.get(key, 0) + 1
    _prune_and_save(stats)


def _prune_and_save(stats: dict) -> None:
    oldest = (_today() - timedelta(days=KEEP_DAYS)).isoformat()
    for d in [d for d in stats["days"] if d < oldest]:
        del stats["days"][d]
    _save(stats)


# ---------- отчёт для админки ----------

def _since(days: int) -> str:
    return (_today() - timedelta(days=days - 1)).isoformat()


def _count(stats: dict, event: str, days: int) -> dict[str, int]:
    total: dict[str, int] = {}
    start = _since(days)
    for d, day in stats["days"].items():
        if d >= start:
            for key, n in day["events"].get(event, {}).items():
                total[key] = total.get(key, 0) + n
    return total


def _count_site(stats: dict, event: str, days: int) -> dict[str, int]:
    total: dict[str, int] = {}
    start = _since(days)
    for d, day in stats["days"].items():
        if d >= start:
            for key, n in day.get("site", {}).get(event, {}).items():
                total[key] = total.get(key, 0) + n
    return total


def _active(stats: dict, days: int) -> int:
    start = _since(days)
    return len({u for d, day in stats["days"].items() if d >= start for u in day["active"]})


def _new(stats: dict, days: int) -> int:
    start = _since(days)
    return sum(1 for u in stats["users"].values() if u["first"] >= start)


def report(catalog: dict) -> str:
    stats = _load()
    e = html.escape
    names = {p["id"]: p["name"] for p in catalog["products"]}
    cats = {c["id"]: c["title"] for c in catalog["categories"]}

    lines = [
        "📊 <b>Статистика</b> <i>(вы и другие админы не учитываетесь)</i>",
        "",
        f"👥 Всего людей заходило: <b>{len(stats['users'])}</b>",
        f"🆕 Новых: сегодня <b>{_new(stats, 1)}</b> · за 7 дней <b>{_new(stats, 7)}</b> · за 30 дней <b>{_new(stats, 30)}</b>",
        f"🙋 Заходили: сегодня <b>{_active(stats, 1)}</b> · за 7 дней <b>{_active(stats, 7)}</b> · за 30 дней <b>{_active(stats, 30)}</b>",
    ]

    views = sorted(_count(stats, "product", 30).items(), key=lambda kv: -kv[1])
    lines += ["", "👀 <b>Просмотры товаров за 30 дней:</b>"]
    if views:
        for i, (pid, n) in enumerate(views[:15], 1):
            lines.append(f"{i}. {e(names.get(pid, 'удалённый товар'))} — {n}")
    else:
        lines.append("пока нет")

    opened = _count(stats, "category", 30)
    if opened:
        lines += ["", "🗂 <b>Открывали разделы за 30 дней:</b>"]
        for cid, n in sorted(opened.items(), key=lambda kv: -kv[1]):
            lines.append(f"• {e(cats.get(cid, 'удалённый раздел'))} — {n}")
    about = sum(_count(stats, "about", 30).values())
    lines.append(f"ℹ️ «О магазине» открывали за 30 дней: {about}")

    lines += ["", "📅 <b>Последние 7 дней</b> (👥 людей · 👀 просмотров товаров):"]
    for i in range(6, -1, -1):
        d = _today() - timedelta(days=i)
        day = stats["days"].get(d.isoformat(), {"active": [], "events": {}})
        n_views = sum(day["events"].get("product", {}).values())
        lines.append(f"{d:%d.%m} — 👥 {len(day['active'])} · 👀 {n_views}")

    visits = sum(_count_site(stats, "visit", 30).values())
    clicks = sorted(_count_site(stats, "click", 30).items(), key=lambda kv: -kv[1])
    lines += ["", f"🌐 <b>Сайт за 30 дней:</b> открывали {visits} раз"]
    if clicks:
        lines.append("Нажатия на кнопки на сайте:")
        lines += [f"• {e(key)} — {n}" for key, n in clicks[:15]]

    lines += ["", "<i>Нажатия на кнопки-ссылки внутри Telegram боту не видны — считаются только на сайте.</i>"]
    return "\n".join(lines)
