# 🤖 Cricket Hot Match Engine — Feature Specification (sprint 3)

Sprint 3 adds a Telegram chatbot powered by a LangGraph ReAct agent. The bot delivers proactive match
alerts and answers natural-language questions backed by all A1 analyses.

---

## 1. 🎯 Objective

Build a conversational Telegram interface that:

* Proactively notifies the user when a hotness signal fires (IN_GAME or PRE_MATCH) — once per match per signal type
* Answers natural-language queries about any live or completed match
* Serves all analysis views: win prob curve, hotness curve, forecast overlay, signal timeline, turning points, raw ball table

---

## 2. 🧩 Scope

### Included

* `bot/` service (new Docker Compose service)
* LangGraph ReAct agent with 8 tools
* Proactive alert loop (background asyncio coroutine)
* Telegram command shortcuts (bypass LLM for common queries)
* State persistence across restarts (`data/bot_state.json`)
* `.env.example` for secret management

### Excluded

* Multi-user auth (bot is single-user by design — personal notifier)
* Web/push notification delivery beyond Telegram

---

## 3. 🏗️ Architecture

```
Telegram user
   ↓ commands / free text
bot/main.py  (python-telegram-bot Application)
   ├── shortcut commands  →  tools.py  →  orchestrator API :8080
   └── free text / /analyse  →  agent.py (LangGraph ReAct)
                                  ↓ tool calls
                                tools.py  →  orchestrator API :8080
                                  ↓ results
                              AI response  →  Telegram reply (text + optional PNG)

alert_loop.py  (asyncio background coroutine, same process)
   ├── polls /matches/current every 30s
   ├── detects new signal fingerprints
   └── broadcasts alert to all subscribed chat IDs
```

The bot never calls the engine API or reads JSONL files directly. All data flows through the orchestrator.

---

## 4. 🤖 LangGraph Agent

### Architecture

ReAct pattern using `langgraph.prebuilt.create_react_agent`.

```python
llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash", temperature=0)
graph = create_react_agent(llm, tools=[...8 tools...])
```

The agent loop: LLM decides tool call → ToolNode executes → result back to LLM → repeat until LLM emits final text response.

### Tool Invocation

```
Feature: LangGraph agent tool calling

Scenario: Single-tool question
  Given user asks "what's the current score?"
  When agent processes the message
  Then get_match_status tool must be called
  And result must be returned as plain text

Scenario: Multi-tool question
  Given user asks "show me the hotness curve and explain the turning points"
  When agent processes the message
  Then get_hotness_curve and get_key_turning_points must both be called
  And chart PNG must be sent followed by text explanation

Scenario: No tool needed
  Given user asks a general cricket question
  When agent processes the message
  Then agent must answer from system prompt knowledge
  And no tool calls must be made
```

---

## 5. 🛠️ Tools

All tools are synchronous `@tool` functions, callable by the agent or directly from command handlers.

### 5.1 get_match_status

```
Feature: Match status tool

Scenario: Returns current match summary
  Given a live or completed match exists
  When get_match_status is called
  Then output must include teams, ball number, runs needed, balls remaining,
       wickets, win probability, hotness, and last signal
  And all values must be sourced from /matches/current
```

### 5.2 get_win_prob_curve

```
Feature: Win probability chart

Scenario: Chart generated for active match
  Given match history contains at least 1 ball
  When get_win_prob_curve is called
  Then a PNG chart must be returned
  And chart must show win_prob on y-axis vs ball number on x-axis
  And a dashed horizontal line must appear at win_prob = 0.5
  And signal balls must be marked with vertical lines
```

### 5.3 get_hotness_curve

```
Feature: Hotness chart

Scenario: Chart generated
  Given match history exists
  When get_hotness_curve is called
  Then a PNG chart must be returned
  And chart must show hotness on y-axis vs ball number
  And peak hotness ball must be annotated
```

### 5.4 get_forecast_overlay

```
Feature: Forecast overlay chart

Scenario: Chart with forecast line
  Given match history contains balls after ball 60
  When get_forecast_overlay is called
  Then chart must show hotness line and forecast line
  And forecast line must only appear from ball 60 onward
  And a vertical dashed line at ball 60 must be labelled "forecast gate"
```

### 5.5 get_signal_timeline

```
Feature: Signal timeline

Scenario: All signals listed
  Given signals have fired during the match
  When get_signal_timeline is called
  Then output must list each signal with ball number and signal text
  And signals must be ordered by ball number ascending
```

### 5.6 get_key_turning_points

```
Feature: Turning points

Scenario: Top N turning points identified
  Given match history exists
  When get_key_turning_points is called with top_n=5
  Then output must list the 5 balls with largest abs(delta win_prob)
  And each entry must show from/to win prob, delta in percentage points
  And wicket balls and signal balls must be tagged accordingly
```

### 5.7 get_ball_by_ball_table

```
Feature: Ball-by-ball table

Scenario: Last N balls returned
  Given match history exists
  When get_ball_by_ball_table is called with last_n=20
  Then output must be a formatted text table
  And columns must include: ball, win%, hotness, forecast, runs_needed, balls_remaining, wickets, signals
  And at most 20 rows must be returned by default
```

### 5.8 list_matches

```
Feature: Match listing

Scenario: All matches listed
  Given multiple match folders exist in live_polls/
  When list_matches is called
  Then output must list each match_id with ball count and completion status
```

---

## 6. 📣 Telegram Commands

