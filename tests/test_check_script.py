from pathlib import Path

from scripts.check import Check, run_checks


def test_run_checks_runs_in_order() -> None:
    calls: list[tuple[tuple[str, ...], Path, float]] = []
    checks = (
        Check("first", ("first", "--check")),
        Check("second", ("second",)),
    )

    def runner(command: tuple[str, ...], root: Path, timeout: float) -> int:
        calls.append((command, root, timeout))
        return 0

    root = Path("project")
    assert run_checks(checks, root=root, runner=runner, timeout=12.0) == 0
    assert calls == [
        (("first", "--check"), root, 12.0),
        (("second",), root, 12.0),
    ]


def test_run_checks_stops_at_first_failure() -> None:
    calls: list[tuple[str, ...]] = []
    checks = (
        Check("first", ("first",)),
        Check("failing", ("failing",)),
        Check("never", ("never",)),
    )

    def runner(command: tuple[str, ...], _root: Path, _timeout: float) -> int:
        calls.append(command)
        return 7 if command[0] == "failing" else 0

    assert run_checks(checks, root=Path("project"), runner=runner) == 7
    assert calls == [("first",), ("failing",)]
