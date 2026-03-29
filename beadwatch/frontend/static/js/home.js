// BeadWatch — Home Page Logic

(function () {
    'use strict';

    var versionEl = document.getElementById('app-version');
    var alertsContainer = document.getElementById('alerts-container');

    async function apiGet(url) {
        var response = await fetch(url);
        if (!response.ok) throw new Error('API error: ' + response.status);
        return response.json();
    }

    function fmt(value) {
        if (value === null || value === undefined) return '\u2014';
        return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
    }

    async function loadVersion() {
        try {
            var data = await apiGet('/health');
            versionEl.textContent = 'v' + data.version;
        } catch (e) {
            versionEl.textContent = '';
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

    async function dismissAlert(alertId) {
        try {
            await fetch('/api/dashboard/alerts/' + alertId + '/acknowledge', { method: 'POST' });
            await loadAlerts();
        } catch (e) { /* keep alert visible */ }
    }

    async function dismissAllAlerts() {
        try {
            await fetch('/api/dashboard/alerts/acknowledge-all', { method: 'POST' });
            await loadAlerts();
        } catch (e) { /* ignore */ }
    }

    async function loadAlerts() {
        try {
            var alerts = await apiGet('/api/dashboard/alerts/active');
            if (!alerts.length) {
                alertsContainer.innerHTML = '<span class="no-alerts">No active alerts</span>';
                return;
            }

            var header = document.createElement('div');
            header.style.cssText = 'display:flex;align-items:center;margin-bottom:0.5rem;';
            var countSpan = document.createElement('span');
            countSpan.style.cssText = 'font-size:0.8125rem;color:var(--slate-400);';
            countSpan.textContent = alerts.length + ' active alert' + (alerts.length !== 1 ? 's' : '');
            var dismissAllBtn = document.createElement('button');
            dismissAllBtn.className = 'btn-dismiss-all';
            dismissAllBtn.textContent = 'Dismiss All';
            dismissAllBtn.addEventListener('click', function () { dismissAllAlerts(); });
            header.appendChild(countSpan);
            header.appendChild(dismissAllBtn);

            var ul = document.createElement('ul');
            ul.className = 'alert-list';
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
                dismissBtn.addEventListener('click', function () { dismissAlert(a.id); });
                li.appendChild(sevSpan);
                li.appendChild(descSpan);
                li.appendChild(dismissBtn);
                ul.appendChild(li);
            });
            alertsContainer.innerHTML = '';
            alertsContainer.appendChild(header);
            alertsContainer.appendChild(ul);
        } catch (e) {
            alertsContainer.innerHTML = '<span class="no-alerts">Could not load alerts</span>';
        }
    }

    async function init() {
        await loadVersion();
        await loadAlerts();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
