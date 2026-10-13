"""MEIC 0DTE multi-tranche Iron Condor backtester matching MeicBacktester.cs."""
import math
from datetime import datetime, time, timedelta, timezone, date
from typing import List, Optional, Tuple, Set
import pytz
from strategy.models import (
    PriceBar, MeicBacktestConfig, MeicSimulatedTrade, MeicBacktestReport, EquityPoint
)
import strategy.indicators as ind
from services.market_calendar import (
    REGULAR_OPEN_ET, get_session_close_et, is_early_close, is_trading_day
)

RISK_FREE_RATE = 0.045
EASTERN_TZ = pytz.timezone("America/New_York")

def cdf(x: float) -> float:
    """Standard normal cumulative distribution function via Abramowitz & Stegun 7.1.26."""
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911
    sign = -1 if x < 0 else 1
    abs_x = abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + p * abs_x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-abs_x * abs_x)
    return 0.5 * (1.0 + sign * y)

def black_scholes_price(S: float, K: float, sigma: float, tau: float, is_call: bool) -> float:
    if tau <= 0.00001:
        return max(0.0, S - K) if is_call else max(0.0, K - S)
    vol_sqrt_t = sigma * math.sqrt(tau)
    if vol_sqrt_t <= 0:
        vol_sqrt_t = 1e-9
    d1 = (math.log(S / K) + (RISK_FREE_RATE + 0.5 * sigma * sigma) * tau) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    n_d1 = cdf(d1)
    n_d2 = cdf(d2)
    exp_rt = math.exp(-RISK_FREE_RATE * tau)
    if is_call:
        return S * n_d1 - K * exp_rt * n_d2
    else:
        return K * exp_rt * (1.0 - n_d2) - S * (1.0 - n_d1)

def calculate_delta(S: float, K: float, sigma: float, tau: float, is_call: bool) -> float:
    if tau <= 0.00001:
        if is_call:
            return 1.0 if S >= K else 0.0
        else:
            return -1.0 if S <= K else 0.0
    vol_sqrt_t = sigma * math.sqrt(tau)
    if vol_sqrt_t <= 0:
        vol_sqrt_t = 1e-9
    d1 = (math.log(S / K) + (RISK_FREE_RATE + 0.5 * sigma * sigma) * tau) / vol_sqrt_t
    return cdf(d1) if is_call else abs(cdf(d1) - 1.0)

def find_strike_for_delta(spot: float, tau: float, target_delta: float, is_call: bool, step: float, iv: float) -> float:
    best_strike = spot * 1.008 if is_call else spot * 0.992
    min_diff = float('inf')
    start = spot if is_call else spot * 0.85
    end = spot * 1.15 if is_call else spot
    curr = start
    while curr <= end:
        d = calculate_delta(spot, curr, iv, tau, is_call)
        diff = abs(d - target_delta)
        if diff < min_diff:
            min_diff = diff
            best_strike = curr
        curr += step
    return round(best_strike / step) * step

def calculate_iron_condor_debit(spot: float, tau: float, sp: float, lp: float, sc: float, lc: float, iv: float) -> float:
    if tau <= 0.00001:
        put_spread_val = max(0.0, min(sp - lp, sp - spot))
        call_spread_val = max(0.0, min(lc - sc, spot - sc))
        return round(put_spread_val + call_spread_val, 2)

    sc_price = black_scholes_price(spot, sc, iv, tau, is_call=True)
    lc_price = black_scholes_price(spot, lc, iv, tau, is_call=True)
    call_spread = max(0.01, sc_price - lc_price)

    sp_price = black_scholes_price(spot, sp, iv, tau, is_call=False)
    lp_price = black_scholes_price(spot, lp, iv, tau, is_call=False)
    put_spread = max(0.01, sp_price - lp_price)

    return round(call_spread + put_spread, 2)

class ActiveSimulatedTranche:
    def __init__(self, tranche_index, date, entry_time_utc, sched_key, spot, sp, lp, sc, lc, credit):
        self.tranche_index = tranche_index
        self.date = date
        self.entry_time_utc = entry_time_utc
        self.scheduled_time_et = sched_key
        self.spot_at_entry = spot
        self.short_put_strike = sp
        self.long_put_strike = lp
        self.short_call_strike = sc
        self.long_call_strike = lc
        self.entry_credit = credit
        self.current_debit = credit
        self.bars_held = 0