| Command | LLM used | Behaviour |
|---|---|---|
| `/start` | No | Register chat ID for alerts; print command list |
| `/status` | No | Direct call to get_match_status |
| `/chart winprob` | No | Direct call to get_win_prob_curve |
| `/chart hotness` | No | Direct call to get_hotness_curve |
| `/chart forecast` | No | Direct call to get_forecast_overlay |
| `/signals` | No | Direct call to get_signal_timeline |
| `/turning` | No | Direct call to get_key_turning_points |
| `/balls [n]` | No | Direct call to get_ball_by_ball_table(last_n=n) |
| `/matches` | No | Direct call to list_matches |
| `/stop` | No | Unregister chat ID from alerts |
| Free text | Yes | Full LangGraph ReAct agent |

### Requirements (Gherkin)

```
Feature: Telegram command routing

Scenario: /start registers chat
  Given an unregistered chat ID
  When /start is sent
  Then chat ID must be added to subscribed_chats
  And a welcome message listing all commands must be returned

Scenario: /stop unregisters chat
  Given a registered chat ID
  When /stop is sent
  Then chat ID must be removed from subscribed_chats
  And no further alerts must be sent to that chat

Scenario: /status returns summary without LLM
  Given a live match
  When /status is sent
  Then get_match_status result must be returned directly
  And no LLM call must be made

Scenario: Free text routes through agent
  Given user sends "explain the hotness spike at ball 34"
  When message is received
  Then LangGraph agent must be invoked with that message
  And response must be sent back to the same chat
```

---

## 7. 🔔 Proactive Alert Loop

```
Feature: Proactive alerts

Scenario: Alert fires on new IN_GAME signal
  Given a live match
  And forecast crosses threshold causing IN_GAME signal to fire
  And signal fingerprint "{match_id}:match heating up — tune in now" is new
  When alert_loop detects the signal
  Then an alert must be sent to all subscribed chat IDs
  And the fingerprint must be stored in seen_fps

Scenario: Alert fires only once per match per signal type
  Given IN_GAME signal has already fired this match
  When the same signal appears again on the next ball
  Then no new alert must be sent

Scenario: PRE_MATCH alert fires at ball 1
  Given a new match starts
  And win_prob at ball 1 is between 0.40 and 0.60
  When alert_loop detects the PRE_MATCH signal
  Then an alert must be sent with icon 📢
  And fingerprint "{match_id}:50/50 chase — worth watching from the start" must be stored

Scenario: No alert when no registered chats
  Given subscribed_chats is empty
  When a new signal fires
  Then no Telegram messages must be sent

Scenario: Alert loop continues after exception
  Given orchestrator API returns a 500 error
  When alert_loop polls
  Then exception must be caught and logged
  And loop must continue on the next 30s interval
```

Alert message format:
```
🔥 LIVE ALERT — {team1} vs {team2}
Ball {ball}: {signal_text}
Win prob: {win_prob:.1%} | Hotness: {hotness:.3f}
Need {runs_needed} off {balls_remaining} balls, {wickets} wickets down

Reply with /chart hotness or ask me anything.
```

---

## 8. 💾 State Persistence

```
Feature: Alert state persistence

Scenario: State saved on mutation
  Given a chat registers via /start
  When chat ID is added to subscribed_chats
  Then bot_state.json must be written immediately

Scenario: State loaded on startup
  Given bot_state.json exists from a previous run
  When bot/main.py starts
  Then subscribed_chats must be restored
  And seen_fps must be restored
  And no alerts must be re-sent for already-seen signals

Scenario: State file missing on first run
  Given bot_state.json does not exist
  When bot/main.py starts
  Then subscribed_chats must be empty
  And seen_fps must be empty
  And bot must start normally
```

---

## 9. 📊 Charts

All charts produced by `charts.py`. Input: `list[dict]` from `/matches/{id}/history`. Output: `bytes` (PNG).

```
Feature: Chart generation

Scenario: Chart returned as bytes
  Given valid match history
  When any chart function is called
  Then return value must be bytes
  And bytes must decode as a valid PNG

Scenario: Forecast chart handles null forecast values
  Given history where forecast is None before ball 60
  When forecast_overlay_chart is called
  Then forecast line must only be drawn where forecast is not None
  And no error must occur for null values

Scenario: Charts are legible size
  Given any match history
  When a chart is generated
  Then PNG dimensions must be approximately 1200×480px (10×4in @ 120dpi)
```

---

## 10. 🐳 Docker Integration

New `bot` service in `docker-compose.yml`:

```yaml
bot:
  build: .
  command: python -m bot.main
  volumes:
    - ./data:/app/data
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

```
Feature: Docker service health

Scenario: Bot starts after orchestrator is healthy
  Given docker compose up --build
  When orchestrator passes its health check
  Then bot service must start and connect to Telegram

Scenario: Bot state survives restart
  Given bot has registered subscribers and seen signals
  When docker compose restart bot
  Then bot_state.json on the host volume must preserve state
  And registered chats must still receive future alerts
```

---

## 11. ⚙️ Configuration

Environment variables (via `.env`):

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | Yes | BotFather token |
| `GOOGLE_API_KEY` | Yes | Gemini API key (free tier) |
| `ORCHESTRATOR_URL` | No | Default: `http://orchestrator:8080` |
| `BOT_STATE_PATH` | No | Default: `data/bot_state.json` |

---

## 12. 🚀 MVP Definition

Sprint 3 is complete when:

* `/start` registers a chat and alerts are delivered for both PRE_MATCH and IN_GAME signals during a live match
* `/status`, `/chart hotness`, `/signals`, `/turning` all return correct results without errors
* Free-text "what are the top turning points?" returns a ranked list via the agent
* `docker compose restart bot` preserves subscriptions
* All 5 Docker services start cleanly with `docker compose up --build`
