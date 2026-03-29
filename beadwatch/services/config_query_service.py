from typing import List

from models.config_models import QCThreshold, QCThresholdUpdateInput


class ConfigQueryService:
    """Query-focused service for config operations against normalized DB."""

    def get_thresholds(self, normalized_db) -> List[QCThreshold]:
        rows = normalized_db.execute_query(
            "SELECT id, metric_name, upper_warning, upper_critical, lower_warning, lower_critical, enabled FROM QCThresholds ORDER BY id"
        )
        return [QCThreshold(**row) for row in rows]

    def update_threshold(self, normalized_db, threshold_id: int, body: QCThresholdUpdateInput) -> dict:
        affected = normalized_db.execute_non_query(
            """UPDATE QCThresholds
               SET upper_warning = ?, upper_critical = ?, lower_warning = ?, lower_critical = ?,
                   enabled = ?, updated_at = GETDATE()
               WHERE id = ?""",
            (
                body.upper_warning,
                body.upper_critical,
                body.lower_warning,
                body.lower_critical,
                body.enabled,
                threshold_id,
            ),
        )
        if affected == 0:
            raise LookupError("Threshold not found")

        rows = normalized_db.execute_query(
            "SELECT id, metric_name, upper_warning, upper_critical, lower_warning, lower_critical, enabled FROM QCThresholds WHERE id = ?",
            (threshold_id,),
        )
        if not rows:
            raise RuntimeError("Threshold updated but could not be re-read")
        return {"success": True, "threshold": QCThreshold(**rows[0]).model_dump()}

    def has_create_database_permission(self, master_conn) -> bool:
        rows = master_conn.execute_query(
            "SELECT HAS_PERMS_BY_NAME(NULL, NULL, 'CREATE ANY DATABASE') AS has_perm"
        )
        return bool(rows and rows[0].get("has_perm"))

    def database_exists(self, master_conn, database_name: str) -> bool:
        rows = master_conn.execute_query(
            "SELECT database_id FROM sys.databases WHERE name = ?",
            (database_name,),
        )
        return bool(rows)

    def get_threshold_export_rows(self, normalized_db) -> list[dict]:
        rows = normalized_db.execute_query(
            "SELECT metric_name, upper_warning, upper_critical, "
            "lower_warning, lower_critical, enabled FROM QCThresholds ORDER BY metric_name"
        )
        return [dict(row) for row in rows]

    def get_known_threshold_metrics(self, normalized_db) -> set[str]:
        rows = normalized_db.execute_query("SELECT metric_name FROM QCThresholds")
        return {row["metric_name"] for row in rows}

    def update_threshold_by_metric_name(
        self,
        normalized_db,
        metric_name: str,
        validated: QCThresholdUpdateInput,
    ) -> None:
        normalized_db.execute_non_query(
            """UPDATE QCThresholds
               SET upper_warning = ?, upper_critical = ?,
                   lower_warning = ?, lower_critical = ?,
                   enabled = ?, updated_at = GETDATE()
               WHERE metric_name = ?""",
            (
                validated.upper_warning,
                validated.upper_critical,
                validated.lower_warning,
                validated.lower_critical,
                validated.enabled,
                metric_name,
            ),
        )
