"""All 21 backtestable strategies matching StrategyRegistry."""
import math
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Tuple, Optional
from strategy.models import PriceBar, BacktestResult, StrategyParameterDefinition, IndicatorSeries, Trade
from strategy.math_utils import build_result, empty_result, is_within_new_trade_window, shift_by_one, to_indicator_points, stop_fill, limit_fill
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
# 20. Institutional MA Trend Filter (IMTF) Strategy
class InstitutionalMaTrendFilterStrategy(BaseStrategy):
    name = "Institutional MA Trend Filter (IMTF)"
    description = (
        "Institutional MA Trend Filter strategy utilizing 20 EMA slope, ATR expansion, "
        "volume confirmation, 20-bar breakout, and accumulation filtering with 1:2 R:R (1.5 ATR SL, 3.0 ATR TP)."
    )
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("EmaLength", "EMA Length", "int", 20),
        StrategyParameterDefinition("SlopeLookback", "Slope Lookback (bars)", "int", 5),
        StrategyParameterDefinition("FlatSlopeThreshold", "Flat Slope Threshold (%)", "double", 0.03),
        StrategyParameterDefinition("AtrLength", "ATR Length", "int", 14),
        StrategyParameterDefinition("AtrAvgLength", "ATR Average Length", "int", 20),
        StrategyParameterDefinition("AtrExpansionMultiplier", "ATR Expansion Multiplier", "double", 1.0),
        StrategyParameterDefinition("VolumeAvgLength", "Volume Average Length", "int", 20),
        StrategyParameterDefinition("VolumeMultiplier", "Volume Multiplier", "double", 1.0),
        StrategyParameterDefinition("BreakoutLookback", "Breakout Lookback (bars)", "int", 20),
        StrategyParameterDefinition("MaCrossLookback", "Accumulation MA Cross Lookback", "int", 10),
        StrategyParameterDefinition("CrossThreshold", "Accumulation Cross Threshold", "int", 2),
        StrategyParameterDefinition("NarrowRangeLookback", "Narrow Range Lookback (bars)", "int", 5),
        StrategyParameterDefinition("NarrowRangeThreshold", "Narrow Range Threshold (%)", "double", 0.5),
        StrategyParameterDefinition("StopAtrMultiplier", "Stop Loss ATR Multiplier", "double", 1.5),
        StrategyParameterDefinition("TakeProfitAtrMultiplier", "Take Profit ATR Multiplier", "double", 3.0),
        StrategyParameterDefinition("AllowShort", "Allow short entries", "bool", True),
        StrategyParameterDefinition("RiskPct", "Risk per trade (% of equity)", "double", 2.0),
        StrategyParameterDefinition("MaxLeverage", "Max leverage (x notional)", "double", 4.0),
        StrategyParameterDefinition("SlippageBps", "Slippage (bps per side)", "double", 1.0),
        StrategyParameterDefinition("RestrictNewTradesToTradingHours", "Only take NEW trades during trading-hours window", "bool", False),
        StrategyParameterDefinition("NewTradesStartTimeCst", "New trades start (HH:mm CST)", "string", "09:00"),
        StrategyParameterDefinition("NewTradesEndTimeCst", "New trades end (HH:mm CST)", "string", "14:30"),
    ]

    def run(self, exec_bars, anchor_bars, parameters, initial_equity):
        n = len(exec_bars)
        if n < 30:
            return empty_result()

        ema_len = int(parameters.get("EmaLength", 20))
        slope_lb = int(parameters.get("SlopeLookback", 5))
        flat_slope_thresh = float(parameters.get("FlatSlopeThreshold", 0.03))
        atr_len = int(parameters.get("AtrLength", 14))
        atr_avg_len = int(parameters.get("AtrAvgLength", 20))
        atr_exp_mult = float(parameters.get("AtrExpansionMultiplier", 1.0))
        vol_avg_len = int(parameters.get("VolumeAvgLength", 20))
        vol_mult = float(parameters.get("VolumeMultiplier", 1.0))
        breakout_lb = int(parameters.get("BreakoutLookback", 20))
        ma_cross_lb = int(parameters.get("MaCrossLookback", 10))
        cross_thresh = int(parameters.get("CrossThreshold", 2))
        narrow_range_lb = int(parameters.get("NarrowRangeLookback", 5))
        narrow_range_thresh = float(parameters.get("NarrowRangeThreshold", 0.5))
        stop_atr_m = float(parameters.get("StopAtrMultiplier", 1.5))
        tp_atr_m = float(parameters.get("TakeProfitAtrMultiplier", 3.0))
        allow_short = bool(parameters.get("AllowShort", True))
        risk_pct = float(parameters.get("RiskPct", 2.0)) / 100.0
        max_lev = float(parameters.get("MaxLeverage", 4.0))
        slip = float(parameters.get("SlippageBps", 1.0)) / 10000.0
        restrict_hrs = bool(parameters.get("RestrictNewTradesToTradingHours", False))
        start_cst = str(parameters.get("NewTradesStartTimeCst", "09:00"))
        end_cst = str(parameters.get("NewTradesEndTimeCst", "14:30"))

        closes = [float(b.close) for b in exec_bars]
        opens = [float(b.open) for b in exec_bars]
        highs = [float(b.high) for b in exec_bars]
        lows = [float(b.low) for b in exec_bars]
        volumes = [float(b.volume) for b in exec_bars]
        times = [b.time_utc for b in exec_bars]

        ma = ind.ema(closes, ema_len)
        atr_vals = ind.atr(exec_bars, atr_len)
        atr_avg = ind.sma(atr_vals, atr_avg_len)
        vol_avg = ind.sma(volumes, vol_avg_len)

        # Calculate breakout levels across all bars for chart visualization
        recent_highs = [float('nan')] * n
        recent_lows = [float('nan')] * n
        for i in range(breakout_lb, n):
            recent_highs[i] = max(highs[i - breakout_lb : i])
            recent_lows[i] = min(lows[i - breakout_lb : i])

        min_req_bar = max(
            ema_len + slope_lb + 2,
            atr_len + atr_avg_len + 2,
            vol_avg_len + 2,
            breakout_lb + 2,
            ma_cross_lb + 2,
            narrow_range_lb + 2
        )

        start_index = -1
        for i in range(min_req_bar, n):
            if not math.isnan(ma[i]) and not math.isnan(atr_avg[i]) and not math.isnan(vol_avg[i]):
                start_index = i
                break

        if start_index < 0 or start_index >= n - 1:
            return empty_result()

        equity = initial_equity
        equity_curve = [equity] * n
        position = 0
        shares = 0.0
        entry_price = 0.0
        stop_price = 0.0
        target_price = 0.0
        entry_index = -1
        trades = []

        for i in range(start_index, n):
            equity_curve[i] = equity + shares * (closes[i] - entry_price) * position if position != 0 else equity

            # Check exits if in position
            if position != 0:
                hit_stop = False
                hit_tp = False
                if position == 1:
                    if opens[i] <= stop_price:
                        hit_stop = True
                    elif opens[i] >= target_price:
                        hit_tp = True
                    elif lows[i] <= stop_price:
                        hit_stop = True
                    elif highs[i] >= target_price:
                        hit_tp = True
                else:  # position == -1
                    if opens[i] >= stop_price:
                        hit_stop = True
                    elif opens[i] <= target_price:
                        hit_tp = True
                    elif highs[i] >= stop_price:
                        hit_stop = True
                    elif lows[i] <= target_price:
                        hit_tp = True

                exit_reason = None
                exit_price = 0.0
                if hit_stop:
                    exit_reason = "stop"
                    exit_price = stop_fill(stop_price, opens[i], position) * (1.0 - slip * position)
                elif hit_tp:
                    exit_reason = "take_profit"
                    exit_price = limit_fill(target_price, opens[i], position) * (1.0 - slip * position)

                if exit_reason is not None:
                    pnl = shares * (exit_price - entry_price) * position
                    equity += pnl
                    ret_pct = pnl / (shares * entry_price) if (shares * entry_price) > 0 else 0.0
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

            # Check entries if flat
            if position == 0 and i + 1 < n:
                # 1. Slope
                ma_prev = ma[i - slope_lb]
                ma_slope = ((ma[i] - ma_prev) / ma_prev) * 100.0 if (ma_prev and ma_prev > 0) else 0.0
                is_slope_up = (ma_slope >= flat_slope_thresh)
                is_slope_down = (ma_slope <= -flat_slope_thresh)
                is_slope_flat = (not is_slope_up and not is_slope_down)

                # 2. ATR Expansion
                atr_exp = (atr_vals[i] > (atr_avg[i] * atr_exp_mult)) if (not math.isnan(atr_vals[i]) and not math.isnan(atr_avg[i])) else False

                # 3. Volume Confirmation
                vol_conf = (volumes[i] > (vol_avg[i] * vol_mult)) if not math.isnan(vol_avg[i]) else False

                # 4. Breakout
                rec_h = recent_highs[i]
                rec_l = recent_lows[i]
                breakout_up = (closes[i] > rec_h) if not math.isnan(rec_h) else False
                breakout_down = (closes[i] < rec_l) if not math.isnan(rec_l) else False

                # 5. Trend Confirmation (2 consecutive bars)
                bull_conf = (closes[i] > ma[i] and closes[i - 1] > ma[i - 1])
                bear_conf = (closes[i] < ma[i] and closes[i - 1] < ma[i - 1])

                # 6. Accumulation Detection
                crosses = 0
                for k in range(i - ma_cross_lb + 1, i + 1):
                    if (closes[k] - ma[k]) * (closes[k - 1] - ma[k - 1]) <= 0:
                        crosses += 1
                freq_crosses = (crosses >= cross_thresh)

                nr_high = max(highs[i - narrow_range_lb + 1 : i + 1])
                nr_low = min(lows[i - narrow_range_lb + 1 : i + 1])
                narrow_range = (((nr_high - nr_low) / nr_low) * 100.0 < narrow_range_thresh) if nr_low > 0 else False

                is_accumulation = is_slope_flat and (freq_crosses or narrow_range or (not atr_exp))

                # 7. Bullish / Bearish Market State
                bullish_state = is_slope_up and (closes[i] > ma[i]) and bull_conf and (not is_accumulation)
                bearish_state = is_slope_down and (closes[i] < ma[i]) and bear_conf and (not is_accumulation)

                # 8. Buy / Sell Signals
                buy_signal = bullish_state and breakout_up and vol_conf and atr_exp and (not is_accumulation)
                sell_signal = allow_short and bearish_state and breakout_down and vol_conf and atr_exp and (not is_accumulation)

                if (buy_signal or sell_signal) and not is_within_new_trade_window(times[i], restrict_hrs, start_cst, end_cst):
                    buy_signal = False
                    sell_signal = False

                if buy_signal:
                    direction = 1
                    fill_price = opens[i + 1] * (1.0 + slip * direction)
                    stop_dist = atr_vals[i] * stop_atr_m
                    tp_dist = atr_vals[i] * tp_atr_m
                    if stop_dist > 0 and not math.isnan(stop_dist):
                        risk_dollars = equity * risk_pct
                        sz = min(risk_dollars / stop_dist, (equity * max_lev) / fill_price)
                        if sz > 0:
                            position = direction
                            shares = sz
                            entry_price = fill_price
                            stop_price = fill_price - stop_dist
                            target_price = fill_price + tp_dist
                            entry_index = i + 1
                elif sell_signal:
                    direction = -1
                    fill_price = opens[i + 1] * (1.0 + slip * direction)
                    stop_dist = atr_vals[i] * stop_atr_m
                    tp_dist = atr_vals[i] * tp_atr_m
                    if stop_dist > 0 and not math.isnan(stop_dist):
                        risk_dollars = equity * risk_pct
                        sz = min(risk_dollars / stop_dist, (equity * max_lev) / fill_price)
                        if sz > 0:
                            position = direction
                            shares = sz
                            entry_price = fill_price
                            stop_price = fill_price + stop_dist
                            target_price = fill_price - tp_dist
                            entry_index = i + 1

        # Keep output range matching start_index
        kept_times = times[start_index:]
        kept_equity = equity_curve[start_index:]
        kept_bars = exec_bars[start_index:]

        result = build_result(kept_times, kept_equity, trades, initial_equity)
        result.indicators.append(IndicatorSeries(f"{ema_len} EMA", "#38BDF8", "Solid", to_indicator_points(kept_bars, ma[start_index:])))
        result.indicators.append(IndicatorSeries(f"{breakout_lb}-bar High", "#10B981", "Dotted", to_indicator_points(kept_bars, recent_highs[start_index:])))
        result.indicators.append(IndicatorSeries(f"{breakout_lb}-bar Low", "#F43F5E", "Dotted", to_indicator_points(kept_bars, recent_lows[start_index:])))
        return result

