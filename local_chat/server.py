import concurrent.futures
import hashlib
import hmac
import http.client
from http.cookies import SimpleCookie, CookieError
import html
import ipaddress
import json
import os
import re
import secrets
import smtplib
import socket
import subprocess
import sqlite3
import ssl
import threading
import time
import unicodedata
from contextlib import contextmanager
from email.message import EmailMessage
from email.utils import formataddr
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

if __package__:
    from .capabilities import (
        discover_capabilities,
        get_capability_documentation,
        list_capabilities,
        register_capability,
    )
    from .memory import PersistentMemory, parse_memory_command
    from .system_tools import (
        PROJECTS_ROOT, SystemToolError, approve_action, prepare_action, reject_action,
    )
else:
    from capabilities import (
        discover_capabilities,
        get_capability_documentation,
        list_capabilities,
        register_capability,
    )
    from memory import PersistentMemory, parse_memory_command
    from system_tools import (
        PROJECTS_ROOT, SystemToolError, approve_action, prepare_action, reject_action,
    )


OLLAMA_URL = "http://127.0.0.1:11434"
DUCKDUCKGO_SEARCH_URL = "https://html.duckduckgo.com/html/"
STATIC_DIR = Path(__file__).resolve().parent / "static"
FONT_FILE = STATIC_DIR / "fonts" / "Vazirmatn-var.woff2"
MAX_BODY_SIZE = 2 * 1024 * 1024
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_SEARCH_QUERY_LENGTH = 500
MAX_SEARCH_RESULTS = 5
MAX_PAGE_BYTES = 1_000_000
MAX_PAGE_TEXT = 3000
APP_VERSION = "1.6.0"
MEMORY_STORE = None
MEMORY_STORE_LOCK = threading.Lock()
APP_DATA_DIR = Path(
    os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")
)
) / "LocalChat"
IDENTITY_DB_PATH = APP_DATA_DIR / "identity.sqlite3"
LEGACY_IDENTITY_DB_PATH = Path(__file__).resolve().parent / "identity.sqlite3"
SMTP_SETTINGS_PATH = Path(__file__).resolve().parent / "smtp_settings.json"
SMTP_SETTINGS_EXAMPLE_PATH = Path(__file__).resolve().parent / "smtp_settings.example.json"
CREATOR = {
    "name": "سید علیرضا موسوی",
    "email": "seyyedalirezamousavi1368@gmail.com",
    "role": "سازنده اصلی (Creator)",
}
CODE_LIFETIME_SECONDS = 10 * 60
CODE_RESEND_SECONDS = 60
CODE_MAX_ATTEMPTS = 5
IDENTITY_LOCK = threading.Lock()
BROWSER_SESSION_TTL = 12 * 60 * 60
BROWSER_SESSION_COOKIE = "localchat_session"
BROWSER_SESSIONS = {}
BROWSER_SESSIONS_LOCK = threading.RLock()


def _new_browser_session():
    now = time.monotonic()
    session_id = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    with BROWSER_SESSIONS_LOCK:
        for key, item in list(BROWSER_SESSIONS.items()):
            if item["expires"] <= now:
                BROWSER_SESSIONS.pop(key, None)
        BROWSER_SESSIONS[session_id] = {
            "csrf": csrf_token,
            "expires": now + BROWSER_SESSION_TTL,
        }
    return session_id, csrf_token


def _browser_session(session_id):
    if not isinstance(session_id, str) or not session_id:
        return None
    now = time.monotonic()
    with BROWSER_SESSIONS_LOCK:
        item = BROWSER_SESSIONS.get(session_id)
        if not item or item["expires"] <= now:
            BROWSER_SESSIONS.pop(session_id, None)
            return None
        return item



PROGRAMMING_TOOL_DEFINITIONS = [
    {"type": "function", "function": {
        "name": "create_project",
        "description": "Prepare a new isolated Python project folder. Requires explicit user approval.",
        "parameters": {"type": "object", "required": ["name"], "properties": {
            "name": {"type": "string", "description": "Lowercase project slug."}
        }},
    }},
    {"type": "function", "function": {
        "name": "create_venv",
        "description": "Prepare a Python virtual environment inside an existing project. Requires explicit user approval.",
        "parameters": {"type": "object", "required": ["project"], "properties": {
            "project": {"type": "string", "description": "Existing project slug."}
        }},
    }},
    {"type": "function", "function": {
        "name": "write_python",
        "description": "Prepare creation of a new Python source file. Existing files are never overwritten. Requires explicit user approval.",
        "parameters": {"type": "object", "required": ["project", "filename", "content"], "properties": {
            "project": {"type": "string"},
            "filename": {"type": "string", "description": "Simple .py filename."},
            "content": {"type": "string", "description": "Complete source code for the new file."}
        }},
    }},
    {"type": "function", "function": {
        "name": "docker_test",
        "description": "Run standard-library unittest tests in a temporary resource-limited Docker container, with networking disabled and the workspace read-only. Requires approval.",
        "parameters": {"type": "object", "required": ["project"], "properties": {
            "project": {"type": "string", "description": "Existing managed project slug."}
        }},
    }},
]
MAX_TOOL_ROUNDS = 8
MAX_PENDING_CHAT_APPROVALS = 8
CHAT_APPROVAL_WAITERS = {}
CHAT_APPROVAL_WAITERS_LOCK = threading.RLock()


def _register_chat_approval(approval_id, session_id):
    with CHAT_APPROVAL_WAITERS_LOCK:
        if len(CHAT_APPROVAL_WAITERS) >= MAX_PENDING_CHAT_APPROVALS:
            return None
        waiter = {"event": threading.Event(), "session_id": session_id, "result": None}
        CHAT_APPROVAL_WAITERS[approval_id] = waiter
        return waiter


def _resolve_chat_approval(approval_id, session_id, result):
    with CHAT_APPROVAL_WAITERS_LOCK:
        waiter = CHAT_APPROVAL_WAITERS.get(approval_id)
        if not waiter or not hmac.compare_digest(waiter["session_id"], str(session_id or "")):
            return False
        waiter["result"] = result
        waiter["event"].set()
        return True


def _wait_for_chat_approval(approval_id, session_id, waiter):
    try:
        if not waiter["event"].wait(timeout=300):
            reject_action(approval_id, session_id)
            return {"ok": False, "error": "زمان تأیید کاربر تمام شد؛ عملیات اجرا نشد."}
        return waiter["result"] or {"ok": False, "error": "نتیجهٔ تأیید دریافت نشد."}
    finally:
        with CHAT_APPROVAL_WAITERS_LOCK:
            CHAT_APPROVAL_WAITERS.pop(approval_id, None)


