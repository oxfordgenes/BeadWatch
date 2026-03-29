"""DOM smoke tests: verify that JS getElementById calls match HTML id attributes.

These tests parse the HTML and JS source files statically — no browser needed.
They catch the class of bug where JS and HTML get out of sync after edits.
"""
import re
from pathlib import Path

import pytest

FRONTEND_DIR = Path(__file__).resolve().parent.parent / 'frontend'


def _extract_html_ids(html_path: Path) -> set:
    """Extract all id="..." values from an HTML file."""
    content = html_path.read_text(encoding='utf-8')
    return set(re.findall(r'id=["\']([^"\']+)["\']', content))


def _extract_js_get_element_by_id(js_path: Path) -> set:
    """Extract all getElementById('...') argument values from a JS file."""
    content = js_path.read_text(encoding='utf-8')
    return set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", content))


class TestDashboardDOMWiring:
    """Verify dashboard.js element references exist in index.html."""

    @pytest.fixture
    def html_ids(self):
        return _extract_html_ids(FRONTEND_DIR / 'index.html')

    @pytest.fixture
    def js_ids(self):
        return _extract_js_get_element_by_id(FRONTEND_DIR / 'static' / 'js' / 'dashboard.js')

    def test_all_js_ids_exist_in_html(self, html_ids, js_ids):
        """Every getElementById call in dashboard.js must reference an id in index.html"""
        missing = js_ids - html_ids
        assert not missing, f"dashboard.js references IDs not found in index.html: {missing}"

    def test_js_references_at_least_core_elements(self, js_ids):
        """Sanity check: JS should reference the known core element IDs"""
        expected_core = {
            'alert-banner', 'alert-list',
            'catalog-group-select', 'bead-lot-select',
            'app-version',
        }
        missing = expected_core - js_ids
        assert not missing, f"dashboard.js is missing getElementById for core elements: {missing}"


class TestStatusDotCSS:
    """Verify the CSS has rules for every data-status value the JS can set."""

    def test_disconnected_sets_critical_not_empty(self):
        """JS error handler must set data-status='critical' so the dot turns red, not gray"""
        js = (FRONTEND_DIR / 'static' / 'js' / 'settings-page.js').read_text(encoding='utf-8')
        # Find the catch block for loadPollStatus — it should set 'critical', not ''
        assert "setAttribute('data-status', 'critical')" in js, (
            "loadPollStatus error handler should set data-status to 'critical' for red dot, not empty string"
        )

    def test_css_has_rules_for_all_status_values(self):
        """CSS must define styles for healthy, degraded, and critical data-status values"""
        css = (FRONTEND_DIR / 'static' / 'css' / 'styles.css').read_text(encoding='utf-8')
        for status in ('healthy', 'degraded', 'critical'):
            pattern = f'data-status="{status}"'
            assert pattern in css, f"styles.css missing rule for {pattern}"


class TestInstrumentsDOMWiring:
    """Verify instruments.js element references exist in instruments.html."""

    @pytest.fixture
    def html_ids(self):
        return _extract_html_ids(FRONTEND_DIR / 'instruments.html')

    @pytest.fixture
    def js_ids(self):
        return _extract_js_get_element_by_id(FRONTEND_DIR / 'static' / 'js' / 'instruments.js')

    def test_all_js_ids_exist_in_html(self, html_ids, js_ids):
        """Every getElementById call in instruments.js must reference an id in instruments.html"""
        missing = js_ids - html_ids
        assert not missing, f"instruments.js references IDs not found in instruments.html: {missing}"


class TestOperatorsDOMWiring:
    """Verify operators.js element references exist in operators.html."""

    @pytest.fixture
    def html_ids(self):
        return _extract_html_ids(FRONTEND_DIR / 'operators.html')

    @pytest.fixture
    def js_ids(self):
        return _extract_js_get_element_by_id(FRONTEND_DIR / 'static' / 'js' / 'operators.js')

    def test_all_js_ids_exist_in_html(self, html_ids, js_ids):
        """Every getElementById call in operators.js must reference an id in operators.html"""
        missing = js_ids - html_ids
        assert not missing, f"operators.js references IDs not found in operators.html: {missing}"

    def test_js_references_at_least_core_elements(self, js_ids):
        """Sanity check: JS should reference the known core operator element IDs"""
        expected_core = {
            'op-counts-chart', 'op-sn-chart', 'op-nc-chart',
            'op-refresh-btn', 'app-version',
        }
        missing = expected_core - js_ids
        assert not missing, f"operators.js is missing getElementById for core elements: {missing}"


class TestSettingsDOMWiring:
    """Verify settings-page.js element references exist in settings.html."""

    @pytest.fixture
    def html_ids(self):
        return _extract_html_ids(FRONTEND_DIR / 'settings.html')

    @pytest.fixture
    def js_ids(self):
        return _extract_js_get_element_by_id(FRONTEND_DIR / 'static' / 'js' / 'settings-page.js')

    def test_all_js_ids_exist_in_html(self, html_ids, js_ids):
        """Every getElementById call in settings-page.js must reference an id in settings.html"""
        missing = js_ids - html_ids
        assert not missing, f"settings-page.js references IDs not found in settings.html: {missing}"


class TestThresholdFrontendScriptPresence:
    """Smoke-check that threshold-related functions and calls exist in settings-page.js."""

    @pytest.fixture
    def settings_js(self):
        return (FRONTEND_DIR / 'static' / 'js' / 'settings-page.js').read_text(encoding='utf-8')

    def test_threshold_render_function_exists(self, settings_js):
        assert 'function renderThresholds' in settings_js

    def test_threshold_save_function_exists(self, settings_js):
        assert 'function saveThreshold' in settings_js

    def test_threshold_load_function_exists(self, settings_js):
        assert 'function loadThresholds' in settings_js
        assert 'loadThresholds()' in settings_js

    def test_threshold_save_displays_validation_errors(self, settings_js):
        """Save handler must surface error detail from 422 responses."""
        assert 'errData.detail' in settings_js

    def test_showmsg_helper_exists(self, settings_js):
        assert 'function showMsg' in settings_js


class TestSetupDOMWiring:
    """Verify setup.js element references exist in setup.html."""

    @pytest.fixture
    def html_ids(self):
        return _extract_html_ids(FRONTEND_DIR / 'setup.html')

    @pytest.fixture
    def js_ids(self):
        return _extract_js_get_element_by_id(FRONTEND_DIR / 'static' / 'js' / 'setup.js')

    def test_all_js_ids_exist_in_html(self, html_ids, js_ids):
        """Every getElementById call in setup.js must reference an id in setup.html"""
        missing = js_ids - html_ids
        assert not missing, f"setup.js references IDs not found in setup.html: {missing}"

    def test_js_references_at_least_core_elements(self, js_ids):
        """Sanity check: JS should reference the known core setup elements"""
        expected_core = {
            'input-server', 'input-database', 'input-username', 'input-password',
            'btn-test', 'btn-save', 'message',
        }
        missing = expected_core - js_ids
        assert not missing, f"setup.js is missing getElementById for core elements: {missing}"
