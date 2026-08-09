from __future__ import annotations

import importlib
import sys


def test_orchestration_package_does_not_eagerly_import_graph() -> None:
    sys.modules.pop("styleforge.orchestration", None)
    sys.modules.pop("styleforge.orchestration.graph", None)

    package = importlib.import_module("styleforge.orchestration")

    assert package.__name__ == "styleforge.orchestration"
    assert "styleforge.orchestration.graph" not in sys.modules
