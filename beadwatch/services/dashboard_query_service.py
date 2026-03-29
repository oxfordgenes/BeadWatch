import json
import logging
import time
from datetime import datetime
from statistics import median
from typing import Any, Dict, List, Optional, Set

from models.data_models import QCAlert, QCMetric, RollingStats
from services.qc_catalog import extract_bead_lot, extract_catalog_group, lot_like_pattern, select_controls

logger = logging.getLogger('beadwatch')


class DashboardQueryService:
    """Query-focused service for dashboard read operations."""

    def get_recent_metrics(
        self,
        normalized_db,
        hours: int = 24,
        metric_name: Optional[str] = None,
    ) -> List[QCMetric]:
        if hours == 0:
            query = """
                SELECT pr.vendor_record_timestamp as timestamp, qm.metric_name, qm.metric_value as value
                FROM QCMetrics qm
                JOIN ProcessedRecords pr ON qm.processed_record_id = pr.id
                WHERE 1 = 1
            """
            params = []
        else:
            query = """
                SELECT pr.vendor_record_timestamp as timestamp, qm.metric_name, qm.metric_value as value
                FROM QCMetrics qm
                JOIN ProcessedRecords pr ON qm.processed_record_id = pr.id
                WHERE pr.vendor_record_timestamp >= DATEADD(hour, -?, GETDATE())
            """
            params = [hours]

        if metric_name:
            query += " AND qm.metric_name = ?"
            params.append(metric_name)

        query += " ORDER BY pr.vendor_record_timestamp DESC"
        results = normalized_db.execute_query(query, tuple(params))
        return [QCMetric(timestamp=row["timestamp"], metric_name=row["metric_name"], value=row["value"]) for row in results]

    def get_rolling_stats(
        self,
        normalized_db,
        metric_name: str,
        window_days: int = 30,
    ) -> RollingStats:
        query = """
            SELECT metric_name, window_days, mean_value, std_dev, min_value, max_value, record_count, updated_at
            FROM RollingStats WHERE metric_name = ? AND window_days = ?
        """
        results = normalized_db.execute_query(query, (metric_name, window_days))
        if not results:
            raise LookupError(f"Statistics not found for metric '{metric_name}'")
        row = results[0]
        return RollingStats(
            metric_name=row["metric_name"],
            window_days=row["window_days"],
            mean=row["mean_value"],
            std_dev=row["std_dev"] or 0,
            min_value=row["min_value"],
            max_value=row["max_value"],
            count=row["record_count"],
            updated_at=row["updated_at"],
        )

    def get_active_alerts(
        self,
        normalized_db,
        severity: Optional[str] = None,
        days: int = 7,
        include_acknowledged: bool = False,
    ) -> List[QCAlert]:
        query = """
            SELECT qa.id, COALESCE(pr.vendor_record_timestamp, qa.created_at) as timestamp,
                   qa.metric_name, qa.threshold_type,
                   qa.threshold_value, qa.actual_value, qa.severity, qa.created_at,
                   COALESCE(qa.display_name, pr.display_name) as display_name
            FROM QCAlerts qa
            LEFT JOIN ProcessedRecords pr ON qa.processed_record_id = pr.id
            WHERE qa.created_at >= DATEADD(day, -?, GETDATE())
        """
        params = [days]
        if not include_acknowledged:
            query += " AND qa.acknowledged = 0"
        if severity:
            query += " AND qa.severity = ?"
            params.append(severity)
        query += " ORDER BY qa.created_at DESC"

        results = normalized_db.execute_query(query, tuple(params))
        return [
            QCAlert(
                id=row["id"],
                timestamp=row["timestamp"],
                metric_name=row["metric_name"],
                threshold_type=row["threshold_type"],
                threshold_value=row["threshold_value"],
                actual_value=row["actual_value"],
                severity=row["severity"],
                created_at=row["created_at"],
                display_name=row.get("display_name"),
            )
            for row in results
        ]

    def get_available_metrics(self, normalized_db) -> List[str]:
        results = normalized_db.execute_query("SELECT DISTINCT metric_name FROM QCMetrics ORDER BY metric_name")
        return [row["metric_name"] for row in results]

    def acknowledge_alert(self, normalized_db, alert_id: int) -> int:
        return normalized_db.execute_non_query(
            """
            UPDATE QCAlerts SET acknowledged = 1, acknowledged_at = GETDATE()
            WHERE id = ? AND acknowledged = 0
            """,
            (alert_id,),
        )

    def acknowledge_all_alerts(self, normalized_db) -> int:
        return normalized_db.execute_non_query(
            "UPDATE QCAlerts SET acknowledged = 1, acknowledged_at = GETDATE() WHERE acknowledged = 0"
        )

    def delete_all_alerts(self, normalized_db) -> int:
        return normalized_db.execute_non_query("DELETE FROM QCAlerts")

    def get_processed_record_count(self, normalized_db) -> int:
        result = normalized_db.execute_query("SELECT COUNT(*) as count FROM ProcessedRecords")
        return result[0]["count"] if result else 0

    def get_catalog_groups(self, vendor_db, days: int = 365) -> List[str]:
        if days == 0:
            query = """
                SELECT DISTINCT t.CatalogID
                FROM dbo.TRAY t
                JOIN dbo.WELL w ON t.TrayID = w.TrayID
                WHERE w.AnalysisDT IS NOT NULL
            """
            rows = vendor_db.execute_query(query)
        else:
            query = """
                SELECT DISTINCT t.CatalogID
                FROM dbo.TRAY t
                JOIN dbo.WELL w ON t.TrayID = w.TrayID
                WHERE t.BuildDT >= DATEADD(day, -?, GETDATE())
                  AND w.AnalysisDT IS NOT NULL
            """
            rows = vendor_db.execute_query(query, (days,))

        groups: Set[str] = set()
        for row in rows:
            group = extract_catalog_group(row.get("CatalogID"))
            if group:
                groups.add(group)
        return sorted(groups)

    def get_bead_lots(self, vendor_db, catalog_group: str, days: int = 365) -> List[str]:
        like_pattern = f"{catalog_group}%"
        if days == 0:
            query = """
                SELECT DISTINCT t.CatalogID
                FROM dbo.TRAY t
                JOIN dbo.WELL w ON t.TrayID = w.TrayID
                WHERE w.AnalysisDT IS NOT NULL
                  AND t.CatalogID LIKE ?
            """
            rows = vendor_db.execute_query(query, (like_pattern,))
        else:
            query = """
                SELECT DISTINCT t.CatalogID
                FROM dbo.TRAY t
                JOIN dbo.WELL w ON t.TrayID = w.TrayID
                WHERE t.BuildDT >= DATEADD(day, -?, GETDATE())
                  AND w.AnalysisDT IS NOT NULL
                  AND t.CatalogID LIKE ?
            """
            rows = vendor_db.execute_query(query, (days, like_pattern))

        lots: Set[str] = set()
        for row in rows:
            group = extract_catalog_group(row.get("CatalogID"))
            if group != catalog_group:
                continue
            lot = extract_bead_lot(row.get("CatalogID"))
            if lot:
                lots.add(lot)
        return sorted(lots, reverse=True)

    # ── Vendor DB Trend Queries ──────────────────────────────────────────────

    def get_controls_trend(self, vendor_db, catalog_group: str, bead_lot: str, days: int) -> List[Dict[str, Any]]:
        lot_pattern = lot_like_pattern(catalog_group, bead_lot)
        t0 = time.perf_counter()
        if days == 0:
            query = """
                SELECT t.BuildDT, t.CatalogID, t.TrayID, t.TrayIDName,
                       w.NC1, w.NC2, w.PC1, w.PC2
                FROM dbo.WELL w
                JOIN dbo.TRAY t ON t.TrayID = w.TrayID
                WHERE w.AnalysisDT IS NOT NULL
                  AND t.CatalogID LIKE ?
            """
            rows = vendor_db.execute_query(query, (lot_pattern,))
        else:
            query = """
                SELECT t.BuildDT, t.CatalogID, t.TrayID, t.TrayIDName,
                       w.NC1, w.NC2, w.PC1, w.PC2
                FROM dbo.WELL w
                JOIN dbo.TRAY t ON t.TrayID = w.TrayID
                WHERE t.BuildDT >= DATEADD(day, -?, GETDATE())
                  AND w.AnalysisDT IS NOT NULL
                  AND t.CatalogID LIKE ?
            """
            rows = vendor_db.execute_query(query, (days, lot_pattern))
        t_sql = time.perf_counter() - t0

        t1 = time.perf_counter()
        runs: Dict[Any, Dict] = {}
        for row in rows:
            if extract_catalog_group(row.get('CatalogID')) != catalog_group:
                continue
            if extract_bead_lot(row.get('CatalogID')) != bead_lot:
                continue
            tray_id = row.get('TrayID')
            if tray_id not in runs:
                runs[tray_id] = {'datetime': row['BuildDT'], 'run_name': row.get('TrayIDName') or '', 'pc_values': [], 'nc_values': []}
            if row['BuildDT'] < runs[tray_id]['datetime']:
                runs[tray_id]['datetime'] = row['BuildDT']
            controls = select_controls(row.get('CatalogID'), row.get('NC1'), row.get('NC2'), row.get('PC1'), row.get('PC2'))
            if controls['pc'] is not None:
                runs[tray_id]['pc_values'].append(float(controls['pc']))
            if controls['nc'] is not None:
                runs[tray_id]['nc_values'].append(float(controls['nc']))

        result = []
        for run in runs.values():
            pc, nc = run['pc_values'], run['nc_values']
            dt = run['datetime']
            result.append({
                'datetime': dt.isoformat(), 'date': dt.date().isoformat(), 'run_name': run['run_name'],
                'pc_median': median(pc) if pc else None, 'pc_min': min(pc) if pc else None, 'pc_max': max(pc) if pc else None,
                'nc_median': median(nc) if nc else None, 'nc_min': min(nc) if nc else None, 'nc_max': max(nc) if nc else None,
            })
        t_filter = time.perf_counter() - t1
        result.sort(key=lambda r: r['datetime'])
        logger.info("controls: sql=%.3fs rows=%d | filter=%.3fs kept=%d | total=%.3fs",
                    t_sql, len(rows), t_filter, len(result), time.perf_counter() - t0)
        return result

    def get_bead_counts_trend(self, vendor_db, catalog_group: str, bead_lot: str, days: int) -> List[Dict[str, Any]]:
        lot_pattern = lot_like_pattern(catalog_group, bead_lot)
        t0 = time.perf_counter()
        if days == 0:
            query = """
                SELECT t.BuildDT, t.TrayID, t.TrayIDName, t.CatalogID,
                       wd.CountValue as bead_count
                FROM dbo.WELL_DETAIL wd
                JOIN dbo.WELL w ON wd.WellID = w.WellID
                JOIN dbo.TRAY t ON w.TrayID = t.TrayID
                WHERE w.AnalysisDT IS NOT NULL
                  AND t.CatalogID LIKE ?
            """
            rows = vendor_db.execute_query(query, (lot_pattern,))
        else:
            query = """
                SELECT t.BuildDT, t.TrayID, t.TrayIDName, t.CatalogID,
                       wd.CountValue as bead_count
                FROM dbo.WELL_DETAIL wd
                JOIN dbo.WELL w ON wd.WellID = w.WellID
                JOIN dbo.TRAY t ON w.TrayID = t.TrayID
                WHERE w.AnalysisDT IS NOT NULL
                  AND t.CatalogID LIKE ?
                  AND t.BuildDT >= DATEADD(day, -?, GETDATE())
            """
            rows = vendor_db.execute_query(query, (lot_pattern, days))
        t_sql = time.perf_counter() - t0

        t1 = time.perf_counter()
        runs: Dict[Any, Dict] = {}
        for row in rows:
            if extract_catalog_group(row.get('CatalogID')) != catalog_group:
                continue
            if extract_bead_lot(row.get('CatalogID')) != bead_lot:
                continue
            if row.get('bead_count') is None:
                continue
            tray_id = row.get('TrayID')
            if tray_id not in runs:
                runs[tray_id] = {'datetime': row['BuildDT'], 'run_name': row.get('TrayIDName') or '', 'counts': []}
            if row['BuildDT'] < runs[tray_id]['datetime']:
                runs[tray_id]['datetime'] = row['BuildDT']
            runs[tray_id]['counts'].append(float(row['bead_count']))

        result = []
        for run in runs.values():
            counts, dt = run['counts'], run['datetime']
            total = len(counts)
            low = sum(1 for c in counts if c < 25)
            result.append({
                'datetime': dt.isoformat(), 'date': dt.date().isoformat(), 'run_name': run['run_name'],
                'median_count': median(counts), 'min_count': min(counts), 'max_count': max(counts),
                'total_readings': total, 'low_count_pct': round(low / total * 100, 1) if total else 0,
            })
        t_filter = time.perf_counter() - t1
        result.sort(key=lambda r: r['datetime'])
        logger.info("bead-counts: sql=%.3fs rows=%d | filter=%.3fs kept=%d | total=%.3fs",
                    t_sql, len(rows), t_filter, len(result), time.perf_counter() - t0)
        return result

    def get_sn_ratio_trend(self, vendor_db, catalog_group: str, bead_lot: str, days: int) -> List[Dict[str, Any]]:
        lot_pattern = lot_like_pattern(catalog_group, bead_lot)
        t0 = time.perf_counter()
        if days == 0:
            query = """
                SELECT t.BuildDT, t.CatalogID, t.TrayID, t.TrayIDName,
                       w.NC1, w.NC2, w.PC1, w.PC2
                FROM dbo.WELL w
                JOIN dbo.TRAY t ON t.TrayID = w.TrayID
                WHERE w.AnalysisDT IS NOT NULL
                  AND t.CatalogID LIKE ?
            """
            rows = vendor_db.execute_query(query, (lot_pattern,))
        else:
            query = """
                SELECT t.BuildDT, t.CatalogID, t.TrayID, t.TrayIDName,
                       w.NC1, w.NC2, w.PC1, w.PC2
                FROM dbo.WELL w
                JOIN dbo.TRAY t ON t.TrayID = w.TrayID
                WHERE t.BuildDT >= DATEADD(day, -?, GETDATE())
                  AND w.AnalysisDT IS NOT NULL
                  AND t.CatalogID LIKE ?
            """
            rows = vendor_db.execute_query(query, (days, lot_pattern))
        t_sql = time.perf_counter() - t0

        t1 = time.perf_counter()
        runs: Dict[Any, Dict] = {}
        for row in rows:
            if extract_catalog_group(row.get('CatalogID')) != catalog_group:
                continue
            if extract_bead_lot(row.get('CatalogID')) != bead_lot:
                continue
            controls = select_controls(row.get('CatalogID'), row.get('NC1'), row.get('NC2'), row.get('PC1'), row.get('PC2'))
            pc_val, nc_val = controls['pc'], controls['nc']
            if pc_val is None or nc_val is None or float(nc_val) <= 0:
                continue
            sn = float(pc_val) / float(nc_val)
            tray_id = row.get('TrayID')
            if tray_id not in runs:
                runs[tray_id] = {'datetime': row['BuildDT'], 'run_name': row.get('TrayIDName') or '', 'sn_values': []}
            if row['BuildDT'] < runs[tray_id]['datetime']:
                runs[tray_id]['datetime'] = row['BuildDT']
            runs[tray_id]['sn_values'].append(round(sn, 2))

        result = []
        for run in runs.values():
            sn_vals, dt = run['sn_values'], run['datetime']
            result.append({
                'datetime': dt.isoformat(), 'date': dt.date().isoformat(), 'run_name': run['run_name'],
                'sn_median': round(median(sn_vals), 2), 'sn_min': round(min(sn_vals), 2),
                'sn_max': round(max(sn_vals), 2), 'well_count': len(sn_vals),
            })
        t_filter = time.perf_counter() - t1
        result.sort(key=lambda r: r['datetime'])
        logger.info("sn-ratio: sql=%.3fs rows=%d | filter=%.3fs kept=%d | total=%.3fs",
                    t_sql, len(rows), t_filter, len(result), time.perf_counter() - t0)
        return result

    def get_qc_sample_names(self, vendor_db, catalog_group: str, bead_lot: str, days: int) -> List[Dict[str, Any]]:
        lot_pattern = lot_like_pattern(catalog_group, bead_lot)
        if days == 0:
            query = """
                SELECT DISTINCT s.SampleIDName, s.PatientID
                FROM dbo.WELL w
                JOIN dbo.TRAY t ON t.TrayID = w.TrayID
                JOIN dbo.SAMPLE s ON s.SampleID = w.SampleID
                WHERE w.AnalysisDT IS NOT NULL
                  AND w.SampleID IS NOT NULL
                  AND t.CatalogID LIKE ?
            """
            rows = vendor_db.execute_query(query, (lot_pattern,))
        else:
            query = """
                SELECT DISTINCT s.SampleIDName, s.PatientID
                FROM dbo.WELL w
                JOIN dbo.TRAY t ON t.TrayID = w.TrayID
                JOIN dbo.SAMPLE s ON s.SampleID = w.SampleID
                WHERE t.BuildDT >= DATEADD(day, -?, GETDATE())
                  AND w.AnalysisDT IS NOT NULL
                  AND w.SampleID IS NOT NULL
                  AND t.CatalogID LIKE ?
            """
            rows = vendor_db.execute_query(query, (days, lot_pattern))

        result, seen = [], set()
        for row in rows:
            key = (row.get('SampleIDName') or '', row.get('PatientID') or '')
            if key in seen:
                continue
            seen.add(key)
            result.append({'sample_name': key[0], 'patient_id': key[1]})
        result.sort(key=lambda r: (r['sample_name'], r['patient_id']))
        return result

    def get_distribution_points(self, vendor_db, catalog_group: str, bead_lot: str, days: int, max_points_per_day: int) -> List[Dict[str, Any]]:
        lot_pattern = lot_like_pattern(catalog_group, bead_lot)
        t0 = time.perf_counter()
        if days == 0:
            query = """
                WITH numbered AS (
                    SELECT t.BuildDT, t.CatalogID, wd.RawData as mfi, wd.BeadID as bead_id,
                           ROW_NUMBER() OVER (PARTITION BY CAST(t.BuildDT AS DATE) ORDER BY wd.WellID) as rn
                    FROM dbo.WELL_DETAIL wd
                    JOIN dbo.WELL w ON wd.WellID = w.WellID
                    JOIN dbo.TRAY t ON w.TrayID = t.TrayID
                    WHERE w.AnalysisDT IS NOT NULL
                      AND t.CatalogID LIKE ?
                )
                SELECT BuildDT, CatalogID, mfi, bead_id FROM numbered WHERE rn <= ?
            """
            rows = vendor_db.execute_query(query, (lot_pattern, max_points_per_day))
        else:
            query = """
                WITH numbered AS (
                    SELECT t.BuildDT, t.CatalogID, wd.RawData as mfi, wd.BeadID as bead_id,
                           ROW_NUMBER() OVER (PARTITION BY CAST(t.BuildDT AS DATE) ORDER BY wd.WellID) as rn
                    FROM dbo.WELL_DETAIL wd
                    JOIN dbo.WELL w ON wd.WellID = w.WellID
                    JOIN dbo.TRAY t ON w.TrayID = t.TrayID
                    WHERE t.BuildDT >= DATEADD(day, -?, GETDATE())
                      AND w.AnalysisDT IS NOT NULL
                      AND t.CatalogID LIKE ?
                )
                SELECT BuildDT, CatalogID, mfi, bead_id FROM numbered WHERE rn <= ?
            """
            rows = vendor_db.execute_query(query, (days, lot_pattern, max_points_per_day))
        t_sql = time.perf_counter() - t0

        t1 = time.perf_counter()
        bucketed: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            if extract_catalog_group(row.get('CatalogID')) != catalog_group:
                continue
            if extract_bead_lot(row.get('CatalogID')) != bead_lot:
                continue
            if row.get('mfi') is None:
                continue
            day = row['BuildDT'].date().isoformat()
            bucketed.setdefault(day, [])
            if len(bucketed[day]) < max_points_per_day:
                bucketed[day].append({'mfi': float(row['mfi']), 'bead_id': row.get('bead_id')})
        t_filter = time.perf_counter() - t1

        points: List[Dict[str, Any]] = [
            {'date': day, 'mfi': pt['mfi'], 'bead_id': pt['bead_id']}
            for day in sorted(bucketed.keys())
            for pt in bucketed[day]
        ]
        logger.info("distribution: sql=%.3fs rows=%d | filter=%.3fs kept=%d | total=%.3fs",
                    t_sql, len(rows), t_filter, len(points), time.perf_counter() - t0)
        return points

    def get_qc_sample_trend_from_cache(self, normalized_db, sqlite_db, catalog_group: str, bead_lot: str, days: int) -> List[Dict[str, Any]]:
        t0 = time.perf_counter()
        if days == 0:
            rows = normalized_db.execute_query(
                "SELECT * FROM dbo.QCSampleCache WHERE catalog_group = ? AND bead_lot = ? ORDER BY analysis_dt",
                (catalog_group, bead_lot)
            )
        else:
            rows = normalized_db.execute_query(
                "SELECT * FROM dbo.QCSampleCache WHERE catalog_group = ? AND bead_lot = ? AND analysis_dt >= DATEADD(day, -?, GETDATE()) ORDER BY analysis_dt",
                (catalog_group, bead_lot, days)
            )
        t_sql = time.perf_counter() - t0

        tracked_beads = sqlite_db.qc.get_qc_tracked_beads(catalog_group=catalog_group, bead_lot=bead_lot)
        tracked_bead_ids = {tb['bead_id'] for tb in tracked_beads}

        t1 = time.perf_counter()
        result = []
        for row in rows:
            dt = row['analysis_dt']
            if isinstance(dt, str):
                dt = datetime.fromisoformat(dt)
            tracked_bead_mfi = None
            if tracked_bead_ids:
                all_beads = {}
                if row.get('bead_mfi_json'):
                    try:
                        all_beads = json.loads(row['bead_mfi_json'])
                    except (json.JSONDecodeError, TypeError):
                        pass
                tracked_bead_mfi = {bid: all_beads.get(bid) for bid in tracked_bead_ids}
            result.append({
                'datetime': dt.isoformat(),
                'date': dt.date().isoformat() if hasattr(dt, 'date') else dt[:10],
                'run_name': row.get('run_name') or '',
                'sample_name': row.get('display_name') or row.get('sample_id_name') or '',
                'role': row.get('role') or '',
                'instrument': row.get('instrument') or '',
                'pc': row.get('pc'), 'nc': row.get('nc'),
                'median_mfi': row.get('median_mfi'),
                'tracked_bead_mfi': tracked_bead_mfi,
                'median_count': row.get('median_count'),
                'sn_ratio': row.get('sn_ratio'),
            })
        t_filter = time.perf_counter() - t1
        logger.info("qc-sample: cache-read sql=%.3fs rows=%d | filter=%.3fs | total=%.3fs",
                    t_sql, len(rows), t_filter, time.perf_counter() - t0)
        return result

    # ── Instrument Cache Queries ─────────────────────────────────────────────

    def get_instruments(self, normalized_db, excluded: Set[str], aliases: Dict[str, str], days: int) -> List[str]:
        if days == 0:
            rows = normalized_db.execute_query("SELECT DISTINCT instrument FROM dbo.InstrumentRunCache")
        else:
            rows = normalized_db.execute_query(
                "SELECT DISTINCT instrument FROM dbo.InstrumentRunCache WHERE analysis_dt >= DATEADD(day, -?, GETDATE())", (days,))
        return sorted(aliases.get(r['instrument'].upper(), r['instrument']) for r in rows if r['instrument'].upper() not in excluded)

    def get_instrument_sn_ratio(self, normalized_db, excluded: Set[str], aliases: Dict[str, str], days: int) -> List[Dict[str, Any]]:
        if days == 0:
            rows = normalized_db.execute_query(
                "SELECT instrument, tray_id, analysis_dt, run_name, sn_median, sn_min, sn_max FROM dbo.InstrumentRunCache WHERE sn_median IS NOT NULL ORDER BY analysis_dt")
        else:
            rows = normalized_db.execute_query(
                "SELECT instrument, tray_id, analysis_dt, run_name, sn_median, sn_min, sn_max FROM dbo.InstrumentRunCache WHERE sn_median IS NOT NULL AND analysis_dt >= DATEADD(day, -?, GETDATE()) ORDER BY analysis_dt", (days,))
        return self._map_cache_rows(rows, excluded, aliases, 'instrument', {'sn_median': 'sn_median', 'sn_min': 'sn_min', 'sn_max': 'sn_max'})

    def get_instrument_nc(self, normalized_db, excluded: Set[str], aliases: Dict[str, str], days: int) -> List[Dict[str, Any]]:
        if days == 0:
            rows = normalized_db.execute_query(
                "SELECT instrument, tray_id, analysis_dt, run_name, nc_median, nc_min, nc_max FROM dbo.InstrumentRunCache WHERE nc_median IS NOT NULL ORDER BY analysis_dt")
        else:
            rows = normalized_db.execute_query(
                "SELECT instrument, tray_id, analysis_dt, run_name, nc_median, nc_min, nc_max FROM dbo.InstrumentRunCache WHERE nc_median IS NOT NULL AND analysis_dt >= DATEADD(day, -?, GETDATE()) ORDER BY analysis_dt", (days,))
        return self._map_cache_rows(rows, excluded, aliases, 'instrument', {'nc_median': 'nc_median', 'nc_min': 'nc_min', 'nc_max': 'nc_max'})

    def get_instrument_bead_counts(self, normalized_db, excluded: Set[str], aliases: Dict[str, str], days: int) -> List[Dict[str, Any]]:
        if days == 0:
            rows = normalized_db.execute_query(
                "SELECT instrument, tray_id, analysis_dt, run_name, count_median, count_min, count_max FROM dbo.InstrumentRunCache WHERE count_median IS NOT NULL ORDER BY analysis_dt")
        else:
            rows = normalized_db.execute_query(
                "SELECT instrument, tray_id, analysis_dt, run_name, count_median, count_min, count_max FROM dbo.InstrumentRunCache WHERE count_median IS NOT NULL AND analysis_dt >= DATEADD(day, -?, GETDATE()) ORDER BY analysis_dt", (days,))
        return self._map_cache_rows(rows, excluded, aliases, 'instrument',
                                    {'count_median': 'median_count', 'count_min': 'min_count', 'count_max': 'max_count'})

    # ── Operator Cache Queries ───────────────────────────────────────────────

    def get_operators(self, normalized_db, excluded: Set[str], aliases: Dict[str, str], days: int) -> List[str]:
        if days == 0:
            rows = normalized_db.execute_query("SELECT DISTINCT operator FROM dbo.OperatorRunCache")
        else:
            rows = normalized_db.execute_query(
                "SELECT DISTINCT operator FROM dbo.OperatorRunCache WHERE analysis_dt >= DATEADD(day, -?, GETDATE())", (days,))
        return sorted(aliases.get(r['operator'].upper(), r['operator']) for r in rows if r['operator'].upper() not in excluded)

    def get_operator_sn_ratio(self, normalized_db, excluded: Set[str], aliases: Dict[str, str], days: int) -> List[Dict[str, Any]]:
        if days == 0:
            rows = normalized_db.execute_query(
                "SELECT operator, tray_id, analysis_dt, run_name, sn_median, sn_min, sn_max FROM dbo.OperatorRunCache WHERE sn_median IS NOT NULL ORDER BY analysis_dt")
        else:
            rows = normalized_db.execute_query(
                "SELECT operator, tray_id, analysis_dt, run_name, sn_median, sn_min, sn_max FROM dbo.OperatorRunCache WHERE sn_median IS NOT NULL AND analysis_dt >= DATEADD(day, -?, GETDATE()) ORDER BY analysis_dt", (days,))
        return self._map_cache_rows(rows, excluded, aliases, 'operator', {'sn_median': 'sn_median', 'sn_min': 'sn_min', 'sn_max': 'sn_max'})

    def get_operator_nc(self, normalized_db, excluded: Set[str], aliases: Dict[str, str], days: int) -> List[Dict[str, Any]]:
        if days == 0:
            rows = normalized_db.execute_query(
                "SELECT operator, tray_id, analysis_dt, run_name, nc_median, nc_min, nc_max FROM dbo.OperatorRunCache WHERE nc_median IS NOT NULL ORDER BY analysis_dt")
        else:
            rows = normalized_db.execute_query(
                "SELECT operator, tray_id, analysis_dt, run_name, nc_median, nc_min, nc_max FROM dbo.OperatorRunCache WHERE nc_median IS NOT NULL AND analysis_dt >= DATEADD(day, -?, GETDATE()) ORDER BY analysis_dt", (days,))
        return self._map_cache_rows(rows, excluded, aliases, 'operator', {'nc_median': 'nc_median', 'nc_min': 'nc_min', 'nc_max': 'nc_max'})

    def get_operator_bead_counts(self, normalized_db, excluded: Set[str], aliases: Dict[str, str], days: int) -> List[Dict[str, Any]]:
        if days == 0:
            rows = normalized_db.execute_query(
                "SELECT operator, tray_id, analysis_dt, run_name, count_median, count_min, count_max FROM dbo.OperatorRunCache WHERE count_median IS NOT NULL ORDER BY analysis_dt")
        else:
            rows = normalized_db.execute_query(
                "SELECT operator, tray_id, analysis_dt, run_name, count_median, count_min, count_max FROM dbo.OperatorRunCache WHERE count_median IS NOT NULL AND analysis_dt >= DATEADD(day, -?, GETDATE()) ORDER BY analysis_dt", (days,))
        return self._map_cache_rows(rows, excluded, aliases, 'operator',
                                    {'count_median': 'median_count', 'count_min': 'min_count', 'count_max': 'max_count'})

    @staticmethod
    def _map_cache_rows(rows, excluded: Set[str], aliases: Dict[str, str], actor_key: str, field_map: Dict[str, str]) -> List[Dict[str, Any]]:
        """Shared row mapper for instrument/operator cache queries."""
        result = []
        for row in rows:
            actor = row[actor_key]
            if actor.upper() in excluded:
                continue
            dt = row['analysis_dt']
            if isinstance(dt, str):
                dt = datetime.fromisoformat(dt)
            entry = {
                actor_key: aliases.get(actor.upper(), actor),
                'datetime': dt.isoformat(),
                'date': dt.date().isoformat(),
                'run_name': row.get('run_name') or '',
            }
            for src, dst in field_map.items():
                entry[dst] = row[src]
            result.append(entry)
        return result
