import hashlib
import json
import subprocess
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from zed_i18n_kit.golden import Decision, SourceSpan
from zed_i18n_kit.scan_result import (
    CapabilityProbe,
    CapabilityProbeStatus,
    ProvenanceRange,
    RuleEvidence,
    ScanMetadata,
    ScanOccurrence,
    ScanResult,
    ScanResultError,
    SourceFileSnapshot,
    parse_scan_result_json,
    serialize_scan_result,
    validate_scan_result,
    validate_scan_result_schema,
    validate_scan_snapshot,
    write_scan_result,
)
from zed_i18n_kit.schema_resources import SCAN_RESULT_SCHEMA_NAME, schema_resource

PROJECT_ROOT = Path(__file__).parents[1]
SCHEMA_RESOURCE = schema_resource(SCAN_RESULT_SCHEMA_NAME)
SOURCE_PATH = PurePosixPath("crates/demo/src/lib.rs")
COMMIT = "a" * 40
SOURCE_HASH = "b" * 64


def test_round_trip_is_deterministic_and_canonically_sorted(tmp_path: Path) -> None:
    result = _result(
        occurrences=(
            _occurrence("two", SourceSpan(20, 24)),
            _occurrence("one", SourceSpan(10, 14)),
        )
    )

    first = serialize_scan_result(result)
    second = serialize_scan_result(parse_scan_result_json(first))

    assert first == second
    assert first.index('"occurrence_id":"one"') < first.index('"occurrence_id":"two"')
    output_path = tmp_path / "nested/scan-result.json"
    write_scan_result(output_path, result)
    assert output_path.read_text(encoding="utf-8") == first


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("unknown_top_level", "fields differ"),
        ("missing_occurrences", "fields differ"),
        ("wrong_version", "schema_version 2"),
        ("unknown_metadata", "fields differ"),
    ],
)
def test_parser_rejects_unknown_missing_and_wrong_version(
    mutation: str, error: str
) -> None:
    value: object = json.loads(serialize_scan_result(_result()))
    assert isinstance(value, dict)
    payload: dict[str, object] = value
    if mutation == "unknown_top_level":
        payload["unknown"] = True
    elif mutation == "missing_occurrences":
        payload.pop("occurrences")
    elif mutation == "wrong_version":
        payload["schema_version"] = 2
    else:
        metadata = payload["metadata"]
        assert isinstance(metadata, dict)
        metadata["unknown"] = True

    with pytest.raises(ScanResultError, match=error):
        parse_scan_result_json(json.dumps(payload))


def test_runtime_rejects_duplicate_ids_and_invalid_slot_contract() -> None:
    occurrence = _occurrence("same", SourceSpan(10, 14))
    with pytest.raises(ScanResultError, match="duplicate scan-result occurrence_id"):
        validate_scan_result(_result(occurrences=(occurrence, occurrence)))

    invalid = replace(occurrence, sink_symbol=None, text_slot="arg[0]")
    with pytest.raises(ScanResultError, match="text_slot requires sink_symbol"):
        validate_scan_result(_result(occurrences=(invalid,)))


def test_runtime_requires_scope_and_source_snapshots_to_match() -> None:
    metadata = replace(_metadata(), source_files=())

    with pytest.raises(ScanResultError, match="must exactly match"):
        validate_scan_result(ScanResult(1, metadata, ()))


def test_parser_rejects_source_paths_that_escape_checkout() -> None:
    payload = json.loads(serialize_scan_result(_result()))
    metadata = payload["metadata"]
    assert isinstance(metadata, dict)
    metadata["scan_scope"] = ["crates/../outside.rs"]
    metadata["source_files"] = [{"path": "crates/../outside.rs", "sha256": SOURCE_HASH}]

    with pytest.raises(ScanResultError, match="must stay within checkout"):
        parse_scan_result_json(json.dumps(payload))


def test_snapshot_rejects_dirty_source_at_same_head(tmp_path: Path) -> None:
    zed_root = tmp_path / "zed"
    source_path = zed_root / SOURCE_PATH
    source_path.parent.mkdir(parents=True)
    source_bytes = b'fn render() { label("Hello"); }\n'
    source_path.write_bytes(source_bytes)
    _run_git(zed_root, "init")
    _run_git(zed_root, "add", SOURCE_PATH.as_posix())
    _run_git(
        zed_root,
        "-c",
        "user.name=Scan Result Test",
        "-c",
        "user.email=scan-result@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    commit = _run_git(zed_root, "rev-parse", "HEAD").stdout.strip()
    metadata = replace(
        _metadata(),
        zed_commit=commit,
        source_files=(
            SourceFileSnapshot(SOURCE_PATH, hashlib.sha256(source_bytes).hexdigest()),
        ),
    )
    result = ScanResult(1, metadata, ())

    validate_scan_snapshot(result, zed_root)
    source_path.write_bytes(source_bytes.replace(b"Hello", b"World"))
    with pytest.raises(ScanResultError, match="snapshot SHA-256 mismatch"):
        validate_scan_snapshot(result, zed_root)


def test_schema_matches_runtime_and_detects_drift(tmp_path: Path) -> None:
    validate_scan_result_schema(SCHEMA_RESOURCE)
    schema = json.loads(SCHEMA_RESOURCE.read_text(encoding="utf-8"))
    schema["properties"]["metadata"]["required"].remove("config_hash")
    drifted = tmp_path / "scan-result-v1.schema.json"
    drifted.write_text(json.dumps(schema), encoding="utf-8")

    with pytest.raises(ScanResultError, match="metadata required fields drifted"):
        validate_scan_result_schema(drifted)


def _result(*, occurrences: tuple[ScanOccurrence, ...] | None = None) -> ScanResult:
    if occurrences is None:
        occurrences = (_occurrence("one", SourceSpan(10, 14)),)
    return ScanResult(1, _metadata(), occurrences)


def _metadata() -> ScanMetadata:
    return ScanMetadata(
        zed_commit=COMMIT,
        tool_version="0.1.0",
        rule_pack_version="gpui-prototype-v1",
        config_hash="c" * 64,
        scan_scope=(SOURCE_PATH,),
        source_files=(SourceFileSnapshot(SOURCE_PATH, SOURCE_HASH),),
        capability_probes=(
            CapabilityProbe(
                "rust-grammar", CapabilityProbeStatus.PASSED, "grammar loaded"
            ),
        ),
    )


def _occurrence(occurrence_id: str, span: SourceSpan) -> ScanOccurrence:
    return ScanOccurrence(
        occurrence_id=occurrence_id,
        path=SOURCE_PATH,
        primary_span=span,
        syntax_kind="string_literal",
        sink_symbol="ui::Label::new",
        text_slot="arg[0]",
        disposition=Decision.REVIEW_REQUIRED,
        provenance=(ProvenanceRange(SOURCE_PATH, span),),
        evidence=(RuleEvidence("label-new", "matched Label::new arg[0]"),),
    )


def _run_git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
