import logging
from fastapi.responses import RedirectResponse

from config.settings import __version__
from database.sqlite_handler import SQLiteHandler
from services.runtime_services import get_runtime, set_runtime_field
from services.startup import initialize_sql_connections

logger = logging.getLogger("beadwatch")


class ApplicationBootstrap:
    """Centralized startup/shutdown orchestration for app runtime services."""

    def startup(self, app) -> None:
        logger.info("Starting BeadWatch v%s", __version__)
        runtime = get_runtime(app)

        set_runtime_field(app, "vendor_db", None)
        set_runtime_field(app, "normalized_db", None)
        set_runtime_field(app, "polling_service", None)
        set_runtime_field(app, "scheduler", None)

        if runtime.sqlite_db is None:
            sqlite_db = SQLiteHandler()
            sqlite_db.initialize()
            set_runtime_field(app, "sqlite_db", sqlite_db)
        else:
            set_runtime_field(app, "sqlite_db", runtime.sqlite_db)

        initialize_sql_connections(app)

    def shutdown(self, app) -> None:
        runtime = get_runtime(app)
        if runtime.scheduler and runtime.scheduler.running:
            runtime.scheduler.shutdown()
            logger.info("Scheduler shut down")
        logger.info("BeadWatch stopped")

    def require_setup_redirect(self, app):
        runtime = get_runtime(app)
        sqlite_db = runtime.sqlite_db
        if not sqlite_db or not sqlite_db.creds.is_initialized():
            return RedirectResponse(url="/setup")
        if sqlite_db.app_state.get_state("normalized_db_initialized") != "true":
            return RedirectResponse(url="/setup")
        return None
