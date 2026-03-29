import sqlite3
from pathlib import Path
from contextlib import contextmanager

from config.settings import SQLITE_DB_PATH, NORMALIZED_DB_NAME, POLL_INTERVAL_STATE_KEY
from utils.encryption import encrypt_password, decrypt_password
from database.repositories import (
    AppStateRepository,
    CredentialsRepository,
    InstrumentSettingsRepository,
    OperatorSettingsRepository,
    QCSettingsRepository,
    ScriptRepository,
    SettingsImportExportService,
)


class SQLiteHandler:
    """Bootstrapper / schema manager for the embedded SQLite configuration database.

    Repositories are exposed as typed attributes; call sites access them directly
    instead of going through delegation methods on this class.

    Attributes:
        creds      -- CredentialsRepository
        app_state  -- AppStateRepository
        scripts    -- ScriptRepository
        qc         -- QCSettingsRepository
        instruments -- InstrumentSettingsRepository
        operators  -- OperatorSettingsRepository
        settings   -- SettingsImportExportService
    """

    # Tables that participate in export/import with their exportable columns
    _EXPORTABLE_TABLES = {
        'qc_sample_definitions': ['pattern', 'match_type', 'role', 'label'],
        'qc_tracked_beads': ['catalog_group', 'bead_lot', 'bead_id', 'label'],
        'excluded_instruments': ['serial_number', 'label'],
        'instrument_aliases': ['serial_number', 'display_name'],
        'excluded_operators': ['username', 'label'],
        'operator_aliases': ['username', 'display_name'],
    }

    # Uniqueness constraints for duplicate detection on import
    _UNIQUE_KEYS = {
        'qc_tracked_beads': ['catalog_group', 'bead_lot', 'bead_id'],
        'excluded_instruments': ['serial_number'],
        'instrument_aliases': ['serial_number'],
        'excluded_operators': ['username'],
        'operator_aliases': ['username'],
    }

    def __init__(self, db_path: Path = SQLITE_DB_PATH):
        self.db_path = db_path
        self.creds = CredentialsRepository(self.get_connection, encrypt_password, decrypt_password, NORMALIZED_DB_NAME)
        self.app_state = AppStateRepository(self.get_connection)
        self.scripts = ScriptRepository(self.get_connection)
        self.qc = QCSettingsRepository(self.get_connection)
        self.instruments = InstrumentSettingsRepository(self.get_connection)
        self.operators = OperatorSettingsRepository(self.get_connection)
        self.settings = SettingsImportExportService(
            self.get_connection,
            POLL_INTERVAL_STATE_KEY,
            self._EXPORTABLE_TABLES,
            self._UNIQUE_KEYS,
        )

    @contextmanager
    def get_connection(self):
        """Context manager for database connections"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self):
        """Create database and tables if they don't exist"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Credentials table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS credentials (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    server_address TEXT NOT NULL,
                    vendor_database TEXT NOT NULL,
                    normalized_database TEXT NOT NULL,
                    username TEXT NOT NULL,
                    encrypted_password BLOB NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # App state table (key-value store)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # SQL initialization scripts storage
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS init_scripts (
                    script_name TEXT PRIMARY KEY,
                    script_content TEXT NOT NULL,
                    version TEXT NOT NULL
                )
            """)

            # Note: init scripts must be schema-only for the target database
            # (no CREATE DATABASE / USE statements). "GO" batch separators are allowed.

            # Schema migration tracking
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    description TEXT,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # QC sample definitions (which sample names are QC controls)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS qc_sample_definitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern TEXT NOT NULL,
                    match_type TEXT DEFAULT 'substring',
                    role TEXT NOT NULL,
                    label TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # QC tracked beads (which specific beads to track per lot)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS qc_tracked_beads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    catalog_group TEXT NOT NULL,
                    bead_lot TEXT NOT NULL,
                    bead_id TEXT NOT NULL,
                    label TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(catalog_group, bead_lot, bead_id)
                )
            """)

            # Excluded instruments (serial numbers to hide from instrument comparison)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS excluded_instruments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    serial_number TEXT NOT NULL UNIQUE,
                    label TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Instrument aliases (friendly display names for serial numbers)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS instrument_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    serial_number TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Excluded operators (usernames to hide from operator comparison)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS excluded_operators (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    label TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Operator aliases (friendly display names for operator usernames)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operator_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.commit()

            # Load default init scripts if not present
            self._load_default_scripts(cursor, conn)

    def _load_default_scripts(self, cursor, conn):
        """Load SQL initialization scripts into SQLite"""
        scripts_dir = Path(__file__).parent / "init_scripts"

        for script_file in scripts_dir.glob("*.sql"):
            script_name = script_file.stem
            script_content = script_file.read_text(encoding="utf-8")

            # Insert or update so the stored copy stays in sync with
            # the file on disk (new tables added to idempotent scripts).
            cursor.execute(
                "SELECT script_content FROM init_scripts WHERE script_name = ?",
                (script_name,)
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    "INSERT INTO init_scripts (script_name, script_content, version) VALUES (?, ?, ?)",
                    (script_name, script_content, "1.0.0")
                )
            elif row['script_content'] != script_content:
                cursor.execute(
                    "UPDATE init_scripts SET script_content = ? WHERE script_name = ?",
                    (script_content, script_name)
                )

        conn.commit()
