from unittest.mock import MagicMock, patch

from fastapi import FastAPI

from services.startup import initialize_sql_connections


def test_initialize_sql_connections_reinitialization_replaces_scheduler_without_duplicate_jobs():
    app = FastAPI()
    sqlite_db = MagicMock()
    sqlite_db.creds.is_initialized.return_value = True
    sqlite_db.creds.get_credentials.return_value = {
        "server": "localhost",
        "vendor_database": "vendor_db",
        "normalized_database": "norm_db",
        "username": "user",
        "password": "pass",
    }
    sqlite_db.app_state.get_state.return_value = "15"
    sqlite_db.scripts.get_init_script.return_value = None
    app.state.sqlite_db = sqlite_db
    app.state.scheduler = None

    scheduler_1 = MagicMock()
    scheduler_1.running = True
    scheduler_2 = MagicMock()
    scheduler_2.running = True

    polling_1 = MagicMock()
    polling_2 = MagicMock()

    with patch("services.startup.SQLServerConnection", side_effect=[MagicMock(), MagicMock(), MagicMock(), MagicMock()]), \
         patch("services.startup.FusionAdapter", return_value=MagicMock()), \
         patch("services.startup.PollingService", side_effect=[polling_1, polling_2]), \
         patch("services.startup.BackgroundScheduler", side_effect=[scheduler_1, scheduler_2]), \
         patch("services.startup.threading.Thread") as mock_thread:
        initialize_sql_connections(app)
        initialize_sql_connections(app)

    # First scheduler is shut down during re-initialization.
    scheduler_1.shutdown.assert_called_once_with(wait=False)

    # Each scheduler gets one polling job and is started exactly once.
    scheduler_1.add_job.assert_called_once()
    scheduler_2.add_job.assert_called_once()
    scheduler_1.start.assert_called_once()
    scheduler_2.start.assert_called_once()

    # New scheduler replaces old scheduler on app.state.
    assert app.state.scheduler is scheduler_2

    # Initial-poll bootstrap thread is started once per initialization.
    assert mock_thread.call_count == 2
