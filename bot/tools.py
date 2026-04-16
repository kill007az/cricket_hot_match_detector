"""
bot/tools.py — Eight LangChain @tool functions that call the orchestrator API.

All tools are synchronous (requests-based). LangGraph wraps them in an executor
automatically when called from an async agent graph.

Chart tools (get_win_prob_curve, get_hotness_curve, get_forecast_overlay) store
their PNG bytes in _chart_cache keyed by chart type. agent.py drains this cache
after each agent run and sends the images to Telegram.

ORCHESTRATOR_URL is read from the environment (default: http://localhost:8080).
"""

from __future__ import annotations

import os

import requests
from langchain_core.tools import tool

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8080")

# Side-channel: chart tools deposit PNG bytes here; agent.py/main.py drain it.
_chart_cache: dict[str, bytes] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get(path: str) -> dict | list:
    resp = requests.get(f"{ORCHESTRATOR_URL}{path}", timeout=10)
    resp.raise_for_status()
    return resp.json()


def _current_match_id() -> str:
    data = _get("/matches/current")
    return data["match_id"]


def _current_history() -> list[dict]:
    match_id = _current_match_id()
    return _get(f"/matches/{match_id}/history")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def _overs_str(balls: int) -> str:
    """Convert ball count to 'X.Y ov' string."""
    return f"{balls // 6}.{balls % 6}"


@tool
def get_match_status() -> str:
    """Return a summary of the current live match: teams, live scores for both
    innings, win probability, hotness, forecast, and last signal fired."""
    try:
        data = _get("/matches/current")
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return "No active match found."
        raise

    last     = data.get("last_state", {})
    match_id = data.get("match_id", "")
    team1    = data.get("team1", "?")
    team2    = data.get("team2", "?")
    win_prob = last.get("win_prob")
    hotness  = last.get("hotness")
    forecast = last.get("forecast")
    rr       = last.get("runs_needed")
    br       = last.get("balls_remaining")
    wk       = last.get("wickets", 0)
    signals  = last.get("signals", [])

    phase = data.get("phase", "inn2")
    inn1_summary = data.get("inn1_summary", {})

    lines = [f"🏏 {team1} vs {team2}"]

    if phase == "inn1":
        # Inn1 still in progress — use authoritative summary from orchestrator
        inn1_runs  = inn1_summary.get("runs", 0)
        inn1_wkts  = inn1_summary.get("wickets", 0)
        inn1_overs = inn1_summary.get("overs", "0.0")
        lines.append(f"{team1}: {inn1_runs}/{inn1_wkts} ({inn1_overs} ov) — innings in progress")
        lines.append(f"{team2}: yet to bat")
        return "\n".join(lines)

    # --- Inn1 final score ---
    if inn1_summary.get("balls", 0) > 0:
        inn1_runs  = inn1_summary.get("runs", 0)
        inn1_wkts  = inn1_summary.get("wickets", 0)
        inn1_overs = inn1_summary.get("overs", "20.0")
        lines.append(f"{team1}: {inn1_runs}/{inn1_wkts} ({inn1_overs} ov)")

    # --- Inn2 live score (chasing team) ---
    try:
        inn2_events = _get(f"/matches/{match_id}/ball_events")
        inn2_runs   = sum(b.get("runs", 0) + b.get("extras", 0) for b in inn2_events)
        inn2_balls  = len(inn2_events)
        # Use scorecard team_total if innings is complete
        try:
            sc2 = _get(f"/matches/{match_id}/scorecard/2")
            if sc2.get("team_total") and br == 0:
                inn2_runs = sc2["team_total"]
        except Exception:
            pass
        lines.append(f"{team2}: {inn2_runs}/{wk} ({_overs_str(inn2_balls)} ov)")
    except Exception:
        pass

    # --- Chase summary ---
    if rr is not None and br is not None:
        if br > 0:
            lines.append(f"Need {rr} off {br} balls")
        else:
            if rr <= 0:
                lines.append(f"{team2} won")
            else:
                lines.append(f"{team1} won by {rr - 1} runs")

    # --- Engine metrics ---
    if win_prob is not None:
        lines.append(f"Win prob: {win_prob:.1%}  |  Hotness: {hotness:.1%}")
    if forecast is not None:
        lines.append(f"Forecast: {forecast:.1%}")
    if signals:
        lines.append(f"Last signal: {signals[-1]}")

    return "\n".join(lines)


