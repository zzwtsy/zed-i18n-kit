from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class DiscoveryError(ValueError):
    """Raised when source discovery cannot establish a safe checkout boundary."""


@dataclass(frozen=True, slots=True)
class DiscoveryPolicy:
    include_globs: tuple[str, ...]
    excluded_crates: frozenset[str]
    excluded_directories: frozenset[str]
    excluded_filenames: frozenset[str]
    excluded_filename_suffixes: tuple[str, ...]


DEFAULT_DISCOVERY_POLICY = DiscoveryPolicy(
    include_globs=("crates/*/src/**/*.rs",),
    excluded_crates=frozenset({"component_preview"}),
    excluded_directories=frozenset(
        {
            "benches",
            "component_preview",
            "examples",
            "fixtures",
            "generated",
            "test",
            "tests",
        }
    ),
    excluded_filenames=frozenset({"generated.rs", "test.rs", "tests.rs"}),
    excluded_filename_suffixes=("_tests.rs",),
)


def discover_source_paths(
    zed_root: Path,
    *,
    policy: DiscoveryPolicy = DEFAULT_DISCOVERY_POLICY,
) -> tuple[PurePosixPath, ...]:
    if not policy.include_globs:
        raise DiscoveryError("discovery policy include_globs cannot be empty")
    try:
        resolved_root = zed_root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise DiscoveryError(
            f"cannot resolve Zed checkout {zed_root}: {error}"
        ) from error
    if not (resolved_root / "crates").is_dir():
        raise DiscoveryError(f"Zed checkout has no crates directory: {zed_root}")

    discovered: set[PurePosixPath] = set()
    for include_glob in policy.include_globs:
        for candidate in zed_root.glob(include_glob):
            if not candidate.is_file():
                continue
            try:
                relative_path = PurePosixPath(
                    candidate.relative_to(zed_root).as_posix()
                )
            except ValueError as error:
                raise DiscoveryError(
                    f"discovered path is outside the checkout: {candidate}"
                ) from error
            if _is_excluded(relative_path, policy):
                continue
            try:
                resolved_candidate = candidate.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise DiscoveryError(
                    f"cannot resolve discovered source {relative_path}: {error}"
                ) from error
            if (
                resolved_candidate != resolved_root
                and resolved_root not in resolved_candidate.parents
            ):
                raise DiscoveryError(
                    f"discovered source resolves outside the checkout: {relative_path}"
                )
            discovered.add(relative_path)

    if not discovered:
        raise DiscoveryError("discovery found no production Rust source files")
    return tuple(sorted(discovered))


def _is_excluded(path: PurePosixPath, policy: DiscoveryPolicy) -> bool:
    parts = path.parts
    if len(parts) < 4 or parts[0] != "crates" or parts[2] != "src":
        return True
    if parts[1] in policy.excluded_crates:
        return True
    if any(part in policy.excluded_directories for part in parts[3:-1]):
        return True
    filename = parts[-1]
    return filename in policy.excluded_filenames or filename.endswith(
        policy.excluded_filename_suffixes
    )
