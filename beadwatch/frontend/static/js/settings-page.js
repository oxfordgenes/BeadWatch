// BeadWatch — Settings Page Logic

(function () {
    'use strict';

    // ---- Helpers ----
    async function apiGet(url) {
        var response = await fetch(url);
        if (!response.ok) throw new Error('API error: ' + response.status);
        return response.json();
    }

    function showMsg(el, text, type) {
        el.textContent = text;
        el.style.display = 'block';
        if (type === 'ok') {
            el.style.color = 'var(--green)';
            el.style.background = 'rgba(34, 197, 94, 0.08)';
        } else {
            el.style.color = 'var(--red)';
            el.style.background = 'rgba(239, 68, 68, 0.08)';
        }
    }

    // ---- Version ----
    var versionEl = document.getElementById('app-version');

    async function loadVersion() {
        try {
            var data = await apiGet('/health');
            versionEl.textContent = 'v' + data.version;
        } catch (e) {
            versionEl.textContent = '';
        }
    }

    // ---- Connection Info ----
    async function loadConnectionInfo() {
        try {
            var data = await apiGet('/api/config/status');
            document.getElementById('conn-server').textContent = data.server || '\u2014';
            document.getElementById('conn-vendor-db').textContent = data.vendor_database || '\u2014';
            document.getElementById('conn-norm-db').textContent = data.normalized_database || '\u2014';
            document.getElementById('conn-username').textContent = data.username || '\u2014';
        } catch (e) {
            // leave defaults
        }
    }

    // ---- Polling Status ----
    var pollStatusDot = document.getElementById('poll-status-dot');
    var pollStatusText = document.getElementById('poll-status-text');

    function humanAge(ms) {
        var s = Math.round(ms / 1000);
        if (s < 60) return s + 's ago';
        var m = Math.round(s / 60);
        if (m < 60) return m + 'm ago';
        var h = Math.round(m / 60);
        if (h < 24) return h + 'h ago';
        return Math.round(h / 24) + 'd ago';
    }

    async function loadPollStatus() {
        try {
            var data = await apiGet('/api/dashboard/status');
            var status = data.status || 'critical';
            pollStatusDot.setAttribute('data-status', status);
            var label = status.charAt(0).toUpperCase() + status.slice(1);
            if (data.last_poll) {
                var ago = new Date() - new Date(data.last_poll);
                pollStatusText.textContent = label + ' \u2014 last run ' + humanAge(ago);
            } else {
                pollStatusText.textContent = label + ' \u2014 no runs yet';
            }
        } catch (e) {
            pollStatusDot.setAttribute('data-status', 'critical');
            pollStatusText.textContent = 'Cannot reach server';
        }
    }

    // ---- Polling Interval ----
    var pollInput = document.getElementById('poll-interval');
    var pollSaveBtn = document.getElementById('poll-save');
    var pollMsg = document.getElementById('poll-msg');

    async function loadPollingInterval() {
        try {
            var data = await apiGet('/api/config/polling-interval');
            pollInput.value = data.interval_minutes;
            pollInput.min = data.min;
            pollInput.max = data.max;
        } catch (e) {}
    }

    if (pollSaveBtn) {
        pollSaveBtn.addEventListener('click', function () {
            var val = parseInt(pollInput.value, 10);
            if (isNaN(val) || val < 5 || val > 1440) {
                pollMsg.textContent = 'Must be between 5 and 1440';
                pollMsg.style.display = 'block';
                pollMsg.style.color = 'var(--red)';
                pollMsg.style.background = 'rgba(239, 68, 68, 0.08)';
                return;
            }
            pollSaveBtn.disabled = true;
            fetch('/api/config/polling-interval', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ interval_minutes: val })
            })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    pollSaveBtn.disabled = false;
                    if (data.success) {
                        pollMsg.textContent = 'Saved \u2014 polling every ' + data.interval_minutes + ' min';
                        pollMsg.style.color = 'var(--green)';
                        pollMsg.style.background = 'rgba(34, 197, 94, 0.08)';
                    } else {
                        pollMsg.textContent = 'Save failed';
                        pollMsg.style.color = 'var(--red)';
                        pollMsg.style.background = 'rgba(239, 68, 68, 0.08)';
                    }
                    pollMsg.style.display = 'block';
                })
                .catch(function () {
                    pollSaveBtn.disabled = false;
                    pollMsg.textContent = 'Save failed \u2014 network error';
                    pollMsg.style.color = 'var(--red)';
                    pollMsg.style.background = 'rgba(239, 68, 68, 0.08)';
                    pollMsg.style.display = 'block';
                });
        });
    }

    // ---- Alert Thresholds ----
    var thresholdList = document.getElementById('threshold-list');
    var thresholdMsg = document.getElementById('threshold-msg');

    var METRIC_LABELS = {
        'min_bead_count': 'Minimum Bead Count',
        'mean_mfi': 'Mean MFI',
        'signal_to_noise': 'Signal-to-Noise Ratio',
        'negative_control_mfi': 'Negative Control MFI'
    };

    function renderThresholds(rows) {
        if (!rows.length) {
            thresholdList.innerHTML = '<p class="section-hint">No thresholds configured.</p>';
            return;
        }
        var html = '<table class="threshold-table">';
        html += '<thead><tr>'
            + '<th>Metric</th>'
            + '<th class="num">Lower Crit</th>'
            + '<th class="num">Lower Warn</th>'
            + '<th class="num">Upper Warn</th>'
            + '<th class="num">Upper Crit</th>'
            + '<th class="ctr">Enabled</th>'
            + '<th></th>'
            + '</tr></thead><tbody>';

        rows.forEach(function (t) {
            var label = METRIC_LABELS[t.metric_name] || t.metric_name;
            html += '<tr data-id="' + t.id + '">'
                + '<td>' + label + '</td>'
                + '<td class="num"><input type="number" class="form-input th-input th-lc" value="' + (t.lower_critical != null ? t.lower_critical : '') + '"></td>'
                + '<td class="num"><input type="number" class="form-input th-input th-lw" value="' + (t.lower_warning != null ? t.lower_warning : '') + '"></td>'
                + '<td class="num"><input type="number" class="form-input th-input th-uw" value="' + (t.upper_warning != null ? t.upper_warning : '') + '"></td>'
                + '<td class="num"><input type="number" class="form-input th-input th-uc" value="' + (t.upper_critical != null ? t.upper_critical : '') + '"></td>'
                + '<td class="ctr"><input type="checkbox" class="th-enabled"' + (t.enabled ? ' checked' : '') + '></td>'
                + '<td><button class="btn-ghost th-save">Save</button></td>'
                + '</tr>';
        });

        html += '</tbody></table>';
        thresholdList.innerHTML = html;

        thresholdList.querySelectorAll('.th-save').forEach(function (btn) {
            btn.addEventListener('click', function () {
                var tr = btn.closest('tr');
                saveThreshold(tr);
            });
        });
    }

    async function saveThreshold(tr) {
        var id = tr.getAttribute('data-id');
        var lc = tr.querySelector('.th-lc').value;
        var lw = tr.querySelector('.th-lw').value;
        var uw = tr.querySelector('.th-uw').value;
        var uc = tr.querySelector('.th-uc').value;
        var enabled = tr.querySelector('.th-enabled').checked;

        var body = {
            lower_critical: lc === '' ? null : parseFloat(lc),
            lower_warning: lw === '' ? null : parseFloat(lw),
            upper_warning: uw === '' ? null : parseFloat(uw),
            upper_critical: uc === '' ? null : parseFloat(uc),
            enabled: enabled
        };

        try {
            var resp = await fetch('/api/config/thresholds/' + id, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body)
            });
            if (!resp.ok) {
                var errData = await resp.json().catch(function () { return {}; });
                throw new Error(errData.detail || 'API error: ' + resp.status);
            }
            // Reload the full table to pick up any server-side coercion.
            loadThresholds();
            showMsg(thresholdMsg, 'Saved', 'ok');
        } catch (e) {
            showMsg(thresholdMsg, 'Save failed: ' + e.message, 'err');
        }
    }

    async function loadThresholds() {
        try {
            var rows = await apiGet('/api/config/thresholds');
            renderThresholds(rows);
        } catch (e) {
            // normalized DB may not be initialized yet
        }
    }

    // ---- QC Sample Definitions CRUD ----
    function loadQcDefinitions() {
        var list = document.getElementById('qc-def-list');
        if (!list) return;
        fetch('/api/config/qc-sample-definitions')
            .then(function (r) { return r.json(); })
            .then(function (defs) {
                list.innerHTML = '';
                if (!defs.length) {
                    list.innerHTML = '<div style="font-size: 0.75rem; color: var(--slate-500); padding: 0.25rem 0;">No patterns defined</div>';
                    return;
                }
                defs.sort(function (a, b) {
                    if (a.role === b.role) return 0;
                    return a.role === 'positive' ? -1 : 1;
                });
                defs.forEach(function (d) {
                    var row = document.createElement('div');
                    row.style.cssText = 'display:flex;align-items:center;gap:0.5rem;padding:0.25rem 0;font-size:0.8125rem;border-bottom:1px solid var(--navy-600);';
                    var roleColor = d.role === 'positive' ? 'var(--amber)' : 'var(--blue-accent)';
                    row.innerHTML = '<span style="color:' + roleColor + ';font-weight:600;width:4rem;font-size:0.6875rem;text-transform:uppercase;">' + d.role + '</span>' +
                        '<span style="color:var(--slate-200);font-family:var(--font-mono);flex:1;">' + d.pattern + '</span>' +
                        '<span style="color:var(--slate-500);font-size:0.6875rem;">' + d.match_type + '</span>';
                    var delBtn = document.createElement('button');
                    delBtn.className = 'btn-ghost';
                    delBtn.textContent = '\u00d7';
                    delBtn.style.cssText = 'padding:0.125rem 0.375rem;font-size:0.875rem;line-height:1;';
                    delBtn.addEventListener('click', function () {
                        fetch('/api/config/qc-sample-definitions/' + d.id, { method: 'DELETE' })
                            .then(function () { loadQcDefinitions(); });
                    });
                    row.appendChild(delBtn);
                    list.appendChild(row);
                });
            })
            .catch(function () {});
    }

    var qcDefAddBtn = document.getElementById('qc-def-add');
    if (qcDefAddBtn) {
        qcDefAddBtn.addEventListener('click', function () {
            var pattern = document.getElementById('qc-def-pattern').value.trim();
            var role = document.getElementById('qc-def-role').value;
            if (!pattern) return;
            fetch('/api/config/qc-sample-definitions', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pattern: pattern, role: role, match_type: 'substring' })
            })
                .then(function () {
                    document.getElementById('qc-def-pattern').value = '';
                    loadQcDefinitions();
                })
                .catch(function () {});
        });
    }

    // ---- Catalog Group / Bead Lot Selects (shared for discovery + tracked beads) ----
    var discoverGroupSel = document.getElementById('discover-group');
    var discoverLotSel = document.getElementById('discover-lot');
    var beadGroupSel = document.getElementById('bead-group');
    var beadLotSel = document.getElementById('bead-lot');

    // Cache the full lots list so we can filter by group
    var allLots = {};

    async function loadCatalogGroups() {
        try {
            var groups = await apiGet('/api/dashboard/qc/catalog-groups');
            [discoverGroupSel, beadGroupSel].forEach(function (sel) {
                if (!sel) return;
                sel.innerHTML = '<option value="">Catalog Group</option>';
                groups.forEach(function (g) {
                    var opt = document.createElement('option');
                    opt.value = g;
                    opt.textContent = g;
                    sel.appendChild(opt);
                });
            });
        } catch (e) {}
    }

    async function loadBeadLots(group, lotSelect) {
        if (!lotSelect) return;
        lotSelect.innerHTML = '<option value="">Bead Lot</option>';
        if (!group) return;
        try {
            if (!allLots[group]) {
                allLots[group] = await apiGet('/api/dashboard/qc/bead-lots?catalog_group=' + encodeURIComponent(group));
            }
            allLots[group].forEach(function (l) {
                var opt = document.createElement('option');
                opt.value = l;
                opt.textContent = l;
                lotSelect.appendChild(opt);
            });
        } catch (e) {}
    }

    if (discoverGroupSel) {
        discoverGroupSel.addEventListener('change', function () {
            loadBeadLots(discoverGroupSel.value, discoverLotSel);
        });
    }
    if (beadGroupSel) {
        beadGroupSel.addEventListener('change', function () {
            loadBeadLots(beadGroupSel.value, beadLotSel);
        });
    }

    // ---- Sample Name Discovery ----
    var discoverBtn = document.getElementById('discover-btn');
    if (discoverBtn) {
        discoverBtn.addEventListener('click', function () {
            var group = discoverGroupSel ? discoverGroupSel.value : '';
            var lot = discoverLotSel ? discoverLotSel.value : '';
            var list = document.getElementById('discover-list');
            if (!list) return;
            if (!group || !lot) {
                list.innerHTML = '<div style="font-size: 0.75rem; color: var(--amber); padding: 0.25rem 0;">Select a catalog group and bead lot first</div>';
                return;
            }
            list.innerHTML = '<div style="font-size: 0.75rem; color: var(--slate-500); padding: 0.25rem 0;"><span class="spinner"></span> Loading\u2026</div>';
            fetch('/api/dashboard/qc/sample-names?catalog_group=' + encodeURIComponent(group) + '&bead_lot=' + encodeURIComponent(lot) + '&days=0')
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    list.innerHTML = '';
                    if (!data.length) {
                        list.innerHTML = '<div style="font-size: 0.75rem; color: var(--slate-500); padding: 0.25rem 0;">No samples found</div>';
                        return;
                    }
                    var table = document.createElement('table');
                    table.style.cssText = 'width:100%;font-size:0.75rem;border-collapse:collapse;';
                    var thead = '<tr style="color:var(--slate-400);text-align:left;border-bottom:1px solid var(--navy-600);">' +
                        '<th style="padding:0.25rem 0.375rem;">SampleIDName</th>' +
                        '<th style="padding:0.25rem 0.375rem;">PatientID</th></tr>';
                    table.innerHTML = thead;
                    data.forEach(function (row) {
                        var tr = document.createElement('tr');
                        tr.style.cssText = 'border-bottom:1px solid var(--navy-700);';
                        var tdName = document.createElement('td');
                        tdName.style.cssText = 'padding:0.25rem 0.375rem;color:var(--slate-200);font-family:var(--font-mono);';
                        tdName.textContent = row.sample_name || '\u2014';
                        var tdPid = document.createElement('td');
                        tdPid.style.cssText = 'padding:0.25rem 0.375rem;color:var(--slate-300);font-family:var(--font-mono);';
                        tdPid.textContent = row.patient_id || '\u2014';
                        tr.appendChild(tdName);
                        tr.appendChild(tdPid);
                        table.appendChild(tr);
                    });
                    list.appendChild(table);
                })
                .catch(function () {
                    list.innerHTML = '<div style="font-size: 0.75rem; color: var(--red); padding: 0.25rem 0;">Failed to load</div>';
                });
        });
    }

    // ---- Tracked Beads CRUD ----
    function loadQcTrackedBeads() {
        var list = document.getElementById('qc-bead-list');
        if (!list) return;
        fetch('/api/config/qc-tracked-beads')
            .then(function (r) { return r.json(); })
            .then(function (beads) {
                list.innerHTML = '';
                if (!beads.length) {
                    list.innerHTML = '<div style="font-size: 0.75rem; color: var(--slate-500); padding: 0.25rem 0;">No tracked beads</div>';
                    return;
                }
                // Group by catalog_group + bead_lot
                var groups = {};
                beads.forEach(function (b) {
                    var key = b.catalog_group + ' / ' + b.bead_lot;
                    if (!groups[key]) groups[key] = { catalog_group: b.catalog_group, bead_lot: b.bead_lot, beads: [] };
                    groups[key].beads.push(b);
                });
                Object.keys(groups).forEach(function (key) {
                    var g = groups[key];
                    var section = document.createElement('div');
                    section.className = 'tb-group open';

                    var header = document.createElement('div');
                    header.className = 'tb-group-header';
                    var chevron = document.createElement('span');
                    chevron.className = 'tb-chevron';
                    chevron.textContent = '\u25b6';
                    var label = document.createElement('span');
                    label.className = 'tb-group-label';
                    label.textContent = key;
                    var count = document.createElement('span');
                    count.className = 'tb-group-count';
                    count.textContent = g.beads.length + (g.beads.length === 1 ? ' bead' : ' beads');
                    var delAll = document.createElement('button');
                    delAll.className = 'tb-group-del';
                    delAll.textContent = '\u00d7 all';
                    delAll.addEventListener('click', function (e) {
                        e.stopPropagation();
                        if (!confirm('Delete all ' + g.beads.length + ' tracked beads for ' + key + '?')) return;
                        fetch('/api/config/qc-tracked-beads/group/' + encodeURIComponent(g.catalog_group) + '/' + encodeURIComponent(g.bead_lot), { method: 'DELETE' })
                            .then(function () { loadQcTrackedBeads(); });
                    });
                    header.appendChild(chevron);
                    header.appendChild(label);
                    header.appendChild(count);
                    header.appendChild(delAll);
                    header.addEventListener('click', function () {
                        section.classList.toggle('open');
                    });

                    var chips = document.createElement('div');
                    chips.className = 'tb-chips';
                    g.beads.forEach(function (b) {
                        var chip = document.createElement('span');
                        chip.className = 'tb-chip';
                        chip.textContent = b.bead_id + (b.label ? ' (' + b.label + ')' : '');
                        var del = document.createElement('button');
                        del.className = 'tb-chip-del';
                        del.textContent = '\u00d7';
                        del.addEventListener('click', function () {
                            fetch('/api/config/qc-tracked-beads/' + b.id, { method: 'DELETE' })
                                .then(function () { loadQcTrackedBeads(); });
                        });
                        chip.appendChild(del);
                        chips.appendChild(chip);
                    });

                    section.appendChild(header);
                    section.appendChild(chips);
                    list.appendChild(section);
                });
            })
            .catch(function () {});
    }

    var qcBeadAddBtn = document.getElementById('qc-bead-add');
    if (qcBeadAddBtn) {
        qcBeadAddBtn.addEventListener('click', function () {
            var group = beadGroupSel ? beadGroupSel.value : '';
            var lot = beadLotSel ? beadLotSel.value : '';
            var bid = document.getElementById('qc-bead-id').value.trim();
            var label = document.getElementById('qc-bead-label').value.trim();
            if (!group || !lot || !bid) return;
            fetch('/api/config/qc-tracked-beads', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ catalog_group: group, bead_lot: lot, bead_id: bid, label: label || null })
            })
                .then(function () {
                    document.getElementById('qc-bead-id').value = '';
                    document.getElementById('qc-bead-label').value = '';
                    loadQcTrackedBeads();
                })
                .catch(function () {});
        });
    }

    // ---- Excluded Instruments CRUD ----
    var excludedList = document.getElementById('excluded-list');
    var exclSnInput = document.getElementById('excl-sn');
    var exclLabelInput = document.getElementById('excl-label');
    var exclAddBtn = document.getElementById('excl-add');

    function loadExcluded() {
        if (!excludedList) return;
        fetch('/api/config/excluded-instruments')
            .then(function (r) { return r.json(); })
            .then(function (items) {
                if (!items.length) {
                    excludedList.innerHTML = '<span style="font-size: 0.75rem; color: var(--slate-500);">No instruments excluded.</span>';
                    return;
                }
                var html = '';
                items.forEach(function (item) {
                    html += '<span class="excl-chip">';
                    html += item.serial_number;
                    if (item.label) html += ' <span class="excl-chip-label">(' + item.label + ')</span>';
                    html += ' <button data-excl-id="' + item.id + '" title="Remove">&times;</button>';
                    html += '</span>';
                });
                excludedList.innerHTML = html;
            })
            .catch(function () {
                if (excludedList) excludedList.innerHTML = '<span style="font-size: 0.75rem; color: var(--red);">Failed to load.</span>';
            });
    }

    if (exclAddBtn) {
        exclAddBtn.addEventListener('click', async function () {
            var sn = exclSnInput ? exclSnInput.value.trim() : '';
            if (!sn) return;
            var label = exclLabelInput ? exclLabelInput.value.trim() : '';
            exclAddBtn.disabled = true;
            try {
                await fetch('/api/config/excluded-instruments', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ serial_number: sn, label: label || null })
                });
                if (exclSnInput) exclSnInput.value = '';
                if (exclLabelInput) exclLabelInput.value = '';
                await loadExcluded();
            } finally {
                exclAddBtn.disabled = false;
            }
        });
    }

    if (excludedList) {
        excludedList.addEventListener('click', async function (e) {
            var btn = e.target.closest('[data-excl-id]');
            if (!btn) return;
            var id = btn.dataset.exclId;
            btn.disabled = true;
            try {
                await fetch('/api/config/excluded-instruments/' + id, { method: 'DELETE' });
                await loadExcluded();
            } finally {
                btn.disabled = false;
            }
        });
    }

    // ---- Instrument Aliases CRUD ----
    var aliasList = document.getElementById('alias-list');
    var aliasSnInput = document.getElementById('alias-sn');
    var aliasNameInput = document.getElementById('alias-name');
    var aliasAddBtn = document.getElementById('alias-add');

    function loadAliases() {
        if (!aliasList) return;
        fetch('/api/config/instrument-aliases')
            .then(function (r) { return r.json(); })
            .then(function (items) {
                if (!items.length) {
                    aliasList.innerHTML = '<span style="font-size: 0.75rem; color: var(--slate-500);">No nicknames defined.</span>';
                    return;
                }
                var html = '';
                items.forEach(function (item) {
                    html += '<span class="excl-chip">';
                    html += item.display_name;
                    html += ' <span class="excl-chip-label">(' + item.serial_number + ')</span>';
                    html += ' <button data-alias-id="' + item.id + '" title="Remove">&times;</button>';
                    html += '</span>';
                });
                aliasList.innerHTML = html;
            })
            .catch(function () {
                if (aliasList) aliasList.innerHTML = '<span style="font-size: 0.75rem; color: var(--red);">Failed to load.</span>';
            });
    }

    if (aliasAddBtn) {
        aliasAddBtn.addEventListener('click', async function () {
            var sn = aliasSnInput ? aliasSnInput.value.trim() : '';
            var name = aliasNameInput ? aliasNameInput.value.trim() : '';
            if (!sn || !name) return;
            aliasAddBtn.disabled = true;
            try {
                await fetch('/api/config/instrument-aliases', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ serial_number: sn, display_name: name })
                });
                if (aliasSnInput) aliasSnInput.value = '';
                if (aliasNameInput) aliasNameInput.value = '';
                await loadAliases();
            } finally {
                aliasAddBtn.disabled = false;
            }
        });
    }

    if (aliasList) {
        aliasList.addEventListener('click', async function (e) {
            var btn = e.target.closest('[data-alias-id]');
            if (!btn) return;
            var id = btn.dataset.aliasId;
            btn.disabled = true;
            try {
                await fetch('/api/config/instrument-aliases/' + id, { method: 'DELETE' });
                await loadAliases();
            } finally {
                btn.disabled = false;
            }
        });
    }

    // ---- Excluded Operators CRUD ----
    var exclOpList = document.getElementById('excl-op-list');
    var exclOpUsernameInput = document.getElementById('excl-op-username');
    var exclOpLabelInput = document.getElementById('excl-op-label');
    var exclOpAddBtn = document.getElementById('excl-op-add');

    function loadExcludedOperators() {
        if (!exclOpList) return;
        fetch('/api/config/excluded-operators')
            .then(function (r) { return r.json(); })
            .then(function (items) {
                if (!items.length) {
                    exclOpList.innerHTML = '<span style="font-size: 0.75rem; color: var(--slate-500);">No operators excluded.</span>';
                    return;
                }
                var html = '';
                items.forEach(function (item) {
                    html += '<span class="excl-chip">';
                    html += item.username;
                    if (item.label) html += ' <span class="excl-chip-label">(' + item.label + ')</span>';
                    html += ' <button data-excl-op-id="' + item.id + '" title="Remove">&times;</button>';
                    html += '</span>';
                });
                exclOpList.innerHTML = html;
            })
            .catch(function () {
                if (exclOpList) exclOpList.innerHTML = '<span style="font-size: 0.75rem; color: var(--red);">Failed to load.</span>';
            });
    }

    if (exclOpAddBtn) {
        exclOpAddBtn.addEventListener('click', async function () {
            var username = exclOpUsernameInput ? exclOpUsernameInput.value.trim() : '';
            if (!username) return;
            var label = exclOpLabelInput ? exclOpLabelInput.value.trim() : '';
            exclOpAddBtn.disabled = true;
            try {
                await fetch('/api/config/excluded-operators', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username: username, label: label || null })
                });
                if (exclOpUsernameInput) exclOpUsernameInput.value = '';
                if (exclOpLabelInput) exclOpLabelInput.value = '';
                await loadExcludedOperators();
            } finally {
                exclOpAddBtn.disabled = false;
            }
        });
    }

    if (exclOpList) {
        exclOpList.addEventListener('click', async function (e) {
            var btn = e.target.closest('[data-excl-op-id]');
            if (!btn) return;
            var id = btn.dataset.exclOpId;
            btn.disabled = true;
            try {
                await fetch('/api/config/excluded-operators/' + id, { method: 'DELETE' });
                await loadExcludedOperators();
            } finally {
                btn.disabled = false;
            }
        });
    }

    // ---- Operator Aliases CRUD ----
    var opAliasList = document.getElementById('op-alias-list');
    var opAliasUsernameInput = document.getElementById('op-alias-username');
    var opAliasNameInput = document.getElementById('op-alias-name');
    var opAliasAddBtn = document.getElementById('op-alias-add');

    function loadOperatorAliases() {
        if (!opAliasList) return;
        fetch('/api/config/operator-aliases')
            .then(function (r) { return r.json(); })
            .then(function (items) {
                if (!items.length) {
                    opAliasList.innerHTML = '<span style="font-size: 0.75rem; color: var(--slate-500);">No nicknames defined.</span>';
                    return;
                }
                var html = '';
                items.forEach(function (item) {
                    html += '<span class="excl-chip">';
                    html += item.display_name;
                    html += ' <span class="excl-chip-label">(' + item.username + ')</span>';
                    html += ' <button data-op-alias-id="' + item.id + '" title="Remove">&times;</button>';
                    html += '</span>';
                });
                opAliasList.innerHTML = html;
            })
            .catch(function () {
                if (opAliasList) opAliasList.innerHTML = '<span style="font-size: 0.75rem; color: var(--red);">Failed to load.</span>';
            });
    }

    if (opAliasAddBtn) {
        opAliasAddBtn.addEventListener('click', async function () {
            var username = opAliasUsernameInput ? opAliasUsernameInput.value.trim() : '';
            var name = opAliasNameInput ? opAliasNameInput.value.trim() : '';
            if (!username || !name) return;
            opAliasAddBtn.disabled = true;
            try {
                await fetch('/api/config/operator-aliases', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username: username, display_name: name })
                });
                if (opAliasUsernameInput) opAliasUsernameInput.value = '';
                if (opAliasNameInput) opAliasNameInput.value = '';
                await loadOperatorAliases();
            } finally {
                opAliasAddBtn.disabled = false;
            }
        });
    }

    if (opAliasList) {
        opAliasList.addEventListener('click', async function (e) {
            var btn = e.target.closest('[data-op-alias-id]');
            if (!btn) return;
            var id = btn.dataset.opAliasId;
            btn.disabled = true;
            try {
                await fetch('/api/config/operator-aliases/' + id, { method: 'DELETE' });
                await loadOperatorAliases();
            } finally {
                btn.disabled = false;
            }
        });
    }

    // ---- Cache Management ----
    var cacheQcBtn = document.getElementById('cache-qc-refresh');
    var cacheInstBtn = document.getElementById('cache-inst-refresh');
    var cacheOpBtn = document.getElementById('cache-op-refresh');
    var cacheMsg = document.getElementById('cache-msg');

    function showCacheMsg(text, color) {
        if (!cacheMsg) return;
        cacheMsg.textContent = text;
        cacheMsg.style.color = color;
        cacheMsg.style.background = color === 'var(--green)' ? 'rgba(34, 197, 94, 0.08)' : 'rgba(239, 68, 68, 0.08)';
        cacheMsg.style.display = 'block';
        setTimeout(function () { cacheMsg.style.display = 'none'; }, 4000);
    }

    if (cacheQcBtn) {
        cacheQcBtn.addEventListener('click', function () {
            cacheQcBtn.disabled = true;
            cacheQcBtn.textContent = 'Refreshing\u2026';
            fetch('/api/config/qc-cache/refresh', { method: 'POST' })
                .then(function () { showCacheMsg('QC cache refreshed', 'var(--green)'); })
                .catch(function () { showCacheMsg('QC cache refresh failed', 'var(--red)'); })
                .finally(function () {
                    cacheQcBtn.disabled = false;
                    cacheQcBtn.textContent = 'Refresh QC Cache';
                });
        });
    }

    if (cacheInstBtn) {
        cacheInstBtn.addEventListener('click', function () {
            cacheInstBtn.disabled = true;
            cacheInstBtn.textContent = 'Refreshing\u2026';
            fetch('/api/config/instrument-cache/refresh', { method: 'POST' })
                .then(function () { showCacheMsg('Instrument cache refreshed', 'var(--green)'); })
                .catch(function () { showCacheMsg('Instrument cache refresh failed', 'var(--red)'); })
                .finally(function () {
                    cacheInstBtn.disabled = false;
                    cacheInstBtn.textContent = 'Refresh Instrument Cache';
                });
        });
    }

    if (cacheOpBtn) {
        cacheOpBtn.addEventListener('click', function () {
            cacheOpBtn.disabled = true;
            cacheOpBtn.textContent = 'Refreshing\u2026';
            fetch('/api/config/operator-cache/refresh', { method: 'POST' })
                .then(function () { showCacheMsg('Operator cache refreshed', 'var(--green)'); })
                .catch(function () { showCacheMsg('Operator cache refresh failed', 'var(--red)'); })
                .finally(function () {
                    cacheOpBtn.disabled = false;
                    cacheOpBtn.textContent = 'Refresh Operator Cache';
                });
        });
    }

    // ---- Catalog Group Preferences ----
    var cgPrefList = document.getElementById('cg-pref-list');
    var cgPrefMsg = document.getElementById('cg-pref-msg');
    var cgPrefs = [];  // [{group, visible, sort_index}, ...]

    function renderCgPrefs() {
        if (!cgPrefList) return;
        if (!cgPrefs.length) {
            cgPrefList.innerHTML = '<div style="font-size: 0.75rem; color: var(--slate-500); padding: 0.25rem 0;">No catalog groups available</div>';
            return;
        }
        cgPrefList.innerHTML = '';
        cgPrefs.forEach(function (item, idx) {
            var row = document.createElement('div');
            row.className = 'pref-row';

            var arrows = document.createElement('div');
            arrows.className = 'pref-arrows';
            var upBtn = document.createElement('button');
            upBtn.textContent = '\u25b2';
            upBtn.title = 'Move up';
            upBtn.disabled = idx === 0;
            upBtn.addEventListener('click', function () { moveCgPref(idx, -1); });
            var downBtn = document.createElement('button');
            downBtn.textContent = '\u25bc';
            downBtn.title = 'Move down';
            downBtn.disabled = idx === cgPrefs.length - 1;
            downBtn.addEventListener('click', function () { moveCgPref(idx, 1); });
            arrows.appendChild(upBtn);
            arrows.appendChild(downBtn);

            var label = document.createElement('span');
            label.className = 'pref-row-label';
            label.textContent = item.group;

            var toggle = document.createElement('button');
            toggle.className = 'pref-toggle' + (item.visible ? ' on' : '');
            toggle.title = item.visible ? 'Visible' : 'Hidden';
            toggle.addEventListener('click', function () {
                item.visible = !item.visible;
                saveCgPrefs();
                renderCgPrefs();
            });

            row.appendChild(arrows);
            row.appendChild(label);
            row.appendChild(toggle);
            cgPrefList.appendChild(row);
        });
    }

    function moveCgPref(idx, dir) {
        var target = idx + dir;
        if (target < 0 || target >= cgPrefs.length) return;
        var tmp = cgPrefs[idx];
        cgPrefs[idx] = cgPrefs[target];
        cgPrefs[target] = tmp;
        cgPrefs.forEach(function (p, i) { p.sort_index = i; });
        saveCgPrefs();
        renderCgPrefs();
    }

    function saveCgPrefs() {
        fetch('/api/config/catalog-group-prefs', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(cgPrefs)
        }).catch(function () {});
    }

    async function loadCgPrefs() {
        if (!cgPrefList) return;
        try {
            var groups = await apiGet('/api/dashboard/qc/catalog-groups?days=0');
            var saved = [];
            try { saved = await apiGet('/api/config/catalog-group-prefs'); } catch (e) {}
            // Merge: keep saved order/visibility, append new groups at end
            var savedMap = {};
            (saved || []).forEach(function (s) { savedMap[s.group] = s; });
            var merged = [];
            // First: saved items that still exist
            (saved || []).forEach(function (s) {
                if (groups.indexOf(s.group) !== -1) {
                    merged.push({ group: s.group, visible: s.visible, sort_index: merged.length });
                }
            });
            // Then: new groups not in saved
            groups.forEach(function (g) {
                if (!savedMap[g]) {
                    merged.push({ group: g, visible: true, sort_index: merged.length });
                }
            });
            cgPrefs = merged;
            renderCgPrefs();
        } catch (e) {}
    }

    // ---- Bead Lot Preferences ----
    var lotPrefGroupSel = document.getElementById('lot-pref-group');
    var lotPrefList = document.getElementById('lot-pref-list');
    var lotPrefMsg = document.getElementById('lot-pref-msg');
    var allLotPrefs = {};  // {group: [{lot, visible, sort_index}, ...]}

    function renderLotPrefs(group) {
        if (!lotPrefList) return;
        var prefs = allLotPrefs[group] || [];
        if (!prefs.length) {
            lotPrefList.innerHTML = '<div style="font-size: 0.75rem; color: var(--slate-500); padding: 0.25rem 0;">No bead lots for this group</div>';
            return;
        }
        lotPrefList.innerHTML = '';
        prefs.forEach(function (item, idx) {
            var row = document.createElement('div');
            row.className = 'pref-row';

            var arrows = document.createElement('div');
            arrows.className = 'pref-arrows';
            var upBtn = document.createElement('button');
            upBtn.textContent = '\u25b2';
            upBtn.title = 'Move up';
            upBtn.disabled = idx === 0;
            upBtn.addEventListener('click', function () { moveLotPref(group, idx, -1); });
            var downBtn = document.createElement('button');
            downBtn.textContent = '\u25bc';
            downBtn.title = 'Move down';
            downBtn.disabled = idx === prefs.length - 1;
            downBtn.addEventListener('click', function () { moveLotPref(group, idx, 1); });
            arrows.appendChild(upBtn);
            arrows.appendChild(downBtn);

            var label = document.createElement('span');
            label.className = 'pref-row-label';
            label.textContent = item.lot;

            var toggle = document.createElement('button');
            toggle.className = 'pref-toggle' + (item.visible ? ' on' : '');
            toggle.title = item.visible ? 'Visible' : 'Hidden';
            toggle.addEventListener('click', function () {
                item.visible = !item.visible;
                saveLotPrefs();
                renderLotPrefs(group);
            });

            row.appendChild(arrows);
            row.appendChild(label);
            row.appendChild(toggle);
            lotPrefList.appendChild(row);
        });
    }

    function moveLotPref(group, idx, dir) {
        var prefs = allLotPrefs[group];
        var target = idx + dir;
        if (target < 0 || target >= prefs.length) return;
        var tmp = prefs[idx];
        prefs[idx] = prefs[target];
        prefs[target] = tmp;
        prefs.forEach(function (p, i) { p.sort_index = i; });
        saveLotPrefs();
        renderLotPrefs(group);
    }

    function saveLotPrefs() {
        fetch('/api/config/bead-lot-prefs', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(allLotPrefs)
        }).catch(function () {});
    }

    async function loadLotPrefsForGroup(group) {
        if (!lotPrefList || !group) {
            if (lotPrefList) lotPrefList.innerHTML = '';
            return;
        }
        try {
            var lots = await apiGet('/api/dashboard/qc/bead-lots?catalog_group=' + encodeURIComponent(group) + '&days=0');
            var saved = {};
            try { saved = await apiGet('/api/config/bead-lot-prefs'); } catch (e) {}
            var savedForGroup = (saved && saved[group]) || [];
            var savedMap = {};
            savedForGroup.forEach(function (s) { savedMap[s.lot] = s; });
            var merged = [];
            // Saved items that still exist
            savedForGroup.forEach(function (s) {
                if (lots.indexOf(s.lot) !== -1) {
                    merged.push({ lot: s.lot, visible: s.visible, sort_index: merged.length });
                }
            });
            // New lots
            lots.forEach(function (l) {
                if (!savedMap[l]) {
                    merged.push({ lot: l, visible: true, sort_index: merged.length });
                }
            });
            // Update the full prefs object
            if (!allLotPrefs || typeof allLotPrefs !== 'object') allLotPrefs = {};
            allLotPrefs[group] = merged;
            renderLotPrefs(group);
        } catch (e) {}
    }

    async function initLotPrefs() {
        try {
            var saved = await apiGet('/api/config/bead-lot-prefs');
            allLotPrefs = saved || {};
        } catch (e) {
            allLotPrefs = {};
        }
        // Populate group dropdown
        if (lotPrefGroupSel) {
            try {
                var groups = await apiGet('/api/dashboard/qc/catalog-groups?days=0');
                lotPrefGroupSel.innerHTML = '<option value="">Select catalog group</option>';
                groups.forEach(function (g) {
                    var opt = document.createElement('option');
                    opt.value = g;
                    opt.textContent = g;
                    lotPrefGroupSel.appendChild(opt);
                });
            } catch (e) {}
            lotPrefGroupSel.addEventListener('change', function () {
                loadLotPrefsForGroup(lotPrefGroupSel.value);
            });
        }
    }

    // ---- Settings Backup ----
    var backupExportBtn = document.getElementById('backup-export');
    var backupImportInput = document.getElementById('backup-import');
    var backupMsg = document.getElementById('backup-msg');

    function showBackupMsg(text, type) {
        if (!backupMsg) return;
        backupMsg.textContent = text;
        backupMsg.style.display = 'block';
        if (type === 'ok') {
            backupMsg.style.color = 'var(--green)';
            backupMsg.style.background = 'rgba(34, 197, 94, 0.08)';
        } else if (type === 'warn') {
            backupMsg.style.color = 'var(--amber, #f59e0b)';
            backupMsg.style.background = 'rgba(245, 158, 11, 0.08)';
        } else {
            backupMsg.style.color = 'var(--red)';
            backupMsg.style.background = 'rgba(239, 68, 68, 0.08)';
        }
    }

    if (backupExportBtn) {
        backupExportBtn.addEventListener('click', async function () {
            backupExportBtn.disabled = true;
            backupExportBtn.textContent = 'Exporting\u2026';
            try {
                var resp = await fetch('/api/config/settings/export');
                if (!resp.ok) throw new Error('Export failed: ' + resp.status);
                var data = await resp.json();
                var blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
                var url = URL.createObjectURL(blob);
                var a = document.createElement('a');
                a.href = url;
                a.download = 'beadwatch-settings.json';
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                showBackupMsg('Settings exported', 'ok');
            } catch (e) {
                showBackupMsg('Export failed: ' + e.message, 'err');
            } finally {
                backupExportBtn.disabled = false;
                backupExportBtn.textContent = 'Export Settings';
            }
        });
    }

    if (backupImportInput) {
        backupImportInput.addEventListener('change', async function () {
            var file = backupImportInput.files[0];
            if (!file) return;
            try {
                var text = await file.text();
                var json = JSON.parse(text);
                var resp = await fetch('/api/config/settings/import', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(json)
                });
                var result = await resp.json();
                if (!resp.ok) {
                    showBackupMsg('Import failed: ' + (result.detail || resp.status), 'err');
                    return;
                }
                // Build summary
                var parts = [];
                var imp = result.imported || {};
                if (imp.qc_sample_definitions) parts.push(imp.qc_sample_definitions + ' QC patterns');
                if (imp.qc_tracked_beads) parts.push(imp.qc_tracked_beads + ' tracked beads');
                if (imp.instrument_aliases) parts.push(imp.instrument_aliases + ' instrument aliases');
                if (imp.excluded_instruments) parts.push(imp.excluded_instruments + ' excluded instruments');
                if (imp.excluded_operators) parts.push(imp.excluded_operators + ' excluded operators');
                if (imp.operator_aliases) parts.push(imp.operator_aliases + ' operator aliases');
                if (imp.polling_interval_minutes) parts.push('polling interval');
                if (imp.qc_thresholds) parts.push(imp.qc_thresholds + ' thresholds');
                var msg = parts.length ? 'Imported ' + parts.join(', ') + '.' : 'Import complete (no sections changed).';

                if (result.warnings && result.warnings.length) {
                    showBackupMsg(msg + ' \u26A0 ' + result.warnings.join('; '), 'warn');
                } else {
                    showBackupMsg(msg, 'ok');
                }

                // Check if credentials are configured
                try {
                    var status = await apiGet('/api/config/status');
                    if (!status.configured) {
                        showBackupMsg(msg + ' Note: Database credentials are not configured. Go to Setup to connect.', 'warn');
                    }
                } catch (e) {}

                // Reload all settings panels
                loadConnectionInfo();
                loadPollingInterval();
                loadThresholds();
                loadQcDefinitions();
                loadQcTrackedBeads();
                loadExcluded();
                loadAliases();
                loadExcludedOperators();
                loadOperatorAliases();
            } catch (e) {
                showBackupMsg('Import failed: ' + e.message, 'err');
            } finally {
                backupImportInput.value = '';
            }
        });
    }

    // ---- Panel clamping ----
    var CLAMP_THRESHOLD = 136; // px — panels taller than this get clamped

    function classifyPanel(panel) {
        if (panel.classList.contains('is-expanded')) return; // user already expanded
        var body = panel.querySelector('.panel-body');
        if (!body) return;
        if (body.scrollHeight <= CLAMP_THRESHOLD) {
            panel.classList.add('fits-content');
        } else {
            panel.classList.remove('fits-content');
        }
    }

    function initPanelClamping() {
        var panels = document.querySelectorAll('.settings-page details.panel');

        panels.forEach(function (panel) {
            // Classify on initial load
            classifyPanel(panel);

            // Re-classify when content changes (async data loads)
            var body = panel.querySelector('.panel-body');
            if (body) {
                var observer = new MutationObserver(function () {
                    classifyPanel(panel);
                });
                observer.observe(body, { childList: true, subtree: true });
            }

            // Summary click: toggle expand instead of native details toggle
            var summary = panel.querySelector('summary');
            if (summary) {
                summary.addEventListener('click', function (e) {
                    e.preventDefault();
                    if (panel.classList.contains('fits-content')) return;
                    panel.classList.toggle('is-expanded');
                });
            }
        });
    }

    // ---- Init ----
    async function init() {
        await loadVersion();
        loadConnectionInfo();
        loadPollStatus();
        loadPollingInterval();
        loadThresholds();
        loadQcDefinitions();
        loadQcTrackedBeads();
        loadExcluded();
        loadAliases();
        loadExcludedOperators();
        loadOperatorAliases();
        loadCatalogGroups();
        loadCgPrefs();
        initLotPrefs();
        initPanelClamping();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
