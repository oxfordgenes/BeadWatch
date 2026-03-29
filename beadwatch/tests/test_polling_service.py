import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

from services.polling_service import PollingService


@pytest.fixture
def mock_sqlite():
    db = MagicMock()
    db.app_state.get_state.return_value = None  # First poll by default
    return db


@pytest.fixture
def mock_vendor():
    adapter = MagicMock()
    adapter.vendor_name = "TestVendor"
    adapter.get_new_records.return_value = []
    return adapter


@pytest.fixture
def mock_normalized_db():
    return MagicMock()


@pytest.fixture
def service(mock_sqlite, mock_vendor, mock_normalized_db):
    return PollingService(mock_sqlite, mock_vendor, mock_normalized_db)


class TestCheckForNewRecords:
    def test_records_heartbeat(self, service, mock_sqlite):
        """Every poll cycle should record a scheduler heartbeat"""
        service.check_for_new_records()
        heartbeat_calls = [
            c for c in mock_sqlite.app_state.set_state.call_args_list
            if c[0][0] == 'scheduler_last_heartbeat'
        ]
        assert len(heartbeat_calls) >= 1

    def test_first_poll_uses_lookback(self, service, mock_sqlite, mock_vendor):
        """When no last_poll_timestamp exists, uses INITIAL_LOOKBACK_HOURS"""
        mock_sqlite.app_state.get_state.side_effect = lambda key: None
        service.check_for_new_records()
        mock_vendor.get_new_records.assert_called_once()
        call_args = mock_vendor.get_new_records.call_args[0][0]
        assert isinstance(call_args, datetime)
        # Should be roughly 24 hours ago
        assert (datetime.now() - call_args).total_seconds() > 23 * 3600

    def test_subsequent_poll_uses_saved_timestamp(self, service, mock_sqlite, mock_vendor):
        """When last_poll_timestamp exists, uses that timestamp"""
        saved_ts = (datetime.now() - timedelta(minutes=5)).isoformat()
        mock_sqlite.app_state.get_state.side_effect = lambda key: {
            'last_poll_timestamp': saved_ts
        }.get(key)
        service.check_for_new_records()
        call_args = mock_vendor.get_new_records.call_args[0][0]
        assert abs((call_args - datetime.fromisoformat(saved_ts)).total_seconds()) < 1

    def test_no_new_records_does_not_advance_timestamp(self, service, mock_sqlite, mock_vendor):
        """When no records found, cursor must NOT advance to now() to avoid skipping late-arriving records"""
        mock_vendor.get_new_records.return_value = []
        service.check_for_new_records()
        ts_calls = [c for c in mock_sqlite.app_state.set_state.call_args_list if c[0][0] == 'last_poll_timestamp']
        assert len(ts_calls) == 0

    def test_outlier_check_runs_even_without_new_records(self, service, mock_vendor):
        """Outlier detection must run every poll cycle, not just when new records arrive"""
        mock_vendor.get_new_records.return_value = []
        with patch.object(service, 'refresh_qc_cache') as mock_refresh, \
             patch.object(service, 'check_qc_sample_outliers') as mock_outliers, \
             patch.object(service, 'refresh_instrument_cache'):
            service.check_for_new_records()
            mock_refresh.assert_called_once()
            mock_outliers.assert_called_once()

    def test_processes_each_record(self, service, mock_sqlite, mock_vendor, mock_normalized_db):
        """Each record in the batch should be processed"""
        records = [
            {'uuid': 'uuid-1', 'timestamp': datetime.now(), 'raw_data': {'bead_results': []}},
            {'uuid': 'uuid-2', 'timestamp': datetime.now(), 'raw_data': {'bead_results': []}},
        ]
        mock_vendor.get_new_records.return_value = records
        with patch.object(service, '_process_record', return_value={'mean_mfi': 100}):
            service.check_for_new_records()
            assert service._process_record.call_count == 2

    def test_advances_timestamp_to_last_successful(self, service, mock_sqlite, mock_vendor):
        """Timestamp should advance to the last successfully processed record"""
        ts1 = datetime(2024, 1, 1, 10, 0)
        ts2 = datetime(2024, 1, 1, 11, 0)
        records = [
            {'uuid': 'uuid-1', 'timestamp': ts1, 'raw_data': {'bead_results': []}},
            {'uuid': 'uuid-2', 'timestamp': ts2, 'raw_data': {'bead_results': []}},
        ]
        mock_vendor.get_new_records.return_value = records
        with patch.object(service, '_process_record', return_value={'mean_mfi': 100}):
            service.check_for_new_records()
        ts_calls = [c for c in mock_sqlite.app_state.set_state.call_args_list if c[0][0] == 'last_poll_timestamp']
        saved_ts = ts_calls[-1][0][1]
        assert saved_ts == ts2.isoformat()

    def test_all_duplicates_still_advances_cursor(self, service, mock_sqlite, mock_vendor):
        """When all records are duplicates, cursor should still advance to prevent re-fetch loop"""
        ts_max = datetime(2024, 1, 1, 12, 0)
        records = [
            {'uuid': 'uuid-1', 'timestamp': datetime(2024, 1, 1, 10, 0), 'raw_data': {'bead_results': []}},
            {'uuid': 'uuid-2', 'timestamp': ts_max, 'raw_data': {'bead_results': []}},
        ]
        mock_vendor.get_new_records.return_value = records
        # _process_record returns None = duplicate
        with patch.object(service, '_process_record', return_value=None):
            service.check_for_new_records()
        ts_calls = [c for c in mock_sqlite.app_state.set_state.call_args_list if c[0][0] == 'last_poll_timestamp']
        saved_ts = ts_calls[-1][0][1]
        assert saved_ts == ts_max.isoformat()

    def test_error_in_one_record_continues_processing(self, service, mock_sqlite, mock_vendor):
        """An error processing one record should not stop processing others"""
        records = [
            {'uuid': 'uuid-1', 'timestamp': datetime.now(), 'raw_data': {'bead_results': []}},
            {'uuid': 'uuid-2', 'timestamp': datetime.now(), 'raw_data': {'bead_results': []}},
        ]
        mock_vendor.get_new_records.return_value = records
        call_count = 0

        def side_effect(record):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Processing error")
            return {'mean_mfi': 100}

        with patch.object(service, '_process_record', side_effect=side_effect):
            service.check_for_new_records()
        assert call_count == 2

    def test_calls_update_rolling_stats_on_success(self, service, mock_sqlite, mock_vendor):
        """Rolling stats should be updated when at least one record was processed"""
        records = [
            {'uuid': 'uuid-1', 'timestamp': datetime.now(), 'raw_data': {'bead_results': []}},
        ]
        mock_vendor.get_new_records.return_value = records
        with patch.object(service, '_process_record', return_value={'mean_mfi': 100, 'cv_percentage': 5.0}):
            with patch.object(service, '_update_rolling_stats') as mock_update:
                service.check_for_new_records()
                mock_update.assert_called_once_with({'mean_mfi', 'cv_percentage'})

    def test_does_not_call_rolling_stats_when_all_skipped(self, service, mock_sqlite, mock_vendor):
        """Rolling stats should NOT be updated if no records were newly processed"""
        records = [
            {'uuid': 'uuid-1', 'timestamp': datetime.now(), 'raw_data': {'bead_results': []}},
        ]
        mock_vendor.get_new_records.return_value = records
        with patch.object(service, '_process_record', return_value=None):
            with patch.object(service, '_update_rolling_stats') as mock_update:
                service.check_for_new_records()
                mock_update.assert_not_called()

    def test_all_errors_does_not_advance_cursor(self, service, mock_sqlite, mock_vendor):
        """When every record errors, cursor must NOT advance so they can be retried"""
        records = [
            {'uuid': 'uuid-1', 'timestamp': datetime(2024, 1, 1, 10, 0), 'raw_data': {'bead_results': []}},
            {'uuid': 'uuid-2', 'timestamp': datetime(2024, 1, 1, 11, 0), 'raw_data': {'bead_results': []}},
        ]
        mock_vendor.get_new_records.return_value = records
        with patch.object(service, '_process_record', side_effect=Exception("db error")):
            service.check_for_new_records()
        ts_calls = [c for c in mock_sqlite.app_state.set_state.call_args_list if c[0][0] == 'last_poll_timestamp']
        assert len(ts_calls) == 0

    def test_mixed_errors_and_duplicates_does_not_advance_cursor(self, service, mock_sqlite, mock_vendor):
        """When some records error and the rest are duplicates (no successes), cursor must NOT advance"""
        records = [
            {'uuid': 'uuid-1', 'timestamp': datetime(2024, 1, 1, 10, 0), 'raw_data': {'bead_results': []}},
            {'uuid': 'uuid-2', 'timestamp': datetime(2024, 1, 1, 11, 0), 'raw_data': {'bead_results': []}},
        ]
        mock_vendor.get_new_records.return_value = records
        call_count = 0

        def side_effect(record):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("db error")
            return None  # duplicate

        with patch.object(service, '_process_record', side_effect=side_effect):
            service.check_for_new_records()
        ts_calls = [c for c in mock_sqlite.app_state.set_state.call_args_list if c[0][0] == 'last_poll_timestamp']
        assert len(ts_calls) == 0


