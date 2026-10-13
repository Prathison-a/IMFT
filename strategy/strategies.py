"""All 19 backtestable strategies matching StrategyRegistry.cs."""
import math
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Tuple, Optional
from strategy.models import PriceBar, BacktestResult, StrategyParameterDefinition, IndicatorSeries
from strategy.math_utils import build_result, empty_result, is_within_new_trade_window, shift_by_one, to_indicator_points, stop_fill
import strategy.indicators as ind
from strategy.percent_stop_backtester import CommonStopParams, run_percent_stop_backtest

class BaseStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        pass

    @property
    @abstractmethod
    def requires_anchor_timeframe(self) -> bool:
        pass

    @property
    @abstractmethod
    def parameter_definitions(self) -> List[StrategyParameterDefinition]:
        pass

    @abstractmethod
    def run(
        self,
        exec_bars: List[PriceBar],
        anchor_bars: List[PriceBar],
        parameters: Dict[str, Any],
        initial_equity: float
    ) -> BacktestResult:
        pass

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "requires_anchor_timeframe": self.requires_anchor_timeframe,
            "parameters": [p.to_dict() for p in self.parameter_definitions],
        }

# 1. Supertrend Multi-Timeframe Strategy
class SupertrendMultiTimeframeStrategy(BaseStrategy):
    name = "Multi-Timeframe Supertrend (research default)"
    description = (
        "Fast execution-timeframe Supertrend flips, confirmed by a wide/patient anchor-timeframe "
        "Supertrend + an ADX trend-strength filter. Exits via ATR stop and/or a chandelier trailing "
        "stop. Defaults are the best-performing shape found in the SPY research."
    )
    requires_anchor_timeframe = True

    parameter_definitions = [
        StrategyParameterDefinition("ExecSupertrendPeriod", "Exec Supertrend period", "int", 9),
        StrategyParameterDefinition("ExecSupertrendMultiplier", "Exec Supertrend multiplier", "double", 1.5),
        StrategyParameterDefinition("AnchorSupertrendPeriod", "Anchor Supertrend period", "int", 20),
        StrategyParameterDefinition("AnchorSupertrendMultiplier", "Anchor Supertrend multiplier", "double", 4.0),
        StrategyParameterDefinition("AdxPeriod", "ADX period", "int", 10),
        StrategyParameterDefinition("AdxThreshold", "ADX threshold (anchor trend strength)", "double", 15.0),
        StrategyParameterDefinition("StopAtrMultiplier", "ATR stop multiplier", "double", 3.0),
        StrategyParameterDefinition("UseTrailingStop", "Use chandelier trailing stop", "bool", True),
        StrategyParameterDefinition("ChandelierPeriod", "Chandelier period", "int", 22),
        StrategyParameterDefinition("ChandelierMultiplier", "Chandelier multiplier", "double", 2.0),
        StrategyParameterDefinition("AllowShort", "Allow short entries", "bool", True),
        StrategyParameterDefinition("RiskPct", "Risk per trade (% of equity)", "double", 3.0),
        StrategyParameterDefinition("MaxLeverage", "Max leverage (x notional)", "double", 4.0),
        StrategyParameterDefinition("SlippageBps", "Slippage (bps per side)", "double", 1.0),
        StrategyParameterDefinition("RestrictNewTradesToTradingHours", "Only take NEW trades during trading-hours window", "bool", False),
        StrategyParameterDefinition("NewTradesStartTimeCst", "New trades start (HH:mm CST)", "string", "09:00"),
        StrategyParameterDefinition("NewTradesEndTimeCst", "New trades end (HH:mm CST)", "string", "14:30"),
    ]

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        n = len(exec_bars)
        if n < 2 or len(anchor_bars) < 2:
            return empty_result()

        exec_st_p = int(parameters.get("ExecSupertrendPeriod", 9))
        exec_st_m = float(parameters.get("ExecSupertrendMultiplier", 1.5))
        anch_st_p = int(parameters.get("AnchorSupertrendPeriod", 20))
        anch_st_m = float(parameters.get("AnchorSupertrendMultiplier", 4.0))
        adx_p = int(parameters.get("AdxPeriod", 10))
        adx_thresh = float(parameters.get("AdxThreshold", 15.0))
        stop_atr_m = float(parameters.get("StopAtrMultiplier", 3.0))
        use_trail = bool(parameters.get("UseTrailingStop", True))
        chand_p = int(parameters.get("ChandelierPeriod", 22))
        chand_m = float(parameters.get("ChandelierMultiplier", 2.0))
        allow_short = bool(parameters.get("AllowShort", True))
        risk_pct = float(parameters.get("RiskPct", 3.0)) / 100.0
        max_lev = float(parameters.get("MaxLeverage", 4.0))
        slip = float(parameters.get("SlippageBps", 1.0)) / 10000.0
        restrict_hrs = bool(parameters.get("RestrictNewTradesToTradingHours", False))
        start_cst = str(parameters.get("NewTradesStartTimeCst", "09:00"))
        end_cst = str(parameters.get("NewTradesEndTimeCst", "14:30"))

        line1_raw, dir1_raw = ind.supertrend(exec_bars, exec_st_p, exec_st_m)
        atr1_full = ind.atr(exec_bars, max(exec_st_p, 14))

        line4_raw, dir4_raw = ind.supertrend(anchor_bars, anch_st_p, anch_st_m)
        adx4_raw = ind.adx(anchor_bars, adx_p)

        dir4_shifted = shift_by_one([float(d) for d in dir4_raw])
        adx4_shifted = shift_by_one(adx4_raw)
        line4_shifted = shift_by_one(line4_raw)

        dir4_full = [float('nan')] * n
        adx4_full = [float('nan')] * n
        line4_full = [float('nan')] * n
        anchor_idx = 0
        for i in range(n):
            t = exec_bars[i].time_utc
            while anchor_idx + 1 < len(anchor_bars) and anchor_bars[anchor_idx + 1].time_utc <= t:
                anchor_idx += 1
            if anchor_bars[anchor_idx].time_utc <= t:
                dir4_full[i] = dir4_shifted[anchor_idx]
                adx4_full[i] = adx4_shifted[anchor_idx]
                line4_full[i] = line4_shifted[anchor_idx]

        chand_long_full = None
        chand_short_full = None
        if use_trail:
            raw_l, raw_s = ind.chandelier_exit(exec_bars, chand_p, chand_m)
            chand_long_full = shift_by_one(raw_l)
            chand_short_full = shift_by_one(raw_s)

        start_index = -1
        for i in range(n):
            if not math.isnan(atr1_full[i]) and not math.isnan(dir4_full[i]) and not math.isnan(adx4_full[i]):
                start_index = i
                break
        if start_index < 0:
            return empty_result()

        kept_n = n - start_index
        times = [exec_bars[start_index + k].time_utc for k in range(kept_n)]
        opens = [float(exec_bars[start_index + k].open) for k in range(kept_n)]
        highs = [float(exec_bars[start_index + k].high) for k in range(kept_n)]
        lows = [float(exec_bars[start_index + k].low) for k in range(kept_n)]
        closes = [float(exec_bars[start_index + k].close) for k in range(kept_n)]
        dir1 = [dir1_raw[start_index + k] for k in range(kept_n)]
        atr1 = [atr1_full[start_index + k] for k in range(kept_n)]
        dir4 = [dir4_full[start_index + k] for k in range(kept_n)]
        adx4 = [adx4_full[start_index + k] for k in range(kept_n)]
        line1 = [line1_raw[start_index + k] for k in range(kept_n)]
        line4 = [line4_full[start_index + k] for k in range(kept_n)]
        chand_long = [chand_long_full[start_index + k] for k in range(kept_n)] if use_trail else None
        chand_short = [chand_short_full[start_index + k] for k in range(kept_n)] if use_trail else None

        prev_dir1 = [dir1[0]] + [dir1[k - 1] for k in range(1, kept_n)]

        equity = initial_equity
        equity_curve = [equity] * kept_n
        position = 0
        shares = 0.0
        entry_price = 0.0
        stop_price = 0.0
        entry_index = -1
        trades = []

        for i in range(kept_n):
            equity_curve[i] = equity + shares * (closes[i] - entry_price) * position if position != 0 else equity

            if position != 0:
                if use_trail:
                    if position == 1 and not math.isnan(chand_long[i]):
                        stop_price = max(stop_price, chand_long[i])
                    elif position == -1 and not math.isnan(chand_short[i]):
                        stop_price = min(stop_price, chand_short[i])

                hit_stop = (lows[i] <= stop_price) if position == 1 else (highs[i] >= stop_price)
                st_flip_exit = (i > 0 and dir1[i] == -position)
                anchor_against = (dir4[i] == -position)

                exit_reason = None
                exit_price = 0.0
                if hit_stop:
                    exit_reason = "stop"
                    exit_price = stop_fill(stop_price, opens[i], position) * (1.0 - slip * position)
                elif st_flip_exit or anchor_against:
                    exit_reason = "signal_flip" if st_flip_exit else "anchor_flip"
                    exit_price = closes[i] * (1.0 - slip * position)

                if exit_reason is not None:
                    pnl = shares * (exit_price - entry_price) * position
                    equity += pnl
                    ret_pct = pnl / (shares * entry_price) if shares > 0 else 0.0
                    from strategy.models import Trade
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

            if position == 0 and i + 1 < kept_n:
                fresh_long = (dir1[i] == 1 and prev_dir1[i] == -1)
                fresh_short = (dir1[i] == -1 and prev_dir1[i] == 1)

                long_ok = fresh_long and dir4[i] == 1 and adx4[i] >= adx_thresh
                short_ok = allow_short and fresh_short and dir4[i] == -1 and adx4[i] >= adx_thresh

                if (long_ok or short_ok) and not is_within_new_trade_window(times[i], restrict_hrs, start_cst, end_cst):
                    long_ok = False
                    short_ok = False

                if long_ok or short_ok:
                    direction = 1 if long_ok else -1
                    fill_price = opens[i + 1] * (1.0 + slip * direction)
                    stop_dist = atr1[i] * stop_atr_m
                    if stop_dist > 0 and not math.isnan(stop_dist):
                        risk_dollars = equity * risk_pct
                        sz = min(risk_dollars / stop_dist, (equity * max_lev) / fill_price)
                        if sz > 0:
                            position = direction
                            shares = sz
                            entry_price = fill_price
                            stop_price = fill_price - direction * stop_dist
                            entry_index = i + 1

        result = build_result(times, equity_curve, trades, initial_equity)
        result.indicators.append(IndicatorSeries("Exec Supertrend", "#38BDF8", "Solid", to_indicator_points(exec_bars[start_index:], line1)))
        result.indicators.append(IndicatorSeries("Anchor Supertrend", "#A78BFA", "Dashed", to_indicator_points(exec_bars[start_index:], line4)))
        if use_trail:
            result.indicators.append(IndicatorSeries("Chandelier long stop", "#10B981", "Dotted", to_indicator_points(exec_bars[start_index:], chand_long)))
            result.indicators.append(IndicatorSeries("Chandelier short stop", "#F43F5E", "Dotted", to_indicator_points(exec_bars[start_index:], chand_short)))
        return result

