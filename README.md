<p align="center">
  <img src="banner.png" alt="otpKit — MCP server for temporary email and SMS" width="100%">
</p>

<h1 align="center">otpkit</h1>

<p align="center">
An MCP server that creates disposable email addresses and phone numbers, checks their inboxes, and automatically extracts OTP codes and verification links from incoming messages.
</p>

**Zero API keys required** — every email provider and the free SMS source work without authentication.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Providers](#providers)
- [Installation](#installation)
- [Setup](#setup)
- [Tools Reference](#tools-reference)
- [Usage Examples](#usage-examples)
- [Architecture](#architecture)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## How It Works

otpkit runs as an [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server. Any MCP-compatible client — Claude Code, Claude Desktop, Cursor, or your own agent — can call its tools to:

1. **Create** a temporary email address or get a free public phone number
2. **Wait** for an incoming message (email or SMS)
3. **Receive** the message with OTP codes and verification links already extracted

```
Agent                          otpkit                         Provider
  │                              │                              │
  ├─ create_temp_email() ───────►│                              │
  │◄─ {"address": "x@grab..."}──┤                              │
  │                              │                              │
  │   [uses address to sign up on a website]                    │
  │                              │                              │
  ├─ wait_for_email() ──────────►│──── poll every 5s ──────────►│
  │                              │◄─── new message ─────────────┤
  │                              │                              │
  │                              │  extract OTPs + links        │
  │                              │                              │
  │◄─ {"otps": ["847291"]} ─────┤                              │
  │                              │                              │
  │   [enters OTP on the website]                               │
```

All extraction happens automatically — every message is scanned for:
- **OTP codes**: 4–8 digit numbers (e.g. `847291`, `1234`, `98765432`)
- **Verification links**: URLs containing keywords like `verify`, `confirm`, `activate`, `token`, `reset`, `login` — ranked first in results

---

## Providers

### Email (all free, no API key)

| Provider | Domain(s) | Privacy | Expiry | Method |
|---|---|---|---|---|
| `grabmail` | grabmail.io, mixozia.com, linqmail.com | Private | None | REST |
| `catchmail` | catchmail.io | Private | None | REST |
| `guerrillamail` | guerrillamailblock.com + others | Private | ~1 hour | REST |
| `maildrop` | maildrop.cc | **Public** | None | GraphQL |

**Choosing a provider:**
- **grabmail** (default) — best all-round choice. Three domains, no expiry, clean REST API, works worldwide.
- **catchmail** — same quality as grabmail, different domain. Good as a backup.
- **guerrillamail** — private with session-based auth. Addresses expire after ~1 hour.
- **maildrop** — anyone can read any inbox. Use only for non-sensitive tests.

### SMS — Free (no auth)

| Source | Site | Type |
|---|---|---|
| receive-sms-free.cc | [receive-sms-free.cc](https://receive-sms-free.cc) | Public shared numbers |

> **Warning:** Free SMS numbers are publicly shared — anyone can see the messages. Never use them for real accounts or sensitive services.

### SMS — Paid (optional, private numbers)

| Provider | Env Variable | Site |
|---|---|---|
| `5sim` | `FIVESIM_API_KEY` | [5sim.net](https://5sim.net) |
| `smspool` | `SMSPOOL_API_KEY` | [smspool.net](https://smspool.net) |

Paid providers give you a **private** temporary number that only you can read. Set the API key as an environment variable to enable them.

---

## Installation

### From GitHub

```bash
pip install git+https://github.com/secfathy/otpkit.git
```

### From source

```bash
git clone https://github.com/secfathy/otpkit.git
cd otpkit
pip install -e .
```

### Verify installation

```bash
otpkit
# Server starts on stdio — press Ctrl+C to stop
```

---

## Setup

### Claude Code (CLI)

One command — no config file needed:

```bash
claude mcp add otpkit -- otpkit
```

Or using the Python module directly:

```bash
claude mcp add otpkit -- python -m src.server
```

To verify it's registered:

```bash
claude mcp list
```

Now in any Claude Code session you can say:
> "Create a temp email and wait for the verification code"

Claude will call `create_temp_email`, `wait_for_email`, and return the OTP automatically.

### Claude Desktop (macOS / Windows)

1. Open Claude Desktop settings
2. Go to **Developer** → **Edit Config**
3. Add otpkit to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "otpkit": {
      "command": "otpkit"
    }
  }
}
```

4. Restart Claude Desktop

The otpkit tools now appear in Claude's tool list (hammer icon).

### Claude Desktop with paid SMS

```json
{
  "mcpServers": {
    "otpkit": {
      "command": "otpkit",
      "env": {
        "FIVESIM_API_KEY": "your-key-here",
        "SMSPOOL_API_KEY": "your-key-here"
      }
    }
  }
}
```

### Cursor

Add to `.cursor/mcp.json` in your project root (or global `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "otpkit": {
      "command": "otpkit"
    }
  }
}
```

Restart Cursor. The tools appear in Cursor's MCP panel.

### Windsurf

Add to `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "otpkit": {
      "command": "otpkit"
    }
  }
}
```

### Cline (VS Code)

1. Open VS Code → Cline sidebar → **MCP Servers**
2. Click **Edit MCP Settings**
3. Add:

```json
{
  "mcpServers": {
    "otpkit": {
      "command": "otpkit"
    }
  }
}
```

### Continue (VS Code / JetBrains)

Add to `~/.continue/config.yaml`:

```yaml
mcpServers:
  - name: otpkit
    command: otpkit
```

### Zed

Add to Zed settings (`settings.json`):

```json
{
  "context_servers": {
    "otpkit": {
      "command": {
        "path": "otpkit"
      }
    }
  }
}
```

### OpenAI Agents SDK

```python
from openai_agents import Agent, MCPServerStdio

otpkit = MCPServerStdio(command="otpkit")

agent = Agent(
    name="signup-agent",
    instructions="Use otpkit to create temp emails and extract OTPs.",
    mcp_servers=[otpkit],
)
```

### Claude API (Anthropic SDK) with MCP

```python
import anthropic
from anthropic.types import MCPServerStdio

client = anthropic.Anthropic()

server = MCPServerStdio(command="otpkit")

# Use with tool_use in Messages API
# The server exposes 11 tools that Claude can call
```

### Any MCP Client (Generic)

otpkit uses **stdio transport** — it reads JSON-RPC from stdin and writes to stdout. Any MCP-compatible client can use it:

```bash
# Command to run:
otpkit

# Or with Python directly:
python -m src.server
```

The server exposes 11 tools. Use your client's MCP configuration to point at the `otpkit` command.

### Smithery Registry

If you use the [Smithery](https://smithery.ai) MCP registry:

```bash
npx @smithery/cli install otpkit --client claude
```

---

## Tools Reference

### Email Tools

#### `create_temp_email`

Create a disposable email address.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `provider` | string | `"grabmail"` | `grabmail` \| `catchmail` \| `guerrillamail` \| `maildrop` |
| `username` | string | random | Custom username prefix (e.g. `"john"` → `john@grabmail.io`) |

**Returns:**
```json
{
  "address": "tmp1234567@grabmail.io",
  "provider": "grabmail",
  "domains": ["grabmail.io", "mixozia.com", "linqmail.com"]
}
```

#### `check_email`

Check inbox for new messages. Every message is automatically enriched with extracted OTPs and links.

| Parameter | Type | Description |
|---|---|---|
| `address` | string | Email address created with `create_temp_email` |

**Returns:**
```json
{
  "address": "tmp1234567@grabmail.io",
  "provider": "grabmail",
  "count": 1,
  "messages": [
    {
      "id": "abc123",
      "from": "noreply@example.com",
      "subject": "Verify your email",
      "body": "Your verification code is 847291",
      "otps": ["847291"],
      "links": ["https://example.com/verify?token=xyz"]
    }
  ]
}
```

#### `wait_for_email`

Block until a new email arrives or timeout. Polls automatically.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `address` | string | — | Email address to watch |
| `timeout` | int | `60` | Max seconds to wait (max 120) |
| `poll_interval` | int | `5` | Seconds between polls (min 3) |

**Returns** (on success):
```json
{
  "status": "received",
  "waited_seconds": 12,
  "from": "noreply@example.com",
  "subject": "Verify your email",
  "body": "Your code is 847291",
  "otps": ["847291"],
  "links": ["https://example.com/verify?token=xyz"]
}
```

**Returns** (on timeout):
```json
{
  "status": "timeout",
  "waited_seconds": 60
}
```

#### `list_temp_emails`

List all email addresses created in this session.

**Returns:**
```json
[
  {
    "address": "tmp1234567@grabmail.io",
    "provider": "grabmail",
    "age_seconds": 45
  }
]
```

### SMS Tools — Free

#### `list_free_sms_numbers`

List available public phone numbers for receiving SMS. No parameters needed.

**Returns:**
```json
{
  "source": "receive-sms-free.cc",
  "count": 24,
  "numbers": [
    {"phone": "12543207594", "country": "USA"},
    {"phone": "447700900000", "country": "UK"}
  ],
  "warning": "Public shared numbers — anyone can read messages"
}
```

#### `read_free_sms`

Read recent SMS messages on a public number. Each message is scanned for OTP codes.

| Parameter | Type | Description |
|---|---|---|
| `phone` | string | Phone number from `list_free_sms_numbers` |

**Returns:**
```json
{
  "phone": "12543207594",
  "source": "receive-sms-free.cc",
  "count": 20,
  "messages": [
    {
      "from": "19543583114",
      "text": "Your Uber code is 4829",
      "date": "2 min ago",
      "otps": ["4829"]
    }
  ]
}
```

#### `wait_for_sms`

Poll until a new SMS arrives on a free public number or timeout.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `phone` | string | — | Phone number to watch |
| `timeout` | int | `120` | Max seconds to wait (max 300) |
| `poll_interval` | int | `10` | Seconds between polls (min 5) |

### SMS Tools — Paid

#### `get_sms_providers`

List all SMS providers and whether they are configured.

**Returns:**
```json
[
  {"name": "receive-sms-free", "type": "free", "auth": "none", "ready": true},
  {"name": "5sim", "type": "paid", "env_var": "FIVESIM_API_KEY", "ready": false},
  {"name": "smspool", "type": "paid", "env_var": "SMSPOOL_API_KEY", "ready": false}
]
```

#### `buy_temp_number`

Buy a private temporary phone number.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `country` | string | — | Country code: `"russia"`, `"usa"`, `"india"`, etc. |
| `service` | string | — | Target service: `"telegram"`, `"whatsapp"`, `"google"`, `"any"` |
| `provider` | string | `"5sim"` | `5sim` \| `smspool` |

#### `check_paid_sms`

Check if an SMS arrived on a paid number.

| Parameter | Type | Description |
|---|---|---|
| `order_id` | string | Order ID from `buy_temp_number` |

### Utility

#### `extract_otp_from_text`

Extract OTP codes and verification links from any text. Useful for processing messages from other sources.

| Parameter | Type | Description |
|---|---|---|
| `text` | string | Any text to scan |

**Returns:**
```json
{
  "otps": ["847291"],
  "links": ["https://example.com/verify?token=abc123"]
}
```

---

## Usage Examples

### Sign up for a website

```
You: Create a temp email, sign up on example.com, and get the verification code

Agent:
1. create_temp_email(provider="grabmail")
   → {"address": "tmp1794926042@grabmail.io"}

2. [fills registration form with that email]

3. wait_for_email(address="tmp1794926042@grabmail.io", timeout=60)
   → {
       "status": "received",
       "otps": ["847291"],
       "links": ["https://example.com/verify?token=abc123def"]
     }

4. [enters OTP 847291 on the website]
```

### Get an SMS verification code

```
You: Get a phone number and wait for the Telegram verification code

Agent:
1. list_free_sms_numbers()
   → {"count": 24, "numbers": [{"phone": "12543207594", "country": "USA"}, ...]}

2. [uses 12543207594 as the phone number on Telegram]

3. wait_for_sms(phone="12543207594", timeout=120)
   → {
       "status": "received",
       "text": "Telegram code: 48291",
       "otps": ["48291"]
     }
```

### Use a custom email username

```
You: Create a temp email with username "john.doe"

Agent:
1. create_temp_email(provider="grabmail", username="john.doe")
   → {"address": "john.doe@grabmail.io"}
```

### Extract OTP from arbitrary text

```
You: What's the OTP in "Your verification code is 482917. It expires in 10 minutes."

Agent:
1. extract_otp_from_text(text="Your verification code is 482917. It expires in 10 minutes.")
   → {"otps": ["482917"], "links": []}
```

### Private SMS with paid provider

```
You: Buy a private US number for WhatsApp verification

Agent:
1. get_sms_providers()
   → [... {"name": "5sim", "ready": true} ...]

2. buy_temp_number(country="usa", service="whatsapp", provider="5sim")
   → {"order_id": "12345", "phone": "+12025551234"}

3. [uses +12025551234 on WhatsApp]

4. check_paid_sms(order_id="12345")
   → {"status": "RECEIVED", "otps": ["583921"]}
```

---

## Architecture

```
otpkit (MCP Server)
├── Email Backends
│   ├── grabmail.io    ─── REST API, no auth
│   ├── catchmail.io   ─── REST API, no auth
│   ├── guerrillamail  ─── REST API, session-based
│   └── maildrop.cc    ─── GraphQL, no auth
├── SMS Backends
│   ├── receive-sms-free.cc  ─── HTML scraper, no auth
│   ├── 5sim.net             ─── REST API, API key (optional)
│   └── smspool.net          ─── REST API, API key (optional)
├── Extraction Engine
│   ├── OTP regex: 4-8 digit codes
│   └── Link scorer: verification keywords ranked first
└── MCP Transport: stdio
```

- **Python 3.10+** with async/await throughout
- **httpx** for all HTTP requests (async, connection pooling)
- **mcp** SDK — compatible with both v1 (`FastMCP`) and v2 (`MCPServer`)
- **No database** — session state is in-memory only
- **No dependencies** beyond `mcp` and `httpx`

---

## Troubleshooting

### "Unknown address" error

You must create an address with `create_temp_email` before checking it. Addresses only exist for the current session.

### No messages showing up

- **grabmail / catchmail**: Addresses are instant — if no messages appear, the sender hasn't delivered yet. Try `wait_for_email` with a longer timeout.
- **guerrillamail**: Addresses expire after ~1 hour. Create a new one if expired.
- **maildrop**: Public inbox — if someone else is using the same username, you'll see their messages too.

### SMS numbers return 0 messages

Free SMS numbers are scraped from a public site. If the site layout changes or the number has no recent messages, results may be empty. Try a different number from `list_free_sms_numbers`.

### Paid SMS "API key not set"

Set the environment variable before starting otpkit:

```bash
export FIVESIM_API_KEY="your-key"
otpkit
```

Or pass it through your MCP client config (see [Setup](#setup)).

### MCP import error

otpkit supports both MCP v1 and v2. If you see an import error, update the `mcp` package:

```bash
pip install --upgrade mcp
```

---

## Requirements

- Python 3.10+
- [`mcp`](https://pypi.org/project/mcp/) >= 1.0.0
- [`httpx`](https://pypi.org/project/httpx/) >= 0.27.0

---

## License

MIT