class TestIsValidOperator:
    """Tests for _is_valid_operator static method."""

    def test_none_is_invalid(self, service):
        assert PollingService._is_valid_operator(None) is False

    def test_empty_string_is_invalid(self, service):
        assert PollingService._is_valid_operator('') is False

    def test_whitespace_only_is_invalid(self, service):
        assert PollingService._is_valid_operator('   ') is False

    def test_normal_username_is_valid(self, service):
        assert PollingService._is_valid_operator('JDOE') is True

    def test_username_with_whitespace_is_valid(self, service):
        assert PollingService._is_valid_operator('  jdoe  ') is True


class TestOperatorCacheLifecycle:
    """Tests for operator cache populate/rebuild/refresh lifecycle."""

    def test_populate_skips_when_already_populated(self, service, mock_normalized_db):
        """populate_operator_cache should no-op when cache already has rows."""
        mock_normalized_db.execute_query.return_value = [{'n': 10}]
        result = service.populate_operator_cache()
        assert result == 0
        # Should NOT call vendor_db
        mock_normalized_db.execute_non_query.assert_not_called()

    def test_populate_skips_without_vendor_db(self, service):
        """Should return 0 when vendor_db is not available."""
        service.vendor_db = None
        result = service.populate_operator_cache()
        assert result == 0

    def test_rebuild_clears_cache(self, service, mock_normalized_db):
        """rebuild_operator_cache should DELETE all rows (lazy repopulate on next request)."""
        service.rebuild_operator_cache()
        mock_normalized_db.execute_non_query.assert_called_once()
        call_sql = mock_normalized_db.execute_non_query.call_args[0][0]
        assert 'DELETE' in call_sql
        assert 'OperatorRunCache' in call_sql

    def test_refresh_calls_populate_with_since_dt(self, service, mock_normalized_db):
        """refresh_operator_cache should call populate_operator_cache with since_dt from latest cache row."""
        service.vendor_db = MagicMock()  # must be truthy for refresh to proceed
        mock_normalized_db.execute_query.return_value = [
            {'max_dt': datetime(2024, 6, 1, 12, 0)}
        ]
        with patch.object(service, 'populate_operator_cache', return_value=2) as mock_pop:
            service.refresh_operator_cache()
        mock_pop.assert_called_once_with(since_dt=datetime(2024, 6, 1, 12, 0))

    def test_refresh_noop_when_cache_empty(self, service, mock_normalized_db):
        """When cache is empty, refresh returns early (no incremental refresh possible)."""
        service.vendor_db = MagicMock()
        mock_normalized_db.execute_query.return_value = [{'max_dt': None}]
        with patch.object(service, 'populate_operator_cache') as mock_pop:
            service.refresh_operator_cache()
        mock_pop.assert_not_called()

    def test_refresh_noop_without_vendor_db(self, service, mock_normalized_db):
        """When vendor_db is None, refresh returns early."""
        service.vendor_db = None
        service.refresh_operator_cache()
        mock_normalized_db.execute_query.assert_not_called()


