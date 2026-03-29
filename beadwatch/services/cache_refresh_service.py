import json
import logging
import threading
import time
from datetime import datetime
from statistics import median
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("beadwatch")


class CacheRefreshService:
    """Owns cache populate/rebuild/refresh logic and related locks."""

    def __init__(self):
        self._instrument_cache_lock = threading.Lock()
        self._operator_cache_lock = threading.Lock()

    def populate_instrument_cache(
        self,
        normalized_db,
        vendor_db,
        since_dt: Optional[datetime],
        do_populate_fn: Callable[[Optional[datetime]], int],
    ) -> int:
        if not vendor_db:
            logger.warning("populate_instrument_cache: vendor_db not available")
            return 0

        with self._instrument_cache_lock:
            if since_dt is None:
                count_rows = normalized_db.execute_query("SELECT COUNT(*) as n FROM dbo.InstrumentRunCache")
                if count_rows and count_rows[0]["n"] > 0:
                    return 0
            return do_populate_fn(since_dt)

    def do_populate_instrument_cache(
        self,
        normalized_db,
        vendor_db,
        since_dt: Optional[datetime],
        select_controls_fn: Callable[..., Dict[str, Optional[float]]],
        is_valid_instrument_fn: Callable[[Any], bool],
    ) -> int:
        t0 = time.perf_counter()
        logger.info("instrument-cache-populate: STARTING since=%s", since_dt)

        if since_dt:
            sn_query = """
                SELECT t.BuildDT, t.CatalogID, t.CsvSN, t.TrayID, t.TrayIDName,
                       w.NC1, w.NC2, w.PC1, w.PC2
                FROM dbo.WELL w
                JOIN dbo.TRAY t ON t.TrayID = w.TrayID
                WHERE t.BuildDT > ?
                  AND w.AnalysisDT IS NOT NULL
            """
            sn_rows = vendor_db.execute_query(sn_query, (since_dt,))
        else:
            sn_query = """
                SELECT t.BuildDT, t.CatalogID, t.CsvSN, t.TrayID, t.TrayIDName,
                       w.NC1, w.NC2, w.PC1, w.PC2
                FROM dbo.WELL w
                JOIN dbo.TRAY t ON t.TrayID = w.TrayID
                WHERE w.AnalysisDT IS NOT NULL
            """
            sn_rows = vendor_db.execute_query(sn_query)

        sn_runs: Dict[Any, Dict] = {}
        for row in sn_rows:
            csv_sn = row.get("CsvSN")
            if not is_valid_instrument_fn(csv_sn):
                continue
            instrument = str(csv_sn).strip()
            controls = select_controls_fn(row.get("CatalogID"), row.get("NC1"), row.get("NC2"), row.get("PC1"), row.get("PC2"))
            tray_id_str = str(row.get("TrayID"))
            key = (instrument, tray_id_str)
            if key not in sn_runs:
                sn_runs[key] = {
                    "instrument": instrument,
                    "tray_id": tray_id_str,
                    "analysis_dt": row["BuildDT"],
                    "run_name": row.get("TrayIDName") or "",
                    "sn_values": [],
                    "nc_values": [],
                }
            if row["BuildDT"] < sn_runs[key]["analysis_dt"]:
                sn_runs[key]["analysis_dt"] = row["BuildDT"]
            pc_val = controls["pc"]
            nc_val = controls["nc"]
            if nc_val is not None:
                sn_runs[key]["nc_values"].append(float(nc_val))
            if pc_val is not None and nc_val is not None and float(nc_val) > 0:
                sn_runs[key]["sn_values"].append(float(pc_val) / float(nc_val))

        t_sn = time.perf_counter()
        logger.info("instrument-cache-populate: S/N query done in %.1fs, %d rows", t_sn - t0, len(sn_rows))

        _count_sql = """
            SELECT LTRIM(RTRIM(CAST(t.CsvSN AS VARCHAR(100)))) AS instrument,
                   CAST(t.TrayID AS VARCHAR(50)) AS tray_id,
                   t.TrayIDName AS run_name,
                   t.BuildDT AS analysis_dt,
                   AVG(CAST(wd.CountValue AS FLOAT)) AS well_avg_count,
                   MIN(CAST(wd.CountValue AS FLOAT)) AS well_min_count,
                   MAX(CAST(wd.CountValue AS FLOAT)) AS well_max_count
            FROM dbo.WELL_DETAIL wd
            JOIN dbo.WELL w ON wd.WellID = w.WellID
            JOIN dbo.TRAY t ON w.TrayID = t.TrayID
            WHERE wd.CountValue IS NOT NULL
              AND {date_filter}
              AND t.CsvSN IS NOT NULL
              AND LTRIM(RTRIM(CAST(t.CsvSN AS VARCHAR(100)))) NOT IN ('', '9999999')
            GROUP BY t.CsvSN, t.TrayID, t.TrayIDName, t.BuildDT, w.WellID
        """
        if since_dt:
            count_query = _count_sql.format(date_filter="t.BuildDT > ? AND w.AnalysisDT IS NOT NULL")
            count_rows = vendor_db.execute_query(count_query, (since_dt,))
        else:
            count_query = _count_sql.format(date_filter="w.AnalysisDT IS NOT NULL")
            count_rows = vendor_db.execute_query(count_query)

        logger.info("instrument-cache-populate: bead count query done in %.1fs, %d rows", time.perf_counter() - t_sn, len(count_rows))

        count_runs: Dict[Any, Dict] = {}
        for row in count_rows:
            inst = row["instrument"]
            if not is_valid_instrument_fn(inst):
                continue
            tray_id = row["tray_id"]
            key = (inst, tray_id)
            if key not in count_runs:
                count_runs[key] = {
                    "instrument": inst,
                    "tray_id": tray_id,
                    "analysis_dt": row["analysis_dt"],
                    "run_name": row.get("run_name") or "",
                    "avg_values": [],
                    "min_values": [],
                    "max_values": [],
                }
            cr = count_runs[key]
            if row["analysis_dt"] < cr["analysis_dt"]:
                cr["analysis_dt"] = row["analysis_dt"]
            cr["avg_values"].append(row["well_avg_count"])
            cr["min_values"].append(row["well_min_count"])
            cr["max_values"].append(row["well_max_count"])

        for cr in count_runs.values():
            cr["count_median"] = round(median(cr["avg_values"]), 2) if cr["avg_values"] else None
            cr["count_min"] = round(min(cr["min_values"]), 2) if cr["min_values"] else None
            cr["count_max"] = round(max(cr["max_values"]), 2) if cr["max_values"] else None

        t_count = time.perf_counter()
        all_keys = set(sn_runs.keys()) | set(count_runs.keys())
        upserted = 0

        for key in all_keys:
            sn_data = sn_runs.get(key)
            count_data = count_runs.get(key)
            instrument = (sn_data or count_data)["instrument"]
            tray_id = (sn_data or count_data)["tray_id"]
            analysis_dt = (sn_data or count_data)["analysis_dt"]
            run_name = (sn_data or count_data)["run_name"]
            if sn_data and count_data and count_data["analysis_dt"] < analysis_dt:
                analysis_dt = count_data["analysis_dt"]

            sn_median = sn_min = sn_max = None
            if sn_data and sn_data["sn_values"]:
                vals = sn_data["sn_values"]
                sn_median = round(median(vals), 2)
                sn_min = round(min(vals), 2)
                sn_max = round(max(vals), 2)

            nc_median = nc_min = nc_max = None
            if sn_data and sn_data["nc_values"]:
                vals = sn_data["nc_values"]
                nc_median = round(median(vals), 2)
                nc_min = round(min(vals), 2)
                nc_max = round(max(vals), 2)

            count_med = count_data["count_median"] if count_data else None
            count_min_v = count_data["count_min"] if count_data else None
            count_max_v = count_data["count_max"] if count_data else None

            try:
                normalized_db.execute_non_query(
                    """
                    MERGE dbo.InstrumentRunCache AS target
                    USING (SELECT ? AS instrument, ? AS tray_id) AS source
                    ON target.instrument = source.instrument AND target.tray_id = source.tray_id
                    WHEN MATCHED THEN
                        UPDATE SET sn_median=?, sn_min=?, sn_max=?,
                                   nc_median=?, nc_min=?, nc_max=?,
                                   count_median=?, count_min=?, count_max=?,
                                   analysis_dt=?, run_name=?
                    WHEN NOT MATCHED THEN
                        INSERT (instrument, tray_id, analysis_dt, run_name,
                                sn_median, sn_min, sn_max,
                                nc_median, nc_min, nc_max,
                                count_median, count_min, count_max)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        instrument, tray_id,
                        sn_median, sn_min, sn_max,
                        nc_median, nc_min, nc_max,
                        count_med, count_min_v, count_max_v,
                        analysis_dt, run_name,
                        instrument, tray_id, analysis_dt, run_name,
                        sn_median, sn_min, sn_max,
                        nc_median, nc_min, nc_max,
                        count_med, count_min_v, count_max_v,
                    ),
                )
                upserted += 1
            except Exception as e:
                logger.debug("Instrument cache upsert failed: %s", e)

        t_total = time.perf_counter() - t0
        logger.info(
            "instrument-cache-populate: since=%s | sn_rows=%d count_groups=%d | upserted=%d | sn=%.3fs counts=%.3fs total=%.3fs",
            since_dt,
            len(sn_rows),
            len(count_runs),
            upserted,
            t_sn - t0,
            t_count - t_sn,
            t_total,
        )
        return upserted

    def rebuild_instrument_cache(self, normalized_db) -> None:
        normalized_db.execute_non_query("DELETE FROM InstrumentRunCache")
        logger.info("instrument-cache-rebuild: cleared all")

    def refresh_instrument_cache(self, normalized_db, vendor_db, populate_instrument_cache_fn: Callable[..., int]) -> None:
        if not vendor_db:
            return
        max_rows = normalized_db.execute_query("SELECT MAX(analysis_dt) as max_dt FROM dbo.InstrumentRunCache")
        max_dt = max_rows[0]["max_dt"] if max_rows and max_rows[0]["max_dt"] else None
        if max_dt is None:
            return
        populate_instrument_cache_fn(since_dt=max_dt)

    def populate_operator_cache(
        self,
        normalized_db,
        vendor_db,
        since_dt: Optional[datetime],
        do_populate_fn: Callable[[Optional[datetime]], int],
    ) -> int:
        if not vendor_db:
            logger.warning("populate_operator_cache: vendor_db not available")
            return 0
        with self._operator_cache_lock:
            if since_dt is None:
                count_rows = normalized_db.execute_query("SELECT COUNT(*) as n FROM dbo.OperatorRunCache")
                if count_rows and count_rows[0]["n"] > 0:
                    return 0
            return do_populate_fn(since_dt)

    def do_populate_operator_cache(
        self,
        normalized_db,
        vendor_db,
        since_dt: Optional[datetime],
        select_controls_fn: Callable[..., Dict[str, Optional[float]]],
        is_valid_operator_fn: Callable[[Any], bool],
    ) -> int:
        t0 = time.perf_counter()
        logger.info("operator-cache-populate: STARTING since=%s", since_dt)

        if since_dt:
            sn_query = """
                SELECT t.BuildDT, t.CatalogID,
                       UPPER(LTRIM(RTRIM(u.UserName))) AS operator,
                       t.TrayID, t.TrayIDName,
                       w.NC1, w.NC2, w.PC1, w.PC2
                FROM dbo.WELL w
                JOIN dbo.TRAY t ON t.TrayID = w.TrayID
                JOIN dbo.[User] u ON u.UserID = t.AddUserID
                WHERE t.BuildDT > ?
                  AND w.AnalysisDT IS NOT NULL
                  AND u.UserName IS NOT NULL
                  AND LTRIM(RTRIM(u.UserName)) <> ''
            """
            sn_rows = vendor_db.execute_query(sn_query, (since_dt,))
        else:
            sn_query = """
                SELECT t.BuildDT, t.CatalogID,
                       UPPER(LTRIM(RTRIM(u.UserName))) AS operator,
                       t.TrayID, t.TrayIDName,
                       w.NC1, w.NC2, w.PC1, w.PC2
                FROM dbo.WELL w
                JOIN dbo.TRAY t ON t.TrayID = w.TrayID
                JOIN dbo.[User] u ON u.UserID = t.AddUserID
                WHERE w.AnalysisDT IS NOT NULL
                  AND u.UserName IS NOT NULL
                  AND LTRIM(RTRIM(u.UserName)) <> ''
            """
            sn_rows = vendor_db.execute_query(sn_query)

        sn_runs: Dict[Any, Dict] = {}
        for row in sn_rows:
            operator = row.get("operator")
            if not is_valid_operator_fn(operator):
                continue
            operator = str(operator).strip().upper()
            controls = select_controls_fn(row.get("CatalogID"), row.get("NC1"), row.get("NC2"), row.get("PC1"), row.get("PC2"))
            tray_id_str = str(row.get("TrayID"))
            key = (operator, tray_id_str)
            if key not in sn_runs:
                sn_runs[key] = {
                    "operator": operator,
                    "tray_id": tray_id_str,
                    "analysis_dt": row["BuildDT"],
                    "run_name": row.get("TrayIDName") or "",
                    "sn_values": [],
                    "nc_values": [],
                }
            if row["BuildDT"] < sn_runs[key]["analysis_dt"]:
                sn_runs[key]["analysis_dt"] = row["BuildDT"]
            pc_val = controls["pc"]
            nc_val = controls["nc"]
            if nc_val is not None:
                sn_runs[key]["nc_values"].append(float(nc_val))
            if pc_val is not None and nc_val is not None and float(nc_val) > 0:
                sn_runs[key]["sn_values"].append(float(pc_val) / float(nc_val))

        t_sn = time.perf_counter()
        logger.info("operator-cache-populate: S/N query done in %.1fs, %d rows", t_sn - t0, len(sn_rows))

        _count_sql = """
            SELECT UPPER(LTRIM(RTRIM(u.UserName))) AS operator,
                   CAST(t.TrayID AS VARCHAR(50)) AS tray_id,
                   t.TrayIDName AS run_name,
                   t.BuildDT AS analysis_dt,
                   AVG(CAST(wd.CountValue AS FLOAT)) AS well_avg_count,
                   MIN(CAST(wd.CountValue AS FLOAT)) AS well_min_count,
                   MAX(CAST(wd.CountValue AS FLOAT)) AS well_max_count
            FROM dbo.WELL_DETAIL wd
            JOIN dbo.WELL w ON wd.WellID = w.WellID
            JOIN dbo.TRAY t ON w.TrayID = t.TrayID
            JOIN dbo.[User] u ON u.UserID = t.AddUserID
            WHERE wd.CountValue IS NOT NULL
              AND {date_filter}
              AND u.UserName IS NOT NULL
              AND LTRIM(RTRIM(u.UserName)) <> ''
            GROUP BY u.UserName, t.TrayID, t.TrayIDName, t.BuildDT, w.WellID
        """
        if since_dt:
            count_query = _count_sql.format(date_filter="t.BuildDT > ? AND w.AnalysisDT IS NOT NULL")
            count_rows = vendor_db.execute_query(count_query, (since_dt,))
        else:
            count_query = _count_sql.format(date_filter="w.AnalysisDT IS NOT NULL")
            count_rows = vendor_db.execute_query(count_query)

        logger.info("operator-cache-populate: bead count query done in %.1fs, %d rows", time.perf_counter() - t_sn, len(count_rows))

        count_runs: Dict[Any, Dict] = {}
        for row in count_rows:
            operator = row["operator"]
            if not is_valid_operator_fn(operator):
                continue
            operator = str(operator).strip().upper()
            tray_id = row["tray_id"]
            key = (operator, tray_id)
            if key not in count_runs:
                count_runs[key] = {
                    "operator": operator,
                    "tray_id": tray_id,
                    "analysis_dt": row["analysis_dt"],
                    "run_name": row.get("run_name") or "",
                    "avg_values": [],
                    "min_values": [],
                    "max_values": [],
                }
            cr = count_runs[key]
            if row["analysis_dt"] < cr["analysis_dt"]:
                cr["analysis_dt"] = row["analysis_dt"]
            cr["avg_values"].append(row["well_avg_count"])
            cr["min_values"].append(row["well_min_count"])
            cr["max_values"].append(row["well_max_count"])

        for cr in count_runs.values():
            cr["count_median"] = round(median(cr["avg_values"]), 2) if cr["avg_values"] else None
            cr["count_min"] = round(min(cr["min_values"]), 2) if cr["min_values"] else None
            cr["count_max"] = round(max(cr["max_values"]), 2) if cr["max_values"] else None

        t_count = time.perf_counter()
        all_keys = set(sn_runs.keys()) | set(count_runs.keys())
        upserted = 0
        for key in all_keys:
            sn_data = sn_runs.get(key)
            count_data = count_runs.get(key)
            operator = (sn_data or count_data)["operator"]
            tray_id = (sn_data or count_data)["tray_id"]
            analysis_dt = (sn_data or count_data)["analysis_dt"]
            run_name = (sn_data or count_data)["run_name"]
            if sn_data and count_data and count_data["analysis_dt"] < analysis_dt:
                analysis_dt = count_data["analysis_dt"]

            sn_median = sn_min = sn_max = None
            if sn_data and sn_data["sn_values"]:
                vals = sn_data["sn_values"]
                sn_median = round(median(vals), 2)
                sn_min = round(min(vals), 2)
                sn_max = round(max(vals), 2)

            nc_median = nc_min = nc_max = None
            if sn_data and sn_data["nc_values"]:
                vals = sn_data["nc_values"]
                nc_median = round(median(vals), 2)
                nc_min = round(min(vals), 2)
                nc_max = round(max(vals), 2)

            count_med = count_data["count_median"] if count_data else None
            count_min_v = count_data["count_min"] if count_data else None
            count_max_v = count_data["count_max"] if count_data else None

            try:
                normalized_db.execute_non_query(
                    """
                    MERGE dbo.OperatorRunCache AS target
                    USING (SELECT ? AS operator, ? AS tray_id) AS source
                    ON target.operator = source.operator AND target.tray_id = source.tray_id
                    WHEN MATCHED THEN
                        UPDATE SET sn_median=?, sn_min=?, sn_max=?,
                                   nc_median=?, nc_min=?, nc_max=?,
                                   count_median=?, count_min=?, count_max=?,
                                   analysis_dt=?, run_name=?
                    WHEN NOT MATCHED THEN
                        INSERT (operator, tray_id, analysis_dt, run_name,
                                sn_median, sn_min, sn_max,
                                nc_median, nc_min, nc_max,
                                count_median, count_min, count_max)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        operator, tray_id,
                        sn_median, sn_min, sn_max,
                        nc_median, nc_min, nc_max,
                        count_med, count_min_v, count_max_v,
                        analysis_dt, run_name,
                        operator, tray_id, analysis_dt, run_name,
                        sn_median, sn_min, sn_max,
                        nc_median, nc_min, nc_max,
                        count_med, count_min_v, count_max_v,
                    ),
                )
                upserted += 1
            except Exception as e:
                logger.debug("Operator cache upsert failed: %s", e)

        t_total = time.perf_counter() - t0
        logger.info(
            "operator-cache-populate: since=%s | sn_rows=%d count_groups=%d | upserted=%d | sn=%.3fs counts=%.3fs total=%.3fs",
            since_dt,
            len(sn_rows),
            len(count_runs),
            upserted,
            t_sn - t0,
            t_count - t_sn,
            t_total,
        )
        return upserted

    def rebuild_operator_cache(self, normalized_db) -> None:
        normalized_db.execute_non_query("DELETE FROM OperatorRunCache")
        logger.info("operator-cache-rebuild: cleared all")

    def refresh_operator_cache(self, normalized_db, vendor_db, populate_operator_cache_fn: Callable[..., int]) -> None:
        if not vendor_db:
            return
        max_rows = normalized_db.execute_query("SELECT MAX(analysis_dt) as max_dt FROM dbo.OperatorRunCache")
        max_dt = max_rows[0]["max_dt"] if max_rows and max_rows[0]["max_dt"] else None
        if max_dt is None:
            return
        populate_operator_cache_fn(since_dt=max_dt)

    def populate_qc_cache(
        self,
        sqlite_db,
        normalized_db,
        vendor_db,
        catalog_group: str,
        bead_lot: str,
        since_dt: Optional[datetime],
        lot_like_pattern_fn: Callable[[str, str], str],
        extract_catalog_group_fn: Callable[[Optional[str]], Optional[str]],
        extract_bead_lot_fn: Callable[[Optional[str]], Optional[str]],
        select_controls_fn: Callable[..., Dict[str, Optional[float]]],
    ) -> int:
        if not vendor_db:
            logger.warning("populate_qc_cache: vendor_db not available")
            return 0

        definitions = sqlite_db.qc.get_qc_sample_definitions()
        if not definitions:
            return 0

        def _field_matches(value: str) -> Optional[str]:
            v = value.upper()
            for defn in definitions:
                p = defn["pattern"].upper()
                if defn["match_type"] == "exact":
                    if v == p:
                        return defn["role"]
                else:
                    if p in v:
                        return defn["role"]
            return None

        def match_sample(sample_name: Optional[str], patient_id: Optional[str]) -> Optional[str]:
            if patient_id:
                return _field_matches(patient_id)
            if sample_name:
                return _field_matches(sample_name)
            return None

        lot_pattern = lot_like_pattern_fn(catalog_group, bead_lot)
        t0 = time.perf_counter()
        if since_dt:
            query = """
                SELECT t.BuildDT, t.CatalogID, t.TrayID, t.TrayIDName, t.CsvSN,
                       s.SampleIDName, s.PatientID,
                       w.NC1, w.NC2, w.PC1, w.PC2,
                       wd.BeadID, wd.RawData as mfi, wd.CountValue as bead_count
                FROM dbo.WELL w
                JOIN dbo.TRAY t ON t.TrayID = w.TrayID
                JOIN dbo.SAMPLE s ON s.SampleID = w.SampleID
                JOIN dbo.WELL_DETAIL wd ON wd.WellID = w.WellID
                WHERE t.BuildDT > ?
                  AND w.AnalysisDT IS NOT NULL
                  AND w.SampleID IS NOT NULL
                  AND t.CatalogID LIKE ?
            """
            rows = vendor_db.execute_query(query, (since_dt, lot_pattern))
        else:
            query = """
                SELECT t.BuildDT, t.CatalogID, t.TrayID, t.TrayIDName, t.CsvSN,
                       s.SampleIDName, s.PatientID,
                       w.NC1, w.NC2, w.PC1, w.PC2,
                       wd.BeadID, wd.RawData as mfi, wd.CountValue as bead_count
                FROM dbo.WELL w
                JOIN dbo.TRAY t ON t.TrayID = w.TrayID
                JOIN dbo.SAMPLE s ON s.SampleID = w.SampleID
                JOIN dbo.WELL_DETAIL wd ON wd.WellID = w.WellID
                WHERE w.AnalysisDT IS NOT NULL
                  AND w.SampleID IS NOT NULL
                  AND t.CatalogID LIKE ?
            """
            rows = vendor_db.execute_query(query, (lot_pattern,))
        t_sql = time.perf_counter() - t0

        groups: Dict[Any, Dict] = {}
        for row in rows:
            group = extract_catalog_group_fn(row.get("CatalogID"))
            if group != catalog_group:
                continue
            lot = extract_bead_lot_fn(row.get("CatalogID"))
            if lot != bead_lot:
                continue
            sample_name = row.get("SampleIDName")
            patient_id = row.get("PatientID")
            role = match_sample(sample_name, patient_id)
            if role is None:
                continue

            display_name = patient_id if patient_id else sample_name
            tray_id_str = str(row.get("TrayID"))
            key = (tray_id_str, sample_name, patient_id)
            if key not in groups:
                controls = select_controls_fn(row.get("CatalogID"), row.get("NC1"), row.get("NC2"), row.get("PC1"), row.get("PC2"))
                groups[key] = {
                    "tray_id": tray_id_str,
                    "sample_id_name": sample_name,
                    "patient_id": patient_id,
                    "datetime": row["BuildDT"],
                    "run_name": row.get("TrayIDName") or "",
                    "display_name": display_name,
                    "role": role,
                    "instrument": str(row.get("CsvSN") or "").strip(),
                    "pc": float(controls["pc"]) if controls["pc"] is not None else None,
                    "nc": float(controls["nc"]) if controls["nc"] is not None else None,
                    "mfi_values": [],
                    "count_values": [],
                    "bead_mfi": {},
                }
            g = groups[key]
            if row["BuildDT"] < g["datetime"]:
                g["datetime"] = row["BuildDT"]
            mfi_val = row.get("mfi")
            if mfi_val is not None:
                g["mfi_values"].append(float(mfi_val))
            count_val = row.get("bead_count")
            if count_val is not None:
                g["count_values"].append(float(count_val))
            bead_id_str = str(row.get("BeadID", ""))
            if bead_id_str and mfi_val is not None:
                g["bead_mfi"].setdefault(bead_id_str, []).append(float(mfi_val))

        inserted = 0
        for g in groups.values():
            mfi_vals = g["mfi_values"]
            count_vals = g["count_values"]
            pc_val = g["pc"]
            nc_val = g["nc"]
            sn = round(pc_val / nc_val, 2) if (pc_val is not None and nc_val is not None and nc_val > 0) else None
            med_mfi = round(median(mfi_vals), 2) if mfi_vals else None
            med_count = round(median(count_vals), 2) if count_vals else None

            bead_mfi_dict = {}
            for bid, vals in g["bead_mfi"].items():
                bead_mfi_dict[bid] = round(median(vals), 2) if vals else None
            bead_mfi_json = json.dumps(bead_mfi_dict) if bead_mfi_dict else None

            try:
                normalized_db.execute_non_query(
                    """
                    INSERT INTO QCSampleCache
                        (catalog_group, bead_lot, tray_id, sample_id_name, patient_id,
                         analysis_dt, run_name, display_name, role, instrument,
                         pc, nc, median_mfi, median_count, sn_ratio, bead_mfi_json)
                    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    WHERE NOT EXISTS (
                        SELECT 1 FROM QCSampleCache
                        WHERE catalog_group = ? AND bead_lot = ?
                          AND tray_id = ?
                          AND ISNULL(sample_id_name, '') = ISNULL(?, '')
                          AND ISNULL(patient_id, '') = ISNULL(?, '')
                    )
                    """,
                    (
                        catalog_group, bead_lot, g["tray_id"], g["sample_id_name"], g["patient_id"],
                        g["datetime"], g["run_name"], g["display_name"], g["role"], g["instrument"],
                        pc_val, nc_val, med_mfi, med_count, sn, bead_mfi_json,
                        catalog_group, bead_lot, g["tray_id"], g["sample_id_name"], g["patient_id"],
                    ),
                )
                inserted += 1
            except Exception as e:
                logger.debug("QC cache insert skipped (likely duplicate): %s", e)

        logger.info(
            "qc-cache-populate: group=%s lot=%s since=%s | sql=%.3fs rows=%d | inserted=%d",
            catalog_group,
            bead_lot,
            since_dt,
            t_sql,
            len(rows),
            inserted,
        )
        return inserted

    def rebuild_qc_cache(self, normalized_db, catalog_group: Optional[str], bead_lot: Optional[str]) -> None:
        if catalog_group and bead_lot:
            normalized_db.execute_non_query(
                "DELETE FROM QCSampleCache WHERE catalog_group = ? AND bead_lot = ?",
                (catalog_group, bead_lot),
            )
            logger.info("qc-cache-rebuild: cleared group=%s lot=%s", catalog_group, bead_lot)
        else:
            normalized_db.execute_non_query("DELETE FROM QCSampleCache")
            logger.info("qc-cache-rebuild: cleared all")

    def refresh_qc_cache(self, normalized_db, vendor_db, populate_qc_cache_fn: Callable[..., int]) -> None:
        if not vendor_db:
            return
        combos = normalized_db.execute_query("SELECT DISTINCT catalog_group, bead_lot FROM dbo.QCSampleCache")
        if not combos:
            return
        for combo in combos:
            cg = combo["catalog_group"]
            bl = combo["bead_lot"]
            max_rows = normalized_db.execute_query(
                "SELECT MAX(analysis_dt) as max_dt FROM dbo.QCSampleCache WHERE catalog_group = ? AND bead_lot = ?",
                (cg, bl),
            )
            max_dt = max_rows[0]["max_dt"] if max_rows and max_rows[0]["max_dt"] else None
            populate_qc_cache_fn(cg, bl, since_dt=max_dt)
        logger.info("qc-cache-refresh: updated %d group/lot combos", len(combos))
