"""Расчёт состояния вагона по текущей дислокации и текст для бота."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html import escape

from app.graph.network import Network

MSK = timezone(timedelta(hours=3))
INACCURATE_SHARE = 0.2      # до 20% пути прогноз помечается как неточный


def now_msk() -> datetime:
    """Текущее московское время без tzinfo (так хранятся все времена)."""
    return datetime.now(MSK).replace(tzinfo=None, second=0, microsecond=0)


@dataclass
class Status:
    total_km: int
    passed_km: int
    remaining_km: int
    term_days: int
    deadline_end: datetime          # конец дня «срок истекает»
    speed_km_day: float | None
    eta: datetime | None
    margin_days: float | None       # >0 — опережаем, <0 — отстаём
    inaccurate: bool
    arrived: bool


def compute_status(network: Network, from_code: str, to_code: str, accepted_at: datetime,
                   deadline: date, current_code: str, operated_at: datetime) -> Status:
    total = network.route(from_code, to_code).distance
    remaining = min(network.route(current_code, to_code).distance, total)
    passed = total - remaining
    deadline_end = datetime.combine(deadline, datetime.min.time()) + timedelta(days=1)
    term_days = (deadline - accepted_at.date()).days

    elapsed_days = (operated_at - accepted_at).total_seconds() / 86400
    speed = eta = margin = None
    arrived = remaining == 0
    if arrived:
        eta = operated_at
        margin = (deadline_end - eta).total_seconds() / 86400
    elif elapsed_days > 0 and passed > 0:
        speed = passed / elapsed_days
        eta = operated_at + timedelta(days=remaining / speed)
        margin = (deadline_end - eta).total_seconds() / 86400
    inaccurate = not arrived and (total == 0 or passed / total < INACCURATE_SHARE)
    return Status(total, passed, remaining, term_days, deadline_end, speed, eta, margin, inaccurate, arrived)


def station_label(network: Network, code: str) -> str:
    road = network.roads.get(code)
    name = network.names.get(code, code)
    return f"{name} ({road})" if road else name


def format_status(network: Network, number: str, from_code: str, to_code: str,
                  current_code: str, operated_at: datetime, st: Status) -> str:
    pct = round(st.passed_km * 100 / st.total_km) if st.total_km else 0
    lines = [
        f"🚂 <b>Накладная {escape(number)}</b>",
        f"{escape(network.names.get(from_code, from_code))} → {escape(network.names.get(to_code, to_code))}",
        f"📍 {escape(station_label(network, current_code))}, {operated_at:%d.%m %H:%M} МСК",
        "",
        f"Всего: <b>{st.total_km}</b> км",
        f"Пройдено: {st.passed_km} км ({pct}%)",
        f"Осталось: <b>{st.remaining_km}</b> км",
        "",
        f"Срок по накладной: до {st.deadline_end - timedelta(days=1):%d.%m.%Y} ({st.term_days} сут)",
    ]
    if st.arrived:
        lines.append("🏁 Вагон на станции назначения")
    if st.speed_km_day:
        lines.append(f"Средний ход: {st.speed_km_day:.0f} км/сут")
    if st.eta and not st.arrived:
        lines.append(f"Прогноз прибытия: ~{st.eta:%d.%m.%Y}")
    if st.margin_days is not None:
        days = abs(st.margin_days)
        if st.margin_days >= 0:
            lines.append(f"✅ Опережаем срок на ~{days:.0f} сут")
        else:
            lines.append(f"⚠️ Отстаём от срока на ~{days:.0f} сут")
    elif not st.arrived:
        lines.append("Прогноз пока не посчитать: вагон ещё не отъехал")
    if st.inaccurate and st.eta:
        lines.append("<i>Прогноз неточный: пройдено меньше 20% пути</i>")
    lines.append("<i>Расстояния тарифные, по ТР4</i>")
    return "\n".join(lines)