class TestOperatorCacheInPollingLoop:
    """Verify operator cache is refreshed during polling loop."""

    def test_polling_loop_calls_refresh_operator_cache(self, service, mock_vendor):
        mock_vendor.get_new_records.return_value = []
        with patch.object(service, 'refresh_qc_cache'), \
             patch.object(service, 'check_qc_sample_outliers'), \
             patch.object(service, 'refresh_instrument_cache'), \
             patch.object(service, 'refresh_operator_cache') as mock_op_refresh:
            service.check_for_new_records()
            mock_op_refresh.assert_called_once()


class TestCheckQCThresholds:
    def test_creates_critical_alert_upper(self, service):
        """A value above upper_critical should trigger a critical alert"""
        cursor = MagicMock()
        # Simulate a pyodbc Row with attribute access
        threshold = MagicMock()
        threshold.metric_name = 'mean_mfi'
        threshold.upper_warning = 2000
        threshold.upper_critical = 3000
        threshold.lower_warning = None
        threshold.lower_critical = None
        cursor.fetchall.return_value = [threshold]

        with patch.object(service, '_create_alert') as mock_alert:
            service._check_qc_thresholds(cursor, 1, {'mean_mfi': 3500})
            mock_alert.assert_called_once_with(cursor, 1, 'mean_mfi', 'upper', 3000, 3500, 'critical',
                                               vendor_uuid=None, vendor_ts=None, sample_name=None)

    def test_creates_warning_alert_upper(self, service):
        """A value above upper_warning but below upper_critical should trigger a warning"""
        cursor = MagicMock()
        threshold = MagicMock()
        threshold.metric_name = 'mean_mfi'
        threshold.upper_warning = 2000
        threshold.upper_critical = 3000
        threshold.lower_warning = None
        threshold.lower_critical = None
        cursor.fetchall.return_value = [threshold]

        with patch.object(service, '_create_alert') as mock_alert:
            service._check_qc_thresholds(cursor, 1, {'mean_mfi': 2500})
            mock_alert.assert_called_once_with(cursor, 1, 'mean_mfi', 'upper', 2000, 2500, 'warning',
                                               vendor_uuid=None, vendor_ts=None, sample_name=None)

    def test_creates_lower_critical_alert(self, service):
        """A value below lower_critical should trigger a critical alert"""
        cursor = MagicMock()
        threshold = MagicMock()
        threshold.metric_name = 'min_bead_count'
        threshold.upper_warning = None
        threshold.upper_critical = None
        threshold.lower_warning = 30
        threshold.lower_critical = 15
        cursor.fetchall.return_value = [threshold]

        with patch.object(service, '_create_alert') as mock_alert:
            service._check_qc_thresholds(cursor, 1, {'min_bead_count': 10})
            mock_alert.assert_called_once_with(cursor, 1, 'min_bead_count', 'lower', 15, 10, 'critical',
                                               vendor_uuid=None, vendor_ts=None, sample_name=None)

    def test_no_alert_within_normal_range(self, service):
        """Values within threshold bounds should not trigger any alert"""
        cursor = MagicMock()
        threshold = MagicMock()
        threshold.metric_name = 'mean_mfi'
        threshold.upper_warning = 2000
        threshold.upper_critical = 3000
        threshold.lower_warning = 500
        threshold.lower_critical = 100
        cursor.fetchall.return_value = [threshold]

        with patch.object(service, '_create_alert') as mock_alert:
            service._check_qc_thresholds(cursor, 1, {'mean_mfi': 1500})
            mock_alert.assert_not_called()

    def test_skips_metrics_not_in_thresholds(self, service):
        """Metrics without threshold definitions should be silently skipped"""
        cursor = MagicMock()
        threshold = MagicMock()
        threshold.metric_name = 'mean_mfi'
        threshold.upper_warning = 2000
        threshold.upper_critical = 3000
        threshold.lower_warning = None
        threshold.lower_critical = None
        cursor.fetchall.return_value = [threshold]

        with patch.object(service, '_create_alert') as mock_alert:
            service._check_qc_thresholds(cursor, 1, {'cv_percentage': 50.0})
            mock_alert.assert_not_called()

    def test_zero_threshold_not_treated_as_none(self, service):
        """A threshold value of 0.0 should be active, not skipped"""
        cursor = MagicMock()
        threshold = MagicMock()
        threshold.metric_name = 'signal_to_noise'
        threshold.upper_warning = None
        threshold.upper_critical = None
        threshold.lower_warning = 5.0
        threshold.lower_critical = 0.0  # Zero, not None
        cursor.fetchall.return_value = [threshold]

        with patch.object(service, '_create_alert') as mock_alert:
            service._check_qc_thresholds(cursor, 1, {'signal_to_noise': -1.0})
            # Should fire lower_critical because -1 < 0.0
            mock_alert.assert_any_call(cursor, 1, 'signal_to_noise', 'lower', 0.0, -1.0, 'critical',
                                       vendor_uuid=None, vendor_ts=None, sample_name=None)