@tool
def get_win_prob_curve() -> str:
    """Generate a win probability chart for the current match.
    Returns a description; the chart image is sent separately."""
    from bot.charts import win_prob_chart
    try:
        history = _current_history()
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return "No active match found."
        raise
    if not history:
        return "No ball data yet."
    png = win_prob_chart(history)
    _chart_cache["win_prob"] = png
    last_prob = history[-1].get("win_prob", 0)
    return f"Win probability chart generated ({len(history)} balls). Current: {last_prob:.1%}"


@tool
def get_hotness_curve() -> str:
    """Generate a hotness chart for the current match.
    Returns a description; the chart image is sent separately."""
    from bot.charts import hotness_chart
    try:
        history = _current_history()
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return "No active match found."
        raise
    if not history:
        return "No ball data yet."
    png = hotness_chart(history)
    _chart_cache["hotness"] = png
    peak = max(r.get("hotness", 0) or 0 for r in history)
    return f"Hotness chart generated ({len(history)} balls). Peak: {peak:.1%}"


@tool
def get_forecast_overlay() -> str:
    """Generate a hotness + forecast overlay chart for the current match.
    Returns a description; the chart image is sent separately."""
    from bot.charts import forecast_overlay_chart
    try:
        history = _current_history()
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return "No active match found."
        raise
    if not history:
        return "No ball data yet."
    png = forecast_overlay_chart(history)
    _chart_cache["forecast"] = png
    fc_balls = [r for r in history if r.get("forecast") is not None]
    if fc_balls:
        return (f"Forecast overlay chart generated. Forecast active from ball "
                f"{fc_balls[0]['ball']} ({len(fc_balls)} balls).")
    return "Forecast overlay chart generated (forecast not yet active — need 60 balls)."


@tool
def get_signal_timeline() -> str:
    """List all signals that have fired in the current match, ordered by ball number."""
    try:
        match_id = _current_match_id()
        events = _get(f"/matches/{match_id}/signals")
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return "No active match found."
        raise
    if not events:
        return "No signals have fired yet."
    lines = []
    for ev in events:
        for sig in ev.get("signals", []):
            lines.append(f"Ball {ev['ball']}: {sig}")
    return "\n".join(lines) if lines else "No signals have fired yet."


@tool
def get_key_turning_points(top_n: int = 5) -> str:
    """List the top N balls with the largest win probability swings (turning points)."""
    try:
        history = _current_history()
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return "No active match found."
        raise
    if len(history) < 2:
        return "Not enough data yet."

    signal_balls = {r["ball"] for r in history if r.get("signals")}
    deltas = []
    for i in range(1, len(history)):
        prev = history[i - 1].get("win_prob")
        curr = history[i].get("win_prob")
        if prev is None or curr is None:
            continue
        delta = curr - prev
        deltas.append((abs(delta), delta, history[i]))

    deltas.sort(reverse=True)
    lines = []
    for _, delta, row in deltas[:top_n]:
        ball = row["ball"]
        prev_wp = history[ball - 2].get("win_prob", 0)
        curr_wp = row.get("win_prob", 0)
        tags = []
        if row.get("wickets") and ball > 1 and (
            history[ball - 2].get("wickets", 0) < row.get("wickets", 0)
        ):
            tags.append("WICKET")
        if ball in signal_balls:
            tags.append("SIGNAL")
        tag_str = f"  [{', '.join(tags)}]" if tags else ""
        lines.append(
            f"Ball {ball}: {prev_wp:.1%} → {curr_wp:.1%}  "
            f"(Δ{delta:+.1%}){tag_str}"
        )
    return "\n".join(lines)


@tool
def get_ball_by_ball_table(last_n: int = 20) -> str:
    """Return the last N balls as a formatted table with win prob, hotness,
    forecast, runs needed, balls remaining, wickets, and signals."""
    try:
        history = _current_history()
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return "No active match found."
        raise
    if not history:
        return "No ball data yet."

    rows = history[-last_n:]
    header = f"{'Ball':>4}  {'Win%':>6}  {'Hot%':>6}  {'FC%':>6}  {'RR':>4}  {'BR':>4}  {'Wk':>2}  Signals"
    sep = "-" * len(header)
    lines = [header, sep]
    for r in rows:
        wp  = f"{r['win_prob']:.1%}"   if r.get("win_prob")   is not None else "—"
        ht  = f"{r['hotness']:.1%}"    if r.get("hotness")    is not None else "—"
        fc  = f"{r['forecast']:.1%}"   if r.get("forecast")   is not None else "—"
        rr  = str(r.get("runs_needed", "—"))
        br  = str(r.get("balls_remaining", "—"))
        wk  = str(r.get("wickets", "—"))
        sig = ", ".join(r.get("signals", []))
        lines.append(f"{r['ball']:>4}  {wp:>6}  {ht:>6}  {fc:>6}  {rr:>4}  {br:>4}  {wk:>2}  {sig}")
    return "\n".join(lines)


