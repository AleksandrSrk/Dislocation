"""Поиск станций, расчёт статуса, хранилище, разбор дат."""
from datetime import date, datetime

import pytest

from app.bot.parsing import parse_date, parse_datetime
from app.db.storage import Storage
from app.graph.network import load_network
from app.graph.search import search_stations
from app.tr4.download import latest_local_book1
from app.tracking import compute_status, format_status


@pytest.fixture(scope="module")
def network():
    book1 = latest_local_book1()
    if book1 is None:
        pytest.skip("Нет книги 1 ТР4 в data/")
    return load_network(book1)


def test_search_by_name(network):
    codes = search_stations(network, "досатуй")
    assert codes and codes[0] == "946002"


def test_search_by_code(network):
    assert search_stations(network, "190205")[0] == "190205"


def test_search_platforms_go_last(network):
    names = [network.names[c] for c in search_stations(network, "екатеринбург", limit=5)]
    assert not names[0].startswith("ОП")


def test_status_on_the_way(network):
    accepted = datetime(2026, 9, 24, 17, 43)
    full = network.route("190205", "946002")
    mid = full.points[len(full.points) // 2]
    st = compute_status(network, "190205", "946002", accepted, date(2026, 10, 25),
                        mid.code, datetime(2026, 10, 1, 12, 0))
    assert st.passed_km + st.remaining_km == st.total_km
    assert st.speed_km_day and st.speed_km_day > 0
    assert st.eta and st.margin_days is not None
    text = format_status(network, "ЭА000001", "190205", "946002", mid.code, datetime(2026, 10, 1, 12, 0), st)
    assert "ЭА000001" in text and "Осталось" in text


def test_status_at_departure_has_no_forecast(network):
    accepted = datetime(2026, 9, 24, 17, 43)
    st = compute_status(network, "190205", "946002", accepted, date(2026, 10, 25), "190205", accepted)
    assert st.passed_km == 0 and st.eta is None


def test_status_arrived(network):
    accepted = datetime(2026, 9, 24, 17, 43)
    st = compute_status(network, "190205", "946002", accepted, date(2026, 10, 25),
                        "946002", datetime(2026, 10, 10, 8, 0))
    assert st.arrived and st.remaining_km == 0 and st.margin_days > 0


def test_storage_roundtrip(tmp_path):
    s = Storage(tmp_path / "t.sqlite3")
    wb = s.add_waybill(1, "ЭА000001", "190205", "946002", datetime(2026, 9, 24, 17, 43), date(2026, 10, 25))
    assert [w.id for w in s.active_waybills(1)] == [wb.id]
    s.add_dislocation(wb.id, "920002", datetime(2026, 10, 3, 14, 20))
    assert s.dislocations(wb.id)[0].station_code == "920002"
    s.close_waybill(wb.id)
    assert s.active_waybills(1) == []
    assert [w.id for w in s.archived_waybills(1)] == [wb.id]
    s.restore_waybill(wb.id)
    assert [w.id for w in s.active_waybills(1)] == [wb.id]
    assert s.archived_waybills(1) == []
    assert len(s.dislocations(wb.id)) == 1, "история дислокаций не должна теряться"


@pytest.mark.parametrize("text,expected", [
    ("24.09.2026 17:43", datetime(2026, 9, 24, 17, 43)),
    ("24.09.2026", datetime(2026, 9, 24)),
    ("03.10 14:20", datetime(2026, 10, 3, 14, 20)),
    ("3/10/26", datetime(2026, 10, 3)),
])
def test_parse_datetime(text, expected):
    assert parse_datetime(text, date(2026, 10, 5)) == expected


def test_parse_bad_date():
    assert parse_date("31.02.2026", date(2026, 10, 5)) is None
    assert parse_date("завтра", date(2026, 10, 5)) is None
