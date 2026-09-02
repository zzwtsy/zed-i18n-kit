from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from .evaluation import evaluate_scan_result
from .golden import GoldenCorpus, SourceSpan, validate_checkout
from .review import context_span_for_source
from .scan_result import ScanOccurrence, ScanResult, validate_scan_snapshot
from .schema_resources import SchemaResource

AUDIT_BUNDLE_SCHEMA_VERSION = 1
AUDIT_RESULT_SCHEMA_VERSION = 1
AUDIT_SET_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")
AUDIT_BUNDLE_FIELDS = frozenset(
    {
        "schema_version",
        "audit_set_id",
        "zed_commit",
        "corpus_sample_sha256",
        "scan_config_hash",
        "rule_pack_version",
        "sample_size_requested",
        "occurrences",
    }
)
AUDIT_SAMPLE_FIELDS = frozenset(
    {
        "occurrence_id",
        "path",
        "primary_span",
        "context_span",
        "source_context",
        "syntax_kind",
    }
)
AUDIT_RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "audit_set_id",
        "zed_commit",
        "corpus_sample_sha256",
        "scan_config_hash",
        "reviewer_id",
        "decisions",
    }
)
AUDIT_DECISION_FIELDS = frozenset({"occurrence_id", "classification", "rationale"})


class UnlabeledAuditError(ValueError):
    """Raised when an unlabeled occurrence audit violates its protocol."""


class AuditClassification(StrEnum):
    LEGITIMATE_CANDIDATE = "legitimate_candidate"
    FALSE_POSITIVE = "false_positive"
    CORPUS_GAP = "corpus_gap"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True, slots=True)
class AuditSample:
    occurrence_id: str
    path: PurePosixPath
    primary_span: SourceSpan
    context_span: SourceSpan
    source_context: str
    syntax_kind: str


@dataclass(frozen=True, slots=True)
class AuditBundle:
    audit_set_id: str
    zed_commit: str
    corpus_sample_sha256: str
    scan_config_hash: str
    rule_pack_version: str
    sample_size_requested: int
    occurrences: tuple[AuditSample, ...]


@dataclass(frozen=True, slots=True)
class AuditDecision:
    occurrence_id: str
    classification: AuditClassification
    rationale: str


@dataclass(frozen=True, slots=True)
class AuditResult:
    audit_set_id: str
    zed_commit: str
    corpus_sample_sha256: str
    scan_config_hash: str
    reviewer_id: str
    decisions: tuple[AuditDecision, ...]


@dataclass(frozen=True, slots=True)
class AuditReconciliation:
    audit_set_id: str
    reviewer_id: str
    classification_counts: Mapping[AuditClassification, int]
    missing_occurrence_ids: tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        return not self.missing_occurrence_ids


def build_audit_bundle(
    corpus: GoldenCorpus,
    scan_result: ScanResult,
    zed_root: Path,
    *,
    audit_set_id: str,
    sample_size: int,
) -> AuditBundle:
    _validate_audit_set_id(audit_set_id)
    if sample_size <= 0:
        raise UnlabeledAuditError("sample_size must be greater than zero")
    validate_checkout(corpus, zed_root)
    validate_scan_snapshot(scan_result, zed_root)
    evaluation = evaluate_scan_result(corpus, scan_result)
    occurrences_by_id = {
        occurrence.occurrence_id: occurrence for occurrence in scan_result.occurrences
    }
    candidates = tuple(
        occurrences_by_id[occurrence_id]
        for occurrence_id in evaluation.unlabeled_occurrence_ids
    )
    selected = _select_occurrences(
        candidates,
        corpus_paths={sample.path for sample in corpus.samples},
        audit_set_id=audit_set_id,
        sample_size=sample_size,
    )
    sources: dict[PurePosixPath, bytes] = {}
    samples: list[AuditSample] = []
    for occurrence in selected:
        source = sources.get(occurrence.path)
        if source is None:
            try:
                source = (zed_root / occurrence.path).read_bytes()
            except OSError as error:
                raise UnlabeledAuditError(
                    f"cannot read audit source {occurrence.path}: {error}"
                ) from error
            sources[occurrence.path] = source
        context_span = context_span_for_source(source, occurrence.primary_span)
        try:
            source_context = source[
                context_span.start_byte : context_span.end_byte
            ].decode("utf-8")
        except UnicodeDecodeError as error:
            raise UnlabeledAuditError(
                f"{occurrence.occurrence_id}: audit context is not valid UTF-8"
            ) from error
        samples.append(
            AuditSample(
                occurrence_id=occurrence.occurrence_id,
                path=occurrence.path,
                primary_span=occurrence.primary_span,
                context_span=context_span,
                source_context=source_context,
                syntax_kind=occurrence.syntax_kind,
            )
        )
    metadata = scan_result.metadata
    return AuditBundle(
        audit_set_id=audit_set_id,
        zed_commit=metadata.zed_commit,
        corpus_sample_sha256=corpus.manifest.sample_sha256,
        scan_config_hash=metadata.config_hash,
        rule_pack_version=metadata.rule_pack_version,
        sample_size_requested=sample_size,
        occurrences=tuple(samples),
    )


