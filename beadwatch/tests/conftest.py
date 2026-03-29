"""Root conftest for BeadWatch tests.

Adds the beadwatch package directory to sys.path so that test imports like
``from services.metrics_calculator import MetricsCalculator`` work when running
pytest from the project root.
"""
import sys
from pathlib import Path

# Insert beadwatch/ into the path so modules resolve without package install
_beadwatch_root = str(Path(__file__).resolve().parent.parent)
if _beadwatch_root not in sys.path:
    sys.path.insert(0, _beadwatch_root)
