import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.controllers.dashboard_controller import DashboardController, _cache
from api.controllers.config_controller import ConfigController
from config.settings import __version__


@pytest.fixture(autouse=True)
def _clear_dashboard_cache():
    """Ensure the module-level TTL cache is empty between tests."""
    _cache.clear()
    yield
    _cache.clear()


@pytest.fixture
def mock_sqlite_db():
    db = MagicMock()
    db.creds.is_initialized.return_value = True
    db.app_state.get_state.return_value = None
    return db


@pytest.fixture
def mock_normalized_db():
    db = MagicMock()
    db.execute_query.return_value = []
    db.get_status.return_value = {'status': 'connected', 'last_successful_query': 'SELECT 1', 'error_message': None}
    return db


@pytest.fixture
def app(mock_sqlite_db, mock_normalized_db):
    """Create a FastAPI app with mocked state for testing controllers"""
    app = FastAPI()

    app.state.sqlite_db = mock_sqlite_db
    app.state.normalized_db = mock_normalized_db
    app.state.vendor_db = MagicMock()
    app.state.polling_service = MagicMock()
    app.state.scheduler = None

    dashboard = DashboardController()
    app.include_router(dashboard.router)

    config = ConfigController()
    app.include_router(config.router)

    # Root and health routes for completeness
    @app.get("/health")
    async def health():
        return {"status": "ok", "version": __version__}

    return app


@pytest.fixture
def client(app):
    return TestClient(app)


