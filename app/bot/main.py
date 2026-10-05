"""Telegram-бот Dislocation: накладные и отслеживание вагонов.

Запуск: python -m app.bot.main
"""
import asyncio
import logging
from datetime import datetime
from html import escape
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, Message, ReplyKeyboardMarkup, TelegramObject)

from app import config
from app.bot.parsing import parse_date, parse_datetime
from app.db.storage import Storage
from app.graph.network import Network, load_network
from app.graph.search import search_stations
from app.tr4.download import download_book1, latest_local_book1
from app.tracking import compute_status, format_status, now_msk, station_label

log = logging.getLogger("dislocation")
router = Router()

BTN_NEW = "➕ Новая накладная"
BTN_TRACK = "📍 Отследить"
BTN_LIST = "📋 Активные"
BTN_ARCHIVE = "🗄 Архив"
MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=BTN_NEW), KeyboardButton(text=BTN_TRACK)],
              [KeyboardButton(text=BTN_LIST), KeyboardButton(text=BTN_ARCHIVE)]],
    resize_keyboard=True)
ICON_CLOSE = "🗄"
ICON_RESTORE = "↩️"

network: Network
storage: Storage


class NewWaybill(StatesGroup):
    number = State()
    from_station = State()
    to_station = State()
    accepted = State()
    deadline = State()


class Track(StatesGroup):
    station = State()
    time = State()


class AccessMiddleware(BaseMiddleware):
    """Пускает только Telegram ID из белого списка."""
    async def __call__(self, handler: Callable[[TelegramObject, dict], Awaitable[Any]],
                       event: TelegramObject, data: dict) -> Any:
        user = data.get("event_from_user")
        if user is None or user.id not in config.ALLOWED_USER_IDS:
            log.warning("Отказ в доступе: %s", user.id if user else None)
            return None
        return await handler(event, data)


# ---------- общие куски ----------
def label(code: str) -> str:
    return escape(station_label(network, code))


def stations_kb(codes: list[str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{station_label(network, c)} · {c}", callback_data=f"st:{c}")]
        for c in codes])


async def ask_station(message: Message, prompt: str) -> None:
    await message.answer(prompt + "\nВведи часть названия или код ЕСР.")


async def offer_stations(message: Message) -> None:
    codes = search_stations(network, message.text or "")
    if not codes:
        await message.answer("Ничего не нашёл. Попробуй иначе: часть названия или код ЕСР.")
        return
    await message.answer("Выбери станцию:", reply_markup=stations_kb(codes))


