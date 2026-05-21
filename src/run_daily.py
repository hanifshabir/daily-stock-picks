from __future__ import annotations

import json
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt
import pandas as pd
import requests
import snowflake.connector
import yfinance as yf

from strategy import MarketContext, PickResult, SymbolHistory, score_symbol


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
CHART_DIR = OUTPUT_DIR / "charts"
WATCHLIST_PATH = ROOT / "watchlist.json"
MARKET_TZ = ZoneInfo("America/New_York")
COMPANY_NAMES = {
    "AAPL": "Apple",
    "NOW": "ServiceNow",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "AMZN": "Amazon",
    "META": "Meta Platforms",
    "GOOGL": "Alphabet",
    "TSLA": "Tesla",
    "AMD": "Advanced Micro Devices",
    "AVGO": "Broadcom",
    "TSM": "Taiwan Semiconductor",
    "WMT": "Walmart",
    "BRK-B": "Berkshire Hathaway",
    "LLY": "Eli Lilly",
    "JPM": "JPMorgan Chase",
    "MU": "Micron Technology",
    "XOM": "Exxon Mobil",
    "V": "Visa",
    "MA": "Mastercard",
    "COST": "Costco",
    "UNH": "UnitedHealth Group",
    "HD": "Home Depot",
    "BAC": "Bank of America",
    "ABBV": "AbbVie",
    "KO": "Coca-Cola",
    "PM": "Philip Morris",
    "GE": "GE Aerospace",
    "CAT": "Caterpillar",
    "WFC": "Wells Fargo",
    "IBM": "IBM",
    "TMUS": "T-Mobile",
    "MCD": "McDonald's",
    "LIN": "Linde",
    "HON": "Honeywell",
    "GS": "Goldman Sachs",
    "INTU": "Intuit",
    "MS": "Morgan Stanley",
    "AMAT": "Applied Materials",
    "BKNG": "Booking Holdings",
    "PGR": "Progressive",
    "DIS": "Walt Disney",
    "C": "Citigroup",
    "TXN": "Texas Instruments",
    "QCOM": "Qualcomm",
    "NKE": "Nike",
    "RTX": "RTX",
    "SPGI": "S&P Global",
    "ADI": "Analog Devices",
    "PEP": "PepsiCo",
    "TJX": "TJX Companies",
    "MDT": "Medtronic",
    "NFLX": "Netflix",
    "PLTR": "Palantir",
    "CRM": "Salesforce",
    "ADBE": "Adobe",
    "ORCL": "Oracle",
    "UBER": "Uber",
    "SHOP": "Shopify",
}


def log_status(stage: str, message: str) -> None:
    print(f"[{stage}] {message}", flush=True)


def load_watchlist() -> list[str]:
    symbols = json.loads(WATCHLIST_PATH.read_text())
    return [symbol.strip().upper() for symbol in symbols if isinstance(symbol, str) and symbol.strip()]


def fetch_daily_history(symbol: str) -> pd.DataFrame:
    history = yf.Ticker(symbol).history(period="6mo", interval="1d", auto_adjust=False)
    return history if history is not None else pd.DataFrame()


def fetch_intraday_history(symbol: str) -> pd.DataFrame:
    history = yf.Ticker(symbol).history(period="5d", interval="5m", auto_adjust=False, prepost=True)
    if history is None or history.empty:
        return pd.DataFrame()
    return select_latest_regular_session(history)


def select_latest_regular_session(history: pd.DataFrame) -> pd.DataFrame:
    intraday = history.copy()
    if intraday.empty:
        return intraday

    if not isinstance(intraday.index, pd.DatetimeIndex):
        return intraday

    if intraday.index.tz is None:
        session_index = intraday.index
    else:
        session_index = intraday.index.tz_convert(MARKET_TZ)

    regular_mask = (
        (session_index.time >= datetime.strptime("09:30", "%H:%M").time())
        & (session_index.time <= datetime.strptime("16:00", "%H:%M").time())
    )
    regular_hours = intraday.loc[regular_mask].copy()
    regular_index = session_index[regular_mask]

    if not regular_hours.empty:
        regular_hours["session_date"] = regular_index.date
        regular_with_volume = regular_hours.loc[regular_hours["Volume"].fillna(0) > 0].copy()
        source = regular_with_volume if not regular_with_volume.empty else regular_hours
        latest_date = source["session_date"].max()
        selected = source.loc[source["session_date"] == latest_date].drop(columns=["session_date"])
        if not selected.empty:
            return selected

    latest_date = session_index.date.max()
    latest_session = intraday.loc[session_index.date == latest_date].copy()
    return latest_session.loc[latest_session["Volume"].fillna(0) > 0].copy()


