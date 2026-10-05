"""API и страница карты для Telegram Mini App.

Запуск: uvicorn app.api.main:app --host 0.0.0.0 --port 8000
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse

from app import config
from app.api.auth import validate_init_data
from app.db.storage import Storage
from app.geo import load_coords, point_for, route_geometry, split_at_km
from app.graph.network import load_network
from app.tr4.download import download_book1, latest_local_book1
from app.tracking import compute_status

STATIC = Path(__file__).resolve().parent / "static"
state: dict = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    book1 = latest_local_book1() or download_book1()
    state["network"] = load_network(book1)
    state["coords"] = load_coords()
    state["storage"] = Storage(config.SQLITE_PATH)
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def no_index(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    return "User-agent: *\nDisallow: /\n"


@app.get("/map")
def map_page() -> FileResponse:
    return FileResponse(STATIC / "map.html")


def current_user(init_data: str | None) -> int:
    user = validate_init_data(init_data or "", config.BOT_TOKEN)
    if not user or user.get("id") not in config.ALLOWED_USER_IDS:
        raise HTTPException(status_code=403, detail="Нет доступа")
    return user["id"]


@app.get("/api/waybill/{waybill_id}")
def waybill_map(waybill_id: int, x_init_data: str | None = Header(default=None)) -> dict:
    user_id = current_user(x_init_data)
    network, coords, storage = state["network"], state["coords"], state["storage"]
    wb = storage.get_waybill(waybill_id)
    if not wb or wb.owner_id != user_id:
        raise HTTPException(status_code=404, detail="Накладная не найдена")

    route = network.route(wb.from_code, wb.to_code)
    geom = route_geometry(route, coords)
    history = storage.dislocations(wb.id)

    def marker(code: str, **extra) -> dict | None:
        p = point_for(code, geom, coords)
        return {"lat": p[0], "lon": p[1], "name": network.names.get(code, code), **extra} if p else None

    status = None
    passed_line: list = []
    rest_line = [[g.lat, g.lon] for g in geom]
    if history:
        last = history[-1]
        st = compute_status(network, wb.from_code, wb.to_code, wb.accepted_at, wb.deadline,
                            last.station_code, last.operated_at)
        passed_line, rest_line = split_at_km(geom, st.passed_km)
        status = {
            "station": network.names.get(last.station_code, last.station_code),
            "operated_at": last.operated_at.strftime("%d.%m %H:%M"),
            "passed_km": st.passed_km, "remaining_km": st.remaining_km,
            "eta": st.eta.strftime("%d.%m.%Y") if st.eta else None,
            "margin_days": round(st.margin_days, 1) if st.margin_days is not None else None,
        }

    return {
        "number": wb.number,
        "from": network.names.get(wb.from_code, wb.from_code),
        "to": network.names.get(wb.to_code, wb.to_code),
        "total_km": route.distance,
        "deadline": wb.deadline.strftime("%d.%m.%Y"),
        "status": status,
        "passed": passed_line,
        "rest": rest_line,
        "start": marker(wb.from_code),
        "end": marker(wb.to_code),
        "current": marker(history[-1].station_code) if history else None,
        "history": [m for d in history
                    if (m := marker(d.station_code, time=d.operated_at.strftime("%d.%m %H:%M")))],
    }
