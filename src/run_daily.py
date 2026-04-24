from __future__ import annotations

import json
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import requests
import snowflake.connector
import yfinance as yf

from strategy import MarketRegime, PickResult, compute_market_regime, score_symbol


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
CHART_DIR = OUTPUT_DIR / "charts"
WATCHLIST_PATH = ROOT / "watchlist.json"
HISTORY_PATH = OUTPUT_DIR / "history.csv"

SECTOR_MAP: dict[str, str] = {
    "AAPL": "Tech", "NOW": "Tech", "MSFT": "Tech", "NVDA": "Tech",
    "AMZN": "Consumer/Cloud", "META": "Tech", "GOOGL": "Tech",
    "TSLA": "Consumer/EV", "AMD": "Tech", "AVGO": "Tech",
    "NFLX": "Media", "PLTR": "Tech", "CRM": "Tech", "ADBE": "Tech",
    "ORCL": "Tech", "UBER": "Consumer", "SHOP": "Consumer/Tech",
    "QQQ": "ETF", "SPY": "ETF", "SMH": "ETF", "SOXX": "ETF",
}

MARKET_SYMBOLS = {"SPY", "QQQ"}


def log_status(stage: str, message: str) -> None:
    print(f"[{stage}] {message}", flush=True)


def load_watchlist() -> list[str]:
    symbols = json.loads(WATCHLIST_PATH.read_text())
    return [s.strip().upper() for s in symbols if isinstance(s, str) and s.strip()]


def fetch_daily_history(symbol: str) -> pd.DataFrame:
    history = yf.Ticker(symbol).history(period="1y", interval="1d", auto_adjust=False)
    return history if history is not None else pd.DataFrame()


def fetch_intraday_history(symbol: str) -> pd.DataFrame:
    history = yf.Ticker(symbol).history(period="5d", interval="5m", auto_adjust=False, prepost=True)
    return history if history is not None else pd.DataFrame()


def check_earnings_soon(symbol: str) -> bool:
    try:
        cal = yf.Ticker(symbol).calendar
        if cal is None:
            return False
        if isinstance(cal, dict):
            date_val = cal.get("Earnings Date")
            if date_val is None:
                return False
            if isinstance(date_val, list):
                date_val = date_val[0]
            earnings_date = pd.Timestamp(date_val).date()
        elif isinstance(cal, pd.DataFrame):
            if cal.empty:
                return False
            earnings_date = pd.Timestamp(cal.iloc[0, 0]).date()
        else:
            return False
        today = datetime.now(timezone.utc).date()
        delta = (earnings_date - today).days
        return 0 <= delta <= 3
    except Exception:
        return False


def build_dataframe(results: list[PickResult]) -> pd.DataFrame:
    rows = [
        {
            "Symbol": pick.symbol,
            "Sector": SECTOR_MAP.get(pick.symbol, "Other"),
            "Action": pick.action,
            "Score": pick.score,
            "Last Price": round(pick.last_price, 2),
            "Entry": round(pick.suggested_entry, 2),
            "Stop": round(pick.stop_loss, 2),
            "Target": round(pick.target, 2),
            "RSI": round(pick.rsi14, 1),
            "52W High %": round(pick.pct_from_52w_high * 100, 1),
            "From Open %": round(pick.intraday_change_pct * 100, 2),
            "Vs Prior Close %": round(pick.day_change_pct * 100, 2),
            "5D %": round(pick.return_5d * 100, 2),
            "20D %": round(pick.return_20d * 100, 2),
            "SMA20": round(pick.sma20, 2),
            "SMA50": round(pick.sma50, 2),
            "Daily Vol x": round(pick.volume_ratio, 2),
            "Intraday Vol x": round(pick.intraday_volume_ratio, 2),
            "VWAP Gap %": round(pick.vwap_distance_pct * 100, 2),
            "Earnings Soon": "⚠️" if pick.earnings_soon else "",
            "Reason": pick.reason,
        }
        for pick in results
    ]
    return pd.DataFrame(rows)


