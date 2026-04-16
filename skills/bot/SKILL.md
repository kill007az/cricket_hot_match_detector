# 🤖 Cricket Hot Match Bot — Implementation Reference

Built across Sprint 3 and Sprint 4. Covers everything about how the bot was designed, built, and how it behaves at runtime.

---

## 1. 🎯 What the bot does

A personal Telegram bot that:

1. **Proactively alerts** at every key match lifecycle event and when a match heats up — fires once per event per match, survives restarts without re-alerting
2. **Serves live analysis on demand** via slash commands (no LLM overhead)
3. **Answers free-text questions** about any live or historical match via a LangGraph ReAct agent backed by Gemini Flash, with **per-chat session memory** so follow-ups work

The bot is a **single-user personal notifier** — no auth, no multi-tenancy. Anyone who sends `/start` can subscribe, but that is by design for personal use.

---

## 2. 🏗️ Architecture

```
Telegram user
     │
     │  commands / free text
     ▼
bot/main.py  (python-telegram-bot v21 Application)
     │
     ├── /start /stop /status /signals /turning /balls /matches
     │         │
     │         ▼
     │   tools.py  ──→  orchestrator API :8080
     │         │
     │   charts.py (for /chart commands)
     │         │
     │         └──→  Telegram reply (text or photo)
     │
     └── free text
               │
               ▼
         agent.py  (LangGraph ReAct + MemorySaver)
               │
               ├── tool calls ──→  tools.py ──→  orchestrator API :8080
               │                        │
               │                   charts.py (side-channel _chart_cache)
               │
               └──→  text reply + chart photos (drained from _chart_cache)

alert_loop.py  (asyncio background task, same process)
     │
     ├── polls /matches/current every 30s (httpx async)
     ├── checks lifecycle conditions (inn1_balls, inn2_balls, match_over)
     ├── checks signal fingerprints against state.seen_fps
     └── broadcasts alerts to all state.subscribed_chats
```

**Key constraint:** The bot never touches `engine_outputs.jsonl`, `ball_events.jsonl`, or the engine API directly. All data flows exclusively through the orchestrator at `:8080`.

---

## 3. 📁 File-by-file breakdown

### `bot/state.py`
Owns all mutable runtime state:
- `subscribed_chats: set[int]` — Telegram chat IDs that receive proactive alerts
- `seen_fps: list[str]` — alert fingerprints already sent (ordered list for trim-oldest logic)

**Persistence:** written to `data/bot_state.json` on every mutation (subscribe, unsubscribe, add_fingerprint). Loaded once at startup via `load()`. Handles missing file gracefully (first boot).

**Fingerprint cap:** trimmed to 1000 entries max — oldest half deleted when exceeded. Prevents unbounded growth in a 24/7 service.

```json
{
  "subscribed_chats": [123456789],
  "seen_fps": [
    "mi_vs_pbks_2026-04-16:INN1_STARTED",
    "mi_vs_pbks_2026-04-16:PRE_MATCH",
    "mi_vs_pbks_2026-04-16:50/50 chase — worth watching from the start"
  ]
}
```

---

### `bot/charts.py`
Three pure functions — input is the history list from `GET /matches/{id}/history`, output is raw PNG bytes. No disk I/O.

| Function | Chart |
|---|---|
| `win_prob_chart(history)` | Win probability line, dashed 50% line, signal vertical lines |
| `hotness_chart(history)` | Hotness line, peak annotated with arrow |
| `forecast_overlay_chart(history)` | Hotness + forecast lines; forecast only drawn where not None; gate line labelled |

**Backend:** `matplotlib.use("Agg")` is the first call in the module — mandatory for headless Docker (no display server). Must be imported before any other matplotlib call.

**Size:** 1200×480px (`figsize=(12, 4.8)`, `dpi=100`). Matches Telegram's photo display proportions.

---

