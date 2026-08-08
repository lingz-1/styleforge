import importlib
import sys


def test_workflow_package_does_not_eagerly_import_executable_graph() -> None:
    sys.modules.pop("styleforge.workflow", None)
    sys.modules.pop("styleforge.workflow.graph", None)

    package = importlib.import_module("styleforge.workflow")

    assert package.__name__ == "styleforge.workflow"
    assert "styleforge.workflow.graph" not in sys.modules
