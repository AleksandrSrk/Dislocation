"""Разбор дат, которые пользователь вводит руками."""
import re
from datetime import date, datetime

DATE_RE = re.compile(r"^\s*(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\s*(?:(\d{1,2})[:.](\d{2}))?\s*$")


def parse_datetime(text: str, today: date) -> datetime | None:
    """«24.09.2026 17:43», «24.09.2026», «24.09 17:43», «24.09» → datetime. Год по умолчанию текущий."""
    m = DATE_RE.match(text)
    if not m:
        return None
    day, month, year, hour, minute = m.groups()
    year = int(year) if year else today.year
    if year < 100:
        year += 2000
    try:
        return datetime(year, int(month), int(day), int(hour or 0), int(minute or 0))
    except ValueError:
        return None


def parse_date(text: str, today: date) -> date | None:
    dt = parse_datetime(text, today)
    return dt.date() if dt else None
