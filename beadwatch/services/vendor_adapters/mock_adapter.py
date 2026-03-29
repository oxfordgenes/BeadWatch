from typing import List, Dict, Any
from datetime import datetime, timedelta
import random
import uuid as uuid_module

from .base_adapter import BaseVendorAdapter


class MockVendorAdapter(BaseVendorAdapter):
    """Mock adapter that generates synthetic test data for development/testing"""

    def __init__(self, db_connection=None):
        super().__init__(db_connection)
        self.vendor_name = "MockVendor"
        self._record_counter = 0

    def get_new_records(self, last_check_timestamp: datetime) -> List[Dict[str, Any]]:
        """Generate 2-5 synthetic test records"""
        num_records = random.randint(2, 5)
        records = []

        for i in range(num_records):
            self._record_counter += 1
            record_time = last_check_timestamp + timedelta(minutes=random.randint(1, 60))

            bead_results = []
            for bead_id in range(1, random.randint(50, 100)):
                bead_results.append({
                    'bead_id': bead_id,
                    'mfi': random.gauss(1500, 500),
                    'count': random.randint(40, 80)
                })

            records.append({
                'uuid': str(uuid_module.uuid4()),
                'timestamp': record_time,
                'raw_data': {
                    'sample_id': f'SAMPLE-{self._record_counter:05d}',
                    'assay_type': random.choice(['SAB Class I', 'SAB Class II', 'Screen']),
                    'bead_results': bead_results,
                    'control_bead_mfi': random.gauss(15000, 1000),
                    'negative_control_mfi': random.gauss(150, 30)
                }
            })

        return records

    def get_test_result_data(self, record_uuid: str) -> Dict[str, Any]:
        return {'uuid': record_uuid, 'sample_id': 'MOCK-SAMPLE', 'assay_type': 'SAB Class I'}

    def get_bead_definitions(self) -> List[Dict[str, Any]]:
        return [{'bead_id': i, 'name': f'Bead-{i}', 'specificity': f'HLA-A*{i:02d}'} for i in range(1, 101)]

    def test_connection(self) -> bool:
        return True
