"""
bot/charts.py — Matplotlib chart generation returning PNG bytes.

All functions accept the history list from GET /matches/{id}/history:
    [{"ball": int, "win_prob": float, "hotness": float, "forecast": float|None, ...}]

Output is always raw PNG bytes (no disk I/O).
Charts are 1200x480px (figsize 12x4.8 @ 100 dpi).
"""

import io

import matplotlib
matplotlib.use("Agg")  # must be before any other matplotlib import
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker


_FIG_SIZE = (12, 4.8)
_DPI = 100
_SIGNAL_COLOR = "#e74c3c"
_GRID_ALPHA = 0.25


def _signal_balls(history: list[dict]) -> list[int]:
    return [row["ball"] for row in history if row.get("signals")]


def _to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=_DPI, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def win_prob_chart(history: list[dict]) -> bytes:
    balls = [r["ball"] for r in history]
    probs = [r["win_prob"] for r in history]

    fig, ax = plt.subplots(figsize=_FIG_SIZE)
    ax.plot(balls, probs, color="#2980b9", linewidth=1.5, label="Win prob")
    ax.axhline(0.5, color="#888", linewidth=1, linestyle="--", label="50%")

    for b in _signal_balls(history):
        ax.axvline(b, color=_SIGNAL_COLOR, linewidth=1, linestyle=":", alpha=0.8)

    ax.set_xlabel("Ball")
    ax.set_ylabel("Win probability")
    ax.set_title("Win Probability")
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1))
    ax.set_ylim(0, 1)
    ax.grid(alpha=_GRID_ALPHA)
    ax.legend(fontsize=9)
    fig.tight_layout()
    return _to_png(fig)


def hotness_chart(history: list[dict]) -> bytes:
    balls = [r["ball"] for r in history]
    hotness = [r["hotness"] for r in history]

    peak_ball = max(range(len(hotness)), key=lambda i: hotness[i] or 0)

    fig, ax = plt.subplots(figsize=_FIG_SIZE)
    ax.plot(balls, hotness, color="#e67e22", linewidth=1.5, label="Hotness")

    # Annotate peak
    ax.annotate(
        f"Peak {hotness[peak_ball]:.1%}",
        xy=(balls[peak_ball], hotness[peak_ball]),
        xytext=(10, -15),
        textcoords="offset points",
        fontsize=8,
        arrowprops=dict(arrowstyle="->", color="#555"),
    )

    for b in _signal_balls(history):
        ax.axvline(b, color=_SIGNAL_COLOR, linewidth=1, linestyle=":", alpha=0.8)

    ax.set_xlabel("Ball")
    ax.set_ylabel("Hotness")
    ax.set_title("Hotness")
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1))
    ax.set_ylim(0, 1)
    ax.grid(alpha=_GRID_ALPHA)
    ax.legend(fontsize=9)
    fig.tight_layout()
    return _to_png(fig)


def forecast_overlay_chart(history: list[dict]) -> bytes:
    balls = [r["ball"] for r in history]
    hotness = [r["hotness"] for r in history]
    # forecast is None before ball 60 — matplotlib skips None gaps automatically
    forecast = [r.get("forecast") for r in history]

    fig, ax = plt.subplots(figsize=_FIG_SIZE)
    ax.plot(balls, hotness, color="#e67e22", linewidth=1.5, label="Hotness")

    # Plot forecast, skipping None values
    fc_balls = [b for b, f in zip(balls, forecast) if f is not None]
    fc_vals  = [f for f in forecast if f is not None]
    if fc_balls:
        ax.plot(fc_balls, fc_vals, color="#8e44ad", linewidth=1.5,
                linestyle="--", label="Forecast")
        ax.axvline(fc_balls[0], color="#8e44ad", linewidth=1,
                   linestyle=":", alpha=0.6, label="Forecast gate")
        ax.text(fc_balls[0] + 1, 0.02, "forecast gate", fontsize=7,
                color="#8e44ad", alpha=0.8)

    for b in _signal_balls(history):
        ax.axvline(b, color=_SIGNAL_COLOR, linewidth=1, linestyle=":", alpha=0.8)

    ax.set_xlabel("Ball")
    ax.set_ylabel("Value")
    ax.set_title("Hotness + Forecast Overlay")
    ax.yaxis.set_major_formatter(mticker.PercentFormatter(xmax=1))
    ax.set_ylim(0, 1)
    ax.grid(alpha=_GRID_ALPHA)
    ax.legend(fontsize=9)
    fig.tight_layout()
    return _to_png(fig)
