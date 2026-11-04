"""Regression tests for Home Assistant import compatibility."""

from pathlib import Path
import subprocess
import sys


def test_import_without_climate_temperature_unit_export():
    """Load the integration and climate platform without the old re-export."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import homeassistant.components.climate as climate; "
            "climate.__dict__.pop('UnitOfTemperature', None); "
            "import custom_components.proflame_connect_wifi; "
            "import custom_components.proflame_connect_wifi.climate",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
