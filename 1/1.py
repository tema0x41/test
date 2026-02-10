import re
import sys
import socket
import smtplib
import argparse
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import dns.resolver

# ──────────────────────────────────────────────
# Настройки
# ──────────────────────────────────────────────
SMTP_TIMEOUT = 10            # секунд на подключение
SMTP_HELO_DOMAIN = "verify.local"  # домен для HELO/EHLO
MAIL_FROM = "verify@verify.local"  # адрес отправителя для MAIL FROM

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Модели данных
# ──────────────────────────────────────────────
class DomainStatus(Enum):
    VALID = "домен валиден"
    NOT_FOUND = "домен отсутствует"
    NO_MX = "MX-записи отсутствуют или некорректны"


class SmtpStatus(Enum):
    USER_EXISTS = "пользователь существует (250)"
    USER_NOT_FOUND = "пользователь не найден (550)"
    CATCH_ALL = "сервер принимает всё (catch-all)"
    GREYLISTED = "greylisting / временная ошибка (4xx)"
    CONNECTION_FAILED = "не удалось подключиться к SMTP"
    SKIPPED = "пропущено (нет MX)"
    UNKNOWN = "неизвестный ответ"


@dataclass
class VerificationResult:
    email: str
    user: str
    domain: str
    format_valid: bool = False
    domain_status: DomainStatus = DomainStatus.NOT_FOUND
    mx_records: list[str] = field(default_factory=list)
    smtp_status: SmtpStatus = SmtpStatus.SKIPPED
    smtp_code: Optional[int] = None
    smtp_message: str = ""


# ──────────────────────────────────────────────
# 1. Валидация формата
# ──────────────────────────────────────────────
EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)


def parse_email(email: str) -> tuple[Optional[str], Optional[str]]:
    """Возвращает (user, domain) или (None, None) при невалидном формате."""
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        return None, None
    user, domain = email.rsplit("@", 1)
    return user, domain


# ──────────────────────────────────────────────
# 2. DNS MX-проверка
# ──────────────────────────────────────────────
_mx_cache: dict[str, tuple[DomainStatus, list[str]]] = {}


def check_mx(domain: str) -> tuple[DomainStatus, list[str]]:
    """
    Резолвит MX-записи домена.
    Возвращает (статус, [список mx-хостов по приоритету]).
    """
    if domain in _mx_cache:
        return _mx_cache[domain]

    mx_hosts: list[str] = []
    status = DomainStatus.NOT_FOUND

    try:
        answers = dns.resolver.resolve(domain, "MX")
        mx_hosts = [
            str(r.exchange).rstrip(".")
            for r in sorted(answers, key=lambda r: r.preference)
        ]
        if mx_hosts:
            status = DomainStatus.VALID
        else:
            status = DomainStatus.NO_MX

    except dns.resolver.NoAnswer:
        # Домен есть, но MX-записей нет — пробуем A-запись как fallback
        try:
            dns.resolver.resolve(domain, "A")
            mx_hosts = [domain]
            status = DomainStatus.VALID
            log.info(f"  [{domain}] MX нет, но A-запись найдена — используем домен напрямую")
        except Exception:
            status = DomainStatus.NO_MX

    except dns.resolver.NXDOMAIN:
        status = DomainStatus.NOT_FOUND

    except dns.resolver.NoNameservers:
        status = DomainStatus.NO_MX

    except Exception as e:
        log.warning(f"  [{domain}] DNS-ошибка: {e}")
        status = DomainStatus.NO_MX

    _mx_cache[domain] = (status, mx_hosts)
    return status, mx_hosts


# ──────────────────────────────────────────────
# 3. SMTP Handshake
# ──────────────────────────────────────────────
def smtp_verify(email: str, mx_hosts: list[str]) -> tuple[SmtpStatus, int, str]:
    """
    Выполняет SMTP handshake без отправки письма.

    Последовательность:
        EHLO → MAIL FROM:<> → RCPT TO:<email> → QUIT

    Возвращает (SmtpStatus, smtp_code, smtp_message).
    """
    for mx in mx_hosts:
        try:
            log.info(f"  Подключение к {mx}:25 ...")
            smtp = smtplib.SMTP(timeout=SMTP_TIMEOUT)
            smtp.connect(mx, 25)
            smtp.ehlo(SMTP_HELO_DOMAIN)

            # MAIL FROM (пустой bounce-адрес — стандартная практика верификации)
            code, msg = smtp.mail(MAIL_FROM)
            if code != 250:
                smtp.quit()
                continue

            # RCPT TO — ключевая команда
            code, msg = smtp.rcpt(email)
            msg_decoded = msg.decode("utf-8", errors="replace")

            smtp.quit()

            if code == 250:
                return SmtpStatus.USER_EXISTS, code, msg_decoded
            elif code == 550 or code == 551 or code == 553:
                return SmtpStatus.USER_NOT_FOUND, code, msg_decoded
            elif 400 <= code < 500:
                return SmtpStatus.GREYLISTED, code, msg_decoded
            else:
                return SmtpStatus.UNKNOWN, code, msg_decoded

        except smtplib.SMTPServerDisconnected as e:
            log.warning(f"  [{mx}] Сервер разорвал соединение: {e}")
            continue
        except smtplib.SMTPConnectError as e:
            log.warning(f"  [{mx}] Ошибка подключения: {e}")
            continue
        except socket.timeout:
            log.warning(f"  [{mx}] Таймаут подключения")
            continue
        except OSError as e:
            log.warning(f"  [{mx}] Сетевая ошибка: {e}")
            continue

    return SmtpStatus.CONNECTION_FAILED, 0, "Не удалось подключиться ни к одному MX"