# 2. Donchian Breakout Strategy
class DonchianBreakoutStrategy(BaseStrategy):
    name = "Donchian Channel Breakout"
    description = (
        "Classic single-timeframe breakout: enter long on a new N-bar high, short on a new "
        "N-bar low. Exits via an ATR stop and/or a chandelier trailing stop."
    )
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("ChannelPeriod", "Breakout channel period (bars)", "int", 20),
        StrategyParameterDefinition("StopAtrPeriod", "ATR period (for stop)", "int", 14),
        StrategyParameterDefinition("StopAtrMultiplier", "ATR stop multiplier", "double", 2.5),
        StrategyParameterDefinition("UseTrailingStop", "Use chandelier trailing stop", "bool", True),
        StrategyParameterDefinition("ChandelierPeriod", "Chandelier period", "int", 22),
        StrategyParameterDefinition("ChandelierMultiplier", "Chandelier multiplier", "double", 3.0),
        StrategyParameterDefinition("AllowShort", "Allow short entries", "bool", True),
        StrategyParameterDefinition("RiskPct", "Risk per trade (% of equity)", "double", 3.0),
        StrategyParameterDefinition("MaxLeverage", "Max leverage (x notional)", "double", 4.0),
        StrategyParameterDefinition("SlippageBps", "Slippage (bps per side)", "double", 1.0),
        StrategyParameterDefinition("RestrictNewTradesToTradingHours", "Only take NEW trades during trading-hours window", "bool", False),
        StrategyParameterDefinition("NewTradesStartTimeCst", "New trades start (HH:mm CST)", "string", "09:00"),
        StrategyParameterDefinition("NewTradesEndTimeCst", "New trades end (HH:mm CST)", "string", "14:30"),
    ]

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        n = len(exec_bars)
        if n < 2:
            return empty_result()

        channel_p = int(parameters.get("ChannelPeriod", 20))
        stop_atr_p = int(parameters.get("StopAtrPeriod", 14))
        stop_atr_m = float(parameters.get("StopAtrMultiplier", 2.5))
        use_trail = bool(parameters.get("UseTrailingStop", True))
        chand_p = int(parameters.get("ChandelierPeriod", 22))
        chand_m = float(parameters.get("ChandelierMultiplier", 3.0))
        allow_short = bool(parameters.get("AllowShort", True))
        risk_pct = float(parameters.get("RiskPct", 3.0)) / 100.0
        max_lev = float(parameters.get("MaxLeverage", 4.0))
        slip = float(parameters.get("SlippageBps", 1.0)) / 10000.0
        restrict_hrs = bool(parameters.get("RestrictNewTradesToTradingHours", False))
        start_cst = str(parameters.get("NewTradesStartTimeCst", "09:00"))
        end_cst = str(parameters.get("NewTradesEndTimeCst", "14:30"))

        upper_prior, lower_prior = ind.donchian_channel(exec_bars, channel_p)
        atr_vals = ind.atr(exec_bars, stop_atr_p)

        chand_l_full = None
        chand_s_full = None
        if use_trail:
            raw_l, raw_s = ind.chandelier_exit(exec_bars, chand_p, chand_m)
            chand_l_full = shift_by_one(raw_l)
            chand_s_full = shift_by_one(raw_s)

        start_index = -1
        for i in range(n):
            if not math.isnan(upper_prior[i]) and not math.isnan(lower_prior[i]) and not math.isnan(atr_vals[i]):
                start_index = i
                break
        if start_index < 0:
            return empty_result()

        kept_n = n - start_index
        times = [exec_bars[start_index + k].time_utc for k in range(kept_n)]
        opens = [float(exec_bars[start_index + k].open) for k in range(kept_n)]
        highs = [float(exec_bars[start_index + k].high) for k in range(kept_n)]
        lows = [float(exec_bars[start_index + k].low) for k in range(kept_n)]
        closes = [float(exec_bars[start_index + k].close) for k in range(kept_n)]
        upper = [upper_prior[start_index + k] for k in range(kept_n)]
        lower = [lower_prior[start_index + k] for k in range(kept_n)]
        atr_k = [atr_vals[start_index + k] for k in range(kept_n)]
        chand_long = [chand_l_full[start_index + k] for k in range(kept_n)] if use_trail else None
        chand_short = [chand_s_full[start_index + k] for k in range(kept_n)] if use_trail else None

        equity = initial_equity
        equity_curve = [equity] * kept_n
        position = 0
        shares = 0.0
        entry_price = 0.0
        stop_price = 0.0
        entry_index = -1
        trades = []

        for i in range(kept_n):
            equity_curve[i] = equity + shares * (closes[i] - entry_price) * position if position != 0 else equity

            if position != 0:
                if use_trail:
                    if position == 1 and not math.isnan(chand_long[i]):
                        stop_price = max(stop_price, chand_long[i])
                    elif position == -1 and not math.isnan(chand_short[i]):
                        stop_price = min(stop_price, chand_short[i])

                hit_stop = (lows[i] <= stop_price) if position == 1 else (highs[i] >= stop_price)
                if hit_stop:
                    exit_price = stop_fill(stop_price, opens[i], position) * (1.0 - slip * position)
                    pnl = shares * (exit_price - entry_price) * position
                    equity += pnl
                    from strategy.models import Trade
                    trades.append(Trade(
                        entry_time_utc=times[entry_index],
                        exit_time_utc=times[i],
                        direction=position,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        shares=shares,
                        pnl=pnl,
                        return_pct=pnl / (shares * entry_price) if shares > 0 else 0.0,
                        exit_reason="stop",
                        bars_held=i - entry_index
                    ))
                    equity_curve[i] = equity
                    position = 0
                    shares = 0.0

            if position == 0 and i + 1 < kept_n:
                long_ok = closes[i] > upper[i]
                short_ok = allow_short and (closes[i] < lower[i])

                if (long_ok or short_ok) and not is_within_new_trade_window(times[i], restrict_hrs, start_cst, end_cst):
                    long_ok = False
                    short_ok = False

                if long_ok or short_ok:
                    direction = 1 if long_ok else -1
                    fill_price = opens[i + 1] * (1.0 + slip * direction)
                    stop_dist = atr_k[i] * stop_atr_m
                    if stop_dist > 0 and not math.isnan(stop_dist):
                        risk_dollars = equity * risk_pct
                        sz = min(risk_dollars / stop_dist, (equity * max_lev) / fill_price)
                        if sz > 0:
                            position = direction
                            shares = sz
                            entry_price = fill_price
                            stop_price = fill_price - direction * stop_dist
                            entry_index = i + 1

        result = build_result(times, equity_curve, trades, initial_equity)
        result.indicators.append(IndicatorSeries("Donchian upper", "#38BDF8", "Solid", to_indicator_points(exec_bars[start_index:], upper)))
        result.indicators.append(IndicatorSeries("Donchian lower", "#38BDF8", "Solid", to_indicator_points(exec_bars[start_index:], lower)))
        if use_trail:
            result.indicators.append(IndicatorSeries("Chandelier long stop", "#10B981", "Dotted", to_indicator_points(exec_bars[start_index:], chand_long)))
            result.indicators.append(IndicatorSeries("Chandelier short stop", "#F43F5E", "Dotted", to_indicator_points(exec_bars[start_index:], chand_short)))
        return result

