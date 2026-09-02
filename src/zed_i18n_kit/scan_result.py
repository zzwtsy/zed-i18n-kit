from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from .golden import Decision, GoldenCorpusError, SourceSpan
from .schema_resources import SchemaResource

SCAN_RESULT_SCHEMA_VERSION = 1
GIT_TIMEOUT_SECONDS = 30.0


class ScanResultError(ValueError):
    """Raised when a scan result violates its persistent contract."""


class CapabilityProbeStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SourceFileSnapshot:
    path: PurePosixPath
    sha256: str


@dataclass(frozen=True, slots=True)
class CapabilityProbe:
    probe_id: str
    status: CapabilityProbeStatus
    details: str


@dataclass(frozen=True, slots=True)
class ScanMetadata:
    zed_commit: str
    tool_version: str
    rule_pack_version: str
    config_hash: str
    scan_scope: tuple[PurePosixPath, ...]
    source_files: tuple[SourceFileSnapshot, ...]
    capability_probes: tuple[CapabilityProbe, ...]


@dataclass(frozen=True, slots=True, order=True)
class ProvenanceRange:
    path: PurePosixPath
    source_span: SourceSpan


@dataclass(frozen=True, slots=True, order=True)
class RuleEvidence:
    rule_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ScanOccurrence:
    occurrence_id: str
    path: PurePosixPath
    primary_span: SourceSpan
    syntax_kind: str
    sink_symbol: str | None
    text_slot: str | None
    disposition: Decision
    provenance: tuple[ProvenanceRange, ...]
    evidence: tuple[RuleEvidence, ...]


@dataclass(frozen=True, slots=True)
class ScanResult:
    schema_version: int
    metadata: ScanMetadata
    occurrences: tuple[ScanOccurrence, ...]


def load_scan_result(path: Path) -> ScanResult:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ScanResultError(f"cannot read scan result {path}: {error}") from error
    return parse_scan_result_json(text, str(path))


def parse_scan_result_json(text: str, context: str = "scan-result") -> ScanResult:
    try:
        raw_value: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise ScanResultError(f"{context}: invalid JSON: {error}") from error
    raw = _as_string_mapping(raw_value, context)
    _require_exact_keys(raw, {"schema_version", "metadata", "occurrences"}, context)

    schema_version = _require_int(raw, "schema_version", context)
    if schema_version != SCAN_RESULT_SCHEMA_VERSION:
        raise ScanResultError(f"{context}: unsupported schema_version {schema_version}")

    result = ScanResult(
        schema_version=schema_version,
        metadata=_parse_metadata(_require_mapping(raw, "metadata", context), context),
        occurrences=_parse_occurrences(raw["occurrences"], context),
    )
    validate_scan_result(result)
    return result


