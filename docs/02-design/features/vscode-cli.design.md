# Design: VSCode CLI Command for OFKMS v2

> Feature: `vscode-cli`
> Plan: `docs/01-plan/features/vscode-cli.plan.md`
> Created: 2026-03-04
> Status: Draft

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│  VSCode Terminal                                        │
│                                                         │
│  $ ofkms ask "BMSとMFSの違い"                           │
│  $ ofkms health                                         │
│  $ ofkms login                                          │
│                                                         │
└────────────────────┬────────────────────────────────────┘
                     │ (python -m cli)
┌────────────────────▼────────────────────────────────────┐
│  CLI Layer (cli/)                                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │
│  │ commands │ │ client   │ │ config   │ │ display   │  │
│  │ (click)  │ │ (httpx)  │ │ (json)   │ │ (rich)    │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘  │
└────────────────────┬────────────────────────────────────┘
                     │ HTTP (X-API-Key header)
┌────────────────────▼────────────────────────────────────┐
│  OFKMS v2 REST API (api/server.py)                     │
│  http://192.168.8.11:8000                              │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌──────────────┐    │
│  │/health │ │/query  │ │/query/ │ │/admin/login  │    │
│  │        │ │        │ │ stream │ │/admin/keys   │    │
│  └────────┘ └────────┘ └────────┘ └──────────────┘    │
└─────────────────────────────────────────────────────────┘
```

## 2. File Structure

```
cli/
├── __init__.py              # Package init
├── __main__.py              # Entry point: python -m cli → main()
├── client.py                # OFKMSClient — httpx wrapper for all API calls
├── config.py                # ConfigManager — ~/.ofkms/config.json CRUD
├── display.py               # Rich output formatting (tables, markdown, panels)
└── commands/
    ├── __init__.py           # Click group registration
    ├── ask.py                # ofkms ask "query" [--stream] [--product] [--lang]
    ├── health.py             # ofkms health
    ├── products.py           # ofkms products
    ├── auth.py               # ofkms login / ofkms keys {list|create|revoke}
    └── config_cmd.py         # ofkms config {show|set|reset}
```

Total: **10 files** to create

## 3. Module Design

### 3.1 `cli/config.py` — ConfigManager

Config 파일 경로: `~/.ofkms/config.json`

```python
@dataclass
class CLIConfig:
    api_url: str = "http://localhost:8000"
    api_key: str | None = None
    default_language: str | None = None    # ja|ko|en or None (auto)
    default_product: str | None = None
    stream: bool = True

class ConfigManager:
    CONFIG_DIR = Path.home() / ".ofkms"
    CONFIG_FILE = CONFIG_DIR / "config.json"

    @classmethod
    def load() -> CLIConfig

    @classmethod
    def save(config: CLIConfig) -> None

    @classmethod
    def get(key: str) -> str | None

    @classmethod
    def set(key: str, value: str) -> None

    @classmethod
    def reset() -> None          # Delete config file
```

**Valid config keys**: `api-url`, `api-key`, `default-language`, `default-product`, `stream`

### 3.2 `cli/client.py` — OFKMSClient

모든 REST API 호출을 캡슐화. `httpx.AsyncClient` 사용.

```python
class OFKMSClient:
    def __init__(self, api_url: str, api_key: str | None = None):
        self._base = api_url.rstrip("/")
        self._api_key = api_key

    def _headers(self) -> dict:
        """Return headers with X-API-Key if set."""
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["X-API-Key"] = self._api_key
        return h

    # ── Public endpoints (no auth) ──

    async def health(self) -> dict:
        """GET /v1/health → HealthResponse"""

    # ── Authenticated endpoints ──

    async def products(self) -> dict:
        """GET /v1/products → ProductsResponse"""

    async def query(self, query: str, language: str | None,
                    product: str | None, include_sources: bool = True,
                    include_phases: bool = False) -> dict:
        """POST /v1/query → QueryResponse"""

    async def query_stream(self, query: str, language: str | None,
                           product: str | None) -> AsyncGenerator[dict, None]:
        """POST /v1/query/stream → SSE events

        Yields dicts with keys: event, data
        Events: 'phase', 'answer', 'done', 'error'
        """

    async def login(self, username: str, password: str) -> dict:
        """POST /v1/admin/login → LoginResponse"""

    async def create_key(self, name: str | None = None) -> dict:
        """POST /v1/admin/keys → KeyCreateResponse"""

    async def list_keys(self) -> dict:
        """GET /v1/admin/keys → KeyListResponse"""

    async def revoke_key(self, key_id: int) -> dict:
        """DELETE /v1/admin/keys/{key_id}"""