def append_history(results_df: pd.DataFrame, run_date: str) -> None:
    if results_df.empty:
        return
    history_df = results_df.copy()
    history_df.insert(0, "Date", run_date)
    if HISTORY_PATH.exists():
        history_df.to_csv(HISTORY_PATH, mode="a", header=False, index=False)
    else:
        history_df.to_csv(HISTORY_PATH, mode="w", header=True, index=False)
    log_status("HISTORY", f"Appended {len(history_df)} rows to {HISTORY_PATH}.")


def prepare_snowflake_dataframe(results_df: pd.DataFrame, generated_at_iso: str) -> pd.DataFrame:
    if results_df.empty:
        return pd.DataFrame()
    upload_df = results_df[
        ["Symbol", "Action", "Score", "Last Price", "Entry", "Stop", "Target",
         "RSI", "From Open %", "Vs Prior Close %", "5D %", "20D %",
         "Daily Vol x", "Intraday Vol x", "VWAP Gap %", "Reason"]
    ].copy()
    upload_df.insert(0, "run_ts", generated_at_iso)
    upload_df.columns = [
        "run_ts", "symbol", "action", "score", "last_price", "entry",
        "stop_loss", "target", "rsi", "from_open_pct", "vs_prior_close_pct",
        "return_5d_pct", "return_20d_pct", "daily_volume_ratio",
        "intraday_volume_ratio", "vwap_gap_pct", "reason",
    ]
    return upload_df


def action_badge(action: str) -> str:
    colors = {
        "Buy Watch": ("#14532d", "#dcfce7"),
        "Watch": ("#92400e", "#fef3c7"),
        "Skip": ("#991b1b", "#fee2e2"),
    }
    text_color, bg_color = colors.get(action, ("#1f2937", "#e5e7eb"))
    return (
        f'<span style="display:inline-block;padding:4px 10px;border-radius:999px;'
        f'background:{bg_color};color:{text_color};font-weight:700;font-size:12px;">{action}</span>'
    )


def regime_badge(regime: str) -> str:
    configs = {
        "bullish": ("🟢", "#14532d", "#dcfce7", "BULLISH"),
        "neutral": ("🟡", "#92400e", "#fef3c7", "NEUTRAL"),
        "weak": ("🔴", "#991b1b", "#fee2e2", "WEAK"),
    }
    icon, tc, bg, label = configs.get(regime, ("⚪", "#1f2937", "#e5e7eb", regime.upper()))
    return (
        f'<span style="display:inline-block;padding:6px 14px;border-radius:999px;'
        f'background:{bg};color:{tc};font-weight:800;font-size:13px;">{icon} Market {label}</span>'
    )


def _fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def build_report(results: list[PickResult], generated_at: str, regime: MarketRegime | None) -> str:
    lines = [
        "# Intraday Stock Picks",
        "",
        f"Generated at: {generated_at}",
        "",
    ]

    if regime:
        lines.append(f"Market regime: {regime.regime.upper()} (SPY score {regime.spy_score}/5)")
        lines.append(f"SPY: ${regime.spy_last:.2f} | SMA20: ${regime.spy_sma20:.2f} | 5D: {_fmt_pct(regime.spy_return_5d)}")
        lines.append("")

    if not results:
        lines.append("No picks were generated today.")
        return "\n".join(lines)

    buy_watch = [r for r in results if r.action == "Buy Watch"]
    watch = [r for r in results if r.action == "Watch"]
    skip = [r for r in results if r.action == "Skip"]
    lines.append(f"Summary: {len(buy_watch)} Buy Watch | {len(watch)} Watch | {len(skip)} Skip out of {len(results)} scanned")
    lines.append("")

    leaders = results[:10]
    for idx, pick in enumerate(leaders, start=1):
        earnings_flag = " ⚠️ EARNINGS SOON" if pick.earnings_soon else ""
        lines.extend([
            f"## {idx}. {pick.symbol} ({pick.action}){earnings_flag}",
            f"- Score: {pick.score}",
            f"- Last price: ${pick.last_price:.2f}  |  SMA20: ${pick.sma20:.2f}  |  SMA50: ${pick.sma50:.2f}",
            f"- Entry: ${pick.suggested_entry:.2f}  |  Stop: ${pick.stop_loss:.2f}  |  Target: ${pick.target:.2f}",
            f"- RSI(14): {pick.rsi14:.0f}  |  52W High: {_fmt_pct(pick.pct_from_52w_high)} from high",
            f"- From open: {_fmt_pct(pick.intraday_change_pct)}  |  Vs prior close: {_fmt_pct(pick.day_change_pct)}",
            f"- 5-day momentum: {_fmt_pct(pick.return_5d)}  |  20-day momentum: {_fmt_pct(pick.return_20d)}",
            f"- Daily volume ratio: {pick.volume_ratio:.2f}x  |  Intraday volume ratio: {pick.intraday_volume_ratio:.2f}x",
            f"- VWAP gap: {_fmt_pct(pick.vwap_distance_pct)}",
            f"- Reason: {pick.reason}",
            "",
        ])

    lines.append("## Full Ranked Table")
    lines.append("")
    for pick in results:
        earnings_flag = " ⚠️" if pick.earnings_soon else ""
        lines.append(
            f"- {pick.symbol}{earnings_flag}: {pick.action}, score {pick.score}, "
            f"entry ${pick.suggested_entry:.2f}, stop ${pick.stop_loss:.2f}, target ${pick.target:.2f}, "
            f"RSI {pick.rsi14:.0f}, from open {_fmt_pct(pick.intraday_change_pct)}"
        )

    return "\n".join(lines)


