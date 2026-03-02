from pathlib import Path

COOKIE_FILE_PATH = Path(__file__).parent / "cookies.txt"


def has_cookies() -> bool:
    return COOKIE_FILE_PATH.is_file()


def save_cookies(content: str) -> None:
    COOKIE_FILE_PATH.write_text(content, encoding="utf-8")


def delete_cookies() -> None:
    if COOKIE_FILE_PATH.is_file():
        COOKIE_FILE_PATH.unlink()
