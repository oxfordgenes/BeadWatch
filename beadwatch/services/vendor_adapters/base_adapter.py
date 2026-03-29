from abc import ABC, abstractmethod
from typing import List, Dict, Any
from datetime import datetime


class BaseVendorAdapter(ABC):
    """Abstract base class for vendor-specific database adapters"""

    def __init__(self, db_connection):
        self.db = db_connection
        self.vendor_name: str = "Unknown"

    @abstractmethod
    def get_new_records(self, last_check_timestamp: datetime) -> List[Dict[str, Any]]:
        """
        Query vendor database for new records since last check.

        Returns: List of dicts with structure:
            {
                'uuid': str,
                'timestamp': datetime,
                'raw_data': dict  # Normalized data for metrics calculation
            }
        """
        pass

    @abstractmethod
    def get_test_result_data(self, record_uuid: str) -> Dict[str, Any]:
        """Retrieve detailed test result data for a specific record"""
        pass

    @abstractmethod
    def get_bead_definitions(self) -> List[Dict[str, Any]]:
        """Get reference data about bead types and assay configurations"""
        pass

    def test_connection(self) -> bool:
        """Test if vendor database is accessible"""
        try:
            self.db.execute_query("SELECT 1")
            return True
        except Exception:
            return False
