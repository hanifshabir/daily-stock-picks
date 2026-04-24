from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PickResult:
    symbol: str
    score: int
    action: str
    last_price: float
    open_price: float
    day_change_pct: float
    intraday_change_pct: float
    sma20: float
    sma50: float
    return_5d: float
    return_20d: float
    volume_ratio: float
    intraday_volume_ratio: float
    vwap_distance_pct: float
    rsi14: float
    high_52w: float
    low_52w: float
    pct_from_52w_high: float
    suggested_entry: float
    stop_loss: float
    target: float
    earnings_soon: bool
    reason: str


@dataclass
class MarketRegime:
    spy_score: int
    spy_action: str
    spy_last: float
    spy_sma20: float
    spy_sma50: float
    spy_return_5d: float
    qqq_score: int
    qqq_last: float
    qqq_sma20: float
    qqq_return_5d: float
    regime: str  # "bullish" | "neutral" | "weak"
    regime_penalty: int


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value != value:  # NaN check
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def classify_action(score: int) -> str:
    if score >= 75:
        return "Buy Watch"
    if score >= 55:
        return "Watch"
    return "Skip"


def compute_rsi(closes, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = closes.diff().dropna()
    gains = deltas.where(deltas > 0, 0.0)
    losses = -deltas.where(deltas < 0, 0.0)
    avg_gain = gains.iloc[:period].mean()
    avg_loss = losses.iloc[:period].mean()
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains.iloc[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses.iloc[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def compute_market_regime(spy_daily, qqq_daily) -> MarketRegime:
    """Score SPY and QQQ to determine overall market health."""
    default = MarketRegime(
        spy_score=0, spy_action="unknown", spy_last=0.0, spy_sma20=0.0,
        spy_sma50=0.0, spy_return_5d=0.0, qqq_score=0, qqq_last=0.0,
        qqq_sma20=0.0, qqq_return_5d=0.0, regime="neutral", regime_penalty=0,
    )
    if spy_daily is None or spy_daily.empty or len(spy_daily) < 60:
        return default
    if qqq_daily is None or qqq_daily.empty or len(qqq_daily) < 60:
        return default

    for df in [spy_daily, qqq_daily]:
        df["sma20"] = df["Close"].rolling(20).mean()
        df["sma50"] = df["Close"].rolling(50).mean()
        df["return_5d"] = df["Close"].pct_change(5)

    spy_row = spy_daily.iloc[-1]
    qqq_row = qqq_daily.iloc[-1]

    spy_last = _safe_float(spy_row["Close"])
    spy_sma20 = _safe_float(spy_row["sma20"])
    spy_sma50 = _safe_float(spy_row["sma50"])
    spy_r5d = _safe_float(spy_row["return_5d"])

    qqq_last = _safe_float(qqq_row["Close"])
    qqq_sma20 = _safe_float(qqq_row["sma20"])
    qqq_r5d = _safe_float(qqq_row["return_5d"])

    spy_score = 0
    if spy_last > spy_sma20:
        spy_score += 1
    if spy_last > spy_sma50:
        spy_score += 1
    if spy_r5d > 0.005:
        spy_score += 1
    if qqq_last > qqq_sma20:
        spy_score += 1
    if qqq_r5d > 0.005:
        spy_score += 1

    if spy_score >= 4:
        regime = "bullish"
        regime_penalty = 0
    elif spy_score >= 2:
        regime = "neutral"
        regime_penalty = -10
    else:
        regime = "weak"
        regime_penalty = -25

    return MarketRegime(
        spy_score=spy_score,
        spy_action=classify_action(spy_score * 20),
        spy_last=spy_last,
        spy_sma20=spy_sma20,
        spy_sma50=spy_sma50,
        spy_return_5d=spy_r5d,
        qqq_score=spy_score,
        qqq_last=qqq_last,
        qqq_sma20=qqq_sma20,
        qqq_return_5d=qqq_r5d,
        regime=regime,
        regime_penalty=regime_penalty,
    )


def score_symbol(
    symbol: str,
    daily_df,
    intraday_df,
    regime: MarketRegime | None = None,
    earnings_soon: bool = False,
) -> PickResult | None:
    if daily_df is None or daily_df.empty or len(daily_df) < 60:
        return None

    daily = daily_df.copy().dropna()
    if len(daily) < 60:
        return None

    daily["sma20"] = daily["Close"].rolling(20).mean()
    daily["sma50"] = daily["Close"].rolling(50).mean()
    daily["avg_volume20"] = daily["Volume"].rolling(20).mean()
    daily["return_5d"] = daily["Close"].pct_change(5)
    daily["return_20d"] = daily["Close"].pct_change(20)

    daily_row = daily.iloc[-1]
    sma20 = _safe_float(daily_row["sma20"])
    sma50 = _safe_float(daily_row["sma50"])
    close = _safe_float(daily_row["Close"])
    return_5d = _safe_float(daily_row["return_5d"])
    return_20d = _safe_float(daily_row["return_20d"])
    avg_volume20 = _safe_float(daily_row["avg_volume20"])
    daily_volume = _safe_float(daily_row["Volume"])
    volume_ratio = daily_volume / avg_volume20 if avg_volume20 else 0.0

    # 52-week high/low
    recent = daily.tail(252)
    high_52w = _safe_float(recent["High"].max() if "High" in recent.columns else recent["Close"].max())
    low_52w = _safe_float(recent["Low"].min() if "Low" in recent.columns else recent["Close"].min())
    pct_from_52w_high = (close - high_52w) / high_52w if high_52w else 0.0

    # RSI
    rsi14 = compute_rsi(daily["Close"].tail(60))

    intraday = None
    if intraday_df is not None and not intraday_df.empty:
        intraday = intraday_df.copy().dropna()

    last_price = close
    open_price = close
    day_change_pct = 0.0
    intraday_change_pct = 0.0
    intraday_volume_ratio = 0.0
    vwap_distance_pct = 0.0
    intraday_vwap = close

    if intraday is not None and len(intraday) >= 12:
        intraday["turnover"] = intraday["Close"] * intraday["Volume"]
        cumulative_volume = intraday["Volume"].cumsum()
        cumulative_turnover = intraday["turnover"].cumsum()
        intraday["vwap"] = cumulative_turnover / cumulative_volume.replace(0, None)
        intraday["avg_volume12"] = intraday["Volume"].rolling(12).mean()

        intraday_row = intraday.iloc[-1]
        last_price = _safe_float(intraday_row["Close"], close)
        open_price = _safe_float(intraday.iloc[0]["Open"], close)
        previous_close = _safe_float(daily.iloc[-2]["Close"], close) if len(daily) > 1 else close
        day_change_pct = (last_price - previous_close) / previous_close if previous_close else 0.0
        intraday_change_pct = (last_price - open_price) / open_price if open_price else 0.0
        avg_bar_volume = _safe_float(intraday_row["avg_volume12"])
        last_bar_volume = _safe_float(intraday_row["Volume"])
        intraday_volume_ratio = last_bar_volume / avg_bar_volume if avg_bar_volume else 0.0
        intraday_vwap = _safe_float(intraday_row["vwap"], last_price)
        vwap_distance_pct = (last_price - intraday_vwap) / intraday_vwap if intraday_vwap else 0.0

    # --- Scoring ---
    score = 0
    reasons: list[str] = []

    if last_price > sma20:
        score += 15
        reasons.append("above 20-day average")
    else:
        score -= 5

    if last_price > sma50:
        score += 15
        reasons.append("above 50-day average")
    else:
        score -= 5

    if return_5d > 0.01:
        score += 10
        reasons.append("positive 5-day momentum")
    elif return_5d < -0.03:
        score -= 8

    if return_20d > 0.03:
        score += 10
        reasons.append("positive 20-day momentum")
    elif return_20d < -0.08:
        score -= 10

    if volume_ratio > 1.1:
        score += 10
        reasons.append("daily volume above average")
    elif volume_ratio < 0.8:
        score -= 5

    if intraday_change_pct > 0.003:
        score += 15
        reasons.append("strong move from today's open")
    elif intraday_change_pct < -0.004:
        score -= 10

    if day_change_pct > 0.005:
        score += 10
        reasons.append("green versus prior close")
    elif day_change_pct < -0.008:
        score -= 8

    if intraday_volume_ratio > 1.2:
        score += 10
        reasons.append("intraday volume spike")
    elif intraday_volume_ratio < 0.7 and intraday_volume_ratio > 0:
        score -= 5

    if vwap_distance_pct > 0.002:
        score += 10
        reasons.append("trading above VWAP")
    elif vwap_distance_pct < -0.003:
        score -= 8

    if last_price < sma20 < sma50:
        score -= 12

    # RSI scoring
    if 50 <= rsi14 <= 70:
        score += 10
        reasons.append(f"RSI healthy ({rsi14:.0f})")
    elif rsi14 > 80:
        score -= 8
        reasons.append(f"RSI overbought ({rsi14:.0f})")
    elif rsi14 < 35:
        score -= 8
        reasons.append(f"RSI oversold ({rsi14:.0f})")

    # 52-week high proximity
    if -0.05 <= pct_from_52w_high <= 0:
        score += 10
        reasons.append("near 52-week high")
    elif pct_from_52w_high < -0.40:
        score -= 8

    # Earnings penalty
    if earnings_soon:
        score -= 15
        reasons.append("⚠️ earnings within 3 days")

    # Market regime adjustment
    if regime is not None and regime.regime_penalty != 0:
        score += regime.regime_penalty
        if regime.regime == "weak":
            reasons.append("market regime weak")
        elif regime.regime == "neutral":
            reasons.append("market regime neutral")

    reason = ", ".join(reasons) if reasons else "no strong intraday signal"

    # --- Entry / Stop / Target ---
    # Entry: current price (or VWAP if price is above it — cleaner entry)
    suggested_entry = round(min(last_price, intraday_vwap) if intraday_vwap > 0 else last_price, 2)

    # Stop: 5-day low or 3% below entry, whichever is tighter
    recent_5d_low = _safe_float(daily.tail(5)["Low"].min() if "Low" in daily.columns else last_price * 0.97)
    stop_pct = last_price * 0.97
    stop_loss = round(max(recent_5d_low, stop_pct) if recent_5d_low < last_price else stop_pct, 2)

    # Target: 2:1 risk/reward from entry
    risk = suggested_entry - stop_loss
    target = round(suggested_entry + (risk * 2), 2) if risk > 0 else round(last_price * 1.06, 2)

    return PickResult(
        symbol=symbol,
        score=score,
        action=classify_action(score),
        last_price=last_price,
        open_price=open_price,
        day_change_pct=day_change_pct,
        intraday_change_pct=intraday_change_pct,
        sma20=sma20,
        sma50=sma50,
        return_5d=return_5d,
        return_20d=return_20d,
        volume_ratio=volume_ratio,
        intraday_volume_ratio=intraday_volume_ratio,
        vwap_distance_pct=vwap_distance_pct,
        rsi14=rsi14,
        high_52w=high_52w,
        low_52w=low_52w,
        pct_from_52w_high=pct_from_52w_high,
        suggested_entry=suggested_entry,
        stop_loss=stop_loss,
        target=target,
        earnings_soon=earnings_soon,
        reason=reason,
    )
