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

from strategy import PickResult, score_symbol


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
CHART_DIR = OUTPUT_DIR / "charts"
WATCHLIST_PATH = ROOT / "watchlist.json"


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
    return history if history is not None else pd.DataFrame()


def build_dataframe(results: list[PickResult]) -> pd.DataFrame:
    rows = [
        {
            "Symbol": pick.symbol,
            "Action": pick.action,
            "Score": pick.score,
            "Last Price": round(pick.last_price, 2),
            "From Open %": round(pick.intraday_change_pct * 100, 2),
            "Vs Prior Close %": round(pick.day_change_pct * 100, 2),
            "5D %": round(pick.return_5d * 100, 2),
            "20D %": round(pick.return_20d * 100, 2),
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
        "from_open_pct",
        "vs_prior_close_pct",
        "return_5d_pct",
        "return_20d_pct",
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
            "from_open_pct",
            "vs_prior_close_pct",
            "return_5d_pct",
            "return_20d_pct",
            "daily_volume_ratio",
            "intraday_volume_ratio",
            "vwap_gap_pct",
            "reason",
        ]
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
        f'<span style="display:inline-block;padding:4px 8px;border-radius:999px;'
        f'background:{bg_color};color:{text_color};font-weight:700;font-size:12px;">{action}</span>'
    )