# ──────────────────────────────────────────────
# 4. Главная логика верификации
# ──────────────────────────────────────────────
def verify_email(email: str) -> VerificationResult:
    """Полная проверка одного email-адреса."""
    result = VerificationResult(email=email, user="", domain="")

    # Формат
    user, domain = parse_email(email)
    if user is None or domain is None:
        log.warning(f"✗ {email} — невалидный формат")
        return result

    result.user = user
    result.domain = domain
    result.format_valid = True

    # DNS MX
    log.info(f"Проверяю {email} ...")
    domain_status, mx_hosts = check_mx(domain)
    result.domain_status = domain_status
    result.mx_records = mx_hosts

    if domain_status != DomainStatus.VALID:
        log.info(f"  DNS: {domain_status.value}")
        return result

    log.info(f"  MX-записи: {', '.join(mx_hosts)}")

    # SMTP
    smtp_status, code, message = smtp_verify(email, mx_hosts)
    result.smtp_status = smtp_status
    result.smtp_code = code
    result.smtp_message = message

    log.info(f"  SMTP: {smtp_status.value} (код {code})")
    return result


# ──────────────────────────────────────────────
# 5. Красивый вывод
# ──────────────────────────────────────────────
def print_results(results: list[VerificationResult]) -> None:
    """Печатает таблицу результатов."""
    print()
    print("=" * 100)
    print(f"{'EMAIL':<35} {'ДОМЕН':<30} {'MX':<15} {'SMTP':<20}")
    print("-" * 100)

    for r in results:
        if not r.format_valid:
            print(f"{r.email:<35} {'— невалидный формат —':<30} {'—':<15} {'—':<20}")
            continue

        domain_mark = {
            DomainStatus.VALID: "✔ валиден",
            DomainStatus.NOT_FOUND: "✗ не найден",
            DomainStatus.NO_MX: "⚠ нет MX",
        }[r.domain_status]

        smtp_mark = {
            SmtpStatus.USER_EXISTS: f"✔ существует ({r.smtp_code})",
            SmtpStatus.USER_NOT_FOUND: f"✗ не найден ({r.smtp_code})",
            SmtpStatus.CATCH_ALL: "⚠ catch-all",
            SmtpStatus.GREYLISTED: f"⏳ greylist ({r.smtp_code})",
            SmtpStatus.CONNECTION_FAILED: "✗ нет соединения",
            SmtpStatus.SKIPPED: "— пропущено",
            SmtpStatus.UNKNOWN: f"? ({r.smtp_code})",
        }[r.smtp_status]

        mx_short = r.mx_records[0][:13] + ".." if r.mx_records and len(r.mx_records[0]) > 15 else (r.mx_records[0] if r.mx_records else "—")

        print(f"{r.email:<35} {domain_mark:<30} {mx_short:<15} {smtp_mark:<20}")

    print("=" * 100)
    print()


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Проверка email-адресов через DNS MX + SMTP Handshake",
        epilog="Пример: python email_verifier.py user@gmail.com admin@example.org",
    )
    p.add_argument("emails", nargs="*", help="Email-адреса через пробел")
    p.add_argument("-f", "--file", help="Файл со списком email (по одному на строку)")
    p.add_argument("-v", "--verbose", action="store_true", help="Подробный вывод (DEBUG)")
    return p


def collect_emails(args: argparse.Namespace) -> list[str]:
    """Собирает email-адреса из всех источников."""
    emails: list[str] = []

    if args.emails:
        emails.extend(args.emails)

    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        emails.append(line)
        except FileNotFoundError:
            log.error(f"Файл не найден: {args.file}")
            sys.exit(1)

    return emails


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    emails = collect_emails(args)

    if not emails:
        parser.print_help()
        print("\n⚠  Не передано ни одного email-адреса.\n")
        sys.exit(0)

    # Убираем дубли, сохраняя порядок
    seen = set()
    unique: list[str] = []
    for e in emails:
        e_lower = e.strip().lower()
        if e_lower not in seen:
            seen.add(e_lower)
            unique.append(e_lower)

    log.info(f"Всего адресов для проверки: {len(unique)}")

    results = [verify_email(e) for e in unique]
    print_results(results)


if __name__ == "__main__":
    main()
