# Cricket Hot Match Bot

A personal Telegram bot that watches live IPL matches and:

- **Pushes proactive alerts** at every key moment — match start, innings change, hotness spike, match end
- **Answers slash commands instantly** — scoreboard, charts, turning points, ball table
- **Understands natural language** — ask anything about the match; a Gemini Flash ReAct agent figures out which tools to call

---

## Setup

### 1. Get credentials

| Credential | Where |
|---|---|
| `TELEGRAM_TOKEN` | Message [@BotFather](https://t.me/BotFather) on Telegram, create a bot, copy the token |
| `GOOGLE_API_KEY` | [Google AI Studio](https://aistudio.google.com/) → API keys (free tier works) |

### 2. Add to `.env`

```
TELEGRAM_TOKEN=your_telegram_bot_token_here
GOOGLE_API_KEY=your_google_api_key_here
```

Optional model override (default: `gemini-2.0-flash`):

```
GEMINI_MODEL=gemini-2.0-flash
```

### 3. Start

```bash
docker compose up --build -d
```

The bot starts after the orchestrator passes its health check. Open Telegram, find your bot, and send `/start`.

---

## Commands

| Command | What you get |
|---|---|
| `/start` | Subscribe to alerts + command list |
| `/stop` | Unsubscribe from alerts |
| `/status` | Live scoreboard: both innings scores, win prob, hotness, forecast |
| `/chart winprob` | Win probability over the chase (chart image) |
| `/chart hotness` | Hotness score over the chase (chart image) |
| `/chart forecast` | Hotness + 6-ball forecast overlay (chart image) |
| `/signals` | All alert signals fired this match with ball numbers |
| `/turning` | Top 5 win probability swings (turning points) |
| `/balls [n]` | Last N balls table — win%, hotness, forecast, runs needed (default 20) |
| `/matches` | All recorded matches with ball count and teams |

---

## Asking questions (free text)

The agent understands follow-ups and multi-part questions. Examples:

```
What's the score?
Show me the hotness chart and explain the big swings
Who scored the most runs in the first innings?
How many sixes has team 2 hit?
What was the run rate by over?
When does CSK play next?
Show the win prob curve
Was this a good match to watch?
```

The agent has access to all match data via 14 tools — if a specific tool doesn't cover your question, it writes Python against the raw ball data and runs it.

---

## Alerts

Six alerts per match — each fires exactly once, never re-sent after a restart:

| Alert | When |
|---|---|
| 🏏 **Match started** | First ball of innings 1 recorded |
| 📊 **Inn1 complete** | First innings over — includes runs scored and target |
| 🎯 **Chase started** | First ball of the chase — includes opening win probability |
| 📢 **50/50 chase** | Ball 1 win prob is 40–60% — structurally competitive, worth watching from the start |
| 🔥 **Hot match** | Hotness forecaster predicts a spike in the next 6 balls (after over 10) |
| 🏆 **Match over** | Result with final scores |

Lifecycle alerts (inn started/ended, match over) are written by Gemini Flash for a natural-language summary. If the LLM is unavailable, a template message is used.

---

## Configuration

All configuration is via environment variables:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `TELEGRAM_TOKEN` | Yes | — | BotFather token |
| `GOOGLE_API_KEY` | Yes | — | Gemini API key |
| `GEMINI_MODEL` | No | `gemini-2.0-flash` | Gemini model name |
| `ORCHESTRATOR_URL` | No | `http://orchestrator:8080` | Orchestrator base URL |
| `BOT_STATE_PATH` | No | `data/bot_state.json` | Persistent state file path |

---

## Agent tools reference

| Tool | Orchestrator endpoint | What it returns |
|---|---|---|
| `get_match_status` | `GET /matches/current` | Live scoreboard — both innings, win%, hotness |
| `get_win_prob_curve` | `GET /matches/{id}/history` | Win probability chart (PNG) |
| `get_hotness_curve` | `GET /matches/{id}/history` | Hotness chart (PNG) |
| `get_forecast_overlay` | `GET /matches/{id}/history` | Hotness + forecast overlay chart (PNG) |
| `get_signal_timeline` | `GET /matches/{id}/signals` | All signals with ball numbers |
| `get_key_turning_points` | `GET /matches/{id}/history` | Top N win prob swings, tagged WICKET/SIGNAL |
| `get_ball_by_ball_table` | `GET /matches/{id}/history` | Fixed-width table of last N balls |
| `get_match_scorecard` | `GET /matches/{id}/ball_events[_inn1]` | Over-by-over: runs, wkts, 4s, 6s |
| `get_batting_summary` | `GET /matches/{id}/ball_events[_inn1]` | Totals: runs, sixes, fours, dots, extras, RR |
| `get_batting_card` | `GET /matches/{id}/scorecard/{n}` | Per-batter: runs, balls, 4s, 6s, SR |
| `get_bowling_card` | `GET /matches/{id}/scorecard/{n}` | Per-bowler: overs, runs, wkts, economy |
| `run_python` | (fetches history + both ball_events) | Executes Python; `history`, `ball_events`, `ball_events_inn1` available |
| `list_matches` | `GET /matches` | All recorded matches |
| `get_schedule` | `GET /schedule` | Upcoming IPL 2026 fixtures (optional team filter) |

---

## How charts are delivered

LangGraph tool results must be strings — chart bytes cannot pass through the message graph directly. Chart tools deposit PNG bytes in a module-level side-channel dict (`_chart_cache`) and return a text description. After the agent finishes, `agent.py` drains the cache and `main.py` sends each PNG as a separate `reply_photo`. Command handlers (non-agent) use the same side-channel with an explicit clear-before / drain-after pattern.

---

## Session memory

Each Telegram chat gets its own conversation thread via `MemorySaver` keyed by `chat_id`. Follow-up messages that reference prior context ("Sure", "4", "that one", "now show the hotness") work correctly within a session. Memory is in-process only — it resets on container restart.

---

## Persistent state (`data/bot_state.json`)

```json
{
  "subscribed_chats": [123456789],
  "seen_fps": [
    "csk_vs_kkr_2026-04-22:INN1_STARTED",
    "csk_vs_kkr_2026-04-22:PRE_MATCH",
    "csk_vs_kkr_2026-04-22:Hotness forecast spike detected"
  ]
}
```

- **`subscribed_chats`** — chat IDs that receive proactive alerts; survives restarts
- **`seen_fps`** — fingerprints of every alert already sent; prevents duplicates after restart; trimmed to 1000 entries max

---

## Troubleshooting

**Bot sends no alerts**
- Check it's running: `docker compose logs -f bot`
- Check you sent `/start` from Telegram
- Verify `TELEGRAM_TOKEN` and `GOOGLE_API_KEY` are set in `.env`

**"No active match found"**
- The poller must be running and have sent at least one ball to the engine
- Check `docker compose logs -f poller`

**LLM errors (429 / quota)**
- Free tier has rate limits; retries with exponential backoff (up to 3×)
- Switch to a different model: `GEMINI_MODEL=gemini-2.0-flash-lite` in `.env`, then `docker compose restart bot`

**"Sorry, I encountered an error" on a specific tool**
- If a tool call was interrupted mid-execution, LangGraph's MemorySaver can be left with a pending tool call and no response — the next message in that chat errors immediately
- This is auto-recovered: the agent detects the broken state, clears the thread, and retries in the same call
- If it still fails after recovery, the underlying tool or orchestrator endpoint is the issue — check `docker compose logs orchestrator`

**Score seems wrong**
- The bot reads `team_total` from the saved scorecard (which includes wide/no-ball runs), not by summing legal deliveries only
- If scorecard hasn't been saved yet (match still in progress), the live running total from `ball_events` is shown instead

---

## Architecture

```
Telegram user
     │
     │  /command or free text
     ▼
bot/main.py  (python-telegram-bot v21)
     │
     ├── /commands ──→ tools.py ──→ orchestrator :8080
     │                    │
     │               charts.py (PNG side-channel)
     │
     └── free text ──→ agent.py (LangGraph ReAct + MemorySaver)
                           │
                           └──→ tools.py ──→ orchestrator :8080

alert_loop.py  (asyncio background, same process)
     ├── polls /matches/current every 30s
     └── broadcasts lifecycle + hotness alerts to all subscribed chats
```

The bot never reads files directly or calls the engine API — all data flows through the orchestrator at `:8080`.
