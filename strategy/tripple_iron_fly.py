"""TrippleIronFly SPX IF MAX 3 ladder backtester matching TrippleIronFlyBacktester.cs."""
import math
from datetime import datetime, time, timedelta, timezone, date
from typing import List, Optional, Tuple, Set
import pytz
from strategy.models import (
    PriceBar, TrippleIronFlyConfig, TrippleIronFlyTrade, TrippleIronFlyReport, EquityPoint
)
from services.market_calendar import (
    REGULAR_OPEN_ET, get_session_close_et, is_early_close
)
from strategy.meic_backtester import black_scholes_price

EASTERN_TZ = pytz.timezone("America/New_York")

def iron_fly_value(spot: float, tau: float, body: float, lower_wing: float, upper_wing: float, iv: float) -> float:
    if tau <= 0.00001:
        call_side = max(0.0, min(upper_wing - body, spot - body))
        put_side = max(0.0, min(body - lower_wing, body - spot))
        return round(call_side + put_side, 2)

    short_call = black_scholes_price(spot, body, iv, tau, is_call=True)
    long_call = black_scholes_price(spot, upper_wing, iv, tau, is_call=True)
    call_spread = max(0.01, short_call - long_call)

    short_put = black_scholes_price(spot, body, iv, tau, is_call=False)
    long_put = black_scholes_price(spot, lower_wing, iv, tau, is_call=False)
    put_spread = max(0.01, short_put - long_put)

    return round(call_spread + put_spread, 2)

class ActiveFly:
    def __init__(self, fly_index, date_val, entry_time_utc, spot, body, lower_wing, upper_wing,
                 ladder_step, concurrent_at_entry, credit, credit_raw):
        self.fly_index = fly_index
        self.date = date_val
        self.entry_time_utc = entry_time_utc
        self.spot_at_entry = spot
        self.body_strike = body
        self.lower_wing = lower_wing
        self.upper_wing = upper_wing
        self.ladder_step = ladder_step
        self.concurrent_at_entry = concurrent_at_entry
        self.entry_credit = credit
        self.current_debit = credit
        self.entry_credit_raw = credit_raw
        self.current_debit_raw = credit_raw
        self.bars_held = 0

def open_fly(fly_index: int, date_val: date, entry_time_utc: datetime, spot: float, body: float,
             ladder_step: int, concurrent_at_entry: int, tau: float, wing: float,
             config: TrippleIronFlyConfig) -> ActiveFly:
    grid_step = config.strike_increment if config.strike_increment > 0 else wing
    lower_wing = round((body - wing) / grid_step) * grid_step
    upper_wing = round((body + wing) / grid_step) * grid_step

    credit_raw = iron_fly_value(spot, tau, body, lower_wing, upper_wing, config.baseline_iv)
    credit = credit_raw - 4.0 * config.slippage_per_leg_points
    if credit < 0.05:
        credit = 0.05

    fly = ActiveFly(
        fly_index=fly_index,
        date_val=date_val,
        entry_time_utc=entry_time_utc,
        spot=spot,
        body=body,
        lower_wing=lower_wing,
        upper_wing=upper_wing,
        ladder_step=ladder_step,
        concurrent_at_entry=concurrent_at_entry,
        credit=round(credit, 2),
        credit_raw=round(credit_raw, 2)
    )
    fly.current_debit = round(credit_raw + 4.0 * config.slippage_per_leg_points, 2)
    return fly

