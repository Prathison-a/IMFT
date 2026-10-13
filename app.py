from dotenv import load_dotenv
load_dotenv()
"""Flask web server and API for BackTester Python edition."""
import os, sys, math, json
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, jsonify, send_file, Response
import io, csv

# Ensure current directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.alpaca_client import AlpacaDataService, AlpacaRateLimitException
from services.bar_cache import BarCache
from services.api_tracker import ApiCallTracker
from services.market_calendar import is_trading_day, is_early_close, get_session_close_et
from strategy.models import (
    PriceBar, MeicBacktestConfig, TrippleIronFlyConfig
)
from strategy.strategies import StrategyRegistry
from strategy.math_utils import split_by_date
from strategy.optimizer import StrategyOptimizer
from strategy.meic_backtester import run_meic_backtest
from strategy.tripple_iron_fly import run_tripple_iron_fly_backtest

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config['JSON_SORT_KEYS'] = False

tracker = ApiCallTracker()
cache = BarCache()
data_service = AlpacaDataService(tracker=tracker, cache=cache)

# In-memory store for recently fetched data (for CSV export)
_last_fetched_bars = []
_last_fetched_symbol = "SPY"

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/strategies", methods=["GET"])
def get_strategies():
    strategies_data = [s.to_dict() for s in StrategyRegistry.ALL]
    return jsonify(strategies_data)

@app.route("/api/data/fetch", methods=["POST"])
def fetch_data():
    global _last_fetched_bars, _last_fetched_symbol
    data = request.get_json() or {}
    symbol = data.get("symbol", "SPY").strip().upper()
    minutes = int(data.get("minutes", 15))
    days = int(data.get("days", 5))
    force_refresh = bool(data.get("force_refresh", False))

    if not symbol:
        return jsonify({"error": "Symbol is required"}), 400
    if minutes <= 0 or days <= 0:
        return jsonify({"error": "Minutes and days must be positive integers"}), 400

    end_utc = datetime.now(timezone.utc) - timedelta(minutes=16)
    start_utc = end_utc - timedelta(days=days)

    progress_logs = []
    def log_progress(msg):
        progress_logs.append(f"{datetime.now().strftime('%H:%M:%S')}  {msg}")

    try:
        bars = data_service.get_bars(
            symbol=symbol,
            start_utc=start_utc,
            end_utc=end_utc,
            timeframe_val=minutes,
            timeframe_unit="Min",
            progress_callback=log_progress,
            force_refresh=force_refresh
        )

        _last_fetched_bars = bars
        _last_fetched_symbol = symbol

        chart_data = [b.to_dict() for b in bars]
        return jsonify({
            "symbol": symbol,
            "bars_count": len(bars),
            "chart_data": chart_data,
            "logs": progress_logs
        })
    except AlpacaRateLimitException as ex:
        return jsonify({"error": f"Rate limit hit: {str(ex)}", "rate_limited": True}), 429
    except Exception as ex:
        return jsonify({"error": f"Error fetching bars: {str(ex)}"}), 500

@app.route("/api/data/export_csv", methods=["GET"])
def export_csv():
    global _last_fetched_bars, _last_fetched_symbol
    if not _last_fetched_bars:
        return "No data available to export", 400

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["time_utc", "open", "high", "low", "close", "volume"])
    for b in _last_fetched_bars:
        writer.writerow([
            b.time_utc.strftime("%Y-%m-%d %H:%M:%S"),
            b.open, b.high, b.low, b.close, b.volume
        ])

    output.seek(0)
    filename = f"{_last_fetched_symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.route("/api/backtest/run", methods=["POST"])