```

#### SSE 스트리밍 파싱 규격

서버가 보내는 SSE 이벤트 형식:
```
event: phase
data: {"phase": 0, "name": "query_analysis", "status": "complete", "time_ms": 5}

event: phase
data: {"phase": 1, "name": "embedding_search", "status": "complete", "time_ms": 450}

event: phase
data: {"phase": 2, "name": "domain_knowledge", "status": "complete", "time_ms": 1200}

event: phase
data: {"phase": 3, "name": "ofcode_web", "status": "complete", "time_ms": 300}

event: answer
data: {"answer": "## BMS vs MFS\n...", "confidence": 0.85, "language": "ja", "intent": "comparison", "product": "openframe_osc_7"}

event: done
data: {"total_time_ms": 2100}
```

`httpx`의 `stream()` + line-by-line SSE 파싱:
```python
async def query_stream(self, ...):
    async with httpx.AsyncClient(...) as client:
        async with client.stream("POST", url, json=body, headers=self._headers()) as resp:
            event_type = None
            for line in resp.aiter_lines():
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    yield {"event": event_type, "data": data}
```

#### 에러 처리

| HTTP Status | Client 동작 |
|---|---|
| 401 | `AuthenticationError` raise → "API 키가 유효하지 않습니다. `ofkms login` 실행" |
| 403 | `PermissionError` raise → "권한이 부족합니다" |
| 404 | `NotFoundError` raise |
| 500 | `ServerError` raise → "서버 오류. `ofkms health`로 상태 확인" |
| Connection Error | `ConnectionError` → "서버에 연결할 수 없습니다: {api_url}" |

### 3.3 `cli/display.py` — Rich Display

```python
class Display:
    console = Console()

    @staticmethod
    def answer(response: dict) -> None:
        """Format query response with markdown, metadata, sources."""
        # 1. Answer panel (Rich Markdown)
        # 2. Metadata table (confidence, intent, product, time)
        # 3. Sources table (document, page, score)

    @staticmethod
    def answer_streaming(event: dict) -> None:
        """Display single SSE event during streaming."""
        # phase event → spinner "Phase N: name... ✓ (Nms)"
        # answer event → Rich Markdown panel
        # done event → total time

    @staticmethod
    def health(response: dict) -> None:
        """Format health check as colored status table."""
        # Service | Status | Latency
        # llm_qwen3 | ✓ ok | 45ms
        # bge_m3 | ✗ error | —

    @staticmethod
    def products(response: dict) -> None:
        """Format product list as table."""
        # ID | Name | Keywords

    @staticmethod
    def keys(response: dict) -> None:
        """Format API keys as table."""
        # ID | Prefix | Name | Active | Created | Last Used

    @staticmethod
    def error(message: str, hint: str | None = None) -> None:
        """Display error with optional hint."""

    @staticmethod
    def success(message: str) -> None:
        """Display success message."""

    @staticmethod
    def key_created(response: dict) -> None:
        """Display new API key with copy-paste highlight."""
```

### 3.4 `cli/commands/` — Click Commands

#### 3.4.1 `commands/__init__.py` — CLI Group

```python
import click
from cli.commands.ask import ask
from cli.commands.health import health
from cli.commands.products import products
from cli.commands.auth import login, keys
from cli.commands.config_cmd import config

@click.group()
@click.version_option(version="2.0.0", prog_name="ofkms")
def cli():
    """OFKMS v2 — OpenFrame Knowledge Management CLI"""
    pass

cli.add_command(ask)
cli.add_command(health)
cli.add_command(products)
cli.add_command(login)
cli.add_command(keys)
cli.add_command(config)
```

#### 3.4.2 `commands/ask.py`

```python
@click.command()
@click.argument("query")
@click.option("--stream/--no-stream", default=None,
              help="Enable/disable streaming (default: config value)")
@click.option("--product", "-p", default=None,
              help="Product filter (e.g. mvs_openframe_7.1)")
@click.option("--lang", "-l", default=None,
              help="Response language: ja|ko|en")
@click.option("--phases", is_flag=True,
              help="Include phase timing details")
def ask(query, stream, product, lang, phases):
    """Ask a question about OpenFrame products."""
    asyncio.run(_ask(query, stream, product, lang, phases))

async def _ask(query, stream, product, lang, phases):
    cfg = ConfigManager.load()
    # stream default: config.stream if --stream not specified
    use_stream = stream if stream is not None else cfg.stream

    client = OFKMSClient(cfg.api_url, cfg.api_key)

    if use_stream:
        # SSE streaming mode
        with Live(...) as live:
            async for event in client.query_stream(query, lang or cfg.default_language,
                                                    product or cfg.default_product):
                Display.answer_streaming(event)
    else:
        # Synchronous mode
        with console.status("Searching..."):
            resp = await client.query(query, lang or cfg.default_language,
                                       product or cfg.default_product,
                                       include_phases=phases)
        Display.answer(resp)
