from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolHistory:
    prev_avg_score_3: float = 0.0
    prev_buy_rate_3: float = 0.0
    prev_watch_rate_3: float = 0.0
    prev_score_std_3: float = 0.0
    sample_size: int = 0


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
    reason: str


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value != value:  # NaN check
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def classify_action(score: int) -> str:
    if score >= 80:
        return "Buy Watch"
    if score >= 58:
        return "Watch"
    return "Skip"


def score_symbol(
    symbol: str,
    daily_df,
    intraday_df,
    history: SymbolHistory | None = None,
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

    intraday = None
    if intraday_df is not None and not intraday_df.empty:
        intraday = intraday_df.copy().dropna()

    last_price = close
    open_price = close
    day_change_pct = 0.0
    intraday_change_pct = 0.0
    intraday_volume_ratio = 0.0
    vwap_distance_pct = 0.0

    if intraday is not None:
        intraday = intraday.loc[intraday["Close"].notna()].copy()
        intraday["Volume"] = intraday["Volume"].fillna(0)
        traded_intraday = intraday.loc[intraday["Volume"] > 0].copy()
    else:
        traded_intraday = None

    if traded_intraday is not None and len(traded_intraday) >= 12:
        traded_intraday["turnover"] = traded_intraday["Close"] * traded_intraday["Volume"]
        cumulative_volume = traded_intraday["Volume"].cumsum()
        cumulative_turnover = traded_intraday["turnover"].cumsum()
        traded_intraday["vwap"] = cumulative_turnover / cumulative_volume
        traded_intraday["avg_volume12"] = traded_intraday["Volume"].rolling(12).mean()

        intraday_row = traded_intraday.iloc[-1]
        last_price = _safe_float(intraday_row["Close"], close)
        open_price = _safe_float(traded_intraday.iloc[0]["Open"], close)
        previous_close = _safe_float(daily.iloc[-2]["Close"], close) if len(daily) > 1 else close
        day_change_pct = (
            (last_price - previous_close) / previous_close if previous_close else 0.0
        )
        intraday_change_pct = (
            (last_price - open_price) / open_price if open_price else 0.0
        )
        avg_bar_volume = _safe_float(intraday_row["avg_volume12"])
        last_bar_volume = _safe_float(intraday_row["Volume"])
        intraday_volume_ratio = last_bar_volume / avg_bar_volume if avg_bar_volume else 0.0
        intraday_vwap = _safe_float(intraday_row["vwap"], last_price)
        vwap_distance_pct = (
            (last_price - intraday_vwap) / intraday_vwap if intraday_vwap else 0.0
        )
    elif intraday is not None and not intraday.empty:
        last_price = _safe_float(intraday.iloc[-1]["Close"], close)
        open_price = _safe_float(intraday.iloc[0]["Open"], close)
        previous_close = _safe_float(daily.iloc[-2]["Close"], close) if len(daily) > 1 else close
        day_change_pct = (
            (last_price - previous_close) / previous_close if previous_close else 0.0
        )
        intraday_change_pct = (
            (last_price - open_price) / open_price if open_price else 0.0
        )

    score = 0
    reasons: list[str] = []
    history = history or SymbolHistory()

    if last_price > sma20:
        score += 12
        reasons.append("above 20-day average")
    else:
        score -= 8

    if last_price > sma50:
        score += 12
        reasons.append("above 50-day average")
    else:
        score -= 8

    if return_5d >= 0.035:
        score += 14
        reasons.append("strong 5-day momentum")
    elif return_5d >= 0.015:
        score += 8
        reasons.append("positive 5-day momentum")
    elif return_5d < -0.03:
        score -= 12
    elif return_5d < 0:
        score -= 6

    if return_20d >= 0.20:
        score += 14
        reasons.append("strong 20-day trend")
    elif return_20d >= 0.10:
        score += 8
        reasons.append("positive 20-day momentum")
    elif return_20d < -0.08:
        score -= 12
    elif return_20d < 0:
        score -= 6

    if volume_ratio >= 1.2:
        score += 7
        reasons.append("daily volume above average")
    elif volume_ratio >= 1.0:
        score += 4
    elif volume_ratio < 0.75:
        score -= 4

    if intraday_change_pct >= 0.02:
        score += 16
        reasons.append("strong move from today's open")
    elif intraday_change_pct >= 0.005:
        score += 10
        reasons.append("positive move from today's open")
    elif intraday_change_pct <= -0.02:
        score -= 16
    elif intraday_change_pct < -0.005:
        score -= 10

    if day_change_pct >= 0.015:
        score += 10
        reasons.append("green versus prior close")
    elif day_change_pct >= 0.005:
        score += 6
        reasons.append("up versus prior close")
    elif day_change_pct <= -0.015:
        score -= 10
    elif day_change_pct < -0.005:
        score -= 6

    if intraday_volume_ratio > 0:
        if intraday_volume_ratio >= 1.8:
            score += 6
            reasons.append("intraday volume spike")
        elif intraday_volume_ratio >= 1.3:
            score += 3
            reasons.append("intraday volume support")
        elif intraday_volume_ratio < 0.7:
            score -= 2

    if vwap_distance_pct >= 0.02:
        score += 12
        reasons.append("well above VWAP")
    elif vwap_distance_pct >= 0.007:
        score += 8
        reasons.append("trading above VWAP")
    elif vwap_distance_pct <= -0.02:
        score -= 12
    elif vwap_distance_pct < -0.005:
        score -= 8

    if history.sample_size >= 2:
        if history.prev_buy_rate_3 >= 0.67:
            score += 12
            reasons.append("consistent recent Buy Watch history")
        elif history.prev_buy_rate_3 >= 0.34:
            score += 6
            reasons.append("recent Buy Watch history")

        if history.prev_watch_rate_3 >= 0.67:
            score += 6
            reasons.append("recent watchlist persistence")

        if history.prev_avg_score_3 >= 75:
            score += 10
            reasons.append("high recent average score")
        elif history.prev_avg_score_3 >= 60:
            score += 5
        elif history.prev_avg_score_3 < 25:
            score -= 6

        if history.prev_score_std_3 >= 25:
            score -= 8
        elif history.prev_score_std_3 >= 15:
            score -= 4

    if last_price < sma20 < sma50:
        score -= 12

    if intraday_change_pct > 0 and vwap_distance_pct < 0:
        score -= 6
    if day_change_pct > 0 and return_5d < 0:
        score -= 4
    if return_20d > 0.20 and return_5d < 0:
        score -= 4

    reason = ", ".join(reasons) if reasons else "no strong intraday signal"

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
        reason=reason,
    )
