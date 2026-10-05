"""Хранилище накладных и дислокаций (SQLite на MVP).

Времена хранятся как ISO-строки по московскому времени (как в накладных).
SQL намеренно простой — переезд на PostgreSQL без переписывания логики.
"""
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS waybills (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id      INTEGER NOT NULL,
    number        TEXT    NOT NULL,
    from_code     TEXT    NOT NULL,
    to_code       TEXT    NOT NULL,
    accepted_at   TEXT    NOT NULL,
    deadline      TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'in_transit',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS dislocations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    waybill_id    INTEGER NOT NULL REFERENCES waybills(id) ON DELETE CASCADE,
    station_code  TEXT    NOT NULL,
    operated_at   TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_waybills_owner ON waybills(owner_id, status);
CREATE INDEX IF NOT EXISTS idx_disloc_waybill ON dislocations(waybill_id, operated_at);
"""


@dataclass
class Waybill:
    id: int
    owner_id: int
    number: str
    from_code: str
    to_code: str
    accepted_at: datetime
    deadline: date
    status: str


@dataclass
class Dislocation:
    id: int
    waybill_id: int
    station_code: str
    operated_at: datetime


class Storage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: FastAPI выполняет запросы в пуле потоков
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        # WAL: бот пишет, API читает ту же базу без взаимных блокировок
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)

    @staticmethod
    def _waybill(row) -> Waybill:
        return Waybill(row["id"], row["owner_id"], row["number"], row["from_code"], row["to_code"],
                       datetime.fromisoformat(row["accepted_at"]), date.fromisoformat(row["deadline"]),
                       row["status"])

    def add_waybill(self, owner_id: int, number: str, from_code: str, to_code: str,
                    accepted_at: datetime, deadline: date) -> Waybill:
        cur = self.conn.execute(
            "INSERT INTO waybills (owner_id, number, from_code, to_code, accepted_at, deadline) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (owner_id, number, from_code, to_code, accepted_at.isoformat(), deadline.isoformat()))
        self.conn.commit()
        return self.get_waybill(cur.lastrowid)

    def get_waybill(self, waybill_id: int) -> Waybill | None:
        row = self.conn.execute("SELECT * FROM waybills WHERE id = ?", (waybill_id,)).fetchone()
        return self._waybill(row) if row else None

    def active_waybills(self, owner_id: int) -> list[Waybill]:
        rows = self.conn.execute(
            "SELECT * FROM waybills WHERE owner_id = ? AND status = 'in_transit' ORDER BY id",
            (owner_id,)).fetchall()
        return [self._waybill(r) for r in rows]

    def archived_waybills(self, owner_id: int, limit: int = 30) -> list[Waybill]:
        rows = self.conn.execute(
            "SELECT * FROM waybills WHERE owner_id = ? AND status = 'arrived' ORDER BY id DESC LIMIT ?",
            (owner_id, limit)).fetchall()
        return [self._waybill(r) for r in rows]

    def close_waybill(self, waybill_id: int) -> None:
        """Перенос в архив (не удаление)."""
        self.conn.execute("UPDATE waybills SET status = 'arrived' WHERE id = ?", (waybill_id,))
        self.conn.commit()

    def restore_waybill(self, waybill_id: int) -> None:
        self.conn.execute("UPDATE waybills SET status = 'in_transit' WHERE id = ?", (waybill_id,))
        self.conn.commit()

    def add_dislocation(self, waybill_id: int, station_code: str, operated_at: datetime) -> Dislocation:
        cur = self.conn.execute(
            "INSERT INTO dislocations (waybill_id, station_code, operated_at) VALUES (?, ?, ?)",
            (waybill_id, station_code, operated_at.isoformat()))
        self.conn.commit()
        return Dislocation(cur.lastrowid, waybill_id, station_code, operated_at)

    def dislocations(self, waybill_id: int) -> list[Dislocation]:
        rows = self.conn.execute(
            "SELECT * FROM dislocations WHERE waybill_id = ? ORDER BY operated_at, id",
            (waybill_id,)).fetchall()
        return [Dislocation(r["id"], r["waybill_id"], r["station_code"],
                            datetime.fromisoformat(r["operated_at"])) for r in rows]