# ---------- старт и отмена ----------
@router.message(CommandStart())
@router.message(Command("cancel"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отслеживание вагонов. Что делаем?", reply_markup=MAIN_KB)


# ---------- новая накладная ----------
@router.message(F.text == BTN_NEW)
async def new_waybill(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(NewWaybill.number)
    await message.answer("Номер накладной? (например ЭА000001)\nОтмена — /cancel")


@router.message(F.text.in_({BTN_TRACK, BTN_LIST}))
async def menu_track(message: Message, state: FSMContext) -> None:
    # Кнопки меню регистрируются раньше обработчиков состояний — работают из любого шага
    await choose_waybill(message, state)


@router.message(F.text == BTN_ARCHIVE)
async def menu_archive(message: Message, state: FSMContext) -> None:
    await state.clear()
    kb = archive_kb(message.from_user.id)
    if kb is None:
        await message.answer("Архив пуст.")
        return
    await message.answer(f"Архив накладных. {ICON_RESTORE} — вернуть в активные.", reply_markup=kb)


@router.message(NewWaybill.number, F.text)
async def nw_number(message: Message, state: FSMContext) -> None:
    await state.update_data(number=message.text.strip().upper())
    await state.set_state(NewWaybill.from_station)
    await ask_station(message, "Станция отправления?")


@router.message(NewWaybill.from_station, F.text)
@router.message(NewWaybill.to_station, F.text)
@router.message(Track.station, F.text)
async def station_query(message: Message) -> None:
    await offer_stations(message)


@router.callback_query(NewWaybill.from_station, F.data.startswith("st:"))
async def nw_from(cb: CallbackQuery, state: FSMContext) -> None:
    code = cb.data[3:]
    await state.update_data(from_code=code)
    await state.set_state(NewWaybill.to_station)
    await cb.message.edit_text(f"Отправление: {label(code)}")
    await ask_station(cb.message, "Станция назначения?")
    await cb.answer()


@router.callback_query(NewWaybill.to_station, F.data.startswith("st:"))
async def nw_to(cb: CallbackQuery, state: FSMContext) -> None:
    code = cb.data[3:]
    data = await state.get_data()
    try:
        route = network.route(data["from_code"], code)
    except Exception:  # noqa: BLE001 — показываем пользователю и даём выбрать другую
        log.exception("Нет маршрута")
        await cb.answer("Не смог построить маршрут до этой станции, выбери другую", show_alert=True)
        return
    await state.update_data(to_code=code, total_km=route.distance)
    await state.set_state(NewWaybill.accepted)
    await cb.message.edit_text(f"Назначение: {label(code)}\nРасстояние: {route.distance} км")
    await cb.message.answer("Дата и время приёма к перевозке (МСК)?\nНапример: 24.09.2026 17:43")
    await cb.answer()


@router.message(NewWaybill.accepted, F.text)
async def nw_accepted(message: Message, state: FSMContext) -> None:
    dt = parse_datetime(message.text, now_msk().date())
    if not dt:
        await message.answer("Не понял дату. Формат: 24.09.2026 17:43 или 24.09.2026")
        return
    await state.update_data(accepted_at=dt.isoformat())
    await state.set_state(NewWaybill.deadline)
    await message.answer("Срок доставки истекает (дата из накладной)?\nНапример: 25.10.2026")


@router.message(NewWaybill.deadline, F.text)
async def nw_deadline(message: Message, state: FSMContext) -> None:
    deadline = parse_date(message.text, now_msk().date())
    data = await state.get_data()
    accepted = datetime.fromisoformat(data["accepted_at"])
    if not deadline or deadline < accepted.date():
        await message.answer("Не понял дату или она раньше приёма. Формат: 25.10.2026")
        return
    wb = storage.add_waybill(message.from_user.id, data["number"], data["from_code"],
                             data["to_code"], accepted, deadline)
    await state.clear()
    await message.answer(
        f"✅ Накладная {escape(wb.number)} сохранена\n"
        f"{label(wb.from_code)} → {label(wb.to_code)}, {data['total_km']} км\n"
        f"Приём: {accepted:%d.%m.%Y %H:%M} МСК\n"
        f"Срок: до {deadline:%d.%m.%Y} ({(deadline - accepted.date()).days} сут)",
        reply_markup=MAIN_KB)


# ---------- список и отслеживание ----------
def waybill_title(w) -> str:
    return f"{w.number} · {network.names.get(w.from_code, w.from_code)} → {network.names.get(w.to_code, w.to_code)}"


def waybills_kb(owner_id: int) -> InlineKeyboardMarkup | None:
    """Активные: накладная (отследить) + иконка «в архив» справа."""
    rows = [[InlineKeyboardButton(text=waybill_title(w), callback_data=f"wb:{w.id}"),
             InlineKeyboardButton(text=ICON_CLOSE, callback_data=f"close:{w.id}")]
            for w in storage.active_waybills(owner_id)]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def archive_kb(owner_id: int) -> InlineKeyboardMarkup | None:
    """Архив: накладная + иконка «восстановить» справа."""
    rows = [[InlineKeyboardButton(text=waybill_title(w), callback_data=f"arch:{w.id}"),
             InlineKeyboardButton(text=ICON_RESTORE, callback_data=f"restore:{w.id}")]
            for w in storage.archived_waybills(owner_id)]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


ACTIVE_HINT = f"Какую накладную отслеживаем? {ICON_CLOSE} — убрать в архив."


async def choose_waybill(message: Message, state: FSMContext) -> None:
    await state.clear()
    kb = waybills_kb(message.from_user.id)
    if kb is None:
        await message.answer("Активных накладных нет. Создай: «➕ Новая накладная».")
        return
    await message.answer(ACTIVE_HINT, reply_markup=kb)


@router.callback_query(F.data.startswith("wb:"))
async def track_start(cb: CallbackQuery, state: FSMContext) -> None:
    wb = storage.get_waybill(int(cb.data[3:]))
    if not wb or wb.owner_id != cb.from_user.id:
        await cb.answer("Накладная не найдена", show_alert=True)
        return
    await state.set_state(Track.station)
    await state.update_data(waybill_id=wb.id)
    history = storage.dislocations(wb.id)
    last = f"\nПоследняя дислокация: {label(history[-1].station_code)}, " \
           f"{history[-1].operated_at:%d.%m %H:%M}" if history else ""
    await cb.message.edit_text(f"Накладная {escape(wb.number)}{last}")
    await ask_station(cb.message, "Текущая станция операции?")
    await cb.answer()


@router.callback_query(Track.station, F.data.startswith("st:"))
async def track_station(cb: CallbackQuery, state: FSMContext) -> None:
    code = cb.data[3:]
    await state.update_data(station_code=code)
    await state.set_state(Track.time)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Сейчас", callback_data="now")]])
    await cb.message.edit_text(f"Станция: {label(code)}")
    await cb.message.answer("Время операции (МСК)? Нажми «Сейчас» или введи: 03.10 14:20",
                            reply_markup=kb)
    await cb.answer()


async def finish_tracking(message: Message, state: FSMContext, operated_at: datetime) -> None:
    data = await state.get_data()
    wb = storage.get_waybill(data["waybill_id"])
    if operated_at < wb.accepted_at:
        await message.answer("Время операции раньше приёма к перевозке. Введи ещё раз.")
        return
    storage.add_dislocation(wb.id, data["station_code"], operated_at)
    await state.clear()
    st = compute_status(network, wb.from_code, wb.to_code, wb.accepted_at, wb.deadline,
                        data["station_code"], operated_at)
    text = format_status(network, wb.number, wb.from_code, wb.to_code, data["station_code"], operated_at, st)
    await message.answer(text, reply_markup=MAIN_KB)


@router.callback_query(Track.time, F.data == "now")
async def track_now(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.message.edit_reply_markup(reply_markup=None)
    await finish_tracking(cb.message, state, now_msk())
    await cb.answer()


@router.message(Track.time, F.text)
async def track_time(message: Message, state: FSMContext) -> None:
    dt = parse_datetime(message.text, now_msk().date())
    if not dt:
        await message.answer("Не понял время. Формат: 03.10 14:20 или 03.10.2026 14:20")
        return
    await finish_tracking(message, state, dt)


def own_waybill(cb: CallbackQuery):
    wb = storage.get_waybill(int(cb.data.split(":", 1)[1]))
    return wb if wb and wb.owner_id == cb.from_user.id else None


@router.callback_query(F.data.startswith("close:"))
async def close_waybill(cb: CallbackQuery, state: FSMContext) -> None:
    wb = own_waybill(cb)
    if not wb:
        await cb.answer("Накладная не найдена", show_alert=True)
        return
    await state.clear()
    storage.close_waybill(wb.id)
    kb = waybills_kb(cb.from_user.id)
    if kb is None:
        await cb.message.edit_text("Активных накладных нет. Создай: «➕ Новая накладная».")
    else:
        await cb.message.edit_text(ACTIVE_HINT, reply_markup=kb)
    await cb.answer(f"{wb.number} — в архиве")


@router.callback_query(F.data.startswith("restore:"))
async def restore_waybill(cb: CallbackQuery) -> None:
    wb = own_waybill(cb)
    if not wb:
        await cb.answer("Накладная не найдена", show_alert=True)
        return
    storage.restore_waybill(wb.id)
    kb = archive_kb(cb.from_user.id)
    if kb is None:
        await cb.message.edit_text("Архив пуст.")
    else:
        await cb.message.edit_reply_markup(reply_markup=kb)
    await cb.answer(f"{wb.number} — снова в активных")


@router.callback_query(F.data.startswith("arch:"))
async def archive_info(cb: CallbackQuery) -> None:
    wb = own_waybill(cb)
    if not wb:
        await cb.answer("Накладная не найдена", show_alert=True)
        return
    history = storage.dislocations(wb.id)
    last = (f"\nПоследняя дислокация: {station_label(network, history[-1].station_code)}, "
            f"{history[-1].operated_at:%d.%m %H:%M}") if history else ""
    text = f"{waybill_title(wb)}\nПриём: {wb.accepted_at:%d.%m.%Y}, срок до {wb.deadline:%d.%m.%Y}{last}"
    await cb.answer(text[:200], show_alert=True)   # лимит Telegram на всплывающее окно


# ---------- запуск ----------
def load_graph() -> Network:
    book1 = latest_local_book1() or download_book1()
    log.info("Граф из %s", book1.name)
    return load_network(book1)


async def main() -> None:
    global network, storage
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.BOT_TOKEN or not config.ALLOWED_USER_IDS:
        raise SystemExit("Заполни BOT_TOKEN и ALLOWED_USER_IDS в .env")
    network = load_graph()
    storage = Storage(config.SQLITE_PATH)
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()
    dp.message.outer_middleware(AccessMiddleware())
    dp.callback_query.outer_middleware(AccessMiddleware())
    dp.include_router(router)
    log.info("Бот запущен, станций в графе: %d", len(network.names))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