def open_simulated_iron_condor(
    tranche_index: int,
    session_date: date,
    entry_time_utc: datetime,
    sched_key: str,
    spot: float,
    tau: float,
    config: MeicBacktestConfig
) -> Optional[ActiveSimulatedTranche]:
    step = 5.0 if spot > 1000 else (0.5 if spot < 200 else 1.0)
    sc_strike = find_strike_for_delta(spot, tau, config.short_call_delta_target, is_call=True, step=step, iv=config.baseline_iv)
    lc_strike = sc_strike + config.wing_width

    sp_strike = find_strike_for_delta(spot, tau, config.short_put_delta_target, is_call=False, step=step, iv=config.baseline_iv)
    lp_strike = sp_strike - config.wing_width

    if sp_strike >= sc_strike:
        return None

    entry_credit = calculate_iron_condor_debit(spot, tau, sp_strike, lp_strike, sc_strike, lc_strike, config.baseline_iv)
    if entry_credit < 0.05:
        entry_credit = 0.05

    return ActiveSimulatedTranche(
        tranche_index=tranche_index,
        date=session_date,
        entry_time_utc=entry_time_utc,
        sched_key=sched_key,
        spot=spot,
        sp=sp_strike,
        lp=lp_strike,
        sc=sc_strike,
        lc=lc_strike,
        credit=round(entry_credit, 2)
    )

