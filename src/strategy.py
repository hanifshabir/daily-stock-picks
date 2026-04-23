from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PickResult:
    symbol: str
    score: int
    close: float
    sma20: float
    sma50: float
    return_5d: float
    return_20d: float
    volume_ratio: float
    reason: str


def score_symbol(symbol: str, df) -> PickResult | None:
    if df is None or df.empty or len(df) < 60:
        return None

    work = df.copy().dropna()
    if len(work) < 60:
        return None

    work["sma20"] = work["Close"].rolling(20).mean()
    work["sma50"] = work["Close"].rolling(50).mean()
    work["avg_volume20"] = work["Volume"].rolling(20).mean()
    work["return_5d"] = work["Close"].pct_change(5)
    work["return_20d"] = work["Close"].pct_change(20)

    row = work.iloc[-1]
    close = float(row["Close"])
    sma20 = float(row["sma20"])
    sma50 = float(row["sma50"])
    avg_volume20 = float(row["avg_volume20"])
    return_5d = float(row["return_5d"]) if row["return_5d"] == row["return_5d"] else 0.0
    return_20d = float(row["return_20d"]) if row["return_20d"] == row["return_20d"] else 0.0
    volume = float(row["Volume"])
    volume_ratio = volume / avg_volume20 if avg_volume20 else 0.0

    score = 0
    reasons: list[str] = []

    if close > sma20:
        score += 25
        reasons.append("above 20-day average")

    if close > sma50:
        score += 25
        reasons.append("above 50-day average")

    if return_5d > 0.01:
        score += 15
        reasons.append("positive 5-day momentum")
    elif return_5d < -0.03:
        score -= 10

    if return_20d > 0.03:
        score += 20
        reasons.append("positive 20-day momentum")
    elif return_20d < -0.08:
        score -= 15

    if volume_ratio > 1.1:
        score += 15
        reasons.append("volume above 20-day average")
    elif volume_ratio < 0.8:
        score -= 5

    if close < sma20 < sma50:
        score -= 15

    reason = ", ".join(reasons) if reasons else "no strong signal"

    return PickResult(
        symbol=symbol,
        score=score,
        close=close,
        sma20=sma20,
        sma50=sma50,
        return_5d=return_5d,
        return_20d=return_20d,
        volume_ratio=volume_ratio,
        reason=reason,
    )
