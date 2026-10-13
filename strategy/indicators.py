"""Technical indicator calculations matching Indicators.cs and PortedIndicators.cs."""
import math
from typing import List, Tuple, Dict, Optional
from strategy.models import PriceBar

def ewm_adjust_false(x: List[float], alpha: float, min_periods: int) -> List[float]:
    """Replicates pandas ewm(alpha, adjust=False, min_periods).mean()"""
    n = len(x)
    out = [float('nan')] * n
    prev = float('nan')
    valid_count = 0
    for i in range(n):
        val = x[i]
        if math.isnan(val):
            out[i] = float('nan')
            continue
        prev = val if math.isnan(prev) else alpha * val + (1.0 - alpha) * prev
        valid_count += 1
        out[i] = prev if valid_count >= min_periods else float('nan')
    return out

def true_range(bars: List[PriceBar]) -> List[float]:
    n = len(bars)
    tr = [float('nan')] * n
    for i in range(n):
        high = float(bars[i].high)
        low = float(bars[i].low)
        hl = high - low
        if i == 0:
            tr[i] = hl
            continue
        prev_close = float(bars[i - 1].close)
        hc = abs(high - prev_close)
        lc = abs(low - prev_close)
        tr[i] = max(hl, max(hc, lc))
    return tr

def atr(bars: List[PriceBar], period: int) -> List[float]:
    tr = true_range(bars)
    return ewm_adjust_false(tr, 1.0 / period, period)

def supertrend(bars: List[PriceBar], period: int, multiplier: float) -> Tuple[List[float], List[int]]:
    """Returns (supertrend_line, direction) where direction is +1 (bull) or -1 (bear)."""
    n = len(bars)
    atr_vals = atr(bars, period)
    final_upper = [float('nan')] * n
    final_lower = [float('nan')] * n
    line = [float('nan')] * n
    direction = [1] * n

    start_i = -1
    for i in range(n):
        if not math.isnan(atr_vals[i]):
            start_i = i
            break
    if start_i < 0:
        return line, direction

    def hl2(idx: int) -> float:
        return (float(bars[idx].high) + float(bars[idx].low)) / 2.0

    final_upper[start_i] = hl2(start_i) + multiplier * atr_vals[start_i]
    final_lower[start_i] = hl2(start_i) - multiplier * atr_vals[start_i]
    direction[start_i] = 1
    line[start_i] = final_lower[start_i]

    for i in range(start_i + 1, n):
        ub = hl2(i) + multiplier * atr_vals[i]
        lb = hl2(i) - multiplier * atr_vals[i]
        close_prev = float(bars[i - 1].close)
        close = float(bars[i].close)

        final_upper[i] = ub if (ub < final_upper[i - 1] or close_prev > final_upper[i - 1]) else final_upper[i - 1]
        final_lower[i] = lb if (lb > final_lower[i - 1] or close_prev < final_lower[i - 1]) else final_lower[i - 1]

        if direction[i - 1] == 1:
            direction[i] = -1 if close < final_lower[i] else 1
        else:
            direction[i] = 1 if close > final_upper[i] else -1

        line[i] = final_lower[i] if direction[i] == 1 else final_upper[i]

    return line, direction

def adx(bars: List[PriceBar], period: int) -> List[float]:
    n = len(bars)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(n):
        if i == 0:
            continue
        up_move = float(bars[i].high) - float(bars[i - 1].high)
        down_move = float(bars[i - 1].low) - float(bars[i].low)
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0

    tr = true_range(bars)
    atr_vals = ewm_adjust_false(tr, 1.0 / period, period)
    plus_dm_smoothed = ewm_adjust_false(plus_dm, 1.0 / period, period)
    minus_dm_smoothed = ewm_adjust_false(minus_dm, 1.0 / period, period)

    dx = [float('nan')] * n
    for i in range(n):
        if not math.isnan(atr_vals[i]) and atr_vals[i] > 0:
            p_di = 100.0 * plus_dm_smoothed[i] / atr_vals[i]
            m_di = 100.0 * minus_dm_smoothed[i] / atr_vals[i]
            denom = p_di + m_di
            dx[i] = 100.0 * abs(p_di - m_di) / denom if denom > 0 else 0.0

    return ewm_adjust_false(dx, 1.0 / period, period)

def rolling_max(x: List[float], period: int) -> List[float]:
    n = len(x)
    out = [float('nan')] * n
    for i in range(n):
        if i < period - 1:
            continue
        out[i] = max(x[i - period + 1 : i + 1])
    return out

def rolling_min(x: List[float], period: int) -> List[float]:
    n = len(x)
    out = [float('nan')] * n
    for i in range(n):
        if i < period - 1:
            continue
        out[i] = min(x[i - period + 1 : i + 1])
    return out

