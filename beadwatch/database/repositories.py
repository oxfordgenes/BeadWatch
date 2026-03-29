from datetime import datetime
from typing import Dict, List, Optional


class CredentialsRepository:
    def __init__(self, get_connection, encrypt_password, decrypt_password, normalized_db_name: str):
        self._get_connection = get_connection
        self._encrypt_password = encrypt_password
        self._decrypt_password = decrypt_password
        self._normalized_db_name = normalized_db_name

    def is_initialized(self) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM credentials")
            count = cursor.fetchone()[0]
            return count > 0

    def save_credentials(self, server: str, vendor_database: str, username: str, password: str) -> None:
        encrypted_pwd = self._encrypt_password(password)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO credentials
                (id, server_address, vendor_database, normalized_database, username, encrypted_password)
                VALUES (1, ?, ?, ?, ?, ?)
                """,
                (server, vendor_database, self._normalized_db_name, username, encrypted_pwd),
            )
            conn.commit()

    def get_credentials(self) -> Optional[Dict[str, str]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT server_address, vendor_database, normalized_database,
                       username, encrypted_password, created_at
                FROM credentials
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "server": row["server_address"],
                "vendor_database": row["vendor_database"],
                "normalized_database": row["normalized_database"],
                "username": row["username"],
                "password": self._decrypt_password(row["encrypted_password"]),
                "created_at": row["created_at"],
            }


class AppStateRepository:
    def __init__(self, get_connection):
        self._get_connection = get_connection

    def get_state(self, key: str) -> Optional[str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM app_state WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            cursor.execute(
                """
                INSERT INTO app_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?
                """,
                (key, value, now, value, now),
            )
            conn.commit()


class ScriptRepository:
    def __init__(self, get_connection):
        self._get_connection = get_connection

    def get_init_script(self, script_name: str) -> Optional[str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT script_content FROM init_scripts WHERE script_name = ?", (script_name,))
            row = cursor.fetchone()
            return row["script_content"] if row else None


class QCSettingsRepository:
    def __init__(self, get_connection):
        self._get_connection = get_connection

    def get_qc_sample_definitions(self) -> List[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, pattern, match_type, role, label, created_at "
                "FROM qc_sample_definitions ORDER BY id"
            )
            return [dict(row) for row in cursor.fetchall()]

    def add_qc_sample_definition(self, pattern: str, match_type: str, role: str, label: Optional[str] = None) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO qc_sample_definitions (pattern, match_type, role, label) VALUES (?, ?, ?, ?)",
                (pattern, match_type, role, label),
            )
            self._mark_cache_stale(cursor)
            conn.commit()
            return cursor.lastrowid

    def delete_qc_sample_definition(self, definition_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM qc_sample_definitions WHERE id = ?", (definition_id,))
            deleted = cursor.rowcount > 0
            if deleted:
                self._mark_cache_stale(cursor)
            conn.commit()
            return deleted

    def _mark_cache_stale(self, cursor) -> None:
        now = datetime.now().isoformat()
        cursor.execute(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?",
            ("qc_cache_stale", "true", now, "true", now),
        )

    def get_qc_tracked_beads(self, catalog_group: Optional[str] = None, bead_lot: Optional[str] = None) -> List[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT id, catalog_group, bead_lot, bead_id, label, created_at FROM qc_tracked_beads"
            params: list = []
            clauses: list = []
            if catalog_group:
                clauses.append("catalog_group = ?")
                params.append(catalog_group)
            if bead_lot:
                clauses.append("bead_lot = ?")
                params.append(bead_lot)
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY id"
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def add_qc_tracked_bead(self, catalog_group: str, bead_lot: str, bead_id: str, label: Optional[str] = None) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO qc_tracked_beads (catalog_group, bead_lot, bead_id, label) VALUES (?, ?, ?, ?)",
                (catalog_group, bead_lot, bead_id, label),
            )
            conn.commit()
            return cursor.lastrowid

    def delete_qc_tracked_bead(self, bead_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM qc_tracked_beads WHERE id = ?", (bead_id,))
            conn.commit()
            return cursor.rowcount > 0

    def delete_qc_tracked_beads_by_group(self, catalog_group: str, bead_lot: str) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM qc_tracked_beads WHERE catalog_group = ? AND bead_lot = ?",
                (catalog_group, bead_lot),
            )
            conn.commit()
            return cursor.rowcount


class InstrumentSettingsRepository:
    def __init__(self, get_connection):
        self._get_connection = get_connection

    def get_excluded_instruments(self) -> List[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, serial_number, label, created_at FROM excluded_instruments ORDER BY id")
            return [dict(row) for row in cursor.fetchall()]

    def add_excluded_instrument(self, serial_number: str, label: Optional[str] = None) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO excluded_instruments (serial_number, label) VALUES (?, ?)",
                (serial_number, label),
            )
            conn.commit()
            return cursor.lastrowid

    def delete_excluded_instrument(self, instrument_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM excluded_instruments WHERE id = ?", (instrument_id,))
            conn.commit()
            return cursor.rowcount > 0

    def get_instrument_aliases(self) -> List[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, serial_number, display_name, created_at FROM instrument_aliases ORDER BY id")
            return [dict(row) for row in cursor.fetchall()]

    def add_instrument_alias(self, serial_number: str, display_name: str) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO instrument_aliases (serial_number, display_name) VALUES (?, ?)",
                (serial_number, display_name),
            )
            conn.commit()
            return cursor.lastrowid

    def delete_instrument_alias(self, alias_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM instrument_aliases WHERE id = ?", (alias_id,))
            conn.commit()
            return cursor.rowcount > 0


class OperatorSettingsRepository:
    def __init__(self, get_connection):
        self._get_connection = get_connection

    def get_excluded_operators(self) -> List[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, label, created_at FROM excluded_operators ORDER BY id")
            rows = [dict(row) for row in cursor.fetchall()]
            for row in rows:
                row["username"] = row["username"].strip().upper()
            return rows

    def add_excluded_operator(self, username: str, label: Optional[str] = None) -> int:
        username = username.strip().upper()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO excluded_operators (username, label) VALUES (?, ?)", (username, label))
            conn.commit()
            return cursor.lastrowid

    def delete_excluded_operator(self, operator_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM excluded_operators WHERE id = ?", (operator_id,))
            conn.commit()
            return cursor.rowcount > 0

    def get_operator_aliases(self) -> List[Dict]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, display_name, created_at FROM operator_aliases ORDER BY id")
            rows = [dict(row) for row in cursor.fetchall()]
            for row in rows:
                row["username"] = row["username"].strip().upper()
            return rows

    def add_operator_alias(self, username: str, display_name: str) -> int:
        username = username.strip().upper()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO operator_aliases (username, display_name) VALUES (?, ?)", (username, display_name))
            conn.commit()
            return cursor.lastrowid

    def delete_operator_alias(self, alias_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM operator_aliases WHERE id = ?", (alias_id,))
            conn.commit()
            return cursor.rowcount > 0


class SettingsImportExportService:
    def __init__(self, get_connection, poll_interval_state_key: str, exportable_tables: Dict[str, List[str]], unique_keys: Dict[str, List[str]]):
        self._get_connection = get_connection
        self._poll_interval_state_key = poll_interval_state_key
        self._exportable_tables = exportable_tables
        self._unique_keys = unique_keys

    def export_all_settings(self) -> Dict:
        data = {}
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for table, columns in self._exportable_tables.items():
                cols = ", ".join(columns)
                cursor.execute(f"SELECT {cols} FROM {table} ORDER BY id")
                data[table] = [dict(row) for row in cursor.fetchall()]

            cursor.execute("SELECT value FROM app_state WHERE key = ?", (self._poll_interval_state_key,))
            row = cursor.fetchone()
            if row is not None:
                data["polling_interval_minutes"] = int(row["value"])
        return data

    def validate_import_data(self, data: Dict) -> None:
        for table, columns in self._exportable_tables.items():
            if table not in data:
                continue
            rows = data[table]
            if not isinstance(rows, list):
                raise ValueError(f"'{table}' must be a list")
            for i, row in enumerate(rows):
                if not isinstance(row, dict):
                    raise ValueError(f"'{table}[{i}]' must be an object, got {type(row).__name__}")

            if table == "qc_sample_definitions":
                valid_roles = {"positive", "negative"}
                for i, row in enumerate(rows):
                    role = row.get("role")
                    if role not in valid_roles:
                        raise ValueError(f"Invalid role '{role}' in qc_sample_definitions[{i}]")

            unique_keys = self._unique_keys.get(table)
            if unique_keys:
                seen = set()
                for row in rows:
                    key_vals = tuple(row.get(k) for k in unique_keys)
                    if table in ("excluded_operators", "operator_aliases"):
                        key_vals = tuple(v.strip().upper() if isinstance(v, str) else v for v in key_vals)
                    if key_vals in seen:
                        raise ValueError(f"Duplicate {unique_keys} in {table}: {key_vals}")
                    seen.add(key_vals)

    def import_all_settings(self, data: Dict) -> Dict:
        imported = {}
        with self._get_connection() as conn:
            cursor = conn.cursor()
            try:
                for table, columns in self._exportable_tables.items():
                    if table not in data:
                        continue
                    rows = data[table]
                    cursor.execute(f"DELETE FROM {table}")
                    for row in rows:
                        if table in ("excluded_operators", "operator_aliases") and "username" in row:
                            row = dict(row)
                            row["username"] = row["username"].strip().upper()

                        placeholders = ", ".join("?" for _ in columns)
                        cols = ", ".join(columns)
                        values = tuple(row.get(c) for c in columns)
                        cursor.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", values)
                    imported[table] = len(rows)

                if "polling_interval_minutes" in data:
                    val = data["polling_interval_minutes"]
                    now = datetime.now().isoformat()
                    cursor.execute(
                        "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?",
                        (self._poll_interval_state_key, str(val), now, str(val), now),
                    )
                    imported["polling_interval_minutes"] = True

                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return imported