class TestUpdateRollingStats:
    def test_empty_changed_metrics_is_noop(self, service, mock_normalized_db):
        """Passing an empty set should not trigger any database queries"""
        service._update_rolling_stats(set())
        mock_normalized_db.execute_non_query.assert_not_called()

    def test_queries_all_windows_per_metric(self, service, mock_normalized_db):
        """Each changed metric should trigger rolling stats for all 4 windows (7, 30, 365, 0=all)"""
        service.metrics_calc = MagicMock()
        service.metrics_calc.calculate_rolling_statistics.return_value = {
            'mean': 100, 'std_dev': 10, 'min': 50, 'max': 150, 'count': 20
        }
        service._update_rolling_stats({'mean_mfi'})
        assert service.metrics_calc.calculate_rolling_statistics.call_count == 4
        windows_called = [
            c[0][1] for c in service.metrics_calc.calculate_rolling_statistics.call_args_list
        ]
        assert sorted(windows_called) == [0, 7, 30, 365]

    def test_skips_window_when_no_stats(self, service, mock_normalized_db):
        """If calculate_rolling_statistics returns empty, that window should be skipped"""
        service.metrics_calc = MagicMock()
        service.metrics_calc.calculate_rolling_statistics.return_value = {}
        service._update_rolling_stats({'mean_mfi'})
        mock_normalized_db.execute_non_query.assert_not_called()