def build_html_report(results: list[PickResult], generated_at: str, regime: MarketRegime | None) -> str:
    if not results:
        return (
            "<html><body><h1>Intraday Stock Picks</h1>"
            f"<p>Generated at: {generated_at}</p><p>No picks were generated today.</p></body></html>"
        )

    buy_watch_count = sum(1 for r in results if r.action == "Buy Watch")
    watch_count = sum(1 for r in results if r.action == "Watch")
    skip_count = sum(1 for r in results if r.action == "Skip")

    regime_html = ""
    if regime:
        spy_color = "#166534" if regime.spy_return_5d >= 0 else "#b91c1c"
        regime_html = f"""
        <div style="display:flex;align-items:center;gap:20px;flex-wrap:wrap;margin-top:16px;
                    background:rgba(255,255,255,0.08);border-radius:12px;padding:14px 18px;">
          <div>{regime_badge(regime.regime)}</div>
          <div style="font-size:13px;opacity:0.9;">
            SPY <strong>${regime.spy_last:.2f}</strong> &nbsp;|&nbsp;
            SMA20 <strong>${regime.spy_sma20:.2f}</strong> &nbsp;|&nbsp;
            5D <strong style="color:{spy_color};">{_fmt_pct(regime.spy_return_5d)}</strong>
          </div>
          <div style="font-size:13px;opacity:0.9;">
            QQQ <strong>${regime.qqq_last:.2f}</strong> &nbsp;|&nbsp;
            SMA20 <strong>${regime.qqq_sma20:.2f}</strong> &nbsp;|&nbsp;
            5D <strong style="color:{spy_color};">{_fmt_pct(regime.qqq_return_5d)}</strong>
          </div>
        </div>
        """

    summary_cards = []
    for pick in results[:3]:
        rr = round((pick.target - pick.suggested_entry) / (pick.suggested_entry - pick.stop_loss), 1) if (pick.suggested_entry - pick.stop_loss) > 0 else 0
        earnings_note = '<div style="font-size:11px;color:#fbbf24;margin-top:4px;">⚠️ Earnings soon</div>' if pick.earnings_soon else ""
        summary_cards.append(f"""
        <div style="flex:1;min-width:200px;background:#0f172a;color:#f8fafc;border-radius:16px;padding:18px;">
          <div style="font-size:12px;opacity:0.7;">Top setup · {SECTOR_MAP.get(pick.symbol, "Other")}</div>
          <div style="font-size:28px;font-weight:800;margin-top:4px;">{pick.symbol}</div>
          <div style="margin-top:8px;">{action_badge(pick.action)}</div>
          {earnings_note}
          <div style="font-size:28px;font-weight:800;margin-top:10px;">{pick.score}</div>
          <div style="margin-top:10px;font-size:12px;display:grid;grid-template-columns:1fr 1fr;gap:4px;">
            <div>Entry <strong>${pick.suggested_entry:.2f}</strong></div>
            <div>Stop <strong style="color:#f87171;">${pick.stop_loss:.2f}</strong></div>
            <div>Target <strong style="color:#4ade80;">${pick.target:.2f}</strong></div>
            <div>R:R <strong>{rr}x</strong></div>
            <div>RSI <strong>{pick.rsi14:.0f}</strong></div>
            <div>52W <strong>{pick.pct_from_52w_high*100:+.1f}%</strong></div>
          </div>
        </div>
        """)

    rows = []
    for pick in results:
        row_bg = "#ffffff" if pick.action != "Skip" else "#f8fafc"
        earnings_cell = '<span style="color:#d97706;font-weight:700;">⚠️</span>' if pick.earnings_soon else ""
        rsi_color = "#166534" if 50 <= pick.rsi14 <= 70 else ("#b91c1c" if pick.rsi14 > 80 or pick.rsi14 < 35 else "#374151")
        rows.append(f"""
        <tr style="background:{row_bg};">
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-weight:700;">{pick.symbol}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-size:12px;color:#64748b;">{SECTOR_MAP.get(pick.symbol, "Other")}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;">{action_badge(pick.action)}{earnings_cell}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-weight:700;">{pick.score}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;">${pick.last_price:.2f}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;font-weight:600;">${pick.suggested_entry:.2f}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;color:#b91c1c;font-weight:600;">${pick.stop_loss:.2f}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;color:#166534;font-weight:600;">${pick.target:.2f}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;color:{rsi_color};font-weight:600;">{pick.rsi14:.0f}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;">{pick.pct_from_52w_high*100:+.1f}%</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;">${pick.sma20:.2f}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;">${pick.sma50:.2f}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;color:{'#166534' if pick.intraday_change_pct >= 0 else '#b91c1c'};">{_fmt_pct(pick.intraday_change_pct)}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;color:{'#166534' if pick.day_change_pct >= 0 else '#b91c1c'};">{_fmt_pct(pick.day_change_pct)}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;">{pick.intraday_volume_ratio:.2f}x</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;">{_fmt_pct(pick.vwap_distance_pct)}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #e5e7eb;max-width:380px;font-size:12px;">{pick.reason}</td>
        </tr>
        """)

    return f"""
    <html>
      <body style="margin:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#0f172a;">
        <div style="max-width:1300px;margin:0 auto;padding:32px 20px;">

          <!-- Header -->
          <div style="background:linear-gradient(135deg,#0f172a,#1d4ed8);border-radius:24px;padding:28px;color:#f8fafc;">
            <div style="font-size:13px;opacity:0.8;">Intraday watchlist dashboard</div>
            <h1 style="margin:6px 0;font-size:32px;font-weight:800;">Daily Stock Picks</h1>
            <p style="margin:0;font-size:14px;opacity:0.85;">Generated at {generated_at} · Ranked with trend, RSI, VWAP, momentum, and volume</p>
            {regime_html}
          </div>

          <!-- Run summary -->
          <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:16px;">
            <div style="flex:1;min-width:140px;background:#dcfce7;border-radius:16px;padding:16px;text-align:center;">
              <div style="font-size:28px;font-weight:800;color:#14532d;">{buy_watch_count}</div>
              <div style="font-size:13px;color:#166534;font-weight:600;">Buy Watch</div>
            </div>
            <div style="flex:1;min-width:140px;background:#fef3c7;border-radius:16px;padding:16px;text-align:center;">
              <div style="font-size:28px;font-weight:800;color:#92400e;">{watch_count}</div>
              <div style="font-size:13px;color:#92400e;font-weight:600;">Watch</div>
            </div>
            <div style="flex:1;min-width:140px;background:#fee2e2;border-radius:16px;padding:16px;text-align:center;">
              <div style="font-size:28px;font-weight:800;color:#991b1b;">{skip_count}</div>
              <div style="font-size:13px;color:#991b1b;font-weight:600;">Skip</div>
            </div>
            <div style="flex:1;min-width:140px;background:#e0f2fe;border-radius:16px;padding:16px;text-align:center;">
              <div style="font-size:28px;font-weight:800;color:#0369a1;">{len(results)}</div>
              <div style="font-size:13px;color:#0369a1;font-weight:600;">Total Scanned</div>
            </div>
          </div>

          <!-- Top 3 cards -->
          <div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:16px;">
            {''.join(summary_cards)}
          </div>

          <!-- Full table -->
          <div style="background:#ffffff;border-radius:24px;padding:20px;margin-top:20px;
                      box-shadow:0 10px 30px rgba(15,23,42,0.08);overflow-x:auto;">
            <h2 style="margin:0 0 6px;font-size:20px;">Full ranked table</h2>
            <p style="margin:0 0 16px;color:#475569;font-size:13px;">
              All symbols ranked by score. Entry, stop, and target are indicative only — not financial advice.
            </p>
            <table style="width:100%;border-collapse:collapse;font-size:13px;">
              <thead>
                <tr style="background:#eff6ff;text-align:left;">
                  <th style="padding:10px 12px;border-bottom:2px solid #bfdbfe;">Symbol</th>
                  <th style="padding:10px 12px;border-bottom:2px solid #bfdbfe;">Sector</th>
                  <th style="padding:10px 12px;border-bottom:2px solid #bfdbfe;">Action</th>
                  <th style="padding:10px 12px;border-bottom:2px solid #bfdbfe;">Score</th>
                  <th style="padding:10px 12px;border-bottom:2px solid #bfdbfe;">Last</th>
                  <th style="padding:10px 12px;border-bottom:2px solid #bfdbfe;">Entry</th>
                  <th style="padding:10px 12px;border-bottom:2px solid #bfdbfe;">Stop</th>
                  <th style="padding:10px 12px;border-bottom:2px solid #bfdbfe;">Target</th>
                  <th style="padding:10px 12px;border-bottom:2px solid #bfdbfe;">RSI</th>
                  <th style="padding:10px 12px;border-bottom:2px solid #bfdbfe;">52W Hi%</th>
                  <th style="padding:10px 12px;border-bottom:2px solid #bfdbfe;">SMA20</th>
                  <th style="padding:10px 12px;border-bottom:2px solid #bfdbfe;">SMA50</th>
                  <th style="padding:10px 12px;border-bottom:2px solid #bfdbfe;">From Open</th>
                  <th style="padding:10px 12px;border-bottom:2px solid #bfdbfe;">Vs Close</th>
                  <th style="padding:10px 12px;border-bottom:2px solid #bfdbfe;">Intra Vol</th>
                  <th style="padding:10px 12px;border-bottom:2px solid #bfdbfe;">VWAP Gap</th>
                  <th style="padding:10px 12px;border-bottom:2px solid #bfdbfe;">Reason</th>
                </tr>
              </thead>
              <tbody>
                {''.join(rows)}
              </tbody>
            </table>
          </div>

          <!-- Charts -->
          <div style="background:#ffffff;border-radius:24px;padding:20px;margin-top:20px;
                      box-shadow:0 10px 30px rgba(15,23,42,0.08);">
            <h2 style="margin:0 0 10px;font-size:20px;">Charts</h2>
            <div style="display:flex;gap:16px;flex-wrap:wrap;">
              <img src="cid:scores_chart" alt="Score ranking"
                   style="max-width:100%;width:500px;border-radius:16px;border:1px solid #e2e8f0;" />
              <img src="cid:momentum_chart" alt="Momentum vs volume"
                   style="max-width:100%;width:500px;border-radius:16px;border:1px solid #e2e8f0;" />
              <img src="cid:rsi_chart" alt="RSI distribution"
                   style="max-width:100%;width:500px;border-radius:16px;border:1px solid #e2e8f0;" />
            </div>
          </div>

          <div style="margin-top:20px;padding:16px;background:#f8fafc;border-radius:12px;
                      font-size:12px;color:#64748b;text-align:center;">
            This report is a watchlist generator only. It is not financial advice.
            Entry, stop, and target levels are indicative and based on simple rules.
            Always do your own research before trading.
          </div>
        </div>
      </body>
    </html>
    """


