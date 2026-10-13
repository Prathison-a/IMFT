/* ===================================================================
   BackTester Python Edition - Client Logic & TradingView Charts
   =================================================================== */

document.addEventListener("DOMContentLoaded", () => {
    // --- Global State & Palette ---
    let isDark = true;
    let registeredStrategies = [];

    const PALETTE = {
        dark: {
            bg: '#161B26',
            text: '#94A3B8',
            grid: 'rgba(38, 50, 72, 0.6)',
            border: '#263248',
            up: '#10B981',
            down: '#F43F5E',
            line: '#38BDF8',
            equity: '#10B981',
            bar: '#38BDF8'
        },
        light: {
            bg: '#FFFFFF',
            text: '#64748B',
            grid: 'rgba(226, 232, 240, 0.8)',
            border: '#E2E8F0',
            up: '#16A34A',
            down: '#E11D48',
            line: '#0284C7',
            equity: '#16A34A',
            bar: '#0284C7'
        }
    };

    function currentPalette() {
        return isDark ? PALETTE.dark : PALETTE.light;
    }

    // --- Chart Registry ---
    const charts = {
        dataViewer: { chart: null, candle: null, volume: null },
        btPrice: { chart: null, candle: null, indicators: [] },
        btEquity: { chart: null, line: null },
        meicEquity: { chart: null, line: null },
        ironflyEquity: { chart: null, line: null },
        apiHourly: { chart: null, histogram: null },
        apiDaily: { chart: null, histogram: null },
    };

    function createTvChart(containerId, height = 480) {
        const container = document.getElementById(containerId);
        if (!container) return null;
        container.innerHTML = "";
        const p = currentPalette();

        const chart = LightweightCharts.createChart(container, {
            width: container.clientWidth || 800,
            height: height,
            layout: {
                background: { color: p.bg },
                textColor: p.text,
                fontFamily: "'Inter', sans-serif"
            },
            grid: {
                vertLines: { color: p.grid },
                horzLines: { color: p.grid },
            },
            timeScale: {
                timeVisible: true,
                secondsVisible: false,
                borderColor: p.border,
            },
            rightPriceScale: {
                borderColor: p.border,
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
            }
        });

        // ResizeObserver for reliable container sizing
        new ResizeObserver(() => {
            if (container.clientWidth > 0) {
                chart.applyOptions({ width: container.clientWidth });
            }
        }).observe(container);

        return chart;
    }

    function applyThemeToCharts() {
        const p = currentPalette();
        Object.values(charts).forEach(item => {
            if (item.chart) {
                item.chart.applyOptions({
                    layout: { background: { color: p.bg }, textColor: p.text },
                    grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
                    timeScale: { borderColor: p.border },
                    rightPriceScale: { borderColor: p.border },
                });
                if (item.candle) {
                    item.candle.applyOptions({
                        upColor: p.up, downColor: p.down, borderUpColor: p.up, borderDownColor: p.down,
                        wickUpColor: p.up, wickDownColor: p.down,
                    });
                }
            }
        });
    }

    // --- Theme Toggle ---
    const themeToggleBtn = document.getElementById("themeToggleBtn");
    themeToggleBtn.addEventListener("click", () => {
        isDark = !isDark;
        document.body.className = isDark ? "theme-dark" : "theme-light";
        applyThemeToCharts();
    });

    // --- Tab Switching ---
    const navTabs = document.querySelectorAll(".nav-tab");
    const tabPanes = document.querySelectorAll(".tab-pane");

    navTabs.forEach(tab => {
        tab.addEventListener("click", () => {
            navTabs.forEach(t => t.classList.remove("active"));
            tabPanes.forEach(p => p.classList.remove("active"));
            tab.classList.add("active");
            const targetId = tab.getAttribute("data-tab");
            const targetPane = document.getElementById(targetId);
            if (targetPane) targetPane.classList.add("active");

            // Chart resize trigger on tab change
            setTimeout(() => {
                window.dispatchEvent(new Event('resize'));
                if (targetId === "tab-api-monitor") {
                    loadApiMonitorStats();
                }
            }, 50);
        });
    });

    // =================================================================
    // TAB 1: DATA VIEWER
    // =================================================================
    const dvSymbolInput = document.getElementById("dvSymbol");
    const dvMinutesInput = document.getElementById("dvMinutes");
    const dvDaysInput = document.getElementById("dvDays");
    const dvForceRefresh = document.getElementById("dvForceRefresh");
    const dvFetchBtn = document.getElementById("dvFetchBtn");
    const dvSaveBtn = document.getElementById("dvSaveBtn");
    const dvChartTitle = document.getElementById("dvChartTitle");
    const dvChartMeta = document.getElementById("dvChartMeta");

    dvFetchBtn.addEventListener("click", async () => {
        const symbol = dvSymbolInput.value.trim().toUpperCase();
        const minutes = parseInt(dvMinutesInput.value) || 15;
        const days = parseInt(dvDaysInput.value) || 5;
        const force = dvForceRefresh.checked;

        dvFetchBtn.disabled = true;
        dvFetchBtn.textContent = "Fetching...";
        dvChartMeta.textContent = `Connecting to Alpaca for ${symbol}...`;

        try {
            const resp = await fetch("/api/data/fetch", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ symbol, minutes, days, force_refresh: force })
            });
            const data = await resp.json();
            if (!resp.ok) {
                alert(data.error || "Failed to fetch data");
                dvChartMeta.textContent = data.error || "Fetch failed";
                return;
            }

            dvChartTitle.textContent = `${symbol} - ${minutes}m Candlestick Chart`;
            dvChartMeta.textContent = `${data.bars_count.toLocaleString()} bars loaded`;
            dvSaveBtn.disabled = false;

            renderDataViewerChart(data.chart_data);
        } catch (err) {
            alert("Error: " + err.message);
            dvChartMeta.textContent = "Error fetching data";
        } finally {
            dvFetchBtn.disabled = false;
            dvFetchBtn.textContent = "Fetch Data";
        }
    });

    function renderDataViewerChart(bars) {
        if (!charts.dataViewer.chart) {
            charts.dataViewer.chart = createTvChart("chart-data-viewer", 480);
        }
        const chart = charts.dataViewer.chart;
        const p = currentPalette();

        if (!charts.dataViewer.candle) {
            charts.dataViewer.candle = chart.addCandlestickSeries({
                upColor: p.up, downColor: p.down, borderUpColor: p.up, borderDownColor: p.down,
                wickUpColor: p.up, wickDownColor: p.down
            });
            charts.dataViewer.volume = chart.addHistogramSeries({
                color: p.up,
                priceFormat: { type: 'volume' },
                priceScaleId: '',
            });
            charts.dataViewer.volume.priceScale().applyOptions({
                scaleMargins: { top: 0.8, bottom: 0 }
            });
        }

        const candleData = [];
        const volumeData = [];

        bars.sort((a, b) => a.time - b.time);
        bars.forEach(b => {
            candleData.push({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close });
            volumeData.push({
                time: b.time,
                value: b.volume,
                color: b.close >= b.open ? 'rgba(16, 185, 129, 0.5)' : 'rgba(244, 63, 94, 0.5)'
            });
        });

        charts.dataViewer.candle.setData(candleData);
        charts.dataViewer.volume.setData(volumeData);
        chart.timeScale().fitContent();
    }

    dvSaveBtn.addEventListener("click", () => {
        window.location.href = "/api/data/export_csv";
    });

    // =================================================================
    // TAB 2: STRATEGY BACKTEST
    // =================================================================
    const btStrategySelect = document.getElementById("btStrategySelect");
    const btStrategyDesc = document.getElementById("btStrategyDesc");
    const anchorTfGroup = document.getElementById("anchorTfGroup");
    const btDynamicParams = document.getElementById("btDynamicParams");
    const btRunBtn = document.getElementById("btRunBtn");
    const btOptimizeBtn = document.getElementById("btOptimizeBtn");
    const btConsole = document.getElementById("btConsole");

    const btSharpe = document.getElementById("btSharpe");
    const btCagr = document.getElementById("btCagr");
    const btMaxDd = document.getElementById("btMaxDd");
    const btWinRate = document.getElementById("btWinRate");
    const btNumTrades = document.getElementById("btNumTrades");
    const btProfitFactor = document.getElementById("btProfitFactor");
    const btChartMeta = document.getElementById("btChartMeta");
    const btPeriodsCard = document.getElementById("btPeriodsCard");
    const btPeriodsTable = document.getElementById("btPeriodsTable").querySelector("tbody");
    const btTradesCount = document.getElementById("btTradesCount");
    const btTradesTable = document.getElementById("btTradesTable").querySelector("tbody");

    const btTogglePrice = document.getElementById("btTogglePrice");
    const btToggleEquity = document.getElementById("btToggleEquity");
    const chartBtPriceCont = document.getElementById("chart-backtest-price");
    const chartBtEquityCont = document.getElementById("chart-backtest-equity");

    btTogglePrice.addEventListener("click", () => {
        btTogglePrice.classList.add("active");
        btToggleEquity.classList.remove("active");
        chartBtPriceCont.style.display = "block";
        chartBtEquityCont.style.display = "none";
        window.dispatchEvent(new Event('resize'));
    });

    btToggleEquity.addEventListener("click", () => {
        btToggleEquity.classList.add("active");
        btTogglePrice.classList.remove("active");
        chartBtEquityCont.style.display = "block";
        chartBtPriceCont.style.display = "none";
        window.dispatchEvent(new Event('resize'));
    });

    // Load available strategies
    async function initStrategies() {
        try {
            const resp = await fetch("/api/strategies");
            registeredStrategies = await resp.json();
            btStrategySelect.innerHTML = "";
            registeredStrategies.forEach((st, idx) => {
                const opt = document.createElement("option");
                opt.value = st.name;
                opt.textContent = st.name;
                btStrategySelect.appendChild(opt);
            });
            onStrategySelected(registeredStrategies[0]);
        } catch (err) {
            console.error("Failed to load strategies", err);
        }
    }

    btStrategySelect.addEventListener("change", () => {
        const selected = registeredStrategies.find(s => s.name === btStrategySelect.value);
        if (selected) onStrategySelected(selected);
    });

    function onStrategySelected(strategy) {
        btStrategyDesc.textContent = strategy.description;
        anchorTfGroup.style.display = strategy.requires_anchor_timeframe ? "flex" : "none";

        btDynamicParams.innerHTML = "";
        strategy.parameters.forEach(p => {
            const row = document.createElement("div");
            row.className = "form-group";
            if (p.type === "bool" || p.type === "Bool") {
                row.className = "form-group checkbox-group";
                row.innerHTML = `
                    <label>
                        <input type="checkbox" id="param_${p.key}" ${p.default_value ? "checked" : ""}> ${p.label}
                    </label>
                `;
            } else {
                row.innerHTML = `
                    <label for="param_${p.key}">${p.label}:</label>
                    <input type="${p.type === 'int' ? 'number' : 'text'}" id="param_${p.key}" value="${p.default_value}" class="form-input">
                `;
            }
            btDynamicParams.appendChild(row);
        });
    }

    function readBacktestParams(strategy) {
        const params = {};
        strategy.parameters.forEach(p => {
            const el = document.getElementById(`param_${p.key}`);
            if (el) {
                if (p.type === "bool" || p.type === "Bool") {
                    params[p.key] = el.checked;
                } else if (p.type === "int" || p.type === "Int") {
                    params[p.key] = parseInt(el.value);
                } else if (p.type === "double" || p.type === "Double") {
                    params[p.key] = parseFloat(el.value);
                } else {
                    params[p.key] = el.value;
                }
            }
        });
        return params;
    }

    btRunBtn.addEventListener("click", async () => {
        const strategy = registeredStrategies.find(s => s.name === btStrategySelect.value);
        if (!strategy) return;

        const payload = {
            strategy_name: strategy.name,
            symbol: document.getElementById("btSymbol").value.trim().toUpperCase(),
            exec_amount: parseInt(document.getElementById("btExecAmt").value) || 15,
            exec_unit: document.getElementById("btExecUnit").value,
            anchor_amount: parseInt(document.getElementById("btAnchorAmt").value) || 1,
            anchor_unit: document.getElementById("btAnchorUnit").value,
            lookback_days: parseInt(document.getElementById("btLookback").value) || 60,
            initial_equity: parseFloat(document.getElementById("btInitialEquity").value) || 100000,
            force_refresh: document.getElementById("btForceRefresh").checked,
            split_date: document.getElementById("btSplitDate").value || null,
            parameters: readBacktestParams(strategy)
        };

        btRunBtn.disabled = true;
        btRunBtn.textContent = "Running...";
        btConsole.textContent = "Launching backtest job...\n";
        btChartMeta.textContent = "Executing strategy...";

        try {
            const resp = await fetch("/api/backtest/run", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const data = await resp.json();

            if (data.logs && data.logs.length) {
                btConsole.textContent = data.logs.join("\n");
                btConsole.scrollTop = btConsole.scrollHeight;
            }

            if (!resp.ok) {
                alert(data.error || "Backtest failed");
                btChartMeta.textContent = data.error || "Failed";
                return;
            }

            renderBacktestResult(data, strategy);
        } catch (err) {
            alert("Error: " + err.message);
            btChartMeta.textContent = "Error executing backtest";
        } finally {
            btRunBtn.disabled = false;
            btRunBtn.textContent = "Run Backtest";
        }
    });

    function renderBacktestPeriods(periods) {
        btPeriodsTable.innerHTML = "";
        btPeriodsCard.style.display = periods.length ? "" : "none";
        const fmt = (v, digits, suffix = "") => v !== null && v !== undefined ? v.toFixed(digits) + suffix : "n/a";
        periods.forEach(pr => {
            const pnlClass = pr.net_profit >= 0 ? "pnl-positive" : "pnl-negative";
            const pf = pr.profit_factor === 99.9 ? "inf" : fmt(pr.profit_factor, 2);
            const row = document.createElement("tr");
            row.innerHTML = `
                <td>${pr.label}</td>
                <td><span class="pnl-badge ${pnlClass}">$${pr.net_profit.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span></td>
                <td>${fmt(pr.cagr, 1, "%")}</td>
                <td>${fmt(pr.sharpe, 2)}</td>
                <td>${fmt(pr.max_drawdown, 1, "%")}</td>
                <td>${pf}</td>
                <td>${fmt(pr.win_rate, 1, "%")}</td>
                <td>${pr.num_trades.toLocaleString()}</td>`;
            btPeriodsTable.appendChild(row);
        });
    }

    function renderBacktestResult(data, strategy) {
        const res = data.result;
        btSharpe.textContent = res.sharpe !== null ? res.sharpe.toFixed(2) : "n/a";
        btCagr.textContent = res.cagr !== null ? res.cagr.toFixed(1) + "%" : "n/a";
        btMaxDd.textContent = res.max_drawdown !== null ? res.max_drawdown.toFixed(1) + "%" : "n/a";
        btWinRate.textContent = res.win_rate.toFixed(1) + "%";
        btNumTrades.textContent = res.num_trades.toLocaleString();
        btProfitFactor.textContent = res.profit_factor !== null ? (res.profit_factor === 99.9 ? "inf" : res.profit_factor.toFixed(2)) : "n/a";
        btChartMeta.textContent = `${strategy.name} • ${res.num_trades} Trades`;
        renderBacktestPeriods(data.periods || []);

        // Render Price Chart with Candlesticks, Markers & Indicators
        if (!charts.btPrice.chart) {
            charts.btPrice.chart = createTvChart("chart-backtest-price", 480);
        }
        const priceChart = charts.btPrice.chart;
        const p = currentPalette();

        // Clear existing indicators
        charts.btPrice.indicators.forEach(s => priceChart.removeSeries(s));
        charts.btPrice.indicators = [];

        if (!charts.btPrice.candle) {
            charts.btPrice.candle = priceChart.addCandlestickSeries({
                upColor: p.up, downColor: p.down, borderUpColor: p.up, borderDownColor: p.down,
                wickUpColor: p.up, wickDownColor: p.down
            });
        }

        const candles = data.candles || [];
        candles.sort((a, b) => a.time - b.time);
        charts.btPrice.candle.setData(candles);

        // Trade Markers
        if (data.markers && data.markers.length) {
            const sortedMarkers = [...data.markers].sort((a, b) => a.time - b.time);
            charts.btPrice.candle.setMarkers(sortedMarkers);
        }

        // Indicator Overlays
        (res.indicators || []).forEach(indSeries => {
            const lineStyle = indSeries.style === "Dashed" ? LightweightCharts.LineStyle.Dashed :
                             (indSeries.style === "Dotted" ? LightweightCharts.LineStyle.Dotted : LightweightCharts.LineStyle.Solid);
            const lineSeries = priceChart.addLineSeries({
                color: indSeries.color || p.line,
                lineWidth: 1,
                lineStyle: lineStyle,
                title: indSeries.name,
                priceLineVisible: false
            });
            const points = (indSeries.points || []).sort((a, b) => a.time - b.time);
            lineSeries.setData(points);
            charts.btPrice.indicators.push(lineSeries);
        });

        priceChart.timeScale().fitContent();

        // Render Equity Chart
        if (!charts.btEquity.chart) {
            charts.btEquity.chart = createTvChart("chart-backtest-equity", 480);
            charts.btEquity.line = charts.btEquity.chart.addLineSeries({
                color: p.equity,
                lineWidth: 2,
                title: "Portfolio Equity ($)"
            });
        }
        const eqPoints = (res.equity_curve || []).sort((a, b) => a.time - b.time);
        charts.btEquity.line.setData(eqPoints);
        charts.btEquity.chart.timeScale().fitContent();

        // Populate Trades Table
        btTradesCount.textContent = `${res.trades.length} trades`;
        btTradesTable.innerHTML = "";
        if (!res.trades.length) {
            btTradesTable.innerHTML = `<tr><td colspan="11" class="text-center text-muted">No trades generated</td></tr>`;
            return;
        }

        res.trades.forEach((t, i) => {
            const tr = document.createElement("tr");
            const isWin = t.pnl > 0;
            const pnlClass = isWin ? "pnl-positive" : "pnl-negative";
            tr.innerHTML = `
                <td>${i + 1}</td>
                <td>${t.entry_time}</td>
                <td>${t.exit_time}</td>
                <td><span class="pnl-badge ${t.side === 'Long' ? 'pnl-positive' : 'pnl-negative'}">${t.side}</span></td>
                <td>$${t.entry_price.toFixed(2)}</td>
                <td>$${t.exit_price.toFixed(2)}</td>
                <td>${t.shares.toFixed(2)}</td>
                <td><span class="pnl-badge ${pnlClass}">$${t.pnl.toFixed(2)}</span></td>
                <td><span class="pnl-badge ${pnlClass}">${t.return_pct.toFixed(2)}%</span></td>
                <td>${t.exit_reason}</td>
                <td>${t.bars_held}</td>
            `;
            btTradesTable.appendChild(tr);
        });
    }

    // =================================================================
    // OPTIMIZER MODAL
    // =================================================================
    const optimizerModal = document.getElementById("optimizerModal");
    const optCloseBtn = document.getElementById("optCloseBtn");
    const optStartBtn = document.getElementById("optStartBtn");
    const optProgress = document.getElementById("optProgress");
    const optBestBanner = document.getElementById("optBestBanner");
    const optResultsTable = document.getElementById("optResultsTable").querySelector("tbody");

    btOptimizeBtn.addEventListener("click", () => {
        optimizerModal.style.display = "flex";
    });

    optCloseBtn.addEventListener("click", () => {
        optimizerModal.style.display = "none";
    });

    optStartBtn.addEventListener("click", async () => {
        const strategy = registeredStrategies.find(s => s.name === btStrategySelect.value);
        if (!strategy) return;

        optStartBtn.disabled = true;
        optStartBtn.textContent = "Optimizing...";
        optProgress.style.display = "block";
        optProgress.textContent = "Running Monte Carlo parameter sweeps against historical bars...";
        optBestBanner.style.display = "none";

        const payload = {
            strategy_name: strategy.name,
            symbol: document.getElementById("btSymbol").value.trim().toUpperCase(),
            exec_amount: parseInt(document.getElementById("btExecAmt").value) || 15,
            exec_unit: document.getElementById("btExecUnit").value,
            anchor_amount: parseInt(document.getElementById("btAnchorAmt").value) || 1,
            anchor_unit: document.getElementById("btAnchorUnit").value,
            lookback_days: parseInt(document.getElementById("btLookback").value) || 60,
            initial_equity: parseFloat(document.getElementById("btInitialEquity").value) || 100000,
            trials: parseInt(document.getElementById("optTrials").value) || 30,
            target_sharpe: parseFloat(document.getElementById("optTargetSharpe").value) || 1.0,
            min_trades: parseInt(document.getElementById("optMinTrades").value) || 10,
            force_refresh: document.getElementById("btForceRefresh").checked
        };

        try {
            const resp = await fetch("/api/backtest/optimize", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const data = await resp.json();
            if (!resp.ok) {
                alert(data.error || "Optimization failed");
                return;
            }

            optProgress.textContent = `Completed ${data.trials_run} parameter trials. Best configuration ranked by Sharpe*sqrt(trades).`;

            if (data.best) {
                const b = data.best;
                optBestBanner.style.display = "block";
                optBestBanner.innerHTML = `<strong>Best Trial Selected:</strong> Sharpe ${b.result.sharpe || 'n/a'}, CAGR ${b.result.cagr || 'n/a'}%, Win Rate ${b.result.win_rate}%, Trades ${b.result.num_trades}.`;
            }

            // Populate table
            optResultsTable.innerHTML = "";
            data.trials.forEach(tr => {
                const row = document.createElement("tr");
                const paramStr = Object.entries(tr.params).map(([k, v]) => `${k}=${v}`).join(", ");
                row.innerHTML = `
                    <td>${tr.trial_index}</td>
                    <td><strong>${tr.sharpe !== null ? tr.sharpe.toFixed(2) : 'n/a'}</strong></td>
                    <td>${tr.cagr !== null ? tr.cagr.toFixed(1) + '%' : 'n/a'}</td>
                    <td>${tr.max_drawdown !== null ? tr.max_drawdown.toFixed(1) + '%' : 'n/a'}</td>
                    <td>${tr.win_rate.toFixed(1)}%</td>
                    <td>${tr.trades}</td>
                    <td>${tr.profit_factor !== null ? tr.profit_factor.toFixed(2) : 'n/a'}</td>
                    <td style="max-width: 250px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${paramStr}">${paramStr}</td>
                    <td><button class="btn btn-sm btn-primary apply-param-btn">Apply</button></td>
                `;
                row.querySelector(".apply-param-btn").addEventListener("click", () => {
                    Object.entries(tr.params).forEach(([k, v]) => {
                        const el = document.getElementById(`param_${k}`);
                        if (el) {
                            if (typeof v === "boolean") el.checked = v;
                            else el.value = v;
                        }
                    });
                    optimizerModal.style.display = "none";
                    btRunBtn.click();
                });
                optResultsTable.appendChild(row);
            });

        } catch (err) {
            alert("Error: " + err.message);
        } finally {
            optStartBtn.disabled = false;
            optStartBtn.textContent = "Start Search";
        }
    });

    // =================================================================
    // TAB 3: MEIC 0DTE BACKTEST
    // =================================================================
    const meicRunBtn = document.getElementById("meicRunBtn");
    const meicConsole = document.getElementById("meicConsole");
    const meicTotalPnl = document.getElementById("meicTotalPnl");
    const meicReturnPct = document.getElementById("meicReturnPct");
    const meicWinRate = document.getElementById("meicWinRate");
    const meicProfitFactor = document.getElementById("meicProfitFactor");
    const meicMaxDd = document.getElementById("meicMaxDd");
    const meicWindowsSkipped = document.getElementById("meicWindowsSkipped");
    const meicTradesTable = document.getElementById("meicTradesTable").querySelector("tbody");
    const meicTradesCount = document.getElementById("meicTradesCount");

    meicRunBtn.addEventListener("click", async () => {
        const payload = {
            symbol: document.getElementById("meicSymbol").value.trim().toUpperCase(),
            days: parseInt(document.getElementById("meicDays").value) || 30,
            timeframe_minutes: parseInt(document.getElementById("meicTimeframe").value) || 5,
            schedule_times_et: document.getElementById("meicSchedule").value,
            short_call_delta_target: parseFloat(document.getElementById("meicCallDelta").value) || 0.10,
            short_put_delta_target: parseFloat(document.getElementById("meicPutDelta").value) || 0.10,
            wing_width: parseFloat(document.getElementById("meicWingWidth").value) || 5.0,
            quantity_per_tranche: parseInt(document.getElementById("meicQty").value) || 1,
            take_profit_percent: parseFloat(document.getElementById("meicTP").value) || 50.0,
            stop_loss_multiplier: parseFloat(document.getElementById("meicSL").value) || 1.0,
            enable_adx_filter: document.getElementById("meicEnableAdx").checked,
            force_refresh: document.getElementById("meicForceRefresh").checked
        };

        meicRunBtn.disabled = true;
        meicRunBtn.textContent = "Running...";
        meicConsole.textContent = "Launching MEIC 0DTE simulation...\n";

        try {
            const resp = await fetch("/api/meic/run", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const data = await resp.json();

            if (data.logs && data.logs.length) {
                meicConsole.textContent = data.logs.join("\n");
                meicConsole.scrollTop = meicConsole.scrollHeight;
            }

            if (!resp.ok) {
                alert(data.error || "MEIC Simulation failed");
                return;
            }

            renderMeicResult(data.report);
        } catch (err) {
            alert("Error: " + err.message);
        } finally {
            meicRunBtn.disabled = false;
            meicRunBtn.textContent = "Run MEIC Backtest";
        }
    });

    function renderMeicResult(rep) {
        const isPositive = rep.total_pnl >= 0;
        meicTotalPnl.textContent = `$${rep.total_pnl.toLocaleString('en-US', { minimumFractionDigits: 2 })}`;
        meicTotalPnl.className = `metric-value font-mono ${isPositive ? 'text-emerald' : 'text-rose'}`;
        meicReturnPct.textContent = `${rep.return_on_capital_pct.toFixed(2)}%`;
        meicWinRate.textContent = `${rep.win_rate.toFixed(1)}% (${rep.win_count}W / ${rep.loss_count}L)`;
        meicProfitFactor.textContent = rep.profit_factor >= 99 ? 'inf' : rep.profit_factor.toFixed(2);
        meicMaxDd.textContent = `$${rep.max_drawdown.toFixed(2)} (${rep.max_drawdown_pct.toFixed(1)}%)`;
        meicWindowsSkipped.textContent = `${rep.scheduled_windows_evaluated} eval / ${rep.skipped_by_adx_filter} ADX skip`;

        // Render Equity Chart
        if (!charts.meicEquity.chart) {
            charts.meicEquity.chart = createTvChart("chart-meic-equity", 380);
            charts.meicEquity.line = charts.meicEquity.chart.addLineSeries({
                color: isPositive ? currentPalette().up : currentPalette().down,
                lineWidth: 2,
                title: "MEIC Portfolio Equity ($)"
            });
        }
        charts.meicEquity.line.applyOptions({
            color: isPositive ? currentPalette().up : currentPalette().down
        });
        const eqPoints = (rep.equity_curve || []).sort((a, b) => a.time - b.time);
        charts.meicEquity.line.setData(eqPoints);
        charts.meicEquity.chart.timeScale().fitContent();

        // Populate Table
        meicTradesCount.textContent = `${rep.trades.length} tranches`;
        meicTradesTable.innerHTML = "";
        if (!rep.trades.length) {
            meicTradesTable.innerHTML = `<tr><td colspan="13" class="text-center text-muted">No simulated tranches</td></tr>`;
            return;
        }

        rep.trades.forEach((t, i) => {
            const tr = document.createElement("tr");
            const pnlClass = t.is_win ? "pnl-positive" : "pnl-negative";
            tr.innerHTML = `
                <td>${t.tranche_index}</td>
                <td>${t.date}</td>
                <td>${t.scheduled_time_et}</td>
                <td>${t.entry_time.split(" ")[1]}</td>
                <td>${t.exit_time.split(" ")[1]}</td>
                <td>$${t.spot_at_entry.toFixed(2)}</td>
                <td>$${t.spot_at_exit.toFixed(2)}</td>
                <td><strong>${t.strikes_summary}</strong></td>
                <td>$${t.entry_credit.toFixed(2)}</td>
                <td>$${t.exit_debit.toFixed(2)}</td>
                <td><span class="pnl-badge ${pnlClass}">$${t.realized_pnl.toFixed(2)}</span></td>
                <td><span class="pnl-badge ${pnlClass}">${t.return_pct.toFixed(1)}%</span></td>
                <td>${t.exit_reason}</td>
            `;
            meicTradesTable.appendChild(tr);
        });
    }

    // =================================================================
    // TAB 4: TRIPPLE IRON FLY BACKTEST
    // =================================================================
    const ifRunBtn = document.getElementById("ifRunBtn");
    const ifConsole = document.getElementById("ifConsole");
    const ifWarningBanner = document.getElementById("ifWarningBanner");
    const ifNetPnl = document.getElementById("ifNetPnl");
    const ifGrossPnl = document.getElementById("ifGrossPnl");
    const ifCostDrag = document.getElementById("ifCostDrag");
    const ifWinRate = document.getElementById("ifWinRate");
    const ifMaxDd = document.getElementById("ifMaxDd");
    const ifPeakFlies = document.getElementById("ifPeakFlies");
    const ifTradesTable = document.getElementById("ifTradesTable").querySelector("tbody");
    const ifTradesCount = document.getElementById("ifTradesCount");

    ifRunBtn.addEventListener("click", async () => {
        const payload = {
            symbol: document.getElementById("ifSymbol").value.trim().toUpperCase(),
            days: parseInt(document.getElementById("ifDays").value) || 30,
            timeframe_minutes: parseInt(document.getElementById("ifTimeframe").value) || 5,
            first_entry_time_et: document.getElementById("ifFirstEntry").value,
            strike_spacing: parseFloat(document.getElementById("ifSpacing").value) || 5.0,
            move_trigger_points: parseFloat(document.getElementById("ifTrigger").value) || 2.5,
            wing_width: parseFloat(document.getElementById("ifWingWidth").value) || 20.0,
            max_concurrent_flies: parseInt(document.getElementById("ifMaxFlies").value) || 3,
            bail_on_opposite_touch: document.getElementById("ifBailOpposite").checked,
            roll_at_max: document.getElementById("ifRollAtMax").checked,
            force_refresh: document.getElementById("ifForceRefresh").checked
        };

        ifRunBtn.disabled = true;
        ifRunBtn.textContent = "Simulating...";
        ifConsole.textContent = "Launching SPX IF MAX 3 ladder simulation...\n";

        try {
            const resp = await fetch("/api/tripple_iron_fly/run", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const data = await resp.json();

            if (data.logs && data.logs.length) {
                ifConsole.textContent = data.logs.join("\n");
                ifConsole.scrollTop = ifConsole.scrollHeight;
            }

            if (!resp.ok) {
                alert(data.error || "TrippleIronFly simulation failed");
                return;
            }

            renderIronFlyResult(data.report);
        } catch (err) {
            alert("Error: " + err.message);
        } finally {
            ifRunBtn.disabled = false;
            ifRunBtn.textContent = "Run SPX Ladder";
        }
    });

    function renderIronFlyResult(rep) {
        if (rep.strike_grid_warning) {
            ifWarningBanner.style.display = "block";
            ifWarningBanner.textContent = "⚠️ " + rep.strike_grid_warning;
        } else {
            ifWarningBanner.style.display = "none";
        }

        const isPositive = rep.total_pnl >= 0;
        ifNetPnl.textContent = `$${rep.total_pnl.toLocaleString('en-US', { minimumFractionDigits: 2 })}`;
        ifNetPnl.className = `metric-value font-mono ${isPositive ? 'text-emerald' : 'text-rose'}`;
        ifGrossPnl.textContent = `$${rep.gross_pnl.toLocaleString('en-US', { minimumFractionDigits: 2 })}`;
        const totalCosts = rep.total_slippage + rep.total_commissions;
        ifCostDrag.textContent = `$${totalCosts.toFixed(2)} (${rep.cost_drag_pct.toFixed(1)}%)`;
        ifWinRate.textContent = `${rep.win_rate.toFixed(1)}% (${rep.win_count}W / ${rep.loss_count}L)`;
        ifMaxDd.textContent = `$${rep.max_drawdown.toFixed(2)} (${rep.max_drawdown_pct.toFixed(1)}%)`;
        ifPeakFlies.textContent = `${rep.peak_concurrent_flies} (Max Risk $${rep.max_risk_per_ladder.toLocaleString()})`;

        // Render Equity Chart
        if (!charts.ironflyEquity.chart) {
            charts.ironflyEquity.chart = createTvChart("chart-ironfly-equity", 380);
            charts.ironflyEquity.line = charts.ironflyEquity.chart.addLineSeries({
                color: isPositive ? currentPalette().up : currentPalette().down,
                lineWidth: 2,
                title: "SPX Ladder Equity Net of Costs ($)"
            });
        }
        charts.ironflyEquity.line.applyOptions({
            color: isPositive ? currentPalette().up : currentPalette().down
        });
        const eqPoints = (rep.equity_curve || []).sort((a, b) => a.time - b.time);
        charts.ironflyEquity.line.setData(eqPoints);
        charts.ironflyEquity.chart.timeScale().fitContent();

        // Populate Table
        ifTradesCount.textContent = `${rep.trades.length} ladder positions`;
        ifTradesTable.innerHTML = "";
        if (!rep.trades.length) {
            ifTradesTable.innerHTML = `<tr><td colspan="14" class="text-center text-muted">No ladder positions</td></tr>`;
            return;
        }

        rep.trades.forEach((t, i) => {
            const tr = document.createElement("tr");
            const pnlClass = t.is_win ? "pnl-positive" : "pnl-negative";
            tr.innerHTML = `
                <td>${t.fly_index}</td>
                <td>${t.date}</td>
                <td><span class="pnl-badge ${t.ladder_step === 0 ? 'text-cyan' : ''}">${t.ladder_step_str}</span></td>
                <td>${t.entry_time.split(" ")[1]}</td>
                <td>${t.exit_time.split(" ")[1]}</td>
                <td>$${t.spot_at_entry.toFixed(2)}</td>
                <td>$${t.spot_at_exit.toFixed(2)}</td>
                <td><strong>${t.strikes_summary}</strong></td>
                <td>$${t.entry_credit.toFixed(2)}</td>
                <td>$${t.exit_debit.toFixed(2)}</td>
                <td>$${t.gross_pnl.toFixed(2)}</td>
                <td>$${t.total_cost.toFixed(2)}</td>
                <td><span class="pnl-badge ${pnlClass}">$${t.realized_pnl.toFixed(2)}</span></td>
                <td>${t.exit_reason}</td>
            `;
            ifTradesTable.appendChild(tr);
        });
    }

    // =================================================================
    // TAB 5: API MONITOR
    // =================================================================
    const apiCallsToday = document.getElementById("apiCallsToday");
    const apiCallsHour = document.getElementById("apiCallsHour");
    const apiCallsMinute = document.getElementById("apiCallsMinute");
    const apiCallsTable = document.getElementById("apiCallsTable").querySelector("tbody");
    const apiRefreshBtn = document.getElementById("apiRefreshBtn");

    async function loadApiMonitorStats() {
        try {
            const resp = await fetch("/api/tracker/stats");
            const data = await resp.json();

            apiCallsToday.textContent = data.count_today.toLocaleString();
            apiCallsHour.textContent = data.count_last_hour.toLocaleString();
            apiCallsMinute.textContent = data.count_last_minute.toLocaleString();

            renderHourlyApiChart(data.hourly_today);
            renderDailyApiChart(data.daily_history);
            renderRecentApiCalls(data.recent_calls);
        } catch (err) {
            console.error("Failed to load API monitor stats", err);
        }
    }

    function renderHourlyApiChart(hourlyData) {
        if (!charts.apiHourly.chart) {
            charts.apiHourly.chart = createTvChart("chart-api-hourly", 260);
            charts.apiHourly.histogram = charts.apiHourly.chart.addHistogramSeries({
                color: currentPalette().bar,
                priceFormat: { type: 'volume' }
            });
        }
        // Timestamp today at given hour
        const now = new Date();
        const base = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 0, 0, 0));
        const pts = (hourlyData || []).map(([hour, count]) => {
            const t = new Date(base.getTime() + hour * 3600 * 1000);
            return {
                time: Math.floor(t.getTime() / 1000),
                value: count,
                color: count > 100 ? '#F43F5E' : currentPalette().bar
            };
        });
        charts.apiHourly.histogram.setData(pts);
        charts.apiHourly.chart.timeScale().fitContent();
    }

    function renderDailyApiChart(dailyData) {
        if (!charts.apiDaily.chart) {
            charts.apiDaily.chart = createTvChart("chart-api-daily", 260);
            charts.apiDaily.histogram = charts.apiDaily.chart.addHistogramSeries({
                color: currentPalette().bar,
                priceFormat: { type: 'volume' }
            });
        }
        const pts = (dailyData || []).map(([dayStr, count]) => {
            const t = new Date(dayStr + "T00:00:00Z");
            return {
                time: Math.floor(t.getTime() / 1000),
                value: count,
                color: count > 1000 ? '#F43F5E' : currentPalette().bar
            };
        });
        charts.apiDaily.histogram.setData(pts);
        charts.apiDaily.chart.timeScale().fitContent();
    }

    function renderRecentApiCalls(calls) {
        apiCallsTable.innerHTML = "";
        if (!calls || !calls.length) {
            apiCallsTable.innerHTML = `<tr><td colspan="3" class="text-center text-muted">No API calls recorded in this period</td></tr>`;
            return;
        }

        calls.forEach(c => {
            const tr = document.createElement("tr");
            const isError = c.detail.includes("RATE_LIMITED") || c.detail.includes("HTTP_");
            tr.innerHTML = `
                <td class="font-mono">${c.time}</td>
                <td><span class="pnl-badge ${isError ? 'pnl-negative' : 'pnl-positive'}">${c.endpoint}</span></td>
                <td>${c.detail}</td>
            `;
            apiCallsTable.appendChild(tr);
        });
    }

    apiRefreshBtn.addEventListener("click", loadApiMonitorStats);

    // Initial startup
    initStrategies();
    loadApiMonitorStats();
});