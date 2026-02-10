#!/usr/bin/env python3
"""
tg_sender.py — отправка текста из .txt файла в Telegram-чат через бота.

Зависимости:
    pip install requests python-dotenv

Использование:
    python tg_sender.py message.txt
    python tg_sender.py report.txt --parse-mode HTML
    python tg_sender.py notes.txt --preview
"""

import os
import sys
import argparse
import logging
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# ──────────────────────────────────────────────
# Настройки
# ──────────────────────────────────────────────
TELEGRAM_MAX_LENGTH = 4096  # максимум символов в одном сообщении
API_URL = "https://api.telegram.org/bot{token}/{method}"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 1. Конфигурация
# ──────────────────────────────────────────────
class Config:
    """Загружает и валидирует конфигурацию."""

    def __init__(self) -> None:
        # Ищем .env в директории скрипта
        env_path = Path(__file__).parent / ".env"
        load_dotenv(env_path)

        self.bot_token: str = os.getenv("TG_BOT_TOKEN", "")
        self.chat_id: str = os.getenv("TG_CHAT_ID", "")

    def validate(self) -> None:
        errors: list[str] = []
        if not self.bot_token:
            errors.append("TG_BOT_TOKEN — не задан")
        if not self.chat_id:
            errors.append("TG_CHAT_ID — не задан")

        if errors:
            log.error("Ошибки конфигурации:")
            for e in errors:
                log.error(f"  • {e}")
            log.error("")
            log.error("Создайте файл .env рядом со скриптом:")
            log.error('  TG_BOT_TOKEN=123456:ABC-DEF...')
            log.error('  TG_CHAT_ID=123456789')
            sys.exit(1)


# ──────────────────────────────────────────────
# 2. Чтение файла
# ──────────────────────────────────────────────
def read_file(filepath: str) -> str:
    """Читает текстовый файл и возвращает его содержимое."""
    path = Path(filepath)

    if not path.exists():
        log.error(f"Файл не найден: {path}")
        sys.exit(1)

    if not path.is_file():
        log.error(f"Указанный путь — не файл: {path}")
        sys.exit(1)

    if path.stat().st_size == 0:
        log.error(f"Файл пуст: {path}")
        sys.exit(1)

    text = path.read_text(encoding="utf-8")
    log.info(f"Прочитано {len(text)} символов из {path.name}")
    return text


# ──────────────────────────────────────────────
# 3. Разбивка на части
# ──────────────────────────────────────────────
def split_text(text: str, max_length: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    """
    Разбивает текст на части, не превышающие max_length.
    Старается разрезать по последнему переносу строки.
    """
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        # Ищем последний перенос строки в пределах лимита
        cut = text.rfind("\n", 0, max_length)
        if cut == -1:
            # Нет переноса — ищем последний пробел
            cut = text.rfind(" ", 0, max_length)
        if cut == -1:
            # Нет и пробела — режем жёстко
            cut = max_length

        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")

    log.info(f"Текст разбит на {len(chunks)} частей")
    return chunks


# ──────────────────────────────────────────────
# 4. Отправка в Telegram
# ──────────────────────────────────────────────
class TelegramSender:
    """Обёртка над Telegram Bot API."""

    def __init__(self, config: Config) -> None:
        self.token = config.bot_token
        self.chat_id = config.chat_id
        self.session = requests.Session()

    def _api_url(self, method: str) -> str:
        return API_URL.format(token=self.token, method=method)

    def send_message(
        self,
        text: str,
        parse_mode: Optional[str] = None,
        disable_preview: bool = True,
    ) -> dict:
        """Отправляет одно сообщение. Возвращает ответ API."""
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": disable_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        resp = self.session.post(
            self._api_url("sendMessage"),
            json=payload,
            timeout=15,
        )

        data = resp.json()

        if not data.get("ok"):
            error_desc = data.get("description", "Неизвестная ошибка")
            error_code = data.get("error_code", "?")
            log.error(f"Telegram API ошибка [{error_code}]: {error_desc}")
            return data

        message_id = data["result"]["message_id"]
        log.info(f"✔ Сообщение отправлено (message_id: {message_id})")
        return data

    def send_long_text(
        self,
        text: str,
        parse_mode: Optional[str] = None,
    ) -> list[dict]:
        """Разбивает длинный текст и отправляет по частям."""
        chunks = split_text(text)
        results: list[dict] = []

        for i, chunk in enumerate(chunks, 1):
            if len(chunks) > 1:
                header = f"📄 Часть {i}/{len(chunks)}\n\n"
                chunk = header + chunk

            log.info(f"Отправка части {i}/{len(chunks)} ({len(chunk)} символов)...")
            result = self.send_message(chunk, parse_mode=parse_mode)
            results.append(result)

            if not result.get("ok"):
                log.error("Остановка отправки из-за ошибки")
                break

        return results

    def check_bot(self) -> bool:
        """Проверяет валидность токена через getMe."""
        try:
            resp = self.session.get(self._api_url("getMe"), timeout=10)
            data = resp.json()
            if data.get("ok"):
                bot_name = data["result"].get("username", "?")
                log.info(f"Бот подключён: @{bot_name}")
                return True
            else:
                log.error(f"Невалидный токен: {data.get('description')}")
                return False
        except requests.RequestException as e:
            log.error(f"Ошибка сети: {e}")
            return False


# ──────────────────────────────────────────────
# 5. CLI
# ──────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Отправка текста из .txt файла в Telegram-чат",
        epilog="Пример: python tg_sender.py report.txt --parse-mode HTML",
    )
    p.add_argument(
        "file",
        help="Путь к .txt файлу с текстом",
    )
    p.add_argument(
        "--parse-mode",
        choices=["Markdown", "MarkdownV2", "HTML"],
        default=None,
        help="Режим форматирования (по умолчанию: plain text)",
    )
    p.add_argument(
        "--preview",
        action="store_true",
        help="Только показать текст, не отправлять",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Проверить подключение к боту и выйти",
    )
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Конфигурация
    config = Config()
    config.validate()

    # Только проверка бота
    if args.check:
        sender = TelegramSender(config)
        ok = sender.check_bot()
        sys.exit(0 if ok else 1)

    # Чтение файла
    text = read_file(args.file)

    # Превью
    if args.preview:
        print("\n" + "=" * 50)
        print("ПРЕВЬЮ СООБЩЕНИЯ")
        print("=" * 50)
        print(text[:500])
        if len(text) > 500:
            print(f"\n... (ещё {len(text) - 500} символов)")
        print("=" * 50)
        print(f"Частей: {len(split_text(text))}")
        print(f"Chat ID: {config.chat_id}")
        print()
        return

    # Отправка
    sender = TelegramSender(config)

    if not sender.check_bot():
        sys.exit(1)

    results = sender.send_long_text(text, parse_mode=args.parse_mode)

    # Итог
    success = sum(1 for r in results if r.get("ok"))
    total = len(results)
    print(f"\n✅ Отправлено: {success}/{total} частей")

    if success < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
