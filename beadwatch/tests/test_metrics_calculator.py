import pytest
from services.metrics_calculator import MetricsCalculator


@pytest.fixture
def calc():
    return MetricsCalculator()


class TestCalculateQCMetrics:
    def test_basic_metrics(self, calc):
        raw_data = {
            'bead_results': [
                {'bead_id': 1, 'mfi': 1000, 'count': 60},
                {'bead_id': 2, 'mfi': 2000, 'count': 70},
                {'bead_id': 3, 'mfi': 3000, 'count': 50},
            ],
            'control_bead_mfi': 15000,
            'negative_control_mfi': 150,
            'catalog_group': 'LS1A04',
            'bead_lot': '008'
        }
        metrics = calc.calculate_qc_metrics(raw_data)

        assert metrics['mean_mfi'] == 2000.0
        assert metrics['median_mfi'] == 2000.0
        assert metrics['min_mfi'] == 1000
        assert metrics['max_mfi'] == 3000
        assert metrics['mean_bead_count'] == 60.0
        assert metrics['min_bead_count'] == 50
        assert metrics['total_beads'] == 3
        assert metrics['signal_to_noise'] == 100.0
        assert metrics['positive_control_mfi'] == 15000
        assert metrics['negative_control_mfi'] == 150
        assert metrics['positive_control_mfi__catalog_LS1A04'] == 15000
        assert metrics['positive_control_mfi__catalog_LS1A04__lot_008'] == 15000

    def test_cv_percentage(self, calc):
        raw_data = {
            'bead_results': [
                {'bead_id': 1, 'mfi': 100, 'count': 50},
                {'bead_id': 2, 'mfi': 200, 'count': 50},
            ],
            'control_bead_mfi': None,
            'negative_control_mfi': None
        }
        metrics = calc.calculate_qc_metrics(raw_data)
        assert 'cv_percentage' not in metrics

    def test_empty_bead_results(self, calc):
        raw_data = {'bead_results': []}
        metrics = calc.calculate_qc_metrics(raw_data)
        assert metrics == {'records_processed': 1}

    def test_missing_bead_results(self, calc):
        raw_data = {}
        metrics = calc.calculate_qc_metrics(raw_data)
        assert metrics == {'records_processed': 1}

    def test_single_bead_no_cv(self, calc):
        """Single bead result should not produce cv_percentage or std_dev_mfi"""
        raw_data = {
            'bead_results': [{'bead_id': 1, 'mfi': 1500, 'count': 60}],
            'control_bead_mfi': None,
            'negative_control_mfi': None
        }
        metrics = calc.calculate_qc_metrics(raw_data)
        assert 'mean_mfi' in metrics
        assert 'cv_percentage' not in metrics
        assert 'std_dev_mfi' not in metrics

    def test_zero_negative_control(self, calc):
        """Zero negative control should not cause division by zero"""
        raw_data = {
            'bead_results': [{'bead_id': 1, 'mfi': 1000, 'count': 50}],
            'control_bead_mfi': 15000,
            'negative_control_mfi': 0
        }
        metrics = calc.calculate_qc_metrics(raw_data)
        assert 'signal_to_noise' not in metrics

    def test_low_bead_count_percentage(self, calc):
        raw_data = {
            'bead_results': [
                {'bead_id': 1, 'mfi': 1000, 'count': 10},   # below 25
                {'bead_id': 2, 'mfi': 1000, 'count': 20},   # below 25
                {'bead_id': 3, 'mfi': 1000, 'count': 60},   # above 25
                {'bead_id': 4, 'mfi': 1000, 'count': 80},   # above 25
            ],
            'control_bead_mfi': None,
            'negative_control_mfi': None
        }
        metrics = calc.calculate_qc_metrics(raw_data)
        assert metrics['low_count_bead_percentage'] == 50.0

    def test_none_mfi_values_filtered(self, calc):
        raw_data = {
            'bead_results': [
                {'bead_id': 1, 'mfi': None, 'count': 50},
                {'bead_id': 2, 'mfi': 1000, 'count': 50},
            ],
            'control_bead_mfi': None,
            'negative_control_mfi': None
        }
        metrics = calc.calculate_qc_metrics(raw_data)
        assert metrics['mean_mfi'] == 1000.0


class TestCalculateRollingStatistics:
    def test_with_mocked_db(self, calc):
        class MockDB:
            def execute_query(self, query, params):
                return [
                    {'metric_value': 100},
                    {'metric_value': 200},
                    {'metric_value': 300},
                ]

        stats = calc.calculate_rolling_statistics('mean_mfi', 30, MockDB())
        assert stats['mean'] == 200.0
        assert stats['min'] == 100
        assert stats['max'] == 300
        assert stats['count'] == 3
        assert stats['std_dev'] == 100.0

    def test_empty_results(self, calc):
        class MockDB:
            def execute_query(self, query, params):
                return []

        stats = calc.calculate_rolling_statistics('mean_mfi', 30, MockDB())
        assert stats == {}

    def test_single_value(self, calc):
        class MockDB:
            def execute_query(self, query, params):
                return [{'metric_value': 42}]

        stats = calc.calculate_rolling_statistics('mean_mfi', 7, MockDB())
        assert stats['mean'] == 42
        assert stats['std_dev'] == 0
        assert stats['count'] == 1
