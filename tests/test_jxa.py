import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from personal_activity_mcp.common.jxa import JXABackendError, run_jxa


class ExampleBackendError(JXABackendError):
    pass


@pytest.mark.skipif(
    not Path("/usr/bin/osascript").is_file(),
    reason="osascript is only available on macOS",
)
def test_run_jxa_executes_program_supplied_over_stdin() -> None:
    result = run_jxa(
        Path("/usr/bin/osascript"),
        """
function run(argv) {
  const marker = $("call\\n").dataUsingEncoding($.NSUTF8StringEncoding);
  $.NSFileHandle.fileHandleWithStandardOutput.writeData(marker);
  return argv[0];
}
""",
        ["stdin-ok"],
        application_name="Example",
        error_type=ExampleBackendError,
    )

    assert result.splitlines() == ["call", "stdin-ok"]


def test_run_jxa_returns_trimmed_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "personal_activity_mcp.common.jxa.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=" payload \n",
            stderr="",
        ),
    )

    result = run_jxa(
        Path("/usr/bin/osascript"),
        "function run() {}",
        ["arg"],
        application_name="Example",
        error_type=ExampleBackendError,
    )

    assert result == "payload"


def test_run_jxa_passes_script_arguments_over_stdin_not_process_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def run(*args: object, **kwargs: object) -> SimpleNamespace:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("personal_activity_mcp.common.jxa.subprocess.run", run)

    run_jxa(
        Path("/usr/bin/osascript"),
        "function run(argv) { return argv[0]; }",
        ["private event title"],
        application_name="Example",
        error_type=ExampleBackendError,
    )

    command = captured["args"][0]
    assert command == ["/usr/bin/osascript", "-l", "JavaScript"]
    assert "private event title" in str(captured["kwargs"]["input"])


def test_run_jxa_marks_invocation_failure_as_not_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_to_start(*args: object, **kwargs: object) -> None:
        raise OSError("missing executable")

    monkeypatch.setattr(
        "personal_activity_mcp.common.jxa.subprocess.run",
        fail_to_start,
    )

    with pytest.raises(ExampleBackendError) as captured:
        run_jxa(
            Path("/missing/osascript"),
            "function run() {}",
            [],
            application_name="Example",
            error_type=ExampleBackendError,
        )

    assert captured.value.external_state_changed is False


def test_run_jxa_timeout_preserves_unknown_write_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def time_out(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=30)

    monkeypatch.setattr("personal_activity_mcp.common.jxa.subprocess.run", time_out)

    with pytest.raises(ExampleBackendError) as captured:
        run_jxa(
            Path("/usr/bin/osascript"),
            "function run() {}",
            [],
            application_name="Example",
            error_type=ExampleBackendError,
        )

    assert captured.value.external_state_changed is None


def test_run_jxa_preserves_unknown_write_outcome_for_script_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "personal_activity_mcp.common.jxa.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="permission denied\n",
        ),
    )

    with pytest.raises(ExampleBackendError) as captured:
        run_jxa(
            Path("/usr/bin/osascript"),
            "function run() {}",
            [],
            application_name="Example",
            error_type=ExampleBackendError,
        )

    assert str(captured.value) == "Example automation failed: permission denied"
    assert captured.value.external_state_changed is None
