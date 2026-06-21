from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from deerflow.persistence.base import Base
from deerflow.persistence.runtime_config.model import RuntimeConfigRow
from deerflow.persistence.runtime_config.sql import stable_json_hash

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "backend" / "scripts" / "check_stateless_live_gates.py"
REMOTE_LIVE_TEST_PATH = REPO_ROOT / "backend" / "tests" / "test_aio_sandbox_remote_live.py"

spec = importlib.util.spec_from_file_location("check_stateless_live_gates", SCRIPT_PATH)
assert spec is not None
assert spec.loader is not None
live_gate_check = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = live_gate_check
spec.loader.exec_module(live_gate_check)

remote_live_spec = importlib.util.spec_from_file_location("remote_live_smoke_test", REMOTE_LIVE_TEST_PATH)
assert remote_live_spec is not None
assert remote_live_spec.loader is not None
remote_live_smoke_test = importlib.util.module_from_spec(remote_live_spec)
sys.modules[remote_live_spec.name] = remote_live_smoke_test
remote_live_spec.loader.exec_module(remote_live_smoke_test)


def _write_db_app_config(db_path: Path, payload: dict[str, object]) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(
                RuntimeConfigRow(
                    key="app",
                    payload_json=payload,
                    schema_version="1",
                    revision=1,
                    content_hash=stable_json_hash(payload),
                    updated_by="test",
                )
            )
            session.commit()
    finally:
        engine.dispose()


def _write_db_mcp_servers(db_path: Path, payload: dict[str, dict[str, object]]) -> None:
    from deerflow.config.database_config import DatabaseConfig
    from deerflow.config.mcp_store import DbMcpServerStore

    store = DbMcpServerStore(database_config=DatabaseConfig(backend="sqlite", sqlite_dir=str(db_path.parent)))
    store.save_mcp_servers(payload, updated_by="test")


def test_remote_live_gate_requires_host_path_or_prefix():
    report = live_gate_check.build_gate_report(
        ["remote_live"],
        env={
            "DEER_FLOW_RUN_REMOTE_AIO_SANDBOX": "1",
            "DEER_FLOW_REMOTE_AIO_PROVISIONER_URL": "http://provisioner:8002",
        },
    )

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["name"] == "remote_live"
    assert gate["ready_to_invoke"] is False
    assert "DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH or DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX" in gate["missing_env"]
    assert gate["command"] == ["uv", "--directory", "backend", "run", "pytest", "tests/test_aio_sandbox_remote_live.py", "-q"]