def run_backtest():
    data = request.get_json() or {}
    strategy_name = data.get("strategy_name")
    symbol = data.get("symbol", "SPY").strip().upper()
    exec_amount = int(data.get("exec_amount", 15))
    exec_unit = data.get("exec_unit", "Min")
    anchor_amount = int(data.get("anchor_amount", 1))
    anchor_unit = data.get("anchor_unit", "Hour")
    lookback_days = int(data.get("lookback_days", 60))
    initial_equity = float(data.get("initial_equity", 100000.0))
    user_params = data.get("parameters", {})
    force_refresh = bool(data.get("force_refresh", False))
    split_date_str = str(data.get("split_date") or "").strip()

    strategy = StrategyRegistry.get_by_name(strategy_name)
    if not strategy:
        return jsonify({"error": f"Strategy '{strategy_name}' not found"}), 404

    split_date = None
    if split_date_str:
        try:
            split_date = datetime.strptime(split_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return jsonify({"error": f"Invalid split date '{split_date_str}' (expected YYYY-MM-DD)"}), 400

    end_utc = datetime.now(timezone.utc) - timedelta(minutes=16)
    start_utc = end_utc - timedelta(days=lookback_days)

    progress_logs = []
    def log_progress(msg):
        progress_logs.append(f"{datetime.now().strftime('%H:%M:%S')}  {msg}")

    try:
        log_progress(f"Fetching {symbol} execution bars ({exec_amount}{exec_unit})...")
        exec_bars = data_service.get_bars(
            symbol=symbol,
            start_utc=start_utc,
            end_utc=end_utc,
            timeframe_val=exec_amount,
            timeframe_unit=exec_unit,
            progress_callback=log_progress,
            force_refresh=force_refresh
        )
        log_progress(f"Got {len(exec_bars)} execution bars.")

        anchor_bars = []
        if strategy.requires_anchor_timeframe:
            log_progress(f"Fetching {symbol} anchor bars ({anchor_amount}{anchor_unit})...")
            anchor_bars = data_service.get_bars(
                symbol=symbol,
                start_utc=start_utc,
                end_utc=end_utc,
                timeframe_val=anchor_amount,
                timeframe_unit=anchor_unit,
                progress_callback=log_progress,
                force_refresh=force_refresh
            )
            log_progress(f"Got {len(anchor_bars)} anchor bars.")
        else:
            log_progress(f"{strategy.name} does not use an anchor timeframe -- skipped that fetch.")

        if len(exec_bars) < 20 or (strategy.requires_anchor_timeframe and len(anchor_bars) < 20):
            return jsonify({
                "error": "Not enough bars returned to run backtest. Check symbol or lookback range.",
                "logs": progress_logs
            }), 400

        log_progress(f"Running strategy '{strategy.name}'...")
        result = strategy.run(exec_bars, anchor_bars, user_params, initial_equity)
        log_progress(f"Backtest complete: {result.num_trades} trades executed.")

        # Build trade chart markers
        markers = []
        for t in result.trades:
            is_long = (t.direction == 1)
            entry_ts = int(t.entry_time_utc.timestamp())
            exit_ts = int(t.exit_time_utc.timestamp())
            markers.append({
                "time": entry_ts,
                "position": "belowBar" if is_long else "aboveBar",
                "color": "#10B981" if is_long else "#F43F5E",
                "shape": "arrowUp" if is_long else "arrowDown",
                "text": "Buy" if is_long else "Sell",
            })
            markers.append({
                "time": exit_ts,
                "position": "aboveBar" if is_long else "belowBar",
                "color": "#10B981" if t.pnl > 0 else "#F43F5E",
                "shape": "circle",
                "text": t.exit_reason,
            })

        periods = []
        if split_date is not None:
            periods = [{"label": label, **r.metrics_dict()} for label, r in split_by_date(result, split_date)]

        return jsonify({
            "result": result.to_dict(),
            "periods": periods,
            "candles": [b.to_dict() for b in exec_bars],
            "markers": markers,
            "logs": progress_logs
        })
    except AlpacaRateLimitException as ex:
        return jsonify({"error": f"Rate limit hit: {str(ex)}", "rate_limited": True, "logs": progress_logs}), 429
    except Exception as ex:
        return jsonify({"error": f"Backtest error: {str(ex)}", "logs": progress_logs}), 500

@app.route("/api/backtest/optimize", methods=["POST"])
def optimize_backtest():
    data = request.get_json() or {}
    strategy_name = data.get("strategy_name")
    symbol = data.get("symbol", "SPY").strip().upper()
    exec_amount = int(data.get("exec_amount", 15))
    exec_unit = data.get("exec_unit", "Min")
    anchor_amount = int(data.get("anchor_amount", 1))
    anchor_unit = data.get("anchor_unit", "Hour")
    lookback_days = int(data.get("lookback_days", 60))
    initial_equity = float(data.get("initial_equity", 100000.0))
    trials_count = int(data.get("trials", 30))
    target_sharpe = float(data.get("target_sharpe", 1.0))
    min_trades = int(data.get("min_trades", 10))
    force_refresh = bool(data.get("force_refresh", False))

    strategy = StrategyRegistry.get_by_name(strategy_name)
    if not strategy:
        return jsonify({"error": f"Strategy '{strategy_name}' not found"}), 404

    end_utc = datetime.now(timezone.utc) - timedelta(minutes=16)
    start_utc = end_utc - timedelta(days=lookback_days)

    try:
        exec_bars = data_service.get_bars(symbol, start_utc, end_utc, exec_amount, exec_unit, force_refresh=force_refresh)
        anchor_bars = []
        if strategy.requires_anchor_timeframe:
            anchor_bars = data_service.get_bars(symbol, start_utc, end_utc, anchor_amount, anchor_unit, force_refresh=force_refresh)

        trials = StrategyOptimizer.search(strategy, exec_bars, anchor_bars, initial_equity, trials=trials_count)
        best = StrategyOptimizer.pick_best(trials, target_sharpe=target_sharpe, min_trades=min_trades)

        trials_summary = []
        for i, t in enumerate(trials):
            trials_summary.append({
                "trial_index": i + 1,
                "params": t.params,
                "sharpe": round(t.result.sharpe, 2) if not math.isnan(t.result.sharpe) else None,
                "cagr": round(t.result.cagr * 100, 2) if not math.isnan(t.result.cagr) else None,
                "max_drawdown": round(t.result.max_drawdown * 100, 2) if not math.isnan(t.result.max_drawdown) else None,
                "win_rate": round(t.result.win_rate * 100, 1),
                "trades": t.result.num_trades,
                "profit_factor": round(t.result.profit_factor, 2) if not (math.isnan(t.result.profit_factor) or t.result.profit_factor == float('inf')) else 99.9,
            })

        return jsonify({
            "trials_run": len(trials),
            "best": best.to_dict() if best else None,
            "trials": sorted(trials_summary, key=lambda x: (x["sharpe"] or -999), reverse=True)
        })
    except Exception as ex:
        return jsonify({"error": f"Optimization error: {str(ex)}"}), 500

@app.route("/api/meic/run", methods=["POST"])
def run_meic():
    data = request.get_json() or {}
    config = MeicBacktestConfig(
        symbol=data.get("symbol", "SPY").strip().upper(),
        days=int(data.get("days", 30)),
        timeframe_minutes=int(data.get("timeframe_minutes", 5)),
        schedule_times_et=str(data.get("schedule_times_et", "09:45, 10:45, 11:45, 13:00, 14:00")),
        short_call_delta_target=float(data.get("short_call_delta_target", 0.10)),
        short_put_delta_target=float(data.get("short_put_delta_target", 0.10)),
        wing_width=float(data.get("wing_width", 5.0)),
        quantity_per_tranche=int(data.get("quantity_per_tranche", 1)),
        take_profit_percent=float(data.get("take_profit_percent", 50.0)),
        stop_loss_multiplier=float(data.get("stop_loss_multiplier", 1.0)),
        enable_adx_filter=bool(data.get("enable_adx_filter", True)),
        max_adx_threshold=float(data.get("max_adx_threshold", 25.0)),
        adx_period=int(data.get("adx_period", 14)),
        enable_ci_filter=bool(data.get("enable_ci_filter", False)),
        max_ci_threshold=float(data.get("max_ci_threshold", 25.0)),
        require_ci_exhaustion=bool(data.get("require_ci_exhaustion", True)),
        initial_capital=float(data.get("initial_capital", 50000.0)),
        baseline_iv=float(data.get("baseline_iv", 0.16)),
        force_refresh_cache=bool(data.get("force_refresh", False)),
    )

    end_utc = datetime.now(timezone.utc) - timedelta(minutes=16)
    start_utc = end_utc - timedelta(days=config.days)

    progress_logs = []
    def log_progress(msg):
        progress_logs.append(f"{datetime.now().strftime('%H:%M:%S')}  {msg}")

    try:
        log_progress(f"Fetching {config.symbol} {config.timeframe_minutes}m bars for MEIC backtest...")
        bars = data_service.get_bars(
            symbol=config.symbol,
            start_utc=start_utc,
            end_utc=end_utc,
            timeframe_val=config.timeframe_minutes,
            timeframe_unit="Min",
            progress_callback=log_progress,
            force_refresh=config.force_refresh_cache
        )
        log_progress(f"Got {len(bars)} bars. Running MEIC 0DTE Iron Condor simulation...")
        report = run_meic_backtest(bars, config)
        log_progress(f"MEIC Backtest complete: {report.total_trades} trades, Total PnL: ${report.total_pnl:,.2f}")

        return jsonify({
            "report": report.to_dict(),
            "logs": progress_logs
        })
    except Exception as ex:
        return jsonify({"error": f"MEIC Error: {str(ex)}", "logs": progress_logs}), 500

@app.route("/api/tripple_iron_fly/run", methods=["POST"])
def run_tripple_iron_fly():
    data = request.get_json() or {}
    config = TrippleIronFlyConfig(
        symbol=data.get("symbol", "$SPX").strip().upper(),
        days=int(data.get("days", 30)),
        timeframe_minutes=int(data.get("timeframe_minutes", 5)),
        first_entry_time_et=str(data.get("first_entry_time_et", "09:45")),
        strike_spacing=float(data.get("strike_spacing", 5.0)),
        strike_increment=float(data.get("strike_increment", 5.0)),
        move_trigger_points=float(data.get("move_trigger_points", 2.5)),
        wing_width=float(data.get("wing_width", 20.0)),
        max_concurrent_flies=int(data.get("max_concurrent_flies", 3)),
        roll_at_max=bool(data.get("roll_at_max", False)),
        bail_on_opposite_touch=bool(data.get("bail_on_opposite_touch", True)),
        quantity_per_fly=int(data.get("quantity_per_fly", 1)),
        take_profit_percent=float(data.get("take_profit_percent", 25.0)),
        enable_stop_loss=bool(data.get("enable_stop_loss", True)),
        stop_loss_multiplier=float(data.get("stop_loss_multiplier", 1.0)),
        commission_per_contract_per_leg=float(data.get("commission_per_contract_per_leg", 0.65)),
        slippage_per_leg_points=float(data.get("slippage_per_leg_points", 0.05)),
        initial_capital=float(data.get("initial_capital", 100000.0)),
        baseline_iv=float(data.get("baseline_iv", 0.16)),
        force_refresh_cache=bool(data.get("force_refresh", False)),
    )

    # Note: For $SPX, if Alpaca free-tier doesn't have index bars directly, fallback symbol is SPY scaled or ETF
    fetch_symbol = config.symbol
    if fetch_symbol.startswith("$"):
        fetch_symbol = fetch_symbol.lstrip("$")

    end_utc = datetime.now(timezone.utc) - timedelta(minutes=16)
    start_utc = end_utc - timedelta(days=config.days)

    progress_logs = []
    def log_progress(msg):
        progress_logs.append(f"{datetime.now().strftime('%H:%M:%S')}  {msg}")

    try:
        log_progress(f"Fetching {fetch_symbol} {config.timeframe_minutes}m bars for TrippleIronFly ladder...")
        bars = data_service.get_bars(
            symbol=fetch_symbol,
            start_utc=start_utc,
            end_utc=end_utc,
            timeframe_val=config.timeframe_minutes,
            timeframe_unit="Min",
            progress_callback=log_progress,
            force_refresh=config.force_refresh_cache
        )
        log_progress(f"Got {len(bars)} bars. Running SPX IF MAX 3 ladder simulation...")
        report = run_tripple_iron_fly_backtest(bars, config)
        log_progress(f"TrippleIronFly simulation complete: {report.total_trades} trades, Net PnL: ${report.total_pnl:,.2f}")

        return jsonify({
            "report": report.to_dict(),
            "logs": progress_logs
        })
    except Exception as ex:
        return jsonify({"error": f"TrippleIronFly Error: {str(ex)}", "logs": progress_logs}), 500

@app.route("/api/tracker/stats", methods=["GET"])
def tracker_stats():
    return jsonify({
        "count_today": tracker.count_today(),
        "count_last_hour": tracker.count_last_hour(),
        "count_last_minute": tracker.count_last_minute(),
        "target_rpm": data_service.target_requests_per_minute,
        "hourly_today": tracker.counts_by_hour_today(),
        "daily_history": tracker.counts_by_day(30),
        "recent_calls": tracker.recent_calls(100)
    })

if __name__ == "__main__":
    import webbrowser
    port = 5000
    print(f"Starting BackTester Python server on http://127.0.0.1:{port}...")
    try:
        webbrowser.open(f"http://127.0.0.1:{port}")
    except Exception:
        pass
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)