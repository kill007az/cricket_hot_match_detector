# Working Context — Cricket Hot Match Engine

Append a new section here after every major/minor step. Keeps future sessions
oriented without re-reading all source files.

---

## Sprint 1 — Initial build (2026-04-14)

### What was built

Full engine repo scaffolded from the sprint-1 feature spec.

**Directory layout**
```
engine/
    __init__.py
    models.py        — BallEvent, ChaseState, HotnessState, MatchSession, EngineOutput
    state.py         — StateUpdater (immutable per-ball state update)
    features.py      — FeatureExtractor (6 features for win-prob NN)
    win_prob.py      — WinProbModel (loads win_prob_nn.pt)
    hotness.py       — HotnessCalculator (closeness + momentum formula)
    forecaster.py    — HotnessForecaster (loads hotness_forecaster.pt)
    signals.py       — SignalEvaluator (pre-match + in-game signals)
    orchestrator.py  — EngineOrchestrator (full pipeline, in-memory sessions)

api/
    __init__.py
    main.py          — FastAPI app, lifespan model loading
    routes.py        — POST /match/init, POST /match/{id}/ball, GET /match/{id}/state

tests/
    __init__.py
    simulate_hot_match.py  — Replay KKR vs LSG ball-by-ball via HTTP

.gitignore
requirements.txt
working_context.md  (this file)
```

### Post-sprint refactor — `bot/llm.py`

Extracted LLM construction out of `agent.py` into a dedicated `bot/llm.py` factory (`get_llm()`). Single place to configure model name, temperature, and API key. `agent.py` calls `get_llm()` — no LLM details hardcoded there. Swapping models only requires changing `llm.py`.

---

### Key design decisions

1. **BallEvent = legal delivery only.**
   Wides and no-balls are not sent to the engine. Extras on a BallEvent are
   byes/leg-byes only. The simulation script filters illegal deliveries before
   sending.

2. **Idempotency via ball_key.**
   `ball_key = f"{match_id}:{innings}:{over:.1f}"`. Duplicate POSTs return
   `is_duplicate=True` with the previous output unchanged.

3. **`balls_fraction` hardcoded to `/ 120.0`.**
   Both the win-prob NN and the forecaster were trained with this convention.
   The spec says `/ total_balls` — that's a known discrepancy; training wins.

4. **Overlap in data ingestion.**
   The simulation script deliberately re-sends 3 random balls to demonstrate
   idempotency. The engine absorbs them without mutating state.

5. **Models loaded once at startup.**
   `EngineOrchestrator.__init__` loads both `.pt` files. FastAPI lifespan
   stores the orchestrator on `app.state.engine` — shared across requests.

6. **Forecast gate: ball >= 60 AND >= 12-ball history.**
   Both conditions must hold. Early in the match neither condition is met.

### Model checkpoint keys (for reference)

| Checkpoint | Relevant keys |
|---|---|
| `win_prob_nn.pt` | `model_state_dict`, `input_dim`, `hidden_dims`, `X_mean`, `X_std`, `feature_cols` |
| `hotness_forecaster.pt` | `model_state_dict`, `input_dim`, `hidden_dims`, `lookback`, `horizon`, `X_train_mean`, `X_train_std` |

### How to run

```bash
# Start API
conda run -n cricket_hot uvicorn api.main:app --reload --port 8000

# In a second terminal — run the simulation
conda run -n cricket_hot python -m tests.simulate_hot_match
```

### Known gaps / next steps

- **Threshold calibration** — `FORECAST_THRESHOLD = 0.60` in `signals.py` (raised from 0.55 after first live run).
  Not formally calibrated. Run on more matches.
- **Signal deduplication** — in-game signal can fire on multiple consecutive
  balls once threshold is crossed. Decide: fire-once-per-match vs fire-once-
  per-crossing.
- **`balls_fraction` / total_balls for DLS** — currently both models hard-code
  `/120`; DLS matches with fewer overs are slightly mis-calibrated. Acceptable
  for now.
- **First innings signals** — not in scope for sprint 1.
- **Persistent session store** — sessions are in-memory; restart loses state.
  Sprint 2 could add Redis or SQLite.
- **Authentication** — no auth on the API; add before exposing publicly.

---

