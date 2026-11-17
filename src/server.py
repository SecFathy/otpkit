"""
otpkit — Temporary Email & SMS MCP Server

Create disposable email addresses and phone numbers for receiving OTPs,
verification links, and confirmation codes. Every email provider works
without an API key. SMS includes free public-number scrapers and optional
paid providers.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

import httpx

try:
    from mcp.server.fastmcp import FastMCP
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as FastMCP  # mcp >= 2.x

mcp = FastMCP(
    "otpkit",
    description=(
        "Temporary email and SMS for receiving OTPs and verification links. "
        "All email providers are free — no API keys required."
    ),
)

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

@dataclass
class EmailAccount:
    id: str
    address: str
    password: str
    token: str
    provider: str
    extra: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass
class SmsOrder:
    order_id: str
    phone: str
    country: str
    service: str
    provider: str
    created_at: float = field(default_factory=time.time)


_emails: dict[str, EmailAccount] = {}
_sms_orders: dict[str, SmsOrder] = {}
_http: httpx.AsyncClient | None = None

EMAIL_PROVIDERS = ["grabmail", "catchmail", "guerrillamail", "maildrop"]
SMS_FREE_SOURCES = ["receive-sms-free"]
SMS_PAID_PROVIDERS = {
    "5sim": "FIVESIM_API_KEY",
    "smspool": "SMSPOOL_API_KEY",
}

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=30, follow_redirects=True)
    return _http


def _ok(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"error": msg})


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

_OTP_RE = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")
_URL_RE = re.compile(r"https?://[^\s\"'<>\]\)]+")
_VERIFY_KW = frozenset([
    "verify", "confirm", "activate", "validate", "token", "code",
    "auth", "register", "signup", "sign-up", "callback", "redirect",
    "reset", "magic", "login", "otp",
])


def _extract_otp(text: str) -> list[str]:
    return _OTP_RE.findall(text)


def _extract_links(text: str) -> list[str]:
    raw = _URL_RE.findall(text)
    verify = [u for u in raw if any(k in u.lower() for k in _VERIFY_KW)]
    other = [u for u in raw if u not in verify]
    return verify + other


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _html_to_text(html: str) -> str:
    ext = _TextExtractor()
    try:
        ext.feed(html)
    except Exception:
        return html
    return ext.get_text()


def _enrich(entry: dict, body: str, html: str = "") -> dict:
    combined = f"{body} {html}"
    entry["otps"] = _extract_otp(combined)
    entry["links"] = _extract_links(combined)
    return entry


# =====================================================================
#  EMAIL BACKENDS
# =====================================================================

# --- Guerrilla Mail --------------------------------------------------

_GUERRILLA = "https://api.guerrillamail.com/ajax.php"


async def _guerrilla_get_address(session_id: str = "") -> dict:
    c = await _client()
    cookies = {"SUBSCR": session_id} if session_id else {}
    r = await c.get(_GUERRILLA, params={"f": "get_email_address", "lang": "en"}, cookies=cookies)
    r.raise_for_status()
    return r.json()


async def _guerrilla_set_user(email_user: str, session_id: str) -> dict:
    c = await _client()
    r = await c.get(
        _GUERRILLA,
        params={"f": "set_email_user", "email_user": email_user, "lang": "en"},
        cookies={"SUBSCR": session_id},
    )
    r.raise_for_status()
    return r.json()


async def _guerrilla_check(session_id: str, seq: int = 0) -> list[dict]:
    c = await _client()
    r = await c.get(_GUERRILLA, params={"f": "check_email", "seq": str(seq)}, cookies={"SUBSCR": session_id})
    r.raise_for_status()
    return r.json().get("list", [])


async def _guerrilla_fetch(session_id: str, email_id: str) -> dict:
    c = await _client()
    r = await c.get(_GUERRILLA, params={"f": "fetch_email", "email_id": email_id}, cookies={"SUBSCR": session_id})
    r.raise_for_status()
    return r.json()


# --- Maildrop.cc (GraphQL) -------------------------------------------

_MAILDROP = "https://api.maildrop.cc/graphql"


async def _maildrop_inbox(mailbox: str) -> list[dict]:
    c = await _client()
    q = '{ inbox(mailbox: "%s") { id headerfrom subject date } }' % mailbox
    r = await c.post(_MAILDROP, json={"query": q})
    r.raise_for_status()
    return r.json().get("data", {}).get("inbox", [])


async def _maildrop_read(mailbox: str, msg_id: str) -> dict:
    c = await _client()
    q = '{ message(mailbox: "%s", id: "%s") { id headerfrom subject date html } }' % (mailbox, msg_id)
    r = await c.post(_MAILDROP, json={"query": q})
    r.raise_for_status()
    return r.json().get("data", {}).get("message", {})


# --- GrabMail.io (REST, no auth) ------------------------------------

_GRABMAIL = "https://grabmail.io/api/v1"
_GRABMAIL_DOMAINS = ["grabmail.io", "mixozia.com", "linqmail.com"]


async def _grabmail_inbox(address: str) -> list[dict]:
    c = await _client()
    r = await c.get(f"{_GRABMAIL}/mailbox", params={"address": address})
    r.raise_for_status()
    return r.json().get("messages", [])


async def _grabmail_read(msg_id: str, mailbox: str) -> dict:
    c = await _client()
    r = await c.get(f"{_GRABMAIL}/message/{msg_id}", params={"mailbox": mailbox})
    r.raise_for_status()
    return r.json()


# --- CatchMail.io (REST, no auth) ------------------------------------

_CATCHMAIL = "https://api.catchmail.io/api/v1"


async def _catchmail_inbox(address: str) -> list[dict]:
    c = await _client()
    r = await c.get(f"{_CATCHMAIL}/mailbox", params={"address": address})
    r.raise_for_status()
    return r.json().get("messages", [])


async def _catchmail_read(msg_id: str, mailbox: str) -> dict:
    c = await _client()
    r = await c.get(f"{_CATCHMAIL}/message/{msg_id}", params={"mailbox": mailbox})
    r.raise_for_status()
    return r.json()


# =====================================================================
#  SMS BACKENDS — free (scrape public numbers)
# =====================================================================

async def _receivesmsfree_numbers() -> list[dict]:
    c = await _client()
    r = await c.get("https://receive-sms-free.cc/", headers=_BROWSER_HEADERS)
    r.raise_for_status()
    numbers: list[dict] = []
    for m in re.finditer(r'href="/(Free-[^/]+)/(\d+)/"', r.text):
        country = m.group(1).replace("Free-", "").replace("-Phone-Number", "").replace("-", " ")
        numbers.append({"phone": m.group(2), "country": country})
    return numbers


async def _receivesmsfree_messages(phone: str) -> list[dict]:
    """Parse sms-item divs from receive-sms-free.cc."""
    c = await _client()
    for prefix in ["Free-USA-Phone-Number", "Free-UK-Phone-Number",
                    "Free-Canada-Phone-Number", "Free-France-Phone-Number"]:
        r = await c.get(f"https://receive-sms-free.cc/{prefix}/{phone}/", headers=_BROWSER_HEADERS)
        if r.status_code == 200 and "sms-item" in r.text:
            break
    r.raise_for_status()
    return _parse_sms_items(r.text)


def _parse_sms_items(html: str) -> list[dict]:
    """Parse the sms-item div layout used by receive-sms-free.cc and similar sites.

    Structure:
      <div class="sms-item ...">
        <span class="sender-badge">SENDER</span>
        <span class="time-text ...">TIME</span>
        <p class="sms-content ...">MESSAGE</p>
      </div>
    """
    messages: list[dict] = []
    for block in re.finditer(r'<div[^>]*class="sms-item[^"]*"[^>]*>(.*?)</div>\s*(?=<div|$)', html, re.DOTALL):
        inner = block.group(1)
        sender = ""
        date = ""
        text = ""
        sender_m = re.search(r'class="sender-badge[^"]*"[^>]*>([^<]+)<', inner)
        if sender_m:
            sender = sender_m.group(1).strip()
        time_m = re.search(r'class="time-text[^"]*"[^>]*>.*?(?:<[^>]+>)*\s*([^<]+?)\s*<', inner, re.DOTALL)
        if time_m:
            date = time_m.group(1).strip()
        text_m = re.search(r'class="sms-content[^"]*"[^>]*>(.*?)</p>', inner, re.DOTALL)
        if text_m:
            text = _html_to_text(text_m.group(1)).strip()
        if text:
            messages.append({
                "from": sender, "text": text,
                "date": date, "otps": _extract_otp(text),
            })
    return messages


# =====================================================================
#  SMS BACKENDS — paid (optional)
# =====================================================================

def _require_key(provider: str) -> str:
    env_var = SMS_PAID_PROVIDERS[provider]
    key = os.environ.get(env_var, "")
    if not key:
        raise ValueError(f"{env_var} environment variable is not set. Get a key from {provider}.")
    return key


async def _fivesim_buy(country: str, service: str) -> SmsOrder:
    key = _require_key("5sim")
    c = await _client()
    r = await c.get(
        f"https://5sim.net/v1/user/buy/activation/{country}/{service}/any",
        headers={"Authorization": f"Bearer {key}"},
    )
    r.raise_for_status()
    d = r.json()
    order = SmsOrder(order_id=str(d["id"]), phone=d["phone"], country=country, service=service, provider="5sim")
    _sms_orders[order.order_id] = order
    return order


async def _fivesim_check(order_id: str) -> dict:
    key = _require_key("5sim")
    c = await _client()
    r = await c.get(f"https://5sim.net/v1/user/check/{order_id}", headers={"Authorization": f"Bearer {key}"})
    r.raise_for_status()
    return r.json()


async def _smspool_buy(country: str, service: str) -> SmsOrder:
    key = _require_key("smspool")
    c = await _client()
    r = await c.post("https://api.smspool.net/purchase/sms", data={"key": key, "country": country, "service": service})
    r.raise_for_status()
    d = r.json()
    order = SmsOrder(order_id=str(d["order_id"]), phone=d.get("number", ""), country=country, service=service, provider="smspool")
    _sms_orders[order.order_id] = order
    return order


async def _smspool_check(order_id: str) -> dict:
    key = _require_key("smspool")
    c = await _client()
    r = await c.post("https://api.smspool.net/sms/check", data={"key": key, "orderid": order_id})
    r.raise_for_status()
    return r.json()


# =====================================================================
#  MCP TOOLS — Email
# =====================================================================

@mcp.tool()
async def create_temp_email(provider: str = "grabmail", username: str = "") -> str:
    """Create a temporary email address. All providers are free with no API key.

    Args:
        provider: grabmail | catchmail | guerrillamail | maildrop
        username: Custom username prefix. Random if omitted.

    Returns:
        JSON with the new address and provider name.
    """
    if provider not in EMAIL_PROVIDERS:
        return _err(f"Unknown provider '{provider}'. Choose from: {', '.join(EMAIL_PROVIDERS)}")

    try:
        if provider == "grabmail":
            uname = username or f"tmp{int(time.time())}"
            address = f"{uname}@grabmail.io"
            _emails[address] = EmailAccount(
                id=address, address=address, password="", token="",
                provider="grabmail",
            )
            return _ok({"address": address, "provider": "grabmail",
                         "domains": _GRABMAIL_DOMAINS})

        if provider == "catchmail":
            uname = username or f"tmp{int(time.time())}"
            address = f"{uname}@catchmail.io"
            _emails[address] = EmailAccount(
                id=address, address=address, password="", token="",
                provider="catchmail",
            )
            return _ok({"address": address, "provider": "catchmail"})

        if provider == "guerrillamail":
            data = await _guerrilla_get_address()
            sid = data.get("sid_token", "")
            address = data.get("email_addr", "")
            if username:
                data = await _guerrilla_set_user(username, sid)
                address = data.get("email_addr", address)
                sid = data.get("sid_token", sid)
            _emails[address] = EmailAccount(
                id=sid, address=address, password="", token=sid,
                provider="guerrillamail", extra={"sid": sid, "seq": 0},
            )
            return _ok({"address": address, "provider": "guerrillamail"})

        if provider == "maildrop":
            uname = username or f"tmp{int(time.time())}"
            address = f"{uname}@maildrop.cc"
            _emails[address] = EmailAccount(
                id=uname, address=address, password="", token="",
                provider="maildrop", extra={"mailbox": uname},
            )
            return _ok({"address": address, "provider": "maildrop", "warning": "Public inbox — anyone can read it"})

    except httpx.HTTPStatusError as e:
        return _err(f"{provider}: HTTP {e.response.status_code} — {e.response.text[:200]}")
    except Exception as e:
        return _err(f"{provider}: {e}")

    return _err("Unreachable")


@mcp.tool()
async def list_temp_emails() -> str:
    """List all temporary email addresses created in this session."""
    return _ok([
        {"address": ea.address, "provider": ea.provider, "age_seconds": int(time.time() - ea.created_at)}
        for ea in _emails.values()
    ])


@mcp.tool()
async def check_email(address: str) -> str:
    """Check inbox for new messages. Auto-extracts OTP codes and verification links.

    Args:
        address: Temp email address to check.

    Returns:
        JSON with messages, each containing extracted otps[] and links[].
    """
    ea = _emails.get(address)
    if not ea:
        return _err(f"Unknown address '{address}'. Create it first with create_temp_email.")

    try:
        results: list[dict] = []

        if ea.provider == "grabmail":
            for m in (await _grabmail_inbox(address))[:10]:
                full = await _grabmail_read(m["id"], address)
                body = full.get("text", "") or ""
                html = full.get("html", "") or ""
                results.append(_enrich(
                    {"id": m["id"], "from": m.get("from", ""), "subject": m.get("subject", ""), "body": body},
                    body, html,
                ))

        elif ea.provider == "catchmail":
            for m in (await _catchmail_inbox(address))[:10]:
                full = await _catchmail_read(m["id"], address)
                body_obj = full.get("body", {})
                body = body_obj.get("text", "") if isinstance(body_obj, dict) else str(body_obj)
                html = body_obj.get("html", "") if isinstance(body_obj, dict) else ""
                results.append(_enrich(
                    {"id": m["id"], "from": m.get("from", ""), "subject": m.get("subject", ""), "body": body},
                    body, html,
                ))

        elif ea.provider == "guerrillamail":
            sid = ea.extra.get("sid", ea.token)
            for m in (await _guerrilla_check(sid, ea.extra.get("seq", 0)))[:10]:
                full = await _guerrilla_fetch(sid, str(m.get("mail_id", "")))
                raw_body = full.get("mail_body", "")
                text = _html_to_text(raw_body)
                results.append(_enrich(
                    {"id": m.get("mail_id"), "from": m.get("mail_from", ""), "subject": m.get("mail_subject", ""), "body": text},
                    text, raw_body,
                ))

        elif ea.provider == "maildrop":
            mailbox = ea.extra.get("mailbox", ea.id)
            for m in (await _maildrop_inbox(mailbox))[:10]:
                full = await _maildrop_read(mailbox, m["id"])
                html = full.get("html", "") or ""
                text = _html_to_text(html)
                results.append(_enrich(
                    {"id": m["id"], "from": m.get("headerfrom", ""), "subject": m.get("subject", ""), "body": text},
                    text, html,
                ))

        return _ok({"address": address, "provider": ea.provider, "count": len(results), "messages": results})

    except httpx.HTTPStatusError as e:
        return _err(f"HTTP {e.response.status_code} — {e.response.text[:200]}")
    except Exception as e:
        return _err(str(e))


@mcp.tool()
async def wait_for_email(address: str, timeout: int = 60, poll_interval: int = 5) -> str:
    """Poll until a new email arrives or timeout.

    Args:
        address: Temp email address to watch.
        timeout: Max seconds to wait (default 60, max 120).
        poll_interval: Seconds between polls (default 5, min 3).

    Returns:
        JSON with the new message including extracted otps[] and links[], or timeout status.
    """
    ea = _emails.get(address)
    if not ea:
        return _err(f"Unknown address '{address}'")

    timeout = min(max(timeout, 5), 120)
    poll_interval = max(poll_interval, 3)
    start = time.time()

    initial = json.loads(await check_email(address))
    seen_ids = {str(m.get("id", "")) for m in initial.get("messages", [])}

    while time.time() - start < timeout:
        await asyncio.sleep(poll_interval)
        current = json.loads(await check_email(address))
        for m in current.get("messages", []):
            if str(m.get("id", "")) not in seen_ids:
                return _ok({
                    "status": "received",
                    "waited_seconds": int(time.time() - start),
                    "from": m.get("from", ""),
                    "subject": m.get("subject", ""),
                    "body": m.get("body", ""),
                    "otps": m.get("otps", []),
                    "links": m.get("links", []),
                })

    return _ok({"status": "timeout", "waited_seconds": int(time.time() - start)})


# =====================================================================
#  MCP TOOLS — SMS (free)
# =====================================================================

@mcp.tool()
async def list_free_sms_numbers() -> str:
    """List available free public phone numbers for receiving SMS. No auth needed.

    Returns:
        JSON with available phone numbers from receive-sms-free.cc.
        These are shared public numbers — anyone can read messages.
    """
    try:
        numbers = await _receivesmsfree_numbers()
        return _ok({
            "source": "receive-sms-free.cc", "count": len(numbers), "numbers": numbers,
            "warning": "Public shared numbers — anyone can read messages",
        })
    except Exception as e:
        return _err(f"receive-sms-free: {e}")


@mcp.tool()
async def read_free_sms(phone: str) -> str:
    """Read recent SMS messages on a free public phone number. No auth needed.

    Args:
        phone: Phone number (digits only, from list_free_sms_numbers).
    """
    try:
        msgs = await _receivesmsfree_messages(phone)
        return _ok({"phone": phone, "source": "receive-sms-free.cc", "count": len(msgs), "messages": msgs[:25]})
    except Exception as e:
        return _err(str(e))


@mcp.tool()
async def wait_for_sms(
    phone: str,
    timeout: int = 120,
    poll_interval: int = 10,
) -> str:
    """Poll until a new SMS arrives on a free public number or timeout.

    Args:
        phone: Phone number to watch.
        timeout: Max seconds to wait (default 120, max 300).
        poll_interval: Seconds between polls (default 10, min 5).
    """
    timeout = min(max(timeout, 10), 300)
    poll_interval = max(poll_interval, 5)
    start = time.time()

    initial = json.loads(await read_free_sms(phone))
    seen = {m.get("text", "") + m.get("date", "") for m in initial.get("messages", [])}

    while time.time() - start < timeout:
        await asyncio.sleep(poll_interval)
        current = json.loads(await read_free_sms(phone))
        for m in current.get("messages", []):
            key = m.get("text", "") + m.get("date", "")
            if key not in seen:
                return _ok({
                    "status": "received",
                    "waited_seconds": int(time.time() - start),
                    "from": m.get("from", ""),
                    "text": m.get("text", ""),
                    "otps": m.get("otps", []),
                })

    return _ok({"status": "timeout", "waited_seconds": int(time.time() - start)})


# =====================================================================
#  MCP TOOLS — SMS (paid, optional)
# =====================================================================

@mcp.tool()
async def get_sms_providers() -> str:
    """List all SMS providers and their configuration status."""
    providers: list[dict] = [
        {"name": "receive-sms-free", "type": "free", "auth": "none",
         "site": "receive-sms-free.cc", "ready": True,
         "note": "Public shared numbers — anyone can read messages"},
    ]
    for name, env_var in SMS_PAID_PROVIDERS.items():
        providers.append({
            "name": name, "type": "paid", "env_var": env_var,
            "ready": bool(os.environ.get(env_var)),
        })
    return _ok(providers)


@mcp.tool()
async def buy_temp_number(country: str, service: str, provider: str = "5sim") -> str:
    """Buy a private temporary phone number (paid, requires API key in env).

    Args:
        country: Country code — e.g. "russia", "usa", "india".
        service: Target service — e.g. "telegram", "whatsapp", "google", "any".
        provider: 5sim | smspool
    """
    if provider not in SMS_PAID_PROVIDERS:
        return _err(f"Unknown provider '{provider}'. Choose from: {', '.join(SMS_PAID_PROVIDERS)}")
    try:
        order = await ({"5sim": _fivesim_buy, "smspool": _smspool_buy}[provider])(country, service)
        return _ok({"order_id": order.order_id, "phone": order.phone, "provider": order.provider})
    except ValueError as e:
        return _err(str(e))
    except httpx.HTTPStatusError as e:
        return _err(f"HTTP {e.response.status_code} — {e.response.text[:200]}")


@mcp.tool()
async def check_paid_sms(order_id: str) -> str:
    """Check if an SMS arrived on a paid temporary number.

    Args:
        order_id: Order ID from buy_temp_number.
    """
    order = _sms_orders.get(order_id)
    if not order:
        return _err(f"Unknown order '{order_id}'")
    try:
        if order.provider == "5sim":
            d = await _fivesim_check(order_id)
            codes: list[str] = []
            for s in d.get("sms", []):
                codes.extend(_extract_otp(s.get("text", "") or s.get("code", "")))
            return _ok({"status": d.get("status"), "phone": order.phone, "sms": d.get("sms", []), "otps": codes})

        if order.provider == "smspool":
            d = await _smspool_check(order_id)
            text = d.get("sms", "") or d.get("message", "")
            return _ok({"status": d.get("status"), "phone": order.phone, "sms": text, "otps": _extract_otp(text)})

    except httpx.HTTPStatusError as e:
        return _err(f"HTTP {e.response.status_code}")

    return _err("Unknown provider")


# =====================================================================
#  MCP TOOLS — Utility
# =====================================================================

@mcp.tool()
async def extract_otp_from_text(text: str) -> str:
    """Extract OTP codes (4-8 digits) and verification links from any text.

    Args:
        text: Raw text to scan — email body, SMS content, HTML, etc.
    """
    return _ok({"otps": _extract_otp(text), "links": _extract_links(text)})


# =====================================================================
#  Entrypoint
# =====================================================================

def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