class TestCheckQCSampleOutliers:
    def test_creates_alerts_for_outliers(self, service, mock_normalized_db):
        """QC cache entries beyond mean ± 2SD should generate outlier alerts"""
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 50}],  # cache count
            # group stats
            [{'catalog_group': 'LS1A04', 'bead_lot': '008', 'role': 'positive',
              'mean_mfi': 1000.0, 'sd_mfi': 100.0, 'n': 20}],
            # outlier entries
            [{'id': 42, 'display_name': 'POS CONTROL', 'median_mfi': 1300.0,
              'analysis_dt': datetime(2024, 1, 1), 'role': 'positive'}],
        ]
        mock_normalized_db.execute_non_query.return_value = 1
        result = service.check_qc_sample_outliers()
        assert result == 1
        mock_normalized_db.execute_non_query.assert_called_once()
        call_args = mock_normalized_db.execute_non_query.call_args
        assert 'qc_median_mfi' in call_args[0][0] or call_args[0][1][0] == 'qc_median_mfi'

    def test_no_alerts_when_no_outliers(self, service, mock_normalized_db):
        """No outlier alerts when all entries are within 2SD"""
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 50}],  # cache count
            [{'catalog_group': 'LS1A04', 'bead_lot': '008', 'role': 'positive',
              'mean_mfi': 1000.0, 'sd_mfi': 100.0, 'n': 20}],
            [],  # no outliers found
        ]
        result = service.check_qc_sample_outliers()
        assert result == 0
        mock_normalized_db.execute_non_query.assert_not_called()

    def test_no_alerts_when_insufficient_data(self, service, mock_normalized_db):
        """No outlier alerts when groups have fewer than 5 data points"""
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 3}],  # cache count
            [],  # HAVING filters out small groups
        ]
        result = service.check_qc_sample_outliers()
        assert result == 0

    def test_skips_already_alerted_entries(self, service, mock_normalized_db):
        """Entries with existing qc_cache_id alerts should not be re-alerted"""
        mock_normalized_db.execute_query.side_effect = [
            [{'n': 50}],  # cache count
            [{'catalog_group': 'LS1A04', 'bead_lot': '008', 'role': 'positive',
              'mean_mfi': 1000.0, 'sd_mfi': 100.0, 'n': 20}],
            [],  # NOT EXISTS filters out already-alerted entries
        ]
        result = service.check_qc_sample_outliers()
        assert result == 0