```

#### 3.4.3 `commands/health.py`

```python
@click.command()
def health():
    """Check OFKMS API and infrastructure health."""
    asyncio.run(_health())

async def _health():
    cfg = ConfigManager.load()
    client = OFKMSClient(cfg.api_url)  # No API key needed
    resp = await client.health()
    Display.health(resp)
```

#### 3.4.4 `commands/products.py`

```python
@click.command()
def products():
    """List supported OpenFrame products."""
    asyncio.run(_products())

async def _products():
    cfg = ConfigManager.load()
    client = OFKMSClient(cfg.api_url, cfg.api_key)
    resp = await client.products()
    Display.products(resp)
```

#### 3.4.5 `commands/auth.py`

```python
@click.command()
@click.option("--username", "-u", prompt=True)
@click.option("--password", "-p", prompt=True, hide_input=True)
def login(username, password):
    """Login and auto-create an API key."""
    asyncio.run(_login(username, password))

async def _login(username, password):
    cfg = ConfigManager.load()
    client = OFKMSClient(cfg.api_url)

    # Step 1: Verify credentials
    login_resp = await client.login(username, password)
    Display.success(f"Login successful: {login_resp['username']} ({login_resp['role']})")

    # Step 2: Need temp API key to create new key
    #   login endpoint is exempt from auth, but create_key needs auth
    #   → Ask user if they have an existing key, or use admin workflow
    #
    # Approach: After login verification, prompt for existing API key
    #   or auto-generate one if admin provides initial key
    #   For first-time setup: user provides initial key from admin
    Display.success("Login verified. Set your API key:")
    Display.console.print("  ofkms config set api-key <your-api-key>")

@click.group()
def keys():
    """Manage API keys."""
    pass

@keys.command("list")
def keys_list():
    """List your API keys."""
    asyncio.run(_keys_list())

async def _keys_list():
    cfg = ConfigManager.load()
    client = OFKMSClient(cfg.api_url, cfg.api_key)
    resp = await client.list_keys()
    Display.keys(resp)

@keys.command("create")
@click.option("--name", "-n", default=None, help="Key name")
def keys_create(name):
    """Create a new API key."""
    asyncio.run(_keys_create(name))

async def _keys_create(name):
    cfg = ConfigManager.load()
    client = OFKMSClient(cfg.api_url, cfg.api_key)
    resp = await client.create_key(name)
    Display.key_created(resp)

    # Ask: save as default?
    if click.confirm("Save this key as default?"):
        cfg.api_key = resp["api_key"]
        ConfigManager.save(cfg)
        Display.success("Key saved to config")

@keys.command("revoke")
@click.argument("key_id", type=int)
def keys_revoke(key_id):
    """Revoke an API key by ID."""
    asyncio.run(_keys_revoke(key_id))

async def _keys_revoke(key_id):
    cfg = ConfigManager.load()
    client = OFKMSClient(cfg.api_url, cfg.api_key)
    resp = await client.revoke_key(key_id)
    Display.success(f"Key {key_id} revoked")
```

#### 3.4.6 `commands/config_cmd.py`

```python
@click.group()
def config():
    """Manage CLI configuration."""
    pass

@config.command("show")
def config_show():
    """Show current configuration."""
    cfg = ConfigManager.load()
    table = Table(title="OFKMS CLI Config")
    table.add_column("Key")
    table.add_column("Value")
    table.add_row("api-url", cfg.api_url)
    table.add_row("api-key", cfg.api_key[:16] + "..." if cfg.api_key else "(not set)")
    table.add_row("default-language", cfg.default_language or "(auto)")
    table.add_row("default-product", cfg.default_product or "(all)")
    table.add_row("stream", str(cfg.stream))
    console.print(table)

@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key, value):
    """Set a configuration value."""
    VALID_KEYS = {"api-url", "api-key", "default-language", "default-product", "stream"}
    if key not in VALID_KEYS:
        Display.error(f"Invalid key: {key}", f"Valid keys: {', '.join(sorted(VALID_KEYS))}")
        return
    ConfigManager.set(key, value)
    Display.success(f"{key} = {value}")

@config.command("reset")
def config_reset():
    """Reset configuration to defaults."""
    if click.confirm("Reset all settings?"):
        ConfigManager.reset()
        Display.success("Config reset to defaults")