# 21. SPY Micro Trade Engine (IMTF + MTF)
class SpyMicroTradeEngineStrategy(BaseStrategy):
    name = "SPY Micro Trade Engine (IMTF + MTF)"
    description = (
        "1-minute SPY micro trade engine combining multi-timeframe bias (15m/5m), "
        "IMTF accumulation/chop filtering, liquidity sweep reversals & breakout/pullback triggers, "
        "8-point confluence scoring, and dynamic TP1 (50% scale-out) + trailing runner risk management."
    )
    requires_anchor_timeframe = False

    parameter_definitions = [
        # Core Indicators
        StrategyParameterDefinition("FastEmaPeriod", "EMA Fast period", "int", 9),
        StrategyParameterDefinition("SlowEmaPeriod", "EMA Slow period", "int", 20),
        StrategyParameterDefinition("RsiPeriod", "RSI period", "int", 14),
        StrategyParameterDefinition("MacdFast", "MACD Fast period", "int", 12),
        StrategyParameterDefinition("MacdSlow", "MACD Slow period", "int", 26),
        StrategyParameterDefinition("MacdSignal", "MACD Signal period", "int", 9),
        StrategyParameterDefinition("AtrPeriod", "ATR period", "int", 14),

        # MTF Engine
        StrategyParameterDefinition("RequireBias", "Require 15m bias alignment", "bool", True),
        StrategyParameterDefinition("RequireConfirmation", "Require 5m confirmation alignment", "bool", True),

        # Setup Detectors
        StrategyParameterDefinition("UseBreakout", "Use Breakout detector", "bool", True),
        StrategyParameterDefinition("UsePullback", "Use Pullback detector", "bool", True),
        StrategyParameterDefinition("UseReversal", "Use Reversal (sweep) detector", "bool", True),
        StrategyParameterDefinition("TradeDirection", "Trade Direction (Both/Calls Only/Puts Only)", "string", "Both"),
        StrategyParameterDefinition("BreakoutLookback", "Range lookback for breakout (bars)", "int", 15),
        StrategyParameterDefinition("MinRelVol", "Breakout min relative volume", "double", 1.2),
        StrategyParameterDefinition("MinBody", "Min candle body / range", "double", 0.5),
        StrategyParameterDefinition("MinCloseLoc", "Min close location (0=low, 1=high)", "double", 0.7),
        StrategyParameterDefinition("PullbackToleranceAtr", "Pullback tolerance (x ATR)", "double", 0.25),
        StrategyParameterDefinition("PullbackWindow", "Breakout retest window (bars)", "int", 15),
        StrategyParameterDefinition("MinWickRatio", "Reversal min rejection wick / range", "double", 0.5),
        StrategyParameterDefinition("MinRelVolRev", "Reversal min relative volume", "double", 1.0),

        # IMTF Chop Filter
        StrategyParameterDefinition("SlopeLookback", "MA slope lookback", "int", 5),
        StrategyParameterDefinition("FlatSlopeThreshold", "Flat slope threshold (%)", "double", 0.015),
        StrategyParameterDefinition("ConfirmBars", "Bars above/below EMA for phase", "int", 2),
        StrategyParameterDefinition("CrossLookback", "EMA cross lookback", "int", 10),
        StrategyParameterDefinition("CrossThreshold", "Cross count threshold", "int", 2),
        StrategyParameterDefinition("NarrowLookback", "Narrow range lookback", "int", 5),
        StrategyParameterDefinition("NarrowThreshold", "Narrow range threshold (%)", "double", 0.02),
        StrategyParameterDefinition("VolAvgLen", "Volume average length", "int", 20),
        StrategyParameterDefinition("AtrAvgLen", "ATR average length", "int", 20),
        StrategyParameterDefinition("AtrExpMult", "ATR expansion multiplier", "double", 1.0),
        StrategyParameterDefinition("MinAtrPct", "Min ATR % of price", "double", 0.02),

        # Quality Scoring
        StrategyParameterDefinition("MinScoreCont", "Min score - breakout/pullback (of 8)", "int", 6),
        StrategyParameterDefinition("MinScoreRev", "Min score - reversal (of 6)", "int", 5),
        StrategyParameterDefinition("MinRoomR", "Min room to next S/R (x stop distance)", "double", 1.0),

        # Trade & Risk Management
        StrategyParameterDefinition("StopLossAtr", "Stop loss (x ATR)", "double", 0.8),
        StrategyParameterDefinition("MaxReversalStopAtr", "Max reversal stop (x ATR)", "double", 2.0),
        StrategyParameterDefinition("Tp1R", "TP1 (R multiple)", "double", 1.0),
        StrategyParameterDefinition("Tp1Percent", "Take this % off at TP1", "double", 50.0),
        StrategyParameterDefinition("Tp2R", "Runner target TP2 (R multiple)", "double", 2.5),
        StrategyParameterDefinition("TrailAtr", "Runner trail distance (x ATR)", "double", 0.8),
        StrategyParameterDefinition("TimeExitBars", "Time exit (bars) if TP1 not reached", "int", 15),
        StrategyParameterDefinition("RunnerMaxBars", "Max bars for runner", "int", 45),
        StrategyParameterDefinition("RiskPct", "Risk per trade (% of equity)", "double", 1.0),
        StrategyParameterDefinition("MaxLeverage", "Max leverage (x notional)", "double", 4.0),
        StrategyParameterDefinition("SlippageBps", "Slippage (bps per side)", "double", 1.0),
        StrategyParameterDefinition("MaxTradesPerDay", "Max trades per day", "int", 6),
        StrategyParameterDefinition("MaxDailyLossPct", "Max daily loss (% of equity)", "double", 2.0),
        StrategyParameterDefinition("MaxConsecLoss", "Max consecutive losses per day", "int", 3),
        StrategyParameterDefinition("CooldownBars", "Cooldown bars after exit", "int", 2),
        StrategyParameterDefinition("ExitOnOppositeSignal", "Close on opposite signal", "bool", True),
        StrategyParameterDefinition("RestrictNewTradesToTradingHours", "Restrict new trades to trading window", "bool", False),
        StrategyParameterDefinition("NewTradesStartTimeCst", "Session start (HH:mm CST)", "string", "08:35"),
        StrategyParameterDefinition("NewTradesEndTimeCst", "Session end (HH:mm CST)", "string", "14:45"),
    ]

    def run(
        self,
        exec_bars: List[PriceBar],
        anchor_bars: List[PriceBar],
        parameters: Dict[str, Any],
        initial_equity: float
    ) -> BacktestResult:
        n = len(exec_bars)
        if n < 30:
            return empty_result()

        # Parse parameters
        fast_len = int(parameters.get("FastEmaPeriod", 9))
        slow_len = int(parameters.get("SlowEmaPeriod", 20))
        rsi_len = int(parameters.get("RsiPeriod", 14))
        macd_fast = int(parameters.get("MacdFast", 12))
        macd_slow = int(parameters.get("MacdSlow", 26))
        macd_sig = int(parameters.get("MacdSignal", 9))
        atr_len = int(parameters.get("AtrPeriod", 14))

        req_bias = bool(parameters.get("RequireBias", True))
        req_conf = bool(parameters.get("RequireConfirmation", True))

        use_breakout = bool(parameters.get("UseBreakout", True))
        use_pullback = bool(parameters.get("UsePullback", True))
        use_reversal = bool(parameters.get("UseReversal", True))
        trade_dir = str(parameters.get("TradeDirection", "Both")).strip().lower()
        allow_call = trade_dir in ("both", "calls only", "calls", "call", "long", "long only")
        allow_put = trade_dir in ("both", "puts only", "puts", "put", "short", "short only")

        break_len = int(parameters.get("BreakoutLookback", 15))
        min_rel_vol = float(parameters.get("MinRelVol", 1.2))
        min_body = float(parameters.get("MinBody", 0.5))
        min_close_loc = float(parameters.get("MinCloseLoc", 0.7))
        pull_tol_mult = float(parameters.get("PullbackToleranceAtr", 0.25))
        pull_window = int(parameters.get("PullbackWindow", 15))
        min_wick = float(parameters.get("MinWickRatio", 0.5))
        min_rel_vol_rev = float(parameters.get("MinRelVolRev", 1.0))

        slope_lb = int(parameters.get("SlopeLookback", 5))
        flat_slope_thresh = float(parameters.get("FlatSlopeThreshold", 0.015))
        confirm_bars = int(parameters.get("ConfirmBars", 2))
        cross_lb = int(parameters.get("CrossLookback", 10))
        cross_thresh = int(parameters.get("CrossThreshold", 2))
        narrow_lb = int(parameters.get("NarrowLookback", 5))
        narrow_thresh = float(parameters.get("NarrowThreshold", 0.02))
        vol_avg_len = int(parameters.get("VolAvgLen", 20))
        atr_avg_len = int(parameters.get("AtrAvgLen", 20))
        atr_exp_mult = float(parameters.get("AtrExpMult", 1.0))
        min_atr_pct = float(parameters.get("MinAtrPct", 0.02))

        min_score_cont = int(parameters.get("MinScoreCont", 6))
        min_score_rev = int(parameters.get("MinScoreRev", 5))
        min_room_r = float(parameters.get("MinRoomR", 1.0))

        stop_atr_m = float(parameters.get("StopLossAtr", 0.8))
        max_stop_atr = float(parameters.get("MaxReversalStopAtr", 2.0))
        tp1_r = float(parameters.get("Tp1R", 1.0))
        tp1_pct = float(parameters.get("Tp1Percent", 50.0))
        tp2_r = float(parameters.get("Tp2R", 2.5))
        trail_atr = float(parameters.get("TrailAtr", 0.8))
        time_exit_bars = int(parameters.get("TimeExitBars", 15))
        runner_max_bars = int(parameters.get("RunnerMaxBars", 45))
        risk_pct = float(parameters.get("RiskPct", 1.0)) / 100.0
        max_lev = float(parameters.get("MaxLeverage", 4.0))
        slip = float(parameters.get("SlippageBps", 1.0)) / 10000.0
        max_trades = int(parameters.get("MaxTradesPerDay", 6))
        max_daily_loss = float(parameters.get("MaxDailyLossPct", 2.0))
        max_consec_loss = int(parameters.get("MaxConsecLoss", 3))
        cooldown_bars = int(parameters.get("CooldownBars", 2))
        exit_on_opp = bool(parameters.get("ExitOnOppositeSignal", True))
        restrict_hrs = bool(parameters.get("RestrictNewTradesToTradingHours", False))
        start_cst = str(parameters.get("NewTradesStartTimeCst", "08:35"))
        end_cst = str(parameters.get("NewTradesEndTimeCst", "14:45"))

        times = [b.time_utc for b in exec_bars]
        opens = [float(b.open) for b in exec_bars]
        highs = [float(b.high) for b in exec_bars]
        lows = [float(b.low) for b in exec_bars]
        closes = [float(b.close) for b in exec_bars]
        volumes = [float(b.volume) for b in exec_bars]

        # Core Indicators
        ema_fast = ind.ema(closes, fast_len)
        ema_slow = ind.ema(closes, slow_len)
        rsi_vals = ind.rsi(closes, rsi_len)
        macd_line, macd_sig_line, macd_hist = ind.macd(closes, macd_fast, macd_slow, macd_sig)
        atr_vals = ind.atr(exec_bars, atr_len)
        atr_avg = ind.sma(atr_vals, atr_avg_len)
        vol_avg = ind.sma(volumes, vol_avg_len)

        # Microstructure Proxies
        rel_vol = [volumes[i] / vol_avg[i] if (not math.isnan(vol_avg[i]) and vol_avg[i] > 0) else 1.0 for i in range(n)]
        rng = [highs[i] - lows[i] for i in range(n)]
        body_ratio = [abs(closes[i] - opens[i]) / rng[i] if rng[i] > 0 else 0.0 for i in range(n)]
        upper_wick = [(highs[i] - max(opens[i], closes[i])) / rng[i] if rng[i] > 0 else 0.0 for i in range(n)]
        lower_wick = [(min(opens[i], closes[i]) - lows[i]) / rng[i] if rng[i] > 0 else 0.0 for i in range(n)]
        close_loc = [(closes[i] - lows[i]) / rng[i] if rng[i] > 0 else 0.5 for i in range(n)]

        # Multi-Timeframe Resampling & Mapping
        def _resample(b_list: List[PriceBar], mins: int) -> List[PriceBar]:
            if not b_list:
                return []
            res = []
            curr_b = None
            b_o = b_h = b_l = b_c = b_v = 0.0
            b_t = None
            for b in b_list:
                t = b.time_utc
                bucket = int(t.timestamp()) // (mins * 60)
                if curr_b is None or bucket != curr_b:
                    if curr_b is not None:
                        res.append(PriceBar(b_t, b_o, b_h, b_l, b_c, b_v))
                    curr_b = bucket
                    b_t = t
                    b_o = float(b.open)
                    b_h = float(b.high)
                    b_l = float(b.low)
                    b_c = float(b.close)
                    b_v = float(b.volume)
                else:
                    b_h = max(b_h, float(b.high))
                    b_l = min(b_l, float(b.low))
                    b_c = float(b.close)
                    b_v += float(b.volume)
            if curr_b is not None:
                res.append(PriceBar(b_t, b_o, b_h, b_l, b_c, b_v))
            return res

        def _map_htf(e_bars: List[PriceBar], h_bars: List[PriceBar], h_vals: List[float]) -> List[float]:
            nn = len(e_bars)
            out = [float('nan')] * nn
            if not h_bars or not h_vals:
                return out
            h_shifted = shift_by_one(h_vals)
            idx = 0
            m_len = len(h_bars)
            for j in range(nn):
                tj = e_bars[j].time_utc
                while idx + 1 < m_len and h_bars[idx + 1].time_utc <= tj:
                    idx += 1
                if h_bars[idx].time_utc <= tj:
                    out[j] = h_shifted[idx]
            return out

        # 15m Bars (Directional Bias)
        b15_bars = _resample(exec_bars, 15)
        b15_closes = [float(b.close) for b in b15_bars]
        b15_e9_raw = ind.ema(b15_closes, fast_len)
        b15_e20_raw = ind.ema(b15_closes, slow_len)
        b15_e20p_raw = shift_by_one(b15_e20_raw)
        b15_e9 = _map_htf(exec_bars, b15_bars, b15_e9_raw)
        b15_e20 = _map_htf(exec_bars, b15_bars, b15_e20_raw)
        b15_e20p = _map_htf(exec_bars, b15_bars, b15_e20p_raw)
        b15_c = _map_htf(exec_bars, b15_bars, b15_closes)

        # 5m Bars (Confirmation)
        c5_bars = _resample(exec_bars, 5)
        c5_closes = [float(b.close) for b in c5_bars]
        c5_e9_raw = ind.ema(c5_closes, fast_len)
        c5_e20_raw = ind.ema(c5_closes, slow_len)
        c5_rsi_raw = ind.rsi(c5_closes, rsi_len)
        _, _, c5_hist_raw = ind.macd(c5_closes, macd_fast, macd_slow, macd_sig)
        c5_e9 = _map_htf(exec_bars, c5_bars, c5_e9_raw)
        c5_e20 = _map_htf(exec_bars, c5_bars, c5_e20_raw)
        c5_rsi = _map_htf(exec_bars, c5_bars, c5_rsi_raw)
        c5_hist = _map_htf(exec_bars, c5_bars, c5_hist_raw)

        # Session VWAP & Liquidity Levels
        vwap = [float('nan')] * n
        pd_high_arr = [float('nan')] * n
        pd_low_arr = [float('nan')] * n
        or_high_arr = [float('nan')] * n
        or_low_arr = [float('nan')] * n
        sess_high_prev = [float('nan')] * n
        sess_low_prev = [float('nan')] * n

        day_high_map = {}
        day_low_map = {}
        daily_order = []
        for b in exec_bars:
            d = b.time_utc.date()
            if d not in day_high_map:
                day_high_map[d] = float(b.high)
                day_low_map[d] = float(b.low)
                daily_order.append(d)
            else:
                day_high_map[d] = max(day_high_map[d], float(b.high))
                day_low_map[d] = min(day_low_map[d], float(b.low))

        prev_day_lookup = {}
        for k in range(1, len(daily_order)):
            prev_d = daily_order[k - 1]
            curr_d = daily_order[k]
            prev_day_lookup[curr_d] = (day_high_map[prev_d], day_low_map[prev_d])

        curr_day = None
        cum_pv = 0.0
        cum_vol = 0.0
        sess_h = float('-inf')
        sess_l = float('inf')
        or_h = float('-inf')
        or_l = float('inf')
        or_count = 0

        for i in range(n):
            d = times[i].date()
            if curr_day is None or d != curr_day:
                curr_day = d
                cum_pv = 0.0
                cum_vol = 0.0
                sess_h = highs[i]
                sess_l = lows[i]
                or_h = highs[i]
                or_l = lows[i]
                or_count = 1
                sess_high_prev[i] = float('nan')
                sess_low_prev[i] = float('nan')
            else:
                sess_high_prev[i] = sess_h
                sess_low_prev[i] = sess_l
                sess_h = max(sess_h, highs[i])
                sess_l = min(sess_l, lows[i])
                if or_count < 15:
                    or_h = max(or_h, highs[i])
                    or_l = min(or_l, lows[i])
                    or_count += 1

            typ = (highs[i] + lows[i] + closes[i]) / 3.0
            v = volumes[i]
            cum_pv += typ * v
            cum_vol += v
            vwap[i] = (cum_pv / cum_vol) if cum_vol > 0 else typ

            if d in prev_day_lookup:
                pd_high_arr[i], pd_low_arr[i] = prev_day_lookup[d]

            if or_count >= 15:
                or_high_arr[i] = or_h
                or_low_arr[i] = or_l

        # Pivots (5 bars)
        piv_h = [float('nan')] * n
        piv_l = [float('nan')] * n
        for i in range(5, n - 5):
            if all(highs[i] > highs[i - k] for k in range(1, 6)) and all(highs[i] >= highs[i + k] for k in range(1, 6)):
                piv_h[i + 5] = highs[i]
            if all(lows[i] < lows[i - k] for k in range(1, 6)) and all(lows[i] <= lows[i + k] for k in range(1, 6)):
                piv_l[i + 5] = lows[i]

        last_piv_h_arr = [float('nan')] * n
        last_piv_l_arr = [float('nan')] * n
        last_h = float('nan')
        last_l = float('nan')
        for i in range(n):
            if not math.isnan(piv_h[i]):
                last_h = piv_h[i]
            if not math.isnan(piv_l[i]):
                last_l = piv_l[i]
            last_piv_h_arr[i] = last_h
            last_piv_l_arr[i] = last_l

        recent_highs = ind.rolling_max(highs, break_len)
        recent_lows = ind.rolling_min(lows, break_len)

        # Helpers for levels
        def _near_above(p: float, arr: List[float]) -> Optional[float]:
            res = None
            for v in arr:
                if not math.isnan(v) and v > p:
                    if res is None or v < res:
                        res = v
            return res

        def _near_below(p: float, arr: List[float]) -> Optional[float]:
            res = None
            for v in arr:
                if not math.isnan(v) and v < p:
                    if res is None or v > res:
                        res = v
            return res

        def _crossed_up(c_curr: float, c_prev: float, arr: List[float]) -> Optional[float]:
            res = None
            for v in arr:
                if not math.isnan(v) and c_curr > v >= c_prev:
                    res = v if res is None else max(res, v)
            return res

        def _crossed_dn(c_curr: float, c_prev: float, arr: List[float]) -> Optional[float]:
            res = None
            for v in arr:
                if not math.isnan(v) and c_curr < v <= c_prev:
                    res = v if res is None else min(res, v)
            return res

        def _swept_low(l_curr: float, c_curr: float, arr: List[float]) -> bool:
            for v in arr:
                if not math.isnan(v) and l_curr < v < c_curr:
                    return True
            return False

        def _swept_high(h_curr: float, c_curr: float, arr: List[float]) -> bool:
            for v in arr:
                if not math.isnan(v) and h_curr > v > c_curr:
                    return True
            return False

        # Simulation State
        start_index = max(slow_len + slope_lb + 5, 30)
        for i in range(n):
            if not math.isnan(ema_slow[i]) and not math.isnan(atr_vals[i]) and not math.isnan(vwap[i]):
                start_index = max(start_index, i)
                break

        equity = initial_equity
        equity_curve = [initial_equity] * n
        trades: List[Trade] = []

        position = 0  # 1 for Long/Call, -1 for Short/Put
        pos_shares = 0.0
        entry_price = 0.0
        entry_index = 0
        cur_stop = 0.0
        target_tp1 = 0.0
        target_tp2 = 0.0
        tp1_hit = False
        run_extreme = 0.0

        retest_up = None
        brk_bar_up = -999
        retest_dn = None
        brk_bar_dn = -999

        bars_above = 0
        bars_below = 0

        # Day risk tracking
        tracked_day = None
        day_start_equity = initial_equity
        trades_today = 0
        consec_losses = 0
        halted = False
        last_exit_bar = -999

        for i in range(start_index, n):
            d = times[i].date()
            if tracked_day is None or d != tracked_day:
                tracked_day = d
                day_start_equity = equity
                trades_today = 0
                consec_losses = 0
                halted = False

            daily_pnl = equity - day_start_equity
            if daily_pnl <= -(day_start_equity * max_daily_loss / 100.0) or consec_losses >= max_consec_loss:
                halted = True

            atr_val = atr_vals[i]
            atr_pct = (atr_val / closes[i] * 100.0) if closes[i] > 0 else 0.0
            atr_pct_ok = (atr_pct >= min_atr_pct)

            # IMTF Chop & Market State
            ma_prev = ema_slow[i - slope_lb] if (i >= slope_lb and not math.isnan(ema_slow[i - slope_lb])) else ema_slow[i]
            ma_slope = ((ema_slow[i] - ma_prev) / ma_prev * 100.0) if (ma_prev and ma_prev > 0) else 0.0
            slope_flat = abs(ma_slope) < flat_slope_thresh
            slope_up = ma_slope >= flat_slope_thresh
            slope_down = ma_slope <= -flat_slope_thresh

            candle_body_pct = (abs(closes[i] - opens[i]) / closes[i] * 100.0) if closes[i] > 0 else 0.0
            narrow_sum = sum((abs(closes[k] - opens[k]) / closes[k] * 100.0) for k in range(i - narrow_lb + 1, i + 1) if closes[k] > 0)
            is_narrow = (narrow_sum / narrow_lb < narrow_thresh) if narrow_lb > 0 else False

            crosses = sum(1 for k in range(i - cross_lb + 1, i + 1) if (closes[k] - ema_slow[k]) * (closes[k - 1] - ema_slow[k - 1]) <= 0)
            freq_crosses = (crosses >= cross_thresh)

            atr_expanded = (atr_val > atr_avg[i] * atr_exp_mult) if (not math.isnan(atr_val) and not math.isnan(atr_avg[i])) else True

            bars_above = (bars_above + 1) if closes[i] > ema_slow[i] else 0
            bars_below = (bars_below + 1) if closes[i] < ema_slow[i] else 0

            is_accumulation = slope_flat and (freq_crosses or is_narrow or not atr_expanded)

            # MTF Bias & Confirmation
            bias15 = 0
            if not math.isnan(b15_e9[i]) and not math.isnan(b15_e20[i]) and not math.isnan(b15_e20p[i]) and not math.isnan(b15_c[i]):
                if b15_e9[i] > b15_e20[i] and b15_c[i] > b15_e20[i] and b15_e20[i] > b15_e20p[i]:
                    bias15 = 1
                elif b15_e9[i] < b15_e20[i] and b15_c[i] < b15_e20[i] and b15_e20[i] < b15_e20p[i]:
                    bias15 = -1

            conf5 = 0
            if not math.isnan(c5_e9[i]) and not math.isnan(c5_e20[i]) and not math.isnan(c5_rsi[i]) and not math.isnan(c5_hist[i]):
                if c5_e9[i] > c5_e20[i] and c5_rsi[i] > 50 and c5_hist[i] > 0:
                    conf5 = 1
                elif c5_e9[i] < c5_e20[i] and c5_rsi[i] < 50 and c5_hist[i] < 0:
                    conf5 = -1

            # Levels
            rec_h = recent_highs[i - 1] if i > 0 else float('nan')
            rec_l = recent_lows[i - 1] if i > 0 else float('nan')
            lvl_up = [rec_h, pd_high_arr[i], or_high_arr[i], sess_high_prev[i], last_piv_h_arr[i]]
            lvl_dn = [rec_l, pd_low_arr[i],  or_low_arr[i],  sess_low_prev[i],  last_piv_l_arr[i]]

            res_above = _near_above(closes[i], lvl_up)
            sup_below = _near_below(closes[i], lvl_dn)

            stop_dist = atr_val * stop_atr_m
            tol = atr_val * pull_tol_mult

            # Setups
            brk_lvl_up = _crossed_up(closes[i], closes[i - 1], lvl_up)
            brk_lvl_dn = _crossed_dn(closes[i], closes[i - 1], lvl_dn)
            brk_up = (brk_lvl_up is not None) and rel_vol[i] >= min_rel_vol and body_ratio[i] >= min_body and close_loc[i] >= min_close_loc
            brk_dn = (brk_lvl_dn is not None) and rel_vol[i] >= min_rel_vol and body_ratio[i] >= min_body and close_loc[i] <= (1.0 - min_close_loc)

            if brk_up:
                retest_up = brk_lvl_up
                brk_bar_up = i
            if brk_dn:
                retest_dn = brk_lvl_dn
                brk_bar_dn = i

            in_trend_up = ema_fast[i] > ema_slow[i] and closes[i] > ema_slow[i] and closes[i] > vwap[i]
            in_trend_dn = ema_fast[i] < ema_slow[i] and closes[i] < ema_slow[i] and closes[i] < vwap[i]

            pb_val_up = in_trend_up and (lows[i] <= ema_fast[i] + tol or lows[i] <= vwap[i] + tol) and lows[i] >= ema_slow[i] - tol and closes[i] > ema_fast[i] and closes[i] > opens[i] and closes[i] > highs[i - 1]
            pb_val_dn = in_trend_dn and (highs[i] >= ema_fast[i] - tol or highs[i] >= vwap[i] - tol) and highs[i] <= ema_slow[i] + tol and closes[i] < ema_fast[i] and closes[i] < opens[i] and closes[i] < lows[i - 1]

            retest_ok_up = (retest_up is not None and 2 <= (i - brk_bar_up) <= pull_window and lows[i] <= retest_up + tol and closes[i] > retest_up and closes[i] > opens[i] and closes[i] > highs[i - 1])
            retest_ok_dn = (retest_dn is not None and 2 <= (i - brk_bar_dn) <= pull_window and highs[i] >= retest_dn - tol and closes[i] < retest_dn and closes[i] < opens[i] and closes[i] < lows[i - 1])

            pull_up = pb_val_up or retest_ok_up
            pull_dn = pb_val_dn or retest_ok_dn

            rev_up = _swept_low(lows[i], closes[i], lvl_dn) and lower_wick[i] >= min_wick and closes[i] > opens[i] and rsi_vals[i] > rsi_vals[i - 1] and macd_hist[i] > macd_hist[i - 1] and rel_vol[i] >= min_rel_vol_rev
            rev_dn = _swept_high(highs[i], closes[i], lvl_up) and upper_wick[i] >= min_wick and closes[i] < opens[i] and rsi_vals[i] < rsi_vals[i - 1] and macd_hist[i] < macd_hist[i - 1] and rel_vol[i] >= min_rel_vol_rev

            # Scoring
            room_bull = (res_above is None) or ((res_above - closes[i]) >= stop_dist * min_room_r)
            room_bear = (sup_below is None) or ((closes[i] - sup_below) >= stop_dist * min_room_r)

            ema_bull = ema_fast[i] > ema_slow[i] and closes[i] > ema_slow[i]
            ema_bear = ema_fast[i] < ema_slow[i] and closes[i] < ema_slow[i]
            vwap_bull = closes[i] > vwap[i]
            vwap_bear = closes[i] < vwap[i]
            rsi_bull = 50.0 <= rsi_vals[i] <= 75.0
            rsi_bear = 25.0 <= rsi_vals[i] <= 50.0
            macd_bull = macd_line[i] > macd_sig_line[i] and macd_hist[i] > macd_hist[i - 1]
            macd_bear = macd_line[i] < macd_sig_line[i] and macd_hist[i] < macd_hist[i - 1]
            micro_bull = rel_vol[i] >= min_rel_vol and body_ratio[i] >= min_body and close_loc[i] >= min_close_loc
            micro_bear = rel_vol[i] >= min_rel_vol and body_ratio[i] >= min_body and close_loc[i] <= (1.0 - min_close_loc)

            c_bull_score = (
                (1 if bias15 == 1 else 0) + (1 if conf5 == 1 else 0) +
                (1 if ema_bull else 0) + (1 if vwap_bull else 0) +
                (1 if rsi_bull else 0) + (1 if macd_bull else 0) +
                (1 if micro_bull else 0) + (1 if room_bull else 0)
            )
            c_bear_score = (
                (1 if bias15 == -1 else 0) + (1 if conf5 == -1 else 0) +
                (1 if ema_bear else 0) + (1 if vwap_bear else 0) +
                (1 if rsi_bear else 0) + (1 if macd_bear else 0) +
                (1 if micro_bear else 0) + (1 if room_bear else 0)
            )

            rsi_turn_bull = rsi_vals[i] > rsi_vals[i - 1] and rsi_vals[i] < 60.0
            rsi_turn_bear = rsi_vals[i] < rsi_vals[i - 1] and rsi_vals[i] > 40.0
            micro_rev_bull = lower_wick[i] >= min_wick and rel_vol[i] >= min_rel_vol_rev and closes[i] > opens[i] and close_loc[i] >= 0.5
            micro_rev_bear = upper_wick[i] >= min_wick and rel_vol[i] >= min_rel_vol_rev and closes[i] < opens[i] and close_loc[i] <= 0.5

            r_bull_score = (
                (1 if bias15 != -1 else 0) + (1 if conf5 != -1 else 0) +
                (1 if rsi_turn_bull else 0) + (1 if macd_hist[i] > macd_hist[i - 1] else 0) +
                (1 if micro_rev_bull else 0) + (1 if room_bull else 0)
            )
            r_bear_score = (
                (1 if bias15 != 1 else 0) + (1 if conf5 != 1 else 0) +
                (1 if rsi_turn_bear else 0) + (1 if macd_hist[i] < macd_hist[i - 1] else 0) +
                (1 if micro_rev_bear else 0) + (1 if room_bear else 0)
            )

            cont_bull_ok = (not is_accumulation) and (c_bull_score >= min_score_cont) and ((not req_bias) or bias15 == 1) and ((not req_conf) or conf5 == 1)
            cont_bear_ok = (not is_accumulation) and (c_bear_score >= min_score_cont) and ((not req_bias) or bias15 == -1) and ((not req_conf) or conf5 == -1)
            rev_bull_ok = (r_bull_score >= min_score_rev) and (bias15 != -1)
            rev_bear_ok = (r_bear_score >= min_score_rev) and (bias15 != 1)

            brk_call = allow_call and use_breakout and brk_up and cont_bull_ok
            pb_call = allow_call and use_pullback and pull_up and cont_bull_ok
            rev_call = allow_call and use_reversal and rev_up and rev_bull_ok
            call_sig = brk_call or pb_call or rev_call

            brk_put = allow_put and use_breakout and brk_dn and cont_bear_ok
            pb_put = allow_put and use_pullback and pull_dn and cont_bear_ok
            rev_put = allow_put and use_reversal and rev_dn and rev_bear_ok
            put_sig = brk_put or pb_put or rev_put

            decision = 1 if (call_sig and not put_sig) else (-1 if (put_sig and not call_sig) else 0)

            # Reversal stop vs ATR stop
            stop_call = min(max((closes[i] - lows[i]) + atr_val * 0.1, atr_val * 0.5), atr_val * max_stop_atr) if (not (brk_call or pb_call) and rev_call) else stop_dist
            stop_put = min(max((highs[i] - closes[i]) + atr_val * 0.1, atr_val * 0.5), atr_val * max_stop_atr) if (not (brk_put or pb_put) and rev_put) else stop_dist

            # Active Trade Management
            if position != 0:
                run_extreme = max(run_extreme, highs[i]) if position == 1 else min(run_extreme, lows[i])
                bars_held = i - entry_index

                # 1. Opposite signal exit
                if exit_on_opp and ((position == 1 and decision == -1) or (position == -1 and decision == 1)):
                    exit_price = closes[i] * (1.0 - slip * position)
                    pnl = pos_shares * (exit_price - entry_price) * position
                    equity += pnl
                    trades.append(Trade(
                        entry_time_utc=times[entry_index],
                        exit_time_utc=times[i],
                        direction=position,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        shares=pos_shares,
                        pnl=pnl,
                        return_pct=pnl / (pos_shares * entry_price) if pos_shares * entry_price > 0 else 0.0,
                        exit_reason="opposite_signal",
                        bars_held=bars_held
                    ))
                    if pnl < -(0.2 * (day_start_equity * risk_pct)):
                        consec_losses += 1
                    elif pnl > 0:
                        consec_losses = 0
                    last_exit_bar = i
                    position = 0
                    pos_shares = 0.0

                # 2. In trade before TP1
                elif not tp1_hit:
                    hit_stop = (opens[i] <= cur_stop or lows[i] <= cur_stop) if position == 1 else (opens[i] >= cur_stop or highs[i] >= cur_stop)
                    hit_tp1 = (opens[i] >= target_tp1 or highs[i] >= target_tp1) if position == 1 else (opens[i] <= target_tp1 or lows[i] <= target_tp1)

                    if hit_stop:
                        exit_price = stop_fill(cur_stop, opens[i], position) * (1.0 - slip * position)
                        pnl = pos_shares * (exit_price - entry_price) * position
                        equity += pnl
                        trades.append(Trade(
                            entry_time_utc=times[entry_index],
                            exit_time_utc=times[i],
                            direction=position,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            shares=pos_shares,
                            pnl=pnl,
                            return_pct=pnl / (pos_shares * entry_price) if pos_shares * entry_price > 0 else 0.0,
                            exit_reason="stop",
                            bars_held=bars_held
                        ))
                        if pnl < -(0.2 * (day_start_equity * risk_pct)):
                            consec_losses += 1
                        elif pnl > 0:
                            consec_losses = 0
                        last_exit_bar = i
                        position = 0
                        pos_shares = 0.0

                    elif hit_tp1:
                        # Partial scale-out at TP1
                        tp1_hit = True
                        exit_shares = pos_shares * (tp1_pct / 100.0)
                        exit_price = limit_fill(target_tp1, opens[i], position) * (1.0 - slip * position)
                        pnl = exit_shares * (exit_price - entry_price) * position
                        equity += pnl
                        trades.append(Trade(
                            entry_time_utc=times[entry_index],
                            exit_time_utc=times[i],
                            direction=position,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            shares=exit_shares,
                            pnl=pnl,
                            return_pct=pnl / (exit_shares * entry_price) if exit_shares * entry_price > 0 else 0.0,
                            exit_reason="tp1",
                            bars_held=bars_held
                        ))
                        pos_shares -= exit_shares
                        # Move stop to breakeven & begin trailing
                        cur_stop = max(entry_price, run_extreme - atr_val * trail_atr) if position == 1 else min(entry_price, run_extreme + atr_val * trail_atr)

                    elif bars_held >= time_exit_bars:
                        exit_price = closes[i] * (1.0 - slip * position)
                        pnl = pos_shares * (exit_price - entry_price) * position
                        equity += pnl
                        trades.append(Trade(
                            entry_time_utc=times[entry_index],
                            exit_time_utc=times[i],
                            direction=position,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            shares=pos_shares,
                            pnl=pnl,
                            return_pct=pnl / (pos_shares * entry_price) if pos_shares * entry_price > 0 else 0.0,
                            exit_reason="time_exit",
                            bars_held=bars_held
                        ))
                        if pnl < -(0.2 * (day_start_equity * risk_pct)):
                            consec_losses += 1
                        elif pnl > 0:
                            consec_losses = 0
                        last_exit_bar = i
                        position = 0
                        pos_shares = 0.0

                # 3. Runner management (after TP1)
                else:
                    # Update trailing stop
                    if position == 1:
                        cur_stop = max(cur_stop, max(entry_price, run_extreme - atr_val * trail_atr))
                        hit_trail = (opens[i] <= cur_stop or lows[i] <= cur_stop)
                        hit_tp2 = (opens[i] >= target_tp2 or highs[i] >= target_tp2)
                    else:
                        cur_stop = min(cur_stop, min(entry_price, run_extreme + atr_val * trail_atr))
                        hit_trail = (opens[i] >= cur_stop or highs[i] >= cur_stop)
                        hit_tp2 = (opens[i] <= target_tp2 or lows[i] <= target_tp2)

                    if hit_tp2:
                        exit_price = limit_fill(target_tp2, opens[i], position) * (1.0 - slip * position)
                        pnl = pos_shares * (exit_price - entry_price) * position
                        equity += pnl
                        trades.append(Trade(
                            entry_time_utc=times[entry_index],
                            exit_time_utc=times[i],
                            direction=position,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            shares=pos_shares,
                            pnl=pnl,
                            return_pct=pnl / (pos_shares * entry_price) if pos_shares * entry_price > 0 else 0.0,
                            exit_reason="tp2",
                            bars_held=bars_held
                        ))
                        last_exit_bar = i
                        position = 0
                        pos_shares = 0.0

                    elif hit_trail:
                        exit_price = stop_fill(cur_stop, opens[i], position) * (1.0 - slip * position)
                        pnl = pos_shares * (exit_price - entry_price) * position
                        equity += pnl
                        trades.append(Trade(
                            entry_time_utc=times[entry_index],
                            exit_time_utc=times[i],
                            direction=position,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            shares=pos_shares,
                            pnl=pnl,
                            return_pct=pnl / (pos_shares * entry_price) if pos_shares * entry_price > 0 else 0.0,
                            exit_reason="trail_stop",
                            bars_held=bars_held
                        ))
                        last_exit_bar = i
                        position = 0
                        pos_shares = 0.0

                    elif bars_held >= runner_max_bars:
                        exit_price = closes[i] * (1.0 - slip * position)
                        pnl = pos_shares * (exit_price - entry_price) * position
                        equity += pnl
                        trades.append(Trade(
                            entry_time_utc=times[entry_index],
                            exit_time_utc=times[i],
                            direction=position,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            shares=pos_shares,
                            pnl=pnl,
                            return_pct=pnl / (pos_shares * entry_price) if pos_shares * entry_price > 0 else 0.0,
                            exit_reason="runner_time_exit",
                            bars_held=bars_held
                        ))
                        last_exit_bar = i
                        position = 0
                        pos_shares = 0.0

            # Check new entry if flat
            if position == 0 and i + 1 < n and decision != 0:
                in_window = is_within_new_trade_window(times[i], restrict_hrs, start_cst, end_cst)
                cooldown_ok = (i - last_exit_bar) > cooldown_bars
                can_enter = (not halted) and (trades_today < max_trades) and cooldown_ok and atr_pct_ok and in_window

                if can_enter:
                    direction = decision
                    fill_price = opens[i + 1] * (1.0 + slip * direction)
                    act_stop = stop_call if direction == 1 else stop_put

                    if act_stop > 0:
                        risk_dollars = equity * risk_pct
                        sz = min(risk_dollars / act_stop, (equity * max_lev) / fill_price)
                        if sz > 0:
                            position = direction
                            pos_shares = sz
                            entry_price = fill_price
                            entry_index = i + 1
                            cur_stop = fill_price - direction * act_stop
                            target_tp1 = fill_price + direction * act_stop * tp1_r
                            target_tp2 = fill_price + direction * act_stop * tp2_r
                            tp1_hit = False
                            run_extreme = fill_price
                            trades_today += 1

            equity_curve[i] = equity

        # Build final backtest result
        kept_times = times[start_index:]
        kept_equity = equity_curve[start_index:]
        kept_bars = exec_bars[start_index:]

        result = build_result(kept_times, kept_equity, trades, initial_equity)
        result.indicators.append(IndicatorSeries(f"EMA {fast_len}", "#FB923C", "Solid", to_indicator_points(kept_bars, ema_fast[start_index:])))
        result.indicators.append(IndicatorSeries(f"EMA {slow_len}", "#38BDF8", "Solid", to_indicator_points(kept_bars, ema_slow[start_index:])))
        result.indicators.append(IndicatorSeries("Session VWAP", "#A855F7", "Solid", to_indicator_points(kept_bars, vwap[start_index:])))
        result.indicators.append(IndicatorSeries("Prev Day High", "#EF4444", "Dotted", to_indicator_points(kept_bars, pd_high_arr[start_index:])))
        result.indicators.append(IndicatorSeries("Prev Day Low", "#10B981", "Dotted", to_indicator_points(kept_bars, pd_low_arr[start_index:])))

        return result