def build_telegram_text(results: list[PickResult], generated_at: str, regime: MarketRegime | None) -> str:
    if not results:
        return f"Daily Stock Picks\n{generated_at}\n\nNo strong candidates today."

    regime_line = ""
    if regime:
        icon = "🟢" if regime.regime == "bullish" else ("🟡" if regime.regime == "neutral" else "🔴")
        regime_line = f"{icon} Market {regime.regime.upper()} · SPY ${regime.spy_last:.2f} ({_fmt_pct(regime.spy_return_5d)} 5D)\n\n"

    buy_watch = [r for r in results if r.action == "Buy Watch"]
    watch = [r for r in results if r.action == "Watch"]

    lines = [f"📊 Daily Stock Picks", generated_at, "", regime_line]

    if buy_watch:
        lines.append("🟢 BUY WATCH")
        for pick in buy_watch[:5]:
            earnings_flag = " ⚠️" if pick.earnings_soon else ""
            lines.append(
                f"  {pick.symbol}{earnings_flag} · Score {pick.score} · RSI {pick.rsi14:.0f}\n"
                f"  Entry ${pick.suggested_entry:.2f} · Stop ${pick.stop_loss:.2f} · Target ${pick.target:.2f}\n"
                f"  From open {_fmt_pct(pick.intraday_change_pct)} · Vol {pick.intraday_volume_ratio:.2f}x"
            )
        lines.append("")

    if watch:
        lines.append("🟡 WATCH")
        for pick in watch[:5]:
            lines.append(f"  {pick.symbol} · Score {pick.score} · Entry ${pick.suggested_entry:.2f}")
        lines.append("")

    return "\n".join(lines)