class TestDashboardController:
    def test_get_recent_metrics_empty(self, client, mock_normalized_db):
        mock_normalized_db.execute_query.return_value = []
        resp = client.get("/api/dashboard/metrics/recent")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_recent_metrics_with_data(self, client, mock_normalized_db):
        mock_normalized_db.execute_query.return_value = [
            {'timestamp': datetime(2024, 1, 1, 12, 0), 'metric_name': 'mean_mfi', 'value': 1500.0}
        ]
        resp = client.get("/api/dashboard/metrics/recent?metric_name=mean_mfi")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]['metric_name'] == 'mean_mfi'
        assert data[0]['value'] == 1500.0

    def test_get_recent_metrics_custom_hours(self, client, mock_normalized_db):
        client.get("/api/dashboard/metrics/recent?hours=48")
        call_args = mock_normalized_db.execute_query.call_args
        assert 48 in call_args[0][1]

    def test_get_rolling_stats_not_found(self, client, mock_normalized_db):
        mock_normalized_db.execute_query.return_value = []
        resp = client.get("/api/dashboard/metrics/rolling-stats?metric_name=mean_mfi&window_days=30")
        assert resp.status_code == 404

    def test_get_rolling_stats_found(self, client, mock_normalized_db):
        mock_normalized_db.execute_query.return_value = [{
            'metric_name': 'mean_mfi', 'window_days': 30, 'mean_value': 1500.0,
            'std_dev': 100.0, 'min_value': 800.0, 'max_value': 2200.0,
            'record_count': 50, 'updated_at': datetime(2024, 1, 1, 12, 0)
        }]
        resp = client.get("/api/dashboard/metrics/rolling-stats?metric_name=mean_mfi&window_days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert data['mean'] == 1500.0
        assert data['count'] == 50

    def test_get_active_alerts_empty(self, client, mock_normalized_db):
        mock_normalized_db.execute_query.return_value = []
        resp = client.get("/api/dashboard/alerts/active")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_active_alerts_with_data(self, client, mock_normalized_db):
        mock_normalized_db.execute_query.return_value = [{
            'id': 1, 'timestamp': datetime(2024, 1, 1), 'metric_name': 'mean_mfi',
            'threshold_type': 'upper', 'threshold_value': 3000.0, 'actual_value': 3500.0,
            'severity': 'critical', 'created_at': datetime(2024, 1, 1),
            'display_name': 'Sample-001'
        }]
        resp = client.get("/api/dashboard/alerts/active")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]['severity'] == 'critical'
        assert data[0]['display_name'] == 'Sample-001'

    def test_acknowledge_alert(self, client, mock_normalized_db):
        mock_normalized_db.execute_non_query.return_value = 1
        resp = client.post("/api/dashboard/alerts/42/acknowledge")
        assert resp.status_code == 200
        data = resp.json()
        assert data['status'] == 'ok'
        assert data['alert_id'] == 42

    def test_acknowledge_alert_not_found(self, client, mock_normalized_db):
        mock_normalized_db.execute_non_query.return_value = 0
        resp = client.post("/api/dashboard/alerts/999/acknowledge")
        assert resp.status_code == 404

    def test_acknowledge_all_alerts(self, client, mock_normalized_db):
        mock_normalized_db.execute_non_query.return_value = 5
        resp = client.post("/api/dashboard/alerts/acknowledge-all")
        assert resp.status_code == 200
        data = resp.json()
        assert data['status'] == 'ok'
        assert data['count'] == 5

    def test_get_system_status(self, client, mock_sqlite_db, mock_normalized_db):
        mock_normalized_db.execute_query.return_value = [{'count': 42}]
        resp = client.get("/api/dashboard/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data['status'] == 'healthy'
        assert data['total_records_processed'] == 42
        assert data['version'] == __version__

    def test_get_system_status_degraded_when_disconnected(self, client, mock_normalized_db):
        mock_normalized_db.get_status.return_value = {
            'status': 'disconnected', 'last_successful_query': None, 'error_message': 'timeout'
        }
        mock_normalized_db.execute_query.return_value = [{'count': 0}]
        resp = client.get("/api/dashboard/status")
        data = resp.json()
        assert data['status'] == 'degraded'

    def test_get_system_status_degraded_when_last_poll_stale(self, client, mock_sqlite_db, mock_normalized_db):
        """Status should be degraded when last_poll is older than 2x the polling interval"""
        stale_time = (datetime.now() - timedelta(days=2)).isoformat()
        mock_sqlite_db.app_state.get_state.side_effect = lambda key: {
            'last_poll_timestamp': stale_time,
            'scheduler_last_heartbeat': None,
            'poll_interval_minutes': '60',
        }.get(key)
        mock_normalized_db.execute_query.return_value = [{'count': 10}]
        resp = client.get("/api/dashboard/status")
        data = resp.json()
        assert data['status'] == 'degraded'

    def test_get_system_status_degraded_when_heartbeat_stale(self, client, mock_sqlite_db, mock_normalized_db):
        """Status should be degraded when scheduler heartbeat is older than 2x the polling interval"""
        stale_time = (datetime.now() - timedelta(hours=5)).isoformat()
        mock_sqlite_db.app_state.get_state.side_effect = lambda key: {
            'last_poll_timestamp': stale_time,
            'scheduler_last_heartbeat': stale_time,
            'poll_interval_minutes': '60',
        }.get(key)
        mock_normalized_db.execute_query.return_value = [{'count': 10}]
        resp = client.get("/api/dashboard/status")
        data = resp.json()
        assert data['status'] == 'degraded'

    def test_get_system_status_healthy_when_recent_heartbeat(self, client, mock_sqlite_db, mock_normalized_db):
        """Status should be healthy when heartbeat and last_poll are recent"""
        recent_time = (datetime.now() - timedelta(minutes=30)).isoformat()
        mock_sqlite_db.app_state.get_state.side_effect = lambda key: {
            'last_poll_timestamp': recent_time,
            'scheduler_last_heartbeat': recent_time,
            'poll_interval_minutes': '60',
        }.get(key)
        mock_normalized_db.execute_query.return_value = [{'count': 10}]
        resp = client.get("/api/dashboard/status")
        data = resp.json()
        assert data['status'] == 'healthy'

    def test_get_system_status_healthy_when_heartbeat_fresh_but_poll_cursor_stale(self, client, mock_sqlite_db, mock_normalized_db):
        """Scheduler running fine but no new records — last_poll cursor is old, heartbeat is fresh"""
        stale_cursor = (datetime.now() - timedelta(days=2)).isoformat()
        fresh_heartbeat = (datetime.now() - timedelta(minutes=5)).isoformat()
        mock_sqlite_db.app_state.get_state.side_effect = lambda key: {
            'last_poll_timestamp': stale_cursor,
            'scheduler_last_heartbeat': fresh_heartbeat,
            'poll_interval_minutes': '60',
        }.get(key)
        mock_normalized_db.execute_query.return_value = [{'count': 10}]
        resp = client.get("/api/dashboard/status")
        data = resp.json()
        assert data['status'] == 'healthy'
        # last_poll in the response should use the heartbeat, not the stale cursor
        assert data['last_poll'] is not None

    def test_get_available_metrics(self, client, mock_normalized_db):
        mock_normalized_db.execute_query.return_value = [
            {'metric_name': 'cv_percentage'},
            {'metric_name': 'mean_mfi'},
        ]
        resp = client.get("/api/dashboard/metrics/available")
        assert resp.status_code == 200
        assert resp.json() == ['cv_percentage', 'mean_mfi']

    def test_503_when_normalized_db_not_configured(self, app, mock_sqlite_db):
        """Dashboard endpoints should return 503 when normalized_db is None"""
        app.state.normalized_db = None
        client = TestClient(app)
        resp = client.get("/api/dashboard/metrics/recent")
        assert resp.status_code == 503


class TestLotLikePattern:
    """Tests for _lot_like_pattern SQL Server LIKE helper."""

    def test_standard_pattern(self):
        pattern = DashboardController._lot_like_pattern("LS1A04", "008")
        assert pattern == "LS1A04%[_]008[_]%"

    def test_different_group_and_lot(self):
        pattern = DashboardController._lot_like_pattern("LS2A01", "012")
        assert pattern == "LS2A01%[_]012[_]%"

    def test_pattern_escapes_underscores(self):
        """Bracket syntax ensures underscores are literal, not wildcards."""
        pattern = DashboardController._lot_like_pattern("LSM12", "005")
        assert "[_]" in pattern
        # Wildcard after group prefix handles extra chars (e.g. LSM12ABC_005_...)
        assert pattern.startswith("LSM12%")

    def test_matches_catalog_with_extra_chars(self):
        """Pattern must match CatalogIDs like LS1A04NC12_008_02."""
        import re
        pattern = DashboardController._lot_like_pattern("LS1A04", "008")
        # Convert SQL LIKE to regex for validation
        regex = pattern.replace("%", ".*").replace("[_]", "_")
        assert re.match(regex, "LS1A04NC12_008_02")
        assert re.match(regex, "LS1A04_008_FOO.1")
        assert not re.match(regex, "LS2A01_008_FOO.1")


class TestDistributionEndpoint:
    """Verify distribution uses lot-specific LIKE and ROW_NUMBER."""

    def test_distribution_uses_lot_pattern_and_row_number(self, app, client):
        vendor_db = app.state.vendor_db
        vendor_db.execute_query.return_value = [
            {'BuildDT': datetime(2024, 1, 1), 'CatalogID': 'LS1A04_008_FOO.1', 'mfi': 1500.0, 'bead_id': 3},
            {'BuildDT': datetime(2024, 1, 1), 'CatalogID': 'LS1A04_008_FOO.1', 'mfi': 1600.0, 'bead_id': 4},
        ]
        resp = client.get("/api/dashboard/qc/distribution?catalog_group=LS1A04&bead_lot=008&days=30")
        assert resp.status_code == 200

        # Verify the SQL passed to execute_query
        call_args = vendor_db.execute_query.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        # Should use lot-specific LIKE pattern (not broad group%)
        assert "LS1A04%[_]008[_]%" in params
        # Should use ROW_NUMBER for server-side downsampling
        assert "ROW_NUMBER()" in sql
        assert "rn <=" in sql

    def test_distribution_returns_points_with_bead_id(self, app, client):
        vendor_db = app.state.vendor_db
        vendor_db.execute_query.return_value = [
            {'BuildDT': datetime(2024, 1, 15), 'CatalogID': 'LS1A04_008_XYZ.1', 'mfi': 2000.0, 'bead_id': 1},
            {'BuildDT': datetime(2024, 1, 15), 'CatalogID': 'LS1A04_008_XYZ.1', 'mfi': 5000.0, 'bead_id': 5},
        ]
        resp = client.get("/api/dashboard/qc/distribution?catalog_group=LS1A04&bead_lot=008&days=30")
        data = resp.json()
        assert len(data) == 2
        assert data[0]['mfi'] == 2000.0
        assert data[0]['bead_id'] == 1
        assert data[0]['date'] == '2024-01-15'
        assert data[1]['bead_id'] == 5


class TestControlsTrendEndpoint:
    """Verify controls trend uses lot-specific LIKE pattern."""

    def test_controls_uses_lot_pattern(self, app, client):
        vendor_db = app.state.vendor_db
        vendor_db.execute_query.return_value = [
            {
                'BuildDT': datetime(2024, 1, 1),
                'CatalogID': 'LS1A04_008_FOO.1',
                'NC1': 100.0, 'NC2': None, 'PC1': 5000.0, 'PC2': None,
            },
        ]
        resp = client.get("/api/dashboard/qc/controls?catalog_group=LS1A04&bead_lot=008&days=30")
        assert resp.status_code == 200

        call_args = vendor_db.execute_query.call_args
        params = call_args[0][1]
        # Should use the lot-specific LIKE pattern
        assert "LS1A04%[_]008[_]%" in params

    def test_controls_does_not_use_row_number(self, app, client):
        """Controls needs all rows for daily averaging — no ROW_NUMBER."""
        vendor_db = app.state.vendor_db
        vendor_db.execute_query.return_value = []
        resp = client.get("/api/dashboard/qc/controls?catalog_group=LS1A04&bead_lot=008&days=30")
        assert resp.status_code == 200

        call_args = vendor_db.execute_query.call_args
        sql = call_args[0][0]
        assert "ROW_NUMBER" not in sql

    def test_controls_returns_per_run_stats(self, app, client):
        vendor_db = app.state.vendor_db
        vendor_db.execute_query.return_value = [
            {
                'BuildDT': datetime(2024, 1, 1, 10, 0),
                'CatalogID': 'LS1A04_008_FOO.1',
                'TrayID': 1, 'TrayIDName': 'Run-A',
                'NC1': 100.0, 'NC2': None, 'PC1': 5000.0, 'PC2': None,
            },
            {
                'BuildDT': datetime(2024, 1, 1, 10, 5),
                'CatalogID': 'LS1A04_008_FOO.1',
                'TrayID': 1, 'TrayIDName': 'Run-A',
                'NC1': 200.0, 'NC2': None, 'PC1': 6000.0, 'PC2': None,
            },
            {
                'BuildDT': datetime(2024, 1, 1, 14, 0),
                'CatalogID': 'LS1A04_008_FOO.1',
                'TrayID': 2, 'TrayIDName': 'Run-B',
                'NC1': 150.0, 'NC2': None, 'PC1': 5500.0, 'PC2': None,
            },
        ]
        resp = client.get("/api/dashboard/qc/controls?catalog_group=LS1A04&bead_lot=008&days=30")
        data = resp.json()
        assert len(data) == 2
        # Run-A: 2 wells grouped together
        assert data[0]['run_name'] == 'Run-A'
        assert data[0]['pc_median'] == 5500.0   # median(5000, 6000)
        assert data[0]['pc_min'] == 5000.0
        assert data[0]['pc_max'] == 6000.0
        assert data[0]['nc_median'] == 150.0    # median(100, 200)
        assert data[0]['nc_min'] == 100.0
        assert data[0]['nc_max'] == 200.0
        # Run-B: single well
        assert data[1]['run_name'] == 'Run-B'
        assert data[1]['pc_median'] == 5500.0
        assert data[1]['nc_median'] == 150.0


class TestBeadCountsEndpoint:
    """Verify bead counts trend endpoint."""

    def test_bead_counts_uses_lot_pattern(self, app, client):
        vendor_db = app.state.vendor_db
        vendor_db.execute_query.return_value = []
        resp = client.get("/api/dashboard/qc/bead-counts?catalog_group=LS1A04&bead_lot=008&days=30")
        assert resp.status_code == 200

        call_args = vendor_db.execute_query.call_args
        params = call_args[0][1]
        assert "LS1A04%[_]008[_]%" in params

    def test_bead_counts_returns_per_run_stats(self, app, client):
        vendor_db = app.state.vendor_db
        vendor_db.execute_query.return_value = [
            {
                'BuildDT': datetime(2024, 1, 1, 10, 0),
                'CatalogID': 'LS1A04_008_FOO.1',
                'TrayID': 1, 'TrayIDName': 'Run-A',
                'bead_count': 85.0,
            },
            {
                'BuildDT': datetime(2024, 1, 1, 10, 5),
                'CatalogID': 'LS1A04_008_FOO.1',
                'TrayID': 1, 'TrayIDName': 'Run-A',
                'bead_count': 30.0,
            },
            {
                'BuildDT': datetime(2024, 1, 1, 10, 10),
                'CatalogID': 'LS1A04_008_FOO.1',
                'TrayID': 1, 'TrayIDName': 'Run-A',
                'bead_count': 110.0,
            },
            {
                'BuildDT': datetime(2024, 1, 1, 10, 15),
                'CatalogID': 'LS1A04_008_FOO.1',
                'TrayID': 1, 'TrayIDName': 'Run-A',
                'bead_count': 10.0,
            },
        ]
        resp = client.get("/api/dashboard/qc/bead-counts?catalog_group=LS1A04&bead_lot=008&days=30")
        data = resp.json()
        assert len(data) == 1
        run = data[0]
        assert run['run_name'] == 'Run-A'
        assert run['median_count'] == 57.5  # median(10, 30, 85, 110)
        assert run['min_count'] == 10.0
        assert run['max_count'] == 110.0
        assert run['total_readings'] == 4
        assert run['low_count_pct'] == 25.0  # 1 out of 4 < 25

    def test_bead_counts_excludes_null_counts(self, app, client):
        vendor_db = app.state.vendor_db
        vendor_db.execute_query.return_value = [
            {
                'BuildDT': datetime(2024, 1, 1, 10, 0),
                'CatalogID': 'LS1A04_008_FOO.1',
                'TrayID': 1, 'TrayIDName': 'Run-A',
                'bead_count': None,
            },
            {
                'BuildDT': datetime(2024, 1, 1, 10, 5),
                'CatalogID': 'LS1A04_008_FOO.1',
                'TrayID': 1, 'TrayIDName': 'Run-A',
                'bead_count': 50.0,
            },
        ]
        resp = client.get("/api/dashboard/qc/bead-counts?catalog_group=LS1A04&bead_lot=008&days=30")
        data = resp.json()
        assert len(data) == 1
        # Only the non-null bead_count should be included
        assert data[0]['total_readings'] == 1
        assert data[0]['median_count'] == 50.0


class TestSnRatioEndpoint:
    """Verify S/N ratio trend endpoint."""

    def test_sn_ratio_uses_lot_pattern(self, app, client):
        vendor_db = app.state.vendor_db
        vendor_db.execute_query.return_value = []
        resp = client.get("/api/dashboard/qc/sn-ratio?catalog_group=LS1A04&bead_lot=008&days=30")
        assert resp.status_code == 200

        call_args = vendor_db.execute_query.call_args
        params = call_args[0][1]
        assert "LS1A04%[_]008[_]%" in params

    def test_sn_ratio_returns_per_run_stats(self, app, client):
        vendor_db = app.state.vendor_db
        vendor_db.execute_query.return_value = [
            {
                'BuildDT': datetime(2024, 1, 1, 10, 0),
                'CatalogID': 'LS1A04_008_FOO.1',
                'TrayID': 1, 'TrayIDName': 'Run-A',
                'NC1': 100.0, 'NC2': None, 'PC1': 5000.0, 'PC2': None,
            },
            {
                'BuildDT': datetime(2024, 1, 1, 10, 5),
                'CatalogID': 'LS1A04_008_FOO.1',
                'TrayID': 1, 'TrayIDName': 'Run-A',
                'NC1': 200.0, 'NC2': None, 'PC1': 6000.0, 'PC2': None,
            },
        ]
        resp = client.get("/api/dashboard/qc/sn-ratio?catalog_group=LS1A04&bead_lot=008&days=30")
        data = resp.json()
        assert len(data) == 1
        run = data[0]
        assert run['run_name'] == 'Run-A'
        # S/N per well: 5000/100=50, 6000/200=30 → median=40
        assert run['sn_median'] == 40.0
        assert run['sn_min'] == 30.0
        assert run['sn_max'] == 50.0
        assert run['well_count'] == 2

    def test_sn_ratio_excludes_zero_nc(self, app, client):
        vendor_db = app.state.vendor_db
        vendor_db.execute_query.return_value = [
            {
                'BuildDT': datetime(2024, 1, 1, 10, 0),
                'CatalogID': 'LS1A04_008_FOO.1',
                'TrayID': 1, 'TrayIDName': 'Run-A',
                'NC1': 0.0, 'NC2': None, 'PC1': 5000.0, 'PC2': None,
            },
            {
                'BuildDT': datetime(2024, 1, 1, 10, 5),
                'CatalogID': 'LS1A04_008_FOO.1',
                'TrayID': 1, 'TrayIDName': 'Run-A',
                'NC1': 100.0, 'NC2': None, 'PC1': 4000.0, 'PC2': None,
            },
        ]
        resp = client.get("/api/dashboard/qc/sn-ratio?catalog_group=LS1A04&bead_lot=008&days=30")
        data = resp.json()
        assert len(data) == 1
        # NC=0 well is excluded; only NC=100 well remains: 4000/100=40
        assert data[0]['well_count'] == 1
        assert data[0]['sn_min'] == 40.0
        assert data[0]['sn_max'] == 40.0

    def test_sn_ratio_excludes_null_controls(self, app, client):
        vendor_db = app.state.vendor_db
        vendor_db.execute_query.return_value = [
            {
                'BuildDT': datetime(2024, 1, 1, 10, 0),
                'CatalogID': 'LS1A04_008_FOO.1',
                'TrayID': 1, 'TrayIDName': 'Run-A',
                'NC1': None, 'NC2': None, 'PC1': 5000.0, 'PC2': None,
            },
        ]
        resp = client.get("/api/dashboard/qc/sn-ratio?catalog_group=LS1A04&bead_lot=008&days=30")
        data = resp.json()
        # Run should be empty since NC is null
        assert len(data) == 0


class TestInstrumentsEndpoint:
    """Verify instruments discovery reads from InstrumentRunCache."""

    def test_get_instruments_returns_distinct_from_cache(self, app, client, mock_normalized_db):
        # First call: COUNT (cache populated), second call: DISTINCT instruments
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 3}],
            [{'instrument': 'ABC123'}, {'instrument': 'DEF456'}],
        ]
        resp = client.get("/api/dashboard/qc/instruments?days=90")
        assert resp.status_code == 200
        data = resp.json()
        assert data == ['ABC123', 'DEF456']

    def test_get_instruments_returns_sorted(self, app, client, mock_normalized_db):
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 3}],
            [{'instrument': 'ZZZ999'}, {'instrument': 'AAA111'}, {'instrument': 'MMM555'}],
        ]
        resp = client.get("/api/dashboard/qc/instruments?days=90")
        data = resp.json()
        assert data == ['AAA111', 'MMM555', 'ZZZ999']

    def test_lazy_populates_on_empty_cache(self, app, client, mock_normalized_db):
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 0}],
            [{'instrument': 'INST-A'}],
        ]
        resp = client.get("/api/dashboard/qc/instruments?days=90")
        assert resp.status_code == 200
        app.state.polling_service.populate_instrument_cache.assert_called_once()