# 3. ATR Dynamic Stop Reversal Strategy
class AtrDynamicStopReversalStrategy(BaseStrategy):
    name = "ATR Dynamic Stop Reversal"
    description = (
        "Always-in-market reversal: flips long/short whenever price closes across a chandelier-style "
        "ATR trailing stop (ported from NinjaTrader's MyATRDynamicStop)."
    )
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("AtrPeriod", "ATR period", "int", 14),
        StrategyParameterDefinition("AtrMultiplier", "ATR multiplier", "double", 2.5),
        StrategyParameterDefinition("EnableLongEntries", "Enable long entries", "bool", True),
        StrategyParameterDefinition("EnableShortEntries", "Enable short entries", "bool", True),
        StrategyParameterDefinition("StopLossPct", "Fixed stop-loss (% from entry, 0 = off)", "double", 0.0),
        StrategyParameterDefinition("UseChandelierStop", "Blend in standard Chandelier Exit stop", "bool", False),
        StrategyParameterDefinition("ChandelierPeriod", "Chandelier period", "int", 14),
        StrategyParameterDefinition("ChandelierMultiplier", "Chandelier multiplier", "double", 3.0),
        StrategyParameterDefinition("RiskPct", "Risk per trade (% of equity)", "double", 3.0),
        StrategyParameterDefinition("MaxLeverage", "Max leverage (x notional)", "double", 4.0),
        StrategyParameterDefinition("SlippageBps", "Slippage (bps per side)", "double", 1.0),
        StrategyParameterDefinition("RestrictNewTradesToTradingHours", "Only take NEW trades during trading-hours window", "bool", False),
        StrategyParameterDefinition("NewTradesStartTimeCst", "New trades start (HH:mm CST)", "string", "09:00"),
        StrategyParameterDefinition("NewTradesEndTimeCst", "New trades end (HH:mm CST)", "string", "14:30"),
    ]

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        n = len(exec_bars)
        if n < 2:
            return empty_result()

        atr_p = int(parameters.get("AtrPeriod", 14))
        atr_m = float(parameters.get("AtrMultiplier", 2.5))
        enable_long = bool(parameters.get("EnableLongEntries", True))
        enable_short = bool(parameters.get("EnableShortEntries", True))
        stop_loss_pct = float(parameters.get("StopLossPct", 0.0)) / 100.0
        use_chand = bool(parameters.get("UseChandelierStop", False))
        chand_p = int(parameters.get("ChandelierPeriod", 14))
        chand_m = float(parameters.get("ChandelierMultiplier", 3.0))
        risk_pct = float(parameters.get("RiskPct", 3.0)) / 100.0
        max_lev = float(parameters.get("MaxLeverage", 4.0))
        slip = float(parameters.get("SlippageBps", 1.0)) / 10000.0
        restrict_hrs = bool(parameters.get("RestrictNewTradesToTradingHours", False))
        start_cst = str(parameters.get("NewTradesStartTimeCst", "09:00"))
        end_cst = str(parameters.get("NewTradesEndTimeCst", "14:30"))

        stop_line, direction = ind.atr_dynamic_stop(exec_bars, atr_p, atr_m)
        chand_l_full = None
        chand_s_full = None
        if use_chand:
            chand_l_full, chand_s_full = ind.chandelier_exit(exec_bars, chand_p, chand_m)

        start_index = -1
        for i in range(1, n):
            if math.isnan(stop_line[i]) or math.isnan(stop_line[i - 1]):
                continue
            if use_chand and (math.isnan(chand_l_full[i]) or math.isnan(chand_s_full[i])):
                continue
            start_index = i
            break
        if start_index < 0:
            return empty_result()

        kept_n = n - start_index
        times = [exec_bars[start_index + k].time_utc for k in range(kept_n)]
        opens = [float(exec_bars[start_index + k].open) for k in range(kept_n)]
        highs = [float(exec_bars[start_index + k].high) for k in range(kept_n)]
        lows = [float(exec_bars[start_index + k].low) for k in range(kept_n)]
        closes = [float(exec_bars[start_index + k].close) for k in range(kept_n)]
        stop = [stop_line[start_index + k] for k in range(kept_n)]
        dir_vals = [direction[start_index + k] for k in range(kept_n)]
        prev_dir = [direction[start_index + k - 1] for k in range(kept_n)]
        chand_long = [chand_l_full[start_index + k] for k in range(kept_n)] if use_chand else None
        chand_short = [chand_s_full[start_index + k] for k in range(kept_n)] if use_chand else None

        equity = initial_equity
        equity_curve = [equity] * kept_n
        position = 0
        shares = 0.0
        entry_price = 0.0
        stop_price = 0.0
        entry_index = -1
        trades = []

        for i in range(kept_n):
            equity_curve[i] = equity + shares * (closes[i] - entry_price) * position if position != 0 else equity

            bull_flip = (dir_vals[i] == 1 and prev_dir[i] == -1)
            bear_flip = (dir_vals[i] == -1 and prev_dir[i] == 1)

            if position != 0 and ((position == 1 and bear_flip) or (position == -1 and bull_flip)):
                exit_price = closes[i] * (1.0 - slip * position)
                pnl = shares * (exit_price - entry_price) * position
                equity += pnl
                from strategy.models import Trade
                trades.append(Trade(
                    entry_time_utc=times[entry_index],
                    exit_time_utc=times[i],
                    direction=position,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    shares=shares,
                    pnl=pnl,
                    return_pct=pnl / (shares * entry_price) if shares > 0 else 0.0,
                    exit_reason="flip",
                    bars_held=i - entry_index
                ))
                equity_curve[i] = equity
                position = 0
                shares = 0.0

            if position != 0:
                if position == 1:
                    stop_price = max(stop_price, stop[i])
                    if use_chand and not math.isnan(chand_long[i]):
                        stop_price = max(stop_price, chand_long[i])
                    if stop_loss_pct > 0:
                        stop_price = max(stop_price, entry_price * (1.0 - stop_loss_pct))
                else:
                    stop_price = min(stop_price, stop[i])
                    if use_chand and not math.isnan(chand_short[i]):
                        stop_price = min(stop_price, chand_short[i])
                    if stop_loss_pct > 0:
                        stop_price = min(stop_price, entry_price * (1.0 + stop_loss_pct))

                hit_stop = (lows[i] <= stop_price) if position == 1 else (highs[i] >= stop_price)
                if hit_stop:
                    exit_price = stop_fill(stop_price, opens[i], position) * (1.0 - slip * position)
                    pnl = shares * (exit_price - entry_price) * position
                    equity += pnl
                    from strategy.models import Trade
                    trades.append(Trade(
                        entry_time_utc=times[entry_index],
                        exit_time_utc=times[i],
                        direction=position,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        shares=shares,
                        pnl=pnl,
                        return_pct=pnl / (shares * entry_price) if shares > 0 else 0.0,
                        exit_reason="stop",
                        bars_held=i - entry_index
                    ))
                    equity_curve[i] = equity
                    position = 0
                    shares = 0.0

            if position == 0 and i + 1 < kept_n and (bull_flip or bear_flip):
                long_ok = bull_flip and enable_long
                short_ok = bear_flip and enable_short

                if (long_ok or short_ok) and not is_within_new_trade_window(times[i], restrict_hrs, start_cst, end_cst):
                    long_ok = False
                    short_ok = False

                if long_ok or short_ok:
                    new_dir = 1 if long_ok else -1
                    fill_price = opens[i + 1] * (1.0 + slip * new_dir)
                    stop_dist = abs(fill_price - stop[i])
                    if stop_dist > 0 and not math.isnan(stop_dist):
                        risk_dollars = equity * risk_pct
                        sz = min(risk_dollars / stop_dist, (equity * max_lev) / fill_price)
                        if sz > 0:
                            position = new_dir
                            shares = sz
                            entry_price = fill_price
                            stop_price = stop[i]
                            entry_index = i + 1

        result = build_result(times, equity_curve, trades, initial_equity)
        result.indicators.append(IndicatorSeries("ATR dynamic stop", "#F59E0B", "Solid", to_indicator_points(exec_bars[start_index:], stop)))
        if use_chand:
            result.indicators.append(IndicatorSeries("Chandelier long stop", "#10B981", "Dotted", to_indicator_points(exec_bars[start_index:], chand_long)))
            result.indicators.append(IndicatorSeries("Chandelier short stop", "#F43F5E", "Dotted", to_indicator_points(exec_bars[start_index:], chand_short)))
        return result

