from pathlib import Path
import os
import sys

__version__ = "1.0.0"

# Determine base paths (works for both dev and PyInstaller)
if getattr(sys, 'frozen', False):
    # Bundled resources (frontend, SQL scripts) are extracted here
    BASE_DIR = Path(sys._MEIPASS)
    # Writable data (config DB, logs, encryption key) lives next to the exe
    EXE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent.parent
    EXE_DIR = BASE_DIR

# Data directory (portable by default; fallback to LOCALAPPDATA if EXE_DIR not writable)
APP_DATA_DIR = EXE_DIR
try:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    test_file = APP_DATA_DIR / ".write_test"
    test_file.write_text("ok")
    test_file.unlink(missing_ok=True)
except Exception:
    APP_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", EXE_DIR)) / "BeadWatch"
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Database paths
SQLITE_DB_PATH = APP_DATA_DIR / "config.db"
ENCRYPTION_KEY_PATH = APP_DATA_DIR / ".beadwatch_key"

# Polling configuration
POLL_INTERVAL_MINUTES = 60
POLL_INTERVAL_MIN = 5
POLL_INTERVAL_MAX = 1440
INITIAL_LOOKBACK_HOURS = 24

# Port configuration
PORT_RANGE_START = 8765
PORT_RANGE_END = 9000

# Logging
LOG_FILE_PATH = APP_DATA_DIR / "beadwatch.log"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
LOG_BACKUP_COUNT = 5

# Normalized database name (created on same SQL Server as vendor database)
NORMALIZED_DB_NAME = "BeadWatch"

# ODBC driver name (must be bundled or present on target machine)
ODBC_DRIVER = "ODBC Driver 18 for SQL Server"

# SQL Server connection timeout (seconds)
SQL_CONNECTION_TIMEOUT = 30

# Max time (seconds) to wait for QC cache population in API requests
CACHE_POPULATE_TIMEOUT = 60

# Scheduler health
SCHEDULER_HEARTBEAT_KEY = "scheduler_last_heartbeat"
POLL_INTERVAL_STATE_KEY = "poll_interval_minutes"