def _fmt_pct(value: float) -> str:
    return f"{value * 100:+.2f}%"


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
                f"- Score: {pick.score}",
                f"- Last price: ${pick.last_price:.2f}",
                f"- From open: {_fmt_pct(pick.intraday_change_pct)}",
                f"- Vs prior close: {_fmt_pct(pick.day_change_pct)}",
                f"- 5-day momentum: {_fmt_pct(pick.return_5d)}",
                f"- 20-day momentum: {_fmt_pct(pick.return_20d)}",
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
            f"- {pick.symbol}: {pick.action}, score {pick.score}, "
            f"from open {_fmt_pct(pick.intraday_change_pct)}, "
            f"vs prior close {_fmt_pct(pick.day_change_pct)}, "
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
        summary_cards.append(
            f"""
            <div style="flex:1;min-width:180px;background:#0f172a;color:#f8fafc;border-radius:16px;padding:16px;">
              <div style="font-size:13px;opacity:0.8;">Top setup</div>
              <div style="font-size:26px;font-weight:800;margin-top:6px;">{pick.symbol}</div>
              <div style="margin-top:8px;">{action_badge(pick.action)}</div>
              <div style="font-size:30px;font-weight:800;margin-top:10px;">{pick.score}</div>
              <div style="margin-top:8px;font-size:13px;">From open {_fmt_pct(pick.intraday_change_pct)}</div>
              <div style="font-size:13px;">VWAP gap {_fmt_pct(pick.vwap_distance_pct)}</div>
            </div>
            """
        )

    rows = []
    for pick in results:
        row_bg = "#ffffff" if pick.action != "Skip" else "#f8fafc"
        rows.append(
            f"""
            <tr style="background:{row_bg};">
              <td style="padding:12px;border-bottom:1px solid #e5e7eb;font-weight:700;">{pick.symbol}</td>
              <td style="padding:12px;border-bottom:1px solid #e5e7eb;">{action_badge(pick.action)}</td>
              <td style="padding:12px;border-bottom:1px solid #e5e7eb;font-weight:700;">{pick.score}</td>
              <td style="padding:12px;border-bottom:1px solid #e5e7eb;">${pick.last_price:.2f}</td>
              <td style="padding:12px;border-bottom:1px solid #e5e7eb;color:{'#166534' if pick.intraday_change_pct >= 0 else '#b91c1c'};">{_fmt_pct(pick.intraday_change_pct)}</td>
              <td style="padding:12px;border-bottom:1px solid #e5e7eb;color:{'#166534' if pick.day_change_pct >= 0 else '#b91c1c'};">{_fmt_pct(pick.day_change_pct)}</td>
              <td style="padding:12px;border-bottom:1px solid #e5e7eb;">{pick.intraday_volume_ratio:.2f}x</td>
              <td style="padding:12px;border-bottom:1px solid #e5e7eb;">{_fmt_pct(pick.vwap_distance_pct)}</td>
              <td style="padding:12px;border-bottom:1px solid #e5e7eb;max-width:420px;">{pick.reason}</td>
            </tr>
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

          <div style="display:flex;gap:16px;flex-wrap:wrap;margin-top:20px;">
            {''.join(summary_cards)}
          </div>

          <div style="background:#ffffff;border-radius:24px;padding:20px;margin-top:20px;box-shadow:0 10px 30px rgba(15,23,42,0.08);">
            <h2 style="margin:0 0 6px;font-size:22px;">Full ranked table</h2>
            <p style="margin:0 0 18px;color:#475569;">All symbols from the watchlist are included below, not just the top picks.</p>
            <table style="width:100%;border-collapse:collapse;font-size:14px;">
              <thead>
                <tr style="background:#eff6ff;text-align:left;">
                  <th style="padding:12px;border-bottom:1px solid #bfdbfe;">Symbol</th>
                  <th style="padding:12px;border-bottom:1px solid #bfdbfe;">Action</th>
                  <th style="padding:12px;border-bottom:1px solid #bfdbfe;">Score</th>
                  <th style="padding:12px;border-bottom:1px solid #bfdbfe;">Last</th>
                  <th style="padding:12px;border-bottom:1px solid #bfdbfe;">From Open</th>
                  <th style="padding:12px;border-bottom:1px solid #bfdbfe;">Vs Prior Close</th>
                  <th style="padding:12px;border-bottom:1px solid #bfdbfe;">Intraday Vol</th>
                  <th style="padding:12px;border-bottom:1px solid #bfdbfe;">VWAP Gap</th>
                  <th style="padding:12px;border-bottom:1px solid #bfdbfe;">Reason</th>
                </tr>
              </thead>
              <tbody>
                {''.join(rows)}
              </tbody>
            </table>
          </div>

          <div style="background:#ffffff;border-radius:24px;padding:20px;margin-top:20px;box-shadow:0 10px 30px rgba(15,23,42,0.08);">
            <h2 style="margin:0 0 10px;font-size:22px;">Charts</h2>
            <p style="margin:0 0 18px;color:#475569;">Attached charts show score ranking and intraday momentum versus volume for the entire watchlist.</p>
            <div style="display:flex;gap:16px;flex-wrap:wrap;">
              <img src="cid:scores_chart" alt="Score ranking" style="max-width:100%;width:500px;border-radius:16px;border:1px solid #e2e8f0;" />
              <img src="cid:momentum_chart" alt="Momentum versus volume" style="max-width:100%;width:500px;border-radius:16px;border:1px solid #e2e8f0;" />
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
      from_open_pct float,
      vs_prior_close_pct float,
      return_5d_pct float,
      return_20d_pct float,
      daily_volume_ratio float,
      intraday_volume_ratio float,
      vwap_gap_pct float,
      reason string
    )
    """

    insert_sql = f"""
    insert into {table} (
      run_ts,
      symbol,
      action,
      score,
      last_price,
      from_open_pct,
      vs_prior_close_pct,
      return_5d_pct,
      return_20d_pct,
      daily_volume_ratio,
      intraday_volume_ratio,
      vwap_gap_pct,
      reason
    )
    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    rows = [tuple(row) for row in upload_df.itertuples(index=False, name=None)]

    try:
        with connection.cursor() as cursor:
            log_status("SNOWFLAKE", f"Ensuring table {table} exists.")
            cursor.execute(create_table_sql)
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
    results: list[PickResult] = []
    total_symbols = len(symbols)
    for index, symbol in enumerate(symbols, start=1):
        try:
            log_status("FETCH", f"[{index}/{total_symbols}] Loading market data for {symbol}.")
            daily_history = fetch_daily_history(symbol)
            intraday_history = fetch_intraday_history(symbol)
            result = score_symbol(symbol, daily_history, intraday_history)
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

    log_status("DELIVERY", "Sending Telegram notification if configured.")
    telegram_text = build_telegram_text(results, generated_at)
    send_telegram_message(telegram_text)
    log_status("DELIVERY", "Sending email report if configured.")
    send_email_report(
        subject=f"Intraday Stock Picks - {generated_at}",
        text_body=report,
        html_body=html_report,
        attachment_paths={"scores_chart": score_chart_path, "momentum_chart": momentum_chart_path},
    )
    log_status("DELIVERY", "Writing results to Snowflake if configured.")
    write_results_to_snowflake(snowflake_df)

    log_status("DONE", "Run completed successfully.")
    print(report)
    print(f"\nSaved report to {report_path}")
    print(f"Saved HTML report to {html_path}")
    print(f"Saved CSV to {csv_path}")
    print(f"Saved JSON to {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
