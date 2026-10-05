"""Координаты станций и геометрия маршрута для карты.

Координаты — из справочника станций (app/resources/stations_coords.csv, ключ — 5 цифр ЕСР).
Станции без координат ставятся на линию между соседями по километражу.
Явно ошибочные координаты (скачок, не объяснимый километражем) отбрасываются.
"""
import csv
import math
from dataclasses import dataclass
from pathlib import Path

from app.graph.network import Route

COORDS_CSV = Path(__file__).resolve().parent / "resources" / "stations_coords.csv"


@dataclass
class GeoPoint:
    code: str
    name: str
    km: int
    lat: float
    lon: float
    exact: bool          # координаты из справочника, а не интерполяция


def load_coords(path: Path = COORDS_CSV) -> dict[str, tuple[float, float]]:
    coords: dict[str, tuple[float, float]] = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            coords[row["code5"]] = (float(row["lat"]), float(row["lon"]))
    return coords


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def route_geometry(route: Route, coords: dict[str, tuple[float, float]]) -> list[GeoPoint]:
    """Каждой точке маршрута — координаты (свои или интерполированные)."""
    pts = route.points
    known: list[tuple[int, tuple[float, float]]] = []
    rejected = 0
    for i, p in enumerate(pts):
        c = coords.get(p.code[:5])
        if c is None:
            continue
        if known and rejected < 3:
            j, prev = known[-1]
            # по прямой не может быть сильно дальше, чем по рельсам
            if haversine_km(prev, c) > (p.km - pts[j].km) * 1.2 + 30:
                rejected += 1
                continue
        # три отказа подряд — значит, ошибочной была предыдущая точка, а не эти
        if rejected >= 3 and known:
            known.pop()
        rejected = 0
        known.append((i, c))
    if not known:
        return []

    out: list[GeoPoint] = []
    k = 0
    for i, p in enumerate(pts):
        while k + 1 < len(known) and known[k + 1][0] <= i:
            k += 1
        ia, ca = known[k]
        if ia == i:
            out.append(GeoPoint(p.code, p.name, p.km, *ca, True))
            continue
        if i < ia or k + 1 >= len(known):          # до первой / после последней известной
            out.append(GeoPoint(p.code, p.name, p.km, *ca, False))
            continue
        ib, cb = known[k + 1]
        span = pts[ib].km - pts[ia].km
        t = (p.km - pts[ia].km) / span if span else 0.0
        out.append(GeoPoint(p.code, p.name, p.km,
                            ca[0] + (cb[0] - ca[0]) * t, ca[1] + (cb[1] - ca[1]) * t, False))
    return out


def split_at_km(geom: list[GeoPoint], km: float) -> tuple[list[list[float]], list[list[float]]]:
    """Делит линию маршрута на пройденную и оставшуюся части по километру."""
    if not geom:
        return [], []
    passed = [[g.lat, g.lon] for g in geom if g.km <= km]
    rest = [[g.lat, g.lon] for g in geom if g.km >= km]
    for a, b in zip(geom, geom[1:]):
        if a.km < km < b.km:
            t = (km - a.km) / (b.km - a.km)
            cut = [a.lat + (b.lat - a.lat) * t, a.lon + (b.lon - a.lon) * t]
            passed.append(cut)
            rest.insert(0, cut)
            break
    return passed, rest


def point_for(code: str, geom: list[GeoPoint], coords: dict[str, tuple[float, float]]) -> tuple[float, float] | None:
    """Координаты станции: из справочника, иначе — с линии маршрута."""
    if code[:5] in coords:
        return coords[code[:5]]
    for g in geom:
        if g.code == code:
            return g.lat, g.lon
    return None