### `bot/llm.py`
LLM factory — single place to configure the language model. Exposes `get_llm()` so `agent.py` and `alert_loop.py` don't hardcode model details.

```python
_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

def get_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(model=_MODEL, temperature=0, google_api_key=...)
```

`GOOGLE_API_KEY` and `GEMINI_MODEL` are both read from env. Default model: `gemini-2.0-flash`. To swap models, change only `.env` — no rebuild needed if using `env_file` in Docker Compose.

**Content-block response handling:** newer Gemini models return `msg.content` as `list[{type, text, extras}]` instead of a plain string. Both `agent.py` and `alert_loop.py` handle both formats:

```python
c = result.content
if isinstance(c, list):
    return " ".join(b["text"] for b in c if isinstance(b, dict) and b.get("type") == "text")
return str(c)
```

---

### `bot/tools.py`
14 synchronous `@tool`-decorated LangChain functions. All use `requests.get` (not httpx — sync is fine; LangGraph wraps them in an executor when called from an async agent).

| Tool | Orchestrator call | Returns |
|---|---|---|
| `get_match_status` | `GET /matches/current` + `/scorecard/1` + `/ball_events` | Full live scoreboard: both innings scores, win%, hotness, forecast, last signal |
| `get_win_prob_curve` | `GET /matches/{id}/history` | Text description; PNG deposited in `_chart_cache["win_prob"]` |
| `get_hotness_curve` | `GET /matches/{id}/history` | Text description; PNG deposited in `_chart_cache["hotness"]` |
| `get_forecast_overlay` | `GET /matches/{id}/history` | Text description; PNG deposited in `_chart_cache["forecast"]` |
| `get_signal_timeline` | `GET /matches/{id}/signals` | Formatted list: `Ball N: signal text` |
| `get_key_turning_points(top_n=5)` | `GET /matches/{id}/history` | Top N balls by abs(Δwin_prob), tagged WICKET/SIGNAL |
| `get_ball_by_ball_table(last_n=20)` | `GET /matches/{id}/history` | Fixed-width text table |
| `get_match_scorecard(innings=2)` | `GET /matches/{id}/ball_events[_inn1]` | Over-by-over: runs, wickets, 4s, 6s |
| `get_batting_summary(innings=2)` | `GET /matches/{id}/ball_events[_inn1]` + `/scorecard/{n}` | Aggregate: sixes, fours, dots, extras, run rate; uses `team_total` from scorecard for accuracy |
| `get_batting_card(innings=2)` | `GET /matches/{id}/scorecard/{n}` | Per-batter: runs, balls, 4s, 6s, SR |
| `get_bowling_card(innings=2)` | `GET /matches/{id}/scorecard/{n}` | Per-bowler: overs, runs, wkts, economy, wides, no-balls |
| `run_python(code)` | (fetches history + both ball_events) | Executes code; `history`, `ball_events`, `ball_events_inn1` pre-loaded; returns stdout |
| `list_matches` | `GET /matches` | One line per match: id, ball count, teams, date |
| `get_schedule(team="")` | `GET /schedule[?team=X]` | Upcoming IPL 2026 fixtures (up to 10 shown); optional team abbreviation filter |

**Chart side-channel (`_chart_cache`):**
LangGraph ToolMessages must have string content — bytes can't be passed back through the message graph. Chart tools store PNG bytes in a module-level dict and return a plain text description. `agent.py` drains `_chart_cache` after the agent stream finishes and yields the bytes. Command handlers in `main.py` call tools directly, clear `_chart_cache` before the call, and drain it after.

**Score accuracy (`get_batting_summary`, `get_match_status`):**
`ball_events` only stores legal deliveries — wides and no-balls are excluded. The authoritative total is read from `scorecard/{n}.team_total` (set by `sum_innings_runs()` in the adapter, which includes all delivery types). `ball_events` run sum is used only as a fallback when the scorecard isn't saved yet.

**Error handling:** Every tool catches `requests.HTTPError` with status 404 and returns a "not available yet" message rather than raising. Other HTTP errors propagate for visibility.

