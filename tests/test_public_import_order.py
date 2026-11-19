"""Public packages must import in a fresh interpreter, in any entry order."""
import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", ["agentbus.policy", "agentbus.tools", "agentbus.execution"])
def test_public_package_import_does_not_depend_on_pytest_collection_order(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True, text=True, timeout=30, shell=False,
    )
    assert result.returncode == 0, result.stderr