class TestInstrumentSnRatioEndpoint:
    """Verify instrument S/N ratio reads from InstrumentRunCache."""

    def test_instrument_sn_ratio_returns_cache_rows(self, app, client, mock_normalized_db):
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 2}],
            [
                {
                    'instrument': 'INST-A', 'tray_id': 1,
                    'analysis_dt': datetime(2024, 1, 1, 10, 0),
                    'run_name': 'Run-1', 'sn_median': 50.0, 'sn_min': 45.0, 'sn_max': 55.0,
                },
                {
                    'instrument': 'INST-B', 'tray_id': 2,
                    'analysis_dt': datetime(2024, 1, 2, 10, 0),
                    'run_name': 'Run-2', 'sn_median': 30.0, 'sn_min': 28.0, 'sn_max': 32.0,
                },
            ],
        ]
        resp = client.get("/api/dashboard/qc/instrument-sn-ratio?days=90")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        instruments = [d['instrument'] for d in data]
        assert 'INST-A' in instruments
        assert 'INST-B' in instruments
        inst_a = [d for d in data if d['instrument'] == 'INST-A'][0]
        assert inst_a['sn_median'] == 50.0
        inst_b = [d for d in data if d['instrument'] == 'INST-B'][0]
        assert inst_b['sn_median'] == 30.0

    def test_lazy_populates_on_empty_cache(self, app, client, mock_normalized_db):
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 0}],
            [],
        ]
        resp = client.get("/api/dashboard/qc/instrument-sn-ratio?days=90")
        assert resp.status_code == 200
        app.state.polling_service.populate_instrument_cache.assert_called_once()


