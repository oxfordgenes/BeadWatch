import asyncio
import logging
import threading
import time
from fastapi import APIRouter, HTTPException, Query, Request
from typing import List, Optional, Dict, Any, Set
from datetime import datetime

from models.data_models import QCMetric, RollingStats, QCAlert, SystemStatus
from config.settings import __version__, SCHEDULER_HEARTBEAT_KEY, POLL_INTERVAL_MINUTES, POLL_INTERVAL_STATE_KEY, CACHE_POPULATE_TIMEOUT
from services.dashboard_query_service import DashboardQueryService
from services.runtime_services import get_runtime
from services.qc_catalog import (
    extract_bead_lot, extract_catalog_group, lot_like_pattern, select_controls,
    uppercased_alias_map, uppercased_value_set,
)
from utils.cache import TTLCache

logger = logging.getLogger('beadwatch')

_cache = TTLCache(default_ttl=120.0, max_entries=50)


class DashboardController:
    """API controller for dashboard data endpoints.

    Dependencies (normalized_db, sqlite_db) are resolved per-request from
    request.app.state rather than injected at construction time, so the
    controller works seamlessly after first-run setup without a restart.
    """

    def __init__(self):
        self.router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
        self.query_service = DashboardQueryService()
        self._populate_events: Dict[str, threading.Event] = {}
        self._populate_guard = threading.Lock()
        self._register_routes()

    def _register_routes(self):
        self.router.get("/metrics/recent", response_model=List[QCMetric])(self.get_recent_metrics)
        self.router.get("/metrics/rolling-stats", response_model=RollingStats)(self.get_rolling_stats)
        self.router.get("/alerts/active", response_model=List[QCAlert])(self.get_active_alerts)
        self.router.get("/status", response_model=SystemStatus)(self.get_system_status)
        self.router.get("/metrics/available")(self.get_available_metrics)
        self.router.get("/qc/catalog-groups")(self.get_catalog_groups)
        self.router.get("/qc/bead-lots")(self.get_bead_lots)
        self.router.get("/qc/controls")(self.get_controls_trend)
        self.router.get("/qc/bead-counts")(self.get_bead_counts_trend)
        self.router.get("/qc/sn-ratio")(self.get_sn_ratio_trend)
        self.router.get("/qc/sample-trend")(self.get_qc_sample_trend)
        self.router.get("/qc/sample-names")(self.get_qc_sample_names)
        self.router.get("/qc/distribution")(self.get_distribution_points)
        self.router.get("/qc/instruments")(self.get_instruments)
        self.router.get("/qc/instrument-sn-ratio")(self.get_instrument_sn_ratio)
        self.router.get("/qc/instrument-nc")(self.get_instrument_nc)
        self.router.get("/qc/instrument-bead-counts")(self.get_instrument_bead_counts)
        self.router.get("/qc/operators")(self.get_operators)
        self.router.get("/qc/operator-sn-ratio")(self.get_operator_sn_ratio)
        self.router.get("/qc/operator-nc")(self.get_operator_nc)
        self.router.get("/qc/operator-bead-counts")(self.get_operator_bead_counts)
        self.router.post("/alerts/acknowledge-all")(self.acknowledge_all_alerts)
        self.router.delete("/alerts/all")(self.delete_all_alerts)
        self.router.post("/alerts/{alert_id}/acknowledge")(self.acknowledge_alert)

    def _get_deps(self, request: Request):
        """Retrieve database handles from runtime services; raise 503 if not ready."""
        rt = get_runtime(request.app)
        if not rt.normalized_db:
            raise HTTPException(status_code=503, detail="Database not configured yet")
        return rt.normalized_db, rt.sqlite_db

    def _get_vendor_db(self, request: Request):
        vendor_db = get_runtime(request.app).vendor_db
        if not vendor_db:
            raise HTTPException(status_code=503, detail="Vendor database not configured yet")
        return vendor_db

    @staticmethod
    def _get_excluded_instruments(sqlite_db) -> Set[str]:
        """Return upper-cased set of excluded instrument serial numbers."""
        rows = sqlite_db.instruments.get_excluded_instruments()
        return uppercased_value_set(rows, "serial_number")

    @staticmethod
    def _get_instrument_aliases(sqlite_db) -> Dict[str, str]:
        """Return {SERIAL_UPPER: display_name} mapping."""
        rows = sqlite_db.instruments.get_instrument_aliases()
        return uppercased_alias_map(rows, "serial_number", "display_name")

    @staticmethod
    def _lot_like_pattern(catalog_group: str, bead_lot: str) -> str:
        """Build a SQL LIKE pattern that targets a specific bead lot.

        CatalogIDs follow the format ``{group_prefix}{extra}_{lot}_{suffix}``
        (e.g. ``LS1A04NC12_008_02.1``), so the pattern uses ``%`` after the
        group prefix to account for variable extra characters before the
        first underscore.
        """
        return lot_like_pattern(catalog_group, bead_lot)

    async def get_recent_metrics(
        self,
        request: Request,
        hours: int = Query(default=24, ge=0, le=87600),
        metric_name: Optional[str] = Query(default=None)
    ) -> List[QCMetric]:
        """Get QC metrics from the last N hours"""
        normalized_db, _ = self._get_deps(request)
        return self.query_service.get_recent_metrics(normalized_db, hours=hours, metric_name=metric_name)

    async def get_rolling_stats(
        self,
        request: Request,
        metric_name: str = Query(...),
        window_days: int = Query(default=30, ge=0, le=365)
    ) -> RollingStats:
        """Get rolling statistics for a specific metric"""
        normalized_db, _ = self._get_deps(request)
        try:
            return self.query_service.get_rolling_stats(normalized_db, metric_name=metric_name, window_days=window_days)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e))

    async def get_active_alerts(
        self,
        request: Request,
        severity: Optional[str] = Query(default=None),
        days: int = Query(default=7, ge=1, le=30),
        include_acknowledged: bool = Query(default=False)
    ) -> List[QCAlert]:
        """Get active QC alerts"""
        normalized_db, _ = self._get_deps(request)
        return self.query_service.get_active_alerts(
            normalized_db,
            severity=severity,
            days=days,
            include_acknowledged=include_acknowledged,
        )

    async def acknowledge_alert(self, alert_id: int, request: Request):
        """Acknowledge a single alert by ID."""
        normalized_db, _ = self._get_deps(request)
        rows_affected = self.query_service.acknowledge_alert(normalized_db, alert_id)
        if rows_affected == 0:
            raise HTTPException(status_code=404, detail="Alert not found or already acknowledged")
        return {"status": "ok", "alert_id": alert_id}

    async def acknowledge_all_alerts(self, request: Request):
        """Acknowledge all unacknowledged alerts."""
        normalized_db, _ = self._get_deps(request)
        rows_affected = self.query_service.acknowledge_all_alerts(normalized_db)
        return {"status": "ok", "count": rows_affected}

    async def delete_all_alerts(self, request: Request):
        """Permanently delete all alerts. Used for testing / reset."""
        normalized_db, _ = self._get_deps(request)
        rows_affected = self.query_service.delete_all_alerts(normalized_db)
        return {"status": "ok", "deleted": rows_affected}

    async def get_system_status(self, request: Request) -> SystemStatus:
        """Get overall system health status, including scheduler heartbeat."""
        normalized_db, sqlite_db = self._get_deps(request)

        last_poll_str = sqlite_db.app_state.get_state('last_poll_timestamp')
        last_poll = datetime.fromisoformat(last_poll_str) if last_poll_str else None

        normalized_status = normalized_db.get_status()

        try:
            record_count = self.query_service.get_processed_record_count(normalized_db)
        except Exception:
            record_count = 0

        # Determine overall status (includes scheduler health check)
        overall_status = 'healthy'
        if normalized_status['status'] != 'connected':
            overall_status = 'degraded'

        heartbeat_str = sqlite_db.app_state.get_state(SCHEDULER_HEARTBEAT_KEY)
        stored_interval = sqlite_db.app_state.get_state(POLL_INTERVAL_STATE_KEY)
        poll_interval = int(stored_interval) if stored_interval else POLL_INTERVAL_MINUTES
        stale_threshold_seconds = (poll_interval * 2 + 5) * 60

        scheduler_healthy = True
        last_heartbeat = None
        if heartbeat_str:
            last_heartbeat = datetime.fromisoformat(heartbeat_str)
            if (datetime.now() - last_heartbeat).total_seconds() > stale_threshold_seconds:
                scheduler_healthy = False
                overall_status = 'degraded'
        elif last_poll:
            # Scheduler has never recorded a heartbeat but we've polled before — suspicious
            scheduler_healthy = False
            overall_status = 'degraded'

        # Use heartbeat (actual last run time) for the response; fall back to
        # last_poll (record cursor) only if no heartbeat has been recorded yet.
        last_run = last_heartbeat or last_poll

        return SystemStatus(
            status=overall_status, last_poll=last_run,
            vendor_database={'status': 'unknown', 'last_successful_query': None, 'error_message': None},
            normalized_database={**normalized_status, 'scheduler_healthy': scheduler_healthy},
            total_records_processed=record_count, version=__version__
        )

    async def get_available_metrics(self, request: Request) -> List[str]:
        """Get list of all available metric names"""
        normalized_db, _ = self._get_deps(request)
        return self.query_service.get_available_metrics(normalized_db)

    @staticmethod
    def _extract_bead_lot(catalog_id: Optional[str]) -> Optional[str]:
        return extract_bead_lot(catalog_id)

    @staticmethod
    def _extract_catalog_group(catalog_id: Optional[str]) -> Optional[str]:
        return extract_catalog_group(catalog_id)

    @staticmethod
    def _select_controls(catalog_id: Optional[str], nc1, nc2, pc1, pc2) -> Dict[str, Optional[float]]:
        return select_controls(catalog_id, nc1, nc2, pc1, pc2)

    async def get_catalog_groups(self, request: Request, days: int = Query(default=365, ge=0, le=3650)) -> List[str]:
        """Return distinct catalog groups observed in recent trays."""
        vendor_db = self._get_vendor_db(request)
        return self.query_service.get_catalog_groups(vendor_db, days=days)

    async def get_bead_lots(
        self,
        request: Request,
        catalog_group: str = Query(...),
        days: int = Query(default=365, ge=0, le=3650)
    ) -> List[str]:
        """Return distinct bead lots observed for a catalog group."""
        vendor_db = self._get_vendor_db(request)
        return self.query_service.get_bead_lots(vendor_db, catalog_group=catalog_group, days=days)

    async def get_controls_trend(
        self,
        request: Request,
        catalog_group: str = Query(...),
        bead_lot: str = Query(...),
        days: int = Query(default=30, ge=0, le=3650)
    ) -> List[Dict[str, Any]]:
        """Return individual NC/PC values per run for a bead lot."""
        cache_key = f"controls|{catalog_group}|{bead_lot}|{days}"
        cached = _cache.get(cache_key)
        if cached is not None:
            logger.info("controls: cache hit key=%s", cache_key)
            return cached
        result = self.query_service.get_controls_trend(self._get_vendor_db(request), catalog_group, bead_lot, days)
        _cache.put(cache_key, result)
        return result

    async def get_bead_counts_trend(
        self,
        request: Request,
        catalog_group: str = Query(...),
        bead_lot: str = Query(...),
        days: int = Query(default=30, ge=0, le=3650)
    ) -> List[Dict[str, Any]]:
        """Return per-run bead count statistics for a bead lot."""
        cache_key = f"bead-counts|{catalog_group}|{bead_lot}|{days}"
        cached = _cache.get(cache_key)
        if cached is not None:
            logger.info("bead-counts: cache hit key=%s", cache_key)
            return cached
        result = self.query_service.get_bead_counts_trend(self._get_vendor_db(request), catalog_group, bead_lot, days)
        _cache.put(cache_key, result)
        return result

    async def get_sn_ratio_trend(
        self,
        request: Request,
        catalog_group: str = Query(...),
        bead_lot: str = Query(...),
        days: int = Query(default=30, ge=0, le=3650)
    ) -> List[Dict[str, Any]]:
        """Return per-run signal-to-noise (PC/NC) ratio for a bead lot."""
        cache_key = f"sn-ratio|{catalog_group}|{bead_lot}|{days}"
        cached = _cache.get(cache_key)
        if cached is not None:
            logger.info("sn-ratio: cache hit key=%s", cache_key)
            return cached
        result = self.query_service.get_sn_ratio_trend(self._get_vendor_db(request), catalog_group, bead_lot, days)
        _cache.put(cache_key, result)
        return result

    async def get_qc_sample_trend(
        self,
        request: Request,
        catalog_group: str = Query(...),
        bead_lot: str = Query(...),
        days: int = Query(default=30, ge=0, le=3650)
    ) -> List[Dict[str, Any]]:
        """Return QC sample MFI trends from the QCSampleCache table.

        On first request for a group/lot, the cache is lazily populated from
        the vendor DB.  Subsequent requests (including different time windows)
        read directly from the cache — zero vendor DB hits.
        """
        normalized_db, sqlite_db = self._get_deps(request)

        # Load definitions — if none configured, nothing to return
        definitions = sqlite_db.qc.get_qc_sample_definitions()
        if not definitions:
            return []

        # Lazy-populate cache if empty for this group/lot
        t0 = time.perf_counter()
        count_rows = normalized_db.execute_query(
            "SELECT COUNT(*) as n FROM dbo.QCSampleCache WHERE catalog_group = ? AND bead_lot = ?",
            (catalog_group, bead_lot)
        )
        if not count_rows or count_rows[0]['n'] == 0:
            polling_service = get_runtime(request.app).polling_service
            if polling_service:
                # Event-based dedup: one thread populates, others wait on the same event
                cache_key = f"{catalog_group}:{bead_lot}"
                with self._populate_guard:
                    existing = self._populate_events.get(cache_key)
                    is_owner = existing is None
                    if is_owner:
                        existing = threading.Event()
                        self._populate_events[cache_key] = existing
                event = existing

                if is_owner:
                    populate_error = [None]

                    def _populate():
                        try:
                            polling_service.populate_qc_cache(catalog_group, bead_lot)
                        except Exception as exc:
                            populate_error[0] = exc
                        finally:
                            event.set()

                    threading.Thread(target=_populate, daemon=True).start()
                    deadline = time.perf_counter() + CACHE_POPULATE_TIMEOUT
                    while not event.wait(timeout=0.1):
                        if time.perf_counter() >= deadline:
                            logger.warning("Cache populate timed out after %ds for %s/%s",
                                           CACHE_POPULATE_TIMEOUT, catalog_group, bead_lot)
                            break
                        await asyncio.sleep(0.1)
                    if populate_error[0]:
                        logger.error("Cache populate failed for %s/%s: %s",
                                     catalog_group, bead_lot, populate_error[0])
                    # Remove entry so hung threads don't permanently block the key
                    with self._populate_guard:
                        self._populate_events.pop(cache_key, None)
                else:
                    # Another request is already populating; wait on its event
                    deadline = time.perf_counter() + CACHE_POPULATE_TIMEOUT
                    while not event.wait(timeout=0.1):
                        if time.perf_counter() >= deadline:
                            logger.warning("Cache populate wait timed out for %s/%s",
                                           catalog_group, bead_lot)
                            break
                        await asyncio.sleep(0.1)

        return self.query_service.get_qc_sample_trend_from_cache(normalized_db, sqlite_db, catalog_group, bead_lot, days)

    async def get_qc_sample_names(
        self,
        request: Request,
        catalog_group: str = Query(...),
        bead_lot: str = Query(...),
        days: int = Query(default=365, ge=0, le=3650)
    ) -> List[Dict[str, Any]]:
        """Return distinct (SampleIDName, PatientID) pairs seen in the vendor DB."""
        return self.query_service.get_qc_sample_names(self._get_vendor_db(request), catalog_group, bead_lot, days)

    async def get_distribution_points(
        self,
        request: Request,
        catalog_group: str = Query(...),
        bead_lot: str = Query(...),
        days: int = Query(default=30, ge=0, le=3650),
        max_points_per_day: int = Query(default=200, ge=50, le=2000)
    ) -> List[Dict[str, Any]]:
        """Return per-day MFI points for a bead lot (downsampled), including bead_id."""
        cache_key = f"distribution|{catalog_group}|{bead_lot}|{days}|{max_points_per_day}"
        cached = _cache.get(cache_key)
        if cached is not None:
            logger.info("distribution: cache hit key=%s", cache_key)
            return cached
        result = self.query_service.get_distribution_points(self._get_vendor_db(request), catalog_group, bead_lot, days, max_points_per_day)
        _cache.put(cache_key, result)
        return result

    async def _ensure_instrument_cache(self, request: Request, normalized_db):
        """Kick off instrument cache populate if empty (fire-and-forget).

        Returns immediately so the endpoint can return an empty list.
        The JS frontend auto-retries every 15s until data appears.
        """
        count_rows = normalized_db.execute_query(
            "SELECT COUNT(*) as n FROM dbo.InstrumentRunCache"
        )
        if not count_rows or count_rows[0]['n'] == 0:
            polling_service = get_runtime(request.app).polling_service
            if polling_service:
                def _run():
                    try:
                        polling_service.populate_instrument_cache()
                    except Exception:
                        logger.exception("instrument cache populate failed in background")
                t = threading.Thread(target=_run, daemon=True)
                t.start()

    async def get_instruments(
        self,
        request: Request,
        days: int = Query(default=90, ge=0, le=3650)
    ) -> List[str]:
        """Return distinct valid instrument serial numbers from the cache."""
        normalized_db, sqlite_db = self._get_deps(request)
        excluded = self._get_excluded_instruments(sqlite_db)
        aliases = self._get_instrument_aliases(sqlite_db)
        await self._ensure_instrument_cache(request, normalized_db)
        return self.query_service.get_instruments(normalized_db, excluded, aliases, days)

    async def get_instrument_sn_ratio(
        self,
        request: Request,
        days: int = Query(default=90, ge=0, le=3650)
    ) -> List[Dict[str, Any]]:
        """Return per-run S/N ratio grouped by instrument (from cache)."""
        normalized_db, sqlite_db = self._get_deps(request)
        excluded = self._get_excluded_instruments(sqlite_db)
        aliases = self._get_instrument_aliases(sqlite_db)
        await self._ensure_instrument_cache(request, normalized_db)
        return self.query_service.get_instrument_sn_ratio(normalized_db, excluded, aliases, days)

    async def get_instrument_nc(
        self,
        request: Request,
        days: int = Query(default=90, ge=0, le=3650)
    ) -> List[Dict[str, Any]]:
        """Return per-run background (NC) grouped by instrument (from cache)."""
        normalized_db, sqlite_db = self._get_deps(request)
        excluded = self._get_excluded_instruments(sqlite_db)
        aliases = self._get_instrument_aliases(sqlite_db)
        await self._ensure_instrument_cache(request, normalized_db)
        return self.query_service.get_instrument_nc(normalized_db, excluded, aliases, days)

    async def get_instrument_bead_counts(
        self,
        request: Request,
        days: int = Query(default=90, ge=0, le=3650)
    ) -> List[Dict[str, Any]]:
        """Return per-run bead count statistics grouped by instrument (from cache)."""
        normalized_db, sqlite_db = self._get_deps(request)
        excluded = self._get_excluded_instruments(sqlite_db)
        aliases = self._get_instrument_aliases(sqlite_db)
        await self._ensure_instrument_cache(request, normalized_db)
        return self.query_service.get_instrument_bead_counts(normalized_db, excluded, aliases, days)

    # ── Operator Comparison ──────────────────────────────────

    @staticmethod
    def _get_excluded_operators(sqlite_db) -> Set[str]:
        """Return upper-cased set of excluded operator usernames."""
        rows = sqlite_db.operators.get_excluded_operators()
        return uppercased_value_set(rows, "username")

    @staticmethod
    def _get_operator_aliases(sqlite_db) -> Dict[str, str]:
        """Return {USERNAME_UPPER: display_name} mapping."""
        rows = sqlite_db.operators.get_operator_aliases()
        return uppercased_alias_map(rows, "username", "display_name")

    async def _ensure_operator_cache(self, request: Request, normalized_db):
        """Kick off operator cache populate if empty (fire-and-forget)."""
        count_rows = normalized_db.execute_query(
            "SELECT COUNT(*) as n FROM dbo.OperatorRunCache"
        )
        if not count_rows or count_rows[0]['n'] == 0:
            polling_service = get_runtime(request.app).polling_service
            if polling_service:
                def _run():
                    try:
                        polling_service.populate_operator_cache()
                    except Exception:
                        logger.exception("operator cache populate failed in background")
                t = threading.Thread(target=_run, daemon=True)
                t.start()

    async def get_operators(
        self,
        request: Request,
        days: int = Query(default=90, ge=0, le=3650)
    ) -> List[str]:
        """Return distinct valid operator usernames from the cache."""
        normalized_db, sqlite_db = self._get_deps(request)
        excluded = self._get_excluded_operators(sqlite_db)
        aliases = self._get_operator_aliases(sqlite_db)
        await self._ensure_operator_cache(request, normalized_db)
        return self.query_service.get_operators(normalized_db, excluded, aliases, days)

    async def get_operator_sn_ratio(
        self,
        request: Request,
        days: int = Query(default=90, ge=0, le=3650)
    ) -> List[Dict[str, Any]]:
        """Return per-run S/N ratio grouped by operator (from cache)."""
        normalized_db, sqlite_db = self._get_deps(request)
        excluded = self._get_excluded_operators(sqlite_db)
        aliases = self._get_operator_aliases(sqlite_db)
        await self._ensure_operator_cache(request, normalized_db)
        return self.query_service.get_operator_sn_ratio(normalized_db, excluded, aliases, days)

    async def get_operator_nc(
        self,
        request: Request,
        days: int = Query(default=90, ge=0, le=3650)
    ) -> List[Dict[str, Any]]:
        """Return per-run background (NC) grouped by operator (from cache)."""
        normalized_db, sqlite_db = self._get_deps(request)
        excluded = self._get_excluded_operators(sqlite_db)
        aliases = self._get_operator_aliases(sqlite_db)
        await self._ensure_operator_cache(request, normalized_db)
        return self.query_service.get_operator_nc(normalized_db, excluded, aliases, days)

    async def get_operator_bead_counts(
        self,
        request: Request,
        days: int = Query(default=90, ge=0, le=3650)
    ) -> List[Dict[str, Any]]:
        """Return per-run bead count statistics grouped by operator (from cache)."""
        normalized_db, sqlite_db = self._get_deps(request)
        excluded = self._get_excluded_operators(sqlite_db)
        aliases = self._get_operator_aliases(sqlite_db)
        await self._ensure_operator_cache(request, normalized_db)
        return self.query_service.get_operator_bead_counts(normalized_db, excluded, aliases, days)