**`ALL_TOOLS` list** at module bottom — imported by `agent.py`.

---

### `bot/agent.py`
Builds the LangGraph ReAct agent once at **module load** with a `MemorySaver` checkpointer:

```python
_checkpointer = MemorySaver()
_agent = create_react_agent(get_llm(), ALL_TOOLS, checkpointer=_checkpointer)
```

**`run_agent(message: str, chat_id: int) -> AsyncGenerator[str | bytes, None]`**

1. Clears `_chart_cache`
2. Streams `_agent.astream(...)` with `thread_id=str(chat_id)` — each chat gets its own conversation thread
3. Collects the final AIMessage text (handles both `str` and content-block list formats)
4. Yields the text string
5. Yields any PNG bytes from `_chart_cache`
6. On error: retries up to 3× with exponential backoff (2s, 4s, 8s + jitter) before yielding an error message

**Session memory:** `MemorySaver` keeps full message history per `chat_id` in-process. Follow-ups like "Sure", "4", "the second one" work correctly within a session. Memory is lost on container restart (in-memory only — acceptable for a personal bot).

**System prompt** instructs the agent to:
- Never ask clarifying questions; always use tool defaults and act immediately
- Assume current match unless another is explicitly named
- Prefer `run_python` for analytical questions not covered by a specific tool
- Use `innings=1` tools for first-innings data
- For schedule questions, call `get_schedule` with optional team filter

---

### LangGraph ReAct graph

```
START
  │
  ▼
┌─────────────────────────────┐
│  agent node (Gemini Flash)  │
│  reads messages, decides:   │
│  - call a tool?             │
│  - emit final response?     │
└────────┬──────────┬─────────┘
         │          │
    tool call    final text
         │          │
         ▼          ▼
  ┌────────────┐   END
  │ tools node │
  │ executes   │
  │ @tool fn   │
  └─────┬──────┘
        │
   tool result (ToolMessage)
        │
        └──→  back to agent node
              (loop until final text)
```

**Execution paths through the graph:**

| User message | Path |
|---|---|
| "what's the score?" | agent → `get_match_status` → agent → END |
| "show hotness curve and explain turning points" | agent → `get_hotness_curve` → agent → `get_key_turning_points` → agent → END |
| "who scored in inn1?" | agent → `get_batting_card(innings=1)` → agent → END |
| "how many sixes?" | agent → `run_python(...)` → agent → END |
| "when does CSK play next?" | agent → `get_schedule(team="CSK")` → agent → END |
| "explain cricket DRS" | agent → END (no tool needed — LLM answers from knowledge) |

---

### `bot/alert_loop.py`
Background asyncio coroutine started via PTB's `post_init` hook. Runs forever.

**Tick logic (every 30s):**

```
if no subscribed chats → skip

GET /matches/current
  → 404: no match active → skip
  → 200: extract match_id, team1, team2, balls_seen (inn2), last_state

GET /matches/{id}/ball_events_inn1
  → inn1_balls = len(events)

GET /matches/{id}/scorecard/1
  → inn1_total = scorecard.team_total

INN1_STARTED  if inn1_balls >= 1  and fp not seen
INN1_ENDED    if inn1_balls >= 6 AND balls >= 1 AND inn1_total > 0  and fp not seen
INN2_STARTED  if balls >= 1  and fp not seen
PRE_MATCH     if balls == 1 AND 0.40 ≤ win_prob ≤ 0.60  and fp not seen
IN_GAME       for each signal in last_state.signals  if fp not seen
MATCH_ENDED   if balls >= 1 AND (rr <= 0 OR wk >= 10 OR br == 0)  and fp not seen

Each: add fp, call _llm_summarise() → build message → send to all chats
```

