from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

from strategy import PickResult, score_symbol


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
WATCHLIST_PATH = ROOT / "watchlist.json"


def load_watchlist() -> list[str]:
    symbols = json.loads(WATCHLIST_PATH.read_text())
    cleaned = []
    for symbol in symbols:
        if isinstance(symbol, str) and symbol.strip():
            cleaned.append(symbol.strip().upper())
    return cleaned


def fetch_history(symbol: str) -> pd.DataFrame:
    ticker = yf.Ticker(symbol)
    history = ticker.history(period="6mo", interval="1d", auto_adjust=False)
    if history is None or history.empty:
        return pd.DataFrame()
    return history


def build_report(picks: list[PickResult], generated_at: str) -> str:
    lines = [
        f"# Daily Stock Picks",
        "",
        f"Generated at: {generated_at}",
        "",
        "These are ranked watchlist candidates, not guaranteed buy signals.",
        "",
    ]

    if not picks:
        lines.append("No picks were generated today.")
        return "\n".join(lines)

    for idx, pick in enumerate(picks, start=1):
        lines.extend(
            [
                f"## {idx}. {pick.symbol}",
                f"- Score: {pick.score}",
                f"- Last close: ${pick.close:.2f}",
                f"- 5-day return: {pick.return_5d * 100:.2f}%",
                f"- 20-day return: {pick.return_20d * 100:.2f}%",
                f"- Volume ratio vs 20-day average: {pick.volume_ratio:.2f}x",
                f"- Reason: {pick.reason}",
                "",
            ]
        )

    return "\n".join(lines)


def send_telegram_message(text: str) -> None:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("Telegram secrets not configured; skipping alert delivery.")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": text[:3900],
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    response.raise_for_status()


def build_telegram_text(picks: list[PickResult], generated_at: str) -> str:
    if not picks:
        return f"Daily Stock Picks\n{generated_at}\n\nNo strong candidates today."

    lines = [f"Daily Stock Picks", generated_at, ""]
    for pick in picks:
        lines.append(
            f"{pick.symbol}: score {pick.score}, close ${pick.close:.2f}, {pick.reason}"
        )
    return "\n".join(lines)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    symbols = load_watchlist()
    results: list[PickResult] = []

    for symbol in symbols:
        try:
            history = fetch_history(symbol)
            result = score_symbol(symbol, history)
            if result is not None:
                results.append(result)
        except Exception as exc:
            print(f"Failed for {symbol}: {exc}")

    results.sort(key=lambda item: item.score, reverse=True)
    top_picks = [pick for pick in results if pick.score >= 40][:5]

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report = build_report(top_picks, generated_at)
    report_path = OUTPUT_DIR / "latest_report.md"
    report_path.write_text(report)

    json_path = OUTPUT_DIR / "latest_picks.json"
    json_path.write_text(
        json.dumps(
            [
                {
                    "symbol": pick.symbol,
                    "score": pick.score,
                    "close": round(pick.close, 2),
                    "return_5d_pct": round(pick.return_5d * 100, 2),
                    "return_20d_pct": round(pick.return_20d * 100, 2),
                    "volume_ratio": round(pick.volume_ratio, 2),
                    "reason": pick.reason,
                }
                for pick in top_picks
            ],
            indent=2,
        )
    )

    telegram_text = build_telegram_text(top_picks, generated_at)
    send_telegram_message(telegram_text)

    print(report)
    print(f"\nSaved report to {report_path}")
    print(f"Saved JSON to {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