class TestInstrumentNcEndpoint:
    """Verify instrument NC (background) reads from InstrumentRunCache."""

    def test_instrument_nc_returns_cache_rows(self, app, client, mock_normalized_db):
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 2}],
            [
                {
                    'instrument': 'INST-A', 'tray_id': 1,
                    'analysis_dt': datetime(2024, 1, 1, 10, 0),
                    'run_name': 'Run-1', 'nc_median': 150.0, 'nc_min': 100.0, 'nc_max': 200.0,
                },
                {
                    'instrument': 'INST-B', 'tray_id': 2,
                    'analysis_dt': datetime(2024, 1, 2, 10, 0),
                    'run_name': 'Run-2', 'nc_median': 300.0, 'nc_min': 280.0, 'nc_max': 320.0,
                },
            ],
        ]
        resp = client.get("/api/dashboard/qc/instrument-nc?days=90")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        inst_a = [d for d in data if d['instrument'] == 'INST-A'][0]
        assert inst_a['nc_median'] == 150.0
        inst_b = [d for d in data if d['instrument'] == 'INST-B'][0]
        assert inst_b['nc_median'] == 300.0

    def test_lazy_populates_on_empty_cache(self, app, client, mock_normalized_db):
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 0}],
            [],
        ]
        resp = client.get("/api/dashboard/qc/instrument-nc?days=90")
        assert resp.status_code == 200
        app.state.polling_service.populate_instrument_cache.assert_called_once()