def serialize_scan_result(result: ScanResult) -> str:
    validate_scan_result(result)
    payload = {
        "metadata": {
            "capability_probes": [
                {
                    "details": probe.details,
                    "probe_id": probe.probe_id,
                    "status": probe.status.value,
                }
                for probe in sorted(
                    result.metadata.capability_probes,
                    key=lambda probe: probe.probe_id,
                )
            ],
            "config_hash": result.metadata.config_hash,
            "rule_pack_version": result.metadata.rule_pack_version,
            "scan_scope": sorted(
                path.as_posix() for path in result.metadata.scan_scope
            ),
            "source_files": [
                {"path": snapshot.path.as_posix(), "sha256": snapshot.sha256}
                for snapshot in sorted(
                    result.metadata.source_files,
                    key=lambda snapshot: snapshot.path,
                )
            ],
            "tool_version": result.metadata.tool_version,
            "zed_commit": result.metadata.zed_commit,
        },
        "occurrences": [
            _serialize_occurrence(occurrence)
            for occurrence in sorted(
                result.occurrences,
                key=lambda occurrence: occurrence.occurrence_id,
            )
        ],
        "schema_version": result.schema_version,
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def write_scan_result(path: Path, result: ScanResult) -> None:
    serialized = serialize_scan_result(result)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise ScanResultError(f"cannot write scan result {path}: {error}") from error


def validate_scan_result(result: ScanResult) -> None:
    if result.schema_version != SCAN_RESULT_SCHEMA_VERSION:
        raise ScanResultError(
            f"unsupported scan-result schema_version {result.schema_version}"
        )

    metadata = result.metadata
    _validate_git_sha(metadata.zed_commit, "metadata.zed_commit")
    _validate_sha256(metadata.config_hash, "metadata.config_hash")
    for field, value in (
        ("metadata.tool_version", metadata.tool_version),
        ("metadata.rule_pack_version", metadata.rule_pack_version),
    ):
        _validate_non_empty(value, field)

    scope_paths = set(metadata.scan_scope)
    if not scope_paths:
        raise ScanResultError("metadata.scan_scope cannot be empty")
    if len(scope_paths) != len(metadata.scan_scope):
        raise ScanResultError("metadata.scan_scope contains duplicate paths")
    for path in scope_paths:
        _validate_source_path(path, "metadata.scan_scope")

    snapshot_paths: set[PurePosixPath] = set()
    for snapshot in metadata.source_files:
        _validate_source_path(snapshot.path, "metadata.source_files")
        _validate_sha256(snapshot.sha256, f"metadata.source_files[{snapshot.path}]")
        if snapshot.path in snapshot_paths:
            raise ScanResultError(
                f"duplicate source snapshot path {snapshot.path.as_posix()}"
            )
        snapshot_paths.add(snapshot.path)
    if snapshot_paths != scope_paths:
        raise ScanResultError(
            "metadata.source_files paths must exactly match metadata.scan_scope"
        )

    probe_ids: set[str] = set()
    for probe in metadata.capability_probes:
        _validate_non_empty(probe.probe_id, "capability probe id")
        _validate_non_empty(probe.details, f"capability probe {probe.probe_id} details")
        if probe.probe_id in probe_ids:
            raise ScanResultError(f"duplicate capability probe {probe.probe_id!r}")
        probe_ids.add(probe.probe_id)
    if not probe_ids:
        raise ScanResultError("metadata.capability_probes cannot be empty")

    occurrence_ids: set[str] = set()
    for occurrence in result.occurrences:
        _validate_occurrence(occurrence, scope_paths)
        if occurrence.occurrence_id in occurrence_ids:
            raise ScanResultError(
                f"duplicate scan-result occurrence_id {occurrence.occurrence_id!r}"
            )
        occurrence_ids.add(occurrence.occurrence_id)


def validate_scan_snapshot(result: ScanResult, zed_root: Path) -> None:
    validate_scan_result(result)
    actual_commit = resolve_git_head(zed_root)
    if actual_commit != result.metadata.zed_commit:
        raise ScanResultError(
            f"scan-result Zed commit mismatch: expected "
            f"{result.metadata.zed_commit}, got {actual_commit}"
        )

    source_bytes_by_path: dict[PurePosixPath, bytes] = {}
    for snapshot in result.metadata.source_files:
        source_path = zed_root / snapshot.path
        try:
            source_bytes = source_path.read_bytes()
        except OSError as error:
            raise ScanResultError(
                f"cannot read scan snapshot source {snapshot.path}: {error}"
            ) from error
        actual_sha256 = hashlib.sha256(source_bytes).hexdigest()
        if actual_sha256 != snapshot.sha256:
            raise ScanResultError(
                f"{snapshot.path}: scan snapshot SHA-256 mismatch: expected "
                f"{snapshot.sha256}, got {actual_sha256}"
            )
        source_bytes_by_path[snapshot.path] = source_bytes

    for occurrence in result.occurrences:
        _validate_span_against_source(
            occurrence.primary_span,
            source_bytes_by_path[occurrence.path],
            f"{occurrence.occurrence_id}: primary_span",
        )
        for provenance in occurrence.provenance:
            _validate_span_against_source(
                provenance.source_span,
                source_bytes_by_path[provenance.path],
                f"{occurrence.occurrence_id}: provenance",
            )


def resolve_git_head(repository: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ScanResultError(
            f"cannot resolve Git commit for {repository}: {error}"
        ) from error
    commit = result.stdout.strip()
    _validate_git_sha(commit, f"Git commit for {repository}")
    return commit


def validate_scan_result_schema(schema_path: SchemaResource) -> None:
    try:
        raw_value: object = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ScanResultError(
            f"cannot load scan-result schema {schema_path}: {error}"
        ) from error
    schema = _as_string_mapping(raw_value, str(schema_path))
    if schema.get("additionalProperties") is not False:
        raise ScanResultError(f"{schema_path}: top-level object must be strict")
    required = _as_string_sequence(schema.get("required"), f"{schema_path}: required")
    if set(required) != {"schema_version", "metadata", "occurrences"}:
        raise ScanResultError(f"{schema_path}: top-level required fields drifted")

    properties = _as_string_mapping(
        schema.get("properties"), f"{schema_path}: properties"
    )
    if set(properties) != {"schema_version", "metadata", "occurrences"}:
        raise ScanResultError(f"{schema_path}: top-level properties drifted")

    schema_version = _as_string_mapping(
        properties["schema_version"], f"{schema_path}: properties.schema_version"
    )
    if schema_version.get("const") != SCAN_RESULT_SCHEMA_VERSION:
        raise ScanResultError(f"{schema_path}: schema_version drifted from runtime")

    metadata_schema = _as_string_mapping(
        properties["metadata"], f"{schema_path}: properties.metadata"
    )
    if metadata_schema.get("additionalProperties") is not False:
        raise ScanResultError(f"{schema_path}: metadata object must be strict")
    metadata_required = _as_string_sequence(
        metadata_schema.get("required"), f"{schema_path}: metadata.required"
    )
    expected_metadata = {
        "zed_commit",
        "tool_version",
        "rule_pack_version",
        "config_hash",
        "scan_scope",
        "source_files",
        "capability_probes",
    }
    if set(metadata_required) != expected_metadata:
        raise ScanResultError(f"{schema_path}: metadata required fields drifted")

    definitions = _as_string_mapping(schema.get("$defs"), f"{schema_path}: $defs")
    occurrence_items = _as_string_mapping(
        _as_string_mapping(
            properties["occurrences"], f"{schema_path}: properties.occurrences"
        ).get("items"),
        f"{schema_path}: occurrence items",
    )
    if occurrence_items.get("$ref") != "#/$defs/occurrence":
        raise ScanResultError(f"{schema_path}: occurrence reference drifted")
    occurrence_schema = _as_string_mapping(
        definitions.get("occurrence"), f"{schema_path}: $defs.occurrence"
    )
    if occurrence_schema.get("additionalProperties") is not False:
        raise ScanResultError(f"{schema_path}: occurrence object must be strict")
    occurrence_required = _as_string_sequence(
        occurrence_schema.get("required"), f"{schema_path}: occurrence.required"
    )
    expected_occurrence = {
        "occurrence_id",
        "path",
        "primary_span",
        "syntax_kind",
        "sink_symbol",
        "text_slot",
        "disposition",
        "provenance",
        "evidence",
    }
    if set(occurrence_required) != expected_occurrence:
        raise ScanResultError(f"{schema_path}: occurrence required fields drifted")

    strict_definitions = {
        "sourceSpan",
        "sourceFileSnapshot",
        "capabilityProbe",
        "provenanceRange",
        "ruleEvidence",
        "occurrence",
    }
    for definition_name in strict_definitions:
        definition = _as_string_mapping(
            definitions.get(definition_name),
            f"{schema_path}: $defs.{definition_name}",
        )
        if definition.get("additionalProperties") is not False:
            raise ScanResultError(
                f"{schema_path}: $defs.{definition_name} object must be strict"
            )

    occurrence_properties = _as_string_mapping(
        occurrence_schema.get("properties"),
        f"{schema_path}: $defs.occurrence.properties",
    )
    disposition_schema = _as_string_mapping(
        occurrence_properties.get("disposition"),
        f"{schema_path}: disposition",
    )
    if disposition_schema.get("enum") != [member.value for member in Decision]:
        raise ScanResultError(f"{schema_path}: disposition enum drifted from runtime")

    probe_schema = _as_string_mapping(
        definitions.get("capabilityProbe"), f"{schema_path}: $defs.capabilityProbe"
    )
    probe_properties = _as_string_mapping(
        probe_schema.get("properties"),
        f"{schema_path}: $defs.capabilityProbe.properties",
    )
    status_schema = _as_string_mapping(
        probe_properties.get("status"), f"{schema_path}: probe status"
    )
    if status_schema.get("enum") != [member.value for member in CapabilityProbeStatus]:
        raise ScanResultError(f"{schema_path}: probe status enum drifted from runtime")


def _serialize_occurrence(occurrence: ScanOccurrence) -> dict[str, object]:
    return {
        "disposition": occurrence.disposition.value,
        "evidence": [
            {"reason": evidence.reason, "rule_id": evidence.rule_id}
            for evidence in sorted(occurrence.evidence)
        ],
        "occurrence_id": occurrence.occurrence_id,
        "path": occurrence.path.as_posix(),
        "primary_span": _serialize_span(occurrence.primary_span),
        "provenance": [
            {
                "path": provenance.path.as_posix(),
                "source_span": _serialize_span(provenance.source_span),
            }
            for provenance in sorted(occurrence.provenance)
        ],
        "sink_symbol": occurrence.sink_symbol,
        "syntax_kind": occurrence.syntax_kind,
        "text_slot": occurrence.text_slot,
    }


def _serialize_span(span: SourceSpan) -> dict[str, int]:
    return {"end_byte": span.end_byte, "start_byte": span.start_byte}


def _parse_metadata(raw: Mapping[str, object], context: str) -> ScanMetadata:
    metadata_context = f"{context}: metadata"
    _require_exact_keys(
        raw,
        {
            "zed_commit",
            "tool_version",
            "rule_pack_version",
            "config_hash",
            "scan_scope",
            "source_files",
            "capability_probes",
        },
        metadata_context,
    )
    return ScanMetadata(
        zed_commit=_require_string(raw, "zed_commit", metadata_context),
        tool_version=_require_string(raw, "tool_version", metadata_context),
        rule_pack_version=_require_string(raw, "rule_pack_version", metadata_context),
        config_hash=_require_string(raw, "config_hash", metadata_context),
        scan_scope=tuple(
            _parse_source_path(value, f"{metadata_context}: scan_scope")
            for value in _as_string_sequence(
                raw["scan_scope"], f"{metadata_context}: scan_scope"
            )
        ),
        source_files=_parse_source_files(raw["source_files"], metadata_context),
        capability_probes=_parse_capability_probes(
            raw["capability_probes"], metadata_context
        ),
    )


def _parse_source_files(value: object, context: str) -> tuple[SourceFileSnapshot, ...]:
    snapshots: list[SourceFileSnapshot] = []
    for index, item in enumerate(_as_sequence(value, f"{context}: source_files")):
        item_context = f"{context}: source_files[{index}]"
        raw = _as_string_mapping(item, item_context)
        _require_exact_keys(raw, {"path", "sha256"}, item_context)
        snapshots.append(
            SourceFileSnapshot(
                path=_parse_source_path(
                    _require_string(raw, "path", item_context), item_context
                ),
                sha256=_require_string(raw, "sha256", item_context),
            )
        )
    return tuple(snapshots)


def _parse_capability_probes(
    value: object, context: str
) -> tuple[CapabilityProbe, ...]:
    probes: list[CapabilityProbe] = []
    for index, item in enumerate(_as_sequence(value, f"{context}: capability_probes")):
        item_context = f"{context}: capability_probes[{index}]"
        raw = _as_string_mapping(item, item_context)
        _require_exact_keys(raw, {"probe_id", "status", "details"}, item_context)
        probes.append(
            CapabilityProbe(
                probe_id=_require_string(raw, "probe_id", item_context),
                status=_parse_enum(
                    CapabilityProbeStatus,
                    _require_string(raw, "status", item_context),
                    item_context,
                ),
                details=_require_string(raw, "details", item_context),
            )
        )
    return tuple(probes)


def _parse_occurrences(value: object, context: str) -> tuple[ScanOccurrence, ...]:
    occurrences: list[ScanOccurrence] = []
    for index, item in enumerate(_as_sequence(value, f"{context}: occurrences")):
        item_context = f"{context}: occurrences[{index}]"
        raw = _as_string_mapping(item, item_context)
        _require_exact_keys(
            raw,
            {
                "occurrence_id",
                "path",
                "primary_span",
                "syntax_kind",
                "sink_symbol",
                "text_slot",
                "disposition",
                "provenance",
                "evidence",
            },
            item_context,
        )
        occurrences.append(
            ScanOccurrence(
                occurrence_id=_require_string(raw, "occurrence_id", item_context),
                path=_parse_source_path(
                    _require_string(raw, "path", item_context), item_context
                ),
                primary_span=_parse_span(
                    _require_mapping(raw, "primary_span", item_context),
                    f"{item_context}: primary_span",
                ),
                syntax_kind=_require_string(raw, "syntax_kind", item_context),
                sink_symbol=_optional_string(raw, "sink_symbol", item_context),
                text_slot=_optional_string(raw, "text_slot", item_context),
                disposition=_parse_enum(
                    Decision,
                    _require_string(raw, "disposition", item_context),
                    item_context,
                ),
                provenance=_parse_provenance(raw["provenance"], item_context),
                evidence=_parse_evidence(raw["evidence"], item_context),
            )
        )
    return tuple(occurrences)


def _parse_provenance(value: object, context: str) -> tuple[ProvenanceRange, ...]:
    provenance: list[ProvenanceRange] = []
    for index, item in enumerate(_as_sequence(value, f"{context}: provenance")):
        item_context = f"{context}: provenance[{index}]"
        raw = _as_string_mapping(item, item_context)
        _require_exact_keys(raw, {"path", "source_span"}, item_context)
        provenance.append(
            ProvenanceRange(
                path=_parse_source_path(
                    _require_string(raw, "path", item_context), item_context
                ),
                source_span=_parse_span(
                    _require_mapping(raw, "source_span", item_context),
                    f"{item_context}: source_span",
                ),
            )
        )
    return tuple(provenance)


def _parse_evidence(value: object, context: str) -> tuple[RuleEvidence, ...]:
    evidence_items: list[RuleEvidence] = []
    for index, item in enumerate(_as_sequence(value, f"{context}: evidence")):
        item_context = f"{context}: evidence[{index}]"
        raw = _as_string_mapping(item, item_context)
        _require_exact_keys(raw, {"rule_id", "reason"}, item_context)
        evidence_items.append(
            RuleEvidence(
                rule_id=_require_string(raw, "rule_id", item_context),
                reason=_require_string(raw, "reason", item_context),
            )
        )
    return tuple(evidence_items)


def _parse_span(raw: Mapping[str, object], context: str) -> SourceSpan:
    _require_exact_keys(raw, {"start_byte", "end_byte"}, context)
    try:
        return SourceSpan(
            start_byte=_require_int(raw, "start_byte", context),
            end_byte=_require_int(raw, "end_byte", context),
        )
    except GoldenCorpusError as error:
        raise ScanResultError(f"{context}: {error}") from error


def _validate_occurrence(
    occurrence: ScanOccurrence, scope_paths: set[PurePosixPath]
) -> None:
    _validate_non_empty(occurrence.occurrence_id, "occurrence_id")
    _validate_source_path(occurrence.path, occurrence.occurrence_id)
    if occurrence.path not in scope_paths:
        raise ScanResultError(
            f"{occurrence.occurrence_id}: path is outside metadata.scan_scope"
        )
    _validate_non_empty(
        occurrence.syntax_kind, f"{occurrence.occurrence_id}: syntax_kind"
    )
    if occurrence.sink_symbol is not None:
        _validate_non_empty(
            occurrence.sink_symbol, f"{occurrence.occurrence_id}: sink_symbol"
        )
    if occurrence.text_slot is not None:
        _validate_non_empty(
            occurrence.text_slot, f"{occurrence.occurrence_id}: text_slot"
        )
        if occurrence.sink_symbol is None:
            raise ScanResultError(
                f"{occurrence.occurrence_id}: text_slot requires sink_symbol"
            )
    if not occurrence.evidence:
        raise ScanResultError(f"{occurrence.occurrence_id}: evidence cannot be empty")

    provenance_ranges: set[ProvenanceRange] = set()
    for provenance in occurrence.provenance:
        _validate_source_path(provenance.path, occurrence.occurrence_id)
        if provenance.path not in scope_paths:
            raise ScanResultError(
                f"{occurrence.occurrence_id}: provenance path is outside scan scope"
            )
        if provenance in provenance_ranges:
            raise ScanResultError(
                f"{occurrence.occurrence_id}: duplicate provenance range"
            )
        provenance_ranges.add(provenance)

    evidence_items: set[RuleEvidence] = set()
    for evidence in occurrence.evidence:
        _validate_non_empty(evidence.rule_id, f"{occurrence.occurrence_id}: rule_id")
        _validate_non_empty(evidence.reason, f"{occurrence.occurrence_id}: reason")
        if evidence in evidence_items:
            raise ScanResultError(f"{occurrence.occurrence_id}: duplicate evidence")
        evidence_items.add(evidence)


def _validate_source_path(path: PurePosixPath, context: str) -> None:
    if path.is_absolute() or ".." in path.parts:
        raise ScanResultError(f"{context}: source path must stay within checkout")
    if not path.parts or path.parts[0] != "crates" or path.suffix != ".rs":
        raise ScanResultError(f"{context}: source path must be crates/**/*.rs")


def _validate_git_sha(value: str, context: str) -> None:
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise ScanResultError(f"{context} must be a full lowercase Git SHA")


def _validate_sha256(value: str, context: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ScanResultError(f"{context} must be lowercase SHA-256")


def _validate_non_empty(value: str, context: str) -> None:
    if not value.strip():
        raise ScanResultError(f"{context} cannot be empty")


def _validate_span_against_source(
    span: SourceSpan, source_bytes: bytes, context: str
) -> None:
    if span.end_byte > len(source_bytes):
        raise ScanResultError(
            f"{context} ends at {span.end_byte}, past source byte length "
            f"{len(source_bytes)}"
        )
    try:
        source_bytes[span.start_byte : span.end_byte].decode("utf-8")
    except UnicodeDecodeError as error:
        raise ScanResultError(f"{context} does not follow UTF-8 boundaries") from error


def _parse_source_path(value: str, context: str) -> PurePosixPath:
    path = PurePosixPath(value)
    _validate_source_path(path, context)
    return path


def _require_exact_keys(
    raw: Mapping[str, object], expected: set[str], context: str
) -> None:
    actual = set(raw)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ScanResultError(
            f"{context}: fields differ: missing={missing}, unknown={unknown}"
        )


def _require_mapping(
    raw: Mapping[str, object], key: str, context: str
) -> Mapping[str, object]:
    return _as_string_mapping(raw[key], f"{context}: {key}")


def _as_string_mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ScanResultError(f"{context}: expected an object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ScanResultError(f"{context}: object keys must be strings")
        result[key] = item
    return result


def _as_sequence(value: object, context: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise ScanResultError(f"{context}: expected an array")
    return value


def _as_string_sequence(value: object, context: str) -> tuple[str, ...]:
    items: list[str] = []
    for item in _as_sequence(value, context):
        if not isinstance(item, str):
            raise ScanResultError(f"{context}: expected only strings")
        items.append(item)
    return tuple(items)


def _require_string(raw: Mapping[str, object], key: str, context: str) -> str:
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise ScanResultError(f"{context}: {key} must be a non-empty string")
    return value


def _optional_string(raw: Mapping[str, object], key: str, context: str) -> str | None:
    value = raw[key]
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ScanResultError(f"{context}: {key} must be a non-empty string or null")
    return value


def _require_int(raw: Mapping[str, object], key: str, context: str) -> int:
    value = raw[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ScanResultError(f"{context}: {key} must be an integer")
    return value


def _parse_enum[E: StrEnum](enum_type: type[E], value: str, context: str) -> E:
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(member.value for member in enum_type)
        raise ScanResultError(
            f"{context}: invalid {enum_type.__name__} {value!r}; expected {allowed}"
        ) from error