def serialize_audit_bundle(bundle: AuditBundle) -> str:
    payload = {
        "audit_set_id": bundle.audit_set_id,
        "corpus_sample_sha256": bundle.corpus_sample_sha256,
        "occurrences": [
            {
                "context_span": _serialize_span(sample.context_span),
                "occurrence_id": sample.occurrence_id,
                "path": sample.path.as_posix(),
                "primary_span": _serialize_span(sample.primary_span),
                "source_context": sample.source_context,
                "syntax_kind": sample.syntax_kind,
            }
            for sample in bundle.occurrences
        ],
        "rule_pack_version": bundle.rule_pack_version,
        "sample_size_requested": bundle.sample_size_requested,
        "scan_config_hash": bundle.scan_config_hash,
        "schema_version": AUDIT_BUNDLE_SCHEMA_VERSION,
        "zed_commit": bundle.zed_commit,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_audit_bundle(path: Path, bundle: AuditBundle) -> None:
    _write_text_atomically(path, serialize_audit_bundle(bundle))


def validate_audit_schema_contracts(
    bundle_schema_path: SchemaResource,
    result_schema_path: SchemaResource,
) -> None:
    bundle_schema = _load_schema(bundle_schema_path, "unlabeled audit bundle")
    _validate_schema_object(
        bundle_schema,
        AUDIT_BUNDLE_FIELDS,
        str(bundle_schema_path),
        schema_version=AUDIT_BUNDLE_SCHEMA_VERSION,
    )
    bundle_properties = _schema_properties(bundle_schema, str(bundle_schema_path))
    occurrences_schema = _as_mapping(
        bundle_properties["occurrences"],
        f"{bundle_schema_path}: properties.occurrences",
    )
    occurrence_schema = _as_mapping(
        occurrences_schema.get("items"), f"{bundle_schema_path}: occurrences.items"
    )
    _validate_schema_object(
        occurrence_schema,
        AUDIT_SAMPLE_FIELDS,
        f"{bundle_schema_path}: occurrences.items",
    )

    result_schema = _load_schema(result_schema_path, "unlabeled audit result")
    _validate_schema_object(
        result_schema,
        AUDIT_RESULT_FIELDS,
        str(result_schema_path),
        schema_version=AUDIT_RESULT_SCHEMA_VERSION,
    )
    result_properties = _schema_properties(result_schema, str(result_schema_path))
    decisions_schema = _as_mapping(
        result_properties["decisions"], f"{result_schema_path}: properties.decisions"
    )
    decision_schema = _as_mapping(
        decisions_schema.get("items"), f"{result_schema_path}: decisions.items"
    )
    _validate_schema_object(
        decision_schema,
        AUDIT_DECISION_FIELDS,
        f"{result_schema_path}: decisions.items",
    )
    decision_properties = _schema_properties(
        decision_schema, f"{result_schema_path}: decisions.items"
    )
    classification_schema = _as_mapping(
        decision_properties["classification"],
        f"{result_schema_path}: classification",
    )
    actual = classification_schema.get("enum")
    expected = [classification.value for classification in AuditClassification]
    if actual != expected:
        raise UnlabeledAuditError(
            f"{result_schema_path}: classification enum drifted from runtime"
        )


def load_audit_bundle(path: Path) -> AuditBundle:
    return parse_audit_bundle_json(_read_text(path, "audit bundle"), str(path))


def parse_audit_bundle_json(text: str, context: str = "audit-bundle") -> AuditBundle:
    raw = _parse_json_object(text, context)
    _require_exact_keys(raw, AUDIT_BUNDLE_FIELDS, context)
    version = _require_int(raw, "schema_version", context, minimum=1)
    if version != AUDIT_BUNDLE_SCHEMA_VERSION:
        raise UnlabeledAuditError(f"{context}: unsupported schema_version {version}")
    audit_set_id = _require_string(raw, "audit_set_id", context)
    _validate_audit_set_id(audit_set_id, context=context)
    occurrences_value = raw["occurrences"]
    if not isinstance(occurrences_value, list):
        raise UnlabeledAuditError(f"{context}: occurrences must be an array")
    occurrences: list[AuditSample] = []
    occurrence_ids: set[str] = set()
    for index, value in enumerate(occurrences_value):
        item_context = f"{context}: occurrences[{index}]"
        item = _as_mapping(value, item_context)
        _require_exact_keys(item, AUDIT_SAMPLE_FIELDS, item_context)
        occurrence_id = _require_string(item, "occurrence_id", item_context)
        if occurrence_id in occurrence_ids:
            raise UnlabeledAuditError(
                f"{context}: duplicate occurrence {occurrence_id}"
            )
        occurrence_ids.add(occurrence_id)
        primary_span = _parse_span(item["primary_span"], item_context)
        context_span = _parse_span(item["context_span"], item_context)
        if not (
            context_span.start_byte <= primary_span.start_byte
            and primary_span.end_byte <= context_span.end_byte
        ):
            raise UnlabeledAuditError(
                f"{item_context}: context_span must contain primary_span"
            )
        source_context = _require_string(item, "source_context", item_context)
        if len(source_context.encode("utf-8")) != (
            context_span.end_byte - context_span.start_byte
        ):
            raise UnlabeledAuditError(
                f"{item_context}: source_context byte length does not match context_span"
            )
        occurrences.append(
            AuditSample(
                occurrence_id=occurrence_id,
                path=_parse_source_path(
                    _require_string(item, "path", item_context), item_context
                ),
                primary_span=primary_span,
                context_span=context_span,
                source_context=source_context,
                syntax_kind=_require_string(item, "syntax_kind", item_context),
            )
        )
    return AuditBundle(
        audit_set_id=audit_set_id,
        zed_commit=_require_lower_hex(raw, "zed_commit", 40, context),
        corpus_sample_sha256=_require_lower_hex(
            raw, "corpus_sample_sha256", 64, context
        ),
        scan_config_hash=_require_lower_hex(raw, "scan_config_hash", 64, context),
        rule_pack_version=_require_string(raw, "rule_pack_version", context),
        sample_size_requested=_require_int(
            raw, "sample_size_requested", context, minimum=1
        ),
        occurrences=tuple(occurrences),
    )


def load_audit_result(path: Path) -> AuditResult:
    return parse_audit_result_json(_read_text(path, "audit result"), str(path))


def parse_audit_result_json(text: str, context: str = "audit-result") -> AuditResult:
    raw = _parse_json_object(text, context)
    _require_exact_keys(raw, AUDIT_RESULT_FIELDS, context)
    version = _require_int(raw, "schema_version", context, minimum=1)
    if version != AUDIT_RESULT_SCHEMA_VERSION:
        raise UnlabeledAuditError(f"{context}: unsupported schema_version {version}")
    audit_set_id = _require_string(raw, "audit_set_id", context)
    _validate_audit_set_id(audit_set_id, context=context)
    decisions_value = raw["decisions"]
    if not isinstance(decisions_value, list):
        raise UnlabeledAuditError(f"{context}: decisions must be an array")
    decisions: list[AuditDecision] = []
    occurrence_ids: set[str] = set()
    for index, value in enumerate(decisions_value):
        item_context = f"{context}: decisions[{index}]"
        item = _as_mapping(value, item_context)
        _require_exact_keys(item, AUDIT_DECISION_FIELDS, item_context)
        occurrence_id = _require_string(item, "occurrence_id", item_context)
        if occurrence_id in occurrence_ids:
            raise UnlabeledAuditError(
                f"{context}: duplicate decision for {occurrence_id}"
            )
        occurrence_ids.add(occurrence_id)
        decisions.append(
            AuditDecision(
                occurrence_id=occurrence_id,
                classification=_parse_enum(
                    AuditClassification,
                    _require_string(item, "classification", item_context),
                    item_context,
                ),
                rationale=_require_string(item, "rationale", item_context),
            )
        )
    return AuditResult(
        audit_set_id=audit_set_id,
        zed_commit=_require_lower_hex(raw, "zed_commit", 40, context),
        corpus_sample_sha256=_require_lower_hex(
            raw, "corpus_sample_sha256", 64, context
        ),
        scan_config_hash=_require_lower_hex(raw, "scan_config_hash", 64, context),
        reviewer_id=_require_string(raw, "reviewer_id", context),
        decisions=tuple(decisions),
    )


def reconcile_audit_result(
    bundle: AuditBundle, result: AuditResult
) -> AuditReconciliation:
    identity_checks = (
        ("audit set", bundle.audit_set_id, result.audit_set_id),
        ("Zed commit", bundle.zed_commit, result.zed_commit),
        (
            "corpus sample SHA-256",
            bundle.corpus_sample_sha256,
            result.corpus_sample_sha256,
        ),
        ("scan config hash", bundle.scan_config_hash, result.scan_config_hash),
    )
    for label, expected, actual in identity_checks:
        if actual != expected:
            raise UnlabeledAuditError(
                f"audit result {label} mismatch: expected {expected}, got {actual}"
            )
    expected_ids = {sample.occurrence_id for sample in bundle.occurrences}
    decisions_by_id = {
        decision.occurrence_id: decision for decision in result.decisions
    }
    unknown = tuple(sorted(set(decisions_by_id) - expected_ids))
    if unknown:
        raise UnlabeledAuditError(
            "audit result contains unknown occurrence IDs: " + ",".join(unknown)
        )
    counts = {
        classification: sum(
            decision.classification is classification
            for decision in decisions_by_id.values()
        )
        for classification in AuditClassification
    }
    return AuditReconciliation(
        audit_set_id=bundle.audit_set_id,
        reviewer_id=result.reviewer_id,
        classification_counts=counts,
        missing_occurrence_ids=tuple(sorted(expected_ids - set(decisions_by_id))),
    )


def serialize_audit_reconciliation(report: AuditReconciliation) -> str:
    payload = {
        "audit_set_id": report.audit_set_id,
        "classification_counts": {
            classification.value: report.classification_counts.get(classification, 0)
            for classification in AuditClassification
        },
        "complete": report.is_complete,
        "missing_occurrence_ids": list(report.missing_occurrence_ids),
        "reviewer_id": report.reviewer_id,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _select_occurrences(
    candidates: tuple[ScanOccurrence, ...],
    *,
    corpus_paths: set[PurePosixPath],
    audit_set_id: str,
    sample_size: int,
) -> tuple[ScanOccurrence, ...]:
    selected: list[ScanOccurrence] = []
    for path_is_covered in (False, True):
        groups: dict[tuple[str, str, str], list[ScanOccurrence]] = defaultdict(list)
        for occurrence in candidates:
            if (occurrence.path in corpus_paths) is not path_is_covered:
                continue
            rule_key = "+".join(
                sorted({evidence.rule_id for evidence in occurrence.evidence})
            )
            risk = (
                "high"
                if occurrence.disposition.value == "review_required"
                or occurrence.syntax_kind != "string_literal"
                or len(occurrence.provenance) > 1
                else "baseline"
            )
            groups[rule_key, occurrence.disposition.value, risk].append(occurrence)
        for group in groups.values():
            group.sort(
                key=lambda occurrence: (
                    hashlib.sha256(
                        f"{audit_set_id}\0{occurrence.occurrence_id}".encode()
                    ).hexdigest(),
                    occurrence.occurrence_id,
                )
            )
        group_keys = sorted(groups)
        index = 0
        while len(selected) < sample_size:
            added = False
            for key in group_keys:
                group = groups[key]
                if index < len(group):
                    selected.append(group[index])
                    added = True
                    if len(selected) == sample_size:
                        return tuple(selected)
            if not added:
                break
            index += 1
    return tuple(selected)


def _read_text(path: Path, artifact: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise UnlabeledAuditError(f"cannot read {artifact} {path}: {error}") from error


def _load_schema(path: SchemaResource, artifact: str) -> dict[str, object]:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise UnlabeledAuditError(
            f"cannot load {artifact} schema {path}: {error}"
        ) from error
    return _as_mapping(value, str(path))


def _validate_schema_object(
    schema: Mapping[str, object],
    expected_fields: frozenset[str],
    context: str,
    *,
    schema_version: int | None = None,
) -> None:
    if schema.get("additionalProperties") is not False:
        raise UnlabeledAuditError(
            f"{context}: object must reject additional properties"
        )
    required = schema.get("required")
    if not isinstance(required, list) or set(required) != expected_fields:
        raise UnlabeledAuditError(f"{context}: required fields drifted from runtime")
    properties = _schema_properties(schema, context)
    if set(properties) != expected_fields:
        raise UnlabeledAuditError(f"{context}: properties drifted from runtime")
    if schema_version is not None:
        version_schema = _as_mapping(
            properties["schema_version"], f"{context}: schema_version"
        )
        if version_schema.get("const") != schema_version:
            raise UnlabeledAuditError(f"{context}: schema_version drifted from runtime")


def _schema_properties(schema: Mapping[str, object], context: str) -> dict[str, object]:
    return _as_mapping(schema.get("properties"), f"{context}: properties")


def _write_text_atomically(path: Path, text: str) -> None:
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
            temporary.write(text)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise UnlabeledAuditError(
            f"cannot write audit bundle {path}: {error}"
        ) from error


def _parse_json_object(text: str, context: str) -> dict[str, object]:
    try:
        value: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise UnlabeledAuditError(f"{context}: invalid JSON: {error.msg}") from error
    return _as_mapping(value, context)


def _as_mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise UnlabeledAuditError(f"{context}: expected a JSON object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise UnlabeledAuditError(f"{context}: object keys must be strings")
        result[key] = item
    return result


def _require_exact_keys(
    raw: Mapping[str, object], expected: frozenset[str], context: str
) -> None:
    actual = set(raw)
    if actual != expected:
        raise UnlabeledAuditError(
            f"{context}: fields differ; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _require_string(raw: Mapping[str, object], key: str, context: str) -> str:
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise UnlabeledAuditError(f"{context}: {key} must be a non-empty string")
    return value


def _require_int(
    raw: Mapping[str, object], key: str, context: str, *, minimum: int
) -> int:
    value = raw[key]
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise UnlabeledAuditError(
            f"{context}: {key} must be an integer greater than or equal to {minimum}"
        )
    return value


def _require_lower_hex(
    raw: Mapping[str, object], key: str, length: int, context: str
) -> str:
    value = _require_string(raw, key, context)
    if len(value) != length or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise UnlabeledAuditError(
            f"{context}: {key} must be {length}-character lowercase hex"
        )
    return value


def _parse_span(value: object, context: str) -> SourceSpan:
    raw = _as_mapping(value, f"{context}: span")
    _require_exact_keys(raw, frozenset({"start_byte", "end_byte"}), context)
    return SourceSpan(
        _require_int(raw, "start_byte", context, minimum=0),
        _require_int(raw, "end_byte", context, minimum=1),
    )


def _serialize_span(span: SourceSpan) -> dict[str, int]:
    return {"end_byte": span.end_byte, "start_byte": span.start_byte}


def _parse_source_path(value: str, context: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or len(path.parts) < 4
        or path.parts[0] != "crates"
        or path.suffix != ".rs"
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise UnlabeledAuditError(f"{context}: invalid Rust source path {value!r}")
    return path


def _parse_enum[EnumType: StrEnum](
    enum_type: type[EnumType], value: str, context: str
) -> EnumType:
    try:
        return enum_type(value)
    except ValueError as error:
        raise UnlabeledAuditError(
            f"{context}: invalid {enum_type.__name__} {value!r}"
        ) from error


def _validate_audit_set_id(audit_set_id: str, *, context: str = "audit_set_id") -> None:
    if AUDIT_SET_PATTERN.fullmatch(audit_set_id) is None:
        raise UnlabeledAuditError(
            f"{context}: audit_set_id must be 3-64 lowercase letters, digits or hyphens"
        )