class TestInstrumentBeadCountsEndpoint:
    """Verify instrument bead counts reads from InstrumentRunCache."""

    def test_instrument_bead_counts_returns_cache_rows(self, app, client, mock_normalized_db):
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 2}],
            [
                {
                    'instrument': 'INST-A', 'tray_id': 1,
                    'analysis_dt': datetime(2024, 1, 1, 10, 0),
                    'run_name': 'Run-1',
                    'count_median': 90.0, 'count_min': 80.0, 'count_max': 100.0,
                },
                {
                    'instrument': 'INST-B', 'tray_id': 2,
                    'analysis_dt': datetime(2024, 1, 2, 10, 0),
                    'run_name': 'Run-2',
                    'count_median': 50.0, 'count_min': 45.0, 'count_max': 55.0,
                },
            ],
        ]
        resp = client.get("/api/dashboard/qc/instrument-bead-counts?days=90")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        inst_a = [d for d in data if d['instrument'] == 'INST-A'][0]
        assert inst_a['median_count'] == 90.0
        assert inst_a['min_count'] == 80.0
        assert inst_a['max_count'] == 100.0
        inst_b = [d for d in data if d['instrument'] == 'INST-B'][0]
        assert inst_b['median_count'] == 50.0

    def test_lazy_populates_on_empty_cache(self, app, client, mock_normalized_db):
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 0}],
            [],
        ]
        resp = client.get("/api/dashboard/qc/instrument-bead-counts?days=90")
        assert resp.status_code == 200
        app.state.polling_service.populate_instrument_cache.assert_called_once()


