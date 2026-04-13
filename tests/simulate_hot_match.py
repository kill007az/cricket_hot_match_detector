"""
Simulation test: replay KKR vs LSG (2026-04-09) ball by ball against the engine.

Demonstrates:
  - Correct match initialisation from innings 1 summary
  - Ball-by-ball polling (one POST per legal delivery)
  - Overlap handling: re-sends 3 random balls; confirms is_duplicate=True
  - Signal detection: pre-match and in-game forecast signals printed when fired
  - Final summary table

Run:
    conda run -n cricket_hot python -m tests.simulate_hot_match
"""

import json
import random
import sys
import time
from pathlib import Path

import requests
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = "http://localhost:8000"
DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "raw" / "kkr_vs_lsg_2026-04-09.json"
MATCH_ID = "kkr_vs_lsg_2026-04-09"
DELAY_SECONDS = 0.0   # set > 0 to simulate real-time polling pace

# ---------------------------------------------------------------------------
# Cricsheet helpers
# ---------------------------------------------------------------------------

def is_legal(delivery: dict) -> bool:
    """A delivery is illegal (not a legal ball) if it is a wide or no-ball."""
    extras = delivery.get("extras", {})
    return "wides" not in extras and "noballs" not in extras


def parse_innings(inn: dict) -> list:
    """
    Returns a list of BallEvent-compatible dicts for all LEGAL deliveries.

    over: float uses the cricket notation  over_num.legal_delivery_in_over
    e.g. legal ball 3 of over 14 → 14.3
    """
    events = []
    for over_obj in inn["overs"]:
        over_num = over_obj["over"]   # 0-indexed
        legal_idx = 0
        for delivery in over_obj["deliveries"]:
            if not is_legal(delivery):
                # Still contributes extras to runs — absorbed into next legal ball
                # is NOT sent to the engine as a BallEvent per contract
                continue
            legal_idx += 1
            events.append({
                "innings": 2,
                "over": round(over_num + legal_idx / 10, 1),
                "runs": delivery["runs"]["batter"],
                "extras": delivery["runs"]["extras"],
                "wicket": "wickets" in delivery,
            })
    return events


