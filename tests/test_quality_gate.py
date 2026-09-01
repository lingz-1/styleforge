import pytest

from scripts.run_quality_gate import (
    QualityGateError,
    TEST_RUNTIME_ROOT,
    _console_safe,
    _scoped_dsn,
    _validate_runtime_root,
    _validate_schema_name,
)


def test_console_output_replaces_characters_unsupported_by_gbk() -> None:
    assert _console_safe("Vite ➜ ready", "gbk") == "Vite ? ready"


def test_scoped_dsn_adds_isolated_search_path() -> None:
    assert _scoped_dsn("postgresql://u@localhost/db", "quality_abcdef123456") == (
        "postgresql://u@localhost/db?options=-csearch_path%3Dquality_abcdef123456"
    )
    assert _scoped_dsn(
        "postgresql://u@localhost/db?sslmode=disable",
        "quality_abcdef123456",
    ).endswith("&options=-csearch_path%3Dquality_abcdef123456")


def test_schema_guard_rejects_non_quality_names() -> None:
    _validate_schema_name("quality_abcdef123456")
    for unsafe in ("public", "quality_bad", 'quality_abc" CASCADE'):
        with pytest.raises(QualityGateError):
            _validate_schema_name(unsafe)


def test_runtime_guard_allows_only_prefixed_children() -> None:
    safe = TEST_RUNTIME_ROOT / "quality-abcdef123456"
    assert _validate_runtime_root(safe) == safe.resolve()
    for unsafe in (
        TEST_RUNTIME_ROOT,
        TEST_RUNTIME_ROOT / "other",
        TEST_RUNTIME_ROOT.parent,
    ):
        with pytest.raises(QualityGateError):
            _validate_runtime_root(unsafe)
