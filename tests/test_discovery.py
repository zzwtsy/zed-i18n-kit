from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from zed_i18n_kit.discovery import (
    DEFAULT_DISCOVERY_POLICY,
    DiscoveryError,
    discover_source_paths,
)


def test_default_discovery_includes_only_production_crate_sources(
    tmp_path: Path,
) -> None:
    zed_root = tmp_path / "zed"
    included = (
        "crates/alpha/src/lib.rs",
        "crates/alpha/src/nested/view.rs",
        "crates/beta/src/main.rs",
    )
    excluded = (
        "crates/alpha/tests/integration.rs",
        "crates/alpha/examples/demo.rs",
        "crates/alpha/src/tests/fixture.rs",
        "crates/alpha/src/test.rs",
        "crates/alpha/src/view_tests.rs",
        "crates/alpha/src/fixtures/sample.rs",
        "crates/alpha/src/generated/bindings.rs",
        "crates/alpha/src/generated.rs",
        "crates/component_preview/src/lib.rs",
        "crates/beta/src/readme.txt",
    )
    for relative_path in (*included, *excluded):
        path = zed_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fn fixture() {}\n", encoding="utf-8")

    discovered = discover_source_paths(zed_root)

    assert discovered == tuple(PurePosixPath(path) for path in included)


def test_discovery_rejects_source_symlink_resolving_outside_checkout(
    tmp_path: Path,
) -> None:
    zed_root = tmp_path / "zed"
    source_dir = zed_root / "crates/demo/src"
    source_dir.mkdir(parents=True)
    outside_source = tmp_path / "outside.rs"
    outside_source.write_text("fn outside() {}\n", encoding="utf-8")
    (source_dir / "lib.rs").symlink_to(outside_source)

    with pytest.raises(DiscoveryError, match="resolves outside the checkout"):
        discover_source_paths(zed_root)


def test_discovery_requires_an_include_pattern_and_matching_source(
    tmp_path: Path,
) -> None:
    zed_root = tmp_path / "zed"
    (zed_root / "crates").mkdir(parents=True)
    empty_policy = replace(DEFAULT_DISCOVERY_POLICY, include_globs=())

    with pytest.raises(DiscoveryError, match="include_globs cannot be empty"):
        discover_source_paths(zed_root, policy=empty_policy)
    with pytest.raises(DiscoveryError, match="found no production Rust"):
        discover_source_paths(zed_root)
