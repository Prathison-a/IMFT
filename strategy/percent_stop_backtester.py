"""Shared percent stop / take profit backtester matching PercentStopBacktester.cs."""
from dataclasses import dataclass
from typing import List, Callable, Optional, Dict, Any, Tuple
from strategy.models import PriceBar, Trade, BacktestResult, StrategyParameterDefinition
from strategy.math_utils import build_result, empty_result, is_within_new_trade_window, stop_fill, limit_fill

@dataclass
class CommonStopParams:
    stop_loss_pct: float = 2.0
    take_profit_pct: float = 5.0
    use_trailing_stop: bool = False
    trailing_stop_pct: float = 2.0
    allow_short: bool = True
    risk_pct: float = 0.03
    max_leverage: float = 4.0
    slippage_bps: float = 1.0
    restrict_new_trades_to_trading_hours: bool = False
    new_trades_start_time_cst: str = "09:00"
    new_trades_end_time_cst: str = "14:30"

    @classmethod
    def get_definitions(cls) -> List[StrategyParameterDefinition]:
        return [
            StrategyParameterDefinition("StopLossPct", "Stop loss (% of entry, 0 = none, size 100% equity)", "double", 2.0),
            StrategyParameterDefinition("TakeProfitPct", "Take profit (% of entry, 0 = none)", "double", 5.0),
            StrategyParameterDefinition("UseTrailingStop", "Use trailing stop instead of fixed stop", "bool", False),
            StrategyParameterDefinition("TrailingStopPct", "Trailing stop distance (%)", "double", 2.0),
            StrategyParameterDefinition("AllowShort", "Allow short entries", "bool", True),
            StrategyParameterDefinition("RiskPct", "Risk per trade (% of equity)", "double", 3.0),
            StrategyParameterDefinition("MaxLeverage", "Max leverage (x notional)", "double", 4.0),
            StrategyParameterDefinition("SlippageBps", "Slippage (bps per side)", "double", 1.0),
            StrategyParameterDefinition("RestrictNewTradesToTradingHours", "Only take NEW trades during trading-hours window", "bool", False),
            StrategyParameterDefinition("NewTradesStartTimeCst", "New trades start (HH:mm CST)", "string", "09:00"),
            StrategyParameterDefinition("NewTradesEndTimeCst", "New trades end (HH:mm CST)", "string", "14:30"),
        ]

    @classmethod
    def from_dict(cls, p: Dict[str, Any]) -> "CommonStopParams":
        return cls(
            stop_loss_pct=float(p.get("StopLossPct", 2.0)),
            take_profit_pct=float(p.get("TakeProfitPct", 5.0)),
            use_trailing_stop=bool(p.get("UseTrailingStop", False)),
            trailing_stop_pct=float(p.get("TrailingStopPct", 2.0)),
            allow_short=bool(p.get("AllowShort", True)),
            risk_pct=float(p.get("RiskPct", 3.0)) / 100.0,
            max_leverage=float(p.get("MaxLeverage", 4.0)),
            slippage_bps=float(p.get("SlippageBps", 1.0)),
            restrict_new_trades_to_trading_hours=bool(p.get("RestrictNewTradesToTradingHours", False)),
            new_trades_start_time_cst=str(p.get("NewTradesStartTimeCst", "09:00")),
            new_trades_end_time_cst=str(p.get("NewTradesEndTimeCst", "14:30")),
        )

