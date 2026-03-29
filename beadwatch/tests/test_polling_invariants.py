from types import SimpleNamespace
from unittest.mock import MagicMock

from services.polling_service import PollingService


def _build_service_with_cursor(cursor):
    sqlite_db = MagicMock()
    vendor = MagicMock()
    vendor.vendor_name = "TestVendor"
    normalized_db = MagicMock()

    txn = MagicMock()
    txn.__enter__.return_value = (MagicMock(), cursor)
    txn.__exit__.return_value = False
    normalized_db.transaction.return_value = txn

    service = PollingService(sqlite_db=sqlite_db, vendor_adapter=vendor, normalized_db=normalized_db)
    return service, cursor


def _record(metric_value=250.0):
    return {
        "uuid": "rec-1",
        "timestamp": "2026-03-19T12:00:00",
        "raw_data": {"sample_id": "QC-001", "metric_value": metric_value},
    }


def _alert_calls(cursor):
    return [
        c for c in cursor.execute.call_args_list
        if "INSERT INTO QCAlerts" in c.args[0]
    ]


def _metric_calls(cursor):
    return [
        c for c in cursor.execute.call_args_list
        if "INSERT INTO QCMetrics" in c.args[0]
    ]


def test_process_record_inserts_processed_record_metrics_and_alert():
    cursor = MagicMock()
    cursor.fetchone.return_value = [123]
    cursor.fetchall.return_value = [
        SimpleNamespace(
            metric_name="mean_mfi",
            upper_warning=100.0,
            upper_critical=200.0,
            lower_warning=None,
            lower_critical=None,
        )
    ]

    service, cursor = _build_service_with_cursor(cursor)
    service.metrics_calc.calculate_qc_metrics = MagicMock(return_value={"mean_mfi": 250.0})

    metrics = service._process_record(_record())

    assert metrics == {"mean_mfi": 250.0}
    assert any("INSERT INTO ProcessedRecords" in c.args[0] for c in cursor.execute.call_args_list)
    assert len(_metric_calls(cursor)) == 1
    assert len(_alert_calls(cursor)) == 1

    alert_params = _alert_calls(cursor)[0].args[1]
    assert alert_params[1] == "mean_mfi"
    assert alert_params[5] == "critical"


def test_process_record_warning_vs_critical_threshold_transition():
    cursor = MagicMock()
    cursor.fetchone.return_value = [123]
    cursor.fetchall.return_value = [
        SimpleNamespace(
            metric_name="mean_mfi",
            upper_warning=100.0,
            upper_critical=200.0,
            lower_warning=None,
            lower_critical=None,
        )
    ]

    service, cursor = _build_service_with_cursor(cursor)
    service.metrics_calc.calculate_qc_metrics = MagicMock(return_value={"mean_mfi": 150.0})

    service._process_record(_record(metric_value=150.0))
    alert_params = _alert_calls(cursor)[0].args[1]
    assert alert_params[5] == "warning"


def test_process_record_no_alert_when_metric_in_range():
    cursor = MagicMock()
    cursor.fetchone.return_value = [123]
    cursor.fetchall.return_value = [
        SimpleNamespace(
            metric_name="mean_mfi",
            upper_warning=200.0,
            upper_critical=300.0,
            lower_warning=10.0,
            lower_critical=5.0,
        )
    ]

    service, cursor = _build_service_with_cursor(cursor)
    service.metrics_calc.calculate_qc_metrics = MagicMock(return_value={"mean_mfi": 100.0})

    service._process_record(_record(metric_value=100.0))
    assert len(_alert_calls(cursor)) == 0


def test_process_record_duplicate_short_circuits_without_metric_or_alert_writes():
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []

    service, cursor = _build_service_with_cursor(cursor)
    service.metrics_calc.calculate_qc_metrics = MagicMock(return_value={"mean_mfi": 250.0})

    result = service._process_record(_record())
    assert result is None
    assert len(_metric_calls(cursor)) == 0
    assert len(_alert_calls(cursor)) == 0
