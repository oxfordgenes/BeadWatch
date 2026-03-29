// BeadWatch — Setup Wizard Logic

(function () {
    'use strict';

    const form = document.getElementById('setup-form');
    const serverInput = document.getElementById('input-server');
    const databaseInput = document.getElementById('input-database');
    const usernameInput = document.getElementById('input-username');
    const passwordInput = document.getElementById('input-password');
    const pollIntervalInput = document.getElementById('input-poll-interval');
    const testBtn = document.getElementById('btn-test');
    const saveBtn = document.getElementById('btn-save');
    const messageEl = document.getElementById('message');

    function getCredentials() {
        return {
            server: serverInput.value.trim(),
            vendor_database: databaseInput.value.trim(),
            username: usernameInput.value.trim(),
            password: passwordInput.value
        };
    }

    function showMessage(text, type) {
        messageEl.textContent = text;
        messageEl.className = 'message ' + type;
        messageEl.classList.remove('hidden');
    }

    function hideMessage() {
        messageEl.classList.add('hidden');
    }

    function setLoading(button, loading) {
        if (loading) {
            button.disabled = true;
            button.dataset.originalText = button.textContent;
            button.innerHTML = '<span class="spinner"></span> Working...';
        } else {
            button.disabled = false;
            button.textContent = button.dataset.originalText || button.textContent;
        }
    }

    function validate() {
        const creds = getCredentials();
        return creds.server && creds.vendor_database && creds.username && creds.password;
    }

    async function apiPost(url, body) {
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || `Request failed (${response.status})`);
        }
        return data;
    }

    async function loadPollingInterval() {
        try {
            const response = await fetch('/api/config/polling-interval');
            if (response.ok) {
                const data = await response.json();
                pollIntervalInput.value = data.interval_minutes;
            }
        } catch (err) {
            // Silent fail: default value in HTML is fine
        }
    }

    async function loadConfigStatus() {
        try {
            const response = await fetch('/api/config/status');
            if (!response.ok) {
                return;
            }
            const status = await response.json();
            if (!status || !status.configured) {
                return;
            }

            if (status.server) {
                serverInput.value = status.server;
            }
            if (status.vendor_database) {
                databaseInput.value = status.vendor_database;
            }
            if (status.username) {
                usernameInput.value = status.username;
            }

            if (!status.normalized_initialized) {
                const createdAt = status.created_at ? ` (saved ${status.created_at})` : '';
                showMessage(
                    'Credentials are saved, but the BeadWatch database was not initialized. ' +
                    'Please test the connection and click "Save & Initialize" to finish setup.' + createdAt,
                    'error'
                );
            }
        } catch (err) {
            // Silent fail: setup still usable
        }
    }

    // Test Connection
    testBtn.addEventListener('click', async function () {
        if (!validate()) {
            showMessage('Please fill in all fields.', 'error');
            return;
        }
        hideMessage();
        setLoading(testBtn, true);

        try {
            const result = await apiPost('/api/config/test-connection', getCredentials());
            if (result.success) {
                const serverTime = result.details && result.details.server_time
                    ? ` (server time: ${result.details.server_time})`
                    : '';
                showMessage('Connection successful!' + serverTime, 'success');
                saveBtn.disabled = false;
            } else {
                showMessage(result.message, 'error');
            }
        } catch (err) {
            showMessage(err.message, 'error');
        } finally {
            setLoading(testBtn, false);
        }
    });

    // Save & Initialize
    saveBtn.addEventListener('click', async function () {
        if (!validate()) {
            showMessage('Please fill in all fields.', 'error');
            return;
        }
        hideMessage();
        setLoading(saveBtn, true);

        try {
            // Step 1: Save credentials
            showMessage('Saving credentials...', 'success');
            await apiPost('/api/config/credentials', getCredentials());

            // Step 2: Initialize database
            showMessage('Creating normalized database...', 'success');
            await apiPost('/api/config/initialize-database', {});

            // Step 3: Save polling interval
            const interval = parseInt(pollIntervalInput.value, 10);
            if (interval >= 5 && interval <= 1440) {
                await apiPost('/api/config/polling-interval', { interval_minutes: interval });
            }

            showMessage('Setup complete! Redirecting to dashboard...', 'success');
            setTimeout(function () {
                window.location.href = '/';
            }, 1500);

        } catch (err) {
            showMessage(err.message, 'error');
            setLoading(saveBtn, false);
        }
    });

    // Disable save button until connection is tested
    saveBtn.disabled = true;

    // Re-disable save if inputs change after a successful test
    [serverInput, databaseInput, usernameInput, passwordInput].forEach(function (input) {
        input.addEventListener('input', function () {
            saveBtn.disabled = true;
            hideMessage();
        });
    });

    loadConfigStatus();
    loadPollingInterval();
})();