## Sprint 2 — Live polling service + first live run (2026-04-14/16)

### What was built

**Polling service** (`polling/`):
- `CricbuzzClient` — Cricbuzz unofficial API client with bot-avoidance headers; new endpoint `/api/mcenter/{id}/full-commentary/{inn}` (old `/api/cricket-match/` → 404)
- `adapter.py` — rewritten for new API field names (`legalRuns`, `totalRuns`, `overNumber`); wide/no-ball dedup by overNumber+timestamp
- `LivePoller` — 3-phase loop; auto-discovers cb_id via HTML scrape of cricbuzz.com (P5 ✅); resume via `ball_events.jsonl`
- `run.py` — unified local launcher (engine + poller)
- Docker: `Dockerfile` + `docker-compose.yml` for all four services

**Backlog fixes after first live run (CSK vs KKR, 2026-04-14):**
- B2: innings end condition for loss-by-runs
- B3: iterative smart wait (self-correcting)
- M3: forecast threshold 0.55 → 0.60
- U1: cumulative score column in Phase 3 table
- U2: win%/hotness as percentages
- P1: strategic timeout detection via commentary text
- P2: ping-pong buffer for raw inn2 files
- P3: super over detection + looping

**Tests:** 30 unit tests in `tests/test_poller_changes.py`

### Key observations from first live run

- Win prob and hotness tracked match drama accurately (peak hotness 0.912 at over 8.5 when genuinely 50/50)
- Momentum cliff at 6-ball boundary visible (backlog M1 — not yet fixed)
- Engine restart mid-match wiped state; forecaster delayed as a result (backlog B1 — not yet fixed)
- Win prob slightly overconfident at extreme wicket-loss scenarios (backlog M2 — not yet fixed)

### Remaining backlog (group 3)

- **B1** — engine restart state replay from `ball_events.jsonl`
- **M1** — momentum smoothing (EMA instead of hard 6-ball window)
- **M2** — win prob calibration at extremes
- **P4** — post-match Cricsheet data collection script
- **A1** — match analysis / debug view (scope TBD)

---

## Sprint 3 — Telegram bot + LangGraph agent (2026-04-16)

### What was built

**P5 — Auto-discovery of live Cricbuzz match ID:**
- `CricbuzzClient._fetch_live_matches()` — scrapes `cricbuzz.com/cricket-match/live-scores` HTML, extracts `(cb_id, team1_slug, team2_slug)` via regex, filters by status string in `title` attribute (skips "Preview" and "X won")
- `find_live_match(team1, team2)` and `find_live_ipl_match()` restored and working
- `_phase1_find_match()` in poller restored — polls every `poll_interval` seconds until match appears
- `run_live.py` and `run.py` — all args optional; no args = fully automatic
- `docker-compose.yml` — `CB_ID`/`TEAM1`/`TEAM2` optional via `${VAR:+--flag $VAR}` syntax
- **P6 added to backlog** — smart pre-match wait for always-on service (currently polls every 60s regardless of schedule)

**Telegram bot (`bot/`):**
- `state.py` — loads/saves `data/bot_state.json`; `subscribed_chats` + `seen_fps` (capped at 1000 entries)
- `charts.py` — `win_prob_chart`, `hotness_chart`, `forecast_overlay_chart` → PNG bytes via matplotlib Agg backend
- `tools.py` — 8 LangChain `@tool` functions (sync, requests-based); chart tools deposit PNG bytes in `_chart_cache` side-channel
- `agent.py` — `run_agent()` async generator; LangGraph `create_react_agent` with Gemini Flash; yields `str` (text) and `bytes` (charts) interleaved
- `alert_loop.py` — asyncio background coroutine; polls `/matches/current` every 30s; fires PRE_MATCH (ball 1, wp 40–60%) and IN_GAME alerts once per fingerprint per match
- `main.py` — PTB v21 Application; all command handlers; free-text → agent; alert loop started via `post_init` hook

**Infrastructure:**
- `requirements.txt` — added `python-telegram-bot==21.5`, `langgraph>=0.2.28`, `langchain-google-genai>=2.0.0`, `langchain-core>=0.3.0`
- `docker-compose.yml` — new `bot` service; depends on orchestrator healthcheck; mounts `./data`
- `.env.example` — `TELEGRAM_TOKEN`, `GOOGLE_API_KEY`, optional `TEAM1`/`TEAM2`/`CB_ID`