# 4. MA Crossover Strategy (ported)
class MaCrossoverStrategy(BaseStrategy):
    name = "MA Crossover (ported)"
    description = (
        "Ported from DataCollectorAndTrading. Buy when short MA crosses above long MA, sell on opposite cross. "
        "Fixed % stop/take-profit. For the EMA 20/50 4h trend filter (IntradayStrategyResearch): "
        "UseEma on, 20/50, AllowShort off, StopLossPct 0, TakeProfitPct 0, MaxLeverage 1."
    )
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("ShortMaPeriod", "Short MA period", "int", 9),
        StrategyParameterDefinition("LongMaPeriod", "Long MA period", "int", 21),
        StrategyParameterDefinition("UseEma", "Use EMA instead of SMA", "bool", False),
        StrategyParameterDefinition("TradeExistingTrends", "Trade existing trend (not just fresh cross)", "bool", False),
    ] + CommonStopParams.get_definitions()

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        short_p = int(parameters.get("ShortMaPeriod", 9))
        long_p = int(parameters.get("LongMaPeriod", 21))
        trade_existing = bool(parameters.get("TradeExistingTrends", False))
        use_ema = bool(parameters.get("UseEma", False))
        common = CommonStopParams.from_dict(parameters)

        closes = [float(b.close) for b in exec_bars]
        ma = ind.ema if use_ema else ind.sma
        short_sma = ma(closes, short_p)
        long_sma = ma(closes, long_p)
        min_bar = max(short_p, long_p)
        awaiting_fresh = [False]

        def signal(i):
            if i < 1 or math.isnan(short_sma[i]) or math.isnan(long_sma[i]):
                return False, False
            fresh_bull = (short_sma[i - 1] <= long_sma[i - 1] and short_sma[i] > long_sma[i])
            fresh_bear = (short_sma[i - 1] >= long_sma[i - 1] and short_sma[i] < long_sma[i])
            buy, sell = fresh_bull, fresh_bear
            if fresh_bull or fresh_bear:
                awaiting_fresh[0] = False
            if trade_existing and not awaiting_fresh[0]:
                if short_sma[i] > long_sma[i]:
                    buy = True
                if short_sma[i] < long_sma[i]:
                    sell = True
            return buy, sell

        res = run_percent_stop_backtest(exec_bars, min_bar, signal, common, initial_equity, lambda: awaiting_fresh.__setitem__(0, True))
        ma_label = "EMA" if use_ema else "SMA"
        res.indicators.append(IndicatorSeries(f"Short {ma_label}", "#38BDF8", "Solid", to_indicator_points(exec_bars, short_sma)))
        res.indicators.append(IndicatorSeries(f"Long {ma_label}", "#F59E0B", "Solid", to_indicator_points(exec_bars, long_sma)))
        return res