class TestQCSampleTrendEndpoint:
    """Tests for GET /api/dashboard/qc/sample-trend (reads from QCSampleCache)."""

    def _cache_row(self, **overrides):
        """Build a mock QCSampleCache row dict."""
        row = {
            'id': 1,
            'catalog_group': 'LS1A04',
            'bead_lot': '008',
            'tray_id': 1,
            'sample_id_name': 'MP1-Test',
            'patient_id': None,
            'analysis_dt': datetime(2024, 1, 1, 10, 0),
            'run_name': 'Run-A',
            'display_name': 'MP1-Test',
            'role': 'positive',
            'instrument': 'INST1',
            'pc': 5000.0,
            'nc': 100.0,
            'median_mfi': 2500.0,
            'median_count': 85.0,
            'sn_ratio': 50.0,
            'bead_mfi_json': '{"1": 2000.0, "2": 3000.0}',
            '_dedup': 'LS1A04|008|1|MP1-Test|',
        }
        row.update(overrides)
        return row

    def test_returns_empty_when_no_definitions(self, app, client, mock_sqlite_db):
        mock_sqlite_db.qc.get_qc_sample_definitions.return_value = []
        resp = client.get("/api/dashboard/qc/sample-trend?catalog_group=LS1A04&bead_lot=008&days=30")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_cached_samples_with_metrics(self, app, client, mock_sqlite_db, mock_normalized_db):
        mock_sqlite_db.qc.get_qc_sample_definitions.return_value = [
            {"pattern": "MP1", "match_type": "substring", "role": "positive"},
        ]
        mock_sqlite_db.qc.get_qc_tracked_beads.return_value = []

        # First call: COUNT returns non-zero (cache populated)
        # Second call: returns cached rows
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 1}],
            [self._cache_row()],
        ]

        resp = client.get("/api/dashboard/qc/sample-trend?catalog_group=LS1A04&bead_lot=008&days=30")
        data = resp.json()
        assert len(data) == 1
        entry = data[0]
        assert entry['sample_name'] == 'MP1-Test'
        assert entry['role'] == 'positive'
        assert entry['median_mfi'] == 2500.0
        assert entry['pc'] == 5000.0
        assert entry['nc'] == 100.0
        assert entry['sn_ratio'] == 50.0
        assert entry['median_count'] == 85.0

    def test_lazy_populates_on_cache_miss(self, app, client, mock_sqlite_db, mock_normalized_db):
        mock_sqlite_db.qc.get_qc_sample_definitions.return_value = [
            {"pattern": "MP1", "match_type": "substring", "role": "positive"},
        ]
        mock_sqlite_db.qc.get_qc_tracked_beads.return_value = []

        # First call: COUNT returns 0 (cache empty), triggers populate
        # Second call: returns cached rows after populate
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 0}],
            [self._cache_row()],
        ]

        resp = client.get("/api/dashboard/qc/sample-trend?catalog_group=LS1A04&bead_lot=008&days=30")
        data = resp.json()
        assert len(data) == 1
        # Verify populate was called
        app.state.polling_service.populate_qc_cache.assert_called_once_with('LS1A04', '008')

    def test_display_name_uses_patient_id(self, app, client, mock_sqlite_db, mock_normalized_db):
        """When display_name comes from PatientID, it should be returned as sample_name."""
        mock_sqlite_db.qc.get_qc_sample_definitions.return_value = [
            {"pattern": "MP1", "match_type": "substring", "role": "positive"},
        ]
        mock_sqlite_db.qc.get_qc_tracked_beads.return_value = []

        mock_normalized_db.execute_query.side_effect = [
            [{'n': 1}],
            [self._cache_row(display_name='MP1', patient_id='MP1', sample_id_name='POS CONTROL E')],
        ]

        resp = client.get("/api/dashboard/qc/sample-trend?catalog_group=LS1A04&bead_lot=008&days=30")
        data = resp.json()
        assert len(data) == 1
        assert data[0]['sample_name'] == 'MP1'
        assert data[0]['role'] == 'positive'

    def test_returns_empty_when_cache_empty_and_no_vendor_data(self, app, client, mock_sqlite_db, mock_normalized_db):
        mock_sqlite_db.qc.get_qc_sample_definitions.return_value = [
            {"pattern": "MP1", "match_type": "exact", "role": "positive"},
        ]
        mock_sqlite_db.qc.get_qc_tracked_beads.return_value = []

        # COUNT=0, populate runs but adds nothing, then SELECT returns empty
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 0}],
            [],
        ]

        resp = client.get("/api/dashboard/qc/sample-trend?catalog_group=LS1A04&bead_lot=008&days=30")
        data = resp.json()
        assert len(data) == 0

    def test_includes_tracked_bead_mfi_from_json(self, app, client, mock_sqlite_db, mock_normalized_db):
        mock_sqlite_db.qc.get_qc_sample_definitions.return_value = [
            {"pattern": "MP1", "match_type": "substring", "role": "positive"},
        ]
        mock_sqlite_db.qc.get_qc_tracked_beads.return_value = [
            {"bead_id": "3"},
        ]

        mock_normalized_db.execute_query.side_effect = [
            [{'n': 1}],
            [self._cache_row(bead_mfi_json='{"3": 2500.0, "5": 1000.0}')],
        ]

        resp = client.get("/api/dashboard/qc/sample-trend?catalog_group=LS1A04&bead_lot=008&days=30")
        data = resp.json()
        assert len(data) == 1
        entry = data[0]
        assert entry['tracked_bead_mfi'] is not None
        assert entry['tracked_bead_mfi']['3'] == 2500.0
        # Bead 5 not tracked — should not be in tracked_bead_mfi
        assert '5' not in entry['tracked_bead_mfi']

    def test_reads_from_normalized_db_not_vendor(self, app, client, mock_sqlite_db, mock_normalized_db):
        """Verify the endpoint reads from normalized_db (cache), not vendor_db."""
        mock_sqlite_db.qc.get_qc_sample_definitions.return_value = [
            {"pattern": "MP1", "match_type": "substring", "role": "positive"},
        ]
        mock_sqlite_db.qc.get_qc_tracked_beads.return_value = []

        mock_normalized_db.execute_query.side_effect = [
            [{'n': 1}],
            [],
        ]

        resp = client.get("/api/dashboard/qc/sample-trend?catalog_group=LS1A04&bead_lot=008&days=30")
        assert resp.status_code == 200

        # normalized_db should have been called (COUNT + SELECT)
        assert mock_normalized_db.execute_query.call_count == 2
        # vendor_db should NOT have been called for sample-trend
        vendor_db = app.state.vendor_db
        vendor_db.execute_query.assert_not_called()


