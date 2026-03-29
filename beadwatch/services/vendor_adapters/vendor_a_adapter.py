from typing import List, Dict, Any
from datetime import datetime
import logging

from .base_adapter import BaseVendorAdapter

logger = logging.getLogger('beadwatch')


class VendorAAdapter(BaseVendorAdapter):
    """
    Adapter for Vendor A's database schema.

    Expected raw_data structure for metrics calculation:
    {
        'sample_id': 'ABC123',
        'assay_type': 'SAB Class I',
        'bead_results': [
            {'bead_id': 1, 'mfi': 1250, 'count': 55},
            {'bead_id': 2, 'mfi': 890, 'count': 62},
            ...
        ],
        'control_bead_mfi': 15000,
        'negative_control_mfi': 150
    }
    """

    def __init__(self, db_connection):
        super().__init__(db_connection)
        self.vendor_name = "VendorA"

    def get_new_records(self, last_check_timestamp: datetime) -> List[Dict[str, Any]]:
        """Query Vendor A database for new test results"""
        query = """
            SELECT
                tr.ResultGUID as uuid,
                tr.CreatedDateTime as timestamp,
                tr.ResultID,
                tr.SampleID,
                tr.AssayType,
                tr.PositiveControlMFI,
                tr.NegativeControlMFI
            FROM dbo.TestResults tr
            WHERE tr.CreatedDateTime >= ?
            ORDER BY tr.CreatedDateTime ASC
        """

        results = self.db.execute_query(query, (last_check_timestamp,))

        processed_records = []
        for row in results:
            bead_results = self._get_bead_results(row['ResultID'])

            processed_records.append({
                'uuid': str(row['uuid']),
                'timestamp': row['timestamp'],
                'raw_data': {
                    'sample_id': row['SampleID'],
                    'assay_type': row['AssayType'],
                    'bead_results': bead_results,
                    'control_bead_mfi': row['PositiveControlMFI'],
                    'negative_control_mfi': row['NegativeControlMFI']
                }
            })

        logger.info(f"VendorA: Found {len(processed_records)} new records")
        return processed_records

    def _get_bead_results(self, result_id: int) -> List[Dict[str, Any]]:
        """Get individual bead measurements for a test result"""
        query = """
            SELECT BeadID as bead_id, MFIValue as mfi, BeadCount as count
            FROM dbo.BeadResults
            WHERE ResultID = ?
            ORDER BY BeadID
        """
        return self.db.execute_query(query, (result_id,))

    def get_test_result_data(self, record_uuid: str) -> Dict[str, Any]:
        """Retrieve full details for a specific test result"""
        query = """
            SELECT tr.ResultGUID, tr.SampleID, tr.AssayType, tr.CreatedDateTime,
                   tr.PositiveControlMFI, tr.NegativeControlMFI, tr.OperatorID, tr.InstrumentID
            FROM dbo.TestResults tr
            WHERE tr.ResultGUID = ?
        """
        results = self.db.execute_query(query, (record_uuid,))

        if not results:
            return None

        row = results[0]
        return {
            'uuid': str(row['ResultGUID']),
            'sample_id': row['SampleID'],
            'assay_type': row['AssayType'],
            'timestamp': row['CreatedDateTime'],
            'control_bead_mfi': row['PositiveControlMFI'],
            'negative_control_mfi': row['NegativeControlMFI']
        }

    def get_bead_definitions(self) -> List[Dict[str, Any]]:
        """Get bead reference data"""
        query = """
            SELECT BeadID as bead_id, BeadName as name, Specificity as specificity, AssayType as assay_type
            FROM dbo.BeadDefinitions
            WHERE Active = 1
            ORDER BY BeadID
        """
        return self.db.execute_query(query)
