"""Эталонные накладные: расстояние по графу против ЭТРАН (SPEC.md, раздел 8)."""
import pytest

from app.graph.network import Network, load_network
from app.tr4.download import latest_local_book1

# (накладная, отправление, назначение, км по ЭТРАН, допуск %)
REFERENCE = [
    ("ЭА000001", "190205", "946002", 6688, 2),
    ("ЭА000002", "774000", "524000", 2938, 2),
    ("ЭА000003", "796103", "524000", 3229, 5),   # известное расхождение, см. SPEC
    ("ЭА000004", "812005", "814301", 329, 2),
]


@pytest.fixture(scope="session")
def network() -> Network:
    book1 = latest_local_book1()
    if book1 is None:
        pytest.skip("Нет книги 1 ТР4 в data/. Запусти: python -m app.tr4.download")
    return load_network(book1)


@pytest.mark.parametrize("waybill,src,dst,etran_km,tolerance", REFERENCE)
def test_reference_distance(network, waybill, src, dst, etran_km, tolerance):
    route = network.route(src, dst)
    error = abs(route.distance - etran_km) / etran_km * 100
    assert error <= tolerance, f"{waybill}: граф {route.distance} км, ЭТРАН {etran_km} км ({error:.1f}%)"


@pytest.mark.parametrize("waybill,src,dst,etran_km,tolerance", REFERENCE)
def test_route_points_consistent(network, waybill, src, dst, etran_km, tolerance):
    route = network.route(src, dst)
    assert route.points[0].code == network.normalize(src)
    assert route.points[-1].code == network.normalize(dst)
    assert route.points[0].km == 0
    kms = [p.km for p in route.points]
    assert kms == sorted(kms), "км вдоль маршрута должны только расти"


def test_remaining_distance(network):
    """Остаток от промежуточной станции + пройденное = весь путь."""
    full = network.route("190205", "946002")
    mid = full.points[len(full.points) // 2]
    remaining = network.route(mid.code, "946002")
    assert abs(mid.km + remaining.distance - full.distance) <= full.distance * 0.01


def test_five_digit_code(network):
    assert network.normalize("19020") == network.normalize("190205")