@tool
def list_matches() -> str:
    """List all recorded matches with ball count and completion status."""
    try:
        matches = _get("/matches")
    except Exception as e:
        return f"Error fetching matches: {e}"
    if not matches:
        return "No matches recorded yet."
    lines = []
    for m in matches:
        lines.append(
            f"{m['match_id']}  —  {m['balls_seen']} balls  "
            f"({m['team1']} vs {m['team2']}, {m['date']})"
        )
    return "\n".join(lines)


def _current_ball_events(innings: int = 2) -> list[dict]:
    match_id = _current_match_id()
    if innings == 1:
        try:
            return _get(f"/matches/{match_id}/ball_events_inn1")
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return []
            raise
    return _get(f"/matches/{match_id}/ball_events")


@tool
def get_match_scorecard(innings: int = 2) -> str:
    """Return an over-by-over scorecard for the current match.
    innings=1 for the first innings, innings=2 (default) for the chase."""
    try:
        events = _current_ball_events(innings)
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return "No active match found."
        raise
    if not events:
        return f"No innings {innings} data available yet."
    from collections import defaultdict
    overs: dict[int, dict] = defaultdict(lambda: {"runs": 0, "wickets": 0, "fours": 0, "sixes": 0})
    for b in events:
        ov = int(b.get("over", 0))   # over number (0-indexed)
        r  = b.get("runs", 0)
        overs[ov]["runs"]    += r + b.get("extras", 0)
        overs[ov]["wickets"] += 1 if b.get("wicket") else 0
        overs[ov]["fours"]   += 1 if r == 4 else 0
        overs[ov]["sixes"]   += 1 if r == 6 else 0

    header = f"{'Ov':>3}  {'Runs':>5}  {'Wkts':>5}  {'4s':>3}  {'6s':>3}"
    sep    = "-" * len(header)
    lines  = [header, sep]
    total_runs = 0
    for ov in sorted(overs):
        d = overs[ov]
        total_runs += d["runs"]
        lines.append(
            f"{ov+1:>3}  {d['runs']:>5}  {d['wickets']:>5}  {d['fours']:>3}  {d['sixes']:>3}"
        )
    lines.append(sep)
    lines.append(f"Total runs (incl. extras): {total_runs}")
    return "\n".join(lines)


@tool
def get_batting_summary(innings: int = 2) -> str:
    """Return batting totals for the current match: total runs, sixes, fours,
    dot balls, extras, and overall run rate.
    innings=1 for first innings, innings=2 (default) for the chase."""
    try:
        events = _current_ball_events(innings)
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return "No active match found."
        raise
    if not events:
        return f"No innings {innings} data available yet."

    sixes    = sum(1 for b in events if b.get("runs") == 6)
    fours    = sum(1 for b in events if b.get("runs") == 4)
    dots     = sum(1 for b in events if b.get("runs", 0) == 0 and b.get("extras", 0) == 0)
    extras   = sum(b.get("extras", 0) for b in events)
    wickets  = sum(1 for b in events if b.get("wicket"))
    balls    = len(events)

    # Use authoritative team total from scorecard (includes wide/no-ball runs)
    # Fall back to summing ball_events if scorecard not available
    total_runs = sum(b.get("runs", 0) + b.get("extras", 0) for b in events)
    try:
        match_id = _current_match_id()
        sc = _get(f"/matches/{match_id}/scorecard/{innings}")
        if sc.get("team_total"):
            total_runs = sc["team_total"]
    except Exception:
        pass

    run_rate = (total_runs / balls * 6) if balls else 0

    return (
        f"Balls bowled : {balls}\n"
        f"Total runs   : {total_runs}\n"
        f"Wickets      : {wickets}\n"
        f"Run rate     : {run_rate:.2f}\n"
        f"Sixes (6s)   : {sixes}\n"
        f"Fours (4s)   : {fours}\n"
        f"Dot balls    : {dots}\n"
        f"Extras       : {extras}"
    )


