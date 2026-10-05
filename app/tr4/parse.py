"""Парсинг книги 1 ТР4: участки со станциями и километражем.

Формат листа (одна дорога на лист):
    " 9) участок 94-009 "ХАРАНОР - ПРИАРГУНСК" (Основной тарифный участок)"
    "№ п/п | Коды | от станции | до ст. Харанор | до ст. Приаргунск"
    "  8.  | 946002 . | Досатуй | 169 км | 38 км"
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import xlrd

RUSSIA_MARK = "(Р)"
CODE_RE = re.compile(r"(\d{6})")
KM_RE = re.compile(r"(\d+)\s*км")
TYPE_RE = re.compile(r"\(([^()]*)\)\s*$")
END_PREFIX_RE = re.compile(r"^до ст\.\s*")


@dataclass
class Section:
    """Участок: тип и станции в географическом порядке."""
    kind: str
    road: str
    title: str
    end_a: str | None = None          # название конца из шапки «до ст. …»
    end_b: str | None = None
    # (код ЕСР, название, км от конца A, км до конца B)
    rows: list[tuple[str, str, int, int | None]] = field(default_factory=list)

    @property
    def length(self) -> int | None:
        """Полная длина участка A–B (сумма двух км-колонок любой строки)."""
        for _, _, ka, kb in self.rows:
            if kb is not None:
                return ka + kb
        return None


def parse_book1(path: Path) -> list[Section]:
    """Читает все российские участки из книги 1."""
    book = xlrd.open_workbook(str(path))
    sections: list[Section] = []
    for sheet in book.sheets():
        if RUSSIA_MARK not in sheet.name:
            continue
        current: Section | None = None
        for r in range(sheet.nrows):
            row = [str(v) for v in sheet.row_values(r)]
            first = row[0].strip()
            if "участок" in first:
                kind = TYPE_RE.search(first)
                current = Section(kind=kind.group(1) if kind else "?", road=sheet.name, title=first)
                sections.append(current)
                continue
            if current is None or len(row) < 4:
                continue
            if row[1].strip() == "Коды":
                current.end_a = END_PREFIX_RE.sub("", row[3]).strip() or None
                current.end_b = END_PREFIX_RE.sub("", row[4]).strip() if len(row) > 4 else None
                continue
            code = CODE_RE.search(row[1])
            km_a = KM_RE.search(row[3])
            if not (code and km_a):
                continue
            km_b = KM_RE.search(row[4]) if len(row) > 4 else None
            current.rows.append((code.group(1), row[2].strip(), int(km_a.group(1)),
                                 int(km_b.group(1)) if km_b else None))
    return sections
