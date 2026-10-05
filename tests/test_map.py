"""Карта: подпись Telegram, геометрия маршрута, API."""
import hashlib
import hmac
import json
import time
from datetime import date, datetime
from urllib.parse import urlencode

import pytest

from app.api.auth import validate_init_data
from app.geo import GeoPoint, route_geometry, split_at_km
from app.graph.network import Route, RoutePoint

TOKEN = "123456:TEST"


def make_init_data(user_id: int, token: str = TOKEN, auth_date: int | None = None) -> str:
    data = {"auth_date": str(auth_date or int(time.time())), "query_id": "q",
            "user": json.dumps({"id": user_id, "first_name": "A"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


# ---------- подпись ----------
def test_valid_init_data():
    assert validate_init_data(make_init_data(42), TOKEN)["id"] == 42


def test_wrong_token_rejected():
    assert validate_init_data(make_init_data(42, token="999:OTHER"), TOKEN) is None


def test_tampered_user_rejected():
    forged = make_init_data(42).replace("%22id%22%3A+42", "%22id%22%3A+1")
    assert validate_init_data(forged, TOKEN) is None


def test_expired_init_data_rejected():
    old = make_init_data(42, auth_date=int(time.time()) - 3 * 86400)
    assert validate_init_data(old, TOKEN) is None


def test_empty_init_data_rejected():
    assert validate_init_data("", TOKEN) is None


# ---------- геометрия ----------
def route_of(kms: list[int]) -> Route:
    return Route(kms[-1], [RoutePoint(f"{i:05d}0", f"S{i}", k) for i, k in enumerate(kms)])


def test_interpolation_between_known_points():
    route = route_of([0, 50, 100])
    coords = {"00000": (56.0, 60.0), "00002": (56.0, 62.0)}          # средняя без координат
    geom = route_geometry(route, coords)
    assert [g.exact for g in geom] == [True, False, True]
    assert geom[1].lon == pytest.approx(61.0)


def test_outlier_coordinate_dropped():
    route = route_of([0, 10, 20])
    coords = {"00000": (56.0, 60.0), "00001": (43.0, 131.0), "00002": (56.0, 60.3)}  # Владивосток посреди Урала
    geom = route_geometry(route, coords)
    assert geom[1].exact is False and abs(geom[1].lon - 60.15) < 0.01


def test_split_at_km():
    geom = [GeoPoint("a", "A", 0, 0.0, 0.0, True), GeoPoint("b", "B", 100, 0.0, 10.0, True)]
    passed, rest = split_at_km(geom, 25)
    assert passed[-1] == pytest.approx([0.0, 2.5]) and rest[0] == pytest.approx([0.0, 2.5])


def test_real_route_geometry_has_no_jumps():
    """На реальном маршруте соседние точки линии не дальше 300 км друг от друга."""
    from app.geo import haversine_km, load_coords
    from app.graph.network import load_network
    from app.tr4.download import latest_local_book1

    book1 = latest_local_book1()
    if book1 is None:
        pytest.skip("Нет книги 1 ТР4 в data/")
    geom = route_geometry(load_network(book1).route("190205", "946002"), load_coords())
    jumps = [haversine_km((a.lat, a.lon), (b.lat, b.lon)) for a, b in zip(geom, geom[1:])]
    assert max(jumps) < 300
    # у платформ «ОП» и постов координат обычно нет — считаем только станции
    stations = [g for g in geom if not g.name.startswith(("ОП ", "Пут. пост", "Блок-пост"))]
    assert sum(g.exact for g in stations) / len(stations) > 0.6


# ---------- API ----------
@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config
    from app.api import main as api
    from app.db.storage import Storage
    from app.tr4.download import latest_local_book1

    if latest_local_book1() is None:
        pytest.skip("Нет книги 1 ТР4 в data/")
    monkeypatch.setattr(config, "BOT_TOKEN", TOKEN)
    monkeypatch.setattr(config, "ALLOWED_USER_IDS", {42})
    monkeypatch.setattr(config, "SQLITE_PATH", tmp_path / "t.sqlite3")
    with TestClient(api.app) as c:
        storage: Storage = api.state["storage"]
        wb = storage.add_waybill(42, "ЭА000001", "190205", "946002", datetime(2026, 9, 24, 17, 43), date(2026, 10, 25))
        storage.add_dislocation(wb.id, "920002", datetime(2026, 10, 3, 14, 20))
        c.wb_id = wb.id
        yield c


def test_api_requires_signature(client):
    assert client.get(f"/api/waybill/{client.wb_id}").status_code == 403


def test_api_rejects_foreign_user(client):
    r = client.get(f"/api/waybill/{client.wb_id}", headers={"X-Init-Data": make_init_data(7)})
    assert r.status_code == 403


def test_api_returns_route(client):
    r = client.get(f"/api/waybill/{client.wb_id}", headers={"X-Init-Data": make_init_data(42)})
    assert r.status_code == 200
    d = r.json()
    assert d["total_km"] > 6000 and d["status"]["remaining_km"] > 0
    assert len(d["passed"]) > 50 and len(d["rest"]) > 50
    assert d["current"]["name"] == "Тайшет"
    assert r.headers["X-Robots-Tag"].startswith("noindex")


def test_robots_txt(client):
    assert "Disallow: /" in client.get("/robots.txt").text
