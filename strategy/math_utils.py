"""Shared backtesting mathematics and metrics matching BacktestMath.cs."""
import math
from datetime import datetime, time, timezone, timedelta
from typing import List, Optional, Tuple
import pytz
from strategy.models import BacktestResult, Trade, EquityPoint, IndicatorPoint, PriceBar

CST_ZONE = pytz.timezone("America/Chicago")

def is_within_new_trade_window(bar_time_utc: datetime, restrict: bool, start_time_cst: str, end_time_cst: str) -> bool:
    if not restrict:
        return True
    try:
        sh, sm = map(int, start_time_cst.strip().split(":"))
        eh, em = map(int, end_time_cst.strip().split(":"))
        start_t = time(sh, sm)
        end_t = time(eh, em)
    except Exception:
        return True

    if bar_time_utc.tzinfo is None:
        bar_time_utc = bar_time_utc.replace(tzinfo=timezone.utc)
    cst_dt = bar_time_utc.astimezone(CST_ZONE)
    bar_t = cst_dt.time()
    return start_t <= bar_t < end_t

def stop_fill(stop_price: float, bar_open: float, direction: int) -> float:
    """A resting stop that the bar trades through fills at the stop -- or at the open, when the bar
    opens beyond it (a gap, or a trailing stop ratcheted above/below where price already is)."""
    return min(bar_open, stop_price) if direction == 1 else max(bar_open, stop_price)

def limit_fill(limit_price: float, bar_open: float, direction: int) -> float:
    """A resting take-profit limit fills at its price, or at the better open when the bar gaps through it."""
    return max(bar_open, limit_price) if direction == 1 else min(bar_open, limit_price)

def sample_std_dev(x: List[float], mean: float) -> float:
    if len(x) < 2:
        return 0.0
    sum_sq = sum((v - mean) ** 2 for v in x)
    return math.sqrt(sum_sq / (len(x) - 1))

def shift_by_one(x: List[float]) -> List[float]:
    out = [float('nan')] * len(x)
    for i in range(1, len(x)):
        out[i] = x[i - 1]
    return out

def to_indicator_points(bars: List[PriceBar], values: List[float]) -> List[IndicatorPoint]:
    pts = []
    n = min(len(bars), len(values))
    for i in range(n):
        val = values[i]
        if not math.isnan(val):
            pts.append(IndicatorPoint(bars[i].time_utc, float(val)))
    return pts

def build_result(time_list: List[datetime], equity_curve: List[float], trades: List[Trade], initial_equity: float) -> BacktestResult:
    n = len(time_list)
    if n == 0 or len(equity_curve) == 0:
        return empty_result()

    equity_points = [EquityPoint(time_list[i], equity_curve[i]) for i in range(n)]

    # Daily resampling (last per UTC calendar date)
    daily_last = {}
    for i in range(n):
        d = time_list[i].date()
        daily_last[d] = equity_curve[i]

    daily_values = list(daily_last.values())
    daily_ret = []
    for i in range(1, len(daily_values)):
        if daily_values[i - 1] > 0:
            daily_ret.append(daily_values[i] / daily_values[i - 1] - 1.0)

    sharpe = float('nan')
    if len(daily_ret) > 5:
        mean_ret = sum(daily_ret) / len(daily_ret)
        std_ret = sample_std_dev(daily_ret, mean_ret)
        if std_ret > 0:
            sharpe = (mean_ret / std_ret) * math.sqrt(252.0)

    years = (time_list[-1] - time_list[0]).total_seconds() / (86400.0 * 365.25) if n > 1 else float('nan')
    total_ret = (equity_curve[-1] / initial_equity) - 1.0
    cagr = float('nan')
    if years > 0 and (1.0 + total_ret) > 0:
        cagr = ((1.0 + total_ret) ** (1.0 / years)) - 1.0

    running_max = float('-inf')
    max_dd = 0.0
    for eq in equity_curve:
        if eq > running_max:
            running_max = eq
        if running_max > 0:
            dd = (eq - running_max) / running_max
            if dd < max_dd:
                max_dd = dd

    win_rate = 0.0
    profit_factor = float('nan')
    if len(trades) > 0:
        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        win_rate = len(wins) / len(trades)
        gross_win = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float('inf')

    return BacktestResult(
        equity_curve=equity_points,
        trades=trades,
        sharpe=sharpe,
        cagr=cagr,
        max_drawdown=max_dd,
        win_rate=win_rate,
        num_trades=len(trades),
        profit_factor=profit_factor,
    )

def split_by_date(result: BacktestResult, split: datetime) -> List[Tuple[str, BacktestResult]]:
    """In-sample / out-of-sample metrics: equity before vs. from `split`, trades by entry time.
    The out-of-sample curve starts at the last in-sample point, so the return across the boundary is counted."""
    curve = result.equity_curve
    if not curve:
        return []
    if curve[0].time_utc.tzinfo is not None and split.tzinfo is None:
        split = split.replace(tzinfo=timezone.utc)
    elif curve[0].time_utc.tzinfo is None and split.tzinfo is not None:
        split = split.replace(tzinfo=None)
    k = next((i for i, pt in enumerate(curve) if pt.time_utc >= split), len(curve))
    label_date = split.strftime("%Y-%m-%d")
    periods = []
    for label, seg, in_sample in ((f"In-sample (< {label_date})", curve[:k], True),
                              (f"Out-of-sample (>= {label_date})", curve[max(k - 1, 0):], False)):
        if len(seg) < 2 or (not in_sample and k >= len(curve)):
            continue
        trades = [t for t in result.trades if (t.entry_time_utc < split) == in_sample]
        periods.append((label, build_result([pt.time_utc for pt in seg], [pt.equity for pt in seg], trades, seg[0].equity)))
    return periods

def empty_result() -> BacktestResult:
    return BacktestResult(
        equity_curve=[],
        trades=[],
        indicators=[],
        sharpe=float('nan'),
        cagr=float('nan'),
        max_drawdown=float('nan'),
        win_rate=0.0,
        num_trades=0,
        profit_factor=float('nan')
    )