from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

GOLDEN_CORPUS_SCHEMA_NAME = "golden-corpus-sample-v2.schema.json"
SCAN_RESULT_SCHEMA_NAME = "scan-result-v1.schema.json"

type SchemaResource = Path | Traversable


def schema_resource(name: str) -> Traversable:
    """Return a schema shipped with the installed package."""
    resource = files("zed_i18n_kit").joinpath("schemas", name)
    if not resource.is_file():
        raise FileNotFoundError(f"packaged schema does not exist: {name}")
    return resource