**LLM summarisation:** Lifecycle alerts (INN1_STARTED, INN1_ENDED, INN2_STARTED, MATCH_ENDED) call `_llm_summarise(prompt)` via `asyncio.to_thread` to write a natural-language 1–2 sentence alert. If the LLM fails, `None` is returned and the template fallback is used.

**Fingerprint format:** `"{match_id}:{event_type}"` e.g. `"csk_vs_kkr_2026-04-22:INN1_STARTED"`

**Why `httpx.AsyncClient` (not `requests`) here:** The alert loop is a native coroutine — blocking I/O would stall the event loop. All other places (tools, command handlers) use `requests` because they run in a thread executor.

---

### `bot/main.py`
PTB v21 Application. Command routing:

| Command | Handler | LLM? | Tool called |
|---|---|---|---|
| `/start` | `cmd_start` | No | `state.subscribe` |
| `/stop` | `cmd_stop` | No | `state.unsubscribe` |
| `/status` | `cmd_status` | No | `get_match_status` |
| `/chart winprob` | `cmd_chart` | No | `get_win_prob_curve` |
| `/chart hotness` | `cmd_chart` | No | `get_hotness_curve` |
| `/chart forecast` | `cmd_chart` | No | `get_forecast_overlay` |
| `/signals` | `cmd_signals` | No | `get_signal_timeline` |
| `/turning` | `cmd_turning` | No | `get_key_turning_points` |
| `/balls [n]` | `cmd_balls` | No | `get_ball_by_ball_table` |
| `/matches` | `cmd_matches` | No | `list_matches` |
| Free text | `free_text_handler` | **Yes** | Agent decides |

**`post_init` hook:**
```python
async def post_init(application: Application) -> None:
    state.load()
    asyncio.create_task(alert_loop(application))
```
This is the correct PTB v21 pattern — creates the task inside the already-running event loop.

**Chart delivery from agent:**
```python
async for chunk in run_agent(text, update.effective_chat.id):
    if isinstance(chunk, bytes):
        await update.message.reply_photo(photo=io.BytesIO(chunk))
    else:
        await reply(update, chunk)
```

**Message splitting:** Telegram has a 4096-char limit. `_reply()` splits on that boundary.

---

## 4. 🐳 Docker integration

```yaml
bot:
  build: .
  command: python -m bot.main
  volumes:
    - ./data:/app/data          # read-write: bot writes bot_state.json here
  depends_on:
    orchestrator:
      condition: service_healthy
  env_file:
    - .env
  environment:
    - ORCHESTRATOR_URL=http://orchestrator:8080
    - BOT_STATE_PATH=/app/data/bot_state.json
    - PYTHONUNBUFFERED=1
  restart: unless-stopped
```

**Why `./data` not `./data/live_polls`:** The bot writes `bot_state.json` to `data/`, not inside `live_polls/`. Mounting the parent `data/` as read-write for the bot is safe and doesn't conflict with other services.

**`restart: unless-stopped`:** The bot should run 24/7. If it crashes, Docker restarts it automatically.

---

## 5. ⚙️ Configuration

| Env var | Required | Default | Purpose |
|---|---|---|---|
| `TELEGRAM_TOKEN` | Yes | — | BotFather token |
| `GOOGLE_API_KEY` | Yes | — | Gemini API key (free tier works) |
| `GEMINI_MODEL` | No | `gemini-2.0-flash` | Gemini model name |
| `ORCHESTRATOR_URL` | No | `http://orchestrator:8080` | Orchestrator base URL |
| `BOT_STATE_PATH` | No | `data/bot_state.json` | Where to persist state |

---

## 6. 📦 Dependencies

```
python-telegram-bot==21.5      # PTB async (v20+ required for asyncio)
langgraph>=0.2.28               # ReAct agent graph + MemorySaver
langchain-google-genai>=2.0.0  # Gemini via LangChain
langchain-core>=0.3.0          # @tool decorator, HumanMessage, etc.
httpx                           # Async HTTP for alert_loop
matplotlib                      # Chart generation
requests                        # Sync HTTP for tools
```

