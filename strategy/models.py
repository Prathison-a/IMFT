"""Data models for the BackTester trading application."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, List, Optional, Dict

class IndicatorLineStyle(str, Enum):
    SOLID = "Solid"
    DASHED = "Dashed"
    DOTTED = "Dotted"

class StrategyParameterType(str, Enum):
    INT = "Int"
    DOUBLE = "Double"
    BOOL = "Bool"
    STRING = "String"

@dataclass
class PriceBar:
    time_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    
    @property
    def total_trades(self) -> int:
        return self.num_trades

    @property
    def sharpe_ratio(self) -> float:
        return self.sharpe

    @property
    def net_profit(self) -> float:
        if self.equity_curve and len(self.equity_curve) > 1:
            return round(self.equity_curve[-1].equity - self.equity_curve[0].equity, 2)
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time": int(self.time_utc.timestamp()),
            "open": round(float(self.open), 4),
            "high": round(float(self.high), 4),
            "low": round(float(self.low), 4),
            "close": round(float(self.close), 4),
            "volume": round(float(self.volume), 2),
        }

@dataclass
class Trade:
    entry_time_utc: datetime
    exit_time_utc: datetime
    direction: int  # 1 for Long, -1 for Short
    entry_price: float
    exit_price: float
    shares: float
    pnl: float
    return_pct: float
    exit_reason: str
    bars_held: int

    
    @property
    def total_trades(self) -> int:
        return self.num_trades

    @property
    def sharpe_ratio(self) -> float:
        return self.sharpe

    @property
    def net_profit(self) -> float:
        if self.equity_curve and len(self.equity_curve) > 1:
            return round(self.equity_curve[-1].equity - self.equity_curve[0].equity, 2)
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_time": self.entry_time_utc.strftime("%Y-%m-%d %H:%M"),
            "exit_time": self.exit_time_utc.strftime("%Y-%m-%d %H:%M"),
            "side": "Long" if self.direction == 1 else "Short",
            "entry_price": round(self.entry_price, 2),
            "exit_price": round(self.exit_price, 2),
            "shares": round(self.shares, 2),
            "pnl": round(self.pnl, 2),
            "return_pct": round(self.return_pct * 100.0, 2),
            "exit_reason": self.exit_reason,
            "bars_held": self.bars_held,
        }

@dataclass
class EquityPoint:
    time_utc: datetime
    equity: float

    
    @property
    def total_trades(self) -> int:
        return self.num_trades

    @property
    def sharpe_ratio(self) -> float:
        return self.sharpe

    @property
    def net_profit(self) -> float:
        if self.equity_curve and len(self.equity_curve) > 1:
            return round(self.equity_curve[-1].equity - self.equity_curve[0].equity, 2)
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time": int(self.time_utc.timestamp()),
            "value": round(float(self.equity), 2),
        }

@dataclass
class IndicatorPoint:
    time_utc: datetime
    value: float

    
    @property
    def total_trades(self) -> int:
        return self.num_trades

    @property
    def sharpe_ratio(self) -> float:
        return self.sharpe

    @property
    def net_profit(self) -> float:
        if self.equity_curve and len(self.equity_curve) > 1:
            return round(self.equity_curve[-1].equity - self.equity_curve[0].equity, 2)
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time": int(self.time_utc.timestamp()),
            "value": round(float(self.value), 4),
        }

@dataclass
class IndicatorSeries:
    name: str
    color: str
    style: str = "Solid"
    points: List[IndicatorPoint] = field(default_factory=list)

    
    @property
    def total_trades(self) -> int:
        return self.num_trades

    @property
    def sharpe_ratio(self) -> float:
        return self.sharpe

    @property
    def net_profit(self) -> float:
        if self.equity_curve and len(self.equity_curve) > 1:
            return round(self.equity_curve[-1].equity - self.equity_curve[0].equity, 2)
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "color": self.color,
            "style": self.style,
            "points": [p.to_dict() for p in self.points],
        }

@dataclass
class BacktestResult:
    equity_curve: List[EquityPoint] = field(default_factory=list)
    trades: List[Trade] = field(default_factory=list)
    indicators: List[IndicatorSeries] = field(default_factory=list)
    sharpe: float = float('nan')
    cagr: float = float('nan')
    max_drawdown: float = float('nan')
    win_rate: float = 0.0
    num_trades: int = 0
    profit_factor: float = float('nan')

    
    @property
    def total_trades(self) -> int:
        return self.num_trades

    @property
    def sharpe_ratio(self) -> float:
        return self.sharpe

    @property
    def net_profit(self) -> float:
        if self.equity_curve and len(self.equity_curve) > 1:
            return round(self.equity_curve[-1].equity - self.equity_curve[0].equity, 2)
        return 0.0

    def metrics_dict(self) -> Dict[str, Any]:
        sharpe_val = None if (self.sharpe != self.sharpe) else round(self.sharpe, 2)
        cagr_val = None if (self.cagr != self.cagr) else round(self.cagr * 100.0, 2)
        dd_val = None if (self.max_drawdown != self.max_drawdown) else round(self.max_drawdown * 100.0, 2)
        pf_val = None
        if self.profit_factor == float('inf'):
            pf_val = 99.9
        elif not (self.profit_factor != self.profit_factor):
            pf_val = round(self.profit_factor, 2)

        return {
            "sharpe": sharpe_val,
            "cagr": cagr_val,
            "max_drawdown": dd_val,
            "win_rate": round(self.win_rate * 100.0, 1),
            "num_trades": self.num_trades,
            "profit_factor": pf_val,
            "net_profit": self.net_profit,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "equity_curve": [pt.to_dict() for pt in self.equity_curve],
            "trades": [t.to_dict() for t in self.trades],
            "indicators": [s.to_dict() for s in self.indicators],
            **self.metrics_dict(),
        }

@dataclass
class StrategyParameterDefinition:
    key: str
    label: str
    type: str  # 'int', 'double', 'bool', 'string'
    default_value: Any

    
    @property
    def total_trades(self) -> int:
        return self.num_trades

    @property
    def sharpe_ratio(self) -> float:
        return self.sharpe

    @property
    def net_profit(self) -> float:
        if self.equity_curve and len(self.equity_curve) > 1:
            return round(self.equity_curve[-1].equity - self.equity_curve[0].equity, 2)
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "default_value": self.default_value,
        }

def _fmt_strike(x: float) -> str:
    """At most one decimal, no trailing zero (C#'s "0.#"): 6600.0 -> "6600", 6612.5 -> "6612.5"."""
    return f"{x:.1f}".rstrip("0").rstrip(".")

# --- MEIC Models ---
@dataclass
class MeicBacktestConfig:
    symbol: str = "SPY"
    days: int = 30
    timeframe_minutes: int = 5
    schedule_times_et: str = "09:45, 10:45, 11:45, 13:00, 14:00"
    short_call_delta_target: float = 0.10
    short_put_delta_target: float = 0.10
    wing_width: float = 5.0
    quantity_per_tranche: int = 1
    take_profit_percent: float = 50.0
    stop_loss_multiplier: float = 1.0
    enable_adx_filter: bool = True
    max_adx_threshold: float = 25.0
    adx_period: int = 14
    enable_ci_filter: bool = False
    max_ci_threshold: float = 25.0
    require_ci_exhaustion: bool = True
    initial_capital: float = 50000.0
    baseline_iv: float = 0.16
    force_refresh_cache: bool = False

@dataclass
class MeicSimulatedTrade:
    tranche_index: int
    date: datetime
    entry_time_utc: datetime
    exit_time_utc: datetime
    scheduled_time_et: str
    spot_at_entry: float
    spot_at_exit: float
    short_put_strike: float
    long_put_strike: float
    short_call_strike: float
    long_call_strike: float
    entry_credit: float
    exit_debit: float
    realized_pnl: float
    return_pct: float
    exit_reason: str
    bars_held: int

    @property
    def is_win(self) -> bool:
        return self.realized_pnl > 0

    
    @property
    def total_trades(self) -> int:
        return self.num_trades

    @property
    def sharpe_ratio(self) -> float:
        return self.sharpe

    @property
    def net_profit(self) -> float:
        if self.equity_curve and len(self.equity_curve) > 1:
            return round(self.equity_curve[-1].equity - self.equity_curve[0].equity, 2)
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tranche_index": self.tranche_index,
            "date": self.date.strftime("%Y-%m-%d"),
            "entry_time": self.entry_time_utc.strftime("%Y-%m-%d %H:%M"),
            "exit_time": self.exit_time_utc.strftime("%Y-%m-%d %H:%M"),
            "scheduled_time_et": self.scheduled_time_et,
            "spot_at_entry": round(self.spot_at_entry, 2),
            "spot_at_exit": round(self.spot_at_exit, 2),
            "short_put_strike": self.short_put_strike,
            "long_put_strike": self.long_put_strike,
            "short_call_strike": self.short_call_strike,
            "long_call_strike": self.long_call_strike,
            "strikes_summary": f"P: {_fmt_strike(self.long_put_strike)}/{_fmt_strike(self.short_put_strike)} | C: {_fmt_strike(self.short_call_strike)}/{_fmt_strike(self.long_call_strike)}",
            "entry_credit": round(self.entry_credit, 2),
            "exit_debit": round(self.exit_debit, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "return_pct": round(self.return_pct, 1),
            "exit_reason": self.exit_reason,
            "bars_held": self.bars_held,
            "is_win": self.is_win,
        }

@dataclass
class MeicBacktestReport:
    @property
    def net_pnl(self) -> float:
        return self.total_pnl

    total_pnl: float = 0.0
    return_on_capital_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    avg_win_pnl: float = 0.0
    avg_loss_pnl: float = 0.0
    scheduled_windows_evaluated: int = 0
    skipped_by_adx_filter: int = 0
    skipped_by_ci_filter: int = 0
    skipped_by_invalid_strikes: int = 0
    sharpe_ratio: float = 0.0
    equity_curve: List[EquityPoint] = field(default_factory=list)
    trades: List[MeicSimulatedTrade] = field(default_factory=list)

    


    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_pnl": round(self.total_pnl, 2),
            "return_on_capital_pct": round(self.return_on_capital_pct, 2),
            "win_rate": round(self.win_rate, 1),
            "profit_factor": round(self.profit_factor, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "total_trades": self.total_trades,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "avg_win_pnl": round(self.avg_win_pnl, 2),
            "avg_loss_pnl": round(self.avg_loss_pnl, 2),
            "scheduled_windows_evaluated": self.scheduled_windows_evaluated,
            "skipped_by_adx_filter": self.skipped_by_adx_filter,
            "skipped_by_ci_filter": self.skipped_by_ci_filter,
            "skipped_by_invalid_strikes": self.skipped_by_invalid_strikes,
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "equity_curve": [pt.to_dict() for pt in self.equity_curve],
            "trades": [t.to_dict() for t in self.trades],
        }

# --- Tripple Iron Fly Models ---
@dataclass
class TrippleIronFlyConfig:
    symbol: str = "$SPX"
    days: int = 30
    timeframe_minutes: int = 5
    first_entry_time_et: str = "09:45"
    strike_spacing: float = 5.0
    strike_increment: float = 5.0
    move_trigger_points: float = 2.5
    wing_width: float = 20.0
    max_concurrent_flies: int = 3
    roll_at_max: bool = False
    bail_on_opposite_touch: bool = True
    quantity_per_fly: int = 1
    take_profit_percent: float = 25.0
    enable_stop_loss: bool = True
    stop_loss_multiplier: float = 1.0
    commission_per_contract_per_leg: float = 0.65
    slippage_per_leg_points: float = 0.05
    initial_capital: float = 100000.0
    baseline_iv: float = 0.16
    force_refresh_cache: bool = False

@dataclass
class TrippleIronFlyTrade:
    fly_index: int
    date: datetime
    entry_time_utc: datetime
    exit_time_utc: datetime
    spot_at_entry: float
    spot_at_exit: float
    body_strike: float
    lower_wing: float
    upper_wing: float
    ladder_step: int
    concurrent_at_entry: int
    entry_credit: float
    exit_debit: float
    gross_pnl: float
    slippage: float
    commission: float
    realized_pnl: float
    return_pct: float
    exit_reason: str
    bars_held: int

    @property
    def is_win(self) -> bool:
        return self.realized_pnl > 0

    @property
    def total_cost(self) -> float:
        return self.slippage + self.commission

    


    def to_dict(self) -> Dict[str, Any]:
        return {
            "fly_index": self.fly_index,
            "date": self.date.strftime("%Y-%m-%d"),
            "entry_time": self.entry_time_utc.strftime("%Y-%m-%d %H:%M"),
            "exit_time": self.exit_time_utc.strftime("%Y-%m-%d %H:%M"),
            "spot_at_entry": round(self.spot_at_entry, 2),
            "spot_at_exit": round(self.spot_at_exit, 2),
            "body_strike": self.body_strike,
            "lower_wing": self.lower_wing,
            "upper_wing": self.upper_wing,
            "strikes_summary": f"{_fmt_strike(self.lower_wing)} / [{_fmt_strike(self.body_strike)}] / {_fmt_strike(self.upper_wing)}",
            "ladder_step": self.ladder_step,
            "ladder_step_str": "seed" if self.ladder_step == 0 else (f"+{self.ladder_step}" if self.ladder_step > 0 else str(self.ladder_step)),
            "concurrent_at_entry": self.concurrent_at_entry,
            "entry_credit": round(self.entry_credit, 2),
            "exit_debit": round(self.exit_debit, 2),
            "gross_pnl": round(self.gross_pnl, 2),
            "slippage": round(self.slippage, 2),
            "commission": round(self.commission, 2),
            "total_cost": round(self.total_cost, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "return_pct": round(self.return_pct, 1),
            "exit_reason": self.exit_reason,
            "bars_held": self.bars_held,
            "is_win": self.is_win,
        }

@dataclass
class TrippleIronFlyReport:
    @property
    def net_pnl(self) -> float:
        return self.total_pnl

    total_pnl: float = 0.0
    gross_pnl: float = 0.0
    total_slippage: float = 0.0
    total_commissions: float = 0.0
    cost_drag_pct: float = 0.0
    return_on_capital_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    avg_win_pnl: float = 0.0
    avg_loss_pnl: float = 0.0
    sessions_traded: int = 0
    skipped_at_cap: int = 0
    skipped_strike_collision: int = 0
    strike_grid_warning: Optional[str] = None
    rolled_at_cap: int = 0
    bail_on_opposite_touch_exits: int = 0
    take_profit_exits: int = 0
    stop_loss_exits: int = 0
    eod_exits: int = 0
    peak_concurrent_flies: int = 0
    max_risk_per_ladder: float = 0.0
    equity_curve: List[EquityPoint] = field(default_factory=list)
    trades: List[TrippleIronFlyTrade] = field(default_factory=list)

    


    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_pnl": round(self.total_pnl, 2),
            "gross_pnl": round(self.gross_pnl, 2),
            "total_slippage": round(self.total_slippage, 2),
            "total_commissions": round(self.total_commissions, 2),
            "cost_drag_pct": round(self.cost_drag_pct, 1),
            "return_on_capital_pct": round(self.return_on_capital_pct, 2),
            "win_rate": round(self.win_rate, 1),
            "profit_factor": round(self.profit_factor, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "total_trades": self.total_trades,
            "win_count": self.win_count,
            "loss_count": self.loss_count,
            "avg_win_pnl": round(self.avg_win_pnl, 2),
            "avg_loss_pnl": round(self.avg_loss_pnl, 2),
            "sessions_traded": self.sessions_traded,
            "skipped_at_cap": self.skipped_at_cap,
            "skipped_strike_collision": self.skipped_strike_collision,
            "strike_grid_warning": self.strike_grid_warning,
            "rolled_at_cap": self.rolled_at_cap,
            "bail_on_opposite_touch_exits": self.bail_on_opposite_touch_exits,
            "take_profit_exits": self.take_profit_exits,
            "stop_loss_exits": self.stop_loss_exits,
            "eod_exits": self.eod_exits,
            "peak_concurrent_flies": self.peak_concurrent_flies,
            "max_risk_per_ladder": round(self.max_risk_per_ladder, 2),
            "equity_curve": [pt.to_dict() for pt in self.equity_curve],
            "trades": [t.to_dict() for t in self.trades],
        }