def build_dataframe(results: list[PickResult]) -> pd.DataFrame:
    rows = [
        {
            "Symbol": pick.symbol,
            "Action": pick.action,
            "Score": pick.score,
            "Last Price": round(pick.last_price, 2),
            "Entry": round(pick.entry_price, 2),
            "Stop Loss": round(pick.stop_loss, 2),
            "Daily Target": round(pick.daily_target_price, 2),
            "1W Target": round(pick.target_price, 2),
            "From Open %": round(pick.intraday_change_pct * 100, 2),
            "Vs Prior Close %": round(pick.day_change_pct * 100, 2),
            "5D %": round(pick.return_5d * 100, 2),
            "20D %": round(pick.return_20d * 100, 2),
            "RSI 14": round(pick.daily_rsi14, 1),
            "Rel 5D %": round(pick.relative_strength_5d * 100, 2),
            "Rel 20D %": round(pick.relative_strength_20d * 100, 2),
            "Daily Vol x": round(pick.volume_ratio, 2),
            "Intraday Vol x": round(pick.intraday_volume_ratio, 2),
            "VWAP Gap %": round(pick.vwap_distance_pct * 100, 2),
            "Reason": pick.reason,
        }
        for pick in results
    ]
    return pd.DataFrame(rows)


def prepare_snowflake_dataframe(results_df: pd.DataFrame, generated_at_iso: str) -> pd.DataFrame:
    if results_df.empty:
        return pd.DataFrame()

    upload_df = results_df.copy()
    upload_df["run_ts"] = generated_at_iso
    upload_df.columns = [
        "symbol",
        "action",
        "score",
        "last_price",
        "entry",
        "stop_loss",
        "daily_target",
        "target",
        "from_open_pct",
        "vs_prior_close_pct",
        "return_5d_pct",
        "return_20d_pct",
        "daily_rsi14",
        "relative_strength_5d",
        "relative_strength_20d",
        "daily_volume_ratio",
        "intraday_volume_ratio",
        "vwap_gap_pct",
        "reason",
        "run_ts",
    ]
    upload_df = upload_df[
        [
            "run_ts",
            "symbol",
            "action",
            "score",
            "last_price",
            "entry",
            "stop_loss",
            "daily_target",
            "target",
            "from_open_pct",
            "vs_prior_close_pct",
            "return_5d_pct",
            "return_20d_pct",
            "daily_rsi14",
            "relative_strength_5d",
            "relative_strength_20d",
            "daily_volume_ratio",
            "intraday_volume_ratio",
            "vwap_gap_pct",
            "reason",
        ]
    ]
    return upload_df


def build_market_context() -> MarketContext:
    daily_history = fetch_daily_history("SPY")
    intraday_history = fetch_intraday_history("SPY")
    spy_result = score_symbol("SPY", daily_history, intraday_history)
    if spy_result is None:
        log_status("MARKET", "Unable to build SPY market context; using neutral regime.")
        return MarketContext()

    trend_positive = (
        spy_result.last_price > spy_result.sma20
        and spy_result.sma20 >= spy_result.sma50
        and spy_result.return_5d > 0
    )
    intraday_positive = spy_result.intraday_change_pct > 0 and spy_result.day_change_pct > 0
    context = MarketContext(
        trend_positive=trend_positive,
        intraday_positive=intraday_positive,
        spy_return_5d=spy_result.return_5d,
        spy_return_20d=spy_result.return_20d,
        spy_intraday_change_pct=spy_result.intraday_change_pct,
        spy_day_change_pct=spy_result.day_change_pct,
        spy_rsi14=spy_result.daily_rsi14,
    )
    regime = "supportive" if trend_positive else "weak"
    tape = "positive" if intraday_positive else "mixed"
    log_status(
        "MARKET",
        f"SPY regime is {regime}; intraday tape is {tape}; SPY RSI {spy_result.daily_rsi14:.1f}.",
    )
    return context


