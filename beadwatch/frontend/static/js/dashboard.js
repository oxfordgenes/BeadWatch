// BeadWatch — Dashboard Logic

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
        a.download = 'beadwatch-' + name + '-' + date + '.png';
        a.click();
    }

    // ---- State ----
    let currentDays = 0;
    let controlsChart = null;
    let beadCountsChart = null;
    let snRatioChart = null;
    let qcPosChart = null;
    let qcNegChart = null;
    let qcTrackedChart = null;
    let statusTimer = null;
    let dataTimer = null;
    let refreshTimer = null;
    let qcRequestId = 0;
    let activeTab = 'controls';
    let controlsLoadedKey = null;
    let beadCountsLoadedKey = null;
    let snRatioLoadedKey = null;
    let qcSamplesLoadedKey = null;
    let controlsMRChart = null;
    let beadCountsMRChart = null;
    let snRatioMRChart = null;

    // ---- DOM refs (aligned to index.html IDs) ----
    const versionEl = document.getElementById('app-version');
    const alertBanner = document.getElementById('alert-banner');
    const alertBannerSummary = document.getElementById('alert-banner-summary');
    const alertSeverityBadge = document.getElementById('alert-severity-badge');
    const alertSummaryText = document.getElementById('alert-summary-text');
    const alertList = document.getElementById('alert-list');
    const dismissAllBtn = document.getElementById('dismiss-all-btn');
    const catalogGroupSelect = document.getElementById('catalog-group-select');
    const beadLotSelect = document.getElementById('bead-lot-select');
    const timeButtons = document.querySelectorAll('.time-window-toggle button');
    const controlsCanvas = document.getElementById('controls-chart');
    const controlsPlaceholder = document.getElementById('controls-placeholder');
    const controlsSubtitle = document.getElementById('controls-subtitle');
    const beadCountsCanvas = document.getElementById('bead-counts-chart');
    const beadCountsPlaceholder = document.getElementById('bead-counts-placeholder');
    const beadCountsSubtitle = document.getElementById('bead-counts-subtitle');
    const snRatioCanvas = document.getElementById('sn-ratio-chart');
    const snRatioPlaceholder = document.getElementById('sn-ratio-placeholder');
    const snRatioSubtitle = document.getElementById('sn-ratio-subtitle');
    const qcPosCanvas = document.getElementById('qc-pos-chart');
    const qcPosPlaceholder = document.getElementById('qc-pos-placeholder');
    const qcPosSubtitle = document.getElementById('qc-pos-subtitle');
    const qcNegCanvas = document.getElementById('qc-neg-chart');
    const qcNegPlaceholder = document.getElementById('qc-neg-placeholder');
    const qcNegSubtitle = document.getElementById('qc-neg-subtitle');
    const qcTrackedCanvas = document.getElementById('qc-tracked-chart');
    const qcTrackedPlaceholder = document.getElementById('qc-tracked-placeholder');
    const qcTrackedSubtitle = document.getElementById('qc-tracked-subtitle');
    const metricTabButtons = document.querySelectorAll('.tab-btn');
    const metricTabPanels = document.querySelectorAll('.tab-panel');

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
    async function apiGet(url, signal) {
        const response = await fetch(url, signal ? { signal: signal } : undefined);
        if (!response.ok) {
            throw new Error('API error: ' + response.status);
        }
        return response.json();
    }

    function fmt(value) {
        if (value === null || value === undefined) return '\u2014';
        return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
    }

    function toIsoDate(dateObj) {
        if (!dateObj) return '';
        return new Date(dateObj).toISOString().slice(0, 10);
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

    function calcMovingRange(values) {
        var mr = [null];
        for (var i = 1; i < values.length; i++) {
            if (values[i] == null || values[i - 1] == null) {
                mr.push(null);
            } else {
                mr.push(Math.abs(values[i] - values[i - 1]));
            }
        }
        var valid = mr.filter(function (v) { return v != null; });
        var meanMR = 0;
        if (valid.length) {
            var sum = 0;
            for (var j = 0; j < valid.length; j++) sum += valid[j];
            meanMR = sum / valid.length;
        }
        return { mr: mr, meanMR: meanMR, ucl: meanMR * 3.267 };
    }

    function mrSummaryText(mrData, label) {
        var valid = mrData.mr.filter(function (v) { return v != null; });
        if (!valid.length) return '';
        var exceeded = valid.filter(function (v) { return v > mrData.ucl; }).length;
        if (exceeded === 0) return label + ': no points exceed UCL.';
        return label + ': ' + exceeded + ' of ' + valid.length + ' points exceed UCL \u2014 possible run-to-run instability.';
    }

    function flatLine(labels, value) {
        return labels.map(function () { return value; });
    }

    // ---- QC Cache Refresh Button ----
    var qcRefreshBtn = document.getElementById('qc-refresh-btn');
    if (qcRefreshBtn) {
        qcRefreshBtn.addEventListener('click', function () {
            qcRefreshBtn.disabled = true;
            qcRefreshBtn.textContent = 'Refreshing\u2026';
            fetch('/api/config/qc-cache/refresh', { method: 'POST' })
                .then(function () {
                    qcSamplesLoadedKey = null;
                    loadActiveTab();
                })
                .finally(function () {
                    qcRefreshBtn.disabled = false;
                    qcRefreshBtn.textContent = 'Refresh QC Data';
                });
        });
    }

    // ---- Version ----
    async function loadVersion() {
        try {
            var data = await apiGet('/health');
            versionEl.textContent = 'v' + data.version;
        } catch (e) {
            versionEl.textContent = '';
        }
    }


    // ---- Alerts ----
    async function dismissAlert(alertId) {
        try {
            await fetch('/api/dashboard/alerts/' + alertId + '/acknowledge', { method: 'POST' });
            await loadAlerts();
        } catch (e) {
            // Silently ignore — alert will remain visible
        }
    }

    async function dismissAllAlerts() {
        try {
            await fetch('/api/dashboard/alerts/acknowledge-all', { method: 'POST' });
            await loadAlerts();
        } catch (e) {
            // Silently ignore
        }
    }

    var metricLabels = {
        'mean_mfi': 'Mean MFI',
        'min_bead_count': 'Min Bead Count',
        'signal_to_noise': 'Signal-to-Noise',
        'negative_control_mfi': 'Negative Control MFI',
        'cv_percentage': 'CV %',
        'median_mfi': 'Median MFI',
        'low_bead_pct': 'Low Bead %',
        'qc_median_mfi': 'QC Median MFI'
    };

    function friendlyMetric(name) {
        return metricLabels[name] || name.replace(/_/g, ' ');
    }

    function friendlyThreshold(type) {
        return type === 'upper' ? 'too high' : type === 'lower' ? 'too low' : type;
    }

    async function loadAlerts() {
        try {
            var alerts = await apiGet('/api/dashboard/alerts/active');
            if (!alerts.length) {
                alertBanner.classList.remove('visible');
                return;
            }

            var highestSeverity = alerts.some(function (a) { return a.severity === 'critical'; }) ? 'critical' : 'warning';

            alertBanner.classList.add('visible');
            alertSeverityBadge.setAttribute('data-severity', highestSeverity);
            alertSeverityBadge.textContent = highestSeverity.toUpperCase();
            alertSummaryText.textContent = alerts.length + ' active alert' + (alerts.length !== 1 ? 's' : '');

            // Build alert detail list
            alertList.innerHTML = '';
            alerts.forEach(function (a) {
                var li = document.createElement('li');
                var sevSpan = document.createElement('span');
                sevSpan.className = 'alert-list-severity ' + a.severity;
                sevSpan.textContent = a.severity;

                var descSpan = document.createElement('span');
                var ts = new Date(a.timestamp).toLocaleString();
                var sample = a.display_name ? (a.display_name + ' \u2014 ') : '';
                descSpan.textContent = sample + friendlyMetric(a.metric_name) + ' ' + friendlyThreshold(a.threshold_type) + ' (' + fmt(a.actual_value) + ' vs ' + fmt(a.threshold_value) + ') \u2014 ' + ts;

                var dismissBtn = document.createElement('button');
                dismissBtn.className = 'btn-dismiss-alert';
                dismissBtn.title = 'Dismiss';
                dismissBtn.textContent = '\u2715';
                dismissBtn.addEventListener('click', function (e) {
                    e.stopPropagation();
                    dismissAlert(a.id);
                });

                li.appendChild(sevSpan);
                li.appendChild(descSpan);
                li.appendChild(dismissBtn);
                alertList.appendChild(li);
            });
        } catch (e) {
            alertBanner.classList.remove('visible');
        }
    }

    // Toggle expand/collapse on banner click
    alertBannerSummary.addEventListener('click', function () {
        var expanded = alertBanner.classList.toggle('expanded');
        alertBannerSummary.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    });

    // Dismiss All button
    dismissAllBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        dismissAllAlerts();
    });

    // ---- Metric Selector ----
    // ---- Time Window Toggle ----
    timeButtons.forEach(function (btn) {
        btn.addEventListener('click', function () {
            timeButtons.forEach(function (b) { b.setAttribute('aria-pressed', 'false'); });
            btn.setAttribute('aria-pressed', 'true');
            currentDays = parseInt(btn.dataset.days, 10);
            scheduleRefresh();
        });
    });

    // Reference line helper: builds a flat-line dataset config
    function refLineDataset(color, axisID, dash) {
        return {
            data: [],
            borderColor: color,
            borderWidth: 1.5,
            borderDash: dash || [6, 3],
            pointRadius: 0,
            pointHoverRadius: 0,
            fill: false,
            tension: 0,
            yAxisID: axisID
        };
    }

    // Dataset indices:
    //   0=PC max (band top)  1=PC min (band bottom)  2=PC median
    //   3=NC max (band top)  4=NC min (band bottom)  5=NC median
    //   6=PC mean  7=PC +2SD  8=PC -2SD
    //   9=NC mean  10=NC +2SD  11=NC -2SD
    function bandDataset(axisID, fillColor) {
        return {
            data: [],
            borderColor: 'transparent',
            backgroundColor: fillColor,
            pointRadius: 0,
            pointHoverRadius: 0,
            fill: '+1',
            tension: 0.2,
            yAxisID: axisID
        };
    }

    function bandBoundary(axisID) {
        return {
            data: [],
            borderColor: 'transparent',
            pointRadius: 0,
            pointHoverRadius: 0,
            fill: false,
            tension: 0.2,
            yAxisID: axisID
        };
    }

    function initControlsChart() {
        if (!controlsCanvas) return;
        var ctx = controlsCanvas.getContext('2d');
        controlsChart = new Chart(ctx, {
            type: 'line',
            data: {
                datasets: [
                    bandDataset('pcAxis', 'rgba(245, 158, 11, 0.08)'),    // 0: PC max (band)
                    bandBoundary('pcAxis'),                                // 1: PC min
                    {                                                      // 2: PC median
                        label: 'PC',
                        data: [],
                        borderColor: 'rgba(245, 158, 11, 0.3)',
                        pointBorderColor: '#f59e0b',
                        borderWidth: 1.5,
                        pointRadius: 3,
                        pointHoverRadius: 5,
                        fill: false,
                        tension: 0.2,
                        yAxisID: 'pcAxis'
                    },
                    bandDataset('ncAxis', 'rgba(59, 130, 246, 0.08)'),    // 3: NC max (band)
                    bandBoundary('ncAxis'),                                // 4: NC min
                    {                                                      // 5: NC median
                        label: 'NC',
                        data: [],
                        borderColor: 'rgba(59, 130, 246, 0.3)',
                        pointBorderColor: '#3b82f6',
                        borderWidth: 1.5,
                        pointRadius: 3,
                        pointHoverRadius: 5,
                        fill: false,
                        tension: 0.2,
                        yAxisID: 'ncAxis'
                    },
                    refLineDataset('rgba(245, 158, 11, 0.7)', 'pcAxis', []),   // 6: PC mean
                    refLineDataset('rgba(245, 158, 11, 0.5)', 'pcAxis'),       // 7: PC +2SD
                    refLineDataset('rgba(245, 158, 11, 0.5)', 'pcAxis'),       // 8: PC -2SD
                    refLineDataset('rgba(59, 130, 246, 0.7)', 'ncAxis', []),   // 9: NC mean
                    refLineDataset('rgba(59, 130, 246, 0.5)', 'ncAxis'),       // 10: NC +2SD
                    refLineDataset('rgba(59, 130, 246, 0.5)', 'ncAxis')        // 11: NC -2SD
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'nearest',
                    intersect: false
                },
                scales: {
                    x: {
                        type: 'category',
                        grid: { color: 'rgba(30, 41, 59, 0.6)' },
                        ticks: {
                            color: '#64748b', font: { size: 11 }, minRotation: 45, maxRotation: 45, maxTicksLimit: 15, align: 'end',
                            callback: function (value, index) {
                                var label = this.getLabelForValue(value);
                                var prev = index > 0 ? this.getLabelForValue(index - 1) : null;
                                return label !== prev ? label : null;
                            }
                        }
                    },
                    pcAxis: {
                        position: 'left',
                        grid: { color: 'rgba(30, 41, 59, 0.6)' },
                        ticks: { color: '#64748b', font: { size: 11 } }
                    },
                    ncAxis: {
                        position: 'right',
                        grid: { drawOnChartArea: false },
                        ticks: { color: '#64748b', font: { size: 11 } }
                    }
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        align: 'center',
                        padding: 10,
                        onClick: function (e, legendItem, legend) {
                            var ci = legend.chart;
                            var idx = legendItem.datasetIndex;
                            var meta = ci.getDatasetMeta(idx);
                            meta.hidden = meta.hidden === null ? !ci.data.datasets[idx].hidden : null;
                            // PC(2) → band 0,1 + ref 6,7,8; NC(5) → band 3,4 + ref 9,10,11
                            var related = idx === 2 ? [0, 1, 6, 7, 8] : idx === 5 ? [3, 4, 9, 10, 11] : [];
                            related.forEach(function (ri) {
                                ci.getDatasetMeta(ri).hidden = meta.hidden;
                            });
                            ci.update();
                        },
                        labels: {
                            color: '#94a3b8',
                            font: { size: 12 },
                            padding: 20,
                            filter: function (item) { return item.datasetIndex === 2 || item.datasetIndex === 5; }
                        }
                    },
                    tooltip: {
                        backgroundColor: '#1e293b',
                        borderColor: '#273449',
                        borderWidth: 1,
                        titleColor: '#e2e8f0',
                        bodyColor: '#94a3b8',
                        filter: function (item) { return item.datasetIndex === 2 || item.datasetIndex === 5; },
                        callbacks: {
                            title: function (items) {
                                if (!items.length) return '';
                                var idx = items[0].dataIndex;
                                var chart = items[0].chart;
                                var date = chart.data.labels[idx] || '';
                                var run = chart._runNames ? chart._runNames[idx] || '' : '';
                                return run ? run + '  (' + date + ')' : date;
                            },
                            label: function (item) {
                                var m = item.chart._runMeta;
                                var idx = item.dataIndex;
                                if (m && item.datasetIndex === 2 && m.pc_median[idx] != null)
                                    return 'PC: ' + fmt(m.pc_median[idx]);
                                if (m && item.datasetIndex === 5 && m.nc_median[idx] != null)
                                    return 'NC: ' + fmt(m.nc_median[idx]);
                                return item.dataset.label + ': ' + fmt(item.raw);
                            },
                            afterBody: function (items) {
                                if (!items.length) return '';
                                var idx = items[0].dataIndex;
                                var chart = items[0].chart;
                                var m = chart._runMeta;
                                if (!m) return '';
                                var lines = [];
                                if (m.pc_min[idx] != null) lines.push('PC range: ' + fmt(m.pc_min[idx]) + ' \u2013 ' + fmt(m.pc_max[idx]));
                                if (m.nc_min[idx] != null) lines.push('NC range: ' + fmt(m.nc_min[idx]) + ' \u2013 ' + fmt(m.nc_max[idx]));
                                return lines;
                            }
                        }
                    }
                }
            }
        });
    }

    function initBeadCountsChart() {
        if (!beadCountsCanvas) return;
        var ctx = beadCountsCanvas.getContext('2d');
        beadCountsChart = new Chart(ctx, {
            type: 'line',
            data: {
                datasets: [
                    bandDataset('y', 'rgba(34, 197, 94, 0.08)'),  // 0: max (band top)
                    bandBoundary('y'),                              // 1: min (band bottom)
                    {                                               // 2: median line
                        label: 'Median',
                        data: [],
                        borderColor: 'rgba(34, 197, 94, 0.3)',
                        pointBorderColor: '#22c55e',
                        borderWidth: 1.5,
                        pointRadius: 3,
                        pointHoverRadius: 5,
                        fill: false,
                        tension: 0.2,
                        yAxisID: 'y'
                    },
                    refLineDataset('rgba(34, 197, 94, 0.7)', 'y', []),    // 3: mean
                    refLineDataset('rgba(34, 197, 94, 0.5)', 'y'),        // 4: +2SD
                    refLineDataset('rgba(34, 197, 94, 0.5)', 'y')         // 5: -2SD
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'nearest',
                    intersect: false
                },
                scales: {
                    x: {
                        type: 'category',
                        grid: { color: 'rgba(30, 41, 59, 0.6)' },
                        ticks: {
                            color: '#64748b', font: { size: 11 }, minRotation: 45, maxRotation: 45, maxTicksLimit: 15, align: 'end',
                            callback: function (value, index) {
                                var label = this.getLabelForValue(value);
                                var prev = index > 0 ? this.getLabelForValue(index - 1) : null;
                                return label !== prev ? label : null;
                            }
                        }
                    },
                    y: {
                        position: 'left',
                        grid: { color: 'rgba(30, 41, 59, 0.6)' },
                        ticks: { color: '#64748b', font: { size: 11 } }
                    }
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        align: 'center',
                        padding: 10,
                        labels: {
                            color: '#94a3b8',
                            font: { size: 12 },
                            padding: 20,
                            filter: function (item) { return item.datasetIndex === 2; }
                        }
                    },
                    tooltip: {
                        backgroundColor: '#1e293b',
                        borderColor: '#273449',
                        borderWidth: 1,
                        titleColor: '#e2e8f0',
                        bodyColor: '#94a3b8',
                        filter: function (item) { return item.datasetIndex === 2; },
                        callbacks: {
                            title: function (items) {
                                if (!items.length) return '';
                                var idx = items[0].dataIndex;
                                var chart = items[0].chart;
                                var date = chart.data.labels[idx] || '';
                                var run = chart._runNames ? chart._runNames[idx] || '' : '';
                                return run ? run + '  (' + date + ')' : date;
                            },
                            label: function (item) {
                                var m = item.chart._runMeta;
                                var idx = item.dataIndex;
                                if (m && m.median_count[idx] != null)
                                    return 'Median: ' + fmt(m.median_count[idx]);
                                return 'Median: ' + fmt(item.raw);
                            },
                            afterBody: function (items) {
                                if (!items.length) return '';
                                var idx = items[0].dataIndex;
                                var m = items[0].chart._runMeta;
                                if (!m) return '';
                                var lines = [];
                                if (m.min_count[idx] != null)
                                    lines.push('Range: ' + fmt(m.min_count[idx]) + ' \u2013 ' + fmt(m.max_count[idx]));
                                if (m.total_readings[idx] != null)
                                    lines.push('Total readings: ' + m.total_readings[idx]);
                                if (m.low_count_pct[idx] != null)
                                    lines.push('Low count (<25): ' + m.low_count_pct[idx] + '%');
                                return lines;
                            }
                        }
                    }
                }
            }
        });
    }

    async function loadBeadCounts(requestId) {
        if (!beadCountsChart) return;
        var beadLot = beadLotSelect.value;
        var group = catalogGroupSelect.value;
        if (!beadLot || !group) {
            setIdlePlaceholders('Select a catalog group and bead lot');
            return;
        }
        if (beadCountsPlaceholder) {
            beadCountsPlaceholder.innerHTML = '<span class="spinner"></span> Loading bead count data\u2026';
            beadCountsPlaceholder.style.display = '';
        }
        if (beadCountsCanvas) {
            beadCountsCanvas.style.display = 'none';
        }
        try {
            var data = await apiGet('/api/dashboard/qc/bead-counts?catalog_group=' + encodeURIComponent(group) + '&bead_lot=' + encodeURIComponent(beadLot) + '&days=' + currentDays);
            if (requestId !== qcRequestId) return;
            var labels = data.map(function (d) { return d.date; });
            var runNames = data.map(function (d) { return d.run_name; });
            var medianCount = data.map(function (d) { return d.median_count; });
            var minCount = data.map(function (d) { return d.min_count; });
            var maxCount = data.map(function (d) { return d.max_count; });
            var totalReadings = data.map(function (d) { return d.total_readings; });
            var lowCountPct = data.map(function (d) { return d.low_count_pct; });

            var medianValues = medianCount.filter(function (v) { return v != null; });
            var stats = calcMeanSD(medianValues);

            var pad = stats.sd > 0 ? 3 * stats.sd : (stats.mean * 0.1 || 10);
            var yLo = stats.mean - pad, yHi = stats.mean + pad;

            var OUTLIER_FILL = '#b91c1c';
            var fills = medianCount.map(function (v) {
                if (v == null) return 'transparent';
                return (v > stats.mean + 2 * stats.sd || v < stats.mean - 2 * stats.sd)
                    ? OUTLIER_FILL : 'transparent';
            });

            var nudge = pad * 0.015;
            function clampArr(arr, lo, hi) {
                return arr.map(function (v) {
                    if (v == null) return null;
                    return Math.max(lo, Math.min(hi, v));
                });
            }
            var medianPlot = clampArr(medianCount, yLo - nudge, yHi + nudge);

            beadCountsChart.options.scales.y.min = yLo;
            beadCountsChart.options.scales.y.max = yHi;

            beadCountsChart.data.labels = labels;
            beadCountsChart._runNames = runNames;
            beadCountsChart._runMeta = {
                median_count: medianCount, min_count: minCount, max_count: maxCount,
                total_readings: totalReadings, low_count_pct: lowCountPct
            };
            beadCountsChart.data.datasets[0].data = maxCount;
            beadCountsChart.data.datasets[1].data = minCount;
            beadCountsChart.data.datasets[2].data = medianPlot;
            beadCountsChart.data.datasets[2].pointBackgroundColor = fills;
            beadCountsChart.data.datasets[3].data = flatLine(labels, stats.mean);
            beadCountsChart.data.datasets[4].data = flatLine(labels, stats.mean + 2 * stats.sd);
            beadCountsChart.data.datasets[5].data = flatLine(labels, stats.mean - 2 * stats.sd);
            beadCountsChart.update('none');
            // MR sub-chart
            populateMRChart(beadCountsMRChart, document.getElementById('bead-counts-mr-wrapper'),
                document.getElementById('bead-counts-mr-caption'), labels, runNames,
                [{ values: medianCount, label: 'Bead count', key: 'main' }]);

            if (beadCountsPlaceholder) {
                if (!data.length) beadCountsPlaceholder.textContent = 'No bead count data for this selection';
                beadCountsPlaceholder.style.display = data.length ? 'none' : '';
            }
            beadCountsCanvas.style.display = data.length ? '' : 'none';
            if (beadCountsSubtitle) {
                beadCountsSubtitle.textContent = group + ' \u2014 lot ' + beadLot + ' \u2014 ' + (currentDays === 0 ? 'All time' : currentDays + 'd');
            }
            beadCountsLoadedKey = group + '|' + beadLot + '|' + currentDays;
        } catch (e) {
            if (beadCountsPlaceholder) {
                beadCountsPlaceholder.textContent = 'No bead count data available';
                beadCountsPlaceholder.style.display = '';
            }
        }
    }

    function initSnRatioChart() {
        if (!snRatioCanvas) return;
        var ctx = snRatioCanvas.getContext('2d');
        snRatioChart = new Chart(ctx, {
            type: 'line',
            data: {
                datasets: [
                    bandDataset('y', 'rgba(168, 85, 247, 0.08)'),  // 0: max (band top)
                    bandBoundary('y'),                               // 1: min (band bottom)
                    {                                                // 2: median line
                        label: 'S/N Median',
                        data: [],
                        borderColor: 'rgba(168, 85, 247, 0.3)',
                        pointBorderColor: '#a855f7',
                        borderWidth: 1.5,
                        pointRadius: 3,
                        pointHoverRadius: 5,
                        fill: false,
                        tension: 0.2,
                        yAxisID: 'y'
                    },
                    refLineDataset('rgba(168, 85, 247, 0.7)', 'y', []),    // 3: mean
                    refLineDataset('rgba(168, 85, 247, 0.5)', 'y'),        // 4: +2SD
                    refLineDataset('rgba(168, 85, 247, 0.5)', 'y')         // 5: -2SD
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'nearest',
                    intersect: false
                },
                scales: {
                    x: {
                        type: 'category',
                        grid: { color: 'rgba(30, 41, 59, 0.6)' },
                        ticks: {
                            color: '#64748b', font: { size: 11 }, minRotation: 45, maxRotation: 45, maxTicksLimit: 15, align: 'end',
                            callback: function (value, index) {
                                var label = this.getLabelForValue(value);
                                var prev = index > 0 ? this.getLabelForValue(index - 1) : null;
                                return label !== prev ? label : null;
                            }
                        }
                    },
                    y: {
                        position: 'left',
                        grid: { color: 'rgba(30, 41, 59, 0.6)' },
                        ticks: { color: '#64748b', font: { size: 11 } }
                    }
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        align: 'center',
                        padding: 10,
                        labels: {
                            color: '#94a3b8',
                            font: { size: 12 },
                            padding: 20,
                            filter: function (item) { return item.datasetIndex === 2; }
                        }
                    },
                    tooltip: {
                        backgroundColor: '#1e293b',
                        borderColor: '#273449',
                        borderWidth: 1,
                        titleColor: '#e2e8f0',
                        bodyColor: '#94a3b8',
                        filter: function (item) { return item.datasetIndex === 2; },
                        callbacks: {
                            title: function (items) {
                                if (!items.length) return '';
                                var idx = items[0].dataIndex;
                                var chart = items[0].chart;
                                var date = chart.data.labels[idx] || '';
                                var run = chart._runNames ? chart._runNames[idx] || '' : '';
                                return run ? run + '  (' + date + ')' : date;
                            },
                            label: function (item) {
                                var m = item.chart._runMeta;
                                var idx = item.dataIndex;
                                if (m && m.sn_median[idx] != null)
                                    return 'S/N: ' + fmt(m.sn_median[idx]);
                                return 'S/N: ' + fmt(item.raw);
                            },
                            afterBody: function (items) {
                                if (!items.length) return '';
                                var idx = items[0].dataIndex;
                                var m = items[0].chart._runMeta;
                                if (!m) return '';
                                var lines = [];
                                if (m.sn_min[idx] != null)
                                    lines.push('Range: ' + fmt(m.sn_min[idx]) + ' \u2013 ' + fmt(m.sn_max[idx]));
                                if (m.well_count[idx] != null)
                                    lines.push('Wells: ' + m.well_count[idx]);
                                return lines;
                            }
                        }
                    }
                }
            }
        });
    }

    // ---- QC Samples Charts (3 separate charts) ----
    // Shared tooltip title callback for QC charts
    function qcTooltipTitle(items) {
        if (!items.length) return '';
        var idx = items[0].dataIndex;
        var chart = items[0].chart;
        var date = chart.data.labels[idx] || '';
        var m = chart._runMeta;
        var sample = m && m.sample_names ? m.sample_names[idx] || '' : '';
        var run = chart._runNames ? chart._runNames[idx] || '' : '';
        var parts = [];
        if (sample) parts.push(sample);
        if (run) parts.push(run);
        return parts.length ? parts.join(' \u2014 ') + '  (' + date + ')' : date;
    }

    // Datasets: 0=median MFI, 1=mean ref, 2=+2SD ref, 3=-2SD ref
    function initQcRoleChart(canvas, color, label) {
        if (!canvas) return null;
        var ctx = canvas.getContext('2d');
        return new Chart(ctx, {
            type: 'line',
            data: {
                datasets: [
                    {                                                       // 0: median MFI
                        label: label,
                        data: [],
                        borderColor: color.line,
                        pointBorderColor: color.point,
                        borderWidth: 1.5,
                        pointRadius: 3,
                        pointHoverRadius: 5,
                        fill: false,
                        tension: 0.2,
                        yAxisID: 'y'
                    },
                    refLineDataset(color.refSolid, 'y', []),               // 1: mean
                    refLineDataset(color.refDash, 'y'),                    // 2: +2SD
                    refLineDataset(color.refDash, 'y')                     // 3: -2SD
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'nearest', intersect: false },
                scales: {
                    x: {
                        type: 'category',
                        grid: { color: 'rgba(30, 41, 59, 0.6)' },
                        ticks: {
                            color: '#64748b', font: { size: 11 }, minRotation: 45, maxRotation: 45, maxTicksLimit: 15, align: 'end',
                            callback: function (value, index) {
                                var l = this.getLabelForValue(value);
                                var prev = index > 0 ? this.getLabelForValue(index - 1) : null;
                                return l !== prev ? l : null;
                            }
                        }
                    },
                    y: {
                        position: 'left',
                        grid: { color: 'rgba(30, 41, 59, 0.6)' },
                        ticks: { color: '#64748b', font: { size: 11 } }
                    }
                },
                plugins: {
                    legend: {
                        display: true, position: 'top', align: 'center', padding: 10,
                        labels: {
                            color: '#94a3b8', font: { size: 12 }, padding: 20,
                            filter: function (item) { return item.datasetIndex === 0; }
                        }
                    },
                    tooltip: {
                        backgroundColor: '#1e293b', borderColor: '#273449', borderWidth: 1,
                        titleColor: '#e2e8f0', bodyColor: '#94a3b8',
                        filter: function (item) { return item.datasetIndex === 0; },
                        callbacks: {
                            title: qcTooltipTitle,
                            label: function (item) {
                                var m = item.chart._runMeta;
                                var idx = item.dataIndex;
                                if (m && m.median_mfi[idx] != null)
                                    return label + ' MFI: ' + fmt(m.median_mfi[idx]);
                                return label + ' MFI: ' + fmt(item.raw);
                            },
                            afterBody: function (items) {
                                if (!items.length) return '';
                                var idx = items[0].dataIndex;
                                var m = items[0].chart._runMeta;
                                if (!m) return '';
                                var lines = [];
                                if (m.pc[idx] != null) lines.push('PC: ' + fmt(m.pc[idx]));
                                if (m.nc[idx] != null) lines.push('NC: ' + fmt(m.nc[idx]));
                                if (m.sn_ratio[idx] != null) lines.push('S/N: ' + fmt(m.sn_ratio[idx]));
                                return lines;
                            }
                        }
                    }
                }
            }
        });
    }

    function initQcPosChart() {
        qcPosChart = initQcRoleChart(qcPosCanvas, {
            line: 'rgba(245, 158, 11, 0.3)', point: '#f59e0b',
            refSolid: 'rgba(245, 158, 11, 0.7)', refDash: 'rgba(245, 158, 11, 0.5)'
        }, 'Positive QC');
    }

    function initQcNegChart() {
        qcNegChart = initQcRoleChart(qcNegCanvas, {
            line: 'rgba(59, 130, 246, 0.3)', point: '#3b82f6',
            refSolid: 'rgba(59, 130, 246, 0.7)', refDash: 'rgba(59, 130, 246, 0.5)'
        }, 'Negative QC');
    }

    function initQcTrackedChart() {
        if (!qcTrackedCanvas) return;
        var ctx = qcTrackedCanvas.getContext('2d');
        qcTrackedChart = new Chart(ctx, {
            type: 'line',
            data: { datasets: [] },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'nearest', intersect: false },
                scales: {
                    x: {
                        type: 'category',
                        grid: { color: 'rgba(30, 41, 59, 0.6)' },
                        ticks: {
                            color: '#64748b', font: { size: 11 }, minRotation: 45, maxRotation: 45, maxTicksLimit: 15, align: 'end',
                            callback: function (value, index) {
                                var l = this.getLabelForValue(value);
                                var prev = index > 0 ? this.getLabelForValue(index - 1) : null;
                                return l !== prev ? l : null;
                            }
                        }
                    },
                    y: {
                        position: 'left',
                        grid: { color: 'rgba(30, 41, 59, 0.6)' },
                        ticks: { color: '#64748b', font: { size: 11 } }
                    }
                },
                plugins: {
                    legend: {
                        display: true, position: 'top', align: 'center', padding: 10,
                        labels: { color: '#94a3b8', font: { size: 12 }, padding: 20 }
                    },
                    tooltip: {
                        backgroundColor: '#1e293b', borderColor: '#273449', borderWidth: 1,
                        titleColor: '#e2e8f0', bodyColor: '#94a3b8',
                        callbacks: {
                            title: function (items) {
                                if (!items.length) return '';
                                var idx = items[0].dataIndex;
                                var chart = items[0].chart;
                                var ds = items[0].dataset;
                                var isNeg = ds._role === 'negative';
                                var date = isNeg
                                    ? (chart.options.scales.xNeg && chart.options.scales.xNeg.labels ? chart.options.scales.xNeg.labels[idx] || '' : '')
                                    : (chart.data.labels[idx] || '');
                                var sample = isNeg
                                    ? (chart._negSampleNames ? chart._negSampleNames[idx] || '' : '')
                                    : (chart._runMeta && chart._runMeta.sample_names ? chart._runMeta.sample_names[idx] || '' : '');
                                var run = isNeg
                                    ? (chart._negRunNames ? chart._negRunNames[idx] || '' : '')
                                    : (chart._runNames ? chart._runNames[idx] || '' : '');
                                var parts = [];
                                if (sample) parts.push(sample);
                                if (run) parts.push(run);
                                return parts.length ? parts.join(' \u2014 ') + '  (' + date + ')' : date;
                            },
                            label: function (item) {
                                return item.dataset.label + ': ' + fmt(item.raw);
                            }
                        }
                    }
                }
            }
        });
    }

    var TRACKED_BEAD_COLORS = [
        '#22c55e', '#a855f7', '#ec4899', '#06b6d4', '#f97316', '#84cc16'
    ];

    // Helper: populate a single-axis QC role chart (pos or neg)
    function populateQcRoleChart(chart, canvas, placeholder, entries, allLabels, allRunNames, allSampleNames, allPc, allNc, allSn) {
        if (!chart) return;
        var medianArr = allLabels.map(function (_, i) { return entries.indexMap[i] != null ? entries.values[entries.indexMap[i]] : null; });
        var values = medianArr.filter(function (v) { return v != null; });
        if (!values.length) {
            if (placeholder) { placeholder.textContent = 'No data'; placeholder.style.display = ''; }
            if (canvas) canvas.style.display = 'none';
            chart.data.labels = [];
            chart.update('none');
            return;
        }
        var stats = calcMeanSD(values);
        var pad = stats.sd > 0 ? 3 * stats.sd : (stats.mean * 0.1 || 100);
        var yLo = stats.mean - pad, yHi = stats.mean + pad;
        var nudge = pad * 0.015;
        var OUTLIER_FILL = '#b91c1c';
        var fills = medianArr.map(function (v) {
            if (v == null) return 'transparent';
            return (v > stats.mean + 2 * stats.sd || v < stats.mean - 2 * stats.sd) ? OUTLIER_FILL : 'transparent';
        });
        var medianPlot = medianArr.map(function (v) {
            if (v == null) return null;
            return Math.max(yLo - nudge, Math.min(yHi + nudge, v));
        });

        chart.options.scales.y.min = yLo;
        chart.options.scales.y.max = yHi;
        chart.data.labels = allLabels;
        chart._runNames = allRunNames;
        chart._runMeta = { median_mfi: medianArr, pc: allPc, nc: allNc, sn_ratio: allSn, sample_names: allSampleNames };
        chart.data.datasets[0].data = medianPlot;
        chart.data.datasets[0].pointBackgroundColor = fills;
        chart.data.datasets[1].data = flatLine(allLabels, stats.mean);
        chart.data.datasets[2].data = flatLine(allLabels, stats.mean + 2 * stats.sd);
        chart.data.datasets[3].data = flatLine(allLabels, stats.mean - 2 * stats.sd);
        chart.update('none');

        if (placeholder) placeholder.style.display = 'none';
        if (canvas) canvas.style.display = '';
    }

    async function loadQcSamples(requestId) {
        if (!qcPosChart && !qcNegChart && !qcTrackedChart) return;
        var beadLot = beadLotSelect.value;
        var group = catalogGroupSelect.value;
        if (!beadLot || !group) {
            [qcPosPlaceholder, qcNegPlaceholder, qcTrackedPlaceholder].forEach(function (p) {
                if (p) { p.textContent = 'Select a catalog group and bead lot'; p.style.display = ''; }
            });
            [qcPosCanvas, qcNegCanvas, qcTrackedCanvas].forEach(function (c) {
                if (c) c.style.display = 'none';
            });
            return;
        }
        [qcPosPlaceholder, qcNegPlaceholder, qcTrackedPlaceholder].forEach(function (p) {
            if (p) { p.innerHTML = '<span class="spinner"></span> Loading QC sample data\u2026'; p.style.display = ''; }
        });
        [qcPosCanvas, qcNegCanvas, qcTrackedCanvas].forEach(function (c) {
            if (c) c.style.display = 'none';
        });

        try {
            var data = await apiGet('/api/dashboard/qc/sample-trend?catalog_group=' + encodeURIComponent(group) + '&bead_lot=' + encodeURIComponent(beadLot) + '&days=' + currentDays);
            if (requestId !== qcRequestId) return;

            if (!data.length) {
                [qcPosPlaceholder, qcNegPlaceholder].forEach(function (p) {
                    if (p) { p.textContent = 'No QC sample data for this selection (check Settings)'; p.style.display = ''; }
                });
                if (qcTrackedPlaceholder) { qcTrackedPlaceholder.textContent = 'No tracked bead data'; qcTrackedPlaceholder.style.display = ''; }
                [qcPosCanvas, qcNegCanvas, qcTrackedCanvas].forEach(function (c) {
                    if (c) c.style.display = 'none';
                });
                qcSamplesLoadedKey = group + '|' + beadLot + '|' + currentDays;
                return;
            }

            // Sort all entries by datetime
            var allEntries = data.slice().sort(function (a, b) { return a.datetime < b.datetime ? -1 : 1; });
            var allLabels = allEntries.map(function (d) { return d.date; });
            var allRunNames = allEntries.map(function (d) { return d.run_name; });
            var allSampleNames = allEntries.map(function (d) { return d.sample_name; });
            var allPc = allEntries.map(function (d) { return d.pc; });
            var allNc = allEntries.map(function (d) { return d.nc; });
            var allSn = allEntries.map(function (d) { return d.sn_ratio; });

            // Build index maps for pos/neg entries back to the unified label array
            var posValues = [], posIndexMap = {};
            var negValues = [], negIndexMap = {};
            allEntries.forEach(function (d, i) {
                if (d.role === 'positive') { posIndexMap[i] = posValues.length; posValues.push(d.median_mfi); }
                if (d.role === 'negative') { negIndexMap[i] = negValues.length; negValues.push(d.median_mfi); }
            });

            // Populate positive chart
            populateQcRoleChart(qcPosChart, qcPosCanvas, qcPosPlaceholder,
                { values: posValues, indexMap: posIndexMap },
                allLabels, allRunNames, allSampleNames, allPc, allNc, allSn);

            // Populate negative chart
            populateQcRoleChart(qcNegChart, qcNegCanvas, qcNegPlaceholder,
                { values: negValues, indexMap: negIndexMap },
                allLabels, allRunNames, allSampleNames, allPc, allNc, allSn);

            // Populate tracked beads chart — split by role (pos vs neg)
            if (qcTrackedChart) {
                var trackedBeadIds = new Set();
                allEntries.forEach(function (d) {
                    if (d.tracked_bead_mfi) {
                        Object.keys(d.tracked_bead_mfi).forEach(function (bid) { trackedBeadIds.add(bid); });
                    }
                });
                var beadIdArr = Array.from(trackedBeadIds).sort();

                // Build separate label arrays for pos-only and neg-only points
                var posIndices = [], negIndices = [];
                allEntries.forEach(function (d, i) {
                    if (d.role === 'positive') posIndices.push(i);
                    else if (d.role === 'negative') negIndices.push(i);
                });
                var posLabels = posIndices.map(function (i) { return allLabels[i]; });
                var negLabels = negIndices.map(function (i) { return allLabels[i]; });
                var posRunNames = posIndices.map(function (i) { return allRunNames[i]; });
                var negRunNames = negIndices.map(function (i) { return allRunNames[i]; });
                var posSampleNames = posIndices.map(function (i) { return allSampleNames[i]; });
                var negSampleNames = negIndices.map(function (i) { return allSampleNames[i]; });

                // Use pos labels as the primary x-axis; neg gets its own hidden axis
                qcTrackedChart.data.labels = posLabels;
                qcTrackedChart._runNames = posRunNames;
                qcTrackedChart._runMeta = { sample_names: posSampleNames };
                qcTrackedChart._negRunNames = negRunNames;
                qcTrackedChart._negSampleNames = negSampleNames;
                qcTrackedChart.data.datasets = [];

                beadIdArr.forEach(function (bid, idx) {
                    var color = TRACKED_BEAD_COLORS[idx % TRACKED_BEAD_COLORS.length];
                    // Positive role — solid line
                    var posData = posIndices.map(function (i) {
                        var d = allEntries[i];
                        return (d.tracked_bead_mfi && d.tracked_bead_mfi[bid] != null) ? d.tracked_bead_mfi[bid] : null;
                    });
                    qcTrackedChart.data.datasets.push({
                        label: 'Bead ' + bid + ' (Pos)',
                        data: posData,
                        borderColor: color,
                        pointBorderColor: color,
                        borderWidth: 1.5,
                        pointRadius: 3,
                        pointHoverRadius: 5,
                        fill: false,
                        tension: 0.2,
                        xAxisID: 'x',
                        yAxisID: 'y',
                        _role: 'positive'
                    });
                    // Negative role — dashed line
                    var negData = negIndices.map(function (i) {
                        var d = allEntries[i];
                        return (d.tracked_bead_mfi && d.tracked_bead_mfi[bid] != null) ? d.tracked_bead_mfi[bid] : null;
                    });
                    qcTrackedChart.data.datasets.push({
                        label: 'Bead ' + bid + ' (Neg)',
                        data: negData,
                        borderColor: color,
                        pointBorderColor: color,
                        borderWidth: 1.5,
                        borderDash: [5, 3],
                        pointRadius: 3,
                        pointHoverRadius: 5,
                        pointStyle: 'rectRot',
                        fill: false,
                        tension: 0.2,
                        xAxisID: 'xNeg',
                        yAxisID: 'y',
                        _role: 'negative'
                    });
                });

                // Add hidden neg x-axis with matching labels
                qcTrackedChart.options.scales.xNeg = {
                    type: 'category',
                    labels: negLabels,
                    display: false
                };

                qcTrackedChart.update('none');

                if (beadIdArr.length) {
                    if (qcTrackedPlaceholder) qcTrackedPlaceholder.style.display = 'none';
                    if (qcTrackedCanvas) qcTrackedCanvas.style.display = '';
                } else {
                    if (qcTrackedPlaceholder) { qcTrackedPlaceholder.textContent = 'No tracked beads configured for this selection'; qcTrackedPlaceholder.style.display = ''; }
                    if (qcTrackedCanvas) qcTrackedCanvas.style.display = 'none';
                }
                if (qcTrackedSubtitle) {
                    qcTrackedSubtitle.textContent = group + ' \u2014 lot ' + beadLot + ' \u2014 ' + (currentDays === 0 ? 'All time' : currentDays + 'd');
                }
            }

            if (qcPosSubtitle) {
                qcPosSubtitle.textContent = group + ' \u2014 lot ' + beadLot + ' \u2014 ' + (currentDays === 0 ? 'All time' : currentDays + 'd');
            }
            if (qcNegSubtitle) {
                qcNegSubtitle.textContent = group + ' \u2014 lot ' + beadLot + ' \u2014 ' + (currentDays === 0 ? 'All time' : currentDays + 'd');
            }
            qcSamplesLoadedKey = group + '|' + beadLot + '|' + currentDays;
        } catch (e) {
            [qcPosPlaceholder, qcNegPlaceholder].forEach(function (p) {
                if (p) { p.textContent = 'No QC sample data available'; p.style.display = ''; }
            });
            if (qcTrackedPlaceholder) { qcTrackedPlaceholder.textContent = 'No tracked bead data available'; qcTrackedPlaceholder.style.display = ''; }
        }
    }

    async function loadSnRatio(requestId) {
        if (!snRatioChart) return;
        var beadLot = beadLotSelect.value;
        var group = catalogGroupSelect.value;
        if (!beadLot || !group) {
            setIdlePlaceholders('Select a catalog group and bead lot');
            return;
        }
        if (snRatioPlaceholder) {
            snRatioPlaceholder.innerHTML = '<span class="spinner"></span> Loading S/N ratio data\u2026';
            snRatioPlaceholder.style.display = '';
        }
        if (snRatioCanvas) {
            snRatioCanvas.style.display = 'none';
        }
        try {
            var data = await apiGet('/api/dashboard/qc/sn-ratio?catalog_group=' + encodeURIComponent(group) + '&bead_lot=' + encodeURIComponent(beadLot) + '&days=' + currentDays);
            if (requestId !== qcRequestId) return;
            var labels = data.map(function (d) { return d.date; });
            var runNames = data.map(function (d) { return d.run_name; });
            var snMedian = data.map(function (d) { return d.sn_median; });
            var snMin = data.map(function (d) { return d.sn_min; });
            var snMax = data.map(function (d) { return d.sn_max; });
            var wellCount = data.map(function (d) { return d.well_count; });

            var medianValues = snMedian.filter(function (v) { return v != null; });
            var stats = calcMeanSD(medianValues);

            var pad = stats.sd > 0 ? 3 * stats.sd : (stats.mean * 0.1 || 1);
            var yLo = stats.mean - pad, yHi = stats.mean + pad;

            var OUTLIER_FILL = '#b91c1c';
            var fills = snMedian.map(function (v) {
                if (v == null) return 'transparent';
                return (v > stats.mean + 2 * stats.sd || v < stats.mean - 2 * stats.sd)
                    ? OUTLIER_FILL : 'transparent';
            });

            var nudge = pad * 0.015;
            function clampArr(arr, lo, hi) {
                return arr.map(function (v) {
                    if (v == null) return null;
                    return Math.max(lo, Math.min(hi, v));
                });
            }
            var medianPlot = clampArr(snMedian, yLo - nudge, yHi + nudge);

            snRatioChart.options.scales.y.min = yLo;
            snRatioChart.options.scales.y.max = yHi;

            snRatioChart.data.labels = labels;
            snRatioChart._runNames = runNames;
            snRatioChart._runMeta = {
                sn_median: snMedian, sn_min: snMin, sn_max: snMax,
                well_count: wellCount
            };
            snRatioChart.data.datasets[0].data = snMax;
            snRatioChart.data.datasets[1].data = snMin;
            snRatioChart.data.datasets[2].data = medianPlot;
            snRatioChart.data.datasets[2].pointBackgroundColor = fills;
            snRatioChart.data.datasets[3].data = flatLine(labels, stats.mean);
            snRatioChart.data.datasets[4].data = flatLine(labels, stats.mean + 2 * stats.sd);
            snRatioChart.data.datasets[5].data = flatLine(labels, stats.mean - 2 * stats.sd);
            snRatioChart.update('none');
            // MR sub-chart
            populateMRChart(snRatioMRChart, document.getElementById('sn-ratio-mr-wrapper'),
                document.getElementById('sn-ratio-mr-caption'), labels, runNames,
                [{ values: snMedian, label: 'S/N', key: 'main' }]);

            if (snRatioPlaceholder) {
                if (!data.length) snRatioPlaceholder.textContent = 'No S/N ratio data for this selection';
                snRatioPlaceholder.style.display = data.length ? 'none' : '';
            }
            snRatioCanvas.style.display = data.length ? '' : 'none';
            if (snRatioSubtitle) {
                snRatioSubtitle.textContent = group + ' \u2014 lot ' + beadLot + ' \u2014 ' + (currentDays === 0 ? 'All time' : currentDays + 'd');
            }
            snRatioLoadedKey = group + '|' + beadLot + '|' + currentDays;
        } catch (e) {
            if (snRatioPlaceholder) {
                snRatioPlaceholder.textContent = 'No S/N ratio data available';
                snRatioPlaceholder.style.display = '';
            }
        }
    }

    async function loadBeadLots() {
        try {
            var group = catalogGroupSelect.value;
            if (!group) {
                beadLotSelect.innerHTML = '<option value="">Select group first</option>';
                setIdlePlaceholders('Select a catalog group and bead lot');
                return;
            }
            var data = await apiGet('/api/dashboard/qc/bead-lots?catalog_group=' + encodeURIComponent(group) + '&days=0');
            var lotPrefs = {};
            try { lotPrefs = await apiGet('/api/config/bead-lot-prefs'); } catch (e) {}
            beadLotSelect.innerHTML = '';
            if (!data.length) {
                var opt = document.createElement('option');
                opt.textContent = 'No bead lots';
                opt.disabled = true;
                beadLotSelect.appendChild(opt);
                setIdlePlaceholders('No bead lots available for ' + group);
                return;
            }
            // Apply prefs: filter hidden, apply saved order
            var filtered;
            var groupPrefs = (lotPrefs && lotPrefs[group]) || [];
            if (groupPrefs.length) {
                var prefMap = {};
                groupPrefs.forEach(function (p) { prefMap[p.lot] = p; });
                var ordered = [];
                groupPrefs.forEach(function (p) {
                    if (data.indexOf(p.lot) !== -1 && p.visible !== false) {
                        ordered.push(p.lot);
                    }
                });
                data.forEach(function (l) {
                    if (!prefMap[l]) ordered.push(l);
                });
                filtered = ordered;
            } else {
                filtered = data; // already reverse-sorted by backend
            }
            var placeholder = document.createElement('option');
            placeholder.value = '';
            placeholder.textContent = 'Select bead lot\u2026';
            beadLotSelect.appendChild(placeholder);
            filtered.forEach(function (lot) {
                var opt = document.createElement('option');
                opt.value = lot;
                opt.textContent = lot;
                beadLotSelect.appendChild(opt);
            });
            setIdlePlaceholders('Select a bead lot to view charts');
        } catch (e) {
            // silent
        }
    }

    function catalogGroupOrder(name) {
        var upper = name.toUpperCase();
        // LS#PRA => PRA panels (group 2)
        if (/^LS\d.*PRA/.test(upper)) return 2;
        // LSM / LSMUTR => Screens (group 1)
        if (/^LSM/.test(upper)) return 1;
        // LS#xxx (digit after LS, not PRA, not LSM) => Single antigens (group 0)
        if (/^LS\d/.test(upper)) return 0;
        // Everything else (group 3)
        return 3;
    }

    function sortCatalogGroups(groups) {
        return groups.slice().sort(function (a, b) {
            var oa = catalogGroupOrder(a), ob = catalogGroupOrder(b);
            return oa !== ob ? oa - ob : a.localeCompare(b);
        });
    }

    async function loadCatalogGroups() {
        try {
            var data = await apiGet('/api/dashboard/qc/catalog-groups?days=0');
            var prefs = [];
            try { prefs = await apiGet('/api/config/catalog-group-prefs'); } catch (e) {}
            catalogGroupSelect.innerHTML = '';
            if (!data.length) {
                var opt = document.createElement('option');
                opt.textContent = 'No catalog groups';
                opt.disabled = true;
                catalogGroupSelect.appendChild(opt);
                setIdlePlaceholders('No catalog groups available');
                return;
            }
            // Apply prefs: filter hidden, apply saved order
            var filtered;
            if (prefs && prefs.length) {
                var prefMap = {};
                prefs.forEach(function (p) { prefMap[p.group] = p; });
                // Saved groups in order, then unsaved groups at end
                var ordered = [];
                prefs.forEach(function (p) {
                    if (data.indexOf(p.group) !== -1 && p.visible !== false) {
                        ordered.push(p.group);
                    }
                });
                data.forEach(function (g) {
                    if (!prefMap[g]) ordered.push(g);
                });
                filtered = ordered;
            } else {
                filtered = sortCatalogGroups(data);
            }
            var placeholder = document.createElement('option');
            placeholder.value = '';
            placeholder.textContent = 'Select catalog group\u2026';
            catalogGroupSelect.appendChild(placeholder);
            filtered.forEach(function (grp) {
                var opt = document.createElement('option');
                opt.value = grp;
                opt.textContent = grp;
                catalogGroupSelect.appendChild(opt);
            });
        } catch (e) {
            // silent
        }
    }

    catalogGroupSelect.addEventListener('change', function () {
        loadBeadLots();
    });

    beadLotSelect.addEventListener('change', function () {
        scheduleRefresh();
    });

    // ---- Tab Switching ----
    metricTabButtons.forEach(function (btn) {
        btn.addEventListener('click', function () {
            var tab = btn.dataset.tab;
            if (tab === activeTab) return;
            activeTab = tab;
            metricTabButtons.forEach(function (b) {
                b.classList.toggle('active', b.dataset.tab === tab);
                b.setAttribute('aria-selected', b.dataset.tab === tab ? 'true' : 'false');
            });
            metricTabPanels.forEach(function (p) {
                p.classList.toggle('active', p.dataset.tabPanel === tab);
            });
            loadActiveTab();
        });
    });

    function setIdlePlaceholders(message) {
        if (controlsPlaceholder) {
            controlsPlaceholder.textContent = message;
            controlsPlaceholder.style.display = '';
        }
        if (controlsCanvas) {
            controlsCanvas.style.display = 'none';
        }
        if (beadCountsPlaceholder) {
            beadCountsPlaceholder.textContent = message;
            beadCountsPlaceholder.style.display = '';
        }
        if (beadCountsCanvas) {
            beadCountsCanvas.style.display = 'none';
        }
        if (snRatioPlaceholder) {
            snRatioPlaceholder.textContent = message;
            snRatioPlaceholder.style.display = '';
        }
        if (snRatioCanvas) {
            snRatioCanvas.style.display = 'none';
        }
        [qcPosPlaceholder, qcNegPlaceholder, qcTrackedPlaceholder].forEach(function (p) {
            if (p) { p.textContent = message; p.style.display = ''; }
        });
        [qcPosCanvas, qcNegCanvas, qcTrackedCanvas].forEach(function (c) {
            if (c) c.style.display = 'none';
        });
    }

    async function loadControls(requestId) {
        if (!controlsChart) return;
        var beadLot = beadLotSelect.value;
        var group = catalogGroupSelect.value;
        if (!beadLot || !group) {
            setIdlePlaceholders('Select a catalog group and bead lot');
            return;
        }
        if (controlsPlaceholder) {
            controlsPlaceholder.innerHTML = '<span class="spinner"></span> Loading control data\u2026';
            controlsPlaceholder.style.display = '';
        }
        if (controlsCanvas) {
            controlsCanvas.style.display = 'none';
        }
        try {
            var data = await apiGet('/api/dashboard/qc/controls?catalog_group=' + encodeURIComponent(group) + '&bead_lot=' + encodeURIComponent(beadLot) + '&days=' + currentDays);
            if (requestId !== qcRequestId) return;
            var labels = data.map(function (d) { return d.date; });
            var runNames = data.map(function (d) { return d.run_name; });
            var pcMedian = data.map(function (d) { return d.pc_median; });
            var pcMin = data.map(function (d) { return d.pc_min; });
            var pcMax = data.map(function (d) { return d.pc_max; });
            var ncMedian = data.map(function (d) { return d.nc_median; });
            var ncMin = data.map(function (d) { return d.nc_min; });
            var ncMax = data.map(function (d) { return d.nc_max; });

            // Compute mean ± 2SD for Levey-Jennings reference lines (based on medians)
            var pcValues = pcMedian.filter(function (v) { return v != null; });
            var ncValues = ncMedian.filter(function (v) { return v != null; });
            var pcStats = calcMeanSD(pcValues);
            var ncStats = calcMeanSD(ncValues);

            // Axis and clamp bounds: mean ± 3SD (outliers poke outside via clip:false)
            var pcPad = pcStats.sd > 0 ? 3 * pcStats.sd : (pcStats.mean * 0.1 || 100);
            var ncPad = ncStats.sd > 0 ? 3 * ncStats.sd : (ncStats.mean * 0.1 || 100);
            var pcLo = pcStats.mean - pcPad, pcHi = pcStats.mean + pcPad;
            var ncLo = ncStats.mean - ncPad, ncHi = ncStats.mean + ncPad;

            // Fill outliers outside ±2SD with muted red (based on real medians)
            var OUTLIER_FILL = '#b91c1c';
            var pcFills = pcMedian.map(function (v) {
                if (v == null) return 'transparent';
                return (v > pcStats.mean + 2 * pcStats.sd || v < pcStats.mean - 2 * pcStats.sd)
                    ? OUTLIER_FILL : 'transparent';
            });
            var ncFills = ncMedian.map(function (v) {
                if (v == null) return 'transparent';
                return (v > ncStats.mean + 2 * ncStats.sd || v < ncStats.mean - 2 * ncStats.sd)
                    ? OUTLIER_FILL : 'transparent';
            });

            // Clamp outliers slightly beyond axis so ~1/3 of circle peeks at edge
            var pcNudge = pcPad * 0.015;
            var ncNudge = ncPad * 0.015;
            function clamp(arr, lo, hi) {
                return arr.map(function (v) {
                    if (v == null) return null;
                    return Math.max(lo, Math.min(hi, v));
                });
            }
            var pcMedianPlot = clamp(pcMedian, pcLo - pcNudge, pcHi + pcNudge);
            var ncMedianPlot = clamp(ncMedian, ncLo - ncNudge, ncHi + ncNudge);

            // Set axis bounds at mean ± 3SD; outliers poke outside via clip:false
            controlsChart.options.scales.pcAxis.min = pcLo;
            controlsChart.options.scales.pcAxis.max = pcHi;
            controlsChart.options.scales.ncAxis.min = ncLo;
            controlsChart.options.scales.ncAxis.max = ncHi;

            controlsChart.data.labels = labels;
            controlsChart._runNames = runNames;
            controlsChart._runMeta = {
                pc_median: pcMedian, pc_min: pcMin, pc_max: pcMax,
                nc_median: ncMedian, nc_min: ncMin, nc_max: ncMax
            };
            controlsChart.data.datasets[0].data = pcMax;             // band top (Chart.js clips at axis edge)
            controlsChart.data.datasets[1].data = pcMin;             // band bottom
            controlsChart.data.datasets[2].data = pcMedianPlot;      // median points (clamped)
            controlsChart.data.datasets[2].pointBackgroundColor = pcFills;
            controlsChart.data.datasets[3].data = ncMax;             // band top
            controlsChart.data.datasets[4].data = ncMin;             // band bottom
            controlsChart.data.datasets[5].data = ncMedianPlot;      // median points (clamped)
            controlsChart.data.datasets[5].pointBackgroundColor = ncFills;
            controlsChart.data.datasets[6].data = flatLine(labels, pcStats.mean);
            controlsChart.data.datasets[7].data = flatLine(labels, pcStats.mean + 2 * pcStats.sd);
            controlsChart.data.datasets[8].data = flatLine(labels, pcStats.mean - 2 * pcStats.sd);
            controlsChart.data.datasets[9].data = flatLine(labels, ncStats.mean);
            controlsChart.data.datasets[10].data = flatLine(labels, ncStats.mean + 2 * ncStats.sd);
            controlsChart.data.datasets[11].data = flatLine(labels, ncStats.mean - 2 * ncStats.sd);
            controlsChart.update('none');
            // MR sub-chart
            populateMRChart(controlsMRChart, document.getElementById('controls-mr-wrapper'),
                document.getElementById('controls-mr-caption'), labels, runNames,
                [{ values: pcMedian, label: 'PC', key: 'pc' }, { values: ncMedian, label: 'NC', key: 'nc' }]);

            if (controlsPlaceholder) {
                if (!data.length) controlsPlaceholder.textContent = 'No control data for this selection';
                controlsPlaceholder.style.display = data.length ? 'none' : '';
            }
            controlsCanvas.style.display = data.length ? '' : 'none';
            if (controlsSubtitle) {
                controlsSubtitle.textContent = group + ' \u2014 lot ' + beadLot + ' \u2014 ' + (currentDays === 0 ? 'All time' : currentDays + 'd');
            }
            controlsLoadedKey = group + '|' + beadLot + '|' + currentDays;
        } catch (e) {
            if (controlsPlaceholder) {
                controlsPlaceholder.textContent = 'No control data available';
                controlsPlaceholder.style.display = '';
            }
        }
    }

    function loadActiveTab() {
        var group = catalogGroupSelect.value;
        var lot = beadLotSelect.value;
        if (!group || !lot) return;

        var key = group + '|' + lot + '|' + currentDays;

        if (activeTab === 'controls') {
            if (controlsLoadedKey !== key) {
                qcRequestId += 1;
                loadControls(qcRequestId);
            }
        } else if (activeTab === 'bead-counts') {
            if (beadCountsLoadedKey !== key) {
                qcRequestId += 1;
                loadBeadCounts(qcRequestId);
            }
        } else if (activeTab === 'sn-ratio') {
            if (snRatioLoadedKey !== key) {
                qcRequestId += 1;
                loadSnRatio(qcRequestId);
            }
        } else if (activeTab === 'qc-samples') {
            if (qcSamplesLoadedKey !== key) {
                qcRequestId += 1;
                loadQcSamples(qcRequestId);
            }
        }
    }

    function scheduleRefresh() {
        if (refreshTimer) {
            clearTimeout(refreshTimer);
        }
        controlsLoadedKey = null;
        beadCountsLoadedKey = null;
        snRatioLoadedKey = null;
        qcSamplesLoadedKey = null;
        refreshTimer = setTimeout(function () {
            loadActiveTab();
        }, 300);
    }

    // ---- Refresh ----
    function startRefresh() {
        statusTimer = setInterval(function () {
            loadAlerts();
        }, 60000);

        dataTimer = setInterval(function () {
            controlsLoadedKey = null;
            beadCountsLoadedKey = null;
            snRatioLoadedKey = null;
            qcSamplesLoadedKey = null;
            loadActiveTab();
        }, 300000);
    }

    // ---- PNG Export Click Handler ----
    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.btn-export');
        if (!btn) return;
        var key = btn.dataset.export;
        var chartExportMap = {
            'controls': controlsChart,
            'bead-counts': beadCountsChart,
            'sn-ratio': snRatioChart,
            'qc-pos': qcPosChart,
            'qc-neg': qcNegChart,
            'qc-tracked': qcTrackedChart,
            'controls-mr': controlsMRChart,
            'bead-counts-mr': beadCountsMRChart,
            'sn-ratio-mr': snRatioMRChart
        };
        var chart = chartExportMap[key];
        if (chart) exportChartPNG(chart, key);
    });

    // ---- Moving Range Sub-Charts ----
    function createMRChart(canvas, datasets) {
        var ctx = canvas.getContext('2d');
        return new Chart(ctx, {
            type: 'line',
            data: { datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'nearest', intersect: false },
                scales: {
                    x: {
                        type: 'category',
                        grid: { color: 'rgba(30, 41, 59, 0.6)' },
                        ticks: {
                            color: '#64748b', font: { size: 10 }, minRotation: 45, maxRotation: 45, maxTicksLimit: 15, align: 'end',
                            callback: function (value, index) {
                                var label = this.getLabelForValue(value);
                                var prev = index > 0 ? this.getLabelForValue(index - 1) : null;
                                return label !== prev ? label : null;
                            }
                        }
                    },
                    y: {
                        position: 'left',
                        grid: { color: 'rgba(30, 41, 59, 0.6)' },
                        ticks: { color: '#fbbf24', font: { size: 10 } },
                        title: { display: true, text: 'Moving Range', color: '#fbbf24', font: { size: 11 } }
                    }
                },
                plugins: {
                    legend: {
                        display: true, position: 'top', align: 'center', padding: 10,
                        labels: {
                            color: '#94a3b8', font: { size: 11 }, padding: 15,
                            filter: function (item, data) { return data.datasets[item.datasetIndex]._mrRole === 'data'; }
                        }
                    },
                    tooltip: {
                        backgroundColor: '#1e293b', borderColor: '#273449', borderWidth: 1,
                        titleColor: '#e2e8f0', bodyColor: '#94a3b8',
                        filter: function (item) { return item.dataset._mrRole === 'data'; },
                        callbacks: {
                            title: function (items) {
                                if (!items.length) return '';
                                var idx = items[0].dataIndex;
                                var chart = items[0].chart;
                                var date = chart.data.labels[idx] || '';
                                var run = chart._runNames ? chart._runNames[idx] || '' : '';
                                return run ? run + '  (' + date + ')' : date;
                            },
                            label: function (item) {
                                var ds = item.dataset;
                                var ucl = item.chart._mrUCLs && item.chart._mrUCLs[ds._mrKey] || 0;
                                var prefix = ds.label || 'MR';
                                return prefix + ': ' + fmt(item.raw) + (item.raw > ucl ? ' exceeds UCL' : '');
                            }
                        }
                    }
                }
            }
        });
    }

    function mrDataset(label, color, pointColor, key, axisID) {
        return {
            label: label, data: [], borderColor: color, backgroundColor: 'transparent',
            borderWidth: 1.5, borderDash: [4, 2], pointRadius: 2, pointHoverRadius: 4,
            pointBackgroundColor: pointColor, fill: false, tension: 0,
            yAxisID: axisID || 'y',
            _mrRole: 'data', _mrKey: key
        };
    }

    function mrMeanDataset(color, key, axisID) {
        return Object.assign(refLineDataset(color, axisID || 'y', []), { _mrRole: 'mean', _mrKey: key });
    }

    function mrUCLDataset(key, axisID, color) {
        return Object.assign(refLineDataset(color || 'rgba(239, 68, 68, 0.6)', axisID || 'y'), { _mrRole: 'ucl', _mrKey: key });
    }

    function initControlsMRChart() {
        var canvas = document.getElementById('controls-mr-chart');
        if (!canvas) return;
        controlsMRChart = createMRChart(canvas, [
            mrDataset('PC MR', 'rgba(251, 191, 36, 0.75)', '#fbbf24', 'pc', 'pcMR'),
            mrMeanDataset('rgba(251, 191, 36, 0.5)', 'pc', 'pcMR'),
            mrUCLDataset('pc', 'pcMR', 'rgba(251, 191, 36, 0.9)'),
            mrDataset('NC MR', 'rgba(34, 211, 238, 0.75)', '#22d3ee', 'nc', 'ncMR'),
            mrMeanDataset('rgba(34, 211, 238, 0.5)', 'nc', 'ncMR'),
            mrUCLDataset('nc', 'ncMR', 'rgba(34, 211, 238, 0.9)')
        ]);
        // Replace single y axis with dual PC/NC MR axes
        delete controlsMRChart.options.scales.y;
        controlsMRChart.options.scales.pcMR = {
            position: 'left',
            grid: { color: 'rgba(30, 41, 59, 0.6)' },
            ticks: { color: '#fbbf24', font: { size: 10 } },
            title: { display: true, text: 'PC MR', color: '#fbbf24', font: { size: 10 } }
        };
        controlsMRChart.options.scales.ncMR = {
            position: 'right',
            grid: { drawOnChartArea: false },
            ticks: { color: '#22d3ee', font: { size: 10 } },
            title: { display: true, text: 'NC MR', color: '#22d3ee', font: { size: 10 } }
        };
        controlsMRChart.update('none');
    }

    function initBeadCountsMRChart() {
        var canvas = document.getElementById('bead-counts-mr-chart');
        if (!canvas) return;
        beadCountsMRChart = createMRChart(canvas, [
            mrDataset('Moving Range', 'rgba(251, 191, 36, 0.75)', '#fbbf24', 'main'),
            mrMeanDataset('rgba(251, 191, 36, 0.5)', 'main'),
            mrUCLDataset('main')
        ]);
    }

    function initSnRatioMRChart() {
        var canvas = document.getElementById('sn-ratio-mr-chart');
        if (!canvas) return;
        snRatioMRChart = createMRChart(canvas, [
            mrDataset('Moving Range', 'rgba(251, 191, 36, 0.75)', '#fbbf24', 'main'),
            mrMeanDataset('rgba(251, 191, 36, 0.5)', 'main'),
            mrUCLDataset('main')
        ]);
    }

    function populateMRChart(chart, wrapper, captionEl, labels, runNames, series) {
        if (!chart || !wrapper) return;
        // series: [{values, label, key}, ...]
        var dsIdx = 0;
        var summaryParts = [];
        chart._mrUCLs = {};
        for (var s = 0; s < series.length; s++) {
            var mrData = calcMovingRange(series[s].values);
            chart.data.datasets[dsIdx].data = mrData.mr;
            chart.data.datasets[dsIdx + 1].data = flatLine(labels, mrData.meanMR);
            chart.data.datasets[dsIdx + 2].data = flatLine(labels, mrData.ucl);
            chart._mrUCLs[series[s].key] = mrData.ucl;
            summaryParts.push(mrSummaryText(mrData, series[s].label));
            dsIdx += 3;
        }
        chart.data.labels = labels;
        chart._runNames = runNames;
        chart.update('none');
        if (captionEl) {
            captionEl.textContent = summaryParts.filter(function (t) { return t; }).join(' ') || '';
        }
    }

    function toggleMR(tab, show) {
        var wrapperMap = {
            'controls': document.getElementById('controls-mr-wrapper'),
            'bead-counts': document.getElementById('bead-counts-mr-wrapper'),
            'sn-ratio': document.getElementById('sn-ratio-mr-wrapper')
        };
        var wrapper = wrapperMap[tab];
        if (wrapper) wrapper.style.display = show ? '' : 'none';
    }

    document.querySelectorAll('[data-mr]').forEach(function (cb) {
        cb.addEventListener('change', function () {
            var tab = cb.getAttribute('data-mr');
            var show = cb.checked;
            localStorage.setItem('bw-mr-' + tab, show ? '1' : '0');
            toggleMR(tab, show);
        });
    });

    function restoreMRState() {
        ['controls', 'bead-counts', 'sn-ratio'].forEach(function (tab) {
            var saved = localStorage.getItem('bw-mr-' + tab);
            if (saved === '1') {
                var cb = document.querySelector('[data-mr="' + tab + '"]');
                if (cb) cb.checked = true;
                toggleMR(tab, true);
            }
        });
    }

    // ---- Init ----
    async function init() {
        initControlsChart();
        initBeadCountsChart();
        initSnRatioChart();
        initQcPosChart();
        initQcNegChart();
        initQcTrackedChart();
        initControlsMRChart();
        initBeadCountsMRChart();
        initSnRatioMRChart();
        restoreMRState();
        await loadVersion();
        await loadAlerts();
        setIdlePlaceholders('Loading catalog groups\u2026');
        await loadCatalogGroups();
        setIdlePlaceholders('Select a catalog group and bead lot');
        startRefresh();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
