"""Проверка подписи Telegram Mini App (initData).

https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_AGE_SECONDS = 24 * 3600


def validate_init_data(init_data: str, bot_token: str, max_age: int = MAX_AGE_SECONDS,
                       now: float | None = None) -> dict | None:
    """Возвращает пользователя Telegram, если подпись верна и данные свежие, иначе None."""
    if not init_data or not bot_token:
        return None
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", None)
    if not received:
        return None
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        auth_date = int(pairs.get("auth_date", "0"))
        user = json.loads(pairs.get("user", "null"))
    except (ValueError, json.JSONDecodeError):
        return None
    if (now or time.time()) - auth_date > max_age or not isinstance(user, dict):
        return None
    return user
