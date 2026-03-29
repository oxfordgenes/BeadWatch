// BeadWatch — Operator Comparison Logic

(function () {
    'use strict';

    // ---- Dark background plugin (ensures PNG exports aren't transparent) ----
    Chart.register({
        id: 'bgFill',
        beforeDraw: function (chart) {
            var ctx = chart.ctx;
            ctx.save();
            ctx.globalCompositeOperation = 'destination-over';
            ctx.fillStyle = '#0b1120';
            ctx.fillRect(0, 0, chart.width, chart.height);
            ctx.restore();
        }
    });

    // ---- PNG Export Helper ----
    function exportChartPNG(chart, name) {
        var url = chart.toBase64Image('image/png', 1);
        var date = new Date().toISOString().slice(0, 10);
        var a = document.createElement('a');
        a.href = url;
        a.download = 'beadwatch-op-' + name + '-' + date + '.png';
        a.click();
    }

    // ---- State ----
    var currentDays = 90;
    var snChart = null;
    var ncChart = null;
    var countsChart = null;
    var loadedKey = null;
    var dataTimer = null;

    // ---- Colour generation ----
    // Hash-based: each entity name gets a unique, stable colour.
    // Uses golden-angle hue spacing seeded by a string hash so that
    // entities visible at the same time are maximally distinguishable.
    function nameToColour(name, count, index) {
        // Golden angle in degrees — maximally separates sequential hues
        var GOLDEN = 137.508;
        // Simple string hash (djb2)
        var h = 5381;
        for (var i = 0; i < name.length; i++) {
            h = ((h << 5) + h + name.charCodeAt(i)) & 0xffffffff;
        }
        // Use golden-angle spacing by visible index, offset by name hash
        // so the same set always looks good, but different sets don't collide
        var hue = ((index * GOLDEN) + (h % 360) + 360) % 360;
        return 'hsl(' + hue.toFixed(1) + ', 75%, 55%)';
    }

    // ---- DOM refs ----
    var versionEl = document.getElementById('app-version');
    var timeButtons = document.querySelectorAll('.time-window-toggle button');
    var snCanvas = document.getElementById('op-sn-chart');
    var snPlaceholder = document.getElementById('op-sn-placeholder');
    var snSubtitle = document.getElementById('op-sn-subtitle');
    var ncCanvas = document.getElementById('op-nc-chart');
    var ncPlaceholder = document.getElementById('op-nc-placeholder');
    var ncSubtitle = document.getElementById('op-nc-subtitle');
    var countsCanvas = document.getElementById('op-counts-chart');
    var countsPlaceholder = document.getElementById('op-counts-placeholder');
    var countsSubtitle = document.getElementById('op-counts-subtitle');

    // ---- Wide-view toggle ----
    (function () {
        var btn = document.getElementById('btn-wide');
        var main = document.querySelector('.dashboard');
        var iconExpand = document.getElementById('icon-expand');
        var iconCollapse = document.getElementById('icon-collapse');
        var label = document.getElementById('wide-label');
        if (!btn || !main) return;
        function apply(wide) {
            if (wide) {
                main.classList.add('wide');
                iconExpand.style.display = 'none';
                iconCollapse.style.display = '';
                label.textContent = 'Narrow';
            } else {
                main.classList.remove('wide');
                iconExpand.style.display = '';
                iconCollapse.style.display = 'none';
                label.textContent = 'Wide';
            }
        }
        apply(localStorage.getItem('bw-wide') === '1');
        btn.addEventListener('click', function () {
            var isWide = main.classList.contains('wide');
            localStorage.setItem('bw-wide', isWide ? '0' : '1');
            apply(!isWide);
        });
    })();

    // ---- Helpers ----
    async function apiGet(url) {
        var response = await fetch(url);
        if (!response.ok) throw new Error('API error: ' + response.status);
        return response.json();
    }

    function calcMeanSD(values) {
        if (!values.length) return { mean: 0, sd: 0 };
        var sum = 0;
        for (var i = 0; i < values.length; i++) sum += values[i];
        var mean = sum / values.length;
        if (values.length < 2) return { mean: mean, sd: 0 };
        var sqSum = 0;
        for (var i = 0; i < values.length; i++) sqSum += (values[i] - mean) * (values[i] - mean);
        return { mean: mean, sd: Math.sqrt(sqSum / (values.length - 1)) };
    }

    function flatLine(labels, value) {
        return labels.map(function () { return value; });
    }

    function refLine(color, dash) {
        return {
            data: [],
            borderColor: color,
            borderWidth: 1.5,
            borderDash: dash || [6, 3],
            pointRadius: 0,
            pointHoverRadius: 0,
            fill: false,
            tension: 0,
            yAxisID: 'y'
        };
    }

    function addRefLines(chart, color) {
        var all = [];
        chart.data.datasets.forEach(function (ds) {
            ds.data.forEach(function (v) { if (v != null) all.push(v); });
        });
        var stats = calcMeanSD(all);
        var labels = chart.data.labels;

        var meanDS = refLine(color.replace(/[\d.]+\)$/, '0.7)'), []);
        meanDS.label = 'Mean';
        meanDS.data = flatLine(labels, stats.mean);

        var hiDS = refLine(color.replace(/[\d.]+\)$/, '0.5)'));
        hiDS.label = '+2 SD';
        hiDS.data = flatLine(labels, stats.mean + 2 * stats.sd);

        var loDS = refLine(color.replace(/[\d.]+\)$/, '0.5)'));
        loDS.label = '\u22122 SD';
        loDS.data = flatLine(labels, stats.mean - 2 * stats.sd);

        chart.data.datasets.push(meanDS, hiDS, loDS);

        var pad = stats.sd > 0 ? 5 * stats.sd : (stats.mean * 0.1 || 100);
        var yLo = stats.mean - pad;
        var yHi = stats.mean + pad;
        var nudge = pad * 0.015;
        var OUTLIER_FILL = '#b91c1c';
        var twoSD = 2 * stats.sd;

        chart.data.datasets.forEach(function (ds) {
            if (!ds._runMeta) return;
            var fills = ds.data.map(function (v) {
                if (v == null) return 'transparent';
                return (v > stats.mean + twoSD || v < stats.mean - twoSD)
                    ? OUTLIER_FILL : 'transparent';
            });
            ds.pointBackgroundColor = fills;
            ds.data = ds.data.map(function (v) {
                if (v == null) return null;
                return Math.max(yLo - nudge, Math.min(yHi + nudge, v));
            });
        });

        chart.options.scales.y.min = yLo;
        chart.options.scales.y.max = yHi;
        chart.update('none');
    }

    function fmt(value) {
        if (value === null || value === undefined) return '\u2014';
        return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
    }

    function windowLabel() {
        if (currentDays === 0) return 'All time';
        if (currentDays === 90) return '3 months';
        if (currentDays === 365) return '1 year';
        if (currentDays === 730) return '2 years';
        if (currentDays === 1095) return '3 years';
        return currentDays + ' days';
    }

    // ---- Version & Status ----
    async function loadVersion() {
        try {
            var data = await apiGet('/health');
            versionEl.textContent = 'v' + data.version;
        } catch (e) {
            versionEl.textContent = '';
        }
    }

    // ---- Chart Factory ----
    function initChart(canvasEl, yLabel) {
        if (!canvasEl) return null;
        var ctx = canvasEl.getContext('2d');
        return new Chart(ctx, {
            type: 'line',
            data: { datasets: [] },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                clip: false,
                interaction: {
                    mode: 'nearest',
                    intersect: false
                },
                scales: {
                    x: {
                        type: 'category',
                        grid: { color: 'rgba(30, 41, 59, 0.6)' },
                        ticks: {
                            color: '#64748b', font: { size: 11 }, maxRotation: 45,
                            autoSkip: true,
                            maxTicksLimit: 20
                        }
                    },
                    y: {
                        position: 'left',
                        title: { display: true, text: yLabel, color: '#64748b', font: { size: 11 } },
                        grid: { color: 'rgba(30, 41, 59, 0.6)' },
                        ticks: { color: '#64748b', font: { size: 11 } }
                    }
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        align: 'center',
                        labels: {
                            color: '#94a3b8',
                            font: { size: 12 },
                            padding: 20,
                            usePointStyle: true,
                            pointStyle: 'circle',
                            filter: function (item) { return !item.text.match(/^(Mean|[+\u2212]2 SD)$/); }
                        }
                    },
                    tooltip: {
                        backgroundColor: '#1e293b',
                        borderColor: '#273449',
                        borderWidth: 1,
                        titleColor: '#e2e8f0',
                        bodyColor: '#94a3b8',
                        filter: function (item) { return !item.dataset.label.match(/^(Mean|[+\u2212]2 SD)$/); },
                        callbacks: {
                            title: function (items) {
                                if (!items.length) return '';
                                var idx = items[0].dataIndex;
                                var chart = items[0].chart;
                                var ds = chart.data.datasets[items[0].datasetIndex];
                                var meta = ds._runMeta;
                                if (!meta) return '';
                                var date = meta.dates[idx] || '';
                                var run = meta.runNames[idx] || '';
                                return run ? run + '  (' + date + ')' : date;
                            },
                            label: function (item) {
                                return item.dataset.label + ': ' + fmt(item.raw);
                            },
                            afterBody: function (items) {
                                if (!items.length) return '';
                                var ds = items[0].chart.data.datasets[items[0].datasetIndex];
                                var meta = ds._runMeta;
                                var idx = items[0].dataIndex;
                                if (!meta || !meta.ranges) return '';
                                var range = meta.ranges[idx];
                                if (range) return 'Range: ' + fmt(range[0]) + ' \u2013 ' + fmt(range[1]);
                                return '';
                            }
                        }
                    }
                }
            }
        });
    }

    // ---- Build datasets for a chart from grouped data ----
    function buildDatasets(chart, dataByOperator, valueKey, minKey, maxKey) {
        var allDates = [];
        var dateSet = {};
        Object.keys(dataByOperator).forEach(function (op) {
            dataByOperator[op].forEach(function (d) {
                if (!dateSet[d.datetime]) {
                    dateSet[d.datetime] = d.date;
                    allDates.push({ datetime: d.datetime, date: d.date });
                }
            });
        });
        allDates.sort(function (a, b) { return a.datetime < b.datetime ? -1 : 1; });
        var labels = allDates.map(function (d) { return d.date; });

        var datasets = [];
        var operators = Object.keys(dataByOperator).sort();
        operators.forEach(function (op, i) {
            var colour = nameToColour(op, operators.length, i);
            var dataMap = {};
            dataByOperator[op].forEach(function (d) {
                dataMap[d.datetime] = d;
            });

            var values = [];
            var dates = [];
            var runNames = [];
            var ranges = [];
            allDates.forEach(function (slot) {
                var d = dataMap[slot.datetime];
                if (d) {
                    values.push(d[valueKey]);
                    dates.push(d.date);
                    runNames.push(d.run_name);
                    if (minKey && maxKey) {
                        ranges.push([d[minKey], d[maxKey]]);
                    } else {
                        ranges.push(null);
                    }
                } else {
                    values.push(null);
                    dates.push(slot.date);
                    runNames.push('');
                    ranges.push(null);
                }
            });

            var ds = {
                label: op,
                data: values,
                borderColor: colour,
                backgroundColor: 'transparent',
                pointBorderColor: colour,
                pointBackgroundColor: 'transparent',
                borderWidth: 0,
                pointBorderWidth: 1.5,
                pointRadius: 3.5,
                pointHoverRadius: 6,
                pointHoverBorderWidth: 2,
                fill: false,
                showLine: false,
                yAxisID: 'y'
            };
            ds._runMeta = { dates: dates, runNames: runNames, ranges: ranges };
            datasets.push(ds);
        });

        chart.data.labels = labels;
        chart.data.datasets = datasets;
        chart.update('none');
    }

    // ---- Group API response by operator ----
    function groupByOperator(data) {
        var grouped = {};
        data.forEach(function (d) {
            if (!grouped[d.operator]) grouped[d.operator] = [];
            grouped[d.operator].push(d);
        });
        return grouped;
    }

    // ---- Show/hide helpers ----
    function showChart(canvas, placeholder) {
        if (canvas) canvas.style.display = '';
        if (placeholder) placeholder.style.display = 'none';
    }

    function showPlaceholder(canvas, placeholder, msg) {
        if (canvas) canvas.style.display = 'none';
        if (placeholder) {
            placeholder.textContent = msg;
            placeholder.style.display = '';
        }
    }

    function showLoading(canvas, placeholder, msg) {
        if (canvas) canvas.style.display = 'none';
        if (placeholder) {
            placeholder.innerHTML = '<span class="spinner"></span> ' + msg;
            placeholder.style.display = '';
        }
    }

    // ---- Load All Charts ----
    var retryTimer = null;

    async function loadAllCharts() {
        var key = currentDays;
        if (loadedKey === key) return;

        if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }

        showLoading(snCanvas, snPlaceholder, 'Loading S/N ratio data\u2026');
        showLoading(ncCanvas, ncPlaceholder, 'Loading background data\u2026');
        showLoading(countsCanvas, countsPlaceholder, 'Loading bead count data\u2026');

        try {
            var operators = await apiGet('/api/dashboard/qc/operators?days=' + currentDays);

            if (!operators.length) {
                showLoading(snCanvas, snPlaceholder, 'Populating operator cache \u2014 this may take a few minutes\u2026');
                showLoading(ncCanvas, ncPlaceholder, 'Populating operator cache \u2014 this may take a few minutes\u2026');
                showLoading(countsCanvas, countsPlaceholder, 'Populating operator cache \u2014 this may take a few minutes\u2026');
                retryTimer = setTimeout(function () { loadAllCharts(); }, 15000);
                return;
            }

            var results = await Promise.all([
                apiGet('/api/dashboard/qc/operator-sn-ratio?days=' + currentDays),
                apiGet('/api/dashboard/qc/operator-nc?days=' + currentDays),
                apiGet('/api/dashboard/qc/operator-bead-counts?days=' + currentDays),
            ]);

            var snData = results[0];
            var ncData = results[1];
            var countsData = results[2];

            var label = windowLabel();

            if (snChart && snData.length) {
                buildDatasets(snChart, groupByOperator(snData), 'sn_median', 'sn_min', 'sn_max');
                addRefLines(snChart, 'rgba(148, 163, 184, 1)');
                showChart(snCanvas, snPlaceholder);
            } else {
                showPlaceholder(snCanvas, snPlaceholder, 'No S/N ratio data available');
            }
            if (snSubtitle) snSubtitle.textContent = label;

            if (ncChart && ncData.length) {
                buildDatasets(ncChart, groupByOperator(ncData), 'nc_median', 'nc_min', 'nc_max');
                addRefLines(ncChart, 'rgba(148, 163, 184, 1)');
                showChart(ncCanvas, ncPlaceholder);
            } else {
                showPlaceholder(ncCanvas, ncPlaceholder, 'No background data available');
            }
            if (ncSubtitle) ncSubtitle.textContent = label;

            if (countsChart && countsData.length) {
                buildDatasets(countsChart, groupByOperator(countsData), 'median_count', 'min_count', 'max_count');
                addRefLines(countsChart, 'rgba(148, 163, 184, 1)');
                showChart(countsCanvas, countsPlaceholder);
            } else {
                showPlaceholder(countsCanvas, countsPlaceholder, 'No bead count data available');
            }
            if (countsSubtitle) countsSubtitle.textContent = label;

            loadedKey = key;
        } catch (e) {
            showPlaceholder(snCanvas, snPlaceholder, 'Failed to load operator data');
            showPlaceholder(ncCanvas, ncPlaceholder, 'Failed to load operator data');
            showPlaceholder(countsCanvas, countsPlaceholder, 'Failed to load operator data');
        }
    }

    // ---- Time Window Toggle ----
    timeButtons.forEach(function (btn) {
        btn.addEventListener('click', function () {
            timeButtons.forEach(function (b) { b.setAttribute('aria-pressed', 'false'); });
            btn.setAttribute('aria-pressed', 'true');
            currentDays = parseInt(btn.dataset.days, 10);
            loadedKey = null;
            loadAllCharts();
        });
    });

    // ---- Manual Refresh Button ----
    var refreshBtn = document.getElementById('op-refresh-btn');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', function () {
            refreshBtn.disabled = true;
            refreshBtn.textContent = 'Refreshing\u2026';
            fetch('/api/config/operator-cache/refresh', { method: 'POST' })
                .then(function () {
                    loadedKey = null;
                    loadAllCharts();
                })
                .finally(function () {
                    refreshBtn.disabled = false;
                    refreshBtn.textContent = 'Refresh Data';
                });
        });
    }

    // ---- Refresh ----
    function startRefresh() {
        dataTimer = setInterval(function () {
            loadedKey = null;
            loadAllCharts();
        }, 300000);
    }

    // ---- PNG Export Click Handler ----
    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.btn-export');
        if (!btn) return;
        var key = btn.dataset.export;
        var chartExportMap = {
            'sn': snChart,
            'nc': ncChart,
            'counts': countsChart
        };
        var chart = chartExportMap[key];
        if (chart) exportChartPNG(chart, key);
    });

    // ---- Init ----
    async function init() {
        snChart = initChart(snCanvas, 'S/N Ratio');
        ncChart = initChart(ncCanvas, 'NC (MFI)');
        countsChart = initChart(countsCanvas, 'Bead Count');
        await loadVersion();
        await loadAllCharts();
        startRefresh();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