### Key design decisions

1. **Chart side-channel (`_chart_cache`).** LangGraph ToolMessages must have string content. Chart tools store PNG bytes in a module-level dict and return a text description. `agent.py` drains the cache after streaming and yields the bytes. Command handlers call tools directly and drain the cache themselves.

2. **Sync tools in async context.** All 8 tools use synchronous `requests.get`. `asyncio.to_thread` wraps them in command handlers. LangGraph wraps them automatically when called from `astream`.

3. **Alert fingerprint = `{match_id}:{signal_text}`.** One alert per match per signal text, persisted across restarts. PRE_MATCH fingerprint = `{match_id}:PRE_MATCH`.

4. **`post_init` hook for alert loop.** PTB v21 pattern — `asyncio.create_task(alert_loop(app))` inside the `post_init` coroutine ensures the task is created inside the running event loop.

5. **Single-user design.** No auth — bot is a personal notifier. Anyone with the bot link can `/start` and receive alerts. Acceptable for personal use; noted in Known Limitations.

### Remaining backlog

- **B1** — engine restart state replay
- **M1** — momentum smoothing
- **M2** — win prob calibration
- **P4** — Cricsheet auto-fetch script
- **P6** — smart pre-match wait (schedule-aware discovery)
- **D1** — poll-to-Cricsheet converter
- **A1** — match analysis / debug view

---

## Post-Sprint 3 improvements (2026-04-16)

### Bot agent upgrades

**Session memory:**
- `agent.py` — `MemorySaver` checkpointer keyed by `chat_id` (LangGraph `thread_id`). Each Telegram chat gets its own conversation thread. Follow-up messages ("Sure", "4", "that one") now work correctly.
- `run_agent(message, chat_id)` — signature extended; `main.py` passes `update.effective_chat.id`.

**Retry / backoff:**
- `agent.py` — 3-attempt exponential backoff (2s, 4s, 8s + jitter) on `_agent.astream()`. Handles Gemini 429 / 503 transient errors silently.

**LLM model config:**
- `bot/llm.py` — model name driven by `GEMINI_MODEL` env var (default `gemini-2.0-flash`). Change model without rebuilding by updating `.env`.
- `bot/llm.py` — content-block response format handled: `msg.content` may be `str` or `list[{type, text, ...}]`; both parsed correctly in `agent.py`.

**New tools (13 total, was 8):**
- `get_match_scorecard(innings)` — over-by-over runs/wickets/boundaries table
- `get_batting_summary(innings)` — aggregate totals: sixes, fours, dots, extras, run rate
- `get_batting_card(innings)` — per-batter: runs, balls, 4s, 6s, SR (from live scorecard JSON)
- `get_bowling_card(innings)` — per-bowler: overs, runs, wkts, economy, wides, no-balls
- `run_python(code)` — executes arbitrary Python with `history`, `ball_events`, `ball_events_inn1` pre-loaded; stdout returned as string

**System prompt hardened:** never ask clarifying questions, use defaults, assume current match, prefer `run_python` for analytical questions, both innings available.

### Full-match polling (inn1 + inn2)

**`polling/poller.py` — Phase 2 rewritten:**
- Old: smart sleep estimate → one-shot inn1 fetch at Phase 2.5
- New: **live inn1 polling loop** — polls every `poll_interval` seconds, records every legal delivery to `ball_events_inn1.jsonl` in real time
- Inn1 end detection: 2 consecutive empty polls with ≥6 balls seen
- Inn2 wait moved into Phase 2.5 (60s retry loop until first inn2 ball appears)
- Resume support: `_load_seen_keys_inn1()` reads existing `ball_events_inn1.jsonl` on restart

**Scorecard extraction (`polling/adapter.py`):**
- `extract_scorecard(items)` — derives batting + bowling cards from raw Cricbuzz commentary items
- Per-batter: name, runs, balls, 4s, 6s, SR, dots (from `batsmanStriker` fields)
- Per-bowler: name, overs, runs, wkts, maidens, wides, no-balls, economy (from `bowlerStriker` fields)
- Strategy: keep highest `batBalls`/`bowlOvs` entry per player ID across all commentary items