def chandelier_exit(bars: List[PriceBar], period: int, multiplier: float) -> Tuple[List[float], List[float]]:
    atr_vals = atr(bars, period)
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    roll_high = rolling_max(highs, period)
    roll_low = rolling_min(lows, period)
    n = len(bars)
    long_stop = [float('nan')] * n
    short_stop = [float('nan')] * n
    for i in range(n):
        if not math.isnan(roll_high[i]) and not math.isnan(atr_vals[i]):
            long_stop[i] = roll_high[i] - multiplier * atr_vals[i]
            short_stop[i] = roll_low[i] + multiplier * atr_vals[i]
    return long_stop, short_stop

def donchian_channel(bars: List[PriceBar], period: int) -> Tuple[List[float], List[float]]:
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    roll_high = rolling_max(highs, period)
    roll_low = rolling_min(lows, period)
    n = len(bars)
    upper_prior = [float('nan')] * n
    lower_prior = [float('nan')] * n
    for i in range(1, n):
        upper_prior[i] = roll_high[i - 1]
        lower_prior[i] = roll_low[i - 1]
    return upper_prior, lower_prior

def atr_dynamic_stop(bars: List[PriceBar], period: int, multiplier: float) -> Tuple[List[float], List[int]]:
    n = len(bars)
    atr_vals = atr(bars, period)
    stop = [float('nan')] * n
    direction = [1] * n

    start_i = -1
    for i in range(n):
        if not math.isnan(atr_vals[i]):
            start_i = i
            break
    if start_i < 0:
        return stop, direction

    direction[start_i] = 1
    stop[start_i] = float(bars[start_i].close) - multiplier * atr_vals[start_i]

    for i in range(start_i + 1, n):
        close = float(bars[i].close)
        offset = multiplier * atr_vals[i]
        prev_dir = direction[i - 1]
        prev_stop = stop[i - 1]

        if prev_dir == 1:
            if close < prev_stop:
                direction[i] = -1
                stop[i] = close + offset
            else:
                direction[i] = 1
                stop[i] = max(prev_stop, close - offset)
        else:
            if close > prev_stop:
                direction[i] = 1
                stop[i] = close - offset
            else:
                direction[i] = -1
                stop[i] = min(prev_stop, close + offset)

    return stop, direction

# --- Ported Indicators ---

def sma(prices: List[float], period: int) -> List[float]:
    n = len(prices)
    res = [float('nan')] * n
    for i in range(n):
        if i < period - 1:
            continue
        res[i] = sum(prices[i - period + 1 : i + 1]) / period
    return res

def ema(prices: List[float], period: int) -> List[float]:
    n = len(prices)
    res = [float('nan')] * n
    if n == 0 or period < 1:
        return res
    k = 2.0 / (period + 1)
    if n < period:
        res[0] = prices[0]
        for i in range(1, n):
            res[i] = prices[i] * k + res[i - 1] * (1.0 - k)
        return res

    for i in range(period - 1):
        res[i] = prices[i]
    seed = sum(prices[:period]) / period
    res[period - 1] = seed
    for i in range(period, n):
        res[i] = prices[i] * k + res[i - 1] * (1.0 - k)
    return res

def wma(prices: List[float], period: int) -> List[float]:
    n = len(prices)
    res = [float('nan')] * n
    denom = period * (period + 1) / 2.0
    for i in range(n):
        if i < period - 1:
            res[i] = prices[i]
            continue
        w_sum = sum(prices[i - period + 1 + j] * (j + 1) for j in range(period))
        res[i] = w_sum / denom
    return res

def hma(prices: List[float], period: int) -> List[float]:
    half_period = max(1, period // 2)
    sqrt_period = max(1, int(math.sqrt(period)))
    wma_half = wma(prices, half_period)
    wma_full = wma(prices, period)
    diff = [2.0 * wma_half[i] - wma_full[i] for i in range(len(prices))]
    return wma(diff, sqrt_period)

def rsi(prices: List[float], period: int = 14) -> List[float]:
    n = len(prices)
    res = [float('nan')] * n
    if n <= period:
        return res
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        diff = prices[i] - prices[i - 1]
        if diff >= 0:
            gains[i] = diff
        else:
            losses[i] = -diff

    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    res[period] = 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss))) if avg_loss > 0 else 100.0

    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 1e9
        res[i] = 100.0 - (100.0 / (1.0 + rs))

    return res

