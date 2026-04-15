# Skill: Fetch Ball-by-Ball Data for a Match

Given a match description (e.g. "KKR vs LSG IPL 2026 April 9"), this skill produces a JSON file
in `data/raw/` ready for use in the notebooks.

> **This skill is for post-match historical data retrieval only.**
> For live match data the project uses `polling/cricbuzz_client.py`, which polls the Cricbuzz
> unofficial JSON API in real time. Use this skill ~12 hours after a match ends to add it to the
> training/validation set (cricsheet updates approximately weekly).

---

## Source

**cricsheet.org** — free, no login, no API key. Downloads are zip files of JSON match files.
No other source needed for historical data.

---

## Decision tree

```
Is the match an IPL match?
├── YES → use https://cricsheet.org/downloads/ipl_json.zip   (all IPL seasons, ~1200 matches)
│         OR https://cricsheet.org/downloads/<year>_json.zip  (faster, just that year)
└── NO  → Is it a T20 International (e.g. T20 World Cup, bilateral T20I series)?
          ├── YES → use https://cricsheet.org/downloads/t20s_json.zip
          │         OR https://cricsheet.org/downloads/<year>_json.zip
          └── NO  → use https://cricsheet.org/downloads/all_json.zip (all formats, all years)
```

For recent matches (within last ~2 weeks), prefer the year-specific zip as it's smaller and updated more frequently than the full collection.

---

## Complete step-by-step

### 1. Download and extract the zip

```bash
# Replace 2026 with the relevant year
curl -o /tmp/cricket_matches.zip "https://cricsheet.org/downloads/2026_json.zip"
unzip -q /tmp/cricket_matches.zip -d /tmp/cricket_matches/
echo "Files extracted: $(ls /tmp/cricket_matches/ | wc -l)"
```

### 2. Find the match file

Run this Python script — fill in the team names and date:

```python
import json, os

FOLDER     = '/tmp/cricket_matches'
TEAM_1     = 'Kolkata Knight Riders'   # exact name as used by cricsheet
TEAM_2     = 'Lucknow Super Giants'    # exact name as used by cricsheet
DATE       = '2026-04-09'              # YYYY-MM-DD, or None to search all dates

results = []
for fname in os.listdir(FOLDER):
    if not fname.endswith('.json'):
        continue
    try:
        d = json.load(open(os.path.join(FOLDER, fname)))
    except Exception:
        continue
    teams = d['info']['teams']
    dates = d['info']['dates']
    if TEAM_1 in teams and TEAM_2 in teams:
        if DATE is None or DATE in dates:
            results.append((dates[0], fname, d['info'].get('outcome', {})))

for r in sorted(results):
    print(r)
```

If nothing is returned, the match may not be in cricsheet yet (too recent — they update ~weekly).
Try again in a few days, or try the `all_json.zip` if you used a year-specific zip.

### 3. Verify the match is correct

```python
import json

fname = '1527688.json'  # from step 2
d = json.load(open(f'/tmp/cricket_matches/{fname}'))

print('Teams:   ', d['info']['teams'])
print('Date:    ', d['info']['dates'])
print('Outcome: ', d['info'].get('outcome'))
print('Innings: ', len(d['innings']))
print('Inn1 team:', d['innings'][0]['team'], '— overs:', len(d['innings'][0]['overs']))
print('Inn2 team:', d['innings'][1]['team'], '— overs:', len(d['innings'][1]['overs']))
```

### 4. Copy to project

```bash
# Naming convention: <team1_short>_vs_<team2_short>_<date>.json
cp /tmp/cricket_matches/1527688.json \
   "E:/Personal Projects/cricket_hot_match_detector/data/raw/kkr_vs_lsg_2026-04-09.json"
```

---

## Exact team names used by cricsheet

These must match exactly when searching:

| Common name | Cricsheet name |
|---|---|
| KKR | Kolkata Knight Riders |
| LSG | Lucknow Super Giants |
| MI | Mumbai Indians |
| CSK | Chennai Super Kings |
| RCB | Royal Challengers Bengaluru |
| DC | Delhi Capitals |
| GT | Gujarat Titans |
| RR | Rajasthan Royals |
| SRH | Sunrisers Hyderabad |
| PBKS | Punjab Kings |
| India | India |
| Pakistan | Pakistan |

---

## JSON schema reference

```
match.json
├── info
│   ├── teams          # ["Team A", "Team B"] — batting order not implied
│   ├── dates          # ["YYYY-MM-DD"]
│   ├── toss           # {winner: str, decision: "bat"/"field"}
│   ├── outcome        # {winner: str, by: {runs: N}} or {winner: str, by: {wickets: N}}
│   │                  # missing if no result / abandoned
│   └── overs          # scheduled overs (may be 20 even if rain-reduced — do not trust)
└── innings            # list, usually 2 (may be 1 if match abandoned)
    ├── [0]            # first innings
    │   ├── team       # team name (batting)
    │   └── overs      # list of over objects (0-indexed)
    │       └── {over: int, deliveries: [...]}
    │           └── delivery
    │               ├── batter         # batsman name
    │               ├── bowler         # bowler name
    │               ├── non_striker    # other end batsman
    │               ├── runs
    │               │   ├── batter     # runs credited to batsman
    │               │   ├── extras     # extras on this ball
    │               │   └── total      # batter + extras (add this to score)
    │               ├── extras         # ONLY present if there are extras
    │               │   └── {wides: N} / {noballs: N} / {byes: N} / {legbyes: N}
    │               └── wickets        # ONLY present if wicket falls
    │                   └── [{player_out, kind, fielders}]
    └── [1]            # second innings (chase)
```

### Key code patterns

**Check if a delivery is legal** (wides + no-balls do not count toward ball quota):
```python
extras_type = list(ball.get('extras', {}).keys())
is_legal = 'wides' not in extras_type and 'noballs' not in extras_type
```

**Get actual total balls in innings** (use this, not `info.overs`, handles rain reductions):
```python
total_balls = sum(
    1 for ov in innings['overs'] for b in ov['deliveries']
    if 'wides' not in b.get('extras', {}) and 'noballs' not in b.get('extras', {})
)
```

**Check if chasing team won**:
```python
chasing_team = d['innings'][1]['team']
chaser_won   = d['info'].get('outcome', {}).get('winner') == chasing_team
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Match not found in zip | Match too recent — cricsheet updates ~weekly. Wait and re-download. |
| Only 1 innings in file | Match abandoned or no result. Skip it. |
| `outcome` key missing | No result / tie / DLS with no winner. Skip or handle separately. |
| `info.overs` says 20 but fewer overs played | Rain reduction. Always compute `total_balls` from actual deliveries. |
| Team name not matching | Check exact spelling in the cricsheet team names table above. |
