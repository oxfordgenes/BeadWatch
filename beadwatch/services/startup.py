from fastapi import FastAPI
from apscheduler.schedulers.background import BackgroundScheduler
import logging
import threading

from config.settings import POLL_INTERVAL_MINUTES, POLL_INTERVAL_STATE_KEY
from database.sqlite_handler import SQLiteHandler
from database.sqlserver_handler import SQLServerConnection
from services.polling_service import PollingService
from services.runtime_services import get_runtime, set_runtime_field
from services.vendor_adapters.fusion_adapter import FusionAdapter

logger = logging.getLogger('beadwatch')


def initialize_sql_connections(app: FastAPI):
    """Initialize SQL Server connections and polling from stored credentials.

    Called at startup and again after first-run setup completes,
    so the app does not require a restart after configuration.
    """
    runtime = get_runtime(app)
    sqlite_db: SQLiteHandler = runtime.sqlite_db or getattr(app.state, "sqlite_db", None)

    if not sqlite_db.creds.is_initialized():
        return

    try:
        creds = sqlite_db.creds.get_credentials()

        # Vendor database connection
        vendor_db = SQLServerConnection(
            server=creds['server'],
            database=creds['vendor_database'],
            username=creds['username'],
            password=creds['password']
        )
        set_runtime_field(app, "vendor_db", vendor_db)

        # Normalized database connection (same server, different database)
        normalized_db = SQLServerConnection(
            server=creds['server'],
            database=creds['normalized_database'],
            username=creds['username'],
            password=creds['password']
        )
        set_runtime_field(app, "normalized_db", normalized_db)

        # Re-run idempotent schema script to pick up any new tables
        # added since the DB was first created (e.g. QCSampleCache,
        # InstrumentRunCache).  All DDL is guarded with IF NOT EXISTS.
        init_script = sqlite_db.scripts.get_init_script('create_normalized_db')
        if init_script:
            try:
                normalized_db.execute_script(init_script)
                logger.info("Schema script applied to normalized DB")
            except Exception as e:
                logger.warning("Schema script re-run failed (non-fatal): %s", e)

        # Initialize polling service
        vendor_adapter = FusionAdapter(vendor_db)
        polling_service = PollingService(
            sqlite_db=sqlite_db,
            vendor_adapter=vendor_adapter,
            normalized_db=normalized_db,
            vendor_db=vendor_db,
        )
        set_runtime_field(app, "polling_service", polling_service)

        # Determine polling interval (user-configured or default)
        stored_interval = sqlite_db.app_state.get_state(POLL_INTERVAL_STATE_KEY)
        poll_interval = int(stored_interval) if stored_interval else POLL_INTERVAL_MINUTES

        # Start background scheduler (stop existing one first if re-initializing)
        existing_scheduler = runtime.scheduler or getattr(app.state, "scheduler", None)
        if existing_scheduler and existing_scheduler.running:
            existing_scheduler.shutdown(wait=False)

        scheduler = BackgroundScheduler()
        scheduler.add_job(
            polling_service.check_for_new_records,
            'interval',
            minutes=poll_interval,
            id='polling_job',
        )
        scheduler.start()
        set_runtime_field(app, "scheduler", scheduler)
        logger.info("Polling service started (interval=%d min)", poll_interval)

        # Kick off an initial poll so the UI has data and a heartbeat immediately.
        # Also backfill display_name for historical ProcessedRecords.
        def _initial_poll():
            try:
                get_runtime(app).polling_service.backfill_display_names()
            except Exception as e:
                logger.warning("Display name backfill failed (non-fatal): %s", e)
            try:
                get_runtime(app).polling_service.check_for_new_records()
            except Exception as e:
                logger.error(f"Initial poll failed: {e}")

        threading.Thread(target=_initial_poll, daemon=True).start()

    except Exception as e:
        logger.error(f"Failed to initialize database connections: {e}")
        # App can still run for configuration