---

## 7. 🔑 Key design decisions

### Chart side-channel vs returning bytes directly
LangGraph converts all tool return values to `ToolMessage(content=str(...))`. Returning bytes directly would get coerced to a useless string representation. The side-channel dict (`_chart_cache`) allows tools to return a text description (which the LLM can reference) while separately delivering the actual PNG.

### LLM abstracted into `bot/llm.py`
`agent.py` and `alert_loop.py` call `get_llm()` — they do not import or configure `ChatGoogleGenerativeAI` directly. All model details (name, temperature, API key source) live in `llm.py`. To swap to a different model or provider, change only that file (or the `GEMINI_MODEL` env var).

### Sync tools with async agent
Tools use `requests` (sync). LangGraph's `ToolNode` automatically wraps sync callables in a thread executor when invoked from `astream`. No manual wrapping needed. The alert loop uses `httpx.AsyncClient` because it runs natively in the event loop and cannot block.

### Per-chat session memory via MemorySaver
`create_react_agent` with a `MemorySaver` checkpointer keyed by `str(chat_id)` gives each chat its own persistent message history within the process. Follow-ups ("Sure", "4", "that one") resolve correctly. Memory is in-process only — resets on container restart. Acceptable for a personal bot where sessions are short.

### Broken-thread recovery
If a tool call was interrupted mid-execution (container restart, streaming exception), MemorySaver stores an AIMessage with pending `tool_calls` but no corresponding ToolMessage. LangGraph raises `InvalidUpdateError: Found AIMessages with tool calls that do not have a corresponding ToolMessage` on the next invocation of that thread. `run_agent` detects this via `_is_broken_thread_error`, calls `_clear_thread(thread_id)` to wipe the thread from `MemorySaver.storage`, and retries immediately (no backoff — this is a state correction, not a transient API error). The `cleared_thread` flag ensures the wipe happens at most once per call.

### One alert per fingerprint, persisted
Fingerprint = `"{match_id}:{event_type}"`. Once sent, it is written to disk via `state.add_fingerprint(fp)`. A container restart cannot cause duplicate alerts. The `seen_fps` list is trimmed to 1000 entries (oldest half removed) to bound disk usage over a long season.

### `post_init` for background task
PTB v21 uses asyncio internally. `asyncio.create_task()` must be called inside a running event loop. `post_init` is the correct hook — it fires after the Application's event loop starts but before `run_polling()` begins processing updates.

### LLM summarisation with template fallback
Lifecycle alerts call the LLM to write a natural-sounding 1–2 sentence message. If the LLM fails (429, timeout, bad key), `_llm_summarise` returns `None` and the caller falls back to a hardcoded template. This makes lifecycle alerts robust to LLM outages.

---

## 8. 🚀 Feature checklist

| Feature | Status |
|---|---|
| `/start` registers chat; PRE_MATCH and IN_GAME alerts fire during a live match | ✅ |
| Lifecycle alerts: INN1_STARTED, INN1_ENDED, INN2_STARTED, MATCH_ENDED | ✅ |
| LLM-written lifecycle alert text with template fallback | ✅ |
| `/status` shows live scoreboard for both innings | ✅ |
| `/chart hotness`, `/signals`, `/turning`, `/balls`, `/matches` work correctly | ✅ |
| Free-text agent answers "who scored?", "batting card", "bowling figures" | ✅ |
| Session memory — follow-up messages resolve correctly within a chat | ✅ |
| `run_python` sandbox for ad-hoc analytical questions | ✅ |
| Schedule tool — "when does CSK play next?" | ✅ |
| Schedule filter includes today's match (date >= today, not strict future) | ✅ |
| Broken-thread recovery — clears MemorySaver state on InvalidUpdateError, retries once | ✅ |
| `docker compose restart bot` preserves subscriptions and alert history | ✅ |
| All 5 Docker services start cleanly with `docker compose up --build` | ✅ |