def load_symbol_history(limit_runs: int = 3) -> dict[str, SymbolHistory]:
    account = os.getenv("SNOWFLAKE_ACCOUNT")
    user = os.getenv("SNOWFLAKE_USER")
    password = os.getenv("SNOWFLAKE_PASSWORD")
    warehouse = os.getenv("SNOWFLAKE_WAREHOUSE")
    database = os.getenv("SNOWFLAKE_DATABASE")
    schema = os.getenv("SNOWFLAKE_SCHEMA")
    table = os.getenv("SNOWFLAKE_TABLE", "STOCK_PICKS_DAILY")

    if not all([account, user, password, warehouse, database, schema]):
        log_status("HISTORY", "Snowflake secrets not fully configured; skipping score history lookup.")
        return {}

    try:
        connection = snowflake.connector.connect(
            account=account,
            user=user,
            password=password,
            warehouse=warehouse,
            database=database,
            schema=schema,
        )
        try:
            query = f"select run_ts, symbol, action, score from {table} order by run_ts desc"
            history_df = pd.read_sql(query, connection)
        finally:
            connection.close()
    except Exception as exc:
        log_status("HISTORY", f"Unable to load prior Snowflake rows: {exc}")
        return {}

    if history_df.empty:
        log_status("HISTORY", "No prior Snowflake rows found; using standalone scoring.")
        return {}

    history_df["RUN_TS"] = pd.to_datetime(history_df["RUN_TS"])
    history_df["run_date"] = history_df["RUN_TS"].dt.date
    latest_daily = (
        history_df.sort_values("RUN_TS")
        .groupby(["run_date", "SYMBOL"], as_index=False)
        .tail(1)
        .sort_values(["SYMBOL", "RUN_TS"], ascending=[True, False])
    )

    history_by_symbol: dict[str, SymbolHistory] = {}
    for symbol, symbol_df in latest_daily.groupby("SYMBOL"):
        recent = symbol_df.head(limit_runs).copy()
        if recent.empty:
            continue

        buy_rate = float((recent["ACTION"] == "Buy Watch").mean())
        watch_rate = float(recent["ACTION"].isin(["Buy Watch", "Watch"]).mean())
        score_std = recent["SCORE"].std()
        history_by_symbol[symbol] = SymbolHistory(
            prev_avg_score_3=float(recent["SCORE"].mean()),
            prev_buy_rate_3=buy_rate,
            prev_watch_rate_3=watch_rate,
            prev_score_std_3=0.0 if pd.isna(score_std) else float(score_std),
            sample_size=len(recent),
        )

    log_status("HISTORY", f"Loaded recent scoring history for {len(history_by_symbol)} symbols.")
    return history_by_symbol


def backfill_outcome_columns(connection, table: str) -> None:
    select_sql = f"""
    select run_ts, symbol, last_price
    from {table}
    where close_return_same_day is null
       or next_day_return is null
       or forward_5d_return is null
    order by run_ts desc
    """
    pending_df = pd.read_sql(select_sql, connection)
    if pending_df.empty:
        log_status("OUTCOMES", "No pending outcome rows to update.")
        return

    pending_df["RUN_TS"] = pd.to_datetime(pending_df["RUN_TS"])
    symbols = sorted(set(pending_df["SYMBOL"]))
    history_cache: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        history_cache[symbol] = fetch_daily_history(symbol)

    updates: list[tuple[float | None, float | None, float | None, str, str]] = []
    for row in pending_df.itertuples(index=False):
        history = history_cache.get(row.SYMBOL)
        if history is None or history.empty:
            continue

        trade_dates = list(pd.to_datetime(history.index).date)
        closes = history["Close"].tolist()
        signal_date = row.RUN_TS.date()
        if signal_date not in trade_dates:
            continue

        idx = trade_dates.index(signal_date)
        last_price = float(row.LAST_PRICE)
        same_day_close = float(closes[idx]) if idx < len(closes) else None
        next_day_close = float(closes[idx + 1]) if idx + 1 < len(closes) else None
        forward_5d_close = float(closes[idx + 5]) if idx + 5 < len(closes) else None

        same_day_return = ((same_day_close - last_price) / last_price) if same_day_close and last_price else None
        next_day_return = ((next_day_close - last_price) / last_price) if next_day_close and last_price else None
        forward_5d_return = ((forward_5d_close - last_price) / last_price) if forward_5d_close and last_price else None
        updates.append(
            (
                same_day_return,
                next_day_return,
                forward_5d_return,
                str(row.RUN_TS.strftime("%Y-%m-%d %H:%M:%S")),
                row.SYMBOL,
            )
        )

    if not updates:
        log_status("OUTCOMES", "No outcome values were ready to backfill.")
        return

    update_sql = f"""
    update {table}
    set close_return_same_day = %s,
        next_day_return = %s,
        forward_5d_return = %s
    where run_ts = %s
      and symbol = %s
    """
    with connection.cursor() as cursor:
        cursor.executemany(update_sql, updates)
    log_status("OUTCOMES", f"Backfilled outcomes for {len(updates)} rows.")