def run_percent_stop_backtest(
    bars: List[PriceBar],
    min_bar: int,
    signal_fn: Callable[[int], Tuple[bool, bool]],
    p: CommonStopParams,
    initial_equity: float,
    on_exit: Optional[Callable[[], None]] = None
) -> BacktestResult:
    n = len(bars)
    if n < 2 or min_bar >= n - 1:
        return empty_result()

    times = [b.time_utc for b in bars]
    opens = [float(b.open) for b in bars]
    highs = [float(b.high) for b in bars]
    lows = [float(b.low) for b in bars]
    closes = [float(b.close) for b in bars]

    slip = p.slippage_bps / 10000.0
    equity = initial_equity
    equity_curve = [equity] * n
    position = 0  # 1 = Long, -1 = Short, 0 = Flat
    shares = 0.0
    entry_price = 0.0
    stop_price = 0.0
    take_profit_price = 0.0
    peak_since_entry = 0.0
    entry_index = -1
    trades: List[Trade] = []

    for i in range(n):
        equity_curve[i] = equity + shares * (closes[i] - entry_price) * position if position != 0 else equity

        if i < min_bar:
            continue

        buy, sell = signal_fn(i)

        # Check exit of current open position
        if position != 0:
            # Trail from the extreme of bars already completed: using this bar's own high to raise
            # the stop and then its low to hit it would assume the high came first.
            if p.use_trailing_stop and p.trailing_stop_pct > 0:
                if position == 1:
                    stop_price = max(stop_price, peak_since_entry * (1.0 - p.trailing_stop_pct / 100.0))
                else:
                    stop_price = min(stop_price, peak_since_entry * (1.0 + p.trailing_stop_pct / 100.0))

            hit_stop = (lows[i] <= stop_price) if position == 1 else (highs[i] >= stop_price)
            hit_tp = False
            if not hit_stop and p.take_profit_pct > 0:
                hit_tp = (highs[i] >= take_profit_price) if position == 1 else (lows[i] <= take_profit_price)

            opposing_signal = False
            if not hit_stop and not hit_tp:
                opposing_signal = sell if position == 1 else buy

            exit_reason = None
            exit_price = 0.0
            if hit_stop:
                exit_reason = "trailing_stop" if p.use_trailing_stop else "stop_loss"
                exit_price = stop_fill(stop_price, opens[i], position) * (1.0 - slip * position)
            elif hit_tp:
                exit_reason = "take_profit"
                exit_price = limit_fill(take_profit_price, opens[i], position) * (1.0 - slip * position)
            elif opposing_signal:
                exit_reason = "signal_flip"
                exit_price = closes[i] * (1.0 - slip * position)

            if exit_reason is not None:
                pnl = shares * (exit_price - entry_price) * position
                equity += pnl
                ret_pct = pnl / (shares * entry_price) if shares > 0 else 0.0
                trades.append(Trade(
                    entry_time_utc=times[entry_index],
                    exit_time_utc=times[i],
                    direction=position,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    shares=shares,
                    pnl=pnl,
                    return_pct=ret_pct,
                    exit_reason=exit_reason,
                    bars_held=i - entry_index
                ))
                equity_curve[i] = equity
                position = 0
                shares = 0.0
                if on_exit:
                    on_exit()
            else:
                peak_since_entry = max(peak_since_entry, highs[i]) if position == 1 else min(peak_since_entry, lows[i])

        # Check entry if flat
        if position == 0 and i + 1 < n:
            long_ok = buy
            short_ok = p.allow_short and sell
            if (long_ok or short_ok) and not is_within_new_trade_window(
                times[i], p.restrict_new_trades_to_trading_hours, p.new_trades_start_time_cst, p.new_trades_end_time_cst
            ):
                long_ok = False
                short_ok = False

            if long_ok or short_ok:
                direction = 1 if long_ok else -1
                fill_price = opens[i + 1] * (1.0 + slip * direction)
                stop_dist = fill_price * p.stop_loss_pct / 100.0
                if stop_dist > 0 or p.stop_loss_pct == 0:
                    if stop_dist > 0:
                        risk_dollars = equity * p.risk_pct
                        sz = min(risk_dollars / stop_dist, (equity * p.max_leverage) / fill_price)
                    else:
                        # No fixed stop: allocate 100% of equity (capped by MaxLeverage).
                        sz = equity * min(1.0, p.max_leverage) / fill_price
                    if sz > 0:
                        position = direction
                        shares = sz
                        entry_price = fill_price
                        if stop_dist > 0:
                            stop_price = fill_price - direction * stop_dist
                        else:
                            stop_price = 0.0 if direction == 1 else float("inf")
                        take_profit_price = fill_price * (1.0 + direction * p.take_profit_pct / 100.0)
                        peak_since_entry = fill_price
                        entry_index = i + 1

    return build_result(times, equity_curve, trades, initial_equity)