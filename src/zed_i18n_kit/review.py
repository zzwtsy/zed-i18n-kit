from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from .golden import (
    Decision,
    ExpectedPresence,
    GoldenCorpus,
    Ownership,
    SinkKind,
    SourceScope,
    SourceSpan,
    SubjectKind,
    validate_checkout,
)
from .schema_resources import SchemaResource

REVIEW_BUNDLE_SCHEMA_VERSION = 1
REVIEW_RESULT_SCHEMA_VERSION = 1
CONTEXT_LINE_COUNT = 2
REVIEW_SET_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")

BUNDLE_FIELDS = frozenset(
    {
        "schema_version",
        "review_set_id",
        "zed_commit",
        "corpus_sample_sha256",
        "samples",
    }
)
BUNDLE_SAMPLE_FIELDS = frozenset(
    {
        "sample_id",
        "path",
        "source_span",
        "anchor",
        "context_span",
        "source_context",
        "scope",
        "subject_kind",
        "sink_symbol",
        "text_slot",
        "sink_kind",
    }
)
RESULT_FIELDS = frozenset(
    {
        "schema_version",
        "review_set_id",
        "zed_commit",
        "corpus_sample_sha256",
        "reviewer_id",
        "decisions",
    }
)
REVIEW_DECISION_FIELDS = frozenset(
    {
        "sample_id",
        "expected_presence",
        "expected_disposition",
        "ownership",
        "rationale",
    }
)


class ReviewError(ValueError):
    """Raised when independent review evidence violates its protocol."""


@dataclass(frozen=True, slots=True)
class ReviewBundleSample:
    sample_id: str
    path: PurePosixPath
    source_span: SourceSpan
    anchor: str
    context_span: SourceSpan
    source_context: str
    scope: SourceScope
    subject_kind: SubjectKind
    sink_symbol: str | None
    text_slot: str | None
    sink_kind: SinkKind


@dataclass(frozen=True, slots=True)
class ReviewBundle:
    review_set_id: str
    zed_commit: str
    corpus_sample_sha256: str
    samples: tuple[ReviewBundleSample, ...]


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    sample_id: str
    expected_presence: ExpectedPresence
    expected_disposition: Decision
    ownership: Ownership
    rationale: str


@dataclass(frozen=True, slots=True)
class ReviewResult:
    review_set_id: str
    zed_commit: str
    corpus_sample_sha256: str
    reviewer_id: str
    decisions: tuple[ReviewDecision, ...]


@dataclass(frozen=True, slots=True)
class ReviewReconciliation:
    review_set_id: str
    reviewer_id: str
    agreed_sample_ids: tuple[str, ...]
    disputed_sample_ids: tuple[str, ...]
    missing_sample_ids: tuple[str, ...]

    @property
    def is_complete_agreement(self) -> bool:
        return not self.disputed_sample_ids and not self.missing_sample_ids


def build_review_bundle(
    corpus: GoldenCorpus,
    zed_root: Path,
    *,
    review_set_id: str,
) -> ReviewBundle:
    _validate_review_set_id(review_set_id)
    validate_checkout(corpus, zed_root)
    source_cache: dict[PurePosixPath, bytes] = {}
    samples: list[ReviewBundleSample] = []
    for sample in corpus.samples:
        source = source_cache.get(sample.path)
        if source is None:
            try:
                source = (zed_root / sample.path).read_bytes()
            except OSError as error:
                raise ReviewError(
                    f"cannot read review source {sample.path}: {error}"
                ) from error
            source_cache[sample.path] = source
        context_span = context_span_for_source(source, sample.source_span)
        try:
            source_context = source[
                context_span.start_byte : context_span.end_byte
            ].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ReviewError(
                f"{sample.sample_id}: review context is not valid UTF-8"
            ) from error
        samples.append(
            ReviewBundleSample(
                sample_id=sample.sample_id,
                path=sample.path,
                source_span=sample.source_span,
                anchor=sample.anchor,
                context_span=context_span,
                source_context=source_context,
                scope=sample.scope,
                subject_kind=sample.subject_kind,
                sink_symbol=sample.sink_symbol,
                text_slot=sample.text_slot,
                sink_kind=sample.sink_kind,
            )
        )
    return ReviewBundle(
        review_set_id=review_set_id,
        zed_commit=corpus.manifest.zed_commit,
        corpus_sample_sha256=corpus.manifest.sample_sha256,
        samples=tuple(samples),
    )


