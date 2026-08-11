"""The CLI — the interface that has to work without Django, an app, or a settings module.

Every test drives ``main(argv)`` directly, which is also how the Django
management command wraps it: one implementation, one behaviour, no drift.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from taskport.cli import EXIT_ERROR, EXIT_OK, build_parser, main


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """A JSON configuration file, the form the CLI reads outside a framework."""
    path = tmp_path / "taskport.json"
    path.write_text(
        json.dumps(
            {
                "backends": {
                    "inline": {"factory": "inline"},
                    "fast": {"factory": "thread", "max_workers": 2},
                    "batch": {"factory": "subprocess"},
                },
                "routes": [
                    {"kind": "task", "queue": "metadata", "backend": "fast"},
                    {"kind": "job", "profile": "heavy", "backend": "batch"},
                ],
                "defaults": {"inline": "inline", "task": "fast", "job": "batch"},
            }
        ),
        encoding="utf-8",
    )
    return path


def run(config_file: Path, *args: str) -> int:
    return main(["--config", str(config_file), *args])


class TestParser:
    def test_every_documented_command_exists(self) -> None:
        parser = build_parser()
        actions = [a for a in parser._actions if a.dest == "command"]
        assert actions, "the parser should expose subcommands"
        commands = set(actions[0].choices or {})
        assert commands == {
            "backends",
            "capabilities",
            "route",
            "submit-task",
            "submit-job",
            "status",
            "cancel",
            "result",
            "doctor",
        }

    def test_a_command_is_required(self) -> None:
        with pytest.raises(SystemExit):
            main([])


class TestBackends:
    def test_it_lists_backends_routes_and_defaults(
        self, config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run(config_file, "backends") == EXIT_OK
        output = capsys.readouterr().out
        assert "fast" in output
        assert "queue=metadata -> fast" in output
        assert "task" in output

    def test_json_output_is_machine_readable(
        self, config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run(config_file, "--json", "backends") == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert set(payload) == {"backends", "routes", "defaults"}
        assert payload["defaults"]["task"] == "fast"


class TestCapabilities:
    def test_it_reports_what_a_backend_can_do(
        self, config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run(config_file, "capabilities", "fast") == EXIT_OK
        assert "cancel" in capsys.readouterr().out

    def test_it_covers_every_backend_when_none_is_named(
        self, config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run(config_file, "--json", "capabilities") == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert set(payload) == {"inline", "fast", "batch"}

    def test_an_unbuildable_backend_is_reported_not_fatal(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """One broken adapter must not stop you inspecting the rest."""
        path = tmp_path / "broken.json"
        path.write_text(
            json.dumps(
                {
                    "backends": {
                        "ok": {"factory": "inline"},
                        "broken": {"factory": "cloudrun"},  # missing project/location
                    },
                    "defaults": {"inline": "ok"},
                }
            ),
            encoding="utf-8",
        )
        assert main(["--config", str(path), "--json", "capabilities"]) == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert "error" in payload["broken"]
        assert isinstance(payload["ok"], list)


class TestRoute:
    def test_it_explains_which_rule_matched(
        self, config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run(config_file, "route", "--kind", "task", "--queue", "metadata") == EXIT_OK
        output = capsys.readouterr().out
        assert "-> fast" in output
        assert "route #0" in output

    def test_it_falls_back_to_the_default(
        self, config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run(config_file, "route", "--kind", "job", "--profile", "whatever") == EXIT_OK
        assert "default for kind job" in capsys.readouterr().out

    def test_an_unroutable_spec_exits_non_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "noroutes.json"
        path.write_text(
            json.dumps({"backends": {"i": {"factory": "inline"}}, "defaults": {"inline": "i"}}),
            encoding="utf-8",
        )
        assert main(["--config", str(path), "route", "--kind", "task"]) == EXIT_ERROR
        assert "unroutable" in capsys.readouterr().out


class TestSubmit:
    def test_a_task_runs_and_reports_success(
        self, config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = run(
            config_file,
            "submit-task",
            "tasks_fixture:add",
            "--arg",
            "20",
            "--arg",
            "22",
            "--wait",
            "10",
        )
        assert exit_code == EXIT_OK
        assert "succeeded" in capsys.readouterr().out

    def test_json_arguments_are_parsed(
        self, config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = run(
            config_file,
            "--json",
            "submit-task",
            "tasks_fixture:echo",
            "--arg",
            '{"nested": [1, 2]}',
            "--wait",
            "10",
        )
        assert exit_code == EXIT_OK
        assert json.loads(capsys.readouterr().out)["state"] == "succeeded"

    def test_a_bare_word_argument_is_taken_literally(
        self, config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`--arg hello` means the string, not a JSON parse error."""
        exit_code = run(
            config_file,
            "--json",
            "submit-task",
            "tasks_fixture:echo",
            "--arg",
            "hello",
            "--wait",
            "10",
        )
        assert exit_code == EXIT_OK

    def test_a_failing_task_exits_non_zero(self, config_file: Path) -> None:
        """So a shell script or a CI step notices."""
        exit_code = run(config_file, "submit-task", "tasks_fixture:boom", "--wait", "10")
        assert exit_code == EXIT_ERROR

    def test_a_job_runs_as_a_subprocess(
        self, config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = run(
            config_file,
            "submit-job",
            "hello",
            "--wait",
            "30",
            "--",
            sys.executable,
            "-c",
            "print('hi')",
        )
        assert exit_code == EXIT_OK
        assert "succeeded" in capsys.readouterr().out

    def test_env_pairs_are_parsed(self, config_file: Path) -> None:
        exit_code = run(
            config_file,
            "submit-job",
            "envjob",
            "--env",
            "TP=42",
            "--wait",
            "30",
            "--",
            sys.executable,
            "-c",
            "import os,sys; sys.exit(0 if os.environ['TP']=='42' else 1)",
        )
        assert exit_code == EXIT_OK

    def test_a_malformed_env_pair_is_reported(
        self, config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = run(config_file, "submit-job", "j", "--env", "NOEQUALS", "--", "true")
        assert exit_code == EXIT_ERROR
        assert "NAME=VALUE" in capsys.readouterr().err


class TestDoctor:
    def test_a_healthy_deployment_passes(
        self, config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run(config_file, "doctor") == EXIT_OK
        output = capsys.readouterr().out
        assert "[PASS] configuration" in output
        assert "[PASS] backend:fast" in output

    def test_a_backend_that_cannot_be_built_fails_the_check(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The whole point: catch it at deploy time, not at 3am."""
        path = tmp_path / "broken.json"
        path.write_text(
            json.dumps(
                {
                    "backends": {"heavy": {"factory": "cloudrun"}},
                    "defaults": {"job": "heavy"},
                }
            ),
            encoding="utf-8",
        )
        assert main(["--config", str(path), "doctor"]) == EXIT_ERROR
        assert "[FAIL] backend:heavy" in capsys.readouterr().out

    def test_a_missing_default_is_a_warning_not_a_failure(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "partial.json"
        path.write_text(
            json.dumps({"backends": {"i": {"factory": "inline"}}, "defaults": {"inline": "i"}}),
            encoding="utf-8",
        )
        assert main(["--config", str(path), "doctor"]) == EXIT_OK
        assert "[WARN] default:task" in capsys.readouterr().out

    def test_json_output_carries_an_ok_flag(
        self, config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run(config_file, "--json", "doctor") == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert any(check["check"] == "plugins" for check in payload["checks"])


class TestLookupCommands:
    def test_status_needs_to_know_the_owning_backend(
        self, config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A fresh CLI process has no memory of another process's submissions."""
        assert run(config_file, "status", "task_unknown") == EXIT_ERROR
        assert "backend=" in capsys.readouterr().err

    def test_status_reports_an_unknown_id_clearly(
        self, config_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert run(config_file, "status", "task_unknown", "--backend", "fast") == EXIT_ERROR
        assert "ExecutionNotFound" in capsys.readouterr().err


class TestConfigLoading:
    def test_a_missing_config_file_is_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["--config", str(tmp_path / "nope.json"), "backends"]) == EXIT_ERROR
        assert "cannot read" in capsys.readouterr().err

    def test_malformed_json_is_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        assert main(["--config", str(path), "backends"]) == EXIT_ERROR
        assert "not valid JSON" in capsys.readouterr().err

    def test_without_a_config_it_falls_back_to_the_environment(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A container with no TASKPORT_* set still starts and still works."""
        for key in list(dict(__import__("os").environ)):
            if key.startswith("TASKPORT_"):
                monkeypatch.delenv(key, raising=False)
        assert main(["backends"]) == EXIT_OK
        assert "inline" in capsys.readouterr().out