class TestQCCacheRefreshEndpoint:
    """Tests for POST /api/config/qc-cache/refresh."""

    def test_refresh_succeeds(self, app, client):
        resp = client.post("/api/config/qc-cache/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        app.state.polling_service.rebuild_qc_cache.assert_called_once()

    def test_refresh_returns_503_when_no_polling_service(self, app, client):
        app.state.polling_service = None
        resp = client.post("/api/config/qc-cache/refresh")
        assert resp.status_code == 503


class TestInstrumentCacheRefreshEndpoint:
    """Tests for POST /api/config/instrument-cache/refresh."""

    def test_refresh_succeeds(self, app, client):
        resp = client.post("/api/config/instrument-cache/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        app.state.polling_service.rebuild_instrument_cache.assert_called_once()

    def test_refresh_returns_503_when_no_polling_service(self, app, client):
        app.state.polling_service = None
        resp = client.post("/api/config/instrument-cache/refresh")
        assert resp.status_code == 503


class TestDefinitionChangesInvalidateCache:
    """Verify that add/delete QC definitions trigger cache invalidation."""

    def test_add_definition_clears_cache(self, app, client, mock_sqlite_db):
        mock_sqlite_db.qc.add_qc_sample_definition.return_value = 1
        resp = client.post("/api/config/qc-sample-definitions", json={
            "pattern": "MP1", "match_type": "substring", "role": "positive"
        })
        assert resp.status_code == 200
        app.state.polling_service.rebuild_qc_cache.assert_called_once()

    def test_delete_definition_clears_cache(self, app, client, mock_sqlite_db):
        mock_sqlite_db.qc.delete_qc_sample_definition.return_value = True
        resp = client.delete("/api/config/qc-sample-definitions/1")
        assert resp.status_code == 200
        app.state.polling_service.rebuild_qc_cache.assert_called_once()


class TestQCSampleNamesEndpoint:
    """Tests for GET /api/dashboard/qc/sample-names discovery endpoint."""

    def test_returns_distinct_names(self, app, client):
        vendor_db = app.state.vendor_db
        vendor_db.execute_query.return_value = [
            {'SampleIDName': 'POS CONTROL E', 'PatientID': 'MP1'},
            {'SampleIDName': 'POS CONTROL +AO', 'PatientID': 'MP2'},
            {'SampleIDName': 'PATIENT 123', 'PatientID': None},
        ]
        resp = client.get("/api/dashboard/qc/sample-names?catalog_group=LS1A04&bead_lot=008&days=365")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        names = [d['sample_name'] for d in data]
        assert 'POS CONTROL E' in names
        patient_ids = [d['patient_id'] for d in data]
        assert 'MP1' in patient_ids

    def test_returns_sorted(self, app, client):
        vendor_db = app.state.vendor_db
        vendor_db.execute_query.return_value = [
            {'SampleIDName': 'ZZZ', 'PatientID': None},
            {'SampleIDName': 'AAA', 'PatientID': None},
        ]
        resp = client.get("/api/dashboard/qc/sample-names?catalog_group=LS1A04&bead_lot=008")
        data = resp.json()
        assert data[0]['sample_name'] == 'AAA'
        assert data[1]['sample_name'] == 'ZZZ'


class TestOperatorCacheRefreshEndpoint:
    """Tests for POST /api/config/operator-cache/refresh."""

    def test_refresh_succeeds(self, app, client):
        resp = client.post("/api/config/operator-cache/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        app.state.polling_service.rebuild_operator_cache.assert_called_once()

    def test_refresh_returns_503_when_no_polling_service(self, app, client):
        app.state.polling_service = None
        resp = client.post("/api/config/operator-cache/refresh")
        assert resp.status_code == 503


class TestOperatorsEndpoint:
    """Verify operators discovery reads from OperatorRunCache."""

    def test_get_operators_returns_distinct_from_cache(self, app, client, mock_normalized_db, mock_sqlite_db):
        mock_sqlite_db.operators.get_excluded_operators.return_value = []
        mock_sqlite_db.operators.get_operator_aliases.return_value = []
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 3}],
            [{'operator': 'JDOE'}, {'operator': 'ASMITH'}],
        ]
        resp = client.get("/api/dashboard/qc/operators?days=90")
        assert resp.status_code == 200
        data = resp.json()
        assert data == ['ASMITH', 'JDOE']

    def test_get_operators_excludes_hidden(self, app, client, mock_normalized_db, mock_sqlite_db):
        mock_sqlite_db.operators.get_excluded_operators.return_value = [
            {"username": "JDOE", "label": None}
        ]
        mock_sqlite_db.operators.get_operator_aliases.return_value = []
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 3}],
            [{'operator': 'JDOE'}, {'operator': 'ASMITH'}],
        ]
        resp = client.get("/api/dashboard/qc/operators?days=90")
        data = resp.json()
        assert data == ['ASMITH']

    def test_get_operators_applies_aliases(self, app, client, mock_normalized_db, mock_sqlite_db):
        mock_sqlite_db.operators.get_excluded_operators.return_value = []
        mock_sqlite_db.operators.get_operator_aliases.return_value = [
            {"username": "JDOE", "display_name": "Jane Doe"}
        ]
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 3}],
            [{'operator': 'JDOE'}],
        ]
        resp = client.get("/api/dashboard/qc/operators?days=90")
        data = resp.json()
        assert data == ['Jane Doe']

    def test_lazy_populates_on_empty_cache(self, app, client, mock_normalized_db, mock_sqlite_db):
        mock_sqlite_db.operators.get_excluded_operators.return_value = []
        mock_sqlite_db.operators.get_operator_aliases.return_value = []
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 0}],
            [{'operator': 'OP-A'}],
        ]
        resp = client.get("/api/dashboard/qc/operators?days=90")
        assert resp.status_code == 200
        app.state.polling_service.populate_operator_cache.assert_called_once()


