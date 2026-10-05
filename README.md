# Dislocation

Telegram-бот @disloc_bot для отслеживания вагонов в пути по накладной: расстояние,
остаток, срок доставки, опережение/отставание. ТЗ — в [SPEC.md](SPEC.md).

## Локально (Windows)

```cmd
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env          & rem заполнить BOT_TOKEN и ALLOWED_USER_IDS
.venv\Scripts\python -m app.tr4.download
.venv\Scripts\python -m app.bot.main
```

Тесты: `.venv\Scripts\python -m pytest`

## Сервер

Каталог `/opt/apps/dislocation`, всё в Docker Compose.

```bash
cd /opt/apps/dislocation
git pull
docker compose up -d --build
docker compose logs -f bot
```

`.env` на сервере создаётся вручную и в git не хранится. Данные (ТР4, кэш графа,
SQLite) — в `./data`, переживают пересборку контейнера.
