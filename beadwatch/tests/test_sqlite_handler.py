"""Tests for SQLite handler CRUD methods and settings export/import."""
import os
import tempfile
from pathlib import Path

import pytest

from database.sqlite_handler import SQLiteHandler


@pytest.fixture
def db():
    """Create a temp SQLite DB and initialize it."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    handler = SQLiteHandler(Path(path))
    handler.initialize()
    yield handler
    os.unlink(path)


class TestExcludedOperators:
    def test_add_and_get(self, db):
        db.operators.add_excluded_operator('jdoe', label='Left lab')
        rows = db.operators.get_excluded_operators()
        assert len(rows) == 1
        assert rows[0]['username'] == 'JDOE'  # normalized to upper
        assert rows[0]['label'] == 'Left lab'

    def test_add_strips_whitespace(self, db):
        db.operators.add_excluded_operator('  jdoe  ')
        rows = db.operators.get_excluded_operators()
        assert rows[0]['username'] == 'JDOE'

    def test_delete(self, db):
        new_id = db.operators.add_excluded_operator('jdoe')
        assert db.operators.delete_excluded_operator(new_id) is True
        assert db.operators.get_excluded_operators() == []

    def test_delete_nonexistent(self, db):
        assert db.operators.delete_excluded_operator(999) is False

    def test_add_multiple(self, db):
        db.operators.add_excluded_operator('jdoe')
        db.operators.add_excluded_operator('asmith', label='Training')
        rows = db.operators.get_excluded_operators()
        assert len(rows) == 2
        usernames = {r['username'] for r in rows}
        assert usernames == {'JDOE', 'ASMITH'}


class TestOperatorAliases:
    def test_add_and_get(self, db):
        db.operators.add_operator_alias('jdoe', 'Jane Doe')
        rows = db.operators.get_operator_aliases()
        assert len(rows) == 1
        assert rows[0]['username'] == 'JDOE'  # normalized to upper
        assert rows[0]['display_name'] == 'Jane Doe'

    def test_add_strips_whitespace(self, db):
        db.operators.add_operator_alias('  jdoe  ', 'Jane Doe')
        rows = db.operators.get_operator_aliases()
        assert rows[0]['username'] == 'JDOE'

    def test_delete(self, db):
        new_id = db.operators.add_operator_alias('jdoe', 'Jane Doe')
        assert db.operators.delete_operator_alias(new_id) is True
        assert db.operators.get_operator_aliases() == []

    def test_delete_nonexistent(self, db):
        assert db.operators.delete_operator_alias(999) is False

    def test_add_multiple(self, db):
        db.operators.add_operator_alias('jdoe', 'Jane Doe')
        db.operators.add_operator_alias('asmith', 'Alice Smith')
        rows = db.operators.get_operator_aliases()
        assert len(rows) == 2
        names = {r['display_name'] for r in rows}
        assert names == {'Jane Doe', 'Alice Smith'}


class TestSettingsExportImport:
    """Tests for export_all_settings / import_all_settings roundtrip."""

    def _seed_settings(self, db):
        """Populate all exportable tables with test data."""
        db.qc.add_qc_sample_definition('MP1', 'substring', 'positive', label='Pos ctrl')
        db.qc.add_qc_sample_definition('NC', 'substring', 'negative', label=None)
        db.qc.add_qc_tracked_bead('LS1A04', '008', '3', label='A1')
        db.instruments.add_excluded_instrument('ABC123', label='Old unit')
        db.instruments.add_instrument_alias('SN001', 'Lab-1')
        db.operators.add_excluded_operator('jsmith', label=None)
        db.operators.add_operator_alias('jsmith', 'Dr. Smith')
        db.app_state.set_state('poll_interval_minutes', '45')

    def test_export_returns_all_sections(self, db):
        self._seed_settings(db)
        data = db.settings.export_all_settings()
        assert len(data['qc_sample_definitions']) == 2
        assert len(data['qc_tracked_beads']) == 1
        assert len(data['excluded_instruments']) == 1
        assert len(data['instrument_aliases']) == 1
        assert len(data['excluded_operators']) == 1
        assert len(data['operator_aliases']) == 1
        assert data['polling_interval_minutes'] == 45

    def test_export_excludes_ids_and_timestamps(self, db):
        self._seed_settings(db)
        data = db.settings.export_all_settings()
        for row in data['qc_sample_definitions']:
            assert 'id' not in row
            assert 'created_at' not in row

    def test_export_empty_db(self, db):
        data = db.settings.export_all_settings()
        assert data['qc_sample_definitions'] == []
        assert 'polling_interval_minutes' not in data

    def test_roundtrip(self, db):
        """Export → import on a fresh DB produces identical settings."""
        self._seed_settings(db)
        exported = db.settings.export_all_settings()

        # Clear everything manually
        with db.get_connection() as conn:
            for table in db._EXPORTABLE_TABLES:
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM app_state WHERE key = 'poll_interval_minutes'")
            conn.commit()

        db.settings.import_all_settings(exported)
        re_exported = db.settings.export_all_settings()

        assert re_exported == exported

    def test_import_clear_and_replace(self, db):
        """Import replaces existing rows, not appends."""
        self._seed_settings(db)

        new_data = {
            'qc_sample_definitions': [
                {'pattern': 'NEW', 'match_type': 'exact', 'role': 'positive', 'label': None}
            ],
        }
        db.settings.import_all_settings(new_data)

        defs = db.qc.get_qc_sample_definitions()
        assert len(defs) == 1
        assert defs[0]['pattern'] == 'NEW'

    def test_import_omitted_section_untouched(self, db):
        """Sections not in import data are left alone."""
        self._seed_settings(db)

        # Import only qc_sample_definitions — others should be unchanged
        db.settings.import_all_settings({
            'qc_sample_definitions': [
                {'pattern': 'X', 'match_type': 'substring', 'role': 'negative', 'label': None}
            ],
        })

        # excluded_instruments was not in import, should still have data
        instruments = db.instruments.get_excluded_instruments()
        assert len(instruments) == 1
        assert instruments[0]['serial_number'] == 'ABC123'

    def test_import_empty_array_clears_table(self, db):
        self._seed_settings(db)
        db.settings.import_all_settings({'excluded_instruments': []})
        assert db.instruments.get_excluded_instruments() == []

    def test_import_operator_username_normalisation(self, db):
        db.settings.import_all_settings({
            'excluded_operators': [{'username': '  jdoe  ', 'label': None}],
        })
        rows = db.operators.get_excluded_operators()
        assert rows[0]['username'] == 'JDOE'

    def test_validate_rejects_invalid_role(self, db):
        with pytest.raises(ValueError, match="Invalid role"):
            db.settings.validate_import_data({
                'qc_sample_definitions': [
                    {'pattern': 'X', 'match_type': 'substring', 'role': 'unknown', 'label': None}
                ],
            })

    def test_validate_rejects_duplicate_serial_number(self, db):
        with pytest.raises(ValueError, match="Duplicate"):
            db.settings.validate_import_data({
                'instrument_aliases': [
                    {'serial_number': 'SN1', 'display_name': 'A'},
                    {'serial_number': 'SN1', 'display_name': 'B'},
                ],
            })

    def test_validate_rejects_duplicate_operator_username(self, db):
        """Duplicates detected after normalisation."""
        with pytest.raises(ValueError, match="Duplicate"):
            db.settings.validate_import_data({
                'excluded_operators': [
                    {'username': 'jdoe', 'label': None},
                    {'username': '  JDOE  ', 'label': None},
                ],
            })

    def test_validate_rejects_non_dict_row(self, db):
        """Non-dict items in a table array are rejected."""
        with pytest.raises(ValueError, match="must be an object"):
            db.settings.validate_import_data({
                'instrument_aliases': [123],
            })

    def test_validate_rejects_non_dict_row_in_definitions(self, db):
        with pytest.raises(ValueError, match="must be an object"):
            db.settings.validate_import_data({
                'qc_sample_definitions': ["not a dict"],
            })

    def test_validate_passes_for_valid_data(self, db):
        """No exception for valid data."""
        db.settings.validate_import_data({
            'qc_sample_definitions': [
                {'pattern': 'A', 'match_type': 'substring', 'role': 'positive', 'label': None},
            ],
            'excluded_operators': [
                {'username': 'jdoe', 'label': None},
                {'username': 'asmith', 'label': None},
            ],
        })

    def test_import_returns_counts(self, db):
        result = db.settings.import_all_settings({
            'qc_sample_definitions': [
                {'pattern': 'A', 'match_type': 'substring', 'role': 'positive', 'label': None},
                {'pattern': 'B', 'match_type': 'substring', 'role': 'negative', 'label': None},
            ],
            'polling_interval_minutes': 30,
        })
        assert result['qc_sample_definitions'] == 2
        assert result['polling_interval_minutes'] is True
