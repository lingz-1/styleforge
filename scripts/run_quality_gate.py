"""Run the complete StyleForge quality gate with isolated resources."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = WORKSPACE_ROOT / "apps" / "api"
TEST_RUNTIME_ROOT = WORKSPACE_ROOT / "artifacts" / "test-runtime"
DEFAULT_REPORT_ROOT = WORKSPACE_ROOT / "artifacts" / "quality-gate"
STYLE_PYTHON = Path(r"D:\anaconda\envs\style\python.exe")
DEFAULT_NODE = Path(r"D:\node\node.exe")
API_PORT = 18000
WEB_PORT = 15173
SCHEMA_PATTERN = re.compile(r"quality_[0-9a-f]{12}")

sys.path.insert(0, str(API_ROOT))

from styleforge.repositories.database import connect  # noqa: E402


@dataclass(slots=True)
class StepResult:
    name: str
    status: str
    duration_seconds: float
    exit_code: int
    output_tail: list[str]


class QualityGateError(RuntimeError):
    """A quality-gate step or lifecycle guard failed."""


def _console_safe(text: str, encoding: str | None = None) -> str:
    target = encoding or getattr(sys.stdout, "encoding", None) or "utf-8"
    return text.encode(target, errors="replace").decode(target, errors="replace")


def _console_write(text: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    stream.write(_console_safe(text, getattr(stream, "encoding", None)))
    stream.flush()


def _scoped_dsn(dsn: str, schema: str) -> str:
    _validate_schema_name(schema)
    separator = "&" if "?" in dsn else "?"
    return f"{dsn}{separator}options=-csearch_path%3D{schema}"


def _validate_schema_name(schema: str) -> None:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise QualityGateError(f"Unsafe quality schema name: {schema!r}")


def _validate_runtime_root(path: Path) -> Path:
    resolved = path.resolve()
    allowed = TEST_RUNTIME_ROOT.resolve()
    if not resolved.is_relative_to(allowed) or resolved == allowed:
        raise QualityGateError(f"Unsafe runtime cleanup target: {resolved}")
    if not resolved.name.startswith("quality-"):
        raise QualityGateError(f"Runtime directory lacks quality prefix: {resolved}")
    return resolved


def _create_schema(base_dsn: str, schema: str) -> None:
    _validate_schema_name(schema)
    connection = connect(base_dsn)
    try:
        connection.execute(f'CREATE SCHEMA "{schema}"')  # noqa: S608
        connection.commit()
    finally:
        connection.close()


def _drop_schema(base_dsn: str, schema: str) -> None:
    _validate_schema_name(schema)
    connection = connect(base_dsn)
    try:
        connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')  # noqa: S608
        connection.commit()
    finally:
        connection.close()


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(0.25)
        return client.connect_ex(("127.0.0.1", port)) == 0


def _require_free_port(port: int) -> None:
    if _port_open(port):
        raise QualityGateError(f"Required test port is already in use: {port}")


def _wait_for_port(process: subprocess.Popen, port: int, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            raise QualityGateError(
                f"Server exited before port {port} became ready (exit {code})"
            )
        if _port_open(port):
            return
        time.sleep(0.25)
    raise QualityGateError(f"Server did not open port {port} within {timeout:.0f}s")


def _windows_command(command: Sequence[str]) -> list[str]:
    if os.name != "nt" or not str(command[0]).lower().endswith((".cmd", ".bat")):
        return list(command)
    command_line = subprocess.list2cmdline(list(command))
    return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command_line]


def _popen(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    prefix: str,
) -> tuple[subprocess.Popen, threading.Thread]:
    options: dict[str, Any] = {
        "cwd": str(cwd),
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(_windows_command(command), **options)

    def drain() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            _console_write(f"[{prefix}] {line}")

    thread = threading.Thread(target=drain, name=f"quality-{prefix}", daemon=True)
    thread.start()
    return process, thread


def _terminate_tree(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        import signal

        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _run_step(
    name: str,
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> StepResult:
    print(f"\n=== {name} ===")
    started = time.perf_counter()
    tail: deque[str] = deque(maxlen=120)
    process = subprocess.Popen(
        _windows_command(command),
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    for line in process.stdout:
        _console_write(line)
        tail.append(line.rstrip())
    exit_code = process.wait()
    result = StepResult(
        name=name,
        status="passed" if exit_code == 0 else "failed",
        duration_seconds=round(time.perf_counter() - started, 3),
        exit_code=exit_code,
        output_tail=list(tail),
    )
    if exit_code != 0:
        raise QualityGateError(f"{name} failed with exit code {exit_code}")
    return result


def _base_environment(
    scoped_dsn: str,
    runtime_root: Path,
    output_root: Path,
    run_token: str,
) -> dict[str, str]:
    env = dict(os.environ)
    temp_root = runtime_root / "system-temp"
    temp_root.mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "TEMP": str(temp_root),
            "TMP": str(temp_root),
            "STYLEFORGE_E2E_DATABASE_DSN": scoped_dsn,
            "STYLEFORGE_E2E_ARTIFACT_ROOT": str(runtime_root / "runtime-artifacts"),
            "STYLEFORGE_E2E_OUTPUT_ROOT": str(output_root),
            "STYLEFORGE_E2E_API_PORT": str(API_PORT),
            "STYLEFORGE_E2E_API_ROOT": f"http://127.0.0.1:{API_PORT}",
            "STYLEFORGE_E2E_WEB_ROOT": f"http://127.0.0.1:{WEB_PORT}",
            "STYLEFORGE_API_PROXY_TARGET": f"http://127.0.0.1:{API_PORT}",
            "STYLEFORGE_E2E_USER": f"e2e-quality-{run_token}",
            "STYLEFORGE_SEMANTIC_E2E_USER": f"semantic-e2e-quality-{run_token}",
        }
    )
    return env


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run unit tests, web build, browser E2E, and semantic smoke tests."
    )
    parser.add_argument("--skip-unit", action="store_true", help="Skip full pytest")
    parser.add_argument("--skip-build", action="store_true", help="Skip web production build")
    parser.add_argument("--skip-browser", action="store_true", help="Skip Playwright E2E")
    parser.add_argument("--skip-semantic", action="store_true", help="Skip real FashionCLIP smoke")
    parser.add_argument(
        "--keep-runtime",
        action="store_true",
        help="Keep the isolated runtime directory for debugging",
    )
    parser.add_argument(
        "--report-root",
        type=Path,
        default=DEFAULT_REPORT_ROOT,
        help="Directory that receives timestamped reports and screenshots",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    load_dotenv(WORKSPACE_ROOT / ".env", override=False)
    if Path(sys.executable).resolve() != STYLE_PYTHON.resolve():
        raise QualityGateError(
            f"Use the project interpreter: {STYLE_PYTHON}; got {sys.executable}"
        )
    node = Path(os.getenv("STYLEFORGE_NODE", str(DEFAULT_NODE))).resolve()
    vite = WORKSPACE_ROOT / "apps" / "web" / "node_modules" / "vite" / "bin" / "vite.js"
    if not node.is_file():
        raise QualityGateError(f"Node.js executable not found: {node}")
    if not vite.is_file():
        raise QualityGateError(f"Vite entry point not found: {vite}")
    base_dsn = os.getenv("STYLEFORGE_TEST_DATABASE_DSN", "").strip()
    if not base_dsn:
        raise QualityGateError("STYLEFORGE_TEST_DATABASE_DSN is not configured")

    run_token = uuid.uuid4().hex[:12]
    schema = f"quality_{run_token}"
    runtime_root = _validate_runtime_root(TEST_RUNTIME_ROOT / f"quality-{run_token}")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_root = args.report_root.resolve() / f"{timestamp}-{run_token}"
    output_root = report_root / "screenshots"
    runtime_root.mkdir(parents=True, exist_ok=False)
    output_root.mkdir(parents=True, exist_ok=False)
    scoped_dsn = _scoped_dsn(base_dsn, schema)
    env = _base_environment(scoped_dsn, runtime_root, output_root, run_token)

    results: list[StepResult] = []
    api_process: subprocess.Popen | None = None
    web_process: subprocess.Popen | None = None
    schema_created = False
    status = "failed"
    error = ""
    cleanup: dict[str, Any] = {
        "schema_dropped": False,
        "runtime_removed": False,
        "ports_released": False,
    }
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        _create_schema(base_dsn, schema)
        schema_created = True
        if not args.skip_unit:
            results.append(
                _run_step(
                    "pytest-full",
                    [
                        str(STYLE_PYTHON),
                        "-m",
                        "pytest",
                        "-q",
                        "-p",
                        "no:cacheprovider",
                        "--basetemp",
                        str(runtime_root / "pytest"),
                    ],
                    cwd=WORKSPACE_ROOT,
                    env=env,
                )
            )
            results.append(
                _run_step(
                    "wardrobe-quality-lightweight",
                    [
                        str(STYLE_PYTHON),
                        "-m",
                        "evals.runners.evaluate_wardrobe_quality",
                        "--mode",
                        "deterministic",
                        "--cases",
                        str(WORKSPACE_ROOT / "evals" / "cases" / "wardrobe_quality.json"),
                        "--report",
                        str(report_root / "wardrobe-quality-lightweight.json"),
                    ],
                    cwd=WORKSPACE_ROOT,
                    env=env,
                )
            )
        if not args.skip_build:
            results.append(
                _run_step(
                    "web-build",
                    [str(node), str(vite), "build"],
                    cwd=WORKSPACE_ROOT / "apps" / "web",
                    env=env,
                )
            )
        if not args.skip_browser:
            _require_free_port(API_PORT)
            _require_free_port(WEB_PORT)
            api_process, _ = _popen(
                [str(STYLE_PYTHON), str(WORKSPACE_ROOT / "tests" / "e2e" / "run_api.py")],
                cwd=WORKSPACE_ROOT,
                env=env,
                prefix="api",
            )
            _wait_for_port(api_process, API_PORT)
            web_process, _ = _popen(
                [
                    str(node),
                    str(vite),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(WEB_PORT),
                ],
                cwd=WORKSPACE_ROOT / "apps" / "web",
                env=env,
                prefix="web",
            )
            _wait_for_port(web_process, WEB_PORT)
            results.append(
                _run_step(
                    "browser-e2e",
                    [
                        str(STYLE_PYTHON),
                        str(WORKSPACE_ROOT / "tests" / "e2e" / "styleforge_smoke.py"),
                    ],
                    cwd=WORKSPACE_ROOT,
                    env=env,
                )
            )
            _terminate_tree(web_process)
            web_process = None
            _terminate_tree(api_process)
            api_process = None
        if not args.skip_semantic:
            results.append(
                _run_step(
                    "semantic-retrieval",
                    [
                        str(STYLE_PYTHON),
                        str(
                            WORKSPACE_ROOT
                            / "tests"
                            / "e2e"
                            / "semantic_retrieval_smoke.py"
                        ),
                    ],
                    cwd=WORKSPACE_ROOT,
                    env=env,
                )
            )
        status = "passed"
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        _console_write(f"\nQUALITY GATE FAILED: {error}\n", error=True)
    finally:
        _terminate_tree(web_process)
        _terminate_tree(api_process)
        if schema_created:
            try:
                _drop_schema(base_dsn, schema)
                cleanup["schema_dropped"] = True
            except BaseException as exc:
                cleanup["schema_error"] = f"{type(exc).__name__}: {exc}"
                status = "failed"
        cleanup["ports_released"] = not _port_open(API_PORT) and not _port_open(WEB_PORT)
        if not cleanup["ports_released"]:
            status = "failed"
            cleanup["port_error"] = "One or more E2E ports remain open"
        if not args.keep_runtime:
            try:
                shutil.rmtree(_validate_runtime_root(runtime_root))
                cleanup["runtime_removed"] = True
            except BaseException as exc:
                cleanup["runtime_error"] = f"{type(exc).__name__}: {exc}"
                status = "failed"

        report = {
            "status": status,
            "started_at": started_at,
            "duration_seconds": round(time.perf_counter() - started, 3),
            "schema": schema,
            "steps": [asdict(result) for result in results],
            "cleanup": cleanup,
            "error": error,
            "report_root": str(report_root),
        }
        report_root.mkdir(parents=True, exist_ok=True)
        (report_root / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _console_write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
