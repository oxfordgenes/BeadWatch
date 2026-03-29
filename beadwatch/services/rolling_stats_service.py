class RollingStatsService:
    """Updates rolling statistics for changed QC metrics."""

    def update_rolling_stats(self, metrics_calc, normalized_db, changed_metrics: set) -> None:
        if not changed_metrics:
            return

        windows = [7, 30, 365, 0]
        for metric_name in changed_metrics:
            for window_days in windows:
                stats = metrics_calc.calculate_rolling_statistics(metric_name, window_days, normalized_db)
                if not stats:
                    continue

                p = {
                    "name": metric_name,
                    "window": window_days,
                    "mean": stats["mean"],
                    "std": stats["std_dev"],
                    "min": stats["min"],
                    "max": stats["max"],
                    "count": stats["count"],
                }

                upsert_query = """
                    MERGE RollingStats AS target
                    USING (SELECT ? AS metric_name, ? AS window_days) AS source
                    ON target.metric_name = source.metric_name
                       AND target.window_days = source.window_days
                    WHEN MATCHED THEN
                        UPDATE SET mean_value = ?, std_dev = ?,
                                   min_value = ?, max_value = ?,
                                   record_count = ?, updated_at = GETDATE()
                    WHEN NOT MATCHED THEN
                        INSERT (metric_name, window_days, mean_value, std_dev,
                                min_value, max_value, record_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?);
                """

                normalized_db.execute_non_query(
                    upsert_query,
                    (
                        p["name"],
                        p["window"],
                        p["mean"],
                        p["std"],
                        p["min"],
                        p["max"],
                        p["count"],
                        p["name"],
                        p["window"],
                        p["mean"],
                        p["std"],
                        p["min"],
                        p["max"],
                        p["count"],
                    ),
                )