# 5. Hull MA Strategy (ported)
class HullMaStrategy(BaseStrategy):
    name = "Hull MA Turn (ported)"
    description = "Ported from DataCollectorAndTrading. Buy when Hull MA turns up, sell when it turns down."
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("HmaPeriod", "Hull MA period", "int", 9),
    ] + CommonStopParams.get_definitions()

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        p = int(parameters.get("HmaPeriod", 9))
        common = CommonStopParams.from_dict(parameters)
        closes = [float(b.close) for b in exec_bars]
        hma_vals = ind.hma(closes, p)
        min_bar = p + 5

        def signal(i):
            if i < 2 or math.isnan(hma_vals[i]) or math.isnan(hma_vals[i - 1]) or math.isnan(hma_vals[i - 2]):
                return False, False
            curr, prev, prev_prev = hma_vals[i], hma_vals[i - 1], hma_vals[i - 2]
            return (prev_prev >= prev and prev < curr, prev_prev <= prev and prev > curr)

        res = run_percent_stop_backtest(exec_bars, min_bar, signal, common, initial_equity)
        res.indicators.append(IndicatorSeries("Hull MA", "#38BDF8", "Solid", to_indicator_points(exec_bars, hma_vals)))
        return res

# 6. TMO Strategy (ported)
class TmoStrategy(BaseStrategy):
    name = "TMO - True Momentum (ported)"
    description = "Ported from DataCollectorAndTrading. Buy when TMO main crosses signal, sell on opposite cross."
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("TmoLength", "TMO length", "int", 14),
        StrategyParameterDefinition("TmoCalcLength", "Calc length", "int", 5),
        StrategyParameterDefinition("TmoSmoothLength", "Smooth length", "int", 3),
    ] + CommonStopParams.get_definitions()

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        l = int(parameters.get("TmoLength", 14))
        cl = int(parameters.get("TmoCalcLength", 5))
        sl = int(parameters.get("TmoSmoothLength", 3))
        common = CommonStopParams.from_dict(parameters)

        main, sig = ind.tmo(exec_bars, l, cl, sl)
        min_bar = l + cl + sl

        def signal(i):
            if i < 1 or math.isnan(main[i]) or math.isnan(sig[i]):
                return False, False
            buy = (main[i - 1] <= sig[i - 1] and main[i] > sig[i])
            sell = (main[i - 1] >= sig[i - 1] and main[i] < sig[i])
            return buy, sell

        return run_percent_stop_backtest(exec_bars, min_bar, signal, common, initial_equity)