def macd(prices: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[List[float], List[float], List[float]]:
    fast_ema = ema(prices, fast)
    slow_ema = ema(prices, slow)
    n = len(prices)
    macd_line = [fast_ema[i] - slow_ema[i] for i in range(n)]
    signal_line = ema(macd_line, signal)
    histogram = [macd_line[i] - signal_line[i] for i in range(n)]
    return macd_line, signal_line, histogram

def bollinger_bands(prices: List[float], period: int = 20, mult: float = 2.0) -> Tuple[List[float], List[float], List[float]]:
    n = len(prices)
    mid = sma(prices, period)
    upper = [float('nan')] * n
    lower = [float('nan')] * n
    for i in range(n):
        if i < period - 1:
            continue
        window = prices[i - period + 1 : i + 1]
        m = mid[i]
        std = math.sqrt(sum((x - m) ** 2 for x in window) / period)
        upper[i] = m + mult * std
        lower[i] = m - mult * std
    return upper, mid, lower

def keltner_channels(bars: List[PriceBar], ema_period: int = 20, atr_period: int = 10, mult: float = 1.5) -> Tuple[List[float], List[float], List[float]]:
    closes = [float(b.close) for b in bars]
    mid = ema(closes, ema_period)
    atr_vals = atr(bars, atr_period)
    n = len(bars)
    upper = [mid[i] + mult * atr_vals[i] for i in range(n)]
    lower = [mid[i] - mult * atr_vals[i] for i in range(n)]
    return upper, mid, lower

def ttm_squeeze(bars: List[PriceBar], bb_period: int = 20, bb_mult: float = 2.0, kc_period: int = 20, kc_mult: float = 1.5) -> Tuple[List[bool], List[float]]:
    closes = [float(b.close) for b in bars]
    bb_u, bb_m, bb_l = bollinger_bands(closes, bb_period, bb_mult)
    kc_u, kc_m, kc_l = keltner_channels(bars, kc_period, kc_period, kc_mult)
    n = len(bars)
    is_squeeze = [False] * n
    hist = [0.0] * n
    for i in range(n):
        if not math.isnan(bb_u[i]) and not math.isnan(kc_u[i]):
            is_squeeze[i] = (bb_u[i] < kc_u[i]) and (bb_l[i] > kc_l[i])
            hist[i] = closes[i] - (bb_m[i] if not math.isnan(bb_m[i]) else closes[i])
    return is_squeeze, hist

def anchored_vwap(bars: List[PriceBar]) -> List[float]:
    n = len(bars)
    vwap = [float('nan')] * n
    if n == 0:
        return vwap
    cum_vol = 0.0
    cum_pv = 0.0
    for i in range(n):
        typ = (float(bars[i].high) + float(bars[i].low) + float(bars[i].close)) / 3.0
        v = float(bars[i].volume)
        cum_vol += v
        cum_pv += typ * v
        vwap[i] = (cum_pv / cum_vol) if cum_vol > 0 else typ
    return vwap

def tmo(bars: List[PriceBar], length: int = 14, calc_length: int = 5, smooth_length: int = 3) -> Tuple[List[float], List[float]]:
    n = len(bars)
    closes = [float(b.close) for b in bars]
    opens = [float(b.open) for b in bars]
    data = [0.0] * n
    for i in range(length, n):
        cnt = 0
        for j in range(length):
            c_idx = i - j
            o_idx = i - length
            if closes[c_idx] > opens[o_idx]:
                cnt += 1
            elif closes[c_idx] < opens[o_idx]:
                cnt -= 1
        data[i] = float(cnt)
    main = ema(data, calc_length)
    signal = ema(main, smooth_length)
    return main, signal

def trend_flow(bars: List[PriceBar], fast: int = 8, slow: int = 21) -> Tuple[List[float], List[float]]:
    closes = [float(b.close) for b in bars]
    f = ema(closes, fast)
    s = ema(closes, slow)
    return f, s

def composite_index(bars: List[PriceBar]) -> Tuple[List[float], List[float], List[float]]:
    closes = [float(b.close) for b in bars]
    rsi_vals = rsi(closes, 14)
    mom_rsi = [float('nan')] * len(closes)
    for i in range(9, len(closes)):
        mom_rsi[i] = rsi_vals[i] - rsi_vals[i - 9]
    sma_mom = sma(mom_rsi, 3)
    sma_rsi = sma(rsi_vals, 3)
    ci = [float('nan')] * len(closes)
    for i in range(len(closes)):
        if not math.isnan(rsi_vals[i]) and not math.isnan(sma_mom[i]) and not math.isnan(sma_rsi[i]):
            ci[i] = (rsi_vals[i] - 50.0) + sma_mom[i] + (sma_rsi[i] - 50.0)
    fast = sma(ci, 13)
    slow = sma(ci, 33)
    return ci, fast, slow

class Indicators:
    supertrend = staticmethod(supertrend)
    adx = staticmethod(adx)
    chandelier_exit = staticmethod(chandelier_exit)
    donchian_channel = staticmethod(donchian_channel)
    atr_dynamic_stop = staticmethod(atr_dynamic_stop)
    sma = staticmethod(sma)
    ema = staticmethod(ema)
    hma = staticmethod(hma)
    rsi = staticmethod(rsi)
    macd = staticmethod(macd)
    bollinger_bands = staticmethod(bollinger_bands)
    keltner_channels = staticmethod(keltner_channels)
    ttm_squeeze = staticmethod(ttm_squeeze)
    anchored_vwap = staticmethod(anchored_vwap)
    tmo = staticmethod(tmo)
    trend_flow = staticmethod(trend_flow)
    composite_index = staticmethod(composite_index)
    atr = staticmethod(atr)
