from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Set, List
import logging

from config.settings import INITIAL_LOOKBACK_HOURS, SCHEDULER_HEARTBEAT_KEY
from .metrics_calculator import MetricsCalculator
from .cache_refresh_service import CacheRefreshService
from .qc_catalog import extract_bead_lot, extract_catalog_group, lot_like_pattern, select_controls
from .record_ingestion_service import RecordIngestionService
from .rolling_stats_service import RollingStatsService
from .vendor_adapters.base_adapter import BaseVendorAdapter
from database.sqlite_handler import SQLiteHandler
from database.sqlserver_handler import SQLServerConnection

logger = logging.getLogger('beadwatch')


class PollingService:
    """Background service that monitors vendor databases for new records"""

    def __init__(self, sqlite_db: SQLiteHandler, vendor_adapter: BaseVendorAdapter,
                 normalized_db: SQLServerConnection, vendor_db: SQLServerConnection = None):
        self.sqlite = sqlite_db
        self.vendor = vendor_adapter
        self.normalized_db = normalized_db
        self.vendor_db = vendor_db
        self.metrics_calc = MetricsCalculator()
        self.record_ingestion_service = RecordIngestionService()
        self.rolling_stats_service = RollingStatsService()
        self.cache_refresh_service = CacheRefreshService()

    def check_for_new_records(self):
        """Main polling function - runs on scheduler interval"""
        try:
            # Record scheduler heartbeat so the status endpoint can detect stale schedulers
            self.sqlite.app_state.set_state(SCHEDULER_HEARTBEAT_KEY, datetime.now().isoformat())

            logger.info("Polling for new records...")

            last_poll_str = self.sqlite.app_state.get_state('last_poll_timestamp')

            if last_poll_str:
                last_poll = datetime.fromisoformat(last_poll_str)
            else:
                last_poll = datetime.now() - timedelta(hours=INITIAL_LOOKBACK_HOURS)
                logger.info(f"First poll, looking back {INITIAL_LOOKBACK_HOURS} hours")

            new_records = self.vendor.get_new_records(last_poll)

            if not new_records:
                logger.info("No new records found")
                # Do NOT advance the cursor to now() — late-arriving records
                # (inserted into vendor DB after their timestamp) would be skipped.
                # The >= query will re-check from the same point next cycle.
            else:
                logger.info(f"Found {len(new_records)} new records to process")

                processed_count = 0
                skipped_count = 0
                error_count = 0
                changed_metrics: set = set()
                last_successful_timestamp: Optional[datetime] = None

                for record in new_records:
                    try:
                        metrics = self._process_record(record)
                        if metrics is None:
                            skipped_count += 1
                        else:
                            processed_count += 1
                            changed_metrics.update(metrics.keys())
                            last_successful_timestamp = record['timestamp']
                    except Exception as e:
                        logger.error(f"Error processing record {record.get('uuid')}: {e}")
                        error_count += 1

                if processed_count > 0:
                    self._update_rolling_stats(changed_metrics)

                # Only advance the poll timestamp to the last *successfully* processed
                # record, so failed records are retried on the next cycle.
                if last_successful_timestamp:
                    self.sqlite.app_state.set_state('last_poll_timestamp', last_successful_timestamp.isoformat())
                elif error_count == 0:
                    # All records were duplicates (no errors); advance cursor to avoid re-fetch loop.
                    # If any records errored, do NOT advance — they need to be retried.
                    max_ts = max(r['timestamp'] for r in new_records)
                    self.sqlite.app_state.set_state('last_poll_timestamp', max_ts.isoformat())

                logger.info(f"Polling complete: {processed_count} processed, {error_count} errors")

            # Cache refreshes and outlier checks run every cycle, even when
            # no new records were found — the QC cache may have been lazily
            # populated by a dashboard request since the last poll.
            try:
                self.refresh_qc_cache()
            except Exception as e:
                logger.warning("QC cache refresh failed: %s", e)

            try:
                self.check_qc_sample_outliers()
            except Exception as e:
                logger.warning("QC outlier check failed: %s", e)

            try:
                self.refresh_instrument_cache()
            except Exception as e:
                logger.warning("Instrument cache refresh failed: %s", e)

            try:
                self.refresh_operator_cache()
            except Exception as e:
                logger.warning("Operator cache refresh failed: %s", e)

        except Exception as e:
            logger.error(f"Polling service error: {e}")

    def _process_record(self, record: Dict[str, Any]) -> Optional[Dict[str, float]]:
        """Process a single new record atomically within a transaction.

        Returns the calculated metrics dict on success, or None if the
        record was already processed (duplicate).

        If any step fails (insert, metrics, alerts), the entire record
        is rolled back so it can be retried on the next poll cycle.
        """
        return self.record_ingestion_service.process_record(
            normalized_db=self.normalized_db,
            metrics_calc=self.metrics_calc,
            vendor_name=self.vendor.vendor_name,
            record=record,
        )

    def _check_qc_thresholds(self, cursor, record_id: int, metrics: Dict[str, float],
                             vendor_uuid: Optional[str] = None, vendor_ts=None,
                             sample_name: Optional[str] = None):
        """Check metrics against configured thresholds and create alerts.

        Uses the provided cursor so alerts are part of the same transaction
        as the record insert.

        Note: cursor.fetchall() returns pyodbc Row objects which support
        attribute access (row.column_name) but NOT dict-key access (row['column_name']).
        """
        return self.record_ingestion_service.check_qc_thresholds(
            cursor=cursor,
            record_id=record_id,
            metrics=metrics,
            create_alert_fn=self._create_alert,
            vendor_uuid=vendor_uuid,
            vendor_ts=vendor_ts,
            sample_name=sample_name,
        )

    def _create_alert(self, cursor, record_id: int, metric_name: str, threshold_type: str,
                      threshold_value: float, actual_value: float, severity: str,
                      vendor_uuid: Optional[str] = None, vendor_ts=None,
                      sample_name: Optional[str] = None):
        """Insert a new QC alert using the provided transaction cursor."""
        return self.record_ingestion_service.create_alert(
            cursor=cursor,
            record_id=record_id,
            metric_name=metric_name,
            threshold_type=threshold_type,
            threshold_value=threshold_value,
            actual_value=actual_value,
            severity=severity,
            vendor_uuid=vendor_uuid,
            vendor_ts=vendor_ts,
            sample_name=sample_name,
        )

    def _update_rolling_stats(self, changed_metrics: set):
        """Update pre-computed rolling statistics for metrics that changed this cycle.

        Only recalculates stats for metric names present in changed_metrics,
        avoiding unnecessary queries for unchanged metrics.
        """
        return self.rolling_stats_service.update_rolling_stats(
            metrics_calc=self.metrics_calc,
            normalized_db=self.normalized_db,
            changed_metrics=changed_metrics,
        )

    def rebuild_metrics_for_existing_records(self, limit: Optional[int] = None) -> Dict[str, Any]:
        """Recompute metrics for already processed records (e.g., after logic changes)."""
        processed = 0
        skipped = 0
        errors = 0
        changed_metrics: set = set()

        query = """
            SELECT id, vendor_record_uuid
            FROM ProcessedRecords
            WHERE vendor_source = ?
            ORDER BY id DESC
        """
        params = [self.vendor.vendor_name]
        if limit:
            query = query.replace("ORDER BY id DESC", "ORDER BY id DESC OFFSET 0 ROWS FETCH NEXT ? ROWS ONLY")
            params.append(limit)

        records = self.normalized_db.execute_query(query, tuple(params))

        for rec in records:
            try:
                raw = self.vendor.get_test_result_data(rec['vendor_record_uuid'])
                if not raw or not raw.get('bead_results'):
                    skipped += 1
                    continue

                metrics = self.metrics_calc.calculate_qc_metrics(raw)

                with self.normalized_db.transaction() as (conn, cursor):
                    cursor.execute("DELETE FROM QCAlerts WHERE processed_record_id = ?", (rec['id'],))
                    cursor.execute("DELETE FROM QCMetrics WHERE processed_record_id = ?", (rec['id'],))

                    for metric_name, metric_value in metrics.items():
                        if metric_value is not None:
                            cursor.execute(
                                """
                                INSERT INTO QCMetrics (processed_record_id, metric_name, metric_value)
                                VALUES (?, ?, ?)
                                """,
                                (rec['id'], metric_name, float(metric_value))
                            )

                    self._check_qc_thresholds(cursor, rec['id'], metrics)

                changed_metrics.update(metrics.keys())
                processed += 1
            except Exception as e:
                logger.error(f"Error rebuilding metrics for {rec.get('vendor_record_uuid')}: {e}")
                errors += 1

        if changed_metrics:
            self._update_rolling_stats(changed_metrics)

        return {
            'processed': processed,
            'skipped': skipped,
            'errors': errors
        }

    def backfill_display_names(self) -> int:
        """Populate display_name for existing ProcessedRecords that have NULL.

        Queries the vendor DB (Fusion: WELL -> SAMPLE) in batches to look up
        the SampleIDName by WellID (vendor_record_uuid), then updates the
        normalized DB.  Safe to re-run; only touches NULL rows.

        Returns the number of rows updated.
        """
        if not self.vendor_db:
            return 0

        rows = self.normalized_db.execute_query(
            "SELECT id, vendor_record_uuid FROM ProcessedRecords WHERE display_name IS NULL"
        )
        if not rows:
            return 0

        # Build lookup: WellID -> SampleIDName from vendor DB
        uuid_list = [r['vendor_record_uuid'] for r in rows]
        lookup: Dict[str, str] = {}
        batch_size = 500
        for i in range(0, len(uuid_list), batch_size):
            batch = uuid_list[i:i + batch_size]
            placeholders = ','.join(['?'] * len(batch))
            vendor_rows = self.vendor_db.execute_query(
                f"""
                SELECT CAST(w.WellID AS VARCHAR(100)) AS well_id, s.SampleIDName
                FROM dbo.WELL w
                LEFT JOIN dbo.SAMPLE s ON w.SampleID = s.SampleID
                WHERE w.WellID IN ({placeholders})
                """,
                tuple(batch)
            )
            for vr in vendor_rows:
                name = vr.get('SampleIDName')
                if name:
                    lookup[str(vr['well_id'])] = name

        # Update normalized DB
        updated = 0
        for r in rows:
            name = lookup.get(str(r['vendor_record_uuid']))
            if name:
                try:
                    self.normalized_db.execute_non_query(
                        "UPDATE ProcessedRecords SET display_name = ? WHERE id = ?",
                        (name, r['id'])
                    )
                    updated += 1
                except Exception as e:
                    logger.debug("backfill_display_names: update failed for id=%d: %s", r['id'], e)

        if updated:
            logger.info("backfill_display_names: updated %d/%d records", updated, len(rows))
        return updated

    # ── QC Sample Outlier Detection ─────────────────────────────

    QC_OUTLIER_SD = 2  # flag entries beyond mean ± N standard deviations

    def check_qc_sample_outliers(self) -> int:
        """Detect QC sample outliers (beyond mean ± 2 SD) and create alerts.

        Checks median_mfi for each catalog_group / bead_lot / role group.
        Only creates alerts for cache entries that haven't already been flagged
        (dedup via qc_cache_id on QCAlerts).

        Returns the number of new outlier alerts created.
        """
        # How much data is in the cache?
        cache_count = self.normalized_db.execute_query(
            "SELECT COUNT(*) AS n FROM QCSampleCache WHERE median_mfi IS NOT NULL"
        )
        total = cache_count[0]['n'] if cache_count else 0
        logger.info("qc-outlier-check: QCSampleCache has %d rows with median_mfi", total)

        # Get group-level stats (need at least 5 points for meaningful stats)
        group_stats = self.normalized_db.execute_query("""
            SELECT catalog_group, bead_lot, role,
                   AVG(median_mfi) AS mean_mfi,
                   STDEV(median_mfi) AS sd_mfi,
                   COUNT(*) AS n
            FROM QCSampleCache
            WHERE median_mfi IS NOT NULL
            GROUP BY catalog_group, bead_lot, role
            HAVING COUNT(*) >= 5 AND STDEV(median_mfi) > 0
        """)

        if not group_stats:
            logger.info("qc-outlier-check: no groups with >= 5 entries found")
            return 0

        logger.info("qc-outlier-check: %d groups qualify", len(group_stats))
        created = 0
        for gs in group_stats:
            mean = gs['mean_mfi']
            sd = gs['sd_mfi']
            upper_bound = mean + self.QC_OUTLIER_SD * sd
            lower_bound = mean - self.QC_OUTLIER_SD * sd
            logger.info(
                "qc-outlier-check: group=%s lot=%s role=%s n=%d mean=%.2f sd=%.2f bounds=[%.2f, %.2f]",
                gs['catalog_group'], gs['bead_lot'], gs['role'],
                gs['n'], mean, sd, lower_bound, upper_bound,
            )

            # Find outlier entries without an existing alert
            outliers = self.normalized_db.execute_query(
                """
                SELECT c.id, c.display_name, c.median_mfi, c.analysis_dt, c.role
                FROM QCSampleCache c
                WHERE c.catalog_group = ? AND c.bead_lot = ? AND c.role = ?
                  AND c.median_mfi IS NOT NULL
                  AND (c.median_mfi > ? OR c.median_mfi < ?)
                  AND NOT EXISTS (
                      SELECT 1 FROM QCAlerts a WHERE a.qc_cache_id = c.id
                  )
                """,
                (gs['catalog_group'], gs['bead_lot'], gs['role'],
                 upper_bound, lower_bound)
            )

            logger.info("qc-outlier-check: group=%s/%s/%s found %d outliers",
                        gs['catalog_group'], gs['bead_lot'], gs['role'], len(outliers))

            for row in outliers:
                actual = row['median_mfi']
                if actual > upper_bound:
                    threshold_type = 'upper'
                    threshold_val = round(upper_bound, 2)
                else:
                    threshold_type = 'lower'
                    threshold_val = round(lower_bound, 2)

                try:
                    self.normalized_db.execute_non_query(
                        """
                        INSERT INTO QCAlerts
                            (processed_record_id, metric_name, threshold_type,
                             threshold_value, actual_value, severity,
                             display_name, qc_cache_id)
                        VALUES (NULL, ?, ?, ?, ?, 'warning', ?, ?)
                        """,
                        ('qc_median_mfi', threshold_type, threshold_val,
                         round(actual, 2), row.get('display_name'), row['id'])
                    )
                    created += 1
                    logger.warning(
                        "QC Outlier: %s %s median_mfi=%.2f vs %s bound=%.2f | group=%s lot=%s",
                        row.get('display_name'), row['role'], actual,
                        threshold_type, threshold_val,
                        gs['catalog_group'], gs['bead_lot'],
                    )
                except Exception as e:
                    logger.debug("QC outlier alert insert failed: %s", e)

        if created:
            logger.info("qc-outlier-check: created %d new outlier alerts", created)
        return created

    # ── Instrument Run Cache ─────────────────────────────────────

    @staticmethod
    def _is_valid_instrument(csv_sn) -> bool:
        """Return False for null, empty, whitespace-only, or sentinel '9999999' serial numbers."""
        if csv_sn is None:
            return False
        s = str(csv_sn).strip()
        return s != '' and s != '9999999'

    def populate_instrument_cache(self, since_dt: Optional[datetime] = None) -> int:
        """Populate InstrumentRunCache from vendor DB.

        Query 1 (S/N + NC): per-well rows from WELL x TRAY, aggregated in Python.
        Query 2 (bead counts): pre-aggregated in SQL via PERCENTILE_CONT to avoid
        transferring millions of WELL_DETAIL rows.

        Uses a blocking lock so concurrent callers wait for an in-flight
        populate to finish rather than returning empty results.
        Returns the number of rows upserted.
        """
        return self.cache_refresh_service.populate_instrument_cache(
            normalized_db=self.normalized_db,
            vendor_db=self.vendor_db,
            since_dt=since_dt,
            do_populate_fn=self._do_populate_instrument_cache,
        )

    def _do_populate_instrument_cache(self, since_dt: Optional[datetime] = None) -> int:
        return self.cache_refresh_service.do_populate_instrument_cache(
            normalized_db=self.normalized_db,
            vendor_db=self.vendor_db,
            since_dt=since_dt,
            select_controls_fn=self._select_controls,
            is_valid_instrument_fn=self._is_valid_instrument,
        )

    def rebuild_instrument_cache(self):
        """Clear instrument cache. Lazy repopulation on next request."""
        self.cache_refresh_service.rebuild_instrument_cache(self.normalized_db)

    def refresh_instrument_cache(self):
        """Incrementally update instrument cache with new vendor data."""
        self.cache_refresh_service.refresh_instrument_cache(
            normalized_db=self.normalized_db,
            vendor_db=self.vendor_db,
            populate_instrument_cache_fn=self.populate_instrument_cache,
        )

    # ── Operator Run Cache ───────────────────────────────────────

    @staticmethod
    def _is_valid_operator(username) -> bool:
        """Return False for null, empty, or whitespace-only usernames."""
        if username is None:
            return False
        s = str(username).strip()
        return s != ''

    def populate_operator_cache(self, since_dt: Optional[datetime] = None) -> int:
        """Populate OperatorRunCache from vendor DB.

        Uses a blocking lock so concurrent callers wait for an in-flight
        populate to finish rather than returning empty results.
        Returns the number of rows upserted.
        """
        return self.cache_refresh_service.populate_operator_cache(
            normalized_db=self.normalized_db,
            vendor_db=self.vendor_db,
            since_dt=since_dt,
            do_populate_fn=self._do_populate_operator_cache,
        )

    def _do_populate_operator_cache(self, since_dt: Optional[datetime] = None) -> int:
        return self.cache_refresh_service.do_populate_operator_cache(
            normalized_db=self.normalized_db,
            vendor_db=self.vendor_db,
            since_dt=since_dt,
            select_controls_fn=self._select_controls,
            is_valid_operator_fn=self._is_valid_operator,
        )

    def rebuild_operator_cache(self):
        """Clear operator cache. Lazy repopulation on next request."""
        self.cache_refresh_service.rebuild_operator_cache(self.normalized_db)

    def refresh_operator_cache(self):
        """Incrementally update operator cache with new vendor data."""
        self.cache_refresh_service.refresh_operator_cache(
            normalized_db=self.normalized_db,
            vendor_db=self.vendor_db,
            populate_operator_cache_fn=self.populate_operator_cache,
        )

    # ── QC Sample Cache ────────────────────────────────────────

    @staticmethod
    def _lot_like_pattern(catalog_group: str, bead_lot: str) -> str:
        """Build a SQL LIKE pattern that targets a specific bead lot."""
        return lot_like_pattern(catalog_group, bead_lot)

    @staticmethod
    def _extract_catalog_group(catalog_id: Optional[str]) -> Optional[str]:
        return extract_catalog_group(catalog_id)

    @staticmethod
    def _extract_bead_lot(catalog_id: Optional[str]) -> Optional[str]:
        return extract_bead_lot(catalog_id)

    @staticmethod
    def _select_controls(catalog_id: Optional[str], nc1, nc2, pc1, pc2) -> Dict[str, Optional[float]]:
        return select_controls(catalog_id, nc1, nc2, pc1, pc2)

    def populate_qc_cache(self, catalog_group: str, bead_lot: str, since_dt: Optional[datetime] = None) -> int:
        return self.cache_refresh_service.populate_qc_cache(
            sqlite_db=self.sqlite,
            normalized_db=self.normalized_db,
            vendor_db=self.vendor_db,
            catalog_group=catalog_group,
            bead_lot=bead_lot,
            since_dt=since_dt,
            lot_like_pattern_fn=self._lot_like_pattern,
            extract_catalog_group_fn=self._extract_catalog_group,
            extract_bead_lot_fn=self._extract_bead_lot,
            select_controls_fn=self._select_controls,
        )

    def rebuild_qc_cache(self, catalog_group: Optional[str] = None, bead_lot: Optional[str] = None):
        """Clear QC cache (optionally for a specific group/lot). Lazy repopulation on next read."""
        self.cache_refresh_service.rebuild_qc_cache(self.normalized_db, catalog_group, bead_lot)

    def refresh_qc_cache(self):
        """Incrementally update cached group/lot combos with new vendor data."""
        self.cache_refresh_service.refresh_qc_cache(
            normalized_db=self.normalized_db,
            vendor_db=self.vendor_db,
            populate_qc_cache_fn=self.populate_qc_cache,
        )