# 7. Trend Flow Strategy (ported)
class TrendFlowStrategy(BaseStrategy):
    name = "Trend Flow (ported)"
    description = "Ported from DataCollectorAndTrading. Fast EMA / slow EMA trend flow crossover."
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("TrendFlowFast", "Fast period", "int", 8),
        StrategyParameterDefinition("TrendFlowSlow", "Slow period", "int", 21),
    ] + CommonStopParams.get_definitions()

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        f_p = int(parameters.get("TrendFlowFast", 8))
        s_p = int(parameters.get("TrendFlowSlow", 21))
        common = CommonStopParams.from_dict(parameters)
        fast, slow = ind.trend_flow(exec_bars, f_p, s_p)
        min_bar = s_p + 1

        def signal(i):
            if i < 1 or math.isnan(fast[i]) or math.isnan(slow[i]):
                return False, False
            return (fast[i - 1] <= slow[i - 1] and fast[i] > slow[i], fast[i - 1] >= slow[i - 1] and fast[i] < slow[i])

        res = run_percent_stop_backtest(exec_bars, min_bar, signal, common, initial_equity)
        res.indicators.append(IndicatorSeries("Trend Flow Fast", "#10B981", "Solid", to_indicator_points(exec_bars, fast)))
        res.indicators.append(IndicatorSeries("Trend Flow Slow", "#F43F5E", "Solid", to_indicator_points(exec_bars, slow)))
        return res

# 8. EMA Bands Strategy (ported)
class EmaBandsStrategy(BaseStrategy):
    name = "EMA Bands (ported)"
    description = "Ported from DataCollectorAndTrading. Buy when close crosses above EMA band high, sell on low."
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("EmaBandPeriod", "EMA period", "int", 34),
    ] + CommonStopParams.get_definitions()

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        p = int(parameters.get("EmaBandPeriod", 34))
        common = CommonStopParams.from_dict(parameters)
        highs = [float(b.high) for b in exec_bars]
        lows = [float(b.low) for b in exec_bars]
        closes = [float(b.close) for b in exec_bars]
        ema_h = ind.ema(highs, p)
        ema_l = ind.ema(lows, p)
        min_bar = p + 1

        def signal(i):
            if i < 1 or math.isnan(ema_h[i]) or math.isnan(ema_l[i]):
                return False, False
            return (closes[i - 1] <= ema_h[i - 1] and closes[i] > ema_h[i], closes[i - 1] >= ema_l[i - 1] and closes[i] < ema_l[i])

        res = run_percent_stop_backtest(exec_bars, min_bar, signal, common, initial_equity)
        res.indicators.append(IndicatorSeries("EMA Band High", "#38BDF8", "Solid", to_indicator_points(exec_bars, ema_h)))
        res.indicators.append(IndicatorSeries("EMA Band Low", "#F59E0B", "Solid", to_indicator_points(exec_bars, ema_l)))
        return res

# 9. Anchored VWAP Strategy (ported)
class AnchoredVwapStrategy(BaseStrategy):
    name = "Anchored VWAP (ported)"
    description = "Ported from DataCollectorAndTrading. Breakout above high-anchored VWAP or below low-anchored VWAP."
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("AvwapLookback", "Anchor search lookback (bars)", "int", 100),
    ] + CommonStopParams.get_definitions()

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        lookback = int(parameters.get("AvwapLookback", 100))
        common = CommonStopParams.from_dict(parameters)
        closes = [float(b.close) for b in exec_bars]
        vwap_arr = ind.anchored_vwap(exec_bars)
        min_bar = max(lookback, 1)

        def signal(i):
            if i < 1 or math.isnan(vwap_arr[i]):
                return False, False
            return (closes[i - 1] <= vwap_arr[i - 1] and closes[i] > vwap_arr[i], closes[i - 1] >= vwap_arr[i - 1] and closes[i] < vwap_arr[i])

        res = run_percent_stop_backtest(exec_bars, min_bar, signal, common, initial_equity)
        res.indicators.append(IndicatorSeries("Anchored VWAP", "#38BDF8", "Solid", to_indicator_points(exec_bars, vwap_arr)))
        return res

# 10. VWAP + EMA Strategy (ported)
class VwapEmaStrategy(BaseStrategy):
    name = "VWAP + EMA (ported)"
    description = "Ported from DataCollectorAndTrading. Price above both session VWAP and EMA."
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("VwapEmaPeriod", "EMA period", "int", 9),
        StrategyParameterDefinition("TradeExistingTrends", "Trade existing trend", "bool", False),
    ] + CommonStopParams.get_definitions()

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        p = int(parameters.get("VwapEmaPeriod", 9))
        trade_existing = bool(parameters.get("TradeExistingTrends", False))
        common = CommonStopParams.from_dict(parameters)
        closes = [float(b.close) for b in exec_bars]
        ema_vals = ind.ema(closes, p)
        vwap_vals = ind.anchored_vwap(exec_bars)
        min_bar = p + 1

        def signal(i):
            if i < 1 or math.isnan(ema_vals[i]) or math.isnan(vwap_vals[i]):
                return False, False
            is_above = closes[i] > vwap_vals[i] and closes[i] > ema_vals[i]
            is_below = closes[i] < vwap_vals[i] and closes[i] < ema_vals[i]
            if trade_existing:
                return is_above, is_below
            was_above = closes[i - 1] > vwap_vals[i - 1] and closes[i - 1] > ema_vals[i - 1]
            was_below = closes[i - 1] < vwap_vals[i - 1] and closes[i - 1] < ema_vals[i - 1]
            return (is_above and not was_above, is_below and not was_below)

        res = run_percent_stop_backtest(exec_bars, min_bar, signal, common, initial_equity)
        res.indicators.append(IndicatorSeries("EMA", "#38BDF8", "Solid", to_indicator_points(exec_bars, ema_vals)))
        res.indicators.append(IndicatorSeries("VWAP", "#A78BFA", "Solid", to_indicator_points(exec_bars, vwap_vals)))
        return res

# 11. VWAP Pullback Strategy (ported)
class VwapPullbackStrategy(BaseStrategy):
    name = "VWAP Pullback (ported)"
    description = "Ported from DataCollectorAndTrading. Bounce / rejection off VWAP in direction of trend."
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("TrendMaPeriod", "Trend filter SMA period", "int", 50),
    ] + CommonStopParams.get_definitions()

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        p = int(parameters.get("TrendMaPeriod", 50))
        common = CommonStopParams.from_dict(parameters)
        closes = [float(b.close) for b in exec_bars]
        lows = [float(b.low) for b in exec_bars]
        highs = [float(b.high) for b in exec_bars]
        trend_ma = ind.sma(closes, p)
        vwap_vals = ind.anchored_vwap(exec_bars)
        min_bar = p + 1

        def signal(i):
            if i < 1 or math.isnan(trend_ma[i]) or math.isnan(vwap_vals[i]):
                return False, False
            bull = closes[i] > trend_ma[i] and lows[i] <= vwap_vals[i] and closes[i] > vwap_vals[i]
            bear = closes[i] < trend_ma[i] and highs[i] >= vwap_vals[i] and closes[i] < vwap_vals[i]
            return bull, bear

        res = run_percent_stop_backtest(exec_bars, min_bar, signal, common, initial_equity)
        res.indicators.append(IndicatorSeries("Trend SMA", "#F59E0B", "Solid", to_indicator_points(exec_bars, trend_ma)))
        res.indicators.append(IndicatorSeries("VWAP", "#38BDF8", "Solid", to_indicator_points(exec_bars, vwap_vals)))
        return res

