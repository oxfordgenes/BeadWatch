from typing import Dict, Any, List
import statistics
import logging

logger = logging.getLogger('beadwatch')


class MetricsCalculator:
    """Calculate quality control metrics from raw Luminex assay data"""

    MIN_BEAD_COUNT = 25

    def calculate_qc_metrics(self, raw_data: Dict[str, Any]) -> Dict[str, float]:
        """Calculate standard QC metrics from raw test result"""
        metrics = {}

        # Throughput: each processed record counts as 1
        metrics['records_processed'] = 1

        bead_results = raw_data.get('bead_results', [])

        if not bead_results:
            logger.warning("No bead results in raw_data")
            return metrics

        mfi_values = [b['mfi'] for b in bead_results if b.get('mfi') is not None]
        bead_counts = [b['count'] for b in bead_results if b.get('count') is not None]

        # MFI statistics
        if mfi_values:
            metrics['mean_mfi'] = statistics.mean(mfi_values)
            metrics['median_mfi'] = statistics.median(mfi_values)

            if len(mfi_values) > 1:
                std_dev = statistics.stdev(mfi_values)
                metrics['std_dev_mfi'] = std_dev

            metrics['min_mfi'] = min(mfi_values)
            metrics['max_mfi'] = max(mfi_values)

        # Bead count statistics
        if bead_counts:
            metrics['mean_bead_count'] = statistics.mean(bead_counts)
            metrics['min_bead_count'] = min(bead_counts)
            metrics['total_beads'] = len(bead_counts)

            low_count_beads = sum(1 for c in bead_counts if c < self.MIN_BEAD_COUNT)
            metrics['low_count_bead_percentage'] = (low_count_beads / len(bead_counts)) * 100

        # Control performance
        control_mfi = raw_data.get('control_bead_mfi')
        neg_control_mfi = raw_data.get('negative_control_mfi')
        catalog_group = raw_data.get('catalog_group')
        bead_lot = raw_data.get('bead_lot')

        if control_mfi and neg_control_mfi and neg_control_mfi > 0:
            metrics['signal_to_noise'] = control_mfi / neg_control_mfi

        if control_mfi:
            metrics['positive_control_mfi'] = control_mfi
            self._add_catalog_metrics(metrics, 'positive_control_mfi', control_mfi, catalog_group, bead_lot)
        if neg_control_mfi:
            metrics['negative_control_mfi'] = neg_control_mfi

        return metrics

    @staticmethod
    def _sanitize_metric_suffix(value: str) -> str:
        if value is None:
            return None
        return ''.join(ch for ch in str(value).upper() if ch.isalnum())

    def _add_catalog_metrics(self, metrics: Dict[str, float], base_name: str, value: float, catalog_group: str, bead_lot: str):
        group = self._sanitize_metric_suffix(catalog_group)
        lot = self._sanitize_metric_suffix(bead_lot)
        if group:
            metrics[f"{base_name}__catalog_{group}"] = value
            if lot:
                metrics[f"{base_name}__catalog_{group}__lot_{lot}"] = value

    def calculate_rolling_statistics(self, metric_name: str, window_days: int, normalized_db) -> Dict[str, float]:
        """Calculate rolling statistics for a metric over specified window"""
        if window_days == 0:
            query = """
                SELECT qm.metric_value
                FROM QCMetrics qm
                JOIN ProcessedRecords pr ON qm.processed_record_id = pr.id
                WHERE qm.metric_name = ?
                  AND qm.metric_value IS NOT NULL
            """
            results = normalized_db.execute_query(query, (metric_name,))
        else:
            query = """
                SELECT qm.metric_value
                FROM QCMetrics qm
                JOIN ProcessedRecords pr ON qm.processed_record_id = pr.id
                WHERE qm.metric_name = ?
                  AND pr.vendor_record_timestamp >= DATEADD(day, -?, GETDATE())
                  AND qm.metric_value IS NOT NULL
            """
            results = normalized_db.execute_query(query, (metric_name, window_days))
        values = [row['metric_value'] for row in results]

        if not values:
            return {}

        stats = {
            'mean': statistics.mean(values),
            'min': min(values),
            'max': max(values),
            'count': len(values)
        }

        if len(values) > 1:
            stats['std_dev'] = statistics.stdev(values)
        else:
            stats['std_dev'] = 0

        return stats
