"""Поиск станции по куску названия или коду ЕСР."""
import re

from app.graph.network import Network

# Платформы и посты почти никогда не бывают станциями отправления/назначения
MINOR_PREFIXES = ("оп ", "пут. пост", "блок-пост", "пост ")


def normalize_name(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def search_stations(network: Network, query: str, limit: int = 5) -> list[str]:
    """Возвращает до `limit` кодов станций, лучшие совпадения первыми."""
    query = query.strip()
    digits = re.sub(r"\D", "", query)
    if digits and len(digits) >= 4 and digits == query.replace(" ", ""):
        hits = sorted(c for c in network.names if c.startswith(digits[:6]))
        return hits[:limit]

    q = normalize_name(query)
    if not q:
        return []
    scored = []
    for code, name in network.names.items():
        n = normalize_name(name)
        if n == q:
            score = 0
        elif n.startswith(q):
            score = 1
        elif any(w.startswith(q) for w in n.split()):
            score = 2
        elif q in n:
            score = 3
        else:
            continue
        minor = n.startswith(MINOR_PREFIXES) or name.lower().startswith(MINOR_PREFIXES)
        scored.append((score, minor, len(n), n, code))
    scored.sort()
    return [code for *_, code in scored[:limit]]