def should_backfill_outcomes() -> bool:
    value = os.getenv("SNOWFLAKE_BACKFILL_OUTCOMES", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def run_optional_step(stage: str, description: str, callback, *args, **kwargs) -> None:
    log_status(stage, description)
    try:
        callback(*args, **kwargs)
    except Exception as exc:
        log_status(stage, f"{description} failed: {exc}")


def action_badge(action: str) -> str:
    colors = {
        "Buy Watch": ("#14532d", "#dcfce7"),
        "Watch": ("#92400e", "#fef3c7"),
        "Skip": ("#991b1b", "#fee2e2"),
    }
    text_color, bg_color = colors.get(action, ("#1f2937", "#e5e7eb"))
    return (
        f'<span style="display:inline-block;padding:4px 8px;border-radius:999px;'
        f'background:{bg_color};color:{text_color};font-weight:700;font-size:12px;">{action}</span>'
    )


def _fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


def _fmt_money(value: float) -> str:
    return f"${value:.2f}"


def display_name(symbol: str) -> str:
    return COMPANY_NAMES.get(symbol, symbol)


def build_report(results: list[PickResult], generated_at: str) -> str:
    lines = [
        "# Intraday Stock Picks",
        "",
        f"Generated at: {generated_at}",
        "",
        "This report ranks the watchlist using daily trend plus intraday momentum, VWAP, and volume.",
        "",
    ]

    if not results:
        lines.append("No picks were generated today.")
        return "\n".join(lines)

    leaders = results[:10]
    for idx, pick in enumerate(leaders, start=1):
        lines.extend(
            [
                f"## {idx}. {pick.symbol} ({pick.action})",
                f"- Company: {display_name(pick.symbol)}",
                f"- Score: {pick.score}",
                f"- Last price: ${pick.last_price:.2f}",
                f"- Suggested entry: {_fmt_money(pick.entry_price)}",
                f"- Suggested stop loss: {_fmt_money(pick.stop_loss)}",
                f"- Daily target: {_fmt_money(pick.daily_target_price)}",
                f"- 1-week target: {_fmt_money(pick.target_price)}",
                f"- From open: {_fmt_pct(pick.intraday_change_pct)}",
                f"- Vs prior close: {_fmt_pct(pick.day_change_pct)}",
                f"- 5-day momentum: {_fmt_pct(pick.return_5d)}",
                f"- 20-day momentum: {_fmt_pct(pick.return_20d)}",
                f"- Daily RSI(14): {pick.daily_rsi14:.1f}",
                f"- Relative strength vs SPY (5D): {_fmt_pct(pick.relative_strength_5d)}",
                f"- Relative strength vs SPY (20D): {_fmt_pct(pick.relative_strength_20d)}",
                f"- Daily volume ratio: {pick.volume_ratio:.2f}x",
                f"- Intraday volume ratio: {pick.intraday_volume_ratio:.2f}x",
                f"- VWAP gap: {_fmt_pct(pick.vwap_distance_pct)}",
                f"- Reason: {pick.reason}",
                "",
            ]
        )

    lines.append("## Full Ranked Table")
    lines.append("")
    for pick in results:
        lines.append(
            f"- {pick.symbol} ({display_name(pick.symbol)}): {pick.action}, score {pick.score}, "
            f"entry {_fmt_money(pick.entry_price)}, "
            f"daily target {_fmt_money(pick.daily_target_price)}, "
            f"target {_fmt_money(pick.target_price)}, "
            f"from open {_fmt_pct(pick.intraday_change_pct)}, "
            f"intraday volume {pick.intraday_volume_ratio:.2f}x"
        )

    return "\n".join(lines)


def build_html_report(results: list[PickResult], generated_at: str) -> str:
    if not results:
        return (
            "<html><body><h1>Intraday Stock Picks</h1>"
            f"<p>Generated at: {generated_at}</p><p>No picks were generated today.</p></body></html>"
        )

    summary_cards = []
    for pick in results[:3]:
        daily_reward_pct = ((pick.daily_target_price - pick.entry_price) / pick.entry_price) if pick.entry_price else 0.0
        reward_pct = ((pick.target_price - pick.entry_price) / pick.entry_price) if pick.entry_price else 0.0
        risk_pct = ((pick.entry_price - pick.stop_loss) / pick.entry_price) if pick.entry_price else 0.0
        breakdown_items = "".join(
            f'<li style="margin:0 0 4px 18px;">{item}</li>' for item in pick.score_breakdown[:8]
        )
        summary_cards.append(
            f"""
            <div style="flex:1;min-width:250px;background:linear-gradient(180deg,#0f172a,#172554);color:#f8fafc;border-radius:22px;padding:18px;box-shadow:0 16px 40px rgba(15,23,42,0.18);">
              <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;">
                <div>
                  <div style="font-size:12px;letter-spacing:0.08em;text-transform:uppercase;opacity:0.75;">Weekly setup</div>
                  <div style="font-size:28px;font-weight:800;margin-top:4px;">{pick.symbol}</div>
                  <div style="font-size:14px;opacity:0.82;margin-top:4px;">{display_name(pick.symbol)}</div>
                </div>
                <div style="font-size:34px;font-weight:900;">{pick.score}</div>
              </div>
              <div style="margin-top:10px;">{action_badge(pick.action)}</div>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:14px;">
                <div style="background:rgba(255,255,255,0.08);border-radius:14px;padding:12px;">
                  <div style="font-size:11px;opacity:0.7;text-transform:uppercase;">Entry</div>
                  <div style="font-size:22px;font-weight:800;margin-top:4px;">{_fmt_money(pick.entry_price)}</div>
                </div>
                <div style="background:rgba(255,255,255,0.08);border-radius:14px;padding:12px;">
                  <div style="font-size:11px;opacity:0.7;text-transform:uppercase;">Daily Target</div>
                  <div style="font-size:22px;font-weight:800;margin-top:4px;">{_fmt_money(pick.daily_target_price)}</div>
                </div>
                <div style="background:rgba(255,255,255,0.08);border-radius:14px;padding:12px;">
                  <div style="font-size:11px;opacity:0.7;text-transform:uppercase;">1W Target</div>
                  <div style="font-size:22px;font-weight:800;margin-top:4px;">{_fmt_money(pick.target_price)}</div>
                </div>
                <div style="background:rgba(255,255,255,0.08);border-radius:14px;padding:12px;">
                  <div style="font-size:11px;opacity:0.7;text-transform:uppercase;">Stop</div>
                  <div style="font-size:22px;font-weight:800;margin-top:4px;">{_fmt_money(pick.stop_loss)}</div>
                </div>
                <div style="background:rgba(255,255,255,0.08);border-radius:14px;padding:12px;">
                  <div style="font-size:11px;opacity:0.7;text-transform:uppercase;">Daily / Weekly / Risk</div>
                  <div style="font-size:18px;font-weight:800;margin-top:6px;">{daily_reward_pct * 100:.1f}% / {reward_pct * 100:.1f}% / {risk_pct * 100:.1f}%</div>
                </div>
              </div>
              <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:14px;font-size:13px;">
                <div>From open {_fmt_pct(pick.intraday_change_pct)}</div>
                <div>VWAP {_fmt_pct(pick.vwap_distance_pct)}</div>
                <div>Vol {pick.intraday_volume_ratio:.2f}x</div>
                <div>RSI {pick.daily_rsi14:.1f}</div>
              </div>
              <div style="margin-top:14px;padding-top:14px;border-top:1px solid rgba(255,255,255,0.12);">
                <div style="font-size:12px;letter-spacing:0.08em;text-transform:uppercase;opacity:0.75;">Why It Scored Here</div>
                <ul style="margin:8px 0 0;padding:0;font-size:13px;line-height:1.45;color:#dbeafe;">
                  {breakdown_items}
                </ul>
              </div>
            </div>
            """
        )

    scoreboard_cards = []
    for pick in results[:10]:
        scoreboard_cards.append(
            f"""
            <div style="display:grid;grid-template-columns:72px 1fr auto;gap:12px;align-items:center;padding:14px 0;border-bottom:1px solid #e2e8f0;">
              <div style="font-size:30px;font-weight:900;color:#0f172a;">{pick.score}</div>
              <div>
                <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
                  <div style="font-size:18px;font-weight:800;color:#0f172a;">{pick.symbol}</div>
                  <div style="font-size:13px;color:#64748b;">{display_name(pick.symbol)}</div>
                  <div>{action_badge(pick.action)}</div>
                </div>
                <div style="margin-top:6px;font-size:13px;color:#475569;">Entry {_fmt_money(pick.entry_price)} · Day {_fmt_money(pick.daily_target_price)} · 1W {_fmt_money(pick.target_price)} · Stop {_fmt_money(pick.stop_loss)}</div>
              </div>
              <div style="text-align:right;font-size:13px;color:#475569;">
                <div style="color:{'#166534' if pick.intraday_change_pct >= 0 else '#b91c1c'};">Open {_fmt_pct(pick.intraday_change_pct)}</div>
                <div>Vol {pick.intraday_volume_ratio:.2f}x</div>
                <div>RSI {pick.daily_rsi14:.1f}</div>
              </div>
            </div>
            """
        )

    return f"""
    <html>
      <body style="margin:0;background:#f1f5f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#0f172a;">
        <div style="max-width:1100px;margin:0 auto;padding:32px 20px;">
          <div style="background:linear-gradient(135deg,#0f172a,#1d4ed8);border-radius:24px;padding:28px;color:#f8fafc;">
            <div style="font-size:14px;opacity:0.85;">Intraday watchlist dashboard</div>
            <h1 style="margin:8px 0 6px;font-size:34px;line-height:1.1;">Daily Stock Picks</h1>
            <p style="margin:0;font-size:15px;opacity:0.9;">Generated at {generated_at}. Ranked with daily trend, intraday move, VWAP, and volume.</p>
          </div>

          <div style="background:#ffffff;border-radius:24px;padding:24px;margin-top:20px;box-shadow:0 10px 30px rgba(15,23,42,0.08);">
            <h2 style="margin:0 0 6px;font-size:24px;">1-Week Trade Plans</h2>
            <p style="margin:0;color:#475569;">The top setups below show suggested entry, stop, and 1-week target levels for faster decision-making.</p>
            <div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:18px;">
              {''.join(summary_cards)}
            </div>
          </div>

          <div style="background:#ffffff;border-radius:24px;padding:24px;margin-top:20px;box-shadow:0 10px 30px rgba(15,23,42,0.08);">
            <h2 style="margin:0 0 8px;font-size:24px;">Graph Scoreboard</h2>
            <p style="margin:0 0 18px;color:#475569;">The chart-first scoreboard highlights rank, momentum, and volume without relying on a dense table.</p>
            <div style="display:flex;gap:16px;flex-wrap:wrap;">
              <img src="cid:scores_chart" alt="Score ranking" style="max-width:100%;width:500px;border-radius:16px;border:1px solid #e2e8f0;" />
              <img src="cid:momentum_chart" alt="Momentum versus volume" style="max-width:100%;width:500px;border-radius:16px;border:1px solid #e2e8f0;" />
            </div>
            <div style="margin-top:18px;">
              {''.join(scoreboard_cards)}
            </div>
          </div>
        </div>
      </body>
    </html>
    """


def build_telegram_text(results: list[PickResult], generated_at: str) -> str:
    if not results:
        return f"Daily Stock Picks\n{generated_at}\n\nNo strong candidates today."

    lines = [f"Daily Stock Picks", generated_at, ""]
    for pick in results[:10]:
        lines.append(
            f"{pick.symbol}: {pick.action}, score {pick.score}, "
            f"from open {_fmt_pct(pick.intraday_change_pct)}, "
            f"volume {pick.intraday_volume_ratio:.2f}x"
        )
    return "\n".join(lines)


def create_score_chart(df: pd.DataFrame, path: Path) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    chart_df = df.head(12).iloc[::-1]
    fig, ax = plt.subplots(figsize=(10, 6))
    colors = ["#1d4ed8" if action != "Skip" else "#94a3b8" for action in chart_df["Action"]]
    ax.barh(chart_df["Symbol"], chart_df["Score"], color=colors)
    ax.set_title("Top Watchlist Scores", fontsize=16, fontweight="bold")
    ax.set_xlabel("Score")
    ax.set_ylabel("")
    for index, score in enumerate(chart_df["Score"]):
        ax.text(score + 1, index, str(score), va="center", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def create_momentum_chart(df: pd.DataFrame, path: Path) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(10, 6))
    action_colors = {"Buy Watch": "#16a34a", "Watch": "#f59e0b", "Skip": "#dc2626"}
    for action, group in df.groupby("Action"):
        ax.scatter(
            group["From Open %"],
            group["Intraday Vol x"],
            s=group["Score"].clip(lower=10) * 4,
            alpha=0.8,
            label=action,
            color=action_colors.get(action, "#64748b"),
        )
        for _, row in group.iterrows():
            ax.text(row["From Open %"] + 0.03, row["Intraday Vol x"] + 0.01, row["Symbol"], fontsize=8)
    ax.axvline(0, color="#94a3b8", linewidth=1)
    ax.set_title("Intraday Momentum vs Volume", fontsize=16, fontweight="bold")
    ax.set_xlabel("From Open %")
    ax.set_ylabel("Intraday Volume Ratio")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def send_telegram_message(text: str) -> None:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("Telegram secrets not configured; skipping alert delivery.")
        return

    response = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": chat_id, "text": text[:3900], "disable_web_page_preview": True},
        timeout=30,
    )
    response.raise_for_status()


