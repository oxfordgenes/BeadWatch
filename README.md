# BeadWatch

Portable QC dashboard for Luminex bead-based assay systems in H&I laboratories. Monitors vendor databases for new test results, calculates QC metrics, checks thresholds, and displays trends — all from a single zero-install executable.

## Prerequisites

- **Python 3.10+** — [python.org/downloads](https://www.python.org/downloads/) (check "Add Python to PATH" during install)
- **uv** — [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/)
- **SQL Server** — a running instance with a Luminex vendor database (needed for full functionality, not needed for tests)
- **ODBC Driver 18 for SQL Server** — required at runtime for database connectivity

## Development Setup

```
cd D:\CODE\BeadWatch
uv sync
```

This creates the virtual environment and installs all dependencies (including dev tools like pytest and httpx) in one step.

## Running Tests

```
cd beadwatch
uv run pytest tests/ -v
```

Everything is mocked — no SQL Server needed. The test suite covers:

| Module | Tests | What it validates |
|---|---|---|
| `test_encryption.py` | 6 | Fernet encrypt/decrypt round-trips, key management |
| `test_metrics_calculator.py` | 11 | QC metric calculations, edge cases (empty, single, None values) |
| `test_vendor_adapters.py` | 12 | Abstract base enforcement, mock adapter output structure |
| `test_polling_service.py` | 16 | Cursor advancement, dedup, error handling, threshold alerts |
| `test_controllers.py` | 14 | API endpoint responses, 503 when unconfigured, config flow |
| `test_frontend_wiring.py` | 6 | JS getElementById calls match HTML id attributes |

## Running the App

```
cd beadwatch
uv run python main.py
```

Opens `http://localhost:8765` in your browser. On first run you'll see the **setup wizard** to configure your SQL Server connection. After setup, the dashboard becomes available immediately (no restart required).

## Vendor JS Libraries

The chart on the dashboard requires three vendored JavaScript libraries. Placeholder stubs are included to prevent 404 errors, but you must replace them with the real minified bundles before the chart will render:

| File | Download |
|---|---|
| `frontend/static/js/vendor/chart.js` | https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js |
| `frontend/static/js/vendor/date-fns.js` | https://cdn.jsdelivr.net/npm/date-fns@3/cdn.min.js |
| `frontend/static/js/vendor/chartjs-adapter-date-fns.js` | https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3/dist/chartjs-adapter-date-fns.bundle.min.js |

These are vendored locally (not loaded from a CDN) so the app works offline.

## Building the Executable

```
cd beadwatch
uv run pyinstaller beadwatch.spec
```

Output: `dist/BeadWatch.exe` — a single portable executable. No Python installation needed on the target machine (ODBC driver still required).

## Settings Export / Import

BeadWatch stores its configuration in two local files: `config.db` (SQLite) and `.beadwatch_key` (Fernet encryption key). Copying both files to another machine performs a full migration, but also transfers SQL Server credentials, which are typically site- or machine-specific.

The **settings export** (`GET /api/config/settings/export`) produces a portable JSON file containing only the user-configurable settings — QC sample definitions, tracked beads, alert thresholds, instrument/operator aliases and exclusions, and polling preferences. **SQL Server credentials are deliberately excluded.**

Use export/import to:
- Share a tuned QC configuration between workstations or sites without sharing credentials
- Template a standard lab configuration across multiple installations
- Archive settings in a human-readable, version-controllable format

Import via `POST /api/config/settings/import` does a clear-and-replace on all settings tables, so it is safe to re-import an updated export at any time.

## Default Alert Thresholds

| Metric | Lower Warning | Lower Critical | Upper Warning | Upper Critical |
|---|---|---|---|---|
| Minimum bead count | 50 | 25 | — | — |
| Mean MFI | 100 | 50 | 20,000 | 25,000 |
| Signal-to-noise ratio | 10 | 5 | — | — |
| Negative control MFI | — | — | 500 | 1,000 |

All thresholds are configurable via the settings page.

## Normalised QC Database Tables

| Table | Purpose |
|---|---|
| ProcessedRecords | Deduplication tracking |
| QCMetrics | Per-record calculated metrics |
| RollingStats | Pre-computed window statistics (7d/30d/365d/all) |
| QCAlerts | Threshold violations |
| QCThresholds | Configurable alert limits |
| QCSampleCache | Cached QC sample metrics |
| InstrumentRunCache | Per-instrument run aggregates |
| OperatorRunCache | Per-operator run aggregates |

## Project Structure

```
beadwatch/
├── main.py                         # FastAPI entry point
├── config/settings.py              # All constants and paths
├── utils/                          # Encryption, logging, port finder
├── models/                         # Pydantic data/config models
├── database/
│   ├── sqlite_handler.py           # Embedded config database
│   ├── sqlserver_handler.py        # SQL Server with transactions
│   └── init_scripts/               # Idempotent schema scripts
├── services/
│   ├── metrics_calculator.py       # QC metric calculations
│   ├── polling_service.py          # Background vendor DB poller
│   ├── startup.py                  # SQL connection initialization
│   └── vendor_adapters/            # Pluggable vendor DB adapters
├── api/controllers/                # FastAPI route controllers
├── frontend/                       # HTML, CSS, JS (served by FastAPI)
└── tests/                          # pytest suite (all mocked)
```
