from typing import List, Dict, Any
from datetime import datetime
import logging

from .base_adapter import BaseVendorAdapter

logger = logging.getLogger('beadwatch')


class FusionAdapter(BaseVendorAdapter):
    """
    Adapter for OneLambda Fusion database schema.

    Normalized raw_data structure expected by MetricsCalculator:
    {
        'sample_id': 'ABC123',
        'assay_type': 'CatalogID or AssayType',
        'bead_results': [
            {'bead_id': '001', 'mfi': 1250, 'count': 55},
            ...
        ],
        'control_bead_mfi': 15000,
        'negative_control_mfi': 150
    }
    """

    def __init__(self, db_connection):
        super().__init__(db_connection)
        self.vendor_name = "OneLambdaFusion"

    def get_new_records(self, last_check_timestamp: datetime) -> List[Dict[str, Any]]:
        """Query Fusion database for new well results since last check."""
        query = """
            SELECT
                w.WellID as uuid,
                COALESCE(w.ConfirmDT, w.AnalysisDT) as record_timestamp,
                s.SampleIDName as sample_id,
                t.CatalogID as catalog_id,
                w.NC1, w.NC2, w.PC1, w.PC2
            FROM dbo.WELL w
            LEFT JOIN dbo.SAMPLE s ON w.SampleID = s.SampleID
            LEFT JOIN dbo.TRAY t ON w.TrayID = t.TrayID
            WHERE (w.AnalysisDT IS NOT NULL OR w.ConfirmDT IS NOT NULL)
              AND COALESCE(w.ConfirmDT, w.AnalysisDT) >= ?
              AND w.IsActive = 1
              AND w.Excluded = 0
              AND (w.QCExclude = 0 OR w.QCExclude IS NULL)
            ORDER BY COALESCE(w.ConfirmDT, w.AnalysisDT) ASC
        """

        results = self.db.execute_query(query, (last_check_timestamp,))

        processed_records: List[Dict[str, Any]] = []
        for row in results:
            bead_results = self._get_bead_results(row['uuid'])

            catalog_id = row.get('catalog_id')
            control_mfi = self._select_positive_control(catalog_id, row.get('PC1'), row.get('PC2'))
            neg_control_mfi = self._avg_non_null(row.get('NC1'), row.get('NC2'))

            processed_records.append({
                'uuid': str(row['uuid']),
                'timestamp': row['record_timestamp'],
                'raw_data': {
                    'sample_id': row.get('sample_id') or str(row['uuid']),
                    'assay_type': catalog_id or 'Fusion',
                    'catalog_id': catalog_id,
                    'catalog_group': self._derive_catalog_group(catalog_id),
                    'bead_lot': self._extract_bead_lot(catalog_id),
                    'bead_results': bead_results,
                    'control_bead_mfi': control_mfi,
                    'negative_control_mfi': neg_control_mfi
                }
            })

        logger.info(f"Fusion: Found {len(processed_records)} new records")
        return processed_records

    def _get_bead_results(self, well_id: str) -> List[Dict[str, Any]]:
        """Get individual bead measurements for a well."""
        query = """
            SELECT BeadID as bead_id, RawData as mfi, CountValue as count
            FROM dbo.WELL_DETAIL
            WHERE WellID = ?
            ORDER BY BeadID
        """
        return self.db.execute_query(query, (well_id,))

    def get_test_result_data(self, record_uuid: str) -> Dict[str, Any]:
        """Retrieve full details for a specific well record."""
        query = """
            SELECT
                w.WellID as uuid,
                s.SampleIDName as sample_id,
                t.CatalogID as catalog_id,
                COALESCE(w.ConfirmDT, w.AnalysisDT) as record_timestamp,
                w.PC1, w.PC2, w.NC1, w.NC2
            FROM dbo.WELL w
            LEFT JOIN dbo.SAMPLE s ON w.SampleID = s.SampleID
            LEFT JOIN dbo.TRAY t ON w.TrayID = t.TrayID
            WHERE w.WellID = ?
        """
        results = self.db.execute_query(query, (record_uuid,))

        if not results:
            return None

        row = results[0]
        catalog_id = row.get('catalog_id')
        bead_results = self._get_bead_results(row['uuid'])
        return {
            'uuid': str(row['uuid']),
            'sample_id': row.get('sample_id') or str(row['uuid']),
            'assay_type': catalog_id or 'Fusion',
            'catalog_id': catalog_id,
            'catalog_group': self._derive_catalog_group(catalog_id),
            'bead_lot': self._extract_bead_lot(catalog_id),
            'timestamp': row['record_timestamp'],
            'control_bead_mfi': self._select_positive_control(catalog_id, row.get('PC1'), row.get('PC2')),
            'negative_control_mfi': self._avg_non_null(row.get('NC1'), row.get('NC2')),
            'bead_results': bead_results
        }

    def get_bead_definitions(self) -> List[Dict[str, Any]]:
        """Get bead reference data."""
        query = """
            SELECT BeadID as bead_id, Alpha as alpha, Beta as beta
            FROM dbo.BEAD_SPEC
            ORDER BY BeadID
        """
        rows = self.db.execute_query(query)
        return [
            {
                'bead_id': row['bead_id'],
                'name': f"{row.get('alpha', '')}{row.get('beta', '')}".strip(),
                'specificity': None,
                'assay_type': None
            }
            for row in rows
        ]

    @staticmethod
    def _avg_non_null(*values):
        nums = [v for v in values if v is not None]
        if not nums:
            return None
        return sum(nums) / len(nums)

    @staticmethod
    def _select_positive_control(catalog_id: str, pc1, pc2):
        """Use PC2 for LS2* catalogs, otherwise PC1. Fallback to whichever exists."""
        if not catalog_id:
            return pc1 if pc1 is not None else pc2
        catalog_base = str(catalog_id).split('.')[0].upper()
        if catalog_base.startswith('LS2'):
            return pc2 if pc2 is not None else pc1
        return pc1 if pc1 is not None else pc2

    @staticmethod
    def _extract_bead_lot(catalog_id: str):
        """Extract bead lot as the middle underscore segment (e.g., LS1A04NC12_008_02 -> 008)."""
        if not catalog_id:
            return None
        catalog_base = str(catalog_id).split('.')[0]
        parts = catalog_base.split('_')
        if len(parts) >= 2:
            return parts[1]
        return None

    @staticmethod
    def _derive_catalog_group(catalog_id: str):
        """Derive high-level catalog group (e.g., LS1A04, LS2A01, LSM12, LSMMulti)."""
        if not catalog_id:
            return None
        catalog_base = str(catalog_id).split('.')[0].upper()
        for prefix in ("LS1A04", "LS2A01", "LSM12", "LSMMULTI"):
            if catalog_base.startswith(prefix):
                return prefix
        # Fallback to base before underscore
        return catalog_base.split('_')[0]