def send_email_report(subject: str, text_body: str, html_body: str, attachment_paths: dict[str, Path]) -> None:
    email_from = os.getenv("EMAIL_FROM")
    email_to = os.getenv("EMAIL_TO")
    email_app_password = os.getenv("EMAIL_APP_PASSWORD")

    if not email_from or not email_to or not email_app_password:
        print("Email secrets not configured; skipping email delivery.")
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
        with path.open("rb") as file_handle:
            html_part.add_related(
                file_handle.read(),
                maintype="image",
                subtype="png",
                cid=f"<{cid}>",
                filename=path.name,
            )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(email_from, email_app_password)
        smtp.send_message(msg)


def write_results_to_snowflake(upload_df: pd.DataFrame) -> None:
    if upload_df.empty:
        log_status("SNOWFLAKE", "No rows to upload; skipping database write.")
        return

    account = os.getenv("SNOWFLAKE_ACCOUNT")
    user = os.getenv("SNOWFLAKE_USER")
    password = os.getenv("SNOWFLAKE_PASSWORD")
    warehouse = os.getenv("SNOWFLAKE_WAREHOUSE")
    database = os.getenv("SNOWFLAKE_DATABASE")
    schema = os.getenv("SNOWFLAKE_SCHEMA")
    table = os.getenv("SNOWFLAKE_TABLE", "STOCK_PICKS_DAILY")

    if not all([account, user, password, warehouse, database, schema]):
        log_status("SNOWFLAKE", "Secrets not fully configured; skipping database write.")
        return

    log_status("SNOWFLAKE", f"Connecting to Snowflake account {account}.")
    connection = snowflake.connector.connect(
        account=account,
        user=user,
        password=password,
        warehouse=warehouse,
        database=database,
        schema=schema,
    )

    create_table_sql = f"""
    create table if not exists {table} (
      run_ts timestamp_ntz,
      symbol string,
      action string,
      score number,
      last_price float,
      entry float,
      stop_loss float,
      daily_target float,
      target float,
      from_open_pct float,
      vs_prior_close_pct float,
      return_5d_pct float,
      return_20d_pct float,
      daily_rsi14 float,
      relative_strength_5d float,
      relative_strength_20d float,
      daily_volume_ratio float,
      intraday_volume_ratio float,
      vwap_gap_pct float,
      reason string,
      close_return_same_day float,
      next_day_return float,
      forward_5d_return float
    )
    """

    insert_sql = f"""
    insert into {table} (
      run_ts,
      symbol,
      action,
      score,
      last_price,
      entry,
      stop_loss,
      daily_target,
      target,
      from_open_pct,
      vs_prior_close_pct,
      return_5d_pct,
      return_20d_pct,
      daily_rsi14,
      relative_strength_5d,
      relative_strength_20d,
      daily_volume_ratio,
      intraday_volume_ratio,
      vwap_gap_pct,
      reason
    )
    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    rows = [tuple(row) for row in upload_df.itertuples(index=False, name=None)]

    try:
        with connection.cursor() as cursor:
            log_status("SNOWFLAKE", f"Ensuring table {table} exists.")
            cursor.execute(create_table_sql)
            cursor.execute(f"alter table {table} add column if not exists daily_target float")
            cursor.execute(f"alter table {table} add column if not exists daily_rsi14 float")
            cursor.execute(f"alter table {table} add column if not exists relative_strength_5d float")
            cursor.execute(f"alter table {table} add column if not exists relative_strength_20d float")
            cursor.execute(f"alter table {table} add column if not exists close_return_same_day float")
            cursor.execute(f"alter table {table} add column if not exists next_day_return float")
            cursor.execute(f"alter table {table} add column if not exists forward_5d_return float")
            if should_backfill_outcomes():
                log_status("OUTCOMES", "Outcome backfill enabled for this run.")
                backfill_outcome_columns(connection, table)
            else:
                log_status("OUTCOMES", "Outcome backfill disabled; inserting current run only.")
            log_status("SNOWFLAKE", f"Inserting {len(rows)} rows into {table}.")
            cursor.executemany(insert_sql, rows)
        log_status("SNOWFLAKE", f"Uploaded {len(rows)} rows to table {table}.")
    finally:
        connection.close()
        log_status("SNOWFLAKE", "Connection closed.")


def main() -> int:
    log_status("START", "Daily stock picker run started.")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CHART_DIR.mkdir(parents=True, exist_ok=True)
    log_status("SETUP", f"Output directory ready at {OUTPUT_DIR}.")

    symbols = load_watchlist()
    log_status("SETUP", f"Loaded {len(symbols)} symbols from watchlist.")
    symbol_history = load_symbol_history()
    market_context = build_market_context()
    results: list[PickResult] = []
    total_symbols = len(symbols)
    for index, symbol in enumerate(symbols, start=1):
        try:
            log_status("FETCH", f"[{index}/{total_symbols}] Loading market data for {symbol}.")
            daily_history = fetch_daily_history(symbol)
            intraday_history = fetch_intraday_history(symbol)
            result = score_symbol(
                symbol,
                daily_history,
                intraday_history,
                history=symbol_history.get(symbol),
                market_context=market_context,
            )
            if result is not None:
                results.append(result)
                log_status(
                    "SCORE",
                    f"[{index}/{total_symbols}] {symbol} scored {result.score} as {result.action}.",
                )
            else:
                log_status("SCORE", f"[{index}/{total_symbols}] {symbol} skipped due to insufficient data.")
        except Exception as exc:
            log_status("ERROR", f"[{index}/{total_symbols}] Failed for {symbol}: {exc}")

    log_status("RANK", f"Scored {len(results)} symbols successfully.")
    results.sort(key=lambda item: (item.score, item.intraday_change_pct, item.volume_ratio), reverse=True)
    generated_at_dt = datetime.now(timezone.utc)
    generated_at = generated_at_dt.strftime("%Y-%m-%d %H:%M UTC")
    generated_at_iso = generated_at_dt.strftime("%Y-%m-%d %H:%M:%S")

    log_status("REPORT", "Building markdown report.")
    report = build_report(results, generated_at)
    report_path = OUTPUT_DIR / "latest_report.md"
    report_path.write_text(report)
    log_status("REPORT", f"Saved markdown report to {report_path}.")

    log_status("REPORT", "Building tabular outputs.")
    results_df = build_dataframe(results)
    snowflake_df = prepare_snowflake_dataframe(results_df, generated_at_iso)
    json_path = OUTPUT_DIR / "latest_picks.json"
    json_path.write_text(results_df.to_json(orient="records", indent=2))

    html_report = build_html_report(results, generated_at)
    html_path = OUTPUT_DIR / "latest_report.html"
    html_path.write_text(html_report)

    csv_path = OUTPUT_DIR / "latest_picks.csv"
    results_df.to_csv(csv_path, index=False)
    log_status("REPORT", f"Saved HTML, CSV, and JSON outputs to {OUTPUT_DIR}.")

    score_chart_path = CHART_DIR / "scores.png"
    momentum_chart_path = CHART_DIR / "momentum.png"
    if not results_df.empty:
        log_status("CHARTS", "Generating score chart.")
        create_score_chart(results_df, score_chart_path)
        log_status("CHARTS", "Generating momentum chart.")
        create_momentum_chart(results_df, momentum_chart_path)
        log_status("CHARTS", f"Saved charts to {CHART_DIR}.")
    else:
        log_status("CHARTS", "No results available; skipping chart generation.")

    telegram_text = build_telegram_text(results, generated_at)
    run_optional_step("DELIVERY", "Sending Telegram notification if configured.", send_telegram_message, telegram_text)
    run_optional_step(
        "DELIVERY",
        "Sending email report if configured.",
        send_email_report,
        subject=f"Intraday Stock Picks - {generated_at}",
        text_body=report,
        html_body=html_report,
        attachment_paths={"scores_chart": score_chart_path, "momentum_chart": momentum_chart_path},
    )
    run_optional_step(
        "DELIVERY",
        "Writing results to Snowflake if configured.",
        write_results_to_snowflake,
        snowflake_df,
    )

    log_status("DONE", "Run completed successfully.")
    print(report)
    print(f"\nSaved report to {report_path}")
    print(f"Saved HTML report to {html_path}")
    print(f"Saved CSV to {csv_path}")
    print(f"Saved JSON to {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
