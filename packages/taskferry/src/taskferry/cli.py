"""``taskferry`` — the command line, with no framework attached.

    taskferry backends              # what is configured, and where each route goes
    taskferry capabilities NAME     # what one backend can actually do
    taskferry route --kind task --queue metadata
    taskferry submit-job NAME --image ... -- python etl.py
    taskferry status EXECUTION_ID --backend NAME
    taskferry cancel EXECUTION_ID --backend NAME
    taskferry result EXECUTION_ID --backend NAME
    taskferry doctor                # is this deployment actually wired up?

Configuration comes from ``TASKFERRY_*`` environment variables by default (see
:meth:`~taskferry.config.TaskferryConfig.from_env`) or from a JSON file passed with
``--config``. Django management commands may wrap this, but Taskferry's CLI does
not need Django, a settings module, or an application at all — which is the point.

Built on :mod:`argparse` so the CLI adds no dependency to a package that promises
to have none.

Execution ids are process-local for the built-in backends, so ``status`` and
``result`` need ``--backend`` when the execution was submitted somewhere else.
The commands say so rather than silently polling every configured engine.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .config import TaskferryConfig
from .errors import TaskferryError
from .execution import ExecutionKind
from .plugins import available_backends
from .runtime import Taskferry
from .specs import JobSpec, Resources, TaskSpec

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNSUPPORTED = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="taskferry",
        description="Inspect and drive a Taskferry deployment.",
    )
    parser.add_argument("--version", action="version", version=f"taskferry {__version__}")
    parser.add_argument(
        "--config",
        type=Path,
        metavar="PATH",
        help="JSON file with the Taskferry configuration (defaults to TASKFERRY_* env vars)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("backends", help="list configured backends, routes and defaults")

    caps = sub.add_parser("capabilities", help="show what a backend can do")
    caps.add_argument("backend", nargs="?", help="backend name (omit for all)")

    route = sub.add_parser("route", help="explain where a spec would be sent")
    route.add_argument("--kind", choices=[k.value for k in ExecutionKind], default="task")
    route.add_argument("--queue", default="default")
    route.add_argument("--profile", default="default")
    route.add_argument("--name", default="probe")

    submit_task = sub.add_parser("submit-task", help="enqueue a task")
    submit_task.add_argument("task", help="'package.module:function'")
    submit_task.add_argument("--queue", default="default")
    submit_task.add_argument("--backend", help="bypass routing")
    submit_task.add_argument(
        "--arg", action="append", default=[], metavar="JSON", help="positional argument (JSON)"
    )
    submit_task.add_argument(
        "--kwarg", action="append", default=[], metavar="NAME=JSON", help="keyword argument"
    )
    submit_task.add_argument("--wait", type=float, metavar="SECONDS", help="wait for completion")

    submit_job = sub.add_parser("submit-job", help="run a job")
    submit_job.add_argument("job", help="job name")
    submit_job.add_argument("--image")
    submit_job.add_argument("--profile", default="default")
    submit_job.add_argument("--backend", help="bypass routing")
    submit_job.add_argument("--cpu")
    submit_job.add_argument("--memory")
    submit_job.add_argument("--gpu", type=int, default=0)
    submit_job.add_argument(
        "--env", action="append", default=[], metavar="NAME=VALUE", help="environment variable"
    )
    submit_job.add_argument("--wait", type=float, metavar="SECONDS", help="wait for completion")
    # dest is "argv", not "command": argparse stores the subcommand name in
    # `command`, and a positional of the same name would silently overwrite it.
    # Everything after a literal `--` is split off before parsing (see main), so
    # this only ever receives a command given without the separator.
    submit_job.add_argument("argv", nargs="*", help="the command to run (prefer: -- cmd args)")

    for name, help_text in (
        ("status", "show an execution's current state"),
        ("cancel", "cancel an execution"),
        ("result", "show an execution's result"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("execution_id")
        cmd.add_argument("--backend", help="backend that owns the execution")
        if name == "result":
            cmd.add_argument("--timeout", type=float, help="seconds to wait for completion")

    sub.add_parser("doctor", help="check that this deployment is wired up correctly")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    # Split on the first literal `--` ourselves. argparse.REMAINDER would do
    # this, but it starts consuming at the first token it does not recognise, so
    # `submit-job j --env A=1 -- python x` hands `--env` to the job instead of to
    # the parser. Splitting first makes the boundary mean exactly what it looks
    # like it means.
    head, tail = _split_on_separator(list(sys.argv[1:] if argv is None else argv))
    args = parser.parse_args(head)
    if tail:
        args.argv = [*getattr(args, "argv", []), *tail]
    try:
        return _dispatch(args)
    except TaskferryError as exc:
        _fail(f"{type(exc).__name__}: {exc}", as_json=args.json)
        return EXIT_ERROR


def _split_on_separator(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split ``[..., "--", ...]`` into the parser's arguments and the job's argv."""
    if "--" not in argv:
        return argv, []
    index = argv.index("--")
    return argv[:index], argv[index + 1 :]