def create_score_chart(df: pd.DataFrame, path: Path) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    chart_df = df.head(15).iloc[::-1]
    fig, ax = plt.subplots(figsize=(10, 7))
    color_map = {"Buy Watch": "#16a34a", "Watch": "#f59e0b", "Skip": "#94a3b8"}
    colors = [color_map.get(a, "#94a3b8") for a in chart_df["Action"]]
    bars = ax.barh(chart_df["Symbol"], chart_df["Score"], color=colors)
    ax.set_title("Watchlist Score Ranking", fontsize=16, fontweight="bold", pad=12)
    ax.set_xlabel("Score")
    for bar, score in zip(bars, chart_df["Score"]):
        ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
                str(score), va="center", fontsize=10)
    from matplotlib.patches import Patch
    legend = [Patch(color=c, label=l) for l, c in color_map.items()]
    ax.legend(handles=legend, frameon=False, loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def create_momentum_chart(df: pd.DataFrame, path: Path) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(10, 6))
    action_colors = {"Buy Watch": "#16a34a", "Watch": "#f59e0b", "Skip": "#dc2626"}
    for action, group in df.groupby("Action"):
        ax.scatter(
            group["From Open %"], group["Intraday Vol x"],
            s=group["Score"].clip(lower=10) * 4,
            alpha=0.8, label=action,
            color=action_colors.get(action, "#64748b"),
        )
        for _, row in group.iterrows():
            ax.text(row["From Open %"] + 0.03, row["Intraday Vol x"] + 0.01,
                    row["Symbol"], fontsize=8)
    ax.axvline(0, color="#94a3b8", linewidth=1)
    ax.set_title("Intraday Momentum vs Volume", fontsize=16, fontweight="bold")
    ax.set_xlabel("From Open %")
    ax.set_ylabel("Intraday Volume Ratio")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def create_rsi_chart(df: pd.DataFrame, path: Path) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    chart_df = df[df["Action"] != "Skip"].sort_values("RSI", ascending=True)
    if chart_df.empty:
        chart_df = df.sort_values("RSI", ascending=True)
    fig, ax = plt.subplots(figsize=(10, 6))
    color_map = {"Buy Watch": "#16a34a", "Watch": "#f59e0b", "Skip": "#94a3b8"}
    colors = [color_map.get(a, "#94a3b8") for a in chart_df["Action"]]
    ax.barh(chart_df["Symbol"], chart_df["RSI"], color=colors)
    ax.axvline(70, color="#dc2626", linewidth=1, linestyle="--", label="Overbought (70)")
    ax.axvline(50, color="#16a34a", linewidth=1, linestyle="--", label="Midline (50)")
    ax.axvline(35, color="#f59e0b", linewidth=1, linestyle="--", label="Oversold (35)")
    ax.set_title("RSI(14) by Symbol", fontsize=16, fontweight="bold")
    ax.set_xlabel("RSI")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def send_telegram_message(text: str) -> None:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print("Telegram secrets not configured; skipping.")
        return
    response = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": chat_id, "text": text[:3900], "disable_web_page_preview": True},
        timeout=30,
    )
    response.raise_for_status()