**Scorecard persistence:**
- `scorecard_inn1.json` / `scorecard_inn2.json` — written after every poll with new balls; always current

**New data files per match:**

| File | Contents |
|---|---|
| `ball_events_inn1.jsonl` | Inn1 legal deliveries: innings, over, runs, extras, wicket |
| `scorecard_inn1.json` | Inn1 batting + bowling cards |
| `scorecard_inn2.json` | Inn2 batting + bowling cards |

### New orchestrator endpoints

| Endpoint | Description |
|---|---|
| `GET /matches/{id}/ball_events` | Inn2 ball-by-ball |
| `GET /matches/{id}/ball_events_inn1` | Inn1 ball-by-ball |
| `GET /matches/{id}/scorecard/{1 or 2}` | Batting + bowling card for innings |

### Logging fix
- `bot/main.py` — logging level raised to `INFO` so PTB startup messages and agent activity are visible in `docker compose logs bot`.

### Score accuracy fix
- Root cause: `ball_events.jsonl` stores only legal deliveries — wide/no-ball runs were excluded, understating scores by ~5–11 runs per innings.
- Fix: `extract_scorecard()` in `adapter.py` now calls `sum_innings_runs(items)` to compute the true team total (includes all delivery types) and stores it as `team_total` in `scorecard_inn*.json`.
- `get_batting_summary` reads `team_total` from the scorecard endpoint as the authoritative total; falls back to summing ball_events if scorecard unavailable.

### `get_match_status` — live scoreboard
Rewritten to show a full live scoreboard instead of just "need X off Y":

```
🏏 CSK vs KKR
CSK: 192/5 (20 ov)           ← inn1 score from scorecard_inn1.json
KKR: 144/7 (18.2 ov)         ← inn2 score from ball_events (live) or scorecard_inn2.json
Need 49 off 11 balls          ← chase summary
Win prob: 87.3%  |  Hotness: 0.6%
Forecast: 72.1%
Last signal: match heating up — tune in now
```

- Inn1 score: reads `team_total` + wicket count from `GET /matches/{id}/scorecard/1`
- Inn2 score: sums `ball_events` live; uses `scorecard/2` team_total when innings is complete
- Helper `_overs_str(balls)` converts ball count to "X.Y ov" format
- Result indicator: shows "{team} won by N runs" or "{team} won" when match ends

---

## M2 fix — BCE win probability model (2026-04-16)

### Problem
NB03 model trained with MSE on smoothed empirical bin averages. At extreme tail states (6+ wickets down, 80+ runs needed), sparse bins get smoothed toward neighbours → model outputs ~0.05–0.07 where true rate is ~0.01–0.02.

### Fix
Retrained same architecture with `BCEWithLogitsLoss` on raw `chaser_won` (0/1) labels (NB09). BCE learns true win frequency directly from outcomes, bypassing bin averaging entirely.

**Data cleaning added:**
- Filter `balls_remaining <= 0` (end-of-match states causing `rrr` div-by-zero)
- Clip `rrr` to [0, 6] (extreme last-ball states were producing normalised values of 61 → NaN gradients)

**Architecture change:** Sigmoid removed from `_WinProbNet`; model outputs raw logit. `predict()` applies `torch.sigmoid()` manually. This is the correct pattern for `BCEWithLogitsLoss` (numerically stable, avoids boundary errors).

**Result:** Tail states (6+wk, 60+rn, ≤30br) push to 0.001–0.003 vs 0.05–0.07 before. Mid-range curves visually identical to NB03 on all 4 validation matches.

### Files changed
- `notebooks/09_win_prob_bce_experiment.ipynb` — full experiment
- `engine/win_prob.py` — no Sigmoid in model, sigmoid in predict()
- `models/win_prob_nn.pt` — replaced with BCE checkpoint
- `models/win_prob_nn_mse_nb03.pt` — backup of old MSE model
- `tests/test_win_prob_bce.py` — validation test (16/16 passing)

### Remaining backlog
- **B1** — engine restart state replay
- **M1** — momentum smoothing
- **P4** — Cricsheet auto-fetch script
- **P6** — smart pre-match wait
- **D1** — poll-to-Cricsheet converter
- **A1** — match analysis / debug view
