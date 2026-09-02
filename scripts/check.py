"""Run the repository's canonical local quality checks."""

import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Check:
    """A named repository check and its argument vector."""

    label: str
    command: tuple[str, ...]


type CheckRunner = Callable[[tuple[str, ...], Path, float], int]

CHECK_TIMEOUT_SECONDS = 600.0
PROJECT_CHECKS = (
    Check("Ruff format", ("ruff", "format", "--check", ".")),
    Check("Ruff lint", ("ruff", "check", ".")),
    Check("ty", ("ty", "check")),
    Check("pytest", (sys.executable, "-m", "pytest", "-q")),
)


def run_command(command: tuple[str, ...], root: Path, timeout: float) -> int:
    """Run one check without invoking a shell and return its exit status."""
    try:
        result = subprocess.run(command, cwd=root, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"check timed out after {timeout:g}s: {shlex.join(command)}")
        return 124
    return result.returncode


def run_checks(
    checks: Sequence[Check],
    *,
    root: Path,
    runner: CheckRunner = run_command,
    timeout: float = CHECK_TIMEOUT_SECONDS,
) -> int:
    """Run checks in order and stop at the first failure."""
    for check in checks:
        print(f"==> {check.label}: {shlex.join(check.command)}", flush=True)
        return_code = runner(check.command, root, timeout)
        if return_code != 0:
            print(f"{check.label} failed with exit code {return_code}")
            return return_code
    print("All repository checks passed.")
    return 0


def main() -> int:
    """Run all repository checks from the repository root."""
    root = Path(__file__).resolve().parents[1]
    return run_checks(PROJECT_CHECKS, root=root)


if __name__ == "__main__":
    raise SystemExit(main())