def test_acp_sandbox_native_gate_rejects_gateway_acp_in_object_runtime(tmp_path):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "sandbox": {"use": "deerflow.community.aio_sandbox:AioSandboxProvider"},
                "runtime_storage": {
                    "backend": "object",
                    "object_store": {"bucket": "deerflow-runtime"},
                },
                "acp_agents": {
                    "codex": {
                        "command": "codex-acp",
                        "description": "Codex CLI",
                        "execution_mode": "gateway",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    report = live_gate_check.build_gate_report(["acp_sandbox_native"], project_root=tmp_path, env={})

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["name"] == "acp_sandbox_native"
    assert gate["ready_to_invoke"] is False
    assert gate["config_issues"] == [
        "object runtime ACP agent 'codex' must set execution_mode='sandbox' for strict stateless signing"
    ]


def test_acp_sandbox_native_gate_accepts_isolated_sandbox_acp_in_object_runtime(tmp_path):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "sandbox": {
                    "use": "deerflow.community.aio_sandbox:AioSandboxProvider",
                    "ephemeral_profiles": {"codex": {"image": "registry.local/codex-acp:latest"}},
                },
                "runtime_storage": {
                    "backend": "object",
                    "object_store": {"bucket": "deerflow-runtime"},
                },
                "acp_agents": {
                    "codex": {
                        "command": "codex-acp",
                        "description": "Codex CLI",
                        "execution_mode": "sandbox",
                        "sandbox_scope": "isolated",
                        "sandbox_profile": "codex",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    report = live_gate_check.build_gate_report(
        ["acp_sandbox_native"],
        project_root=tmp_path,
        env={"DEER_FLOW_RUN_ACP_SANDBOX_NATIVE": "1"},
    )

    assert report["ok"] is True
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is True
    assert gate["config_issues"] == []
    assert gate["missing_env"] == []
    assert gate["checked_env"] == ["DEER_FLOW_RUN_ACP_SANDBOX_NATIVE"]
    assert gate["command"] == [
        "uv",
        "--directory",
        "backend",
        "run",
        "pytest",
        "tests/test_acp_sandbox_native_live.py",
        "-q",
    ]


def test_acp_sandbox_native_gate_rejects_non_aio_provider_without_agents(tmp_path):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
                "runtime_storage": {
                    "backend": "object",
                    "object_store": {"bucket": "deerflow-runtime"},
                },
                "acp_agents": {},
            }
        ),
        encoding="utf-8",
    )

    report = live_gate_check.build_gate_report(
        ["acp_sandbox_native"],
        project_root=tmp_path,
        env={"DEER_FLOW_RUN_ACP_SANDBOX_NATIVE": "1"},
    )

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is False
    assert gate["config_issues"] == [
        "config.yaml sandbox.use must be AioSandboxProvider for ACP sandbox-native execution"
    ]


def test_acp_sandbox_native_gate_requires_live_opt_in(tmp_path):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "sandbox": {"use": "deerflow.community.aio_sandbox:AioSandboxProvider"},
                "runtime_storage": {
                    "backend": "object",
                    "object_store": {"bucket": "deerflow-runtime"},
                },
                "acp_agents": {
                    "codex": {
                        "command": "codex-acp",
                        "description": "Codex CLI",
                        "execution_mode": "sandbox",
                        "sandbox_scope": "isolated",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    report = live_gate_check.build_gate_report(["acp_sandbox_native"], project_root=tmp_path, env={})

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is False
    assert gate["missing_env"] == ["DEER_FLOW_RUN_ACP_SANDBOX_NATIVE"]
    assert gate["config_issues"] == []


def test_remote_live_gate_accepts_host_path_prefix():
    report = live_gate_check.build_gate_report(
        ["remote_live"],
        env={
            "DEER_FLOW_RUN_REMOTE_AIO_SANDBOX": "1",
            "DEER_FLOW_REMOTE_AIO_PROVISIONER_URL": "http://provisioner:8002",
            "DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX": "/var/lib/deerflow/runtime-skills",
        },
    )

    assert report["ok"] is True
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is True
    assert gate["missing_env"] == []
    assert gate["invalid_env"] == []
    assert gate["checked_env"] == [
        "DEER_FLOW_REMOTE_AIO_PROVISIONER_URL",
        "DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH",
        "DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX",
        "DEER_FLOW_RUN_REMOTE_AIO_SANDBOX",
    ]


def test_remote_live_gate_rejects_invalid_provisioner_url():
    report = live_gate_check.build_gate_report(
        ["remote_live"],
        env={
            "DEER_FLOW_RUN_REMOTE_AIO_SANDBOX": "1",
            "DEER_FLOW_REMOTE_AIO_PROVISIONER_URL": "provisioner:8002",
            "DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX": "/var/lib/deerflow/runtime-skills",
        },
    )

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is False
    assert gate["missing_env"] == []
    assert gate["invalid_env"] == [
        {
            "name": "DEER_FLOW_REMOTE_AIO_PROVISIONER_URL",
            "expected": "http:// or https:// URL",
            "actual": "provisioner:8002",
        }
    ]


def test_remote_live_gate_rejects_relative_host_path_prefix():
    report = live_gate_check.build_gate_report(
        ["remote_live"],
        env={
            "DEER_FLOW_RUN_REMOTE_AIO_SANDBOX": "1",
            "DEER_FLOW_REMOTE_AIO_PROVISIONER_URL": "http://provisioner:8002",
            "DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX": "runtime-skills",
        },
    )

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is False
    assert gate["missing_env"] == []
    assert gate["invalid_env"] == [
        {
            "name": "DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX",
            "expected": "absolute host path",
            "actual": "runtime-skills",
        }
    ]


def test_remote_live_gate_rejects_relative_container_path_and_bad_timeout():
    report = live_gate_check.build_gate_report(
        ["remote_live"],
        env={
            "DEER_FLOW_RUN_REMOTE_AIO_SANDBOX": "1",
            "DEER_FLOW_REMOTE_AIO_PROVISIONER_URL": "http://provisioner:8002",
            "DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH": "/var/lib/deerflow/runtime-skills/sandbox",
            "DEER_FLOW_REMOTE_AIO_SKILLS_CONTAINER_PATH": "mnt/skills",
            "DEER_FLOW_REMOTE_AIO_READY_TIMEOUT": "soon",
        },
    )

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is False
    assert gate["missing_env"] == []
    assert gate["invalid_env"] == [
        {
            "name": "DEER_FLOW_REMOTE_AIO_SKILLS_CONTAINER_PATH",
            "expected": "absolute container path",
            "actual": "mnt/skills",
        },
        {
            "name": "DEER_FLOW_REMOTE_AIO_READY_TIMEOUT",
            "expected": "positive integer seconds",
            "actual": "soon",
        },
    ]


def test_remote_live_pytest_entrypoint_reports_invalid_env_before_backend_create():
    message = remote_live_smoke_test._invalid_remote_live_env_message(
        {
            "DEER_FLOW_REMOTE_AIO_PROVISIONER_URL": "provisioner:8002",
            "DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH": "relative/skills",
            "DEER_FLOW_REMOTE_AIO_SKILLS_CONTAINER_PATH": "mnt/skills",
            "DEER_FLOW_REMOTE_AIO_READY_TIMEOUT": "soon",
        }
    )

    assert message is not None
    assert "DEER_FLOW_REMOTE_AIO_PROVISIONER_URL expected http:// or https:// URL" in message
    assert "DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH expected absolute host path" in message
    assert "DEER_FLOW_REMOTE_AIO_SKILLS_CONTAINER_PATH expected absolute container path" in message
    assert "DEER_FLOW_REMOTE_AIO_READY_TIMEOUT expected positive integer seconds" in message


def test_cli_returns_nonzero_json_when_gate_is_not_ready():
    stdout = io.StringIO()

    exit_code = live_gate_check.main(
        ["--gate", "remote_live", "--json"],
        env={
            "DEER_FLOW_RUN_REMOTE_AIO_SANDBOX": "1",
            "DEER_FLOW_REMOTE_AIO_PROVISIONER_URL": "http://provisioner:8002",
        },
        stdout=stdout,
    )

    assert exit_code == 2
    assert '"ok": false' in stdout.getvalue()
    assert "DEER_FLOW_REMOTE_AIO_SKILLS_HOST_PATH_PREFIX" in stdout.getvalue()


def test_cli_run_refuses_not_ready_gate_without_invoking_runner():
    calls: list[list[str]] = []

    def runner(command: list[str]) -> int:
        calls.append(command)
        return 0

    exit_code = live_gate_check.main(
        ["--gate", "remote_live", "--run"],
        env={},
        runner=runner,
        stdout=io.StringIO(),
    )

    assert exit_code == 2
    assert calls == []


def test_cli_writes_preflight_evidence_for_not_ready_gate(tmp_path, monkeypatch):
    evidence_path = tmp_path / "remote-live-evidence.json"
    git_metadata = {
        "available": True,
        "repo_root": str(REPO_ROOT),
        "head": "test-head",
        "branch": "test-branch",
        "dirty": True,
        "status_short_count": 3,
    }
    monkeypatch.setattr(live_gate_check, "_git_metadata", lambda: git_metadata)

    exit_code = live_gate_check.main(
        ["--gate", "remote_live", "--json", "--evidence-path", str(evidence_path)],
        env={},
        stdout=io.StringIO(),
    )

    assert exit_code == 2
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["schema_version"] == 1
    assert evidence["preflight"]["ok"] is False
    assert evidence["selected_gates"] == ["remote_live"]
    assert evidence["generated_at_utc"].endswith("+00:00")
    assert evidence["cwd"] == str(Path.cwd())
    assert evidence["source"]["git"] == git_metadata
    assert evidence["run_requested"] is False
    assert evidence["executions"] == []
    assert evidence["overall_exit_code"] == 2
    assert "DEER_FLOW_RUN_REMOTE_AIO_SANDBOX" in evidence["preflight"]["gates"][0]["missing_env"]


def test_cli_writes_run_evidence_for_ready_gate(tmp_path):
    evidence_path = tmp_path / "docker-live-evidence.json"
    calls: list[list[str]] = []

    def runner(command: list[str]) -> int:
        calls.append(command)
        return 0

    exit_code = live_gate_check.main(
        ["--gate", "docker_live", "--run", "--evidence-path", str(evidence_path)],
        env={"DEER_FLOW_RUN_LIVE_AIO_SANDBOX": "1"},
        runner=runner,
        stdout=io.StringIO(),
    )

    assert exit_code == 0
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["schema_version"] == 1
    assert calls == [["uv", "--directory", "backend", "run", "pytest", "-m", "docker_live", "-q"]]
    assert evidence["preflight"]["ok"] is True
    assert evidence["selected_gates"] == ["docker_live"]
    assert evidence["run_requested"] is True
    assert evidence["executions"] == [
        {
            "gate": "docker_live",
            "command": ["uv", "--directory", "backend", "run", "pytest", "-m", "docker_live", "-q"],
            "exit_code": 0,
        }
    ]
    assert evidence["overall_exit_code"] == 0


def test_cli_writes_execution_logs_for_run_evidence(tmp_path):
    evidence_path = tmp_path / "docker-live-evidence.json"
    log_dir = tmp_path / "live-gate-logs"

    class RunnerResult:
        returncode = 0
        stdout = "docker live stdout\n"
        stderr = "docker live stderr\n"

        def __int__(self) -> int:
            return self.returncode

    def runner(command: list[str]) -> RunnerResult:
        return RunnerResult()

    try:
        exit_code = live_gate_check.main(
            [
                "--gate",
                "docker_live",
                "--run",
                "--evidence-path",
                str(evidence_path),
                "--evidence-log-dir",
                str(log_dir),
            ],
            env={"DEER_FLOW_RUN_LIVE_AIO_SANDBOX": "1"},
            runner=runner,
            stdout=io.StringIO(),
        )
    except SystemExit as exc:
        exit_code = int(exc.code)

    assert exit_code == 0
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    execution = evidence["executions"][0]
    assert execution["stdout_log_path"] == str(log_dir / "001-docker_live.stdout.log")
    assert execution["stderr_log_path"] == str(log_dir / "001-docker_live.stderr.log")
    assert execution["stdout_log_sha256"] == hashlib.sha256(b"docker live stdout\n").hexdigest()
    assert execution["stderr_log_sha256"] == hashlib.sha256(b"docker live stderr\n").hexdigest()
    assert execution["stdout_log_bytes"] == len(b"docker live stdout\n")
    assert execution["stderr_log_bytes"] == len(b"docker live stderr\n")
    assert Path(execution["stdout_log_path"]).read_text(encoding="utf-8") == "docker live stdout\n"
    assert Path(execution["stderr_log_path"]).read_text(encoding="utf-8") == "docker live stderr\n"


def test_cli_writes_relative_execution_logs_next_to_evidence_bundle(tmp_path, monkeypatch):
    bundle_path = tmp_path / "bundle"
    evidence_path = bundle_path / "docker-live-evidence.json"
    cwd_path = tmp_path / "runner-cwd"
    cwd_path.mkdir()
    monkeypatch.chdir(cwd_path)

    class RunnerResult:
        returncode = 0
        stdout = "portable docker live stdout\n"
        stderr = "portable docker live stderr\n"

        def __int__(self) -> int:
            return self.returncode

    def runner(command: list[str]) -> RunnerResult:
        return RunnerResult()

    exit_code = live_gate_check.main(
        [
            "--gate",
            "docker_live",
            "--run",
            "--evidence-path",
            str(evidence_path),
            "--evidence-log-dir",
            "logs",
        ],
        env={"DEER_FLOW_RUN_LIVE_AIO_SANDBOX": "1"},
        runner=runner,
        stdout=io.StringIO(),
    )

    assert exit_code == 0
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    execution = evidence["executions"][0]
    assert execution["stdout_log_path"] == "logs/001-docker_live.stdout.log"
    assert execution["stderr_log_path"] == "logs/001-docker_live.stderr.log"
    assert (bundle_path / execution["stdout_log_path"]).read_text(encoding="utf-8") == "portable docker live stdout\n"
    assert (bundle_path / execution["stderr_log_path"]).read_text(encoding="utf-8") == "portable docker live stderr\n"

    stdout = io.StringIO()
    validation_exit_code = live_gate_check.main(
        ["--validate-evidence", str(evidence_path), "--require-run", "--require-logs", "--json"],
        stdout=stdout,
    )

    assert validation_exit_code == 0
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is True
    assert validation["success"] is True
    assert validation["errors"] == []


def test_cli_writes_single_evidence_bundle_for_multiple_gates(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "runtime_storage": {
                    "backend": "object",
                    "object_store": {"bucket": "deerflow-runtime"},
                }
            }
        ),
        encoding="utf-8",
    )
    bundle_path = tmp_path / "bundle"
    evidence_path = bundle_path / "evidence.json"

    class RunnerResult:
        returncode = 0

        def __init__(self, command: list[str]) -> None:
            self.stdout = "stdout for " + " ".join(command) + "\n"
            self.stderr = "stderr for " + " ".join(command) + "\n"

        def __int__(self) -> int:
            return self.returncode

    def runner(command: list[str]) -> RunnerResult:
        return RunnerResult(command)

    exit_code = live_gate_check.main(
        [
            "--gate",
            "docker_live",
            "--gate",
            "runtime_object_storage",
            "--run",
            "--evidence-path",
            str(evidence_path),
            "--evidence-log-dir",
            "logs",
        ],
        env={
            "DEER_FLOW_RUN_LIVE_AIO_SANDBOX": "1",
            "DEER_FLOW_CONFIG_PATH": str(config_path),
            "RUNTIME_STORAGE_BACKEND": "object",
        },
        runner=runner,
        stdout=io.StringIO(),
    )

    assert exit_code == 0
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["selected_gates"] == ["docker_live", "runtime_object_storage"]
    assert [execution["gate"] for execution in evidence["executions"]] == ["docker_live", "runtime_object_storage"]
    assert evidence["executions"][0]["stdout_log_path"] == "logs/001-docker_live.stdout.log"
    assert evidence["executions"][1]["stdout_log_path"] == "logs/002-runtime_object_storage.stdout.log"

    stdout = io.StringIO()
    validation_exit_code = live_gate_check.main(
        ["--validate-evidence", str(evidence_path), "--require-run", "--require-logs", "--json"],
        stdout=stdout,
    )

    assert validation_exit_code == 0


def test_cli_validates_successful_evidence_file(tmp_path):
    evidence_path = tmp_path / "successful-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {"git": {"available": False, "error": "not a git checkout"}},
                "preflight": {"ok": True, "gates": [{"name": "docker_live", "checked_env": []}]},
                "selected_gates": ["docker_live"],
                "run_requested": True,
                "executions": [
                    {
                        "gate": "docker_live",
                        "command": ["uv", "--directory", "backend", "run", "pytest", "-m", "docker_live", "-q"],
                        "exit_code": 0,
                    }
                ],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(["--validate-evidence", str(evidence_path), "--json"], stdout=stdout)

    assert exit_code == 0
    validation = json.loads(stdout.getvalue())
    assert validation == {
        "valid": True,
        "success": True,
        "schema_version": 1,
        "overall_exit_code": 0,
        "selected_gates": ["docker_live"],
        "errors": [],
    }


def test_cli_validate_evidence_require_run_accepts_executed_gate_evidence(tmp_path):
    evidence_path = tmp_path / "successful-run-evidence.json"
    expected_command = ["uv", "--directory", "backend", "run", "pytest", "-m", "docker_live", "-q"]
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {"git": {"available": False, "error": "not a git checkout"}},
                "preflight": {
                    "ok": True,
                    "gates": [{"name": "docker_live", "command": expected_command, "ready_to_invoke": True, "checked_env": []}],
                },
                "selected_gates": ["docker_live"],
                "run_requested": True,
                "executions": [
                    {
                        "gate": "docker_live",
                        "command": expected_command,
                        "exit_code": 0,
                    }
                ],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(
        ["--validate-evidence", str(evidence_path), "--require-run", "--json"],
        stdout=stdout,
    )

    assert exit_code == 0
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is True
    assert validation["success"] is True
    assert validation["errors"] == []


def test_cli_validate_evidence_require_run_rejects_mismatched_execution_command(tmp_path):
    evidence_path = tmp_path / "mismatched-command-evidence.json"
    expected_command = ["uv", "--directory", "backend", "run", "pytest", "-m", "docker_live", "-q"]
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {"git": {"available": False, "error": "not a git checkout"}},
                "preflight": {"ok": True, "gates": [{"name": "docker_live", "command": expected_command, "ready_to_invoke": True}]},
                "selected_gates": ["docker_live"],
                "run_requested": True,
                "executions": [
                    {
                        "gate": "docker_live",
                        "command": ["uv", "--directory", "backend", "run", "pytest", "tests/not-the-live-gate.py", "-q"],
                        "exit_code": 0,
                    }
                ],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(
        ["--validate-evidence", str(evidence_path), "--require-run", "--json"],
        stdout=stdout,
    )

    assert exit_code == 1
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is False
    assert validation["success"] is False
    assert validation["overall_exit_code"] == 0
    assert "execution command must match preflight command for gate docker_live" in validation["errors"]


def test_cli_validate_evidence_require_run_rejects_empty_selected_gates(tmp_path):
    evidence_path = tmp_path / "empty-selected-gates-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {"git": {"available": False, "error": "not a git checkout"}},
                "preflight": {"ok": True, "gates": []},
                "selected_gates": [],
                "run_requested": True,
                "executions": [],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(
        ["--validate-evidence", str(evidence_path), "--require-run", "--json"],
        stdout=stdout,
    )

    assert exit_code == 1
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is False
    assert validation["success"] is False
    assert "selected_gates must contain at least one gate when --require-run is used" in validation["errors"]


def test_cli_validate_evidence_require_run_rejects_unknown_selected_gate(tmp_path):
    evidence_path = tmp_path / "unknown-selected-gate-evidence.json"
    expected_command = ["uv", "--directory", "backend", "run", "pytest", "tests/test_unknown_live_gate.py", "-q"]
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {"git": {"available": False, "error": "not a git checkout"}},
                "preflight": {"ok": True, "gates": [{"name": "unknown_live", "command": expected_command}]},
                "selected_gates": ["unknown_live"],
                "run_requested": True,
                "executions": [{"gate": "unknown_live", "command": expected_command, "exit_code": 0}],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(
        ["--validate-evidence", str(evidence_path), "--require-run", "--json"],
        stdout=stdout,
    )

    assert exit_code == 1
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is False
    assert validation["success"] is False
    assert "selected_gates contains unknown gates when --require-run is used: unknown_live" in validation["errors"]


def test_cli_validate_evidence_require_run_rejects_not_ready_preflight(tmp_path):
    evidence_path = tmp_path / "not-ready-preflight-success-evidence.json"
    expected_command = ["uv", "--directory", "backend", "run", "pytest", "tests/test_aio_sandbox_remote_live.py", "-q"]
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {"git": {"available": False, "error": "not a git checkout"}},
                "preflight": {
                    "ok": False,
                    "gates": [
                        {
                            "name": "remote_live",
                            "command": expected_command,
                            "ready_to_invoke": False,
                            "missing_env": ["DEER_FLOW_RUN_REMOTE_AIO_SANDBOX"],
                        }
                    ],
                },
                "selected_gates": ["remote_live"],
                "run_requested": True,
                "executions": [{"gate": "remote_live", "command": expected_command, "exit_code": 0}],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(
        ["--validate-evidence", str(evidence_path), "--require-run", "--json"],
        stdout=stdout,
    )

    assert exit_code == 1
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is False
    assert validation["success"] is False
    assert "preflight.ok must be true when --require-run is used" in validation["errors"]


def test_cli_validate_evidence_require_run_rejects_not_ready_selected_gate(tmp_path):
    evidence_path = tmp_path / "not-ready-selected-gate-success-evidence.json"
    expected_command = ["uv", "--directory", "backend", "run", "pytest", "-m", "docker_live", "-q"]
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {"git": {"available": False, "error": "not a git checkout"}},
                "preflight": {
                    "ok": True,
                    "gates": [{"name": "docker_live", "command": expected_command, "ready_to_invoke": False}],
                },
                "selected_gates": ["docker_live"],
                "run_requested": True,
                "executions": [{"gate": "docker_live", "command": expected_command, "exit_code": 0}],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(
        ["--validate-evidence", str(evidence_path), "--require-run", "--json"],
        stdout=stdout,
    )

    assert exit_code == 1
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is False
    assert validation["success"] is False
    assert "preflight gate docker_live ready_to_invoke must be true when --require-run is used" in validation["errors"]


def test_cli_validate_evidence_require_run_rejects_unselected_execution_gate(tmp_path):
    evidence_path = tmp_path / "unselected-execution-gate-evidence.json"
    expected_command = ["uv", "--directory", "backend", "run", "pytest", "-m", "docker_live", "-q"]
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {"git": {"available": False, "error": "not a git checkout"}},
                "preflight": {
                    "ok": True,
                    "gates": [{"name": "docker_live", "command": expected_command, "ready_to_invoke": True}],
                },
                "selected_gates": ["docker_live"],
                "run_requested": True,
                "executions": [
                    {"gate": "docker_live", "command": expected_command, "exit_code": 0},
                    {"gate": "remote_live", "command": ["uv", "--directory", "backend", "run", "pytest", "tests/test_aio_sandbox_remote_live.py", "-q"], "exit_code": 0},
                ],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(
        ["--validate-evidence", str(evidence_path), "--require-run", "--json"],
        stdout=stdout,
    )

    assert exit_code == 1
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is False
    assert validation["success"] is False
    assert "executions contain gates that were not selected when --require-run is used: remote_live" in validation["errors"]


def test_cli_validate_evidence_require_run_rejects_duplicate_execution_gate(tmp_path):
    evidence_path = tmp_path / "duplicate-execution-gate-evidence.json"
    expected_command = ["uv", "--directory", "backend", "run", "pytest", "-m", "docker_live", "-q"]
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {"git": {"available": False, "error": "not a git checkout"}},
                "preflight": {
                    "ok": True,
                    "gates": [{"name": "docker_live", "command": expected_command, "ready_to_invoke": True}],
                },
                "selected_gates": ["docker_live"],
                "run_requested": True,
                "executions": [
                    {"gate": "docker_live", "command": expected_command, "exit_code": 0},
                    {"gate": "docker_live", "command": expected_command, "exit_code": 0},
                ],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(
        ["--validate-evidence", str(evidence_path), "--require-run", "--json"],
        stdout=stdout,
    )

    assert exit_code == 1
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is False
    assert validation["success"] is False
    assert "executions must contain exactly one record per selected gate when --require-run is used: duplicate docker_live" in validation["errors"]


def test_cli_validate_evidence_require_run_rejects_duplicate_selected_gate(tmp_path):
    evidence_path = tmp_path / "duplicate-selected-gate-evidence.json"
    expected_command = ["uv", "--directory", "backend", "run", "pytest", "-m", "docker_live", "-q"]
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {"git": {"available": False, "error": "not a git checkout"}},
                "preflight": {
                    "ok": True,
                    "gates": [{"name": "docker_live", "command": expected_command, "ready_to_invoke": True}],
                },
                "selected_gates": ["docker_live", "docker_live"],
                "run_requested": True,
                "executions": [{"gate": "docker_live", "command": expected_command, "exit_code": 0}],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(
        ["--validate-evidence", str(evidence_path), "--require-run", "--json"],
        stdout=stdout,
    )

    assert exit_code == 1
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is False
    assert validation["success"] is False
    assert "selected_gates must contain each gate at most once when --require-run is used: duplicate docker_live" in validation["errors"]


def test_cli_validate_evidence_require_run_rejects_duplicate_selected_preflight_gate(tmp_path):
    evidence_path = tmp_path / "duplicate-selected-preflight-gate-evidence.json"
    expected_command = ["uv", "--directory", "backend", "run", "pytest", "-m", "docker_live", "-q"]
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {"git": {"available": False, "error": "not a git checkout"}},
                "preflight": {
                    "ok": True,
                    "gates": [
                        {"name": "docker_live", "command": expected_command, "ready_to_invoke": True},
                        {"name": "docker_live", "command": expected_command, "ready_to_invoke": True},
                    ],
                },
                "selected_gates": ["docker_live"],
                "run_requested": True,
                "executions": [{"gate": "docker_live", "command": expected_command, "exit_code": 0}],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(
        ["--validate-evidence", str(evidence_path), "--require-run", "--json"],
        stdout=stdout,
    )

    assert exit_code == 1
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is False
    assert validation["success"] is False
    assert "preflight.gates must contain each selected gate at most once when --require-run is used: duplicate docker_live" in validation["errors"]


def test_cli_validate_evidence_require_logs_rejects_missing_execution_log_paths(tmp_path):
    evidence_path = tmp_path / "successful-run-without-logs-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {"git": {"available": False, "error": "not a git checkout"}},
                "preflight": {"ok": True, "gates": [{"name": "docker_live", "ready_to_invoke": True, "checked_env": []}]},
                "selected_gates": ["docker_live"],
                "run_requested": True,
                "executions": [
                    {
                        "gate": "docker_live",
                        "command": ["uv", "--directory", "backend", "run", "pytest", "-m", "docker_live", "-q"],
                        "exit_code": 0,
                    }
                ],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    try:
        exit_code = live_gate_check.main(
            ["--validate-evidence", str(evidence_path), "--require-logs", "--json"],
            stdout=stdout,
        )
    except SystemExit as exc:
        exit_code = int(exc.code)

    assert exit_code == 1
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is False
    assert validation["success"] is False
    assert "execution stdout_log_path must be a string for gate docker_live when --require-logs is used" in validation["errors"]
    assert "execution stderr_log_path must be a string for gate docker_live when --require-logs is used" in validation["errors"]


def test_cli_validate_evidence_require_logs_resolves_relative_paths_from_evidence_dir(tmp_path):
    bundle_path = tmp_path / "bundle"
    log_path = bundle_path / "logs"
    log_path.mkdir(parents=True)
    (log_path / "001-docker_live.stdout.log").write_text("docker stdout\n", encoding="utf-8")
    (log_path / "001-docker_live.stderr.log").write_text("docker stderr\n", encoding="utf-8")
    evidence_path = bundle_path / "successful-run-with-relative-logs-evidence.json"
    expected_command = ["uv", "--directory", "backend", "run", "pytest", "-m", "docker_live", "-q"]
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {"git": {"available": False, "error": "not a git checkout"}},
                "preflight": {
                    "ok": True,
                    "gates": [{"name": "docker_live", "command": expected_command, "ready_to_invoke": True, "checked_env": []}],
                },
                "selected_gates": ["docker_live"],
                "run_requested": True,
                "executions": [
                    {
                        "gate": "docker_live",
                        "command": expected_command,
                        "exit_code": 0,
                        "stdout_log_path": "logs/001-docker_live.stdout.log",
                        "stdout_log_sha256": hashlib.sha256(b"docker stdout\n").hexdigest(),
                        "stdout_log_bytes": len(b"docker stdout\n"),
                        "stderr_log_path": "logs/001-docker_live.stderr.log",
                        "stderr_log_sha256": hashlib.sha256(b"docker stderr\n").hexdigest(),
                        "stderr_log_bytes": len(b"docker stderr\n"),
                    }
                ],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(
        ["--validate-evidence", str(evidence_path), "--require-run", "--require-logs", "--json"],
        stdout=stdout,
    )

    assert exit_code == 0
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is True
    assert validation["success"] is True
    assert validation["errors"] == []


def test_cli_validate_evidence_require_logs_rejects_tampered_execution_log(tmp_path):
    bundle_path = tmp_path / "bundle"
    log_path = bundle_path / "logs"
    log_path.mkdir(parents=True)
    (log_path / "001-docker_live.stdout.log").write_text("tampered stdout with extra bytes\n", encoding="utf-8")
    (log_path / "001-docker_live.stderr.log").write_text("docker stderr\n", encoding="utf-8")
    evidence_path = bundle_path / "tampered-run-logs-evidence.json"
    expected_command = ["uv", "--directory", "backend", "run", "pytest", "-m", "docker_live", "-q"]
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {"git": {"available": False, "error": "not a git checkout"}},
                "preflight": {
                    "ok": True,
                    "gates": [{"name": "docker_live", "command": expected_command, "ready_to_invoke": True, "checked_env": []}],
                },
                "selected_gates": ["docker_live"],
                "run_requested": True,
                "executions": [
                    {
                        "gate": "docker_live",
                        "command": expected_command,
                        "exit_code": 0,
                        "stdout_log_path": "logs/001-docker_live.stdout.log",
                        "stdout_log_sha256": hashlib.sha256(b"original stdout\n").hexdigest(),
                        "stdout_log_bytes": len(b"original stdout\n"),
                        "stderr_log_path": "logs/001-docker_live.stderr.log",
                        "stderr_log_sha256": hashlib.sha256(b"docker stderr\n").hexdigest(),
                        "stderr_log_bytes": len(b"docker stderr\n"),
                    }
                ],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(
        ["--validate-evidence", str(evidence_path), "--require-run", "--require-logs", "--json"],
        stdout=stdout,
    )

    assert exit_code == 1
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is False
    assert validation["success"] is False
    assert "execution stdout_log_bytes does not match file size for gate docker_live" in validation["errors"]
    assert "execution stdout_log_sha256 does not match file content for gate docker_live" in validation["errors"]


def test_cli_validate_evidence_require_run_rejects_preflight_only_success(tmp_path):
    evidence_path = tmp_path / "preflight-only-evidence.json"
    expected_command = ["uv", "--directory", "backend", "run", "pytest", "tests/test_aio_sandbox_remote_live.py", "-q"]
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {"git": {"available": True}},
                "preflight": {
                    "ok": True,
                    "gates": [{"name": "remote_live", "command": expected_command, "ready_to_invoke": True, "checked_env": []}],
                },
                "selected_gates": ["remote_live"],
                "run_requested": False,
                "executions": [],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(
        ["--validate-evidence", str(evidence_path), "--require-run", "--json"],
        stdout=stdout,
    )

    assert exit_code == 1
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is False
    assert validation["success"] is False
    assert validation["overall_exit_code"] == 0
    assert "run_requested must be true when --require-run is used" in validation["errors"]
    assert "executions must include every selected gate when --require-run is used: missing remote_live" in validation["errors"]


def test_cli_validate_evidence_require_run_rejects_success_with_failed_execution(tmp_path):
    evidence_path = tmp_path / "inconsistent-success-evidence.json"
    expected_command = ["uv", "--directory", "backend", "run", "pytest", "tests/test_aio_sandbox_remote_live.py", "-q"]
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {"git": {"available": True}},
                "preflight": {"ok": True, "gates": [{"name": "remote_live", "command": expected_command, "checked_env": []}]},
                "selected_gates": ["remote_live"],
                "run_requested": True,
                "executions": [
                    {
                        "gate": "remote_live",
                        "command": expected_command,
                        "exit_code": 2,
                    }
                ],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(
        ["--validate-evidence", str(evidence_path), "--require-run", "--json"],
        stdout=stdout,
    )

    assert exit_code == 1
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is False
    assert validation["success"] is False
    assert validation["overall_exit_code"] == 0
    assert "overall_exit_code 0 conflicts with failed execution exit_code: remote_live=2" in validation["errors"]


def test_cli_validate_evidence_returns_gate_status_for_failed_evidence(tmp_path):
    evidence_path = tmp_path / "failed-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {"git": {"available": True}},
                "preflight": {"ok": False, "gates": [{"name": "remote_live", "checked_env": []}]},
                "selected_gates": ["remote_live"],
                "run_requested": False,
                "executions": [],
                "overall_exit_code": 2,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(["--validate-evidence", str(evidence_path), "--json"], stdout=stdout)

    assert exit_code == 2
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is True
    assert validation["success"] is False
    assert validation["overall_exit_code"] == 2
    assert validation["selected_gates"] == ["remote_live"]
    assert validation["errors"] == []


def test_cli_validate_evidence_rejects_missing_schema_version(tmp_path):
    evidence_path = tmp_path / "invalid-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {},
                "preflight": {"ok": True, "gates": []},
                "selected_gates": [],
                "run_requested": False,
                "executions": [],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(["--validate-evidence", str(evidence_path), "--json"], stdout=stdout)

    assert exit_code == 1
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is False
    assert validation["success"] is False
    assert validation["schema_version"] is None
    assert validation["overall_exit_code"] == 0
    assert "schema_version must be 1" in validation["errors"]


def test_cli_validate_evidence_rejects_boolean_schema_version(tmp_path):
    evidence_path = tmp_path / "bool-schema-evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": True,
                "generated_at_utc": "2026-06-21T00:00:00+00:00",
                "cwd": str(REPO_ROOT / "backend"),
                "source": {},
                "preflight": {"ok": True, "gates": []},
                "selected_gates": [],
                "run_requested": False,
                "executions": [],
                "overall_exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = live_gate_check.main(["--validate-evidence", str(evidence_path), "--json"], stdout=stdout)

    assert exit_code == 1
    validation = json.loads(stdout.getvalue())
    assert validation["valid"] is False
    assert validation["schema_version"] is None
    assert "schema_version must be 1" in validation["errors"]


def test_requires_llm_gate_requires_config_yaml_with_models(tmp_path):
    report = live_gate_check.build_gate_report(["requires_llm"], env={}, project_root=tmp_path)

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is False
    assert gate["config_issues"] == ["config.yaml not found"]


def test_requires_llm_gate_rejects_empty_models_config(tmp_path):
    (tmp_path / "config.yaml").write_text("models:\n", encoding="utf-8")

    report = live_gate_check.build_gate_report(["requires_llm"], env={}, project_root=tmp_path)

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is False
    assert gate["config_issues"] == ["config.yaml has no configured models"]


def test_requires_llm_gate_accepts_config_with_models(tmp_path):
    (tmp_path / "config.yaml").write_text(
        """
models:
  - name: smoke
    use: langchain_openai:ChatOpenAI
    model: gpt-4o-mini
    api_key: $OPENAI_API_KEY
""",
        encoding="utf-8",
    )

    report = live_gate_check.build_gate_report(
        ["requires_llm"],
        env={"OPENAI_API_KEY": "test-key"},
        project_root=tmp_path,
    )

    assert report["ok"] is True
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is True
    assert gate["missing_env"] == []
    assert gate["config_issues"] == []
    assert gate["checked_env"] == ["OPENAI_API_KEY"]


def test_requires_llm_gate_accepts_deer_flow_config_path(tmp_path):
    config_path = tmp_path / "custom-config.yaml"
    config_path.write_text(
        """
models:
  - name: smoke
    use: langchain_openai:ChatOpenAI
    model: gpt-4o-mini
    api_key: $OPENAI_API_KEY
""",
        encoding="utf-8",
    )
    project_root = tmp_path / "empty-project"
    project_root.mkdir()

    report = live_gate_check.build_gate_report(
        ["requires_llm"],
        env={
            "DEER_FLOW_CONFIG_PATH": str(config_path),
            "OPENAI_API_KEY": "test-key",
        },
        project_root=project_root,
    )

    assert report["ok"] is True
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is True
    assert gate["missing_env"] == []
    assert gate["config_issues"] == []


def test_requires_llm_gate_accepts_deer_flow_project_root(tmp_path):
    runtime_project_root = tmp_path / "runtime-project"
    runtime_project_root.mkdir()
    (runtime_project_root / "config.yaml").write_text(
        """
models:
  - name: smoke
    use: langchain_openai:ChatOpenAI
    model: gpt-4o-mini
    api_key: $OPENAI_API_KEY
""",
        encoding="utf-8",
    )
    default_project_root = tmp_path / "empty-default-project"
    default_project_root.mkdir()

    report = live_gate_check.build_gate_report(
        ["requires_llm"],
        env={
            "DEER_FLOW_PROJECT_ROOT": str(runtime_project_root),
            "OPENAI_API_KEY": "test-key",
        },
        project_root=default_project_root,
    )

    assert report["ok"] is True
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is True
    assert gate["missing_env"] == []
    assert gate["config_issues"] == []


def test_requires_llm_gate_reports_missing_deer_flow_project_root(tmp_path):
    missing_project_root = tmp_path / "missing-project"

    report = live_gate_check.build_gate_report(
        ["requires_llm"],
        env={"DEER_FLOW_PROJECT_ROOT": str(missing_project_root)},
        project_root=tmp_path,
    )

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is False
    assert gate["config_issues"] == [
        "DEER_FLOW_PROJECT_ROOT is set to "
        f"{str(missing_project_root)!r}, but the resolved path {str(missing_project_root.resolve())!r} does not exist."
    ]


def test_requires_llm_gate_reports_file_deer_flow_project_root(tmp_path):
    project_root_file = tmp_path / "project-root-file"
    project_root_file.write_text("not a directory", encoding="utf-8")

    report = live_gate_check.build_gate_report(
        ["requires_llm"],
        env={"DEER_FLOW_PROJECT_ROOT": str(project_root_file)},
        project_root=tmp_path,
    )

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is False
    assert gate["config_issues"] == [
        "DEER_FLOW_PROJECT_ROOT is set to "
        f"{str(project_root_file)!r}, but the resolved path {str(project_root_file.resolve())!r} is not a directory."
    ]


def test_requires_llm_gate_reports_missing_file_model_env_reference(tmp_path):
    (tmp_path / "config.yaml").write_text(
        """
models:
  - name: smoke
    use: langchain_openai:ChatOpenAI
    model: gpt-4o-mini
    api_key: $OPENAI_API_KEY
""",
        encoding="utf-8",
    )

    report = live_gate_check.build_gate_report(["requires_llm"], env={}, project_root=tmp_path)

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is False
    assert gate["missing_env"] == ["OPENAI_API_KEY"]
    assert gate["config_issues"] == []


def test_requires_llm_gate_reports_nested_file_model_env_reference(tmp_path):
    (tmp_path / "config.yaml").write_text(
        """
models:
  - name: smoke
    use: langchain_openai:ChatOpenAI
    model: gpt-4o-mini
    default_headers:
      api-key: $AZURE_OPENAI_API_KEY
""",
        encoding="utf-8",
    )

    report = live_gate_check.build_gate_report(["requires_llm"], env={}, project_root=tmp_path)

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is False
    assert gate["missing_env"] == ["AZURE_OPENAI_API_KEY"]
    assert gate["config_issues"] == []


def test_requires_llm_gate_accepts_db_config_with_models(tmp_path):
    db_path = tmp_path / "deerflow.db"
    payload = {
        "models": [
            {
                "name": "db-smoke",
                "use": "langchain_openai:ChatOpenAI",
                "model": "gpt-4o-mini",
                "api_key": "$OPENAI_API_KEY",
            }
        ]
    }
    _write_db_app_config(db_path, payload)

    report = live_gate_check.build_gate_report(
        ["requires_llm"],
        env={
            "DEER_FLOW_CONFIG_SOURCE": "db",
            "DEER_FLOW_DATABASE_URL": f"sqlite:///{db_path}",
            "OPENAI_API_KEY": "test-key",
        },
        project_root=tmp_path,
    )

    assert report["ok"] is True
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is True
    assert gate["missing_env"] == []
    assert gate["config_issues"] == []


def test_requires_llm_gate_uses_bootstrap_sqlite_database_path(tmp_path):
    db_path = tmp_path / "deerflow.db"
    payload = {
        "models": [
            {
                "name": "db-smoke",
                "use": "langchain_openai:ChatOpenAI",
                "model": "gpt-4o-mini",
                "api_key": "$OPENAI_API_KEY",
            }
        ]
    }
    _write_db_app_config(db_path, payload)

    report = live_gate_check.build_gate_report(
        ["requires_llm"],
        env={
            "DEER_FLOW_CONFIG_SOURCE": "db",
            "DEER_FLOW_DATABASE_URL": f"sqlite:///{tmp_path / 'bootstrap-placeholder.db'}",
            "OPENAI_API_KEY": "test-key",
        },
        project_root=tmp_path,
    )

    assert report["ok"] is True
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is True
    assert gate["missing_env"] == []
    assert gate["config_issues"] == []


def test_requires_llm_gate_reports_missing_db_model_env_reference(tmp_path):
    db_path = tmp_path / "deerflow.db"
    payload = {
        "models": [
            {
                "name": "db-smoke",
                "use": "langchain_openai:ChatOpenAI",
                "model": "gpt-4o-mini",
                "api_key": "$OPENAI_API_KEY",
            }
        ]
    }
    _write_db_app_config(db_path, payload)

    report = live_gate_check.build_gate_report(
        ["requires_llm"],
        env={
            "DEER_FLOW_CONFIG_SOURCE": "db",
            "DEER_FLOW_DATABASE_URL": f"sqlite:///{db_path}",
        },
        project_root=tmp_path,
    )

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is False
    assert gate["missing_env"] == ["OPENAI_API_KEY"]
    assert gate["config_issues"] == []


def test_requires_llm_gate_db_mode_requires_database_url(tmp_path):
    report = live_gate_check.build_gate_report(
        ["requires_llm"],
        env={"DEER_FLOW_CONFIG_SOURCE": "db"},
        project_root=tmp_path,
    )

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is False
    assert gate["config_issues"] == ["DEER_FLOW_DATABASE_URL is required when DEER_FLOW_CONFIG_SOURCE=db"]
    assert gate["checked_env"] == ["DEER_FLOW_DATABASE_URL"]


def test_requires_llm_gate_db_mode_reports_checked_env_when_app_config_missing(tmp_path):
    db_path = tmp_path / "deerflow.db"
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()

    report = live_gate_check.build_gate_report(
        ["requires_llm"],
        env={
            "DEER_FLOW_CONFIG_SOURCE": "db",
            "DEER_FLOW_DATABASE_URL": f"sqlite:///{db_path}",
        },
        project_root=tmp_path,
    )

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is False
    assert gate["config_issues"] == ["DB runtime config key 'app' not found"]
    assert gate["checked_env"] == ["DEER_FLOW_DATABASE_URL"]


def test_mcp_stateless_gate_rejects_enabled_stdio_without_runtime_mode(tmp_path):
    (tmp_path / "extensions_config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "local": {
                        "type": "stdio",
                        "command": "npx",
                    },
                    "remote": {
                        "type": "http",
                        "url": "https://mcp.example.test",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    report = live_gate_check.build_gate_report(["mcp_stateless"], env={}, project_root=tmp_path)

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is False
    assert gate["config_issues"] == [
        "enabled stdio MCP server 'local' is not strict-stateless compatible; set stateless.runtime_mode to sticky, sidecar, or single-node"
    ]
    assert gate["mcp_compatibility"] == [
        {
            "name": "local",
            "enabled": True,
            "transport": "stdio",
            "strict_stateless": False,
            "runtime_mode": None,
        },
        {
            "name": "remote",
            "enabled": True,
            "transport": "http",
            "strict_stateless": True,
            "runtime_mode": "remote",
        },
    ]


def test_runtime_object_storage_gate_rejects_filesystem_runtime(tmp_path):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"runtime_storage": {"backend": "filesystem"}}),
        encoding="utf-8",
    )

    report = live_gate_check.build_gate_report(["runtime_object_storage"], env={}, project_root=tmp_path)

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is False
    assert gate["config_issues"] == [
        "config.yaml runtime_storage.backend must be 'object' for runtime PVC removal",
        "config.yaml runtime_storage.object_store is required when backend is object",
        "provisioner RUNTIME_STORAGE_BACKEND must be 'object' for runtime PVC removal",
    ]
    assert gate["checked_env"] == ["RUNTIME_STORAGE_BACKEND", "USERDATA_PVC_NAME"]