# 12. Keltner RSI Mean Reversion Strategy (ported)
class KeltnerRsiMeanReversionStrategy(BaseStrategy):
    name = "Keltner + RSI Mean Reversion (ported)"
    description = "Ported from DataCollectorAndTrading. Mean reversion when price touches Keltner bands and RSI is extreme."
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("RsiPeriod", "RSI period", "int", 14),
        StrategyParameterDefinition("RsiOversold", "RSI oversold", "double", 30.0),
        StrategyParameterDefinition("RsiOverbought", "RSI overbought", "double", 70.0),
    ] + CommonStopParams.get_definitions()

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        r_p = int(parameters.get("RsiPeriod", 14))
        os_level = float(parameters.get("RsiOversold", 30.0))
        ob_level = float(parameters.get("RsiOverbought", 70.0))
        common = CommonStopParams.from_dict(parameters)
        closes = [float(b.close) for b in exec_bars]
        lows = [float(b.low) for b in exec_bars]
        highs = [float(b.high) for b in exec_bars]
        ku, km, kl = ind.keltner_channels(exec_bars, 20, 10, 1.5)
        rsi_vals = ind.rsi(closes, r_p)
        min_bar = max(r_p, 20) + 1

        def signal(i):
            if i < 1 or math.isnan(rsi_vals[i]) or math.isnan(kl[i]):
                return False, False
            buy = lows[i] <= kl[i] and rsi_vals[i] < os_level
            sell = highs[i] >= ku[i] and rsi_vals[i] > ob_level
            return buy, sell

        res = run_percent_stop_backtest(exec_bars, min_bar, signal, common, initial_equity)
        res.indicators.append(IndicatorSeries("Keltner Upper", "#38BDF8", "Solid", to_indicator_points(exec_bars, ku)))
        res.indicators.append(IndicatorSeries("Keltner Lower", "#38BDF8", "Solid", to_indicator_points(exec_bars, kl)))
        return res

# 13. RSI Extremes Strategy (ported)
class RSIExtremesStrategy(BaseStrategy):
    name = "RSI Extremes (ported)"
    description = "Ported from DataCollectorAndTrading. Reversal upon exiting RSI oversold/overbought zone."
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("RsiPeriod", "RSI period", "int", 14),
        StrategyParameterDefinition("RsiOversold", "RSI oversold threshold", "double", 30.0),
        StrategyParameterDefinition("RsiOverbought", "RSI overbought threshold", "double", 70.0),
    ] + CommonStopParams.get_definitions()

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        p = int(parameters.get("RsiPeriod", 14))
        os_level = float(parameters.get("RsiOversold", 30.0))
        ob_level = float(parameters.get("RsiOverbought", 70.0))
        common = CommonStopParams.from_dict(parameters)
        closes = [float(b.close) for b in exec_bars]
        rsi_vals = ind.rsi(closes, p)
        min_bar = p + 2

        def signal(i):
            if i < 1 or math.isnan(rsi_vals[i]) or math.isnan(rsi_vals[i - 1]):
                return False, False
            buy = (rsi_vals[i - 1] <= os_level and rsi_vals[i] > os_level)
            sell = (rsi_vals[i - 1] >= ob_level and rsi_vals[i] < ob_level)
            return buy, sell

        return run_percent_stop_backtest(exec_bars, min_bar, signal, common, initial_equity)

# 14. Bollinger Breakout Strategy (ported)
class BollingerBreakoutStrategy(BaseStrategy):
    name = "Bollinger Breakout (ported)"
    description = "Ported from DataCollectorAndTrading. Price breakout outside Bollinger Bands."
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("BbPeriod", "Bollinger period", "int", 20),
        StrategyParameterDefinition("BbMultiplier", "Band multiplier", "double", 2.0),
    ] + CommonStopParams.get_definitions()

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        p = int(parameters.get("BbPeriod", 20))
        m = float(parameters.get("BbMultiplier", 2.0))
        common = CommonStopParams.from_dict(parameters)
        closes = [float(b.close) for b in exec_bars]
        bu, bm, bl = ind.bollinger_bands(closes, p, m)
        min_bar = p + 1

        def signal(i):
            if i < 1 or math.isnan(bu[i]) or math.isnan(bl[i]):
                return False, False
            return (closes[i - 1] <= bu[i - 1] and closes[i] > bu[i], closes[i - 1] >= bl[i - 1] and closes[i] < bl[i])

        res = run_percent_stop_backtest(exec_bars, min_bar, signal, common, initial_equity)
        res.indicators.append(IndicatorSeries("BB Upper", "#38BDF8", "Solid", to_indicator_points(exec_bars, bu)))
        res.indicators.append(IndicatorSeries("BB Lower", "#38BDF8", "Solid", to_indicator_points(exec_bars, bl)))
        return res

# 15. TTM Squeeze Strategy (ported)
class TtmSqueezeStrategy(BaseStrategy):
    name = "TTM Squeeze (ported)"
    description = "Ported from DataCollectorAndTrading. Fire signal upon exit from volatility squeeze."
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("BbPeriod", "BB period", "int", 20),
        StrategyParameterDefinition("KcPeriod", "KC period", "int", 20),
    ] + CommonStopParams.get_definitions()

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        common = CommonStopParams.from_dict(parameters)
        is_sqz, hist = ind.ttm_squeeze(exec_bars, 20, 2.0, 20, 1.5)
        min_bar = 25

        def signal(i):
            if i < 1:
                return False, False
            was_sqz = is_sqz[i - 1]
            now_sqz = is_sqz[i]
            fired = was_sqz and not now_sqz
            buy = fired and hist[i] > 0
            sell = fired and hist[i] < 0
            return buy, sell

        return run_percent_stop_backtest(exec_bars, min_bar, signal, common, initial_equity)