@tool
def get_batting_card(innings: int = 2) -> str:
    """Return the batting scorecard for the current match.
    innings=1 for first innings, innings=2 (default) for the chase.
    Shows each batter: runs, balls, 4s, 6s, strike rate."""
    try:
        match_id = _current_match_id()
        data = _get(f"/matches/{match_id}/scorecard/{innings}")
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return f"Scorecard for innings {innings} not available yet."
        raise

    rows = data.get("batting", [])
    if not rows:
        return "No batting data available."

    striker  = data.get("current_striker")
    not_out  = set(data.get("not_out", []))

    header = f"{'Batter':<22} {'R':>4} {'B':>4} {'4s':>3} {'6s':>3} {'SR':>6}  Status"
    sep    = "-" * len(header)
    lines  = [header, sep]
    for r in rows:
        if r["name"] in not_out:
            status = "not out *" + (" ← on strike" if r["name"] == striker else "")
        else:
            status = "out"
        lines.append(
            f"{r['name']:<22} {r['runs']:>4} {r['balls']:>4} "
            f"{r['fours']:>3} {r['sixes']:>3} {r['strike_rate']:>6.1f}  {status}"
        )
    lines.append(sep)
    not_out_list = ", ".join(sorted(not_out)) if not_out else "unknown"
    lines.append(f"Not out: {not_out_list}")
    return "\n".join(lines)


@tool
def get_bowling_card(innings: int = 2) -> str:
    """Return the bowling scorecard for the current match.
    innings=1 for first innings, innings=2 (default) for the chase.
    Shows each bowler: overs, runs, wickets, economy."""
    try:
        match_id = _current_match_id()
        data = _get(f"/matches/{match_id}/scorecard/{innings}")
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return f"Scorecard for innings {innings} not available yet."
        raise

    rows = data.get("bowling", [])
    if not rows:
        return "No bowling data available."

    header = f"{'Bowler':<22} {'Ov':>4} {'R':>4} {'W':>3} {'Eco':>5} {'Wd':>3} {'NB':>3}"
    sep    = "-" * len(header)
    lines  = [header, sep]
    for r in rows:
        lines.append(
            f"{r['name']:<22} {r['overs']:>4} {r['runs']:>4} "
            f"{r['wickets']:>3} {r['economy']:>5.1f} {r['wides']:>3} {r['noballs']:>3}"
        )
    return "\n".join(lines)


@tool
def run_python(code: str) -> str:
    """Execute Python code to answer analytical questions about the current match.

    The following variables are pre-loaded in the execution context:
      history          — list of dicts from /matches/{id}/history (2nd innings engine outputs)
                         keys: ball, win_prob, hotness, forecast, runs_needed,
                               balls_remaining, wickets, signals
      ball_events      — list of dicts: 2nd innings (chase) ball-by-ball
                         keys: innings, over, runs, extras, wicket
      ball_events_inn1 — list of dicts: 1st innings ball-by-ball (empty list if not yet recorded)
                         same keys as ball_events

    Print your answer — anything written to stdout is returned.
    Example: print(sum(1 for b in ball_events if b['runs'] == 6))
    Example: print(sum(1 for b in ball_events_inn1 if b['runs'] == 6))
    """
    import contextlib, io as _io
    try:
        history          = _current_history()
        ball_events      = _current_ball_events(innings=2)
        ball_events_inn1 = _current_ball_events(innings=1)
    except requests.HTTPError as e:
        if e.response.status_code == 404:
            return "No active match found."
        raise

    stdout_buf = _io.StringIO()
    try:
        with contextlib.redirect_stdout(stdout_buf):
            exec(code, {  # noqa: S102
                "history": history,
                "ball_events": ball_events,
                "ball_events_inn1": ball_events_inn1,
            })
    except Exception as exc:
        return f"Error: {exc}"

    output = stdout_buf.getvalue().strip()
    return output if output else "(no output)"


@tool
def get_schedule(team: str = "") -> str:
    """
    Return upcoming IPL 2026 fixtures.

    Parameters
    ----------
    team : str, optional
        Team abbreviation to filter (e.g. "CSK", "KKR").  Leave blank for all upcoming matches.
    """
    path = "/schedule"
    if team:
        path += f"?team={team.upper()}"
    data = _get(path)
    matches = data.get("matches", [])
    if not matches:
        return "No upcoming matches found."
    lines = []
    for m in matches[:10]:
        lines.append(
            f"M{m['match']:>2}  {m['home_abbr']} vs {m['away_abbr']}"
            f"  {m['date']} {m['time_ist']} IST  ({m['venue']})"
        )
    total = data.get("total", len(matches))
    if total > 10:
        lines.append(f"... and {total - 10} more matches.")
    return "\n".join(lines)


ALL_TOOLS = [
    get_match_status,
    get_win_prob_curve,
    get_hotness_curve,
    get_forecast_overlay,
    get_signal_timeline,
    get_key_turning_points,
    get_ball_by_ball_table,
    get_match_scorecard,
    get_batting_summary,
    get_batting_card,
    get_bowling_card,
    run_python,
    list_matches,
    get_schedule,
]