def test_runtime_object_storage_gate_accepts_object_runtime(tmp_path):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "runtime_storage": {
                    "backend": "object",
                    "object_store": {
                        "endpoint_url": "http://seaweedfs:8333",
                        "bucket": "deerflow-runtime",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    report = live_gate_check.build_gate_report(
        ["runtime_object_storage"],
        env={"RUNTIME_STORAGE_BACKEND": "object"},
        project_root=tmp_path,
    )

    assert report["ok"] is True
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is True
    assert gate["config_issues"] == []


def test_mcp_stateless_gate_accepts_http_and_explicitly_tagged_stdio(tmp_path):
    (tmp_path / "extensions_config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "type": "stdio",
                        "command": "npx",
                        "stateless": {"runtime_mode": "sidecar"},
                    },
                    "remote": {
                        "transport": "sse",
                        "url": "https://mcp.example.test/sse",
                    },
                    "disabled-local": {
                        "type": "stdio",
                        "enabled": False,
                        "command": "local-mcp",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    report = live_gate_check.build_gate_report(["mcp_stateless"], env={}, project_root=tmp_path)

    assert report["ok"] is True
    gate = report["gates"][0]
    assert gate["ready_to_invoke"] is True
    assert gate["config_issues"] == []
    assert {item["name"]: item["runtime_mode"] for item in gate["mcp_compatibility"]} == {
        "disabled-local": "disabled",
        "github": "sidecar",
        "remote": "remote",
    }


def test_mcp_stateless_gate_checks_db_mcp_config(tmp_path):
    db_path = tmp_path / "deerflow.db"
    _write_db_mcp_servers(
        db_path,
        {
            "github": {
                "type": "stdio",
                "command": "npx",
            },
        },
    )

    report = live_gate_check.build_gate_report(
        ["mcp_stateless"],
        env={
            "DEER_FLOW_CONFIG_SOURCE": "db",
            "DEER_FLOW_DATABASE_URL": f"sqlite:///{db_path}",
        },
        project_root=tmp_path,
    )

    assert report["ok"] is False
    gate = report["gates"][0]
    assert gate["checked_env"] == ["DEER_FLOW_DATABASE_URL"]
    assert gate["config_issues"] == [
        "enabled stdio MCP server 'github' is not strict-stateless compatible; set stateless.runtime_mode to sticky, sidecar, or single-node"
    ]