class IdentityError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _read_smtp_settings_file():
    settings_path = (
        SMTP_SETTINGS_PATH
        if SMTP_SETTINGS_PATH.exists()
        else SMTP_SETTINGS_EXAMPLE_PATH
    )
    try:
        local_settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        local_settings = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise IdentityError(
            f"فایل {settings_path.name} خوانا نیست یا JSON معتبر ندارد.", 503
        ) from exc
    if not isinstance(local_settings, dict):
        raise IdentityError(f"ساختار {settings_path.name} معتبر نیست.", 503)
    return local_settings


def _configured_local_verification_code():
    settings = _read_smtp_settings_file()
    code = settings.get("verification_code")
    if code in (None, ""):
        legacy_password = settings.get("password")
        if isinstance(legacy_password, str) and re.fullmatch(r"\d{6}", legacy_password):
            code = legacy_password
    if code in (None, ""):
        return None
    if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code):
        raise IdentityError(
            "کد محلی در فیلد verification_code باید دقیقاً ۶ رقم باشد.", 503
        )
    return code


def _identity_connect():
    IDENTITY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not IDENTITY_DB_PATH.exists() and LEGACY_IDENTITY_DB_PATH.is_file():
        source = sqlite3.connect(LEGACY_IDENTITY_DB_PATH, timeout=10)
        destination = sqlite3.connect(IDENTITY_DB_PATH, timeout=10)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
    connection = sqlite3.connect(IDENTITY_DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    with connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS creator_identity (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                role TEXT NOT NULL,
                verified_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS creator_verification (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                code_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                last_sent_at INTEGER NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0
            )
            """
        )
    return connection


@contextmanager
def _identity_session():
    connection = _identity_connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _registered_creator():
    with _identity_session() as connection:
        row = connection.execute(
            "SELECT name, email, role, verified_at FROM creator_identity WHERE id = 1"
        ).fetchone()
    if not row:
        return None
    return {
        "name": row["name"],
        "email": row["email"],
        "role": row["role"],
        "verified_at": row["verified_at"],
    }


def _memory_store():
    global MEMORY_STORE
    with MEMORY_STORE_LOCK:
        if MEMORY_STORE is None:
            MEMORY_STORE = PersistentMemory(APP_DATA_DIR / "memory.sqlite3")
        return MEMORY_STORE


def _smtp_settings():
    local_settings = _read_smtp_settings_file()

    username = (
        os.environ.get("LOCAL_CHAT_SMTP_USER")
        or local_settings.get("username")
        or CREATOR["email"]
    )
    password = (
        os.environ.get("LOCAL_CHAT_SMTP_PASSWORD")
        or local_settings.get("password")
        or ""
    )
    host = os.environ.get("LOCAL_CHAT_SMTP_HOST") or local_settings.get(
        "host", "smtp.gmail.com"
    )
    port_value = os.environ.get("LOCAL_CHAT_SMTP_PORT") or local_settings.get(
        "port", 587
    )
    sender = (
        os.environ.get("LOCAL_CHAT_SMTP_FROM")
        or local_settings.get("from_email")
        or username
    )

    if not isinstance(username, str) or not username.strip():
        raise IdentityError("نام کاربری SMTP تنظیم نشده است.", 503)
    if not isinstance(password, str) or not password.strip():
        raise IdentityError(
            "برای ارسال کد، رمز برنامهٔ Gmail را در smtp_settings.json وارد کنید.",
            503,
        )
    if not isinstance(host, str) or not host.strip():
        raise IdentityError("میزبان SMTP معتبر نیست.", 503)
    try:
        port = int(port_value)
    except (TypeError, ValueError) as exc:
        raise IdentityError("درگاه SMTP معتبر نیست.", 503) from exc
    if not 1 <= port <= 65535:
        raise IdentityError("درگاه SMTP معتبر نیست.", 503)

    return {
        "username": username.strip(),
        "password": password.strip(),
        "host": host.strip(),
        "port": port,
        "from_email": str(sender).strip(),
    }


def _send_creator_verification_email(code, settings):
    message = EmailMessage()
    message["Subject"] = "کد تأیید سازندهٔ گفت‌وگوی محلی"
    message["From"] = formataddr(("گفت‌وگوی محلی", settings["from_email"]))
    message["To"] = CREATOR["email"]
    message.set_content(
        f"""سلام {CREATOR["name"]}

کد یک‌بارمصرف تأیید هویت سازندهٔ اصلی:

{code}

این کد تا ۱۰ دقیقه اعتبار دارد و پس از ۵ بار ورود اشتباه باطل می‌شود.
اگر شما این درخواست را نداده‌اید، این ایمیل را نادیده بگیرید.
"""
    )

    try:
        with smtplib.SMTP(settings["host"], settings["port"], timeout=20) as client:
            client.ehlo()
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
            client.login(settings["username"], settings["password"])
            client.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise IdentityError(
            "ورود SMTP ناموفق بود. برای Gmail از رمز برنامهٔ ۱۶ رقمی استفاده کنید.",
            502,
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise IdentityError(
            "ارسال ایمیل تأیید ناموفق بود؛ اتصال و تنظیمات SMTP را بررسی کنید.", 502
        ) from exc


@register_capability(
    id="creator_identity",
    name="شناسایی سازنده",
    description="ثبت و بازیابی هویت سازنده در همین دستگاه.",
    purpose="تأیید کد محلی و پاسخ برنامه‌ای به پرسش دربارهٔ سازنده.",
    activation_conditions=(
        "سازنده کیست",
        "سازنده‌ات کیست",
        "خالق",
        "creator",
        "who am i",
        "من کی هستم",
        "هویت من",
    ),
    inputs=("کد تأیید شش‌رقمی (تنها هنگام ثبت)", "پرسش شناسایی سازنده"),
    outputs=("وضعیت ثبت", "پروفایل سازنده یا پاسخ شناسایی"),
    dependencies=("پایگاه‌دادهٔ SQLite محلی", "فایل تنظیم کد محلی یا SMTP برای ایمیل"),
    limitations=("کد محلی اثبات مالکیت ایمیل نیست.", "اطلاعات فقط روی همین دستگاه ذخیره می‌شود."),
    documentation="creator_identity.md",
)
def _start_creator_verification():
    with IDENTITY_LOCK:
        if _registered_creator():
            return {"verified": True, "profile": _registered_creator()}

        local_code = _configured_local_verification_code()
        settings = None if local_code else _smtp_settings()
        now = int(time.time())
        with _identity_session() as connection:
            pending = connection.execute(
                "SELECT last_sent_at FROM creator_verification WHERE id = 1"
            ).fetchone()
            if pending and now - pending["last_sent_at"] < CODE_RESEND_SECONDS:
                remaining = CODE_RESEND_SECONDS - (now - pending["last_sent_at"])
                raise IdentityError(
                    f"کد ارسال شده است؛ ایمیلتان را بررسی کنید. ارسال دوباره تا {remaining} ثانیهٔ دیگر ممکن است.",
                    429,
                )

            code = local_code or f"{secrets.randbelow(1_000_000):06d}"
            salt = secrets.token_hex(16)
            code_hash = hashlib.pbkdf2_hmac(
                "sha256", code.encode("ascii"), salt.encode("ascii"), 120_000
            ).hex()
            connection.execute(
                """
                INSERT INTO creator_verification
                    (id, code_hash, salt, expires_at, last_sent_at, attempts)
                VALUES (1, ?, ?, ?, ?, 0)
                ON CONFLICT(id) DO UPDATE SET
                    code_hash = excluded.code_hash,
                    salt = excluded.salt,
                    expires_at = excluded.expires_at,
                    last_sent_at = excluded.last_sent_at,
                    attempts = 0
                """,
                (code_hash, salt, now + CODE_LIFETIME_SECONDS, now),
            )

        if local_code:
            return {
                "verified": False,
                "sent": False,
                "local_code": True,
                "expires_in": CODE_LIFETIME_SECONDS,
            }

        try:
            _send_creator_verification_email(code, settings)
        except IdentityError:
            with _identity_session() as connection:
                connection.execute(
                    "DELETE FROM creator_verification WHERE id = 1"
                )
            raise

        return {"verified": False, "sent": True, "expires_in": CODE_LIFETIME_SECONDS}


def _verify_creator_code(code):
    if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code):
        raise IdentityError("کد باید دقیقاً ۶ رقم باشد.")

    now = int(time.time())
    verification_error = None
    verified_profile = None
    with IDENTITY_LOCK:
        with _identity_session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            creator = connection.execute(
                "SELECT name, email, role, verified_at FROM creator_identity WHERE id = 1"
            ).fetchone()
            if creator:
                return {
                    "verified": True,
                    "profile": {
                        "name": creator["name"],
                        "email": creator["email"],
                        "role": creator["role"],
                        "verified_at": creator["verified_at"],
                    },
                }

            challenge = connection.execute(
                "SELECT * FROM creator_verification WHERE id = 1"
            ).fetchone()
            if not challenge:
                verification_error = IdentityError(
                    "کد تأییدی فعال نیست؛ ابتدا کد را ارسال کنید.", 409
                )
            elif challenge["expires_at"] < now:
                connection.execute(
                    "DELETE FROM creator_verification WHERE id = 1"
                )
                verification_error = IdentityError(
                    "مهلت کد پایان یافته است؛ کد تازه درخواست کنید.", 410
                )
            elif challenge["attempts"] >= CODE_MAX_ATTEMPTS:
                verification_error = IdentityError(
                    "تعداد تلاش‌ها بیش از حد شد؛ پس از پایان مهلت ارسال، کد تازه درخواست کنید.",
                    429,
                )
            else:
                submitted_hash = hashlib.pbkdf2_hmac(
                    "sha256",
                    code.encode("ascii"),
                    challenge["salt"].encode("ascii"),
                    120_000,
                ).hex()
                if hmac.compare_digest(submitted_hash, challenge["code_hash"]):
                    connection.execute(
                        """
                        INSERT INTO creator_identity (id, name, email, role, verified_at)
                        VALUES (1, ?, ?, ?, ?)
                        """,
                        (CREATOR["name"], CREATOR["email"], CREATOR["role"], now),
                    )
                    connection.execute("DELETE FROM creator_verification WHERE id = 1")
                    verified_profile = {
                        "verified": True,
                        "profile": {**CREATOR, "verified_at": now},
                    }
                else:
                    attempts = challenge["attempts"] + 1
                    connection.execute(
                        "UPDATE creator_verification SET attempts = ? WHERE id = 1",
                        (attempts,),
                    )
                    if attempts >= CODE_MAX_ATTEMPTS:
                        verification_error = IdentityError(
                            "تعداد تلاش‌ها بیش از حد شد؛ پس از پایان مهلت ارسال، کد تازه درخواست کنید.",
                            429,
                        )
                    else:
                        verification_error = IdentityError(
                            f"کد نادرست است؛ {CODE_MAX_ATTEMPTS - attempts} تلاش باقی مانده."
                        )

    if verification_error:
        raise verification_error
    return verified_profile


def _creator_question_answer(text):
    value = _normalize_capability_text(text)
    creator_mention = bool(
        re.search(r"(?:سازند\w*|خالق\w*|مالک\w*|creator|owner|created|built)", value)
    )
    asks_creator = bool(
        re.search(
            r"(?:کی|چه\s*کسی|کیه|کیست|چیست|who|what|"
            r"معرفی|معرف|بگو|tell\s+me|introduce)",
            value,
        )
    )
    personal_question = bool(
        re.search(
            r"(?:من\s*(?:کیم|کیستم|کیست)|"
            r"من.{0,45}(?:کی|چه\s*کسی|اسم|نام).{0,25}"
            r"(?:هستم|ام|چی|چیه|کیه|who)|"
            r"(?:چی|چه).{0,15}درباره\s*(?:من|م).{0,20}"
            r"(?:می\s*دونی|میدونی|می\s*دانی|میدانی|می\s*شناسی|میشناسی|know|remember)|"
            r"درباره\s*(?:من|م).{0,20}(?:چی|چه).{0,20}"
            r"(?:می\s*دونی|میدونی|می\s*دانی|میدانی|know|remember)|"
            r"(?:who\s+am\s+i|do\s+you\s+know\s+me|"
            r"منو\s*می\s*شناسی|من\s*را\s*می\s*شناسی|"
            r"اسم\s+من\s+(?:چی|چیه)|نام\s+من\s+چی))",
            value,
        )
    )
    creator_name = _normalize_capability_text(CREATOR["name"])
    asks_about_named_creator = creator_name in value and bool(
        re.search(r"(?:کی|چه\s*کسی|کیه|کیست|چیست|who|what|معرفی|بگو)", value)
    )
    self_intro = bool(
        re.search(r"(?:خودتو|خودت را|yourself)", value)
        and re.search(r"(?:معرفی|معرف|introduce|tell\s+me)", value)
        and creator_mention
    )
    creator_question = creator_mention and (asks_creator or self_intro)
    if not personal_question and not creator_question and not asks_about_named_creator:
        return None
    creator = _registered_creator()
    if not creator:
        return None
    return (
        f"شما {creator['name']}، {creator['role']} این ایجنت هستید.\n"
        f"ایمیل ثبت‌شده: {creator['email']}"
    )


def _normalize_capability_text(text):
    value = unicodedata.normalize("NFKC", text).casefold()
    value = value.replace("\u200c", " ").replace("ي", "ی").replace("ك", "ک")
    return re.sub(r"\s+", " ", value).strip()


def _capability_question_answer(text):
    value = _normalize_capability_text(text)
    direct_tool_request = bool(
        re.search(r"(?:سرچ|جست\s*وجو|search|browse)", value)
        and re.search(r"(?:کن|بکن|انجام\s*بده|search|look\s+up)", value)
        and not re.search(r"(?:داری|دارید|می\s*تونی|میتونی|can\s+you)", value)
    )
    if direct_tool_request:
        return None

    asks_about_capabilities = bool(
        re.search(r"(?:چه|چی)\s*بلدی|چه\s*کاری\s*ازت\s*برمیاد", value)
        or (
            re.search(
            r"(?:قابلیت|امکانات|توانایی|کارهایی|کارایی|ابزار|سرچ|جست\s*وجو|"
            r"search|browse|tools|features)",
            value,
            )
            and re.search(
            r"(?:داری|دارید|دارم|داره|می\s*تونی|میتونی|می\s*توانی|میتوانی|"
            r"بلدی|می\s*شه|میشه|هست|چی|چه|بگو|لیست|معرفی|can\s+you|"
            r"what|list|available|have)",
            value,
            )
        )
    )
    if not asks_about_capabilities:
        asks_about_capabilities = bool(
            re.search(r"(?:what\s+can\s+you|your\s+(?:tools|features))", value)
        )
    if not asks_about_capabilities:
        return None
    available = [
        capability
        for capability in list_capabilities()
        if capability["status"] == "available"
    ]
    if not available:
        return "در حال حاضر قابلیت اضافه‌ای در دسترس نیست."
    lines = ["قابلیت‌های فعلی من:"]
    lines.extend(
        f"- {capability['name']}: {capability['description']}"
        for capability in available
    )
    lines.append("برای اطلاعات روزآمد، جست‌وجوی وب را درخواست کن یا دکمه‌اش را روشن کن.")
    return "\n".join(lines)


class PageTextParser(HTMLParser):
    skip_tags = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}
    block_tags = {"article", "br", "div", "h1", "h2", "h3", "li", "p", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, _attrs):
        if tag in self.skip_tags:
            self.skip_depth += 1
        elif not self.skip_depth and tag in self.block_tags:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self.skip_tags and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth and tag in self.block_tags:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)


class DuckDuckGoResultsParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results = []
        self.capture = None
        self.capture_depth = 0
        self.capture_href = ""
        self.parts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if self.capture:
            self.capture_depth += 1
        elif tag == "a" and "result__a" in classes:
            self.capture = "title"
            self.capture_depth = 1
            self.capture_href = attrs.get("href", "")
            self.parts = []
        elif any(value.startswith("result__snippet") for value in classes):
            self.capture = "snippet"
            self.capture_depth = 1
            self.parts = []

    def handle_endtag(self, _tag):
        if not self.capture:
            return
        self.capture_depth -= 1
        if self.capture_depth:
            return
        text = " ".join(" ".join(self.parts).split())
        if self.capture == "title" and text and self.capture_href:
            self.results.append({"title": text, "href": self.capture_href, "snippet": ""})
        elif self.capture == "snippet" and text and self.results:
            self.results[-1]["snippet"] = text
        self.capture = None
        self.capture_href = ""
        self.parts = []

    def handle_data(self, data):
        if self.capture:
            self.parts.append(data)


def _public_address(host, port):
    try:
        addresses = {
            entry[4][0]
            for entry in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        }
    except OSError:
        return None
    if not addresses:
        return None
    try:
        if any(not ipaddress.ip_address(address).is_global for address in addresses):
            return None
    except ValueError:
        return None
    return sorted(addresses)[0]


def _fetch_public_page(url):
    current_url = url
    for _ in range(4):
        try:
            parts = urlsplit(current_url)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                return ""
            if parts.username is not None or parts.password is not None:
                return ""
            port = parts.port or (443 if parts.scheme == "https" else 80)
            expected_port = 443 if parts.scheme == "https" else 80
            if port != expected_port:
                return ""
            host = parts.hostname.encode("idna").decode("ascii")
            address = _public_address(host, port)
            if not address:
                return ""

            connection_type = (
                http.client.HTTPSConnection
                if parts.scheme == "https"
                else http.client.HTTPConnection
            )
            if parts.scheme == "https":
                connection = connection_type(
                    host, port, timeout=8, context=ssl.create_default_context()
                )
            else:
                connection = connection_type(host, port, timeout=8)
            connection._create_connection = (
                lambda _address, timeout, source_address, resolved=address, target_port=port:
                socket.create_connection(
                    (resolved, target_port),
                    timeout=timeout,
                    source_address=source_address,
                )
            )

            target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
            connection.request(
                "GET",
                target,
                headers={
                    "User-Agent": "LocalChat/1.0 (web search)",
                    "Accept": "text/html,text/plain;q=0.9",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                connection.close()
                if not location:
                    return ""
                current_url = urljoin(current_url, location)
                continue
            if response.status != 200:
                connection.close()
                return ""

            content_type = response.getheader("Content-Type", "").lower()
            if "text/html" not in content_type and "text/plain" not in content_type:
                connection.close()
                return ""
            data = response.read(MAX_PAGE_BYTES + 1)
            connection.close()
            if len(data) > MAX_PAGE_BYTES:
                data = data[:MAX_PAGE_BYTES]
            charset_match = re.search(r"charset=([\w.-]+)", content_type)
            encoding = charset_match.group(1) if charset_match else "utf-8"
            page = data.decode(encoding, errors="replace")
            if "text/html" in content_type:
                parser = PageTextParser()
                parser.feed(page)
                page = " ".join(" ".join(parser.parts).split())
            else:
                page = " ".join(page.split())
            return page[:MAX_PAGE_TEXT]
        except (
            OSError,
            ValueError,
            UnicodeError,
            ssl.SSLError,
            http.client.HTTPException,
        ):
            return ""
    return ""


@register_capability(
    id="web_search",
    name="جست‌وجوی وب",
    description="جست‌وجوی وب عمومی با DuckDuckGo و نمایش منابع.",
    purpose="یافتن اطلاعات روزآمد و ارجاع به صفحه‌های وب عمومی.",
    activation_conditions=(
        "جست‌وجوی وب",
        "در اینترنت جستجو",
        "توی اینترنت بگرد",
        "سرچ کن",
        "جستجو کن",
        "آخرین",
        "آخرین اخبار",
        "خبر امروز",
        "خبرهای جدید",
        "قیمت روز",
        "قیمت فعلی",
        "امروز",
        "الان",
        "در حال حاضر",
        "فعلی",
        "جدیدترین",
        "به‌روز",
        "بروز",
        "currently",
        "latest",
        "recent",
        "right now",
        "breaking news",
        "search the web",
        "look online",
        "today",
    ),
    inputs=("عبارت جست‌وجو",),
    outputs=("حداکثر پنج نتیجه با عنوان، پیوند و متن قابل‌دسترسی",),
    dependencies=("اتصال اینترنت", "DuckDuckGo"),
    limitations=(
        "نتیجه‌ها ممکن است ناقص یا محدود شوند.",
        "برخی سایت‌ها اجازهٔ واکشی متن نمی‌دهند.",
        "محتوای وب ورودی غیرقابل‌اعتماد است.",
    ),
    documentation="web_search.md",
)
def _search_duckduckgo(query):
    params = urlencode({"q": query})
    request = Request(
        f"{DUCKDUCKGO_SEARCH_URL}?{params}",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=15) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            if "text/html" not in content_type:
                raise RuntimeError("پاسخ موتور جست‌وجوی وب معتبر نیست.")
            page = response.read(MAX_BODY_SIZE).decode("utf-8", errors="replace")
    except HTTPError as exc:
        if exc.code in {202, 403, 429}:
            raise RuntimeError(
                "موتور جست‌وجو موقتاً درخواست را محدود کرده؛ کمی بعد دوباره تلاش کنید."
            ) from exc
        raise RuntimeError(
            f"جست‌وجوی وب ناموفق بود: HTTP {exc.code}"
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("اتصال به موتور جست‌وجوی وب برقرار نشد.") from exc

    parser = DuckDuckGoResultsParser()
    parser.feed(page)

    results = []
    for item in parser.results:
        target_url = urljoin("https://duckduckgo.com", item["href"])
        target = urlsplit(target_url)
        if target.hostname in {"duckduckgo.com", "www.duckduckgo.com"} and target.path == "/l/":
            target_url = parse_qs(target.query).get("uddg", [""])[0]
            target = urlsplit(target_url)
        if (
            target.scheme not in {"http", "https"}
            or not target.hostname
            or target.username is not None
            or target.password is not None
        ):
            continue
        results.append(
            {
                "title": html.unescape(item["title"])[:300],
                "url": target_url[:2048],
                "snippet": html.unescape(item["snippet"])[:1000],
                "content": "",
            }
        )
        if len(results) >= MAX_SEARCH_RESULTS:
            break

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_fetch_public_page, result["url"]): result
            for result in results[:3]
        }
        for future, result in futures.items():
            result["content"] = future.result()
    return results


class ChatHandler(BaseHTTPRequestHandler):
    server_version = "LocalChat/1.0"
    protocol_version = "HTTP/1.1"

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            "base-uri 'self'; frame-ancestors 'none'",
        )
        super().end_headers()

    def _send_json(self, status, payload, close_connection=False):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if close_connection:
            self.close_connection = True
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _validate_browser_post(self):
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin", "")
        parsed = urlsplit(origin)
        if (not host or parsed.scheme != "http" or parsed.netloc.lower() != host.lower()
                or parsed.username is not None or parsed.password is not None):
            self._send_json(403, {"error": "مبدأ درخواست معتبر نیست؛ صفحه را از نشانی محلی برنامه باز کنید."})
            return False
        try:
            cookies = SimpleCookie()
            cookies.load(self.headers.get("Cookie", ""))
            morsel = cookies.get(BROWSER_SESSION_COOKIE)
            session_id = morsel.value if morsel else None
        except (CookieError, ValueError):
            session_id = None
        session = _browser_session(session_id)
        supplied = self.headers.get("X-CSRF-Token", "")
        if not session or not supplied or not hmac.compare_digest(supplied, session["csrf"]):
            self._send_json(403, {"error": "نشست یا توکن امنیتی معتبر نیست؛ صفحه را تازه‌سازی کنید."})
            return False
        self.browser_session_id = session_id
        return True

    def _deny_creator(
        self, message="ابتدا هویت سازنده را تأیید کنید.", discard_body=False
    ):
        close_connection = False
        if discard_body:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                content_length = 0
                close_connection = True
            if content_length < 0 or content_length > MAX_BODY_SIZE:
                close_connection = True
            elif content_length:
                try:
                    close_connection = len(self.rfile.read(content_length)) != content_length
                except OSError:
                    close_connection = True
        self._send_json(
            403, {"error": message}, close_connection=close_connection
        )

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/session":
            session_id, csrf_token = _new_browser_session()
            body = json.dumps({"csrf_token": csrf_token}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Set-Cookie", BROWSER_SESSION_COOKIE + "=" + session_id + "; Path=/; HttpOnly; SameSite=Strict; Max-Age=" + str(BROWSER_SESSION_TTL))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/status":
            self._send_json(200, {"version": APP_VERSION})
            return
        if path == "/api/identity":
            creator = _registered_creator()
            self._send_json(
                200,
                {"verified": bool(creator), "profile": creator},
            )
            return
        if path == "/api/models":
            self._get_models()
            return
        if path == "/api/memory":
            if not _registered_creator():
                self._deny_creator()
                return
            self._send_json(200, {"memories": _memory_store().list()})
            return
        if path == "/api/capabilities":
            self._send_json(200, {"capabilities": list_capabilities()})
            return
        if path == "/api/system-tools/projects":
            if not _registered_creator():
                self._deny_creator()
                return
            PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
            projects = sorted(
                item.name for item in PROJECTS_ROOT.iterdir()
                if item.is_dir() and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", item.name)
            )
            self._send_json(200, {"projects": projects, "root": str(PROJECTS_ROOT)})
            return
        if path == "/api/capabilities/discover":
            query = parse_qs(urlsplit(self.path).query).get("query", [""])[0]
            if not query.strip() or len(query) > 2000:
                self._send_json(400, {"error": "عبارت کشف قابلیت خالی یا بیش از حد طولانی است."})
                return
            self._send_json(
                200, {"capabilities": discover_capabilities(query.strip())}
            )
            return
        if path.startswith("/api/capabilities/"):
            capability_id = path[len("/api/capabilities/") :]
            try:
                result = get_capability_documentation(capability_id)
            except (OSError, ValueError) as exc:
                self._send_json(500, {"error": f"مستندات قابلیت خوانده نشد: {exc}"})
                return
            if result is None:
                self._send_json(404, {"error": "قابلیت در دسترس نیست یا شناخته نشد."})
                return
            self._send_json(200, result)
            return

        files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/static/style.css": ("style.css", "text/css; charset=utf-8"),
            "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
        }
        if path == "/static/fonts/Vazirmatn-var.woff2":
            file_path = FONT_FILE
            content_type = "font/woff2"
        else:
            asset = files.get(path)
            if asset is None:
                self._send_json(404, {"error": "صفحهٔ موردنظر پیدا نشد."})
                return
            file_path = STATIC_DIR / asset[0]
            content_type = asset[1]
        self._send_static_file(file_path, content_type)

    def _send_static_file(self, file_path, content_type):
        try:
            body = file_path.read_bytes()
        except OSError:
            self._send_json(500, {"error": "فایل رابط کاربری قابل خواندن نیست."})
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _get_models(self):
        try:
            with urlopen(f"{OLLAMA_URL}/api/tags", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
            self._send_json(
                503,
                {"error": "ارتباط با Ollama برقرار نشد. مطمئن شوید Ollama اجراست."},
            )
            return

        models = payload.get("models")
        if not isinstance(models, list):
            self._send_json(502, {"error": "پاسخ Ollama برای فهرست مدل‌ها معتبر نیست."})
            return
        self._send_json(200, {"models": models})

    def _read_json_body(self, max_size=16_384):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "اندازهٔ درخواست معتبر نیست."})
            return None
        if content_length <= 0 or content_length > max_size:
            self._send_json(413, {"error": "اندازهٔ درخواست خالی یا بیش از حد مجاز است."})
            return None
        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "بدنهٔ درخواست JSON معتبر نیست."})
            return None
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "ساختار درخواست معتبر نیست."})
            return None
        return payload

    def _start_identity_verification(self):
        try:
            result = _start_creator_verification()
        except IdentityError as exc:
            self._send_json(exc.status, {"error": str(exc)})
            return
        self._send_json(200, result)

    def _verify_identity(self):
        payload = self._read_json_body()
        if payload is None:
            return
        try:
            result = _verify_creator_code(payload.get("code"))
        except IdentityError as exc:
            self._send_json(exc.status, {"error": str(exc)})
            return
        self._send_json(200, result)

    def _search(self):
        payload = self._read_json_body()
        if payload is None:
            return
        query = payload.get("query")
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > MAX_SEARCH_QUERY_LENGTH
        ):
            self._send_json(400, {"error": "عبارت جست‌وجو خالی یا بیش از حد طولانی است."})
            return

        try:
            results = _search_duckduckgo(query.strip())
        except RuntimeError as exc:
            self._send_json(502, {"error": str(exc)})
            return
        self._send_json(200, {"results": results})


    def _write_chat_event(self, payload):
        line = json.dumps(payload, ensure_ascii=False).encode("utf-8") + bytes([10])
        self.wfile.write(line)
        self.wfile.flush()

    def _chat_with_approved_tools(self, ollama_payload):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        messages = list(ollama_payload["messages"])
        total_calls = 0
        try:
            for _round in range(MAX_TOOL_ROUNDS):
                request_payload = {
                    **ollama_payload,
                    "messages": messages,
                    "stream": False,
                    "tools": PROGRAMMING_TOOL_DEFINITIONS,
                }
                request = Request(
                    f"{OLLAMA_URL}/api/chat",
                    data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=600) as response:
                    model_result = json.loads(response.read().decode("utf-8"))
                message = model_result.get("message") or {}
                tool_calls = message.get("tool_calls") or []
                content = message.get("content") or ""
                if content:
                    self._write_chat_event({
                        "message": {"role": "assistant", "content": content},
                        "done": False,
                    })
                if not tool_calls:
                    self._write_chat_event({
                        "message": {"role": "assistant", "content": ""},
                        "done": True,
                        "done_reason": model_result.get("done_reason", "stop"),
                    })
                    return
                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls,
                })
                for call in tool_calls:
                    total_calls += 1
                    if total_calls > MAX_TOOL_ROUNDS:
                        self._write_chat_event({"error": "تعداد مراحل ابزار از حد مجاز گذشت.", "done": True})
                        return
                    function = call.get("function") or {}
                    tool_name = function.get("name")
                    arguments = function.get("arguments", {})
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError:
                            arguments = None
                    if tool_name not in {"create_project", "create_venv", "write_python", "docker_test"} or not isinstance(arguments, dict):
                        tool_result = {"ok": False, "error": "درخواست ابزار نامعتبر یا خارج از فهرست مجاز است."}
                    else:
                        try:
                            prepared = prepare_action(tool_name, arguments, self.browser_session_id)
                            waiter = _register_chat_approval(prepared["approval_id"], self.browser_session_id)
                            if waiter is None:
                                reject_action(prepared["approval_id"], self.browser_session_id)
                                tool_result = {"ok": False, "error": "تعداد درخواست‌های در انتظار تأیید بیش از حد مجاز است."}
                            else:
                                self._write_chat_event({"approval_required": prepared})
                                tool_result = _wait_for_chat_approval(
                                    prepared["approval_id"], self.browser_session_id, waiter
                                )
                        except SystemToolError as exc:
                            tool_result = {"ok": False, "error": str(exc)}
                        except (OSError, subprocess.SubprocessError) as exc:
                            tool_result = {"ok": False, "error": str(exc)[:1000]}
                    messages.append({
                        "role": "tool",
                        "tool_name": tool_name or "unknown",
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    })
            self._write_chat_event({"error": "مدل به سقف مراحل ابزار رسید؛ برای ادامه پیام تازه‌ای بفرستید.", "done": True})
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            self._write_chat_event({"error": f"Ollama درخواست ابزار را نپذیرفت: {detail[:500]}", "done": True})
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            try:
                self._write_chat_event({"error": f"ارتباط یا پاسخ Ollama ناموفق بود: {str(exc)[:500]}", "done": True})
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self._validate_browser_post():
            return
        if path == "/api/identity/start":
            self._start_identity_verification()
            return
        if path == "/api/identity/verify":
            self._verify_identity()
            return
        if path == "/api/search":
            self._search()
            return
        if path in ("/api/system-tools/prepare", "/api/system-tools/approve", "/api/system-tools/reject"):
            if not _registered_creator():
                self._deny_creator(discard_body=True)
                return
            payload = self._read_json_body(max_size=300_000)
            if payload is None:
                return
            try:
                if path.endswith("/prepare"):
                    result = prepare_action(payload.get("action"), payload.get("args", {}), self.browser_session_id)
                    self._send_json(202, result)
                elif path.endswith("/approve"):
                    approval_id = payload.get("approval_id")
                    result = approve_action(approval_id, self.browser_session_id)
                    _resolve_chat_approval(approval_id, self.browser_session_id, result)
                    self._send_json(200, result)
                else:
                    approval_id = payload.get("approval_id")
                    rejected = reject_action(approval_id, self.browser_session_id)
                    if rejected:
                        _resolve_chat_approval(approval_id, self.browser_session_id, {
                            "ok": False, "rejected": True, "error": "کاربر این عملیات را رد کرد؛ هیچ تغییری انجام نشد."
                        })
                    self._send_json(200, {"rejected": rejected})
            except SystemToolError as exc:
                if path.endswith("/approve"):
                    _resolve_chat_approval(payload.get("approval_id"), self.browser_session_id, {"ok": False, "error": str(exc)})
                self._send_json(400, {"error": str(exc)})
            except subprocess.TimeoutExpired:
                if path.endswith("/approve"):
                    _resolve_chat_approval(payload.get("approval_id"), self.browser_session_id, {"ok": False, "error": "زمان اجرای عملیات تمام شد."})
                self._send_json(504, {"error": "زمان مجاز عملیات تمام شد؛ فرایند متوقف شد."})
            except (OSError, subprocess.SubprocessError) as exc:
                if path.endswith("/approve"):
                    _resolve_chat_approval(payload.get("approval_id"), self.browser_session_id, {"ok": False, "error": str(exc)[:1000]})
                self._send_json(500, {"error": f"اجرای عملیات محلی ناموفق بود: {exc}"})
            return
        if path == "/api/memory":
            if not _registered_creator():
                self._deny_creator(discard_body=True)
                return
            payload = self._read_json_body(max_size=4096)
            if payload is None:
                return
            content = payload.get("content")
            if not isinstance(content, str):
                self._send_json(400, {"error": "متن حافظه معتبر نیست."})
                return
            try:
                item = _memory_store().add(content)
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            self._send_json(201, {"memory": item})
            return
        if path == "/api/memory/forget":
            if not _registered_creator():
                self._deny_creator(discard_body=True)
                return
            payload = self._read_json_body(max_size=4096)
            if payload is None:
                return
            query = payload.get("query", "")
            if not isinstance(query, str):
                self._send_json(400, {"error": "عبارت حذف حافظه معتبر نیست."})
                return
            self._send_json(200, {"deleted": _memory_store().forget(query)})
            return
        if path != "/api/chat":
            self._send_json(404, {"error": "مسیر موردنظر پیدا نشد."})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "اندازهٔ درخواست معتبر نیست."})
            return
        if content_length <= 0 or content_length > MAX_BODY_SIZE:
            self._send_json(413, {"error": "اندازهٔ درخواست خالی یا بیش از حد مجاز است."})
            return

        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "بدنهٔ درخواست JSON معتبر نیست."})
            return

        error = self._validate_chat(payload)
        if error:
            self._send_json(400, {"error": error})
            return

        if not _registered_creator():
            self._deny_creator(
                "برای استفاده از چت، ابتدا هویت سازنده را با کد ایمیل تأیید کنید."
            )
            return

        last_user_text = next(
            (item["content"] for item in reversed(payload["messages"]) if item["role"] == "user"),
            "",
        )
        memory_command = parse_memory_command(last_user_text)
        if memory_command:
            action, value = memory_command
            store = _memory_store()
            if action == "save":
                saved = store.add(value)
                reply = "این مورد را در حافظهٔ محلی ذخیره کردم و در گفت‌وگوهای بعدی هم از آن استفاده می‌کنم."
                if saved.get("duplicate"):
                    reply = "این مورد از قبل در حافظه بود؛ متن آن را به‌روزرسانی کردم."
            elif action == "forget":
                deleted = store.forget(value)
                reply = f"{deleted} مورد مرتبط از حافظه حذف شد." if deleted else "موردی مطابق این عبارت در حافظه پیدا نکردم."
            elif action == "forget_all":
                deleted = store.forget("")
                reply = f"حافظهٔ بلندمدت پاک شد؛ {deleted} مورد حذف شد."
            else:
                items = store.list()
                reply = "هنوز چیزی در حافظهٔ بلندمدت ذخیره نشده است." if not items else (
                    "مواردی که در حافظهٔ محلی ذخیره شده‌اند:\n" +
                    "\n".join(f"• {item['content']}" for item in items)
                )
            body = (json.dumps({
                "model": payload["model"],
                "message": {"role": "assistant", "content": reply},
                "done": True, "done_reason": "stop"
            }, ensure_ascii=False) + "\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        creator_answer = _creator_question_answer(
            payload["messages"][-1]["content"]
            if payload["messages"][-1]["role"] == "user"
            else ""
        )
        if creator_answer:
            body = (
                json.dumps(
                    {
                        "model": payload["model"],
                        "message": {"role": "assistant", "content": creator_answer},
                        "done": True,
                        "done_reason": "stop",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        capability_answer = _capability_question_answer(
            payload["messages"][-1]["content"]
            if payload["messages"][-1]["role"] == "user"
            else ""
        )
        if capability_answer and payload.get("mode", "general") == "programming":
            body = (
                json.dumps(
                    {
                        "model": payload["model"],
                        "message": {
                            "role": "assistant",
                            "content": capability_answer,
                        },
                        "done": True,
                        "done_reason": "stop",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        memory_context = _memory_store().prompt_context()
        mode = payload.get("mode", "general")
        if mode not in {"general", "programming"}:
            self._send_json(400, {"error": "حالت گفتگو معتبر نیست."})
            return
        system_prompt = (
            "تو یک دستیار محلی فارسی‌زبان، دقیق، سریع و صمیمی هستی. "
            "ابتدا منظور واقعی کاربر را بفهم؛ با غلط تایپی و زبان محاوره‌ای فارسی سازگار باش. "
            "اگر درخواست روشن است مستقیم پاسخ بده؛ اگر ابهام مهمی مانع انجام کار است فقط یک سؤال کوتاه بپرس. "
            "اطلاعات نامطمئن را قطعی جلوه نده و پاسخ را روشن و متناسب با سؤال بنویس. "
        )
        if mode == "programming":
            system_prompt += (
                "حالت برنامه‌نویسی فعال است. ابزارهای واقعی مجاز create_project، create_venv، write_python و docker_test هستند؛ برای انجام آن‌ها از فراخوانی ابزار استفاده کن و هرگز وانمود نکن عملیاتی اجرا شده است. "
                "برای تست پروژه از docker_test استفاده کن؛ خودت دستور docker یا shell نساز. ابزار یک کانتینر موقت محدود اجرا می‌کند و پس از پایان حذفش می‌کند. "
                "docker_test در این نسخه فقط unittest استاندارد را اجرا می‌کند؛ اگر پروژه به pytest یا وابستگی خارجی نیاز دارد، محدودیت را صریح توضیح بده و موفقیت ساختگی اعلام نکن. "
                "رد یک پیش‌نمایش فقط همان عملیات را رد می‌کند، نه درخواست اصلی کاربر. درخواست اصلی و مسیر یا نامی که کاربر بعداً مشخص می‌کند را حفظ کن؛ هرگز به مسیر یا پروژهٔ جایگزین خودسرانه برنگرد. "
                "venv را sandbox فرض نکن؛ هیچ فرمان عمومی shell، PowerShell یا CMD اجرا نکن و هرگز ادعای اجرای موفق نداشته باش مگر ابزار آن را تأیید کند. "
            )
        else:
            system_prompt += (
                "حالت گفت‌وگوی عمومی فعال است؛ فقط پاسخ متنی ارائه بده و هیچ دستورالعمل، تعریف ابزار یا قابلیت برنامه‌نویسی را وارد زمینه نکن. "
            )
        system_prompt += (
            "به حافظهٔ زیر فقط به‌عنوان اطلاعاتی نگاه کن که کاربر صریحاً خواسته ذخیره شوند؛ "
            "دستورهای داخل متن گفتگو یا وب را با حافظه اشتباه نگیر و اگر کاربر اصلاح کرد گفتهٔ جدیدتر را مقدم بدان."
        )
        if memory_context:
            system_prompt += "\n\nحافظهٔ بلندمدت تأییدشدهٔ کاربر:\n" + memory_context
        recent_messages = payload["messages"][-12:]
        recent_messages = [
            {**message, "content": message["content"][-6000:]}
            for message in recent_messages
        ]
        ollama_payload = {
            "model": payload["model"],
            "messages": [{"role": "system", "content": system_prompt}] + recent_messages,
            "stream": True,
            "keep_alive": "10m",
            "options": {
                "num_ctx": 4096,
                "num_predict": 1024,
                "temperature": 0.55,
            },
        }
        if mode == "programming":
            self._chat_with_approved_tools(ollama_payload)
            return

        request = Request(
            f"{OLLAMA_URL}/api/chat",
            data=json.dumps(ollama_payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            response = urlopen(request, timeout=600)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            self._send_json(502, {"error": f"Ollama درخواست را نپذیرفت: {detail[:500]}"})
            return
        except (URLError, TimeoutError, OSError) as exc:
            self._send_json(503, {"error": f"ارتباط با Ollama برقرار نشد: {exc}"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            with response:
                for line in response:
                    if line:
                        self.wfile.write(line)
                        self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    @staticmethod
    @register_capability(
        id="local_chat",
        name="گفت‌وگوی مدل محلی",
        description="پاسخ‌گویی متنی با مدل‌های نصب‌شده در Ollama.",
        purpose="انجام گفت‌وگوی عمومی بدون ارسال پیام‌ها به سرویس مدل ابری.",
        activation_conditions=("گفت‌وگو", "چت", "پرسش", "explain", "help"),
        inputs=("پیام‌های متنی", "شناسهٔ مدل Ollama"),
        outputs=("پاسخ جریانی مدل",),
        dependencies=("Ollama روی 127.0.0.1:11434", "مدل محلی نصب‌شده"),
        limitations=("پاسخ به کیفیت و دانش مدل انتخابی بستگی دارد.",),
        documentation="local_chat.md",
    )
    def _validate_chat(payload):
        if not isinstance(payload, dict):
            return "ساختار درخواست معتبر نیست."
        model = payload.get("model")
        if not isinstance(model, str) or not MODEL_PATTERN.fullmatch(model):
            return "نام مدل معتبر نیست."
        if payload.get("mode", "general") not in {"general", "programming"}:
            return "حالت گفتگو معتبر نیست."
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages or len(messages) > 100:
            return "تعداد پیام‌ها معتبر نیست."

        total_chars = 0
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in {
                "user",
                "assistant",
                "system",
            }:
                return "ساختار یکی از پیام‌ها معتبر نیست."
            content = message.get("content")
            if not isinstance(content, str) or not content.strip() or len(content) > 20000:
                return "متن یکی از پیام‌ها خالی یا بیش از حد طولانی است."
            total_chars += len(content)
        if total_chars > 150000:
            return "مجموع متن پیام‌ها بیش از حد مجاز است."
        return None


def main():
    address = ("127.0.0.1", 8000)
    server = ThreadingHTTPServer(address, ChatHandler)
    print("Local chat ready at http://127.0.0.1:8000")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
