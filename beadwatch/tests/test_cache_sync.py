import asyncio
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI

from api.controllers.config_controller import ConfigController
from database.sqlite_handler import SQLiteHandler
from services.polling_service import PollingService


def test_add_qc_sample_definition_triggers_qc_cache_rebuild():
    app = FastAPI()
    app.state.sqlite_db = MagicMock()
    app.state.sqlite_db.qc.add_qc_sample_definition.return_value = 77
    app.state.polling_service = MagicMock()

    request = MagicMock()
    request.app = app

    controller = ConfigController()
    body = MagicMock(pattern="QC", match_type="substring", role="positive", label="QC Pos")

    result = asyncio.run(controller.add_qc_sample_definition(request, body))

    assert result == {"success": True, "id": 77}
    app.state.polling_service.rebuild_qc_cache.assert_called_once()


def test_sqlite_handler_qc_definition_update_marks_cache_stale(tmp_path: Path):
    db_path = tmp_path / "config.db"
    handler = SQLiteHandler(db_path=db_path)
    handler.initialize()

    handler.qc.add_qc_sample_definition(pattern="QC", match_type="substring", role="positive", label="QC Pos")

    # New contract target: SQLite-level writes should mark a cache-stale flag.
    assert handler.app_state.get_state("qc_cache_stale") == "true"


def test_operator_cache_population_is_serialized_by_lock():
    sqlite_db = MagicMock()
    vendor = MagicMock()
    vendor.vendor_name = "TestVendor"
    normalized_db = MagicMock()
    normalized_db.execute_query.return_value = [{"n": 0}]

    service = PollingService(sqlite_db=sqlite_db, vendor_adapter=vendor, normalized_db=normalized_db, vendor_db=MagicMock())

    active = 0
    max_active = 0
    guard = threading.Lock()

    def slow_populate(_since_dt):
        nonlocal active, max_active
        with guard:
            active += 1
            if active > max_active:
                max_active = active
        time.sleep(0.05)
        with guard:
            active -= 1
        return 1

    service._do_populate_operator_cache = slow_populate

    t1 = threading.Thread(target=service.populate_operator_cache)
    t2 = threading.Thread(target=service.populate_operator_cache)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert max_active == 1
