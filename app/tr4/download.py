"""Скачивание актуальной книги 1 Тарифного руководства №4 с sovetgt.org."""
import re
import urllib.request
from pathlib import Path

INDEX_URL = "https://www.sovetgt.org/index.php?link=65"
BOOK1_RE = re.compile(r"https?://(?:www\.)?sovetgt\.org/tr4/\d{4}/\d{2}/\d{2}/Kniga_1_[\d-]+\.xls")
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def find_book1_url() -> str:
    """Находит ссылку на свежую книгу 1 на странице ТР4."""
    with urllib.request.urlopen(INDEX_URL, timeout=60) as resp:
        html = resp.read().decode("cp1251", errors="replace")
    match = BOOK1_RE.search(html)
    if not match:
        raise RuntimeError("Не нашёл ссылку на книгу 1 ТР4 на странице " + INDEX_URL)
    return match.group(0)


def latest_local_book1(data_dir: Path = DATA_DIR) -> Path | None:
    """Самый свежий уже скачанный файл книги 1 (по дате в имени)."""
    files = sorted(data_dir.glob("Kniga_1_*.xls"))
    return files[-1] if files else None


def download_book1(data_dir: Path = DATA_DIR, force: bool = False) -> Path:
    """Скачивает книгу 1, если такой версии ещё нет локально. Возвращает путь к файлу."""
    data_dir.mkdir(parents=True, exist_ok=True)
    url = find_book1_url()
    target = data_dir / url.rsplit("/", 1)[1]
    if target.exists() and not force:
        return target
    tmp = target.with_suffix(".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.replace(target)
    return target


if __name__ == "__main__":
    print(download_book1())
