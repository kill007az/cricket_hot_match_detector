# Skill: Finding and Updating Cricbuzz API Endpoints

The Cricbuzz unofficial API has no public docs and changes without notice.
When endpoints break (404s), use this skill to rediscover the current ones.

---

## Current working endpoints (verified 2026-04-16)

| Purpose | Method | URL |
|---|---|---|
| Ball-by-ball commentary | GET | `https://www.cricbuzz.com/api/mcenter/{cb_id}/full-commentary/{innings}` |
| Live match listing (HTML scrape) | GET | `https://www.cricbuzz.com/live-cricket-scores` |

**Live match listing**: scrape the HTML page (not a JSON API). Match URLs follow the pattern
`/live-cricket-scores/{cb_id}/{team1}-vs-{team2}-{rest}`. The cb_id and team slugs are extracted
via regex. Implemented in `CricbuzzClient._fetch_live_matches()`. No HAR needed.

---

## How to find the Cricbuzz match ID

The numeric match ID appears in every Cricbuzz match URL:

```
https://www.cricbuzz.com/live-cricket-scores/151763/csk-vs-kkr-...
                                               ^^^^^^
                                               cb_id = 151763
```

Copy this number and pass it as `--cb-id 151763`.

---

## Commentary endpoint — response structure

```
GET /api/mcenter/{cb_id}/full-commentary/{innings}

{
  "commentary": [
    {
      "inningsId": 1,
      "commentaryList": [   ← items newest-first
        {
          "overNumber":  2.3,     ← over.ball float (1-indexed); None for non-delivery items
          "ballNbr":     15,      ← sequential; 0 for messages/separators
          "legalRuns":   1,       ← runs credited to batter
          "totalRuns":   1,       ← batter + extras (leg-byes, byes)
          "event":       "NONE",  ← NONE | FOUR | SIX | WICKET | over-break | ...
          "timestamp":   1776..., ← epoch ms
          "batTeamScore": 59,     ← cumulative team score
          ...
        },
        ...
      ]
    }
  ],
  "minTimestamp": null,   ← pagination cursor (null = all items returned)
  ...
}
```

### Wide / no-ball identification

Wides and no-balls **share the same `overNumber` as the subsequent legal delivery**
and have a **lower timestamp**.  The adapter deduplicates by overNumber keeping
the highest-timestamp item, which is always the legal ball.

Do NOT use `event == "BALL"` — in the new API, event is never "BALL".
Legal balls have events like "NONE", "FOUR", "SIX", "WICKET", "over-break".

### Field mapping to BallEvent

| BallEvent field | Source |
|---|---|
| `over` | `overNumber` |
| `runs` | `legalRuns` |
| `extras` | `totalRuns - legalRuns` |
| `wicket` | `"WICKET" in event` |

---

## How to rediscover endpoints when they break

### Step 1 — Capture a HAR during a live match

1. Open Chrome → navigate to the **match commentary page** on cricbuzz.com
2. Open DevTools (F12) → **Network** tab → filter by **Fetch/XHR**
3. Clear the log, then reload the page or wait for the next poll update
4. Right-click in the Network tab → **Save all as HAR with content**
5. Save as `www.cricbuzz.com.har` in the project root

Capture from the **match commentary page** (with ball-by-ball text), not the
homepage — the homepage uses server-side rendering and won't show data API calls.

### Step 2 — Parse the HAR

```python
import json

with open('www.cricbuzz.com.har') as f:
    har = json.load(f)

for e in har['log']['entries']:
    url = e['request']['url']
    status = e['response']['status']
    ct = e['response']['content'].get('mimeType', '')
    if 'json' in ct and status == 200 and 'cricbuzz' in url.lower() and 'advert' not in url:
        print(f"{status}  {url}")
```

### Step 3 — Inspect a ball delivery item

```python
import json

with open('www.cricbuzz.com.har') as f:
    har = json.load(f)

for e in har['log']['entries']:
    if 'full-commentary' in e['request']['url'] and 'mcenter' in e['request']['url']:
        data = json.loads(e['response']['content']['text'])
        items = []
        for inn in data.get('commentary', []):
            items.extend(inn.get('commentaryList', []))
        # Find the first delivery item
        for item in items:
            if item.get('overNumber') and item.get('ballNbr', 0) > 0:
                print(json.dumps(item, indent=2))
                break
        break
```

### Step 4 — Update `cricbuzz_client.py`

The URL is in `get_commentary()`:
```python
url = f"{_BASE}/api/mcenter/{cb_id}/full-commentary/{innings}"
```

If the response structure changed, also update `adapter.py` — specifically
`_extract_legal_balls()`, `parse_legal_balls()`, `count_legal_balls()`, and
`sum_innings_runs()`.

---

## History of endpoint changes

| Date | Old URL | New URL | Notes |
|---|---|---|---|
| ~2026-04 | `/api/cricket-match/{id}/full-commentary/{inn}` | `/api/mcenter/{id}/full-commentary/{inn}` | Path segment changed; response structure also changed |
| ~2026-04 | `/api/cricket-match/live-matches` | HTML scrape of `/live-cricket-scores` | JSON API gone; switched to HTML regex extraction of cb_id from match URLs |