class TestOperatorSnRatioEndpoint:
    """Verify operator S/N ratio reads from OperatorRunCache."""

    def test_operator_sn_ratio_returns_cache_rows(self, app, client, mock_normalized_db, mock_sqlite_db):
        mock_sqlite_db.operators.get_excluded_operators.return_value = []
        mock_sqlite_db.operators.get_operator_aliases.return_value = []
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 2}],
            [
                {
                    'operator': 'JDOE', 'tray_id': 1,
                    'analysis_dt': datetime(2024, 1, 1, 10, 0),
                    'run_name': 'Run-1', 'sn_median': 50.0, 'sn_min': 45.0, 'sn_max': 55.0,
                },
                {
                    'operator': 'ASMITH', 'tray_id': 2,
                    'analysis_dt': datetime(2024, 1, 2, 10, 0),
                    'run_name': 'Run-2', 'sn_median': 30.0, 'sn_min': 28.0, 'sn_max': 32.0,
                },
            ],
        ]
        resp = client.get("/api/dashboard/qc/operator-sn-ratio?days=90")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        operators = [d['operator'] for d in data]
        assert 'JDOE' in operators
        assert 'ASMITH' in operators
        jdoe = [d for d in data if d['operator'] == 'JDOE'][0]
        assert jdoe['sn_median'] == 50.0

    def test_operator_sn_ratio_excludes_hidden(self, app, client, mock_normalized_db, mock_sqlite_db):
        mock_sqlite_db.operators.get_excluded_operators.return_value = [
            {"username": "JDOE", "label": None}
        ]
        mock_sqlite_db.operators.get_operator_aliases.return_value = []
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 2}],
            [
                {
                    'operator': 'JDOE', 'tray_id': 1,
                    'analysis_dt': datetime(2024, 1, 1, 10, 0),
                    'run_name': 'Run-1', 'sn_median': 50.0, 'sn_min': 45.0, 'sn_max': 55.0,
                },
                {
                    'operator': 'ASMITH', 'tray_id': 2,
                    'analysis_dt': datetime(2024, 1, 2, 10, 0),
                    'run_name': 'Run-2', 'sn_median': 30.0, 'sn_min': 28.0, 'sn_max': 32.0,
                },
            ],
        ]
        resp = client.get("/api/dashboard/qc/operator-sn-ratio?days=90")
        data = resp.json()
        assert len(data) == 1
        assert data[0]['operator'] == 'ASMITH'

    def test_lazy_populates_on_empty_cache(self, app, client, mock_normalized_db, mock_sqlite_db):
        mock_sqlite_db.operators.get_excluded_operators.return_value = []
        mock_sqlite_db.operators.get_operator_aliases.return_value = []
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 0}],
            [],
        ]
        resp = client.get("/api/dashboard/qc/operator-sn-ratio?days=90")
        assert resp.status_code == 200
        app.state.polling_service.populate_operator_cache.assert_called_once()


class TestOperatorNcEndpoint:
    """Verify operator NC (background) reads from OperatorRunCache."""

    def test_operator_nc_returns_cache_rows(self, app, client, mock_normalized_db, mock_sqlite_db):
        mock_sqlite_db.operators.get_excluded_operators.return_value = []
        mock_sqlite_db.operators.get_operator_aliases.return_value = []
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 2}],
            [
                {
                    'operator': 'JDOE', 'tray_id': 1,
                    'analysis_dt': datetime(2024, 1, 1, 10, 0),
                    'run_name': 'Run-1', 'nc_median': 150.0, 'nc_min': 100.0, 'nc_max': 200.0,
                },
                {
                    'operator': 'ASMITH', 'tray_id': 2,
                    'analysis_dt': datetime(2024, 1, 2, 10, 0),
                    'run_name': 'Run-2', 'nc_median': 300.0, 'nc_min': 280.0, 'nc_max': 320.0,
                },
            ],
        ]
        resp = client.get("/api/dashboard/qc/operator-nc?days=90")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        jdoe = [d for d in data if d['operator'] == 'JDOE'][0]
        assert jdoe['nc_median'] == 150.0
        asmith = [d for d in data if d['operator'] == 'ASMITH'][0]
        assert asmith['nc_median'] == 300.0

    def test_lazy_populates_on_empty_cache(self, app, client, mock_normalized_db, mock_sqlite_db):
        mock_sqlite_db.operators.get_excluded_operators.return_value = []
        mock_sqlite_db.operators.get_operator_aliases.return_value = []
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 0}],
            [],
        ]
        resp = client.get("/api/dashboard/qc/operator-nc?days=90")
        assert resp.status_code == 200
        app.state.polling_service.populate_operator_cache.assert_called_once()


class TestOperatorBeadCountsEndpoint:
    """Verify operator bead counts reads from OperatorRunCache."""

    def test_operator_bead_counts_returns_cache_rows(self, app, client, mock_normalized_db, mock_sqlite_db):
        mock_sqlite_db.operators.get_excluded_operators.return_value = []
        mock_sqlite_db.operators.get_operator_aliases.return_value = []
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 2}],
            [
                {
                    'operator': 'JDOE', 'tray_id': 1,
                    'analysis_dt': datetime(2024, 1, 1, 10, 0),
                    'run_name': 'Run-1',
                    'count_median': 90.0, 'count_min': 80.0, 'count_max': 100.0,
                },
                {
                    'operator': 'ASMITH', 'tray_id': 2,
                    'analysis_dt': datetime(2024, 1, 2, 10, 0),
                    'run_name': 'Run-2',
                    'count_median': 50.0, 'count_min': 45.0, 'count_max': 55.0,
                },
            ],
        ]
        resp = client.get("/api/dashboard/qc/operator-bead-counts?days=90")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        jdoe = [d for d in data if d['operator'] == 'JDOE'][0]
        assert jdoe['median_count'] == 90.0
        assert jdoe['min_count'] == 80.0
        assert jdoe['max_count'] == 100.0
        asmith = [d for d in data if d['operator'] == 'ASMITH'][0]
        assert asmith['median_count'] == 50.0

    def test_lazy_populates_on_empty_cache(self, app, client, mock_normalized_db, mock_sqlite_db):
        mock_sqlite_db.operators.get_excluded_operators.return_value = []
        mock_sqlite_db.operators.get_operator_aliases.return_value = []
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 0}],
            [],
        ]
        resp = client.get("/api/dashboard/qc/operator-bead-counts?days=90")
        assert resp.status_code == 200
        app.state.polling_service.populate_operator_cache.assert_called_once()


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data['status'] == 'ok'