# 16. Fibonacci EMA Strategy (ported)
class FibonacciEmaStrategy(BaseStrategy):
    name = "Fibonacci EMA (ported)"
    description = "Ported from StockAnalysisAndTrading. Crossover of Fibonacci EMA levels (8 and 21)."
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("FastFib", "Fast Fib EMA", "int", 8),
        StrategyParameterDefinition("SlowFib", "Slow Fib EMA", "int", 21),
    ] + CommonStopParams.get_definitions()

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        f_p = int(parameters.get("FastFib", 8))
        s_p = int(parameters.get("SlowFib", 21))
        common = CommonStopParams.from_dict(parameters)
        closes = [float(b.close) for b in exec_bars]
        fast = ind.ema(closes, f_p)
        slow = ind.ema(closes, s_p)
        min_bar = s_p + 1

        def signal(i):
            if i < 1 or math.isnan(fast[i]) or math.isnan(slow[i]):
                return False, False
            return (fast[i - 1] <= slow[i - 1] and fast[i] > slow[i], fast[i - 1] >= slow[i - 1] and fast[i] < slow[i])

        res = run_percent_stop_backtest(exec_bars, min_bar, signal, common, initial_equity)
        res.indicators.append(IndicatorSeries(f"EMA {f_p}", "#38BDF8", "Solid", to_indicator_points(exec_bars, fast)))
        res.indicators.append(IndicatorSeries(f"EMA {s_p}", "#F59E0B", "Solid", to_indicator_points(exec_bars, slow)))
        return res

# 17. Liquidity Sweep Strategy (ported)
class LiquiditySweepStrategy(BaseStrategy):
    name = "Liquidity Sweep (ported)"
    description = "Ported from StockAnalysisAndTrading. Fakeout beyond swing high/low followed by reversal."
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("SwingLookback", "Swing lookback bars", "int", 15),
    ] + CommonStopParams.get_definitions()

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        lb = int(parameters.get("SwingLookback", 15))
        common = CommonStopParams.from_dict(parameters)
        highs = [float(b.high) for b in exec_bars]
        lows = [float(b.low) for b in exec_bars]
        closes = [float(b.close) for b in exec_bars]
        min_bar = lb + 1

        def signal(i):
            if i < min_bar:
                return False, False
            prior_high = max(highs[i - lb : i])
            prior_low = min(lows[i - lb : i])
            bull = lows[i] < prior_low and closes[i] > prior_low
            bear = highs[i] > prior_high and closes[i] < prior_high
            return bull, bear

        return run_percent_stop_backtest(exec_bars, min_bar, signal, common, initial_equity)

# 18. ICT / SMC Strategy (ported)
class IctSmcStrategy(BaseStrategy):
    name = "ICT / SMC Smart Money (ported)"
    description = "Ported from StockAnalysisAndTrading. Fair Value Gap (FVG) and Market Structure Shift (MSS)."
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("FvgThresholdPct", "Min FVG size (%)", "double", 0.15),
    ] + CommonStopParams.get_definitions()

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        fvg_thresh = float(parameters.get("FvgThresholdPct", 0.15)) / 100.0
        common = CommonStopParams.from_dict(parameters)
        highs = [float(b.high) for b in exec_bars]
        lows = [float(b.low) for b in exec_bars]
        closes = [float(b.close) for b in exec_bars]
        min_bar = 5

        def signal(i):
            if i < 2:
                return False, False
            gap_up = lows[i] - highs[i - 2]
            bull = gap_up > closes[i] * fvg_thresh and closes[i] > closes[i - 1]
            gap_down = lows[i - 2] - highs[i]
            bear = gap_down > closes[i] * fvg_thresh and closes[i] < closes[i - 1]
            return bull, bear

        return run_percent_stop_backtest(exec_bars, min_bar, signal, common, initial_equity)

# 19. MACD Momentum Strategy (ported)
class MacdMomentumStrategy(BaseStrategy):
    name = "MACD Momentum (ported)"
    description = "Ported from StockAnalysisAndTrading. Buy when MACD line is positive and price is rising."
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("MacdFastPeriod", "Fast EMA period", "int", 12),
        StrategyParameterDefinition("MacdSlowPeriod", "Slow EMA period", "int", 26),
    ] + CommonStopParams.get_definitions()

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        fast_p = int(parameters.get("MacdFastPeriod", 12))
        slow_p = int(parameters.get("MacdSlowPeriod", 26))
        common = CommonStopParams.from_dict(parameters)
        closes = [float(b.close) for b in exec_bars]
        macd_line, sig_line, hist = ind.macd(closes, fast_p, slow_p, 9)
        min_bar = slow_p + 1

        def signal(i):
            if i < 1 or math.isnan(macd_line[i]):
                return False, False
            buy = macd_line[i] > 0 and closes[i] > closes[i - 1]
            sell = macd_line[i] < 0 and closes[i] < closes[i - 1]
            return buy, sell

        return run_percent_stop_backtest(exec_bars, min_bar, signal, common, initial_equity)

# Registry of all strategies
class StrategyRegistry:
    ALL: List[BaseStrategy] = [
        SupertrendMultiTimeframeStrategy(),
        DonchianBreakoutStrategy(),
        AtrDynamicStopReversalStrategy(),
        MaCrossoverStrategy(),
        HullMaStrategy(),
        TmoStrategy(),
        TrendFlowStrategy(),
        EmaBandsStrategy(),
        AnchoredVwapStrategy(),
        VwapEmaStrategy(),
        VwapPullbackStrategy(),
        KeltnerRsiMeanReversionStrategy(),
        RSIExtremesStrategy(),
        BollingerBreakoutStrategy(),
        TtmSqueezeStrategy(),
        FibonacciEmaStrategy(),
        LiquiditySweepStrategy(),
        IctSmcStrategy(),
        MacdMomentumStrategy(),
    ]

    @classmethod
    def get_by_name(cls, name: str) -> Optional[BaseStrategy]:
        for s in cls.ALL:
            if s.name.lower() == name.lower():
                return s
        return None

StrategyRegistry.get_all_names = staticmethod(lambda: [s.name for s in StrategyRegistry.ALL])
STRATEGY_CATALOG = {s.name: s for s in StrategyRegistry.ALL}



def run_strategy_backtest(name: str, exec_bars, parameters: dict, anchor_bars=None, initial_equity: float = 100000.0):
    strat = StrategyRegistry.get_by_name(name)
    if strat is None:
        raise ValueError(f'Unknown strategy: {name}')
    if anchor_bars is None:
        anchor_bars = exec_bars
    return strat.run(exec_bars, anchor_bars, parameters, initial_equity)