def send_email_report(
    subject: str, text_body: str, html_body: str, attachment_paths: dict[str, Path]
) -> None:
    email_from = os.getenv("EMAIL_FROM")
    email_to = os.getenv("EMAIL_TO")
    email_app_password = os.getenv("EMAIL_APP_PASSWORD")
    if not email_from or not email_to or not email_app_password:
        print("Email secrets not configured; skipping.")
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = email_from
    msg["To"] = email_to
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    html_part = msg.get_payload()[1]
    for cid, path in attachment_paths.items():
        if not path.exists():
            continue
        with path.open("rb") as fh:
            html_part.add_related(fh.read(), maintype="image", subtype="png",
                                  cid=f"<{cid}>", filename=path.name)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(email_from, email_app_password)
        smtp.send_message(msg)


def write_results_to_snowflake(upload_df: pd.DataFrame) -> None:
    if upload_df.empty:
        log_status("SNOWFLAKE", "No rows to upload; skipping.")
        return
    account = os.getenv("SNOWFLAKE_ACCOUNT")
    user = os.getenv("SNOWFLAKE_USER")
    password = os.getenv("SNOWFLAKE_PASSWORD")
    warehouse = os.getenv("SNOWFLAKE_WAREHOUSE")
    database = os.getenv("SNOWFLAKE_DATABASE")
    schema = os.getenv("SNOWFLAKE_SCHEMA")
    table = os.getenv("SNOWFLAKE_TABLE", "STOCK_PICKS_DAILY")
    if not all([account, user, password, warehouse, database, schema]):
        log_status("SNOWFLAKE", "Secrets not fully configured; skipping.")
        return
    log_status("SNOWFLAKE", f"Connecting to {account}.")
    connection = snowflake.connector.connect(
        account=account, user=user, password=password,
        warehouse=warehouse, database=database, schema=schema,
    )
    create_sql = f"""
    create table if not exists {table} (
      run_ts timestamp_ntz, symbol string, action string, score number,
      last_price float, entry float, stop_loss float, target float, rsi float,
      from_open_pct float, vs_prior_close_pct float, return_5d_pct float,
      return_20d_pct float, daily_volume_ratio float, intraday_volume_ratio float,
      vwap_gap_pct float, reason string
    )"""
    # Migrate existing table if created with old schema
    alter_sqls = [
        f"alter table {table} add column if not exists entry float",
        f"alter table {table} add column if not exists stop_loss float",
        f"alter table {table} add column if not exists target float",
        f"alter table {table} add column if not exists rsi float",
    ]
    insert_sql = f"""
    insert into {table} (
      run_ts, symbol, action, score, last_price, entry, stop_loss, target, rsi,
      from_open_pct, vs_prior_close_pct, return_5d_pct, return_20d_pct,
      daily_volume_ratio, intraday_volume_ratio, vwap_gap_pct, reason
    ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
    rows = [tuple(r) for r in upload_df.itertuples(index=False, name=None)]
    try:
        with connection.cursor() as cur:
            cur.execute(create_sql)
            for alter_sql in alter_sqls:
                cur.execute(alter_sql)
            cur.executemany(insert_sql, rows)
        log_status("SNOWFLAKE", f"Uploaded {len(rows)} rows to {table}.")
    finally:
        connection.close()
        log_status("SNOWFLAKE", "Connection closed.")


def main() -> int:
    log_status("START", "Daily stock picker run started.")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CHART_DIR.mkdir(parents=True, exist_ok=True)

    symbols = load_watchlist()
    log_status("SETUP", f"Loaded {len(symbols)} symbols.")

    # --- Fetch market regime data first ---
    log_status("REGIME", "Fetching SPY and QQQ for market regime.")
    spy_daily = fetch_daily_history("SPY")
    qqq_daily = fetch_daily_history("QQQ")
    regime = compute_market_regime(spy_daily, qqq_daily)
    log_status("REGIME", f"Market regime: {regime.regime.upper()} (score {regime.spy_score}/5, penalty {regime.regime_penalty})")

    # --- Score all symbols ---
    results: list[PickResult] = []
    total = len(symbols)
    for idx, symbol in enumerate(symbols, start=1):
        try:
            log_status("FETCH", f"[{idx}/{total}] {symbol}")
            daily = fetch_daily_history(symbol)
            intraday = fetch_intraday_history(symbol)
            earnings_soon = check_earnings_soon(symbol) if symbol not in MARKET_SYMBOLS else False
            result = score_symbol(symbol, daily, intraday, regime=regime, earnings_soon=earnings_soon)
            if result is not None:
                results.append(result)
                log_status("SCORE", f"[{idx}/{total}] {symbol} → {result.score} ({result.action})")
            else:
                log_status("SCORE", f"[{idx}/{total}] {symbol} skipped (insufficient data)")
        except Exception as exc:
            log_status("ERROR", f"[{idx}/{total}] {symbol} failed: {exc}")

    log_status("RANK", f"Scored {len(results)} symbols.")
    results.sort(key=lambda r: (r.score, r.intraday_change_pct, r.volume_ratio), reverse=True)

    generated_at_dt = datetime.now(timezone.utc)
    generated_at = generated_at_dt.strftime("%Y-%m-%d %H:%M UTC")
    generated_at_iso = generated_at_dt.strftime("%Y-%m-%d %H:%M:%S")
    run_date = generated_at_dt.strftime("%Y-%m-%d")

    # --- Build and save reports ---
    log_status("REPORT", "Building reports.")
    report = build_report(results, generated_at, regime)
    (OUTPUT_DIR / "latest_report.md").write_text(report)

    results_df = build_dataframe(results)
    snowflake_df = prepare_snowflake_dataframe(results_df, generated_at_iso)

    html_report = build_html_report(results, generated_at, regime)
    (OUTPUT_DIR / "latest_report.html").write_text(html_report)
    results_df.to_csv(OUTPUT_DIR / "latest_picks.csv", index=False)
    (OUTPUT_DIR / "latest_picks.json").write_text(results_df.to_json(orient="records", indent=2))

    append_history(results_df, run_date)

    # --- Charts ---
    score_chart_path = CHART_DIR / "scores.png"
    momentum_chart_path = CHART_DIR / "momentum.png"
    rsi_chart_path = CHART_DIR / "rsi.png"
    if not results_df.empty:
        log_status("CHARTS", "Generating charts.")
        create_score_chart(results_df, score_chart_path)
        create_momentum_chart(results_df, momentum_chart_path)
        create_rsi_chart(results_df, rsi_chart_path)
        log_status("CHARTS", "Charts saved.")

    # --- Deliver ---
    log_status("DELIVERY", "Sending Telegram.")
    send_telegram_message(build_telegram_text(results, generated_at, regime))

    log_status("DELIVERY", "Sending email.")
    send_email_report(
        subject=f"Stock Picks · {regime.regime.upper()} Market · {generated_at}",
        text_body=report,
        html_body=html_report,
        attachment_paths={
            "scores_chart": score_chart_path,
            "momentum_chart": momentum_chart_path,
            "rsi_chart": rsi_chart_path,
        },
    )

    log_status("DELIVERY", "Writing to Snowflake.")
    write_results_to_snowflake(snowflake_df)

    log_status("DONE", "Run completed.")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
