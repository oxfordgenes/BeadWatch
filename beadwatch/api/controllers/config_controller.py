import json
import logging
import re
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, Request

logger = logging.getLogger('beadwatch')

from database.sqlserver_handler import SQLServerConnection
from services.config_query_service import ConfigQueryService
from services.runtime_services import get_runtime
from models.config_models import (
    CredentialsInput, ConfigStatus, ConnectionTestResult, PollingIntervalInput,
    QCSampleDefinitionInput, QCSampleDefinition, QCTrackedBeadInput, QCTrackedBead,
    ExcludedInstrumentInput, ExcludedInstrument,
    InstrumentAliasInput, InstrumentAlias,
    ExcludedOperatorInput, ExcludedOperator,
    OperatorAliasInput, OperatorAlias,
    QCThreshold, QCThresholdUpdateInput,
)
from config.settings import (
    NORMALIZED_DB_NAME, POLL_INTERVAL_MINUTES, POLL_INTERVAL_MIN,
    POLL_INTERVAL_MAX, POLL_INTERVAL_STATE_KEY,
)


class ConfigController:
    """API controller for configuration endpoints.

    Like DashboardController, dependencies come from request.app.state.
    After save_credentials + initialize_database, the controller calls
    initialize_sql_connections() so the app is fully operational without
    a restart.
    """

    def __init__(self):
        self.router = APIRouter(prefix="/api/config", tags=["configuration"])
        self.query_service = ConfigQueryService()
        self._register_routes()

    def _register_routes(self):
        self.router.get("/status", response_model=ConfigStatus)(self.get_config_status)
        self.router.post("/credentials")(self.save_credentials)
        self.router.post("/test-connection", response_model=ConnectionTestResult)(self.test_connection)
        self.router.post("/initialize-database")(self.initialize_database)
        self.router.post("/rebuild-metrics")(self.rebuild_metrics)
        self.router.get("/polling-interval")(self.get_polling_interval)
        self.router.post("/polling-interval")(self.set_polling_interval)
        self.router.get("/qc-sample-definitions", response_model=List[QCSampleDefinition])(self.get_qc_sample_definitions)
        self.router.post("/qc-sample-definitions")(self.add_qc_sample_definition)
        self.router.delete("/qc-sample-definitions/{definition_id}")(self.delete_qc_sample_definition)
        self.router.get("/qc-tracked-beads", response_model=List[QCTrackedBead])(self.get_qc_tracked_beads)
        self.router.post("/qc-tracked-beads")(self.add_qc_tracked_bead)
        self.router.delete("/qc-tracked-beads/group/{catalog_group}/{bead_lot}")(self.delete_qc_tracked_beads_by_group)
        self.router.delete("/qc-tracked-beads/{bead_id}")(self.delete_qc_tracked_bead)
        self.router.get("/excluded-instruments", response_model=List[ExcludedInstrument])(self.get_excluded_instruments)
        self.router.post("/excluded-instruments")(self.add_excluded_instrument)
        self.router.delete("/excluded-instruments/{instrument_id}")(self.delete_excluded_instrument)
        self.router.get("/instrument-aliases", response_model=List[InstrumentAlias])(self.get_instrument_aliases)
        self.router.post("/instrument-aliases")(self.add_instrument_alias)
        self.router.delete("/instrument-aliases/{alias_id}")(self.delete_instrument_alias)
        self.router.post("/qc-cache/refresh")(self.refresh_qc_cache)
        self.router.post("/instrument-cache/refresh")(self.refresh_instrument_cache)
        self.router.get("/excluded-operators", response_model=List[ExcludedOperator])(self.get_excluded_operators)
        self.router.post("/excluded-operators")(self.add_excluded_operator)
        self.router.delete("/excluded-operators/{operator_id}")(self.delete_excluded_operator)
        self.router.get("/operator-aliases", response_model=List[OperatorAlias])(self.get_operator_aliases)
        self.router.post("/operator-aliases")(self.add_operator_alias)
        self.router.delete("/operator-aliases/{alias_id}")(self.delete_operator_alias)
        self.router.post("/operator-cache/refresh")(self.refresh_operator_cache)
        self.router.get("/catalog-group-prefs")(self.get_catalog_group_prefs)
        self.router.put("/catalog-group-prefs")(self.put_catalog_group_prefs)
        self.router.get("/bead-lot-prefs")(self.get_bead_lot_prefs)
        self.router.put("/bead-lot-prefs")(self.put_bead_lot_prefs)
        self.router.get("/thresholds", response_model=List[QCThreshold])(self.get_thresholds)
        self.router.put("/thresholds/{threshold_id}")(self.update_threshold)
        self.router.get("/settings/export")(self.export_settings)
        self.router.post("/settings/import")(self.import_settings)

    async def get_config_status(self, request: Request) -> ConfigStatus:
        """Check if application has been configured"""
        sqlite_db = get_runtime(request.app).sqlite_db
        if not sqlite_db.creds.is_initialized():
            return ConfigStatus(configured=False)

        creds = sqlite_db.creds.get_credentials()
        normalized_initialized = sqlite_db.app_state.get_state('normalized_db_initialized') == 'true'
        return ConfigStatus(
            configured=True, server=creds['server'], vendor_database=creds['vendor_database'],
            normalized_database=creds['normalized_database'],
            username=creds.get('username'),
            normalized_initialized=normalized_initialized,
            created_at=creds.get('created_at')
        )

    async def test_connection(self, credentials: CredentialsInput) -> ConnectionTestResult:
        """Test SQL Server connection without saving credentials"""
        try:
            test_conn = SQLServerConnection(
                server=credentials.server, database=credentials.vendor_database,
                username=credentials.username, password=credentials.password
            )

            result = test_conn.test_connection()

            if result['success']:
                return ConnectionTestResult(
                    success=True, message="Connection successful",
                    details={'server_time': str(result.get('server_time'))}
                )
            else:
                return ConnectionTestResult(success=False, message=result['message'])

        except Exception as e:
            return ConnectionTestResult(success=False, message=f"Connection failed: {str(e)}")

    async def save_credentials(self, request: Request, credentials: CredentialsInput) -> dict:
        """Save database credentials after testing connection"""
        sqlite_db = get_runtime(request.app).sqlite_db

        test_result = await self.test_connection(credentials)

        if not test_result.success:
            raise HTTPException(status_code=400, detail=f"Cannot save credentials: {test_result.message}")

        sqlite_db.creds.save_credentials(
            server=credentials.server, vendor_database=credentials.vendor_database,
            username=credentials.username, password=credentials.password
        )

        return {"success": True, "message": "Credentials saved successfully", "next_step": "Call /api/config/initialize-database"}

    async def initialize_database(self, request: Request) -> dict:
        """Initialize the normalized BeadWatch database on SQL Server.

        After successful initialization, re-initializes SQL connections so
        the dashboard becomes available immediately (no restart required).
        """
        sqlite_db = get_runtime(request.app).sqlite_db

        if not sqlite_db.creds.is_initialized():
            raise HTTPException(status_code=400, detail="Credentials not configured")

        # Idempotent: if schema was already applied, still (re)initialize
        # runtime connections so the dashboard becomes available immediately.
        if sqlite_db.app_state.get_state('normalized_db_initialized') == 'true':
            from services.startup import initialize_sql_connections
            initialize_sql_connections(request.app)
            return {"success": True, "message": f"Database '{NORMALIZED_DB_NAME}' already initialized"}

        # Validate database name to prevent SQL injection (names can't be parameterized)
        if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', NORMALIZED_DB_NAME):
            raise HTTPException(status_code=500, detail=f"Invalid database name: {NORMALIZED_DB_NAME}")

        creds = sqlite_db.creds.get_credentials()

        try:
            # Connect to master database for CREATE DATABASE
            master_conn = SQLServerConnection(
                server=creds['server'], database='master',
                username=creds['username'], password=creds['password']
            )

            # Pre-check: verify the login has permission to create databases
            # (avoids a confusing error after credentials were saved successfully)
            if not self.query_service.has_create_database_permission(master_conn):
                raise HTTPException(
                    status_code=403,
                    detail=f"SQL login '{creds['username']}' lacks CREATE DATABASE permission on this server"
                )

            # Check if database exists (parameterized query for the name value)
            if not self.query_service.database_exists(master_conn, NORMALIZED_DB_NAME):
                # Database names cannot be parameterized in DDL; validated above
                master_conn.execute_non_query_autocommit(f"CREATE DATABASE [{NORMALIZED_DB_NAME}]")

            # Connect to BeadWatch database and run schema init script.
            # execute_script() handles GO-batch splitting internally.
            normalized_conn = SQLServerConnection(
                server=creds['server'], database=NORMALIZED_DB_NAME,
                username=creds['username'], password=creds['password']
            )

            init_script = sqlite_db.scripts.get_init_script('create_normalized_db')
            if init_script:
                normalized_conn.execute_script(init_script)

            sqlite_db.app_state.set_state('normalized_db_initialized', 'true')
            sqlite_db.app_state.set_state('normalized_db_version', '1.0.0')

            # Re-initialize SQL connections and polling so dashboard works immediately
            from services.startup import initialize_sql_connections
            initialize_sql_connections(request.app)

            return {"success": True, "message": f"Database '{NORMALIZED_DB_NAME}' initialized successfully"}

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Database initialization failed: {str(e)}")

    async def rebuild_metrics(self, request: Request, limit: int = None) -> dict:
        """Recompute metrics for existing processed records after logic changes."""
        polling_service = get_runtime(request.app).polling_service
        if not polling_service:
            raise HTTPException(status_code=503, detail="Polling service not initialized")

        try:
            result = polling_service.rebuild_metrics_for_existing_records(limit=limit)
            return {"success": True, **result}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Metrics rebuild failed: {str(e)}")

    async def get_polling_interval(self, request: Request) -> dict:
        """Return the current polling interval in minutes."""
        sqlite_db = get_runtime(request.app).sqlite_db
        stored = sqlite_db.app_state.get_state(POLL_INTERVAL_STATE_KEY)
        interval = int(stored) if stored else POLL_INTERVAL_MINUTES
        return {
            "interval_minutes": interval,
            "min": POLL_INTERVAL_MIN,
            "max": POLL_INTERVAL_MAX,
        }

    async def set_polling_interval(self, request: Request, body: PollingIntervalInput) -> dict:
        """Save a new polling interval and reschedule the background job."""
        rt = get_runtime(request.app)
        rt.sqlite_db.app_state.set_state(POLL_INTERVAL_STATE_KEY, str(body.interval_minutes))

        scheduler = rt.scheduler
        if scheduler and scheduler.running:
            scheduler.reschedule_job(
                "polling_job",
                trigger="interval",
                minutes=body.interval_minutes,
            )

        return {"success": True, "interval_minutes": body.interval_minutes}

    # ── QC Sample Definitions ───────────────────────────────────

    async def get_qc_sample_definitions(self, request: Request) -> List[QCSampleDefinition]:
        """List all QC sample definitions."""
        sqlite_db = get_runtime(request.app).sqlite_db
        rows = sqlite_db.qc.get_qc_sample_definitions()
        return [QCSampleDefinition(**row) for row in rows]

    async def add_qc_sample_definition(self, request: Request, body: QCSampleDefinitionInput) -> dict:
        """Add a QC sample definition."""
        sqlite_db = get_runtime(request.app).sqlite_db
        new_id = sqlite_db.qc.add_qc_sample_definition(
            pattern=body.pattern, match_type=body.match_type,
            role=body.role, label=body.label
        )
        # Clear QC sample cache — matching logic changed
        polling_service = get_runtime(request.app).polling_service
        if polling_service:
            try:
                polling_service.rebuild_qc_cache()
            except Exception:
                logger.warning("QC cache rebuild failed after adding definition", exc_info=True)
        return {"success": True, "id": new_id}

    async def delete_qc_sample_definition(self, request: Request, definition_id: int) -> dict:
        """Delete a QC sample definition."""
        sqlite_db = get_runtime(request.app).sqlite_db
        deleted = sqlite_db.qc.delete_qc_sample_definition(definition_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Definition not found")
        # Clear QC sample cache — matching logic changed
        polling_service = get_runtime(request.app).polling_service
        if polling_service:
            try:
                polling_service.rebuild_qc_cache()
            except Exception:
                logger.warning("QC cache rebuild failed after deleting definition", exc_info=True)
        return {"success": True}

    # ── QC Tracked Beads ────────────────────────────────────────

    async def get_qc_tracked_beads(
        self,
        request: Request,
        catalog_group: Optional[str] = Query(default=None),
        bead_lot: Optional[str] = Query(default=None),
    ) -> List[QCTrackedBead]:
        """List tracked beads, optionally filtered."""
        sqlite_db = get_runtime(request.app).sqlite_db
        rows = sqlite_db.qc.get_qc_tracked_beads(catalog_group=catalog_group, bead_lot=bead_lot)
        return [QCTrackedBead(**row) for row in rows]

    async def add_qc_tracked_bead(self, request: Request, body: QCTrackedBeadInput) -> dict:
        """Add a tracked bead."""
        sqlite_db = get_runtime(request.app).sqlite_db
        new_id = sqlite_db.qc.add_qc_tracked_bead(
            catalog_group=body.catalog_group, bead_lot=body.bead_lot,
            bead_id=body.bead_id, label=body.label
        )
        return {"success": True, "id": new_id}

    async def delete_qc_tracked_bead(self, request: Request, bead_id: int) -> dict:
        """Delete a tracked bead."""
        sqlite_db = get_runtime(request.app).sqlite_db
        deleted = sqlite_db.qc.delete_qc_tracked_bead(bead_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Tracked bead not found")
        return {"success": True}

    async def delete_qc_tracked_beads_by_group(self, request: Request, catalog_group: str, bead_lot: str) -> dict:
        """Bulk-delete all tracked beads for a catalog group + lot."""
        sqlite_db = get_runtime(request.app).sqlite_db
        count = sqlite_db.qc.delete_qc_tracked_beads_by_group(catalog_group, bead_lot)
        if count == 0:
            raise HTTPException(status_code=404, detail="No tracked beads found for this group/lot")
        return {"success": True, "deleted": count}

    # ── Excluded Instruments ─────────────────────────────────────

    async def get_excluded_instruments(self, request: Request) -> List[ExcludedInstrument]:
        """List all excluded instrument serial numbers."""
        sqlite_db = get_runtime(request.app).sqlite_db
        rows = sqlite_db.instruments.get_excluded_instruments()
        return [ExcludedInstrument(**row) for row in rows]

    async def add_excluded_instrument(self, request: Request, body: ExcludedInstrumentInput) -> dict:
        """Add an excluded instrument serial number."""
        sqlite_db = get_runtime(request.app).sqlite_db
        new_id = sqlite_db.instruments.add_excluded_instrument(
            serial_number=body.serial_number, label=body.label
        )
        return {"success": True, "id": new_id}

    async def delete_excluded_instrument(self, request: Request, instrument_id: int) -> dict:
        """Delete an excluded instrument."""
        sqlite_db = get_runtime(request.app).sqlite_db
        deleted = sqlite_db.instruments.delete_excluded_instrument(instrument_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Excluded instrument not found")
        return {"success": True}

    # ── Instrument Aliases ────────────────────────────────────

    async def get_instrument_aliases(self, request: Request) -> List[InstrumentAlias]:
        """List all instrument aliases."""
        sqlite_db = get_runtime(request.app).sqlite_db
        rows = sqlite_db.instruments.get_instrument_aliases()
        return [InstrumentAlias(**row) for row in rows]

    async def add_instrument_alias(self, request: Request, body: InstrumentAliasInput) -> dict:
        """Add an instrument alias."""
        sqlite_db = get_runtime(request.app).sqlite_db
        new_id = sqlite_db.instruments.add_instrument_alias(
            serial_number=body.serial_number, display_name=body.display_name
        )
        return {"success": True, "id": new_id}

    async def delete_instrument_alias(self, request: Request, alias_id: int) -> dict:
        """Delete an instrument alias."""
        sqlite_db = get_runtime(request.app).sqlite_db
        deleted = sqlite_db.instruments.delete_instrument_alias(alias_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Instrument alias not found")
        return {"success": True}

    # ── QC Sample Cache ────────────────────────────────────────

    async def refresh_qc_cache(self, request: Request) -> dict:
        """Clear QC sample cache — will repopulate on next view."""
        polling_service = get_runtime(request.app).polling_service
        if not polling_service:
            raise HTTPException(status_code=503, detail="Polling service not initialized")
        polling_service.rebuild_qc_cache()
        return {"success": True, "message": "QC sample cache cleared — will repopulate on next view"}

    # ── Instrument Cache ──────────────────────────────────────

    async def refresh_instrument_cache(self, request: Request) -> dict:
        """Clear instrument cache — will repopulate on next view."""
        polling_service = get_runtime(request.app).polling_service
        if not polling_service:
            raise HTTPException(status_code=503, detail="Polling service not initialized")
        polling_service.rebuild_instrument_cache()
        return {"success": True, "message": "Instrument cache cleared — will repopulate on next view"}

    # ── Excluded Operators ────────────────────────────────────

    async def get_excluded_operators(self, request: Request) -> List[ExcludedOperator]:
        """List all excluded operator usernames."""
        sqlite_db = get_runtime(request.app).sqlite_db
        rows = sqlite_db.operators.get_excluded_operators()
        return [ExcludedOperator(**row) for row in rows]

    async def add_excluded_operator(self, request: Request, body: ExcludedOperatorInput) -> dict:
        """Add an excluded operator username."""
        sqlite_db = get_runtime(request.app).sqlite_db
        new_id = sqlite_db.operators.add_excluded_operator(
            username=body.username, label=body.label
        )
        return {"success": True, "id": new_id}

    async def delete_excluded_operator(self, request: Request, operator_id: int) -> dict:
        """Delete an excluded operator."""
        sqlite_db = get_runtime(request.app).sqlite_db
        deleted = sqlite_db.operators.delete_excluded_operator(operator_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Excluded operator not found")
        return {"success": True}

    # ── Operator Aliases ─────────────────────────────────────

    async def get_operator_aliases(self, request: Request) -> List[OperatorAlias]:
        """List all operator aliases."""
        sqlite_db = get_runtime(request.app).sqlite_db
        rows = sqlite_db.operators.get_operator_aliases()
        return [OperatorAlias(**row) for row in rows]

    async def add_operator_alias(self, request: Request, body: OperatorAliasInput) -> dict:
        """Add an operator alias."""
        sqlite_db = get_runtime(request.app).sqlite_db
        new_id = sqlite_db.operators.add_operator_alias(
            username=body.username, display_name=body.display_name
        )
        return {"success": True, "id": new_id}

    async def delete_operator_alias(self, request: Request, alias_id: int) -> dict:
        """Delete an operator alias."""
        sqlite_db = get_runtime(request.app).sqlite_db
        deleted = sqlite_db.operators.delete_operator_alias(alias_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Operator alias not found")
        return {"success": True}

    # ── Operator Cache ───────────────────────────────────────

    async def refresh_operator_cache(self, request: Request) -> dict:
        """Clear operator cache — will repopulate on next view."""
        polling_service = get_runtime(request.app).polling_service
        if not polling_service:
            raise HTTPException(status_code=503, detail="Polling service not initialized")
        polling_service.rebuild_operator_cache()
        return {"success": True, "message": "Operator cache cleared — will repopulate on next view"}

    # ── Catalog Group Preferences ─────────────────────────────

    async def get_catalog_group_prefs(self, request: Request) -> list:
        """Return saved catalog group visibility/order prefs."""
        sqlite_db = get_runtime(request.app).sqlite_db
        raw = sqlite_db.app_state.get_state('catalog_group_prefs')
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    async def put_catalog_group_prefs(self, request: Request) -> dict:
        """Save catalog group visibility/order prefs."""
        sqlite_db = get_runtime(request.app).sqlite_db
        body = await request.json()
        if not isinstance(body, list):
            raise HTTPException(status_code=400, detail="Expected a JSON array")
        sqlite_db.app_state.set_state('catalog_group_prefs', json.dumps(body))
        return {"success": True}

    # ── Bead Lot Preferences ─────────────────────────────────

    async def get_bead_lot_prefs(self, request: Request) -> dict:
        """Return saved bead lot visibility/order prefs keyed by catalog group."""
        sqlite_db = get_runtime(request.app).sqlite_db
        raw = sqlite_db.app_state.get_state('bead_lot_prefs')
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    async def put_bead_lot_prefs(self, request: Request) -> dict:
        """Save bead lot visibility/order prefs."""
        sqlite_db = get_runtime(request.app).sqlite_db
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Expected a JSON object")
        sqlite_db.app_state.set_state('bead_lot_prefs', json.dumps(body))
        return {"success": True}

    # ── QC Alert Thresholds ─────────────────────────────────────

    def _get_normalized_db(self, request: Request):
        """Retrieve normalized DB handle from app state; raise 503 if not ready."""
        normalized_db = get_runtime(request.app).normalized_db
        if not normalized_db:
            raise HTTPException(status_code=503, detail="Normalized database not initialized")
        return normalized_db

    async def get_thresholds(self, request: Request) -> List[QCThreshold]:
        """List all QC alert thresholds from the normalized DB."""
        normalized_db = self._get_normalized_db(request)
        return self.query_service.get_thresholds(normalized_db)

    async def update_threshold(self, request: Request, threshold_id: int, body: QCThresholdUpdateInput) -> dict:
        """Update a single threshold row.

        Pydantic's model_validator on QCThresholdUpdateInput enforces per-side
        ordering (lc <= lw, uw <= uc) and cross-side only when all four are
        present. Invalid payloads get a 422 automatically.
        """
        normalized_db = self._get_normalized_db(request)
        try:
            return self.query_service.update_threshold(normalized_db, threshold_id=threshold_id, body=body)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── Settings Export / Import ──────────────────────────────────

    async def export_settings(self, request: Request) -> dict:
        """Export all user-configurable settings as JSON."""
        sqlite_db = get_runtime(request.app).sqlite_db
        data = sqlite_db.settings.export_all_settings()

        data['beadwatch_settings_version'] = 1
        data['exported_at'] = datetime.now().isoformat()

        # Include QCThresholds from normalized DB if available
        normalized_db = get_runtime(request.app).normalized_db
        if normalized_db:
            try:
                data['qc_thresholds'] = self.query_service.get_threshold_export_rows(normalized_db)
            except Exception:
                logger.warning("Could not export QCThresholds from normalized DB", exc_info=True)

        return data

    async def import_settings(self, request: Request) -> dict:
        """Import settings from JSON. Two-phase: validate everything, then apply."""
        sqlite_db = get_runtime(request.app).sqlite_db
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")

        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Expected a JSON object")

        # Version check — must be an integer >= 1 (not bool, not string)
        version = body.get('beadwatch_settings_version')
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise HTTPException(status_code=400, detail="beadwatch_settings_version must be an integer >= 1")

        errors = []
        warnings = []

        # ── Phase 1: Validate everything before mutating ────────────

        # 1a. Validate polling_interval_minutes
        if 'polling_interval_minutes' in body:
            pi = body['polling_interval_minutes']
            if isinstance(pi, bool) or not isinstance(pi, int) or pi < POLL_INTERVAL_MIN or pi > POLL_INTERVAL_MAX:
                errors.append(
                    f"polling_interval_minutes must be an integer between "
                    f"{POLL_INTERVAL_MIN} and {POLL_INTERVAL_MAX}"
                )

        # 1b. Validate SQLite table data (duplicates, roles, types)
        try:
            sqlite_db.settings.validate_import_data(body)
        except ValueError as e:
            errors.append(str(e))

        # 1c. Validate qc_thresholds structure and ordering
        threshold_warnings = []
        validated_thresholds = []  # (metric_name, threshold_input) pairs
        if 'qc_thresholds' in body:
            if not isinstance(body['qc_thresholds'], list):
                errors.append("qc_thresholds must be a list")
            else:
                for i, t in enumerate(body['qc_thresholds']):
                    if not isinstance(t, dict):
                        errors.append(f"qc_thresholds[{i}] must be an object")
                        continue
                    if not t.get('metric_name') or not isinstance(t.get('metric_name'), str):
                        errors.append(f"qc_thresholds[{i}] must have a string metric_name")
                        continue
                    # Validate ordering via the same Pydantic model as the update endpoint
                    try:
                        validated = QCThresholdUpdateInput(
                            upper_warning=t.get('upper_warning'),
                            upper_critical=t.get('upper_critical'),
                            lower_warning=t.get('lower_warning'),
                            lower_critical=t.get('lower_critical'),
                            enabled=t.get('enabled', True),
                        )
                        validated_thresholds.append((t['metric_name'], validated))
                    except Exception as e:
                        errors.append(f"qc_thresholds[{i}] ({t['metric_name']}): {e}")

                # Check which metric_names exist in normalized DB
                if not errors:
                    normalized_db = get_runtime(request.app).normalized_db
                    if normalized_db:
                        try:
                            known_metrics = self.query_service.get_known_threshold_metrics(normalized_db)
                            for metric_name, _ in validated_thresholds:
                                if metric_name not in known_metrics:
                                    threshold_warnings.append(
                                        f"QCThreshold metric '{metric_name}' not found in database, skipped"
                                    )
                        except Exception as e:
                            errors.append(f"Failed to query existing thresholds: {e}")
                    else:
                        threshold_warnings.append("Normalized database not available, QCThresholds skipped")

        if errors:
            raise HTTPException(status_code=400, detail="; ".join(errors))

        warnings.extend(threshold_warnings)

        # ── Phase 2: Apply changes ──────────────────────────────────

        # 2a. SQLite tables + polling_interval_minutes
        try:
            sqlite_imported = sqlite_db.settings.import_all_settings(body)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        imported = dict(sqlite_imported)
        all_sections = [
            'qc_sample_definitions', 'qc_tracked_beads', 'excluded_instruments',
            'instrument_aliases', 'excluded_operators', 'operator_aliases',
            'polling_interval_minutes',
        ]
        skipped = [s for s in all_sections if s not in imported]

        # 2b. QCThresholds (merge-update)
        # Note: SQLite and SQL Server cannot share a transaction. Per spec,
        # SQLite settings are the primary payload; threshold updates are
        # supplementary. If a threshold UPDATE fails after SQLite commit,
        # it's reported as a warning, not rolled back.
        if 'qc_thresholds' in body and validated_thresholds:
            normalized_db = get_runtime(request.app).normalized_db
            if normalized_db:
                try:
                    known_metrics = self.query_service.get_known_threshold_metrics(normalized_db)
                except Exception as e:
                    warnings.append(f"Could not query QCThresholds, thresholds skipped: {e}")
                    known_metrics = None

                if known_metrics is None:
                    imported['qc_thresholds'] = 0
                else:
                    threshold_count = 0
                    for metric_name, validated in validated_thresholds:
                        if metric_name not in known_metrics:
                            continue
                        try:
                            self.query_service.update_threshold_by_metric_name(
                                normalized_db, metric_name, validated
                            )
                            threshold_count += 1
                        except Exception as e:
                            warnings.append(f"QCThreshold '{metric_name}' update failed: {e}")
                    imported['qc_thresholds'] = threshold_count
            else:
                skipped.append('qc_thresholds')
        elif 'qc_thresholds' not in body:
            skipped.append('qc_thresholds')
        else:
            # qc_thresholds present but no valid entries (all had unknown metrics)
            imported['qc_thresholds'] = 0
            if not any('QCThresholds skipped' in w for w in warnings):
                skipped.append('qc_thresholds')

        # Rebuild caches
        polling_service = get_runtime(request.app).polling_service
        if polling_service:
            try:
                polling_service.rebuild_qc_cache()
            except Exception:
                pass
            try:
                polling_service.rebuild_instrument_cache()
            except Exception:
                pass
            try:
                polling_service.rebuild_operator_cache()
            except Exception:
                pass

        return {
            "success": True,
            "imported": imported,
            "skipped": skipped,
            "warnings": warnings,
        }