def _dispatch(args: argparse.Namespace) -> int:
    handlers = {
        "backends": _cmd_backends,
        "capabilities": _cmd_capabilities,
        "route": _cmd_route,
        "submit-task": _cmd_submit_task,
        "submit-job": _cmd_submit_job,
        "status": _cmd_status,
        "cancel": _cmd_cancel,
        "result": _cmd_result,
        "doctor": _cmd_doctor,
    }
    return handlers[args.command](args)


# -- configuration --------------------------------------------------------- #
def _load_config(args: argparse.Namespace) -> TaskferryConfig:
    if args.config is None:
        return TaskferryConfig.from_env()
    try:
        payload = json.loads(args.config.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TaskferryError(f"cannot read {args.config}: {exc}") from exc
    except ValueError as exc:
        raise TaskferryError(f"{args.config} is not valid JSON: {exc}") from exc
    return TaskferryConfig.from_mapping(payload)


def _runtime(args: argparse.Namespace) -> Taskferry:
    return Taskferry(config=_load_config(args))


# -- commands ---------------------------------------------------------------- #
def _cmd_backends(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    description = runtime.describe()
    if args.json:
        _emit(description)
        return EXIT_OK
    print("backends:")
    for name, info in description["backends"].items():
        options = f" options={info['options']}" if info["options"] else ""
        print(f"  {name:<20} {info['factory']}{options}")
    print("\nroutes (first match wins):")
    for route in description["routes"] or ["  <none>"]:
        print(f"  {route}")
    print("\ndefaults:")
    for kind, backend in description["defaults"].items() or [("<none>", "")]:
        print(f"  {kind:<20} -> {backend}")
    return EXIT_OK


def _cmd_capabilities(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    names = [args.backend] if args.backend else list(runtime.backend_names())
    report: dict[str, Any] = {}
    for name in names:
        try:
            report[name] = sorted(str(cap) for cap in runtime.capabilities(name))
        except TaskferryError as exc:
            report[name] = {"error": str(exc)}
    if args.json:
        _emit(report)
        return EXIT_OK
    for name, caps in report.items():
        if isinstance(caps, dict):
            print(f"{name}: unavailable — {caps['error']}")
        else:
            print(f"{name}: {', '.join(caps)}")
    return EXIT_OK


def _cmd_route(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    kind = ExecutionKind(args.kind)
    spec = _probe_spec(kind, queue=args.queue, profile=args.profile, name=args.name)
    explanation = runtime.router.explain(spec)
    try:
        backend = runtime.router.resolve(spec)
    except TaskferryError as exc:
        if args.json:
            _emit({"routable": False, "reason": str(exc)})
        else:
            print(f"unroutable: {exc}")
        return EXIT_ERROR
    if args.json:
        _emit({"routable": True, "backend": backend, "matched": explanation})
    else:
        print(f"{kind.value} queue={args.queue} profile={args.profile} -> {backend}")
        print(f"  matched by {explanation}")
    return EXIT_OK


def _cmd_submit_task(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    spec = TaskSpec(
        task=args.task,
        args=tuple(_parse_json(value, "--arg") for value in args.arg),
        kwargs={
            key: _parse_json(value, "--kwarg")
            for key, value in (_split_pair(pair, "--kwarg") for pair in args.kwarg)
        },
        queue=args.queue,
    )
    handle = runtime.submit(spec, backend=args.backend)
    return _report_submission(handle, args)


def _cmd_submit_job(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    command = list(args.argv)
    spec = JobSpec(
        job=args.job,
        image=args.image,
        command=command,
        profile=args.profile,
        env=dict(_split_pair(pair, "--env") for pair in args.env),
        resources=Resources(cpu=args.cpu, memory=args.memory, gpu=args.gpu),
    )
    handle = runtime.submit(spec, backend=args.backend)
    return _report_submission(handle, args)


def _report_submission(handle: Any, args: argparse.Namespace) -> int:
    if args.wait is not None:
        handle.wait(args.wait)
    payload = _execution_payload(handle.execution)
    if args.json:
        _emit(payload)
    else:
        print(f"{payload['id']}  {payload['state']}  backend={payload['backend']}")
        if payload.get("error"):
            print(f"  error: {payload['error']}")
    return EXIT_OK if handle.execution.state.value != "failed" else EXIT_ERROR


def _cmd_status(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    handle = runtime.get(args.execution_id, backend=args.backend)
    payload = _execution_payload(handle.refresh())
    if args.json:
        _emit(payload)
    else:
        print(f"{payload['id']}  {payload['state']}  backend={payload['backend']}")
    return EXIT_OK


def _cmd_cancel(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    execution = runtime.cancel(args.execution_id, backend=args.backend)
    if args.json:
        _emit(_execution_payload(execution))
    else:
        print(f"{execution.id}  {execution.state.value}")
    return EXIT_OK


def _cmd_result(args: argparse.Namespace) -> int:
    runtime = _runtime(args)
    result = runtime.result(args.execution_id, timeout=args.timeout, backend=args.backend)
    payload = {
        "value": result.value,
        "error": result.error,
        "error_type": result.error_type,
        "exit_code": result.exit_code,
        "logs_uri": result.logs_uri,
    }
    if args.json:
        _emit(payload)
    else:
        print(json.dumps(payload["value"], default=str))
    return EXIT_OK


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Check the deployment end to end, and say which part is broken.

    Every configured backend is actually built, because "the adapter is
    installed" and "the adapter can be constructed with these options" are
    different questions and only the second one matters at 3am.
    """
    findings: list[dict[str, str]] = []
    try:
        runtime = _runtime(args)
    except TaskferryError as exc:
        findings.append({"check": "configuration", "status": "fail", "detail": str(exc)})
        return _report_doctor(findings, args)

    findings.append(
        {
            "check": "configuration",
            "status": "ok",
            "detail": f"{len(runtime.config.backends)} backend(s), "
            f"{len(runtime.config.routes)} route(s)",
        }
    )
    findings.append(
        {
            "check": "plugins",
            "status": "ok",
            "detail": ", ".join(available_backends()),
        }
    )
    for name in runtime.backend_names():
        try:
            capabilities = runtime.capabilities(name)
        except Exception as exc:
            findings.append(
                {
                    "check": f"backend:{name}",
                    "status": "fail",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        findings.append(
            {
                "check": f"backend:{name}",
                "status": "ok",
                "detail": ", ".join(sorted(str(c) for c in capabilities)),
            }
        )
    for kind in ExecutionKind:
        default = runtime.router.defaults.get(kind)
        findings.append(
            {
                "check": f"default:{kind.value}",
                "status": "ok" if default else "warn",
                "detail": default or f"no default backend for {kind.value} specs",
            }
        )
    return _report_doctor(findings, args)


def _report_doctor(findings: list[dict[str, str]], args: argparse.Namespace) -> int:
    failed = any(f["status"] == "fail" for f in findings)
    if args.json:
        _emit({"ok": not failed, "checks": findings})
    else:
        symbols = {"ok": "PASS", "warn": "WARN", "fail": "FAIL"}
        for finding in findings:
            print(f"[{symbols[finding['status']]}] {finding['check']}: {finding['detail']}")
    return EXIT_ERROR if failed else EXIT_OK


# -- helpers ------------------------------------------------------------------ #
def _probe_spec(kind: ExecutionKind, *, queue: str, profile: str, name: str) -> Any:
    """A minimal spec used only to ask the router where it would go."""
    if kind is ExecutionKind.JOB:
        return JobSpec(job=name, profile=profile, queue=queue)
    if kind is ExecutionKind.TASK:
        return TaskSpec(task=f"probe:{name}", queue=queue, profile=profile)
    from .specs import InlineSpec

    return InlineSpec(func=lambda: None, name=name, queue=queue, profile=profile)


def _parse_json(raw: str, flag: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        # A bare word is far more common than a JSON string on a command line.
        return raw


def _split_pair(raw: str, flag: str) -> tuple[str, str]:
    key, sep, value = raw.partition("=")
    if not sep:
        raise TaskferryError(f"{flag} expects NAME=VALUE, got {raw!r}")
    return key, value


def _execution_payload(execution: Any) -> dict[str, Any]:
    return {
        "id": str(execution.id),
        "kind": execution.kind.value,
        "backend": execution.backend,
        "state": execution.state.value,
        "name": execution.name,
        "external_id": execution.external_id,
        "error": execution.result.error if execution.result else None,
    }


def _emit(payload: object) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _fail(message: str, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"error": message}, indent=2), file=sys.stderr)
    else:
        print(f"error: {message}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
