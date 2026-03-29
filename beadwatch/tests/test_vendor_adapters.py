import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from services.vendor_adapters.base_adapter import BaseVendorAdapter
from services.vendor_adapters.mock_adapter import MockVendorAdapter


class TestBaseVendorAdapter:
    def test_cannot_instantiate_directly(self):
        """BaseVendorAdapter is abstract and should not be instantiated directly"""
        with pytest.raises(TypeError):
            BaseVendorAdapter(db_connection=MagicMock())

    def test_concrete_subclass_must_implement_abstract_methods(self):
        """A subclass missing abstract methods should raise TypeError"""

        class IncompleteAdapter(BaseVendorAdapter):
            pass

        with pytest.raises(TypeError):
            IncompleteAdapter(db_connection=MagicMock())

    def test_test_connection_success(self):
        """test_connection delegates to db.execute_query and returns True on success"""

        class MinimalAdapter(BaseVendorAdapter):
            def get_new_records(self, last_check_timestamp):
                return []

            def get_test_result_data(self, record_uuid):
                return {}

            def get_bead_definitions(self):
                return []

        mock_db = MagicMock()
        mock_db.execute_query.return_value = [{'test': 1}]
        adapter = MinimalAdapter(mock_db)
        assert adapter.test_connection() is True
        mock_db.execute_query.assert_called_once_with("SELECT 1")

    def test_test_connection_failure(self):
        """test_connection returns False when the database raises an exception"""

        class MinimalAdapter(BaseVendorAdapter):
            def get_new_records(self, last_check_timestamp):
                return []

            def get_test_result_data(self, record_uuid):
                return {}

            def get_bead_definitions(self):
                return []

        mock_db = MagicMock()
        mock_db.execute_query.side_effect = Exception("Connection refused")
        adapter = MinimalAdapter(mock_db)
        assert adapter.test_connection() is False


class TestMockVendorAdapter:
    @pytest.fixture
    def adapter(self):
        return MockVendorAdapter()

    def test_inherits_base(self, adapter):
        assert isinstance(adapter, BaseVendorAdapter)

    def test_vendor_name(self, adapter):
        assert adapter.vendor_name == "MockVendor"

    def test_get_new_records_returns_list(self, adapter):
        ts = datetime.now() - timedelta(hours=1)
        records = adapter.get_new_records(ts)
        assert isinstance(records, list)
        assert 2 <= len(records) <= 5

    def test_record_structure(self, adapter):
        ts = datetime.now() - timedelta(hours=1)
        records = adapter.get_new_records(ts)
        record = records[0]
        assert 'uuid' in record
        assert 'timestamp' in record
        assert 'raw_data' in record
        raw = record['raw_data']
        assert 'sample_id' in raw
        assert 'bead_results' in raw
        assert 'control_bead_mfi' in raw
        assert 'negative_control_mfi' in raw

    def test_records_have_unique_uuids(self, adapter):
        ts = datetime.now() - timedelta(hours=1)
        records = adapter.get_new_records(ts)
        uuids = [r['uuid'] for r in records]
        assert len(uuids) == len(set(uuids))

    def test_record_timestamps_after_last_check(self, adapter):
        ts = datetime.now() - timedelta(hours=1)
        records = adapter.get_new_records(ts)
        for record in records:
            assert record['timestamp'] > ts

    def test_get_test_result_data(self, adapter):
        result = adapter.get_test_result_data("some-uuid")
        assert result['uuid'] == "some-uuid"

    def test_get_bead_definitions(self, adapter):
        beads = adapter.get_bead_definitions()
        assert isinstance(beads, list)
        assert len(beads) == 100
        assert beads[0]['bead_id'] == 1

    def test_test_connection_always_true(self, adapter):
        assert adapter.test_connection() is True

    def test_counter_increments(self, adapter):
        ts = datetime.now()
        adapter.get_new_records(ts)
        first_count = adapter._record_counter
        adapter.get_new_records(ts)
        assert adapter._record_counter > first_count