```

### 3.5 `cli/__main__.py` — Entry Point

```python
"""OFKMS CLI entry point — python -m cli"""
from cli.commands import cli

if __name__ == "__main__":
    cli()
```

## 4. API Contract Mapping

CLI가 사용하는 정확한 API 계약:

### 4.1 `GET /v1/health` (no auth)

**Response** (`HealthResponse`):
```json
{
  "status": "healthy|degraded|unhealthy",
  "services": {
    "llm_qwen3": {"status": "ok|error", "latency_ms": 45},
    "bge_m3": {"status": "ok|error", "latency_ms": 12},
    "neo4j": {"status": "ok|error", "latency_ms": 30},
    "postgresql": {"status": "ok|error", "latency_ms": 8},
    "ofcode": {"status": "ok|error", "latency_ms": 15}
  },
  "version": "2.0.0"
}
```

### 4.2 `GET /v1/products` (auth: X-API-Key)

**Response** (`ProductsResponse`):
```json
{
  "products": [
    {"id": "mvs_openframe_7.1", "name": "MVS OpenFrame 7.1", "keywords": ["mvs", "tjes", ...]}
  ]
}
```

### 4.3 `POST /v1/query` (auth: X-API-Key)

**Request** (`QueryRequest`):
```json
{
  "query": "BMSとMFSの違いを教えてください",
  "language": "ja",
  "product": "openframe_osc_7",
  "include_sources": true,
  "include_phases": false
}
```

**Response** (`QueryResponse`):
```json
{
  "success": true,
  "answer": "## BMS vs MFS\n...",
  "language": "ja",
  "confidence": 0.85,
  "intent": "comparison",
  "product": "openframe_osc_7",
  "sources": [{"document": "OSC_Guide.pdf", "page": 42, "score": 0.92, "type": "neo4j_vector"}],
  "usage": {
    "total_time_ms": 3200,
    "phases_executed": [0, 1, 2, 3, 6],
    "fallback_used": false,
    "phase_times": null
  }
}
```

### 4.4 `POST /v1/query/stream` (auth: X-API-Key)

**Request**: Same as `/v1/query`

**SSE Events**:
```
event: phase
data: {"phase": 0, "name": "query_analysis", "status": "complete", "time_ms": 5}

event: phase
data: {"phase": N, "name": "...", "status": "complete", "time_ms": N}

event: answer
data: {"answer": "...", "confidence": 0.85, "language": "ja", "intent": "...", "product": "..."}

event: done
data: {"total_time_ms": 2100}

event: error    (on failure)
data: {"error": "..."}
```

### 4.5 `POST /v1/admin/login` (no auth)

**Request**: `{"username": "admin", "password": "..."}`
**Response**: `{"message": "Login successful", "user_id": 1, "username": "admin", "role": "admin"}`

### 4.6 `POST /v1/admin/keys` (auth: X-API-Key)

**Request**: `{"name": "vscode-cli"}` (optional)
**Response**: `{"api_key": "ofkms-abc123...", "key_id": 5, "name": "vscode-cli", "message": "Key created..."}`

### 4.7 `GET /v1/admin/keys` (auth: X-API-Key)

**Response**:
```json
{
  "count": 2,
  "keys": [
    {"id": 1, "key_prefix": "ofkms-abc123", "name": "default", "is_active": true,
     "created_at": "...", "last_used_at": "...", "user_id": 1, "username": "admin"}
  ]
}
```

### 4.8 `DELETE /v1/admin/keys/{key_id}` (auth: X-API-Key)

**Response**: `{"message": "Key revoked", "key_id": 3}`

## 5. Display Format Design

### 5.1 `ofkms ask` — Answer Display

```
╭─ OFKMS Answer ───────────────────────────────────────────╮
│                                                           │
│  ## BMS vs MFS                                            │
│                                                           │
│  BMSはCICS/OSCの画面定義マクロで、MFSはIMS/HiDBの         │
│  画面定義マクロです。                                      │
│  ...                                                      │
│                                                           │
╰───────────────────────────────────────────────────────────╯

  Confidence: 0.85 ██████████████████░░ 85%
  Intent: comparison │ Product: openframe_osc_7 │ Time: 3.2s

  Sources:
  ┌───┬──────────────────┬──────┬───────┬─────────────┐
  │ # │ Document         │ Page │ Score │ Type        │
  ├───┼──────────────────┼──────┼───────┼─────────────┤
  │ 1 │ OSC_Guide.pdf    │ 42   │ 0.92  │ neo4j_vector│
  │ 2 │ HiDB_Guide.pdf   │ 15   │ 0.87  │ pg_vector   │
  └───┴──────────────────┴──────┴───────┴─────────────┘