def count_legal_balls(inn: dict) -> int:
    total = 0
    for over_obj in inn["overs"]:
        for delivery in over_obj["deliveries"]:
            if is_legal(delivery):
                total += 1
    return total


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # ---- Load match data ----
    with open(DATA_FILE, encoding="utf-8") as f:
        match_data = json.load(f)

    inn1 = match_data["innings"][0]
    inn2 = match_data["innings"][1]

    # Inn1: compute total legal balls and target
    total_balls_inn1 = count_legal_balls(inn1)
    inn1_runs = sum(
        d["runs"]["total"]
        for ov in inn1["overs"]
        for d in ov["deliveries"]
    )
    target = inn1_runs + 1

    print(f"Match:        {MATCH_ID}")
    print(f"Inn1 runs:    {inn1_runs}  →  Target: {target}")
    print(f"Inn1 balls:   {total_balls_inn1}")
    print(f"Outcome:      {match_data['info']['outcome']}")
    print()

    # ---- Check server is up ----
    # Use a Session for all requests — reuses the TCP connection (keep-alive)
    # instead of opening a new socket per ball, which costs ~2s each on Windows.
    session = requests.Session()
    try:
        session.get(f"{BASE_URL}/docs", timeout=3)
    except requests.exceptions.ConnectionError:
        print("ERROR: API server is not running.")
        print(f"Start it with:  uvicorn api.main:app --reload --port 8000")
        sys.exit(1)

    # ---- Init match ----
    resp = session.post(f"{BASE_URL}/match/init", json={
        "match_id": MATCH_ID,
        "target": target,
        "total_balls": total_balls_inn1,
    })
    resp.raise_for_status()
    print(f"[INIT]  {resp.json()['message']}")
    print()

    # ---- Parse deliveries ----
    events = parse_innings(inn2)
    print(f"Inn2 legal deliveries: {len(events)}")
    print("─" * 70)

    # Pick 3 balls to re-send (for overlap test) — avoid first and last
    overlap_indices = set(random.sample(range(1, len(events) - 1), 3))
    overlap_events = [events[i] for i in sorted(overlap_indices)]

    # ---- Replay ball by ball ----
    results = []
    all_signals = []
    client_latencies = []  # round-trip ms per ball (includes HTTP overhead)

    for i, ev in enumerate(tqdm(events, desc="Processing balls", unit="ball", ncols=70)):
        t_send = time.perf_counter()
        resp = session.post(f"{BASE_URL}/match/{MATCH_ID}/ball", json=ev)
        client_latencies.append((time.perf_counter() - t_send) * 1000)
        resp.raise_for_status()
        out = resp.json()
        results.append(out)

        ball_num = i + 1
        over_str = f"{int(ev['over'])}.{round((ev['over'] % 1) * 10):.0f}"

        if out["signals"]:
            for sig in out["signals"]:
                all_signals.append((ball_num, over_str, sig))
                tqdm.write(f"  *** SIGNAL at ball {ball_num:3d} (over {over_str}): {sig}")

        if DELAY_SECONDS > 0:
            time.sleep(DELAY_SECONDS)

    print()

    # ---- Overlap test ----
    print("─" * 70)
    print("OVERLAP TEST — re-sending 3 already-processed balls:")
    for ev in overlap_events:
        resp = session.post(f"{BASE_URL}/match/{MATCH_ID}/ball", json=ev)
        resp.raise_for_status()
        out = resp.json()
        status = "is_duplicate=True ✓" if out["is_duplicate"] else "FAIL — not marked duplicate"
        print(f"  over {ev['over']:.1f}  →  {status}")
    print()

    # ---- Final state ----
    resp = session.get(f"{BASE_URL}/match/{MATCH_ID}/state")
    resp.raise_for_status()
    state = resp.json()
    print("─" * 70)
    print("FINAL STATE:")
    print(f"  balls_faced:     {state['balls_faced']}")
    print(f"  runs_scored:     {state['runs_scored']}")
    print(f"  wickets:         {state['wickets']}")
    print(f"  runs_remaining:  {state['runs_needed']}")
    print()

    # ---- Signal summary ----
    print("─" * 70)
    print(f"SIGNALS FIRED ({len(all_signals)} total):")
    if all_signals:
        for ball_num, over_str, sig in all_signals:
            print(f"  ball {ball_num:3d}  over {over_str}  →  {sig}")
    else:
        print("  (none)")
    print()

    # ---- Hotness snapshot (every 10 balls) ----
    print("─" * 70)
    print("HOTNESS SNAPSHOT (every 10 balls):")
    header = f"{'Ball':>5}  {'Win%':>6}  {'Hotness':>8}  {'Forecast':>9}  Signals"
    print(header)
    for i, out in enumerate(results):
        ball_num = i + 1
        if ball_num % 10 == 0 or out["signals"]:
            fc_str = f"{out['forecast']:.3f}" if out["forecast"] is not None else "    —  "
            sig_str = " | ".join(out["signals"]) if out["signals"] else ""
            print(
                f"{ball_num:5d}  "
                f"{out['win_prob']:6.3f}  "
                f"{out['hotness']:8.3f}  "
                f"{fc_str:>9}  "
                f"{sig_str}"
            )

    # ---- Latency stats ----
    import statistics

    cl = sorted(client_latencies)
    n = len(cl)

    def pct(data, p):
        idx = max(0, int(len(data) * p / 100) - 1)
        return data[idx]

    server_latencies = [r["processing_ms"] for r in results]
    sl = sorted(server_latencies)

    client_mean  = statistics.mean(cl)
    server_mean  = statistics.mean(sl)
    http_overhead = max(client_mean - server_mean, 0)

    print()
    print("─" * 70)
    print("LATENCY STATS (ms)")
    print(f"{'':18}  {'min':>6}  {'p50':>6}  {'p95':>6}  {'p99':>6}  {'max':>6}  {'mean':>6}")
    print(
        f"  {'Client (round-trip)':<16}  "
        f"{pct(cl,0):6.2f}  {pct(cl,50):6.2f}  "
        f"{pct(cl,95):6.2f}  {pct(cl,99):6.2f}  "
        f"{cl[-1]:6.2f}  {client_mean:6.2f}"
    )
    print(
        f"  {'Server (engine only)':<16}  "
        f"{pct(sl,0):6.2f}  {pct(sl,50):6.2f}  "
        f"{pct(sl,95):6.2f}  {pct(sl,99):6.2f}  "
        f"{sl[-1]:6.2f}  {server_mean:6.2f}"
    )
    print(f"  {'HTTP overhead (est.)':<16}  {'':>6}  {'':>6}  {'':>6}  {'':>6}  {'':>6}  {http_overhead:6.2f}")

    # Server-side step breakdown from debug endpoint
    print()
    resp = session.get(f"{BASE_URL}/debug/latency")
    resp.raise_for_status()
    dbg = resp.json()

    total_mean = dbg["total_mean_ms"]
    print("PIPELINE BREAKDOWN (mean ms/ball):")
    print(f"  {'Step':<18}  {'mean ms':>8}  {'share':>6}  bar")
    for step, info in sorted(dbg["steps"].items(), key=lambda x: -x[1]["mean_ms"]):
        bar = "█" * int(info["share_pct"] / 3)
        print(f"  {step:<18}  {info['mean_ms']:8.4f}  {info['share_pct']:5.1f}%  {bar}")
    print(f"  {'─'*18}  {'─'*8}")
    print(f"  {'TOTAL (engine)':<18}  {total_mean:8.4f}")
    print(f"  {'HTTP overhead':<18}  {http_overhead:8.4f}")

    bottleneck = max(dbg["steps"].items(), key=lambda x: x[1]["mean_ms"])
    print()
    print(f"  BOTTLENECK → {bottleneck[0]}  ({bottleneck[1]['share_pct']}% of engine time)")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
