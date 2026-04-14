# api/

FastAPI HTTP layer over the engine. Models are loaded once at startup and shared across all requests via `app.state.engine`.

---

## Running

```bash
# From the repo root
conda activate cricket_hot
uvicorn api.main:app --reload --port 8000
```

Interactive docs (Swagger UI): `http://localhost:8000/docs`

---

## Endpoints

### `POST /match/init`

Initialise a new match session before the first delivery.

Call this once with innings 1 summary data. Re-calling with the same `match_id` resets the session (all history cleared).

**Request**
```json
{
  "match_id": "kkr_vs_lsg_2026-04-09",
  "target": 182,
  "total_balls": 120
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `match_id` | string | yes | Any unique string — used as the session key |
| `target` | int | yes | Innings 1 runs + 1 |
| `total_balls` | int | yes | Actual legal balls bowled in innings 1 — **not** `overs * 6` |

**Response `201`**
```json
{
  "match_id": "kkr_vs_lsg_2026-04-09",
  "target": 182,
  "total_balls": 120,
  "message": "Match session initialised."
}
```

---

### `POST /match/{match_id}/ball`

Process one legal delivery. Returns the full engine output for that ball.

**Important:** only send **legal** deliveries (no wides, no no-balls). The engine counts every received delivery as a legal ball. Sending a wide will inflate `balls_faced` and corrupt the state.

**Request**
```json
{
  "innings": 2,
  "over": 14.3,
  "runs": 4,
  "extras": 0,
  "wicket": false,
  "timestamp": "2026-04-09T19:42:11Z"
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `innings` | int | no | Defaults to `2` |
| `over` | float | yes | `over.delivery` — e.g. `14.3` = over 14, 3rd legal ball |
| `runs` | int | yes | Batter runs off this ball |
| `extras` | int | no | Byes / leg-byes only. Defaults to `0` |
| `wicket` | bool | no | Defaults to `false` |
| `timestamp` | datetime | no | ISO 8601. Defaults to server UTC now |

**Response `200`**
```json
{
  "match_id": "kkr_vs_lsg_2026-04-09",
  "win_prob": 0.483,
  "hotness": 0.734,
  "forecast": 0.812,
  "runs_needed": 14,
  "balls_remaining": 7,
  "wickets": 7,
  "signals": ["match heating up — tune in now"],
  "is_duplicate": false,
  "processing_ms": 0.91
}
```

| Field | Notes |
|---|---|
| `win_prob` | Batting team win probability, 0–1 |
| `hotness` | Current drama score, 0–1 |
| `forecast` | Predicted peak hotness in next 6 balls, 0–1. `null` before ball 60 or before 12-ball history exists |
| `signals` | Empty list when nothing fires |
| `is_duplicate` | `true` if this exact delivery was already processed — state was not mutated |
| `processing_ms` | Server-side engine time only, excludes HTTP round-trip |

**Idempotency:** re-sending the same `over.delivery` value is safe. The engine returns the previous output with `is_duplicate: true` and does not change any state. Useful for retry logic on network failures.

**Error `404`** — match not initialised
```json
{ "detail": "Match 'kkr_vs_lsg_2026-04-09' not found. Call POST /match/init first." }
```

---

### `GET /match/{match_id}/state`

Returns the current chase state and the last ball's engine output.

**Response `200`**
```json
{
  "match_id": "kkr_vs_lsg_2026-04-09",
  "target": 182,
  "total_balls": 120,
  "runs_scored": 168,
  "wickets": 7,
  "balls_faced": 113,
  "runs_needed": 14,
  "balls_remaining": 7,
  "last_output": { ... }
}
```

`last_output` is `null` if no ball has been processed yet.

**Error `404`** — match not found.

---

### `GET /debug/latency`

Per-step mean latency breakdown across all balls processed since server start. Use this to identify the engine bottleneck.

**Response `200`**
```json
{
  "ball_count": 120,
  "total_mean_ms": 0.91,
  "steps": {
    "win_prob":       { "mean_ms": 0.43, "share_pct": 47.2 },
    "forecast":       { "mean_ms": 0.29, "share_pct": 31.9 },
    "hotness":        { "mean_ms": 0.05, "share_pct": 5.5 },
    "state_update":   { "mean_ms": 0.04, "share_pct": 4.4 },
    "feature_extract":{ "mean_ms": 0.03, "share_pct": 3.3 },
    "signals":        { "mean_ms": 0.02, "share_pct": 2.2 }
  }
}
```

Aggregates across all matches and all balls since startup. Reset by restarting the server.

---

## Error codes

| Code | Meaning |
|---|---|
| `201` | Match initialised successfully |
| `200` | Ball processed successfully |
| `404` | Match not found — call `/match/init` first |
| `422` | Validation error — check request body against schema |
| `500` | Internal server error — check uvicorn terminal for traceback |

---

## Architecture notes

**Model loading.** Both NNs are loaded once inside the FastAPI `lifespan` handler at startup and attached to `app.state.engine`. All routes access them via `request.app.state.engine`. This avoids repeated disk reads and ensures deterministic inference across requests.

**Session storage.** Match sessions are held in a plain Python dict on the `EngineOrchestrator` instance. This means:
- Sessions are process-local — horizontal scaling requires a shared session store (Redis, etc.)
- A server restart clears all sessions — clients must re-init and re-replay

**Concurrency.** FastAPI runs on an async event loop (uvicorn), but `process_ball` is synchronous CPU work. Under high concurrency, requests will queue. For the current single-match live use case this is not an issue. For multi-match scaling, offload to a thread pool with `run_in_executor`.