def serialize_review_bundle(bundle: ReviewBundle) -> str:
    payload = {
        "corpus_sample_sha256": bundle.corpus_sample_sha256,
        "review_set_id": bundle.review_set_id,
        "samples": [
            {
                "anchor": sample.anchor,
                "context_span": _serialize_span(sample.context_span),
                "path": sample.path.as_posix(),
                "sample_id": sample.sample_id,
                "scope": sample.scope.value,
                "sink_kind": sample.sink_kind.value,
                "sink_symbol": sample.sink_symbol,
                "source_context": sample.source_context,
                "source_span": _serialize_span(sample.source_span),
                "subject_kind": sample.subject_kind.value,
                "text_slot": sample.text_slot,
            }
            for sample in bundle.samples
        ],
        "schema_version": REVIEW_BUNDLE_SCHEMA_VERSION,
        "zed_commit": bundle.zed_commit,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_review_bundle(path: Path, bundle: ReviewBundle) -> None:
    _write_text_atomically(path, serialize_review_bundle(bundle))


def validate_review_schema_contracts(
    bundle_schema_path: SchemaResource,
    result_schema_path: SchemaResource,
) -> None:
    bundle_schema = _load_schema(bundle_schema_path, "review bundle")
    _validate_schema_object(
        bundle_schema, BUNDLE_FIELDS, str(bundle_schema_path), schema_version=1
    )
    bundle_properties = _schema_properties(bundle_schema, str(bundle_schema_path))
    bundle_samples = _as_mapping(
        bundle_properties["samples"], f"{bundle_schema_path}: properties.samples"
    )
    bundle_sample_schema = _as_mapping(
        bundle_samples.get("items"), f"{bundle_schema_path}: samples.items"
    )
    _validate_schema_object(
        bundle_sample_schema,
        BUNDLE_SAMPLE_FIELDS,
        f"{bundle_schema_path}: samples.items",
    )
    bundle_sample_properties = _schema_properties(
        bundle_sample_schema, f"{bundle_schema_path}: samples.items"
    )
    _validate_schema_enum(
        bundle_sample_properties, "scope", SourceScope, str(bundle_schema_path)
    )
    _validate_schema_enum(
        bundle_sample_properties,
        "subject_kind",
        SubjectKind,
        str(bundle_schema_path),
    )
    _validate_schema_enum(
        bundle_sample_properties, "sink_kind", SinkKind, str(bundle_schema_path)
    )

    result_schema = _load_schema(result_schema_path, "review result")
    _validate_schema_object(
        result_schema, RESULT_FIELDS, str(result_schema_path), schema_version=1
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
        REVIEW_DECISION_FIELDS,
        f"{result_schema_path}: decisions.items",
    )
    decision_properties = _schema_properties(
        decision_schema, f"{result_schema_path}: decisions.items"
    )
    _validate_schema_enum(
        decision_properties,
        "expected_presence",
        ExpectedPresence,
        str(result_schema_path),
    )
    _validate_schema_enum(
        decision_properties,
        "expected_disposition",
        Decision,
        str(result_schema_path),
    )
    _validate_schema_enum(
        decision_properties, "ownership", Ownership, str(result_schema_path)
    )


def load_review_result(path: Path) -> ReviewResult:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ReviewError(f"cannot read review result {path}: {error}") from error
    return parse_review_result_json(text, str(path))


def parse_review_result_json(text: str, context: str = "review-result") -> ReviewResult:
    try:
        value: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise ReviewError(f"{context}: invalid JSON: {error.msg}") from error
    raw = _as_mapping(value, context)
    _require_exact_keys(raw, RESULT_FIELDS, context)
    schema_version = _require_int(raw, "schema_version", context)
    if schema_version != REVIEW_RESULT_SCHEMA_VERSION:
        raise ReviewError(f"{context}: unsupported schema_version {schema_version}")

    review_set_id = _require_string(raw, "review_set_id", context)
    _validate_review_set_id(review_set_id, context=context)
    zed_commit = _require_lower_hex(raw, "zed_commit", length=40, context=context)
    corpus_sample_sha256 = _require_lower_hex(
        raw, "corpus_sample_sha256", length=64, context=context
    )
    reviewer_id = _require_string(raw, "reviewer_id", context)
    decisions_value = raw["decisions"]
    if not isinstance(decisions_value, list):
        raise ReviewError(f"{context}: decisions must be an array")

    decisions: list[ReviewDecision] = []
    sample_ids: set[str] = set()
    for index, decision_value in enumerate(decisions_value):
        decision_context = f"{context}: decisions[{index}]"
        decision_raw = _as_mapping(decision_value, decision_context)
        _require_exact_keys(decision_raw, REVIEW_DECISION_FIELDS, decision_context)
        sample_id = _require_string(decision_raw, "sample_id", decision_context)
        if sample_id in sample_ids:
            raise ReviewError(f"{context}: duplicate decision for {sample_id}")
        sample_ids.add(sample_id)
        presence = _parse_enum(
            ExpectedPresence,
            _require_string(decision_raw, "expected_presence", decision_context),
            decision_context,
        )
        disposition = _parse_enum(
            Decision,
            _require_string(decision_raw, "expected_disposition", decision_context),
            decision_context,
        )
        _validate_presence_disposition(presence, disposition, decision_context)
        decisions.append(
            ReviewDecision(
                sample_id=sample_id,
                expected_presence=presence,
                expected_disposition=disposition,
                ownership=_parse_enum(
                    Ownership,
                    _require_string(decision_raw, "ownership", decision_context),
                    decision_context,
                ),
                rationale=_require_string(decision_raw, "rationale", decision_context),
            )
        )

    return ReviewResult(
        review_set_id=review_set_id,
        zed_commit=zed_commit,
        corpus_sample_sha256=corpus_sample_sha256,
        reviewer_id=reviewer_id,
        decisions=tuple(decisions),
    )


def reconcile_review_result(
    corpus: GoldenCorpus,
    result: ReviewResult,
    *,
    review_set_id: str,
) -> ReviewReconciliation:
    _validate_review_set_id(review_set_id)
    if result.review_set_id != review_set_id:
        raise ReviewError(
            f"review set mismatch: expected {review_set_id!r}, "
            f"got {result.review_set_id!r}"
        )
    if result.zed_commit != corpus.manifest.zed_commit:
        raise ReviewError(
            f"review result Zed commit mismatch: expected "
            f"{corpus.manifest.zed_commit}, got {result.zed_commit}"
        )
    if result.corpus_sample_sha256 != corpus.manifest.sample_sha256:
        raise ReviewError(
            "review result corpus sample SHA-256 differs from current corpus"
        )

    samples_by_id = {sample.sample_id: sample for sample in corpus.samples}
    decisions_by_id = {decision.sample_id: decision for decision in result.decisions}
    unknown_ids = tuple(sorted(set(decisions_by_id) - set(samples_by_id)))
    if unknown_ids:
        raise ReviewError(
            "review result contains unknown sample IDs: " + ",".join(unknown_ids)
        )

    agreed: list[str] = []
    disputed: list[str] = []
    for sample_id, decision in decisions_by_id.items():
        sample = samples_by_id[sample_id]
        if (
            decision.expected_presence is sample.expected_presence
            and decision.expected_disposition is sample.expected_disposition
            and decision.ownership is sample.ownership
        ):
            agreed.append(sample_id)
        else:
            disputed.append(sample_id)

    return ReviewReconciliation(
        review_set_id=review_set_id,
        reviewer_id=result.reviewer_id,
        agreed_sample_ids=tuple(sorted(agreed)),
        disputed_sample_ids=tuple(sorted(disputed)),
        missing_sample_ids=tuple(sorted(set(samples_by_id) - set(decisions_by_id))),
    )


def serialize_review_reconciliation(report: ReviewReconciliation) -> str:
    payload = {
        "agreed_sample_ids": list(report.agreed_sample_ids),
        "complete_agreement": report.is_complete_agreement,
        "disputed_sample_ids": list(report.disputed_sample_ids),
        "missing_sample_ids": list(report.missing_sample_ids),
        "review_set_id": report.review_set_id,
        "reviewer_id": report.reviewer_id,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def context_span_for_source(source: bytes, span: SourceSpan) -> SourceSpan:
    start = span.start_byte
    for _ in range(CONTEXT_LINE_COUNT + 1):
        previous_newline = source.rfind(b"\n", 0, start)
        if previous_newline < 0:
            start = 0
            break
        start = previous_newline
    if start > 0:
        start += 1

    end = span.end_byte
    for _ in range(CONTEXT_LINE_COUNT + 1):
        next_newline = source.find(b"\n", end)
        if next_newline < 0:
            end = len(source)
            break
        end = next_newline + 1
    return SourceSpan(start, end)


def _serialize_span(span: SourceSpan) -> dict[str, int]:
    return {"end_byte": span.end_byte, "start_byte": span.start_byte}


def _load_schema(path: SchemaResource, artifact: str) -> dict[str, object]:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReviewError(f"cannot load {artifact} schema {path}: {error}") from error
    return _as_mapping(value, str(path))


def _validate_schema_object(
    schema: Mapping[str, object],
    expected_fields: frozenset[str],
    context: str,
    *,
    schema_version: int | None = None,
) -> None:
    if schema.get("additionalProperties") is not False:
        raise ReviewError(f"{context}: object must reject additional properties")
    required = schema.get("required")
    if not isinstance(required, list) or set(required) != expected_fields:
        raise ReviewError(f"{context}: required fields drifted from runtime")
    properties = _schema_properties(schema, context)
    if set(properties) != expected_fields:
        raise ReviewError(f"{context}: properties drifted from runtime")
    if schema_version is not None:
        version_schema = _as_mapping(
            properties["schema_version"], f"{context}: schema_version"
        )
        if version_schema.get("const") != schema_version:
            raise ReviewError(f"{context}: schema_version drifted from runtime")


def _schema_properties(schema: Mapping[str, object], context: str) -> dict[str, object]:
    return _as_mapping(schema.get("properties"), f"{context}: properties")


def _validate_schema_enum[EnumType: StrEnum](
    properties: Mapping[str, object],
    field: str,
    enum_type: type[EnumType],
    context: str,
) -> None:
    field_schema = _as_mapping(properties[field], f"{context}: {field}")
    actual = field_schema.get("enum")
    expected = [member.value for member in enum_type]
    if actual != expected:
        raise ReviewError(
            f"{context}: {field} enum drifted from runtime: "
            f"expected {expected}, got {actual}"
        )


def _validate_presence_disposition(
    presence: ExpectedPresence, disposition: Decision, context: str
) -> None:
    if presence is ExpectedPresence.CANDIDATE:
        if disposition not in {Decision.CONFIRMED, Decision.REVIEW_REQUIRED}:
            raise ReviewError(
                f"{context}: candidate must be confirmed or review_required"
            )
    elif disposition is not Decision.EXCLUDED:
        raise ReviewError(f"{context}: not_candidate must be excluded")


def _validate_review_set_id(
    review_set_id: str, *, context: str = "review_set_id"
) -> None:
    if REVIEW_SET_PATTERN.fullmatch(review_set_id) is None:
        raise ReviewError(
            f"{context}: review_set_id must be 3-64 lowercase letters, digits or hyphens"
        )


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
        raise ReviewError(f"cannot write review artifact {path}: {error}") from error


def _as_mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ReviewError(f"{context}: expected a JSON object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ReviewError(f"{context}: object keys must be strings")
        result[key] = item
    return result


def _require_exact_keys(
    raw: Mapping[str, object], expected: frozenset[str], context: str
) -> None:
    actual = set(raw)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ReviewError(f"{context}: fields differ; missing={missing}, extra={extra}")


def _require_string(raw: Mapping[str, object], key: str, context: str) -> str:
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise ReviewError(f"{context}: {key} must be a non-empty string")
    return value


def _require_int(raw: Mapping[str, object], key: str, context: str) -> int:
    value = raw[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ReviewError(f"{context}: {key} must be an integer")
    return value


def _require_lower_hex(
    raw: Mapping[str, object],
    key: str,
    *,
    length: int,
    context: str,
) -> str:
    value = _require_string(raw, key, context)
    if len(value) != length or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ReviewError(f"{context}: {key} must be {length}-character lowercase hex")
    return value


def _parse_enum[EnumType: StrEnum](
    enum_type: type[EnumType], value: str, context: str
) -> EnumType:
    try:
        return enum_type(value)
    except ValueError as error:
        raise ReviewError(
            f"{context}: invalid {enum_type.__name__} {value!r}"
        ) from error
