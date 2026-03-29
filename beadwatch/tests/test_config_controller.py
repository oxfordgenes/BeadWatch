import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime

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


class TestConfigController:
    def test_get_config_status_unconfigured(self, client, mock_sqlite_db):
        mock_sqlite_db.creds.is_initialized.return_value = False
        resp = client.get("/api/config/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data['configured'] is False

    def test_get_config_status_configured(self, client, mock_sqlite_db):
        mock_sqlite_db.creds.is_initialized.return_value = True
        mock_sqlite_db.creds.get_credentials.return_value = {
            'server': 'LABSERVER', 'vendor_database': 'FusionData',
            'normalized_database': 'BeadWatch', 'username': 'sa',
            'password': 'secret', 'created_at': '2024-01-01'
        }
        mock_sqlite_db.app_state.get_state.return_value = 'true'
        resp = client.get("/api/config/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data['configured'] is True
        assert data['server'] == 'LABSERVER'
        assert data['normalized_initialized'] is True

    @patch('api.controllers.config_controller.SQLServerConnection')
    def test_test_connection_success(self, MockConn, client):
        mock_instance = MagicMock()
        mock_instance.test_connection.return_value = {
            'success': True, 'server_time': '2024-01-01 12:00:00'
        }
        MockConn.return_value = mock_instance

        resp = client.post("/api/config/test-connection", json={
            'server': 'SERVER', 'vendor_database': 'DB', 'username': 'user', 'password': 'pass'
        })
        assert resp.status_code == 200
        assert resp.json()['success'] is True

    @patch('api.controllers.config_controller.SQLServerConnection')
    def test_test_connection_failure(self, MockConn, client):
        mock_instance = MagicMock()
        mock_instance.test_connection.return_value = {
            'success': False, 'message': 'Connection refused'
        }
        MockConn.return_value = mock_instance

        resp = client.post("/api/config/test-connection", json={
            'server': 'BAD', 'vendor_database': 'DB', 'username': 'user', 'password': 'pass'
        })
        assert resp.status_code == 200
        assert resp.json()['success'] is False

    @patch('api.controllers.config_controller.SQLServerConnection')
    def test_save_credentials_rejects_bad_connection(self, MockConn, client):
        mock_instance = MagicMock()
        mock_instance.test_connection.return_value = {'success': False, 'message': 'Failed'}
        MockConn.return_value = mock_instance

        resp = client.post("/api/config/credentials", json={
            'server': 'SERVER', 'vendor_database': 'DB', 'username': 'user', 'password': 'pass'
        })
        assert resp.status_code == 400

    def test_initialize_database_without_credentials(self, client, mock_sqlite_db):
        mock_sqlite_db.creds.is_initialized.return_value = False
        resp = client.post("/api/config/initialize-database")
        assert resp.status_code == 400


class TestPollingIntervalEndpoints:
    """Verify GET/POST /api/config/polling-interval."""

    def test_get_polling_interval_default(self, client, mock_sqlite_db):
        mock_sqlite_db.app_state.get_state.return_value = None
        resp = client.get("/api/config/polling-interval")
        assert resp.status_code == 200
        data = resp.json()
        assert data['interval_minutes'] == 60
        assert data['min'] == 5
        assert data['max'] == 1440

    def test_get_polling_interval_stored(self, client, mock_sqlite_db):
        mock_sqlite_db.app_state.get_state.return_value = '30'
        resp = client.get("/api/config/polling-interval")
        assert resp.status_code == 200
        assert resp.json()['interval_minutes'] == 30

    def test_set_polling_interval_saves_and_returns(self, client, mock_sqlite_db, app):
        mock_scheduler = MagicMock()
        mock_scheduler.running = True
        app.state.scheduler = mock_scheduler

        resp = client.post("/api/config/polling-interval", json={"interval_minutes": 45})
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['interval_minutes'] == 45
        mock_sqlite_db.app_state.set_state.assert_called_with('poll_interval_minutes', '45')
        mock_scheduler.reschedule_job.assert_called_once_with(
            'polling_job', trigger='interval', minutes=45
        )

    def test_set_polling_interval_too_low(self, client):
        resp = client.post("/api/config/polling-interval", json={"interval_minutes": 2})
        assert resp.status_code == 422

    def test_set_polling_interval_too_high(self, client):
        resp = client.post("/api/config/polling-interval", json={"interval_minutes": 2000})
        assert resp.status_code == 422

    def test_set_polling_interval_no_scheduler(self, client, mock_sqlite_db, app):
        """When scheduler is not running, save succeeds without rescheduling."""
        app.state.scheduler = None
        resp = client.post("/api/config/polling-interval", json={"interval_minutes": 120})
        assert resp.status_code == 200
        assert resp.json()['success'] is True
        mock_sqlite_db.app_state.set_state.assert_called_with('poll_interval_minutes', '120')


class TestQCSampleDefinitionsConfig:
    """Tests for QC sample definitions CRUD endpoints."""

    def test_get_definitions_empty(self, client, mock_sqlite_db):
        mock_sqlite_db.qc.get_qc_sample_definitions.return_value = []
        resp = client.get("/api/config/qc-sample-definitions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_post_creates_definition(self, client, mock_sqlite_db):
        mock_sqlite_db.qc.add_qc_sample_definition.return_value = 1
        resp = client.post("/api/config/qc-sample-definitions", json={
            "pattern": "MP1", "match_type": "substring", "role": "positive", "label": "My Pos 1"
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["id"] == 1
        mock_sqlite_db.qc.add_qc_sample_definition.assert_called_once_with(
            pattern="MP1", match_type="substring", role="positive", label="My Pos 1"
        )

    def test_post_rejects_invalid_role(self, client):
        resp = client.post("/api/config/qc-sample-definitions", json={
            "pattern": "MP1", "match_type": "substring", "role": "unknown"
        })
        assert resp.status_code == 422

    def test_delete_succeeds(self, client, mock_sqlite_db):
        mock_sqlite_db.qc.delete_qc_sample_definition.return_value = True
        resp = client.delete("/api/config/qc-sample-definitions/1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_delete_not_found(self, client, mock_sqlite_db):
        mock_sqlite_db.qc.delete_qc_sample_definition.return_value = False
        resp = client.delete("/api/config/qc-sample-definitions/999")
        assert resp.status_code == 404


class TestQCTrackedBeadsConfig:
    """Tests for QC tracked beads CRUD endpoints."""

    def test_get_tracked_beads_empty(self, client, mock_sqlite_db):
        mock_sqlite_db.qc.get_qc_tracked_beads.return_value = []
        resp = client.get("/api/config/qc-tracked-beads")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_tracked_beads_filtered(self, client, mock_sqlite_db):
        mock_sqlite_db.qc.get_qc_tracked_beads.return_value = [
            {"id": 1, "catalog_group": "LS1A04", "bead_lot": "008", "bead_id": "3", "label": "A2", "created_at": "2024-01-01"}
        ]
        resp = client.get("/api/config/qc-tracked-beads?catalog_group=LS1A04&bead_lot=008")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["bead_id"] == "3"
        mock_sqlite_db.qc.get_qc_tracked_beads.assert_called_once_with(
            catalog_group="LS1A04", bead_lot="008"
        )

    def test_post_creates_tracked_bead(self, client, mock_sqlite_db):
        mock_sqlite_db.qc.add_qc_tracked_bead.return_value = 5
        resp = client.post("/api/config/qc-tracked-beads", json={
            "catalog_group": "LS1A04", "bead_lot": "008", "bead_id": "3", "label": "A2"
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["id"] == 5

    def test_delete_not_found(self, client, mock_sqlite_db):
        mock_sqlite_db.qc.delete_qc_tracked_bead.return_value = False
        resp = client.delete("/api/config/qc-tracked-beads/999")
        assert resp.status_code == 404

    def test_bulk_delete_returns_count(self, client, mock_sqlite_db):
        mock_sqlite_db.qc.delete_qc_tracked_beads_by_group.return_value = 3
        resp = client.delete("/api/config/qc-tracked-beads/group/LS1A04/008")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["deleted"] == 3
        mock_sqlite_db.qc.delete_qc_tracked_beads_by_group.assert_called_once_with("LS1A04", "008")

    def test_bulk_delete_not_found(self, client, mock_sqlite_db):
        mock_sqlite_db.qc.delete_qc_tracked_beads_by_group.return_value = 0
        resp = client.delete("/api/config/qc-tracked-beads/group/NOPE/999")
        assert resp.status_code == 404


class TestInstrumentAliasesConfig:
    """Tests for instrument aliases CRUD endpoints."""

    def test_get_aliases_empty(self, client, mock_sqlite_db):
        mock_sqlite_db.instruments.get_instrument_aliases.return_value = []
        resp = client.get("/api/config/instrument-aliases")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_aliases_returns_items(self, client, mock_sqlite_db):
        mock_sqlite_db.instruments.get_instrument_aliases.return_value = [
            {"id": 1, "serial_number": "FM3DD24004021", "display_name": "Lab A", "created_at": "2024-01-01"}
        ]
        resp = client.get("/api/config/instrument-aliases")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["serial_number"] == "FM3DD24004021"
        assert data[0]["display_name"] == "Lab A"

    def test_post_creates_alias(self, client, mock_sqlite_db):
        mock_sqlite_db.instruments.add_instrument_alias.return_value = 1
        resp = client.post("/api/config/instrument-aliases", json={
            "serial_number": "FM3DD24004021", "display_name": "Lab A"
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["id"] == 1
        mock_sqlite_db.instruments.add_instrument_alias.assert_called_once_with(
            serial_number="FM3DD24004021", display_name="Lab A"
        )

    def test_post_rejects_empty_serial(self, client):
        resp = client.post("/api/config/instrument-aliases", json={
            "serial_number": "", "display_name": "Lab A"
        })
        assert resp.status_code == 422

    def test_post_rejects_empty_display_name(self, client):
        resp = client.post("/api/config/instrument-aliases", json={
            "serial_number": "FM3DD24004021", "display_name": ""
        })
        assert resp.status_code == 422

    def test_delete_succeeds(self, client, mock_sqlite_db):
        mock_sqlite_db.instruments.delete_instrument_alias.return_value = True
        resp = client.delete("/api/config/instrument-aliases/1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_delete_not_found(self, client, mock_sqlite_db):
        mock_sqlite_db.instruments.delete_instrument_alias.return_value = False
        resp = client.delete("/api/config/instrument-aliases/999")
        assert resp.status_code == 404


class TestExcludedOperatorsConfig:
    """Tests for excluded operators CRUD endpoints."""

    def test_get_excluded_operators_empty(self, client, mock_sqlite_db):
        mock_sqlite_db.operators.get_excluded_operators.return_value = []
        resp = client.get("/api/config/excluded-operators")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_excluded_operators_returns_items(self, client, mock_sqlite_db):
        mock_sqlite_db.operators.get_excluded_operators.return_value = [
            {"id": 1, "username": "JDOE", "label": "Left lab", "created_at": "2024-01-01"}
        ]
        resp = client.get("/api/config/excluded-operators")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["username"] == "JDOE"
        assert data[0]["label"] == "Left lab"

    def test_post_creates_excluded_operator(self, client, mock_sqlite_db):
        mock_sqlite_db.operators.add_excluded_operator.return_value = 1
        resp = client.post("/api/config/excluded-operators", json={
            "username": "jdoe", "label": "Left lab"
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["id"] == 1
        mock_sqlite_db.operators.add_excluded_operator.assert_called_once_with(
            username="jdoe", label="Left lab"
        )

    def test_post_rejects_empty_username(self, client):
        resp = client.post("/api/config/excluded-operators", json={
            "username": "", "label": "test"
        })
        assert resp.status_code == 422

    def test_delete_succeeds(self, client, mock_sqlite_db):
        mock_sqlite_db.operators.delete_excluded_operator.return_value = True
        resp = client.delete("/api/config/excluded-operators/1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_delete_not_found(self, client, mock_sqlite_db):
        mock_sqlite_db.operators.delete_excluded_operator.return_value = False
        resp = client.delete("/api/config/excluded-operators/999")
        assert resp.status_code == 404


class TestOperatorAliasesConfig:
    """Tests for operator aliases CRUD endpoints."""

    def test_get_aliases_empty(self, client, mock_sqlite_db):
        mock_sqlite_db.operators.get_operator_aliases.return_value = []
        resp = client.get("/api/config/operator-aliases")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_aliases_returns_items(self, client, mock_sqlite_db):
        mock_sqlite_db.operators.get_operator_aliases.return_value = [
            {"id": 1, "username": "JDOE", "display_name": "Jane Doe", "created_at": "2024-01-01"}
        ]
        resp = client.get("/api/config/operator-aliases")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["username"] == "JDOE"
        assert data[0]["display_name"] == "Jane Doe"

    def test_post_creates_alias(self, client, mock_sqlite_db):
        mock_sqlite_db.operators.add_operator_alias.return_value = 1
        resp = client.post("/api/config/operator-aliases", json={
            "username": "jdoe", "display_name": "Jane Doe"
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["id"] == 1
        mock_sqlite_db.operators.add_operator_alias.assert_called_once_with(
            username="jdoe", display_name="Jane Doe"
        )

    def test_post_rejects_empty_username(self, client):
        resp = client.post("/api/config/operator-aliases", json={
            "username": "", "display_name": "Jane Doe"
        })
        assert resp.status_code == 422

    def test_post_rejects_empty_display_name(self, client):
        resp = client.post("/api/config/operator-aliases", json={
            "username": "jdoe", "display_name": ""
        })
        assert resp.status_code == 422

    def test_delete_succeeds(self, client, mock_sqlite_db):
        mock_sqlite_db.operators.delete_operator_alias.return_value = True
        resp = client.delete("/api/config/operator-aliases/1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_delete_not_found(self, client, mock_sqlite_db):
        mock_sqlite_db.operators.delete_operator_alias.return_value = False
        resp = client.delete("/api/config/operator-aliases/999")
        assert resp.status_code == 404


class TestThresholdEndpoints:
    def test_get_thresholds(self, client, mock_normalized_db):
        mock_normalized_db.execute_query.return_value = [
            {'id': 1, 'metric_name': 'min_bead_count', 'upper_warning': None,
             'upper_critical': None, 'lower_warning': 50.0, 'lower_critical': 25.0, 'enabled': True},
            {'id': 2, 'metric_name': 'mean_mfi', 'upper_warning': 20000.0,
             'upper_critical': 25000.0, 'lower_warning': 100.0, 'lower_critical': 50.0, 'enabled': True},
        ]
        resp = client.get("/api/config/thresholds")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]['metric_name'] == 'min_bead_count'
        assert data[0]['lower_warning'] == 50.0
        assert data[1]['enabled'] is True

    def test_get_thresholds_no_normalized_db(self, client, mock_normalized_db):
        client.app.state.normalized_db = None
        resp = client.get("/api/config/thresholds")
        assert resp.status_code == 503

    def test_update_threshold(self, client, mock_normalized_db):
        mock_normalized_db.execute_non_query.return_value = 1
        mock_normalized_db.execute_query.return_value = [
            {'id': 1, 'metric_name': 'min_bead_count', 'upper_warning': None,
             'upper_critical': None, 'lower_warning': 40.0, 'lower_critical': 20.0, 'enabled': True},
        ]
        resp = client.put("/api/config/thresholds/1", json={
            "upper_warning": None,
            "upper_critical": None,
            "lower_warning": 40.0,
            "lower_critical": 20.0,
            "enabled": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['threshold']['lower_warning'] == 40.0
        mock_normalized_db.execute_non_query.assert_called_once()

    def test_update_threshold_not_found(self, client, mock_normalized_db):
        mock_normalized_db.execute_non_query.return_value = 0
        resp = client.put("/api/config/thresholds/999", json={
            "upper_warning": None,
            "upper_critical": None,
            "lower_warning": 40.0,
            "lower_critical": 20.0,
            "enabled": True,
        })
        assert resp.status_code == 404

    def test_update_threshold_no_normalized_db(self, client, mock_normalized_db):
        client.app.state.normalized_db = None
        resp = client.put("/api/config/thresholds/1", json={
            "upper_warning": None,
            "upper_critical": None,
            "lower_warning": 40.0,
            "lower_critical": 20.0,
            "enabled": True,
        })
        assert resp.status_code == 503

    def test_update_threshold_invalid_ordering_lc_gt_lw(self, client, mock_normalized_db):
        """lower_critical > lower_warning should be rejected (422)."""
        resp = client.put("/api/config/thresholds/1", json={
            "lower_critical": 100.0,
            "lower_warning": 50.0,
            "enabled": True,
        })
        assert resp.status_code == 422

    def test_update_threshold_invalid_ordering_uw_gt_uc(self, client, mock_normalized_db):
        """upper_warning > upper_critical should be rejected (422)."""
        resp = client.put("/api/config/thresholds/1", json={
            "upper_warning": 500.0,
            "upper_critical": 100.0,
            "enabled": True,
        })
        assert resp.status_code == 422

    def test_update_threshold_invalid_ordering_lw_gt_uw_all_four(self, client, mock_normalized_db):
        """lower_warning > upper_warning rejected only when all four values present."""
        resp = client.put("/api/config/thresholds/1", json={
            "lower_critical": 10.0,
            "lower_warning": 200.0,
            "upper_warning": 100.0,
            "upper_critical": 300.0,
            "enabled": True,
        })
        assert resp.status_code == 422

    def test_update_threshold_sparse_lower_only_accepted(self, client, mock_normalized_db):
        """Lower-only thresholds (no upper) should be accepted — cross-side not enforced."""
        mock_normalized_db.execute_non_query.return_value = 1
        mock_normalized_db.execute_query.return_value = [
            {'id': 1, 'metric_name': 'min_bead_count', 'upper_warning': None,
             'upper_critical': None, 'lower_warning': 50.0, 'lower_critical': 25.0, 'enabled': True},
        ]
        resp = client.put("/api/config/thresholds/1", json={
            "lower_critical": 25.0,
            "lower_warning": 50.0,
            "enabled": True,
        })
        assert resp.status_code == 200


class TestSettingsExportImport:
    """Tests for GET /api/config/settings/export and POST /api/config/settings/import."""

    def test_export_returns_settings(self, client, mock_sqlite_db, mock_normalized_db):
        mock_sqlite_db.settings.export_all_settings.return_value = {
            'qc_sample_definitions': [{'pattern': 'MP1', 'match_type': 'substring', 'role': 'positive', 'label': None}],
            'qc_tracked_beads': [],
            'excluded_instruments': [],
            'instrument_aliases': [],
            'excluded_operators': [],
            'operator_aliases': [],
            'polling_interval_minutes': 60,
        }
        mock_normalized_db.execute_query.return_value = [
            {'metric_name': 'min_bead_count', 'upper_warning': None, 'upper_critical': None,
             'lower_warning': 50.0, 'lower_critical': 25.0, 'enabled': True},
        ]
        resp = client.get("/api/config/settings/export")
        assert resp.status_code == 200
        data = resp.json()
        assert data['beadwatch_settings_version'] == 1
        assert 'exported_at' in data
        assert len(data['qc_sample_definitions']) == 1
        assert len(data['qc_thresholds']) == 1
        assert data['polling_interval_minutes'] == 60

    def test_export_without_normalized_db(self, client, mock_sqlite_db, app):
        """Export still works when normalized DB is unavailable."""
        app.state.normalized_db = None
        mock_sqlite_db.settings.export_all_settings.return_value = {
            'qc_sample_definitions': [],
            'qc_tracked_beads': [],
            'excluded_instruments': [],
            'instrument_aliases': [],
            'excluded_operators': [],
            'operator_aliases': [],
        }
        resp = client.get("/api/config/settings/export")
        assert resp.status_code == 200
        data = resp.json()
        assert 'qc_thresholds' not in data

    def test_import_full_file(self, client, mock_sqlite_db, mock_normalized_db):
        mock_sqlite_db.settings.import_all_settings.return_value = {
            'qc_sample_definitions': 2,
            'qc_tracked_beads': 1,
            'instrument_aliases': 1,
            'polling_interval_minutes': True,
        }
        mock_normalized_db.execute_query.return_value = [
            {'metric_name': 'min_bead_count'},
            {'metric_name': 'mean_mfi'},
        ]
        mock_normalized_db.execute_non_query.return_value = 1
        payload = {
            'beadwatch_settings_version': 1,
            'qc_sample_definitions': [
                {'pattern': 'MP1', 'match_type': 'substring', 'role': 'positive', 'label': None},
                {'pattern': 'NC', 'match_type': 'substring', 'role': 'negative', 'label': None},
            ],
            'qc_tracked_beads': [{'catalog_group': 'LS1A04', 'bead_lot': '008', 'bead_id': '3', 'label': 'A1'}],
            'instrument_aliases': [{'serial_number': 'SN001', 'display_name': 'Lab-1'}],
            'polling_interval_minutes': 45,
            'qc_thresholds': [
                {'metric_name': 'min_bead_count', 'lower_warning': 50, 'lower_critical': 25,
                 'upper_warning': None, 'upper_critical': None, 'enabled': True},
            ],
        }
        resp = client.post("/api/config/settings/import", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['imported']['qc_sample_definitions'] == 2
        assert data['imported']['qc_thresholds'] == 1
        assert 'excluded_instruments' in data['skipped']
        mock_sqlite_db.settings.import_all_settings.assert_called_once()

    def test_import_missing_version_rejected(self, client):
        resp = client.post("/api/config/settings/import", json={
            'qc_sample_definitions': [],
        })
        assert resp.status_code == 400
        assert 'version' in resp.json()['detail'].lower()

    def test_import_version_zero_rejected(self, client):
        resp = client.post("/api/config/settings/import", json={
            'beadwatch_settings_version': 0,
        })
        assert resp.status_code == 400

    def test_import_version_string_rejected(self, client):
        """Version must be integer, not string."""
        resp = client.post("/api/config/settings/import", json={
            'beadwatch_settings_version': "1",
        })
        assert resp.status_code == 400
        assert 'integer' in resp.json()['detail'].lower()

    def test_import_version_bool_rejected(self, client):
        resp = client.post("/api/config/settings/import", json={
            'beadwatch_settings_version': True,
        })
        assert resp.status_code == 400

    def test_import_validation_error_returns_400(self, client, mock_sqlite_db):
        mock_sqlite_db.settings.validate_import_data.side_effect = ValueError("Duplicate ['serial_number'] in instrument_aliases")
        resp = client.post("/api/config/settings/import", json={
            'beadwatch_settings_version': 1,
            'instrument_aliases': [
                {'serial_number': 'SN1', 'display_name': 'A'},
                {'serial_number': 'SN1', 'display_name': 'B'},
            ],
        })
        assert resp.status_code == 400
        assert 'Duplicate' in resp.json()['detail']
        # Verify import_all_settings was NOT called (validation failed first)
        mock_sqlite_db.settings.import_all_settings.assert_not_called()

    def test_import_unknown_threshold_warns(self, client, mock_sqlite_db, mock_normalized_db):
        mock_sqlite_db.settings.import_all_settings.return_value = {}
        mock_normalized_db.execute_query.return_value = [
            {'metric_name': 'min_bead_count'},
        ]
        mock_normalized_db.execute_non_query.return_value = 1
        resp = client.post("/api/config/settings/import", json={
            'beadwatch_settings_version': 1,
            'qc_thresholds': [
                {'metric_name': 'min_bead_count', 'lower_warning': 50, 'lower_critical': 25,
                 'upper_warning': None, 'upper_critical': None, 'enabled': True},
                {'metric_name': 'custom_metric', 'lower_warning': 10, 'lower_critical': 5,
                 'upper_warning': None, 'upper_critical': None, 'enabled': True},
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['imported']['qc_thresholds'] == 1
        assert any('custom_metric' in w for w in data['warnings'])

    def test_import_without_normalized_db_warns(self, client, mock_sqlite_db, app):
        app.state.normalized_db = None
        mock_sqlite_db.settings.import_all_settings.return_value = {}
        resp = client.post("/api/config/settings/import", json={
            'beadwatch_settings_version': 1,
            'qc_thresholds': [
                {'metric_name': 'min_bead_count', 'lower_warning': 50, 'lower_critical': 25,
                 'upper_warning': None, 'upper_critical': None, 'enabled': True},
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert any('not available' in w.lower() for w in data['warnings'])
        assert 'qc_thresholds' in data['skipped']

    def test_import_partial_file_skips_missing(self, client, mock_sqlite_db):
        """Only qc_sample_definitions in payload — others in skipped."""
        mock_sqlite_db.settings.import_all_settings.return_value = {
            'qc_sample_definitions': 1,
        }
        resp = client.post("/api/config/settings/import", json={
            'beadwatch_settings_version': 1,
            'qc_sample_definitions': [
                {'pattern': 'X', 'match_type': 'substring', 'role': 'negative', 'label': None},
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert 'qc_tracked_beads' in data['skipped']
        assert 'excluded_instruments' in data['skipped']

    def test_import_invalid_polling_interval_rejected(self, client, mock_sqlite_db):
        """Polling interval out of range is rejected before any writes."""
        resp = client.post("/api/config/settings/import", json={
            'beadwatch_settings_version': 1,
            'polling_interval_minutes': 99999,
        })
        assert resp.status_code == 400
        assert 'polling_interval_minutes' in resp.json()['detail']
        mock_sqlite_db.settings.import_all_settings.assert_not_called()

    def test_import_string_polling_interval_rejected(self, client, mock_sqlite_db):
        resp = client.post("/api/config/settings/import", json={
            'beadwatch_settings_version': 1,
            'polling_interval_minutes': "abc",
        })
        assert resp.status_code == 400
        mock_sqlite_db.settings.import_all_settings.assert_not_called()

    def test_import_invalid_threshold_ordering_rejected(self, client, mock_sqlite_db):
        """Threshold with lower_critical > lower_warning is rejected."""
        resp = client.post("/api/config/settings/import", json={
            'beadwatch_settings_version': 1,
            'qc_thresholds': [
                {'metric_name': 'min_bead_count', 'lower_warning': 25, 'lower_critical': 50,
                 'upper_warning': None, 'upper_critical': None, 'enabled': True},
            ],
        })
        assert resp.status_code == 400
        assert 'lower_critical' in resp.json()['detail'].lower()
        mock_sqlite_db.settings.import_all_settings.assert_not_called()

    def test_import_non_dict_row_returns_400(self, client, mock_sqlite_db):
        """Non-dict items in a table array return 400, not 500."""
        mock_sqlite_db.settings.validate_import_data.side_effect = ValueError(
            "'instrument_aliases[0]' must be an object, got int"
        )
        resp = client.post("/api/config/settings/import", json={
            'beadwatch_settings_version': 1,
            'instrument_aliases': [123],
        })
        assert resp.status_code == 400
        assert 'must be an object' in resp.json()['detail']
        mock_sqlite_db.settings.import_all_settings.assert_not_called()

    def test_import_malformed_threshold_entry_rejected(self, client, mock_sqlite_db):
        """Non-dict threshold entry is rejected."""
        resp = client.post("/api/config/settings/import", json={
            'beadwatch_settings_version': 1,
            'qc_thresholds': [123],
        })
        assert resp.status_code == 400
        mock_sqlite_db.settings.import_all_settings.assert_not_called()

    def test_import_threshold_query_failure_warns(self, client, mock_sqlite_db, mock_normalized_db):
        """If querying existing thresholds fails in phase 2, warn instead of silent skip."""
        mock_sqlite_db.settings.import_all_settings.return_value = {}
        # Phase 1 query succeeds (validation), phase 2 query fails (application)
        mock_normalized_db.execute_query.side_effect = [
            [{'metric_name': 'min_bead_count'}],  # phase 1 validation
            Exception("connection lost"),           # phase 2 requery
        ]
        resp = client.post("/api/config/settings/import", json={
            'beadwatch_settings_version': 1,
            'qc_thresholds': [
                {'metric_name': 'min_bead_count', 'lower_warning': 50, 'lower_critical': 25,
                 'upper_warning': None, 'upper_critical': None, 'enabled': True},
            ],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['imported']['qc_thresholds'] == 0
        assert any('could not query' in w.lower() for w in data['warnings'])

    def test_import_rebuilds_caches(self, client, mock_sqlite_db, app):
        mock_sqlite_db.settings.import_all_settings.return_value = {'qc_sample_definitions': 1}
        polling = app.state.polling_service
        resp = client.post("/api/config/settings/import", json={
            'beadwatch_settings_version': 1,
            'qc_sample_definitions': [
                {'pattern': 'X', 'match_type': 'substring', 'role': 'negative', 'label': None},
            ],
        })
        assert resp.status_code == 200
        polling.rebuild_qc_cache.assert_called_once()
        polling.rebuild_instrument_cache.assert_called_once()
        polling.rebuild_operator_cache.assert_called_once()
