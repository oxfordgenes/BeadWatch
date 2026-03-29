import re
import pyodbc
from contextlib import contextmanager
from typing import List, Any, Optional
import logging
from config.settings import ODBC_DRIVER, SQL_CONNECTION_TIMEOUT

logger = logging.getLogger('beadwatch')


class SQLServerConnection:
    """Manages SQL Server database connections using pyodbc"""

    def __init__(self, server: str, database: str, username: str, password: str):
        self.server = server
        self.database = database
        self.username = username
        self.password = password
        self._last_successful_query: Optional[str] = None
        self._last_error: Optional[str] = None

    @contextmanager
    def get_connection(self, autocommit: bool = False):
        """Context manager for database connections"""
        conn = pyodbc.connect(
            f"DRIVER={{{ODBC_DRIVER}}};"
            f"SERVER={self.server};"
            f"DATABASE={self.database};"
            f"UID={self.username};"
            f"PWD={self.password};"
            "TrustServerCertificate=Yes;",
            autocommit=autocommit,
            timeout=SQL_CONNECTION_TIMEOUT,
        )
        try:
            yield conn
        finally:
            conn.close()

    def execute_query(self, query: str, params: tuple = None) -> List[Any]:
        """Execute a query and return results as list of dicts"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params or ())
                columns = [col[0] for col in cursor.description] if cursor.description else []
                results = [dict(zip(columns, row)) for row in cursor.fetchall()]
                self._last_successful_query = query[:100]
                self._last_error = None
                return results
        except Exception as e:
            self._last_error = str(e)
            logger.error(f"Query execution failed: {e}")
            raise

    def execute_non_query(self, query: str, params: tuple = None) -> int:
        """Execute a non-SELECT query and return affected rows"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params or ())
                conn.commit()
                affected = cursor.rowcount
                self._last_successful_query = query[:100]
                self._last_error = None
                return affected
        except Exception as e:
            self._last_error = str(e)
            logger.error(f"Non-query execution failed: {e}")
            raise

    def execute_non_query_autocommit(self, query: str, params: tuple = None) -> int:
        """Execute a DDL statement that cannot run inside a transaction (e.g. CREATE DATABASE)"""
        try:
            with self.get_connection(autocommit=True) as conn:
                cursor = conn.cursor()
                cursor.execute(query, params or ())
                affected = cursor.rowcount
                self._last_successful_query = query[:100]
                self._last_error = None
                return affected
        except Exception as e:
            self._last_error = str(e)
            logger.error(f"Autocommit execution failed: {e}")
            raise

    @contextmanager
    def transaction(self):
        """Context manager for explicit transaction control.

        Usage:
            with db.transaction() as (conn, cursor):
                cursor.execute(...)
                cursor.execute(...)
            # auto-commits on success, rolls back on exception
        """
        conn = pyodbc.connect(
            f"DRIVER={{{ODBC_DRIVER}}};"
            f"SERVER={self.server};"
            f"DATABASE={self.database};"
            f"UID={self.username};"
            f"PWD={self.password};"
            "TrustServerCertificate=Yes;",
            autocommit=False,
            timeout=SQL_CONNECTION_TIMEOUT,
        )
        cursor = conn.cursor()
        try:
            yield conn, cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def execute_script(self, script: str):
        """Execute multi-statement SQL script (split by GO batch separator)"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                # Split by GO on its own line (handles \r\n, trailing whitespace, EOF)
                statements = re.split(
                    r'^\s*GO\s*$', script, flags=re.MULTILINE | re.IGNORECASE
                )

                for statement in statements:
                    statement = statement.strip()
                    if statement:
                        cursor.execute(statement)

                conn.commit()
                self._last_error = None

        except Exception as e:
            self._last_error = str(e)
            logger.error(f"Script execution failed: {e}")
            raise

    def test_connection(self) -> dict:
        """Test database connectivity and return status"""
        try:
            result = self.execute_query("SELECT 1 as test, GETDATE() as server_time")
            return {
                'success': True,
                'message': 'Connection successful',
                'server_time': result[0]['server_time'] if result else None
            }
        except Exception as e:
            return {
                'success': False,
                'message': f'Connection failed: {str(e)}',
                'server_time': None
            }

    def get_status(self) -> dict:
        """Get current connection status"""
        test_result = self.test_connection()
        return {
            'status': 'connected' if test_result['success'] else 'disconnected',
            'last_successful_query': self._last_successful_query,
            'error_message': self._last_error
        }
