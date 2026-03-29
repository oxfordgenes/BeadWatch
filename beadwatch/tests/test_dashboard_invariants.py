import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from api.controllers.dashboard_controller import DashboardController


def _make_request(normalized_db=None, sqlite_db=None, vendor_db=None):
    state = SimpleNamespace(
        normalized_db=normalized_db or MagicMock(),
        sqlite_db=sqlite_db or MagicMock(),
        vendor_db=vendor_db or MagicMock(),
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _extract_query_and_params(mock_db):
    args = mock_db.execute_query.call_args[0]
    if len(args) == 1:
        return args[0], ()
    return args[0], args[1]


def test_lot_like_pattern():
    assert DashboardController._lot_like_pattern("LS1A04", "008") == "LS1A04%[_]008[_]%"


def test_extract_catalog_group_and_bead_lot():
    assert DashboardController._extract_catalog_group("LS1A04NC12_008_02.1") == "LS1A04"
    assert DashboardController._extract_bead_lot("LS1A04NC12_008_02.1") == "008"
    assert DashboardController._extract_catalog_group("UNKNOWN_008_01") is None
    assert DashboardController._extract_bead_lot("LS1A04NC12_X08_02.1") is None


def test_select_controls_prefers_ls2_pair():
    ls1 = DashboardController._select_controls("LS1A04NC12_008_01", 10, 20, 30, 40)
    ls2 = DashboardController._select_controls("LS2A01NC12_008_01", 10, 20, 30, 40)
    assert ls1 == {"nc": 10, "pc": 30}
    assert ls2 == {"nc": 20, "pc": 40}


def test_alias_and_exclusion_maps_are_uppercased():
    sqlite_db = MagicMock()
    sqlite_db.instruments.get_excluded_instruments.return_value = [
        {"serial_number": "inst-1"},
        {"serial_number": "InSt-2"},
    ]
    sqlite_db.instruments.get_instrument_aliases.return_value = [
        {"serial_number": "inst-1", "display_name": "Main"},
    ]

    excluded = DashboardController._get_excluded_instruments(sqlite_db)
    aliases = DashboardController._get_instrument_aliases(sqlite_db)

    assert excluded == {"INST-1", "INST-2"}
    assert aliases == {"INST-1": "Main"}


def test_recent_metrics_sql_shape():
    controller = DashboardController()
    normalized_db = MagicMock()
    normalized_db.execute_query.return_value = []
    request = _make_request(normalized_db=normalized_db)

    asyncio.run(controller.get_recent_metrics(request, hours=24, metric_name="mean_mfi"))
    query, params = _extract_query_and_params(normalized_db)

    assert "FROM QCMetrics" in query
    assert "JOIN ProcessedRecords" in query
    assert "DATEADD(hour, -?, GETDATE())" in query
    assert "qm.metric_name = ?" in query
    assert "ORDER BY pr.vendor_record_timestamp DESC" in query
    assert params == (24, "mean_mfi")


def test_active_alerts_sql_shape():
    controller = DashboardController()
    normalized_db = MagicMock()
    normalized_db.execute_query.return_value = []
    request = _make_request(normalized_db=normalized_db)

    asyncio.run(
        controller.get_active_alerts(
            request,
            severity="critical",
            days=7,
            include_acknowledged=False,
        )
    )
    query, params = _extract_query_and_params(normalized_db)

    assert "FROM QCAlerts qa" in query
    assert "LEFT JOIN ProcessedRecords pr" in query
    assert "DATEADD(day, -?, GETDATE())" in query
    assert "qa.acknowledged = 0" in query
    assert "qa.severity = ?" in query
    assert "ORDER BY qa.created_at DESC" in query
    assert params == (7, "critical")


def test_catalog_groups_sql_shape_with_days():
    controller = DashboardController()
    vendor_db = MagicMock()
    vendor_db.execute_query.return_value = []
    request = _make_request(vendor_db=vendor_db)

    asyncio.run(controller.get_catalog_groups(request, days=365))
    query, params = _extract_query_and_params(vendor_db)

    assert "SELECT DISTINCT t.CatalogID" in query
    assert "FROM dbo.TRAY t" in query
    assert "JOIN dbo.WELL w ON t.TrayID = w.TrayID" in query
    assert "t.BuildDT >= DATEADD(day, -?, GETDATE())" in query
    assert "w.AnalysisDT IS NOT NULL" in query
    assert params == (365,)


def test_bead_lots_sql_shape_with_days_and_group_filter():
    controller = DashboardController()
    vendor_db = MagicMock()
    vendor_db.execute_query.return_value = []
    request = _make_request(vendor_db=vendor_db)

    asyncio.run(controller.get_bead_lots(request, catalog_group="LS1A04", days=90))
    query, params = _extract_query_and_params(vendor_db)

    assert "SELECT DISTINCT t.CatalogID" in query
    assert "t.BuildDT >= DATEADD(day, -?, GETDATE())" in query
    assert "t.CatalogID LIKE ?" in query
    assert params == (90, "LS1A04%")
