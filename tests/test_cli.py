from pathlib import Path

import pytest

from zed_i18n_kit.cli import CliError, _ensure_output_outside_checkout
from zed_i18n_kit.schema_resources import (
    GOLDEN_CORPUS_SCHEMA_NAME,
    SCAN_RESULT_SCHEMA_NAME,
    schema_resource,
)


@pytest.mark.parametrize("relative_output", [".", "scan.json", "crates/ui/src/lib.rs"])
def test_output_path_inside_checkout_is_rejected(
    tmp_path: Path, relative_output: str
) -> None:
    zed_root = tmp_path / "zed"
    zed_root.mkdir()

    with pytest.raises(CliError, match="outside the input Zed checkout"):
        _ensure_output_outside_checkout(zed_root / relative_output, zed_root)


def test_output_path_outside_checkout_is_allowed(tmp_path: Path) -> None:
    zed_root = tmp_path / "zed"
    zed_root.mkdir()

    _ensure_output_outside_checkout(tmp_path / "scan.json", zed_root)


def test_output_symlink_resolving_into_checkout_is_rejected(tmp_path: Path) -> None:
    zed_root = tmp_path / "zed"
    zed_root.mkdir()
    checkout_alias = tmp_path / "checkout-alias"
    checkout_alias.symlink_to(zed_root, target_is_directory=True)

    with pytest.raises(CliError, match="outside the input Zed checkout"):
        _ensure_output_outside_checkout(checkout_alias / "scan.json", zed_root)


def test_schema_resources_are_shipped_with_the_package() -> None:
    for name in (GOLDEN_CORPUS_SCHEMA_NAME, SCAN_RESULT_SCHEMA_NAME):
        resource = schema_resource(name)
        assert resource.is_file()
        assert resource.read_text(encoding="utf-8").startswith("{")
