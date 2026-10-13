# BackTester - Python Edition

A high-performance quantitative trading backtester, technical analysis visualizer, and options strategy modeling suite ported from C# WPF `BackTester` to Python.

## Overview & Feature Parity

The Python edition mirrors the complete architecture and analytical rigor of the C# `BackTester` application:

1. **Data Viewer & Lightweight Candlestick Charts**
   - Interactive TradingView Lightweight Charts with dynamic dark/light theme switching.
   - Real-time technical indicators overlay:
     - Supertrend MTF (multi-timeframe with higher-timeframe ATR step lines)
     - ADX (Average Directional Index with +DI / -DI directional movement)
     - Chandelier Exit (ATR trailing stop)
     - Donchian Channel
     - ATR Dynamic Stop
     - SMA, EMA, Hull Moving Average (HMA)
     - Relative Strength Index (RSI)
     - Moving Average Convergence Divergence (MACD)
     - Bollinger Bands & Keltner Channels
     - TTM Squeeze Momentum
     - Volume-Weighted Average Price (VWAP)
     - True Momentum Oscillator (TMO)
     - Trend Flow & Composite Index
   - Volume histogram sub-chart with directional coloring.
   - Comprehensive bar data grid with export to CSV.

2. **Multi-Strategy Backtester**
   - Fast backtesting engine supporting all **19 trading strategies**:
     - Supertrend MTF
     - Donchian Breakout
     - ATR Dynamic Stop Reversal
     - Chandelier Trend Following
     - EMA Cross (9/21, 50/200, customizable)
     - RSI Mean Reversion
     - MACD Histogram Momentum
     - Bollinger Bands Mean Reversion
     - Keltner Channel Breakout
     - Hull MA Directional
     - TTM Squeeze Breakout
     - VWAP Mean Reversion
     - TMO (True Momentum)
     - Trend Flow
     - Composite Index
     - Momentum Trend
     - Triple EMA (TEMA)
     - Stochastic RSI
     - Williams %R Reversal
   - Execution metrics: Net Profit, Sharpe Ratio (annualized), CAGR %, Max Drawdown %, Win Rate %, Profit Factor, Average Win/Loss, Total Trades.
   - Visual Equity Curve & Interactive Trade Log table.
   - **Monte Carlo Parameter Optimizer**: randomized parameter exploration ranked by risk-adjusted performance (`Sharpe * sqrt(trades)`).

3. **MEIC 0DTE Backtester (Iron Condor Option Strategy)**
   - Multi-tranche intraday 0DTE Iron Condor strategy simulation.
   - Analytical Black-Scholes pricing engine with real-time implied volatility solver.
   - Dynamic delta strike finder for Call and Put credit spreads.
   - Intraday tranche laddering (9:45, 10:15, 10:45, 11:15, etc.).
   - Multi-layer stop-loss criteria:
     - Individual leg/spread stop loss multiplier (e.g. 200% - 300% of initial credit)
     - Maximum daily portfolio stop loss
     - Optional ADX volatility/trend filter to halt entries during strong directional trends.
   - Daily performance metrics and detailed per-tranche trade logs.

4. **TrippleIronFly SPX Ladder Backtester**
   - SPX Index Iron Fly laddering strategy (`SPX IF MAX 3`).
   - Strike grid snapping (snaps to closest 5-point strike interval).
   - Dynamic strike positioning at trigger bar prices.
   - 4-leg fee and slippage modeling.
   - Max 3 concurrent active iron flies.
   - Opposite-touch bailing (exits when price crosses the opposite strike barrier).
   - Roll-at-max logic (re-deploys capital into a fresh fly upon reaching max profit threshold).

5. **API Monitor & Free-Tier Rate Limiter**
   - Transparent client-side pacing (~150 requests/min ceiling, 400ms inter-call spacing) conforming to Alpaca free-tier limits.
   - Local disk cache located in `%LOCALAPPDATA%\BackTester\bar_cache` to eliminate redundant API requests.
   - Thread-safe persistent logging to `%LOCALAPPDATA%\BackTester\api_calls.log`.
   - Real-time API consumption metrics (requests in last minute, requests today, total requests, active rate limit status).

6. **NYSE Session Calendar & Schedule**
   - Accurate market hour boundaries (9:30 AM to 4:00 PM Eastern).
   - Full NYSE holiday calendar calculation (New Year's Day, Martin Luther King Jr. Day, Washington's Birthday, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas).
   - Early close detection (1:00 PM Eastern on Thanksgiving Eve, Black Friday, Christmas Eve, etc.).

---

## Directory Structure

```
BackTesterPython/
├── app.py                      # Flask backend API server & REST controllers
├── main.py                     # Primary launcher (Waitress/Flask server + auto-opens browser)
├── desktop_app.py              # Optional desktop GUI window (PySide6 / PyQt6 / pywebview)
├── run.bat                     # Windows one-click launcher
├── requirements.txt            # Python dependencies
├── README.md                   # Documentation
├── services/                   # Data and market services
│   ├── alpaca_client.py        # Alpaca Market Data v2 client with rate limiter & caching
│   ├── api_tracker.py          # API request tracking and persistent disk logging
│   ├── bar_cache.py            # Local file-based caching for historical bars
│   └── market_calendar.py      # NYSE market session, holiday, and early close calculator
├── strategy/                   # Quantitative strategy and calculation engines
│   ├── models.py               # Core data structures (PriceBar, Trade, BacktestResult, etc.)
│   ├── math_utils.py           # Sharpe, CAGR, Max Drawdown, Equity resampling
│   ├── indicators.py           # Technical indicators (Supertrend, ADX, Chandelier, etc.)
│   ├── percent_stop_backtester.py # Core trade execution engine
│   ├── strategies.py           # All 19 ported strategies
│   ├── optimizer.py            # Monte Carlo parameter optimization
│   ├── meic_backtester.py      # MEIC 0DTE Iron Condor simulation
│   └── tripple_iron_fly.py     # TrippleIronFly SPX laddering simulation
├── templates/
│   └── index.html              # Single Page Application HTML shell
└── static/
    ├── style.css               # Dark/Light WPF aesthetic styling
    └── app.js                  # Frontend controller & TradingView chart integration
```

---

## Getting Started

### Prerequisites
- Python 3.9 or higher (Python 3.11+ recommended)
- Dependencies installed via pip:
  ```bash
  pip install -r requirements.txt
  ```

### Running the Application
Double-click `run.bat` or run:
```bash
python main.py
```
This will start the local server on `http://127.0.0.1:5000` and automatically open your default web browser.

### Native Desktop Mode
If you prefer a self-contained desktop window:
```bash
python desktop_app.py
```
*(Requires PySide6, PyQt6, or pywebview)*

---

## API Keys Configuration
You can supply your Alpaca API credentials directly in the **Data Viewer** or **API Monitor** tab in the UI, or export them as environment variables:
```bash
set APCA_API_KEY_ID=your_key_here
set APCA_API_SECRET_KEY=your_secret_here
```
Free-tier accounts will operate seamlessly with the built-in rate pacing and local bar caching.
