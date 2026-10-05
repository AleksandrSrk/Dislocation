"""Граф ж/д сети РФ по ТР4 кн. 1 и расчёт тарифного пути.

Правила (см. SPEC.md, раздел 5):
- транзит только по основным тарифным участкам;
- участки других типов — только первое/последнее плечо;
- конец участка, не перечисленный в таблице (стык дорог), берётся из шапки;
- одноимённые станции разрешаются по близости кода ЕСР.
"""
import heapq
import pickle
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from app.tr4.parse import Section, parse_book1

TRANSIT_KINDS = {"Основной тарифный участок"}
ROAD_SUFFIX_RE = re.compile(r"\s*\([^()]*ж\.д\.\)\s*$")


@dataclass
class RoutePoint:
    code: str
    name: str
    km: int          # км от начала маршрута


@dataclass
class Route:
    distance: int
    points: list[RoutePoint]


class StationNotFound(KeyError):
    pass


CACHE_VERSION = 3
MAX_END_GAP_KM = 50       # см. достройку концов участков ниже


class Network:
    def __init__(self, sections: list[Section]):
        self.names: dict[str, str] = {}
        self.roads: dict[str, str] = {}      # код → дорога («Сверд», «Моск», …)
        for s in sections:
            road = s.road.replace("(Р)", "").strip()
            for code, name, _, _ in s.rows:
                self.names.setdefault(code, name)
                self.roads.setdefault(code, road)
        by_name: dict[str, list[str]] = defaultdict(list)
        for code, name in self.names.items():
            by_name[name].append(code)

        def resolve(name: str | None, near: str) -> str | None:
            if not name:
                return None
            cands = by_name.get(ROAD_SUFFIX_RE.sub("", name).strip()) or by_name.get(name)
            if not cands:
                return None
            return min(cands, key=lambda c: abs(int(c) - int(near)))

        # Точки участков: [(код, км от конца A)], отсортированные по км
        self.sections: list[tuple[str, list[tuple[str, int]]]] = []
        for s in sections:
            if not s.rows:
                continue
            pts = [(code, ka) for code, _, ka, _ in s.rows]
            codes = {c for c, _ in pts}
            # Конец из шапки достраиваем, только если он рядом с крайней перечисленной станцией.
            # Иначе шапка указывает не на конец участка, а на дальний ориентир
            # (пример: «Суховская — Мегет», вторая колонка «до ст. Тайшет» за 623 км).
            a = resolve(s.end_a, s.rows[0][0])
            if a and a not in codes and s.rows[0][2] <= MAX_END_GAP_KM:
                pts.append((a, 0))
            b = resolve(s.end_b, s.rows[-1][0])
            gap_b = s.rows[-1][3]
            if b and b not in codes and s.length is not None and gap_b is not None and gap_b <= MAX_END_GAP_KM:
                pts.append((b, s.length))
            pts.sort(key=lambda p: p[1])
            self.sections.append((s.kind, pts))

        self.adj: dict[str, dict[str, int]] = defaultdict(dict)
        self.station_sections: dict[str, list[int]] = defaultdict(list)
        for i, (kind, pts) in enumerate(self.sections):
            for code, _ in pts:
                self.station_sections[code].append(i)
            if kind not in TRANSIT_KINDS:
                continue
            for (c1, k1), (c2, k2) in zip(pts, pts[1:]):
                d = k2 - k1
                if c1 != c2 and self.adj[c1].get(c2, 10**9) > d:
                    self.adj[c1][c2] = d
                    self.adj[c2][c1] = d
        self.adj = dict(self.adj)
        self._by_prefix: dict[str, list[str]] = defaultdict(list)
        for code in self.names:
            self._by_prefix[code[:5]].append(code)

    # ---------- поиск станции ----------
    def normalize(self, code: str) -> str:
        """Принимает код ЕСР из 5 или 6 цифр, возвращает 6-значный код из графа."""
        code = re.sub(r"\D", "", str(code))
        if code in self.names:
            return code
        cands = self._by_prefix.get(code[:5], [])
        if len(code) in (5, 6) and cands:
            return cands[0]
        raise StationNotFound(code)

    # ---------- плечи до транзитного графа ----------
    def _legs(self, code: str) -> dict[str, tuple[int, list[tuple[str, int]]]]:
        """Точки входа в транзитный граф: {узел: (км, [(код, км от станции), …])}."""
        if code in self.adj:
            return {code: (0, [(code, 0)])}
        out: dict[str, tuple[int, list[tuple[str, int]]]] = {}
        for i in self.station_sections[code]:
            pts = self.sections[i][1]
            idx = next(j for j, (c, _) in enumerate(pts) if c == code)
            k0 = pts[idx][1]
            for step in (1, -1):
                j = idx
                while 0 <= j < len(pts):
                    c, k = pts[j]
                    if c in self.adj:
                        d = abs(k - k0)
                        if d < out.get(c, (10**9,))[0]:
                            seg = pts[idx:j + 1] if step == 1 else pts[j:idx + 1][::-1]
                            out[c] = (d, [(cc, abs(kk - k0)) for cc, kk in seg])
                        break
                    j += step
        return out

    def _direct(self, a: str, z: str) -> tuple[int, list[tuple[str, int]]] | None:
        """Путь внутри одного участка, если обе станции на нём."""
        best = None
        for i in set(self.station_sections[a]) & set(self.station_sections[z]):
            pts = self.sections[i][1]
            ia = next(j for j, (c, _) in enumerate(pts) if c == a)
            iz = next(j for j, (c, _) in enumerate(pts) if c == z)
            seg = pts[ia:iz + 1] if ia <= iz else pts[iz:ia + 1][::-1]
            k0 = pts[ia][1]
            d = abs(pts[iz][1] - k0)
            if best is None or d < best[0]:
                best = (d, [(c, abs(k - k0)) for c, k in seg])
        return best

    # ---------- маршрут ----------
    def route(self, from_code: str, to_code: str) -> Route:
        a, z = self.normalize(from_code), self.normalize(to_code)
        if a == z:
            return Route(0, [RoutePoint(a, self.names[a], 0)])
        legs_a, legs_z = self._legs(a), self._legs(z)

        dist = {u: d for u, (d, _) in legs_a.items()}
        prev: dict[str, str] = {}
        queue = [(d, u) for u, d in dist.items()]
        heapq.heapify(queue)
        while queue:
            d, u = heapq.heappop(queue)
            if d > dist[u]:
                continue
            for v, w in self.adj[u].items():
                if d + w < dist.get(v, float("inf")):
                    dist[v] = d + w
                    prev[v] = u
                    heapq.heappush(queue, (d + w, v))

        best = None
        for exit_node, (dz, _) in legs_z.items():
            if exit_node in dist and (best is None or dist[exit_node] + dz < best[0]):
                best = (dist[exit_node] + dz, exit_node)
        direct = self._direct(a, z)
        if direct and (best is None or direct[0] <= best[0]):
            return self._make_route(direct[1])
        if best is None:
            raise ValueError(f"Нет пути {a} → {z}")

        total, exit_node = best
        transit = [exit_node]
        while transit[-1] in prev:
            transit.append(prev[transit[-1]])
        transit.reverse()
        entry = transit[0]

        pts: list[tuple[str, int]] = list(legs_a[entry][1])          # a → entry
        km = pts[-1][1]
        for u, v in zip(transit, transit[1:]):                       # entry → exit
            km += self.adj[u][v]
            pts.append((v, km))
        dz, leg = legs_z[exit_node]                                    # exit → z
        for c, off in reversed(leg[:-1]):
            pts.append((c, km + dz - off))
        return self._make_route(pts)

    def _make_route(self, pts: list[tuple[str, int]]) -> Route:
        points = [RoutePoint(c, self.names.get(c, c), k) for c, k in pts]
        return Route(points[-1].km, points)


def load_network(book1: Path, cache_dir: Path | None = None) -> Network:
    """Строит граф из книги 1, кэшируя результат рядом с файлом."""
    cache = (cache_dir or book1.parent) / f"{book1.stem}.network.v{CACHE_VERSION}.pkl"
    if cache.exists() and cache.stat().st_mtime >= book1.stat().st_mtime:
        with cache.open("rb") as f:
            return pickle.load(f)
    net = Network(parse_book1(book1))
    with cache.open("wb") as f:
        pickle.dump(net, f)
    return net
