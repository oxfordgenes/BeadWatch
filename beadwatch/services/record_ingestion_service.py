import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("beadwatch")


class RecordIngestionService:
    """Handles atomic ingestion of vendor records and threshold alert creation."""

    def process_record(
        self,
        normalized_db,
        metrics_calc,
        vendor_name: str,
        record: Dict[str, Any],
    ) -> Optional[Dict[str, float]]:
        """Process one record atomically and return metrics, or None if duplicate."""
        metrics = metrics_calc.calculate_qc_metrics(record["raw_data"])

        with normalized_db.transaction() as (conn, cursor):
            display_name = record.get("raw_data", {}).get("sample_id")
            cursor.execute(
                """
                INSERT INTO ProcessedRecords
                    (vendor_source, vendor_record_uuid, vendor_record_timestamp, display_name)
                OUTPUT INSERTED.id
                SELECT ?, ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM ProcessedRecords WITH (HOLDLOCK, UPDLOCK)
                    WHERE vendor_source = ? AND vendor_record_uuid = ?
                )
                """,
                (
                    vendor_name,
                    record["uuid"],
                    record["timestamp"],
                    display_name,
                    vendor_name,
                    record["uuid"],
                ),
            )
            row = cursor.fetchone()

            if row is None:
                logger.info("Duplicate record skipped: %s", record.get("uuid"))
                return None

            record_id = row[0]
            for metric_name, metric_value in metrics.items():
                if metric_value is not None:
                    cursor.execute(
                        """
                        INSERT INTO QCMetrics (processed_record_id, metric_name, metric_value)
                        VALUES (?, ?, ?)
                        """,
                        (record_id, metric_name, float(metric_value)),
                    )

            self.check_qc_thresholds(
                cursor,
                record_id,
                metrics,
                vendor_uuid=record.get("uuid"),
                vendor_ts=record.get("timestamp"),
                sample_name=display_name,
            )

        logger.debug("Processed record %s: %d metrics calculated", record["uuid"], len(metrics))
        return metrics

    def check_qc_thresholds(
        self,
        cursor,
        record_id: int,
        metrics: Dict[str, float],
        create_alert_fn: Optional[Callable[..., None]] = None,
        vendor_uuid: Optional[str] = None,
        vendor_ts=None,
        sample_name: Optional[str] = None,
    ) -> None:
        """Check thresholds and create QC alerts using the provided cursor."""
        if create_alert_fn is None:
            create_alert_fn = self.create_alert

        cursor.execute(
            """
            SELECT metric_name, upper_warning, upper_critical, lower_warning, lower_critical
            FROM QCThresholds WHERE enabled = 1
            """
        )
        thresholds = cursor.fetchall()

        for threshold in thresholds:
            name = threshold.metric_name
            if name not in metrics:
                continue

            actual_value = metrics[name]
            if threshold.upper_critical is not None and actual_value > threshold.upper_critical:
                create_alert_fn(
                    cursor,
                    record_id,
                    name,
                    "upper",
                    threshold.upper_critical,
                    actual_value,
                    "critical",
                    vendor_uuid=vendor_uuid,
                    vendor_ts=vendor_ts,
                    sample_name=sample_name,
                )
            elif threshold.upper_warning is not None and actual_value > threshold.upper_warning:
                create_alert_fn(
                    cursor,
                    record_id,
                    name,
                    "upper",
                    threshold.upper_warning,
                    actual_value,
                    "warning",
                    vendor_uuid=vendor_uuid,
                    vendor_ts=vendor_ts,
                    sample_name=sample_name,
                )

            if threshold.lower_critical is not None and actual_value < threshold.lower_critical:
                create_alert_fn(
                    cursor,
                    record_id,
                    name,
                    "lower",
                    threshold.lower_critical,
                    actual_value,
                    "critical",
                    vendor_uuid=vendor_uuid,
                    vendor_ts=vendor_ts,
                    sample_name=sample_name,
                )
            elif threshold.lower_warning is not None and actual_value < threshold.lower_warning:
                create_alert_fn(
                    cursor,
                    record_id,
                    name,
                    "lower",
                    threshold.lower_warning,
                    actual_value,
                    "warning",
                    vendor_uuid=vendor_uuid,
                    vendor_ts=vendor_ts,
                    sample_name=sample_name,
                )

    def create_alert(
        self,
        cursor,
        record_id: int,
        metric_name: str,
        threshold_type: str,
        threshold_value: float,
        actual_value: float,
        severity: str,
        vendor_uuid: Optional[str] = None,
        vendor_ts=None,
        sample_name: Optional[str] = None,
    ) -> None:
        """Insert a new QC alert row."""
        cursor.execute(
            """
            INSERT INTO QCAlerts (processed_record_id, metric_name, threshold_type, threshold_value, actual_value, severity, display_name)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (record_id, metric_name, threshold_type, threshold_value, actual_value, severity, sample_name),
        )
        logger.warning(
            "QC Alert: %s %s threshold exceeded (%.4f vs %.1f) - %s | sample=%s record_id=%d ts=%s",
            metric_name,
            threshold_type,
            actual_value,
            threshold_value,
            severity,
            sample_name,
            record_id,
            vendor_ts,
        )