# 22. SPY CIPHER v2.0 (Intraday CALL/PUT Engine)
class SpyCipherV2Strategy(SpyMicroTradeEngineStrategy):
    name = "SPY CIPHER v2.0 (Intraday CALL/PUT Engine)"
    description = (
        "SPY CIPHER v2.0 intraday CALL-bias / PUT-bias momentum & scalp engine. "
        "Integrates multi-timeframe confirmation (15m bias + 5m confirmation), IMTF chop/squeeze regime filtering, "
        "confluence evidence checklist scoring, breakout/retest/pullback/reversal triggers, and dynamic TP1 (50% scale-out) + trailing stops."
    )

# 23. SPY Pullback - RSI(2) in Trend
class SpyPullbackRsiStrategy(BaseStrategy):
    name = "SPY Pullback - RSI(2) in Trend"
    description = (
        "Connors-style RSI(2) pullback in trend strategy for SPY. Enters long when price is above 200 EMA "
        "(and intraday VWAP) with extreme oversold RSI(2) < 10. Exits on mean-reversion snapback above 5 EMA, "
        "RSI(2) > 70, ATR stop-loss, or session close."
    )
    requires_anchor_timeframe = False

    parameter_definitions = [
        StrategyParameterDefinition("TrendEmaLength", "Trend EMA Length", "int", 200),
        StrategyParameterDefinition("UseVwap", "Require Price > VWAP (Intraday)", "bool", True),
        StrategyParameterDefinition("RsiLength", "RSI Length", "int", 2),
        StrategyParameterDefinition("RsiBuy", "Buy when RSI below", "double", 10.0),
        StrategyParameterDefinition("RsiSell", "Exit when RSI above", "double", 70.0),
        StrategyParameterDefinition("ExitEmaLength", "Exit when close above EMA", "int", 5),
        StrategyParameterDefinition("MaxBars", "Time stop (max bars held)", "int", 10),
        StrategyParameterDefinition("AtrPeriod", "ATR Period", "int", 14),
        StrategyParameterDefinition("AtrMultiplier", "Stop Distance (x ATR)", "double", 2.0),
        StrategyParameterDefinition("RiskPct", "Risk per trade (% of equity)", "double", 1.0),
        StrategyParameterDefinition("MaxLeverage", "Max leverage (x notional)", "double", 4.0),
        StrategyParameterDefinition("SlippageBps", "Slippage (bps per side)", "double", 1.0),
        StrategyParameterDefinition("RestrictNewTradesToTradingHours", "Restrict to Entry Session", "bool", True),
        StrategyParameterDefinition("NewTradesStartTimeCst", "Entry window start (HH:mm CST)", "string", "08:45"),
        StrategyParameterDefinition("NewTradesEndTimeCst", "Entry window end (HH:mm CST)", "string", "14:55"),
        StrategyParameterDefinition("FlattenEod", "Flatten at session end", "bool", True),
        StrategyParameterDefinition("FlattenTimeCst", "Flatten time (HH:mm CST)", "string", "14:55"),
    ]

    def run(
        self,
        exec_bars: List[PriceBar],
        anchor_bars: List[PriceBar],
        parameters: Dict[str, Any],
        initial_equity: float
    ) -> BacktestResult:
        n = len(exec_bars)
        if n < 30:
            return empty_result()

        trend_len = int(parameters.get("TrendEmaLength", 200))
        use_vwap = bool(parameters.get("UseVwap", True))
        rsi_len = int(parameters.get("RsiLength", 2))
        rsi_buy = float(parameters.get("RsiBuy", 10.0))
        rsi_sell = float(parameters.get("RsiSell", 70.0))
        exit_ema_len = int(parameters.get("ExitEmaLength", 5))
        max_bars = int(parameters.get("MaxBars", 10))
        atr_period = int(parameters.get("AtrPeriod", 14))
        atr_mult = float(parameters.get("AtrMultiplier", 2.0))
        risk_pct = float(parameters.get("RiskPct", 1.0)) / 100.0
        max_lev = float(parameters.get("MaxLeverage", 4.0))
        slip = float(parameters.get("SlippageBps", 1.0)) / 10000.0
        restrict_hrs = bool(parameters.get("RestrictNewTradesToTradingHours", True))
        start_cst = str(parameters.get("NewTradesStartTimeCst", "08:45"))
        end_cst = str(parameters.get("NewTradesEndTimeCst", "14:55"))
        flat_eod = bool(parameters.get("FlattenEod", True))
        flat_cst = str(parameters.get("FlattenTimeCst", "14:55"))

        closes = [float(b.close) for b in exec_bars]
        opens = [float(b.open) for b in exec_bars]
        highs = [float(b.high) for b in exec_bars]
        lows = [float(b.low) for b in exec_bars]
        volumes = [float(b.volume) for b in exec_bars]
        times = [b.time_utc for b in exec_bars]

        trend_ema = ind.ema(closes, trend_len)
        fast_ema = ind.ema(closes, exit_ema_len)
        rsi_vals = ind.rsi(closes, rsi_len)
        atr_vals = ind.atr(exec_bars, atr_period)

        # Intraday VWAP reset daily
        vwap = [float('nan')] * n
        curr_day = None
        cum_pv = 0.0
        cum_vol = 0.0
        for i in range(n):
            d = times[i].date()
            if curr_day is None or d != curr_day:
                curr_day = d
                cum_pv = 0.0
                cum_vol = 0.0
            typ = (highs[i] + lows[i] + closes[i]) / 3.0
            v = volumes[i]
            cum_pv += typ * v
            cum_vol += v
            vwap[i] = (cum_pv / cum_vol) if cum_vol > 0 else typ

        start_index = max(trend_len, atr_period + 2, 30)
        for i in range(n):
            if not math.isnan(trend_ema[i]) and not math.isnan(atr_vals[i]) and not math.isnan(rsi_vals[i]):
                start_index = max(start_index, i)
                break

        equity = initial_equity
        equity_curve = [initial_equity] * n
        trades: List[Trade] = []

        position = 0
        pos_shares = 0.0
        entry_price = 0.0
        entry_index = 0
        cur_stop = 0.0
        bars_held = 0

        for i in range(start_index, n):
            # In-trade position management
            if position > 0:
                bars_held += 1

                # Check Stop Loss
                hit_stop = (opens[i] <= cur_stop or lows[i] <= cur_stop)
                if hit_stop:
                    exit_price = stop_fill(cur_stop, opens[i], position) * (1.0 - slip)
                    pnl = pos_shares * (exit_price - entry_price)
                    equity += pnl
                    trades.append(Trade(
                        entry_time_utc=times[entry_index],
                        exit_time_utc=times[i],
                        direction=position,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        shares=pos_shares,
                        pnl=pnl,
                        return_pct=pnl / (pos_shares * entry_price) if pos_shares * entry_price > 0 else 0.0,
                        exit_reason="stop_loss",
                        bars_held=bars_held
                    ))
                    position = 0
                    pos_shares = 0.0

                else:
                    # Check conditional exits on close
                    is_eod = False
                    if flat_eod:
                        is_last_bar_of_day = (i + 1 == n) or (times[i + 1].date() != times[i].date())
                        in_trade_win = is_within_new_trade_window(times[i], True, "08:30", flat_cst)
                        is_eod = is_last_bar_of_day or (not in_trade_win)

                    exit_cond = (
                        closes[i] > fast_ema[i]
                        or rsi_vals[i] > rsi_sell
                        or bars_held >= max_bars
                        or is_eod
                    )

                    if exit_cond:
                        exit_price = closes[i] * (1.0 - slip)
                        pnl = pos_shares * (exit_price - entry_price)
                        equity += pnl
                        reason = (
                            "fast_ema_exit" if closes[i] > fast_ema[i] else
                            ("rsi_exit" if rsi_vals[i] > rsi_sell else
                            ("time_stop" if bars_held >= max_bars else "session_exit"))
                        )
                        trades.append(Trade(
                            entry_time_utc=times[entry_index],
                            exit_time_utc=times[i],
                            direction=position,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            shares=pos_shares,
                            pnl=pnl,
                            return_pct=pnl / (pos_shares * entry_price) if pos_shares * entry_price > 0 else 0.0,
                            exit_reason=reason,
                            bars_held=bars_held
                        ))
                        position = 0
                        pos_shares = 0.0

            # New Entry check if flat
            if position == 0 and i + 1 < n:
                trend_ok = closes[i] > trend_ema[i] and (not use_vwap or closes[i] > vwap[i])
                rsi_ok = not math.isnan(rsi_vals[i]) and rsi_vals[i] < rsi_buy
                in_session = is_within_new_trade_window(times[i], restrict_hrs, start_cst, end_cst)

                if trend_ok and rsi_ok and in_session:
                    fill_price = opens[i + 1] * (1.0 + slip)
                    atr_val = atr_vals[i]
                    stop_dist = atr_mult * atr_val if not math.isnan(atr_val) else fill_price * 0.01

                    if stop_dist > 0:
                        risk_dollars = equity * risk_pct
                        sz = min(risk_dollars / stop_dist, (equity * max_lev) / fill_price)
                        sz = math.floor(sz)
                        if sz >= 1:
                            position = 1
                            pos_shares = sz
                            entry_price = fill_price
                            entry_index = i + 1
                            cur_stop = fill_price - stop_dist
                            bars_held = 0

            equity_curve[i] = equity

        kept_times = times[start_index:]
        kept_equity = equity_curve[start_index:]
        kept_bars = exec_bars[start_index:]

        result = build_result(kept_times, kept_equity, trades, initial_equity)
        result.indicators.append(IndicatorSeries(f"Trend EMA {trend_len}", "#F97316", "Solid", to_indicator_points(kept_bars, trend_ema[start_index:])))
        result.indicators.append(IndicatorSeries(f"Exit EMA {exit_ema_len}", "#06B6D4", "Solid", to_indicator_points(kept_bars, fast_ema[start_index:])))
        result.indicators.append(IndicatorSeries("Session VWAP", "#A855F7", "Solid", to_indicator_points(kept_bars, vwap[start_index:])))

        return result

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
        InstitutionalMaTrendFilterStrategy(),
        SpyMicroTradeEngineStrategy(),
        SpyCipherV2Strategy(),
        SpyPullbackRsiStrategy(),
    ]

    @classmethod
    def get_by_name(cls, name: str) -> Optional[BaseStrategy]:
        norm = name.strip().lower()
        for s in cls.ALL:
            if s.name.lower() == norm:
                return s
        # Fuzzy alias support
        if "cipher" in norm:
            for s in cls.ALL:
                if isinstance(s, (SpyCipherV2Strategy, SpyMicroTradeEngineStrategy)):
                    return s
        if "spy micro" in norm:
            for s in cls.ALL:
                if isinstance(s, SpyMicroTradeEngineStrategy):
                    return s
        if "pullback rsi" in norm or "rsi(2)" in norm or "spy pullback" in norm:
            for s in cls.ALL:
                if isinstance(s, SpyPullbackRsiStrategy):
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