```

### 5.2 `ofkms ask --stream` — Streaming Display

```
  ✓ Phase 0: query_analysis (5ms)
  ✓ Phase 1: embedding_search (450ms)
  ✓ Phase 2: domain_knowledge (1200ms)
  ✓ Phase 3: ofcode_web (300ms)
  ⣾ Phase 6: response_generation...

╭─ OFKMS Answer ───────────────────────────────────────────╮
│  ## BMS vs MFS                                            │
│  ...                                                      │
╰───────────────────────────────────────────────────────────╯
  Total: 2.1s
```

### 5.3 `ofkms health` — Health Display

```
  OFKMS v2.0.0 — Service Health

  ┌──────────────┬────────┬─────────┐
  │ Service      │ Status │ Latency │
  ├──────────────┼────────┼─────────┤
  │ llm_qwen3    │ ✓ ok   │ 45ms    │
  │ bge_m3       │ ✓ ok   │ 12ms    │
  │ neo4j        │ ✓ ok   │ 30ms    │
  │ postgresql   │ ✓ ok   │ 8ms     │
  │ ofcode       │ ✗ err  │ —       │
  └──────────────┴────────┴─────────┘

  Overall: degraded (4/5 services ok)
```

### 5.4 `ofkms products` — Products Display

```
  Supported Products (12)

  ┌─────────────────────────┬──────────────────────────┬──────────────────────────────┐
  │ ID                      │ Name                     │ Keywords                     │
  ├─────────────────────────┼──────────────────────────┼──────────────────────────────┤
  │ mvs_openframe_7.1       │ MVS OpenFrame 7.1        │ mvs, tjes, tacf, jcl, vsam   │
  │ openframe_osc_7         │ OpenFrame OSC 7 (CICS)   │ osc, cics, bms, oscmgr       │
  │ ...                     │ ...                      │ ...                          │
  └─────────────────────────┴──────────────────────────┴──────────────────────────────┘
```

## 6. Error Handling Strategy

```python
# Common error handler wrapper
def handle_errors(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except AuthenticationError:
            Display.error("API key is invalid or expired",
                         "Run: ofkms login  or  ofkms config set api-key <key>")
        except ConnectionError as e:
            Display.error(f"Cannot connect to server: {e}",
                         "Check: ofkms config show  and  ofkms health")
        except ServerError as e:
            Display.error(f"Server error: {e}",
                         "Run: ofkms health")
        except httpx.TimeoutException:
            Display.error("Request timed out",
                         "The server may be under heavy load. Try again.")
    return wrapper
```

## 7. Dependencies

```
# New dependency (add to requirements.txt)
click>=8.1.0

# Already in requirements.txt
httpx>=0.27.0
rich>=13.7.0
```

## 8. Implementation Order

| Step | Files | Description | Depends On |
|------|-------|-------------|------------|
| 1 | `cli/__init__.py`, `cli/config.py` | Config file management | — |
| 2 | `cli/client.py` | API client (all endpoints) | Step 1 |
| 3 | `cli/display.py` | Rich output formatting | — |
| 4 | `cli/commands/__init__.py`, `cli/__main__.py` | Click CLI group + entry point | — |
| 5 | `cli/commands/health.py` | `ofkms health` | Steps 2, 3, 4 |
| 6 | `cli/commands/products.py` | `ofkms products` | Steps 2, 3, 4 |
| 7 | `cli/commands/ask.py` | `ofkms ask` (sync + stream) | Steps 2, 3, 4 |
| 8 | `cli/commands/auth.py` | `ofkms login` + `ofkms keys` | Steps 2, 3, 4 |
| 9 | `cli/commands/config_cmd.py` | `ofkms config` | Steps 1, 3, 4 |
| 10 | `requirements.txt` | Add `click>=8.1.0` | — |

## 9. Testing Strategy

REST API 서버가 동작하는 환경에서 수동 테스트:

```bash
# 1. Config
python -m cli config show
python -m cli config set api-url http://192.168.8.11:8000
python -m cli config set api-key ofkms-xxxxx

# 2. Health (no auth)
python -m cli health

# 3. Products (auth required)
python -m cli products

# 4. Query
python -m cli ask "tjesmgr BOOT コマンドの使い方"
python -m cli ask --stream "BMSとMFSの違い" --product openframe_osc_7
python -m cli ask "OFASM設定方法" --lang ja --no-stream

# 5. Auth
python -m cli login -u admin
python -m cli keys list
python -m cli keys create --name "test-key"
python -m cli keys revoke 5
```