def run_meic_backtest(bars: List[PriceBar], config: MeicBacktestConfig) -> MeicBacktestReport:
    report = MeicBacktestReport()
    if not bars:
        return report

    # Parse scheduled times (e.g. "09:45, 10:45, 11:45, 13:00, 14:00")
    scheduled_times = []
    raw_sched = config.schedule_times_et if isinstance(config.schedule_times_et, (list, tuple)) else config.schedule_times_et.split(",")
    for s in raw_sched:
        s = s.strip()
        if not s:
            continue
        try:
            h, m = map(int, s.split(":"))
            scheduled_times.append(time(h, m))
        except Exception:
            pass
    if not scheduled_times:
        scheduled_times = [time(9, 45), time(10, 45), time(11, 45), time(13, 0), time(14, 0)]
    scheduled_times.sort()

    adx_values = ind.adx(bars, config.adx_period) if config.enable_adx_filter else None
    ci_values, _, _ = ind.composite_index(bars) if config.enable_ci_filter else (None, None, None)

    # Group bars by Eastern trading day
    daily_bars: List[List[Tuple[int, PriceBar, datetime]]] = []
    current_day = None
    current_day_list = []

    for i, bar in enumerate(bars):
        t_utc = bar.time_utc if bar.time_utc.tzinfo else bar.time_utc.replace(tzinfo=timezone.utc)
        et = t_utc.astimezone(EASTERN_TZ)
        d_et = et.date()

        session_close = get_session_close_et(d_et)
        if et.time() < REGULAR_OPEN_ET or et.time() >= session_close:
            continue

        if d_et != current_day:
            if current_day_list:
                daily_bars.append(current_day_list)
            current_day = d_et
            current_day_list = []

        current_day_list.append((i, bar, et))

    if current_day_list:
        daily_bars.append(current_day_list)

    equity = config.initial_capital
    report.equity_curve.append(EquityPoint(bars[0].time_utc, equity))

    global_trade_counter = 1
    all_trades: List[MeicSimulatedTrade] = []
    scheduled_windows_evaluated = 0
    skipped_by_adx = 0
    skipped_by_ci = 0
    skipped_by_invalid_strikes = 0

    for day_bars in daily_bars:
        if len(day_bars) < 5:
            continue

        entered_keys: Set[str] = set()
        active_tranches: List[ActiveSimulatedTranche] = []
        session_date = day_bars[0][2].date()
        session_close = get_session_close_et(session_date)
        market_close_et = datetime.combine(session_date, session_close, tzinfo=EASTERN_TZ)

        eod_cutoff = (
            (datetime.combine(session_date, session_close) - timedelta(minutes=10)).time()
            if is_early_close(session_date)
            else time(15, 50, 0)
        )

        for b_idx, (global_idx, bar, et) in enumerate(day_bars):
            spot = float(bar.close)
            minutes_to_close = max(1.0, (market_close_et - et).total_seconds() / 60.0)
            tau = minutes_to_close / (390.0 * 252.0)

            # 1. Scheduled entries
            for sched_t in scheduled_times:
                sched_key = sched_t.strftime("%H:%M")
                if sched_key not in entered_keys:
                    diff = (et.hour * 60 + et.minute) - (sched_t.hour * 60 + sched_t.minute)
                    if 0 <= diff <= 5:
                        entered_keys.add(sched_key)
                        scheduled_windows_evaluated += 1

                        if config.enable_adx_filter and adx_values and global_idx < len(adx_values):
                            curr_adx = adx_values[global_idx]
                            if not math.isnan(curr_adx) and curr_adx > config.max_adx_threshold:
                                skipped_by_adx += 1
                                continue

                        if config.enable_ci_filter and ci_values and global_idx < len(ci_values):
                            curr_ci = ci_values[global_idx]
                            if not math.isnan(curr_ci) and abs(curr_ci) > config.max_ci_threshold:
                                if config.require_ci_exhaustion and global_idx >= 2:
                                    prev_ci = ci_values[global_idx - 1]
                                    is_ob_exp = curr_ci > config.max_ci_threshold and curr_ci > prev_ci
                                    is_os_exp = curr_ci < -config.max_ci_threshold and curr_ci < prev_ci
                                    if is_ob_exp or is_os_exp:
                                        skipped_by_ci += 1
                                        continue
                                else:
                                    skipped_by_ci += 1
                                    continue

                        tranche = open_simulated_iron_condor(
                            global_trade_counter, session_date, bar.time_utc, sched_key, spot, tau, config
                        )
                        if tranche:
                            global_trade_counter += 1
                            active_tranches.append(tranche)
                        else:
                            skipped_by_invalid_strikes += 1

            # 2. Mark-to-market active tranches
            tranches_to_close = []
            for t in active_tranches:
                t.bars_held += 1
                curr_debit = calculate_iron_condor_debit(
                    spot, tau, t.short_put_strike, t.long_put_strike, t.short_call_strike, t.long_call_strike, config.baseline_iv
                )
                t.current_debit = curr_debit

                # Take profit
                target_debit = t.entry_credit * (1.0 - (config.take_profit_percent / 100.0))
                if curr_debit <= target_debit:
                    tranches_to_close.append((t, f"Take Profit Hit (+{config.take_profit_percent:.1f}%)"))
                    continue

                # Stop loss
                stop_debit = t.entry_credit * (1.0 + config.stop_loss_multiplier)
                if curr_debit >= stop_debit:
                    tranches_to_close.append((t, f"Stop Loss Triggered ({config.stop_loss_multiplier:.1f}x Credit)"))
                    continue

                # EOD cutoff
                if et.time() >= eod_cutoff or b_idx == len(day_bars) - 1:
                    tranches_to_close.append((t, "EOD Settlement Cutoff"))
                    continue

            for t, reason in tranches_to_close:
                active_tranches.remove(t)
                pnl_per_share = t.entry_credit - t.current_debit
                total_pnl = round(pnl_per_share * 100.0 * config.quantity_per_tranche, 2)
                ret_pct = (pnl_per_share / t.entry_credit) * 100.0 if t.entry_credit > 0 else 0.0

                trade = MeicSimulatedTrade(
                    tranche_index=t.tranche_index,
                    date=datetime.combine(t.date, time(0, 0)),
                    entry_time_utc=t.entry_time_utc,
                    exit_time_utc=bar.time_utc,
                    scheduled_time_et=t.scheduled_time_et,
                    spot_at_entry=t.spot_at_entry,
                    spot_at_exit=spot,
                    short_put_strike=t.short_put_strike,
                    long_put_strike=t.long_put_strike,
                    short_call_strike=t.short_call_strike,
                    long_call_strike=t.long_call_strike,
                    entry_credit=t.entry_credit,
                    exit_debit=t.current_debit,
                    realized_pnl=total_pnl,
                    return_pct=round(ret_pct, 1),
                    exit_reason=reason,
                    bars_held=t.bars_held
                )
                all_trades.append(trade)
                equity += total_pnl
                report.equity_curve.append(EquityPoint(bar.time_utc, equity))

    report.scheduled_windows_evaluated = scheduled_windows_evaluated
    report.skipped_by_adx_filter = skipped_by_adx
    report.skipped_by_ci_filter = skipped_by_ci
    report.skipped_by_invalid_strikes = skipped_by_invalid_strikes

    report.trades = sorted(all_trades, key=lambda t: t.entry_time_utc, reverse=True)
    report.total_trades = len(all_trades)
    report.win_count = sum(1 for t in all_trades if t.realized_pnl > 0)
    report.loss_count = sum(1 for t in all_trades if t.realized_pnl < 0)
    report.total_pnl = round(sum(t.realized_pnl for t in all_trades), 2)
    report.return_on_capital_pct = round((report.total_pnl / config.initial_capital) * 100.0, 2) if config.initial_capital > 0 else 0.0
    report.win_rate = round(report.win_count / report.total_trades * 100.0, 1) if report.total_trades > 0 else 0.0

    gross_gains = sum(t.realized_pnl for t in all_trades if t.realized_pnl > 0)
    gross_losses = abs(sum(t.realized_pnl for t in all_trades if t.realized_pnl < 0))
    report.profit_factor = round(gross_gains / gross_losses, 2) if gross_losses > 0 else (99.9 if gross_gains > 0 else 0.0)

    report.avg_win_pnl = round(gross_gains / report.win_count, 2) if report.win_count > 0 else 0.0
    report.avg_loss_pnl = round(gross_losses / report.loss_count, 2) if report.loss_count > 0 else 0.0

    peak = config.initial_capital
    max_dd = 0.0
    max_dd_pct = 0.0
    for pt in report.equity_curve:
        if pt.equity > peak:
            peak = pt.equity
        dd = peak - pt.equity
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = (dd / peak) * 100.0 if peak > 0 else 0.0

    report.max_drawdown = round(max_dd, 2)
    report.max_drawdown_pct = round(max_dd_pct, 2)

    return report