def run_tripple_iron_fly_backtest(bars: List[PriceBar], config: TrippleIronFlyConfig) -> TrippleIronFlyReport:
    report = TrippleIronFlyReport()
    if not bars:
        return report

    spacing = config.strike_spacing if config.strike_spacing > 0 else 5.0
    trigger = config.move_trigger_points if config.move_trigger_points > 0 else spacing / 2.0
    wing = config.wing_width if config.wing_width > 0 else spacing * 4.0
    max_flies = max(1, config.max_concurrent_flies)

    increment = config.strike_increment if config.strike_increment > 0 else spacing
    def snap_to_grid(val: float) -> float:
        return round(val / increment) * increment

    rungs_per_inc = spacing / increment
    if abs(rungs_per_inc - round(rungs_per_inc)) > 1e-6:
        report.strike_grid_warning = (
            f"Strike Spacing {spacing:.1f} is not a whole multiple of the {increment:.1f} strike increment, "
            "so adjacent rungs snap onto the same listed strike. This ladder could not be traded as configured."
        )

    try:
        sh, sm = map(int, config.first_entry_time_et.strip().split(":"))
        first_entry_et = time(sh, sm)
    except Exception:
        first_entry_et = time(9, 45)

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

    fly_counter = 1
    all_trades: List[TrippleIronFlyTrade] = []

    sessions_traded = 0
    skipped_at_cap = 0
    rolled_at_cap = 0
    skipped_strike_collision = 0
    bail_exits = 0
    tp_exits = 0
    sl_exits = 0
    eod_exits = 0
    peak_concurrent = 0

    def close_fly(fly: ActiveFly, bar: PriceBar, spot: float, reason: str, active_flies_list: List[ActiveFly]):
        nonlocal equity
        active_flies_list.remove(fly)

        gross_pnl = round((fly.entry_credit_raw - fly.current_debit_raw) * 100.0 * config.quantity_per_fly, 2)
        slippage = round(config.slippage_per_leg_points * 8.0 * 100.0 * config.quantity_per_fly, 2)
        commission = round(config.commission_per_contract_per_leg * 8.0 * config.quantity_per_fly, 2)
        total_pnl = round(gross_pnl - slippage - commission, 2)

        credit_dollars = fly.entry_credit * 100.0 * config.quantity_per_fly
        ret_pct = (total_pnl / credit_dollars * 100.0) if credit_dollars > 0 else 0.0

        all_trades.append(TrippleIronFlyTrade(
            fly_index=fly.fly_index,
            date=datetime.combine(fly.date, time(0, 0)),
            entry_time_utc=fly.entry_time_utc,
            exit_time_utc=bar.time_utc,
            spot_at_entry=fly.spot_at_entry,
            spot_at_exit=spot,
            body_strike=fly.body_strike,
            lower_wing=fly.lower_wing,
            upper_wing=fly.upper_wing,
            ladder_step=fly.ladder_step,
            concurrent_at_entry=fly.concurrent_at_entry,
            entry_credit=fly.entry_credit,
            exit_debit=round(fly.current_debit, 2),
            gross_pnl=gross_pnl,
            slippage=slippage,
            commission=commission,
            realized_pnl=total_pnl,
            return_pct=round(ret_pct, 1),
            exit_reason=reason,
            bars_held=fly.bars_held
        ))
        equity += total_pnl
        report.equity_curve.append(EquityPoint(bar.time_utc, equity))

    for day_bars in daily_bars:
        if len(day_bars) < 5:
            continue

        session_date = day_bars[0][2].date()
        session_close = get_session_close_et(session_date)
        market_close_et = datetime.combine(session_date, session_close, tzinfo=EASTERN_TZ)

        eod_cutoff = (
            (datetime.combine(session_date, session_close) - timedelta(minutes=10)).time()
            if is_early_close(session_date)
            else time(15, 50, 0)
        )

        active_flies: List[ActiveFly] = []
        seed_body = 0.0
        seeded = False

        for b_idx, (_, bar, et) in enumerate(day_bars):
            spot = float(bar.close)
            minutes_to_close = max(1.0, (market_close_et - et).total_seconds() / 60.0)
            tau = minutes_to_close / (390.0 * 252.0)
            is_last_bar = (b_idx == len(day_bars) - 1)
            at_eod = (et.time() >= eod_cutoff or is_last_bar)

            # 0. Seed ladder at first entry time
            if not seeded and not at_eod and et.time() >= first_entry_et:
                seed_body = snap_to_grid(spot)
                seed_fly = open_fly(
                    fly_counter, session_date, bar.time_utc, spot, seed_body, 0, len(active_flies), tau, wing, config
                )
                fly_counter += 1
                active_flies.append(seed_fly)
                seeded = True
                sessions_traded += 1
                peak_concurrent = max(peak_concurrent, len(active_flies))
                continue

            if not seeded:
                continue

            # 1. Mark every open fly
            for f in active_flies:
                f.bars_held += 1
                f.current_debit_raw = iron_fly_value(spot, tau, f.body_strike, f.lower_wing, f.upper_wing, config.baseline_iv)
                f.current_debit = round(f.current_debit_raw + 4.0 * config.slippage_per_leg_points, 2)

            # 2. Exits
            closures = []
            for f in active_flies:
                if at_eod:
                    closures.append((f, "EOD Settlement Cutoff"))
                    continue

                target_debit = f.entry_credit * (1.0 - config.take_profit_percent / 100.0)
                if config.take_profit_percent > 0 and f.current_debit <= target_debit:
                    closures.append((f, f"Take Profit Hit (+{config.take_profit_percent:.1f}%)"))
                    continue

                if config.enable_stop_loss:
                    stop_debit = f.entry_credit * (1.0 + config.stop_loss_multiplier)
                    if f.current_debit >= stop_debit:
                        closures.append((f, f"Stop Loss Triggered ({config.stop_loss_multiplier:.1f}x Credit)"))

            for f, reason in closures:
                if reason.startswith("EOD"):
                    eod_exits += 1
                elif reason.startswith("Take"):
                    tp_exits += 1
                else:
                    sl_exits += 1
                close_fly(f, bar, spot, reason, active_flies)

            if at_eod:
                active_flies.clear()
                continue
            if not active_flies:
                continue

            # 3. Ladder entry check
            highest_body = max(f.body_strike for f in active_flies)
            lowest_body = min(f.body_strike for f in active_flies)

            new_body = None
            if spot >= highest_body + trigger:
                new_body = snap_to_grid(highest_body + spacing)
            elif spot <= lowest_body - trigger:
                new_body = snap_to_grid(lowest_body - spacing)

            if new_body is not None and any(abs(f.body_strike - new_body) < 1e-6 for f in active_flies):
                skipped_strike_collision += 1
                new_body = None

            if new_body is not None:
                if len(active_flies) >= max_flies:
                    if config.roll_at_max:
                        farthest = max(active_flies, key=lambda f: abs(f.body_strike - spot))
                        close_fly(farthest, bar, spot, f"Rolled Off Ladder (cap {max_flies})", active_flies)
                        rolled_at_cap += 1
                    else:
                        skipped_at_cap += 1
                        new_body = None

                if new_body is not None:
                    step = int(round((new_body - seed_body) / spacing))
                    new_f = open_fly(
                        fly_counter, session_date, bar.time_utc, spot, new_body, step, len(active_flies), tau, wing, config
                    )
                    fly_counter += 1
                    active_flies.append(new_f)
                    peak_concurrent = max(peak_concurrent, len(active_flies))

            # 4. Bail on opposite touch
            if config.bail_on_opposite_touch and len(active_flies) >= 2:
                highest_body = max(f.body_strike for f in active_flies)
                lowest_body = min(f.body_strike for f in active_flies)

                doomed = None
                bail_reason = ""
                if spot >= highest_body:
                    doomed = next((f for f in active_flies if abs(f.body_strike - lowest_body) < 1e-6), None)
                    bail_reason = f"Bailed Farthest (spot touched top body {highest_body:.1f})"
                elif spot <= lowest_body:
                    doomed = next((f for f in active_flies if abs(f.body_strike - highest_body) < 1e-6), None)
                    bail_reason = f"Bailed Farthest (spot touched bottom body {lowest_body:.1f})"

                if doomed:
                    bail_exits += 1
                    close_fly(doomed, bar, spot, bail_reason, active_flies)

    report.sessions_traded = sessions_traded
    report.skipped_at_cap = skipped_at_cap
    report.rolled_at_cap = rolled_at_cap
    report.skipped_strike_collision = skipped_strike_collision
    report.bail_on_opposite_touch_exits = bail_exits
    report.take_profit_exits = tp_exits
    report.stop_loss_exits = sl_exits
    report.eod_exits = eod_exits
    report.peak_concurrent_flies = peak_concurrent

    report.trades = sorted(all_trades, key=lambda t: t.entry_time_utc, reverse=True)
    report.total_trades = len(all_trades)
    report.win_count = sum(1 for t in all_trades if t.realized_pnl > 0)
    report.loss_count = sum(1 for t in all_trades if t.realized_pnl < 0)
    report.total_pnl = round(sum(t.realized_pnl for t in all_trades), 2)
    report.gross_pnl = round(sum(t.gross_pnl for t in all_trades), 2)
    report.total_slippage = round(sum(t.slippage for t in all_trades), 2)
    report.total_commissions = round(sum(t.commission for t in all_trades), 2)

    total_costs = report.total_slippage + report.total_commissions
    report.cost_drag_pct = round(total_costs / report.gross_pnl * 100.0, 1) if report.gross_pnl > 0 else 0.0
    report.return_on_capital_pct = round(report.total_pnl / config.initial_capital * 100.0, 2) if config.initial_capital > 0 else 0.0
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

    avg_credit = (sum(t.entry_credit for t in all_trades) / len(all_trades)) if all_trades else (wing * 0.4)
    report.max_risk_per_ladder = round(max(0.0, (wing - avg_credit) * 100.0 * config.quantity_per_fly * max_flies), 2)

    return report