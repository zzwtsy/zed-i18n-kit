from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from .schema_resources import SchemaResource


class GoldenCorpusError(ValueError):
    """Raised when the golden corpus violates its persistent contract."""


class Decision(StrEnum):
    CONFIRMED = "confirmed"
    REVIEW_REQUIRED = "review_required"
    EXCLUDED = "excluded"


class SourceScope(StrEnum):
    PRODUCTION = "production"
    TEST = "test"
    EXAMPLE = "example"
    COMPONENT_PREVIEW = "component_preview"


class SinkKind(StrEnum):
    VISIBLE_TEXT = "visible_text"
    TOOLTIP = "tooltip"
    ACCESSIBILITY = "accessibility"
    TOAST = "toast"
    PROMPT = "prompt"
    NONE = "none"
    UNKNOWN = "unknown"


class Ownership(StrEnum):
    PRODUCT = "product"
    MIXED = "mixed"
    USER = "user"
    PROTOCOL = "protocol"
    DEVELOPER = "developer"
    IDENTITY = "identity"
    UNKNOWN = "unknown"


class Feature(StrEnum):
    DIRECT_LITERAL = "direct_literal"
    BUILDER_ARGUMENT = "builder_argument"
    CHILD_ARGUMENT = "child_argument"
    LOCAL_VARIABLE = "local_variable"
    MATCH_EXPRESSION = "match_expression"
    IF_EXPRESSION = "if_expression"
    FORMAT_TEMPLATE = "format_template"
    CONCATENATION = "concatenation"
    DYNAMIC_EXTERNAL = "dynamic_external"
    ELEMENT_ID = "element_id"
    LOG_OR_DIAGNOSTIC = "log_or_diagnostic"
    PATH_URL_OR_COMMAND = "path_url_or_command"
    TOAST = "toast"
    PROMPT = "prompt"
    ACCESSIBILITY = "accessibility"
    TEST_SCOPE = "test_scope"
    PREVIEW_SCOPE = "preview_scope"
    EXAMPLE_SCOPE = "example_scope"


class SubjectKind(StrEnum):
    SINK_SLOT = "sink_slot"
    EXPRESSION_ORIGIN = "expression_origin"
    SCOPE_EXCLUSION = "scope_exclusion"


class ExpectedPresence(StrEnum):
    CANDIDATE = "candidate"
    NOT_CANDIDATE = "not_candidate"


class ReviewState(StrEnum):
    SINGLE_REVIEW = "single_review"
    INDEPENDENTLY_REVIEWED = "independently_reviewed"
    DISPUTED = "disputed"


@dataclass(frozen=True, slots=True, order=True)
class SourceSpan:
    start_byte: int
    end_byte: int

    def __post_init__(self) -> None:
        if self.start_byte < 0:
            raise GoldenCorpusError("source span start_byte cannot be negative")
        if self.end_byte <= self.start_byte:
            raise GoldenCorpusError(
                "source span end_byte must be greater than start_byte"
            )


@dataclass(frozen=True, slots=True)
class GoldenSample:
    sample_id: str
    path: PurePosixPath
    source_span: SourceSpan
    anchor: str
    scope: SourceScope
    subject_kind: SubjectKind
    sink_symbol: str | None
    text_slot: str | None
    sink_kind: SinkKind
    features: frozenset[Feature]
    ownership: Ownership
    expected_presence: ExpectedPresence
    expected_disposition: Decision
    review_state: ReviewState
    rationale: str


@dataclass(frozen=True, slots=True)
class CoverageRequirement:
    dimension: str
    value: str
    minimum: int


@dataclass(frozen=True, slots=True)
class CorpusManifest:
    schema_version: int
    zed_commit: str
    sample_file: str
    sample_count: int
    sample_sha256: str
    source_files_sha256: Mapping[PurePosixPath, str]
    minimum_counts: tuple[CoverageRequirement, ...]


@dataclass(frozen=True, slots=True)
class GoldenCorpus:
    manifest: CorpusManifest
    samples: tuple[GoldenSample, ...]

    def counts(self) -> dict[str, Counter[str]]:
        counts: dict[str, Counter[str]] = {
            "expected_presence": Counter(),
            "expected_disposition": Counter(),
            "review_state": Counter(),
            "subject_kind": Counter(),
            "scope": Counter(),
            "sink_kind": Counter(),
            "ownership": Counter(),
            "feature": Counter(),
            "crate": Counter(),
            "path": Counter(),
        }
        for sample in self.samples:
            counts["expected_presence"][sample.expected_presence.value] += 1
            counts["expected_disposition"][sample.expected_disposition.value] += 1
            counts["review_state"][sample.review_state.value] += 1
            counts["subject_kind"][sample.subject_kind.value] += 1
            counts["scope"][sample.scope.value] += 1
            counts["sink_kind"][sample.sink_kind.value] += 1
            counts["ownership"][sample.ownership.value] += 1
            counts["crate"][_crate_name(sample.path)] += 1
            counts["path"][sample.path.as_posix()] += 1
            for feature in sample.features:
                counts["feature"][feature.value] += 1
        return counts


SAMPLE_FIELDS = frozenset(
    {
        "id",
        "path",
        "source_span",
        "anchor",
        "scope",
        "subject_kind",
        "sink_symbol",
        "text_slot",
        "sink_kind",
        "features",
        "ownership",
        "expected_presence",
        "expected_disposition",
        "review_state",
        "rationale",
    }
)


def load_corpus(corpus_dir: Path) -> GoldenCorpus:
    manifest = _load_manifest(corpus_dir / "manifest.json")
    sample_path = corpus_dir / manifest.sample_file
    sample_bytes = sample_path.read_bytes()
    actual_sha256 = hashlib.sha256(sample_bytes).hexdigest()
    if actual_sha256 != manifest.sample_sha256:
        raise GoldenCorpusError(
            f"{sample_path}: SHA-256 mismatch: expected "
            f"{manifest.sample_sha256}, got {actual_sha256}"
        )

    samples = tuple(_load_samples(sample_path, sample_bytes.decode("utf-8")))
    corpus = GoldenCorpus(manifest=manifest, samples=samples)
    _validate_corpus_invariants(corpus, sample_path)
    return corpus


def validate_checkout(corpus: GoldenCorpus, zed_root: Path) -> None:
    actual_commit = _zed_commit(zed_root)
    if actual_commit != corpus.manifest.zed_commit:
        raise GoldenCorpusError(
            f"Zed commit mismatch: expected {corpus.manifest.zed_commit}, "
            f"got {actual_commit}"
        )

    source_cache: dict[PurePosixPath, bytes] = {}
    for path, expected_sha256 in corpus.manifest.source_files_sha256.items():
        source_path = zed_root / path
        if not source_path.is_file():
            raise GoldenCorpusError(f"source file does not exist: {path}")
        source_bytes = source_path.read_bytes()
        actual_sha256 = hashlib.sha256(source_bytes).hexdigest()
        if actual_sha256 != expected_sha256:
            raise GoldenCorpusError(
                f"{path}: source SHA-256 mismatch: expected "
                f"{expected_sha256}, got {actual_sha256}"
            )
        source_cache[path] = source_bytes

    for sample in corpus.samples:
        source_bytes = source_cache[sample.path]
        span = sample.source_span
        if span.end_byte > len(source_bytes):
            raise GoldenCorpusError(
                f"{sample.sample_id}: source span ends at {span.end_byte}, "
                f"past {sample.path} byte length {len(source_bytes)}"
            )
        anchor_bytes = source_bytes[span.start_byte : span.end_byte]
        try:
            anchor = anchor_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise GoldenCorpusError(
                f"{sample.sample_id}: source span does not follow UTF-8 boundaries"
            ) from error
        if anchor != sample.anchor:
            raise GoldenCorpusError(
                f"{sample.sample_id}: source span contains {anchor!r}, "
                f"expected anchor {sample.anchor!r}"
            )


def validate_schema_contract(schema_path: SchemaResource) -> None:
    schema = _load_json_object(
        schema_path.read_text(encoding="utf-8"), str(schema_path)
    )
    required = schema.get("required")
    if not isinstance(required, list) or set(required) != SAMPLE_FIELDS:
        raise GoldenCorpusError(f"{schema_path}: required fields drifted from runtime")
    properties = _as_string_mapping(
        schema.get("properties"), f"{schema_path}: properties"
    )
    if set(properties) != SAMPLE_FIELDS:
        raise GoldenCorpusError(f"{schema_path}: properties drifted from runtime")

    enum_contracts: dict[str, type[StrEnum]] = {
        "scope": SourceScope,
        "subject_kind": SubjectKind,
        "sink_kind": SinkKind,
        "ownership": Ownership,
        "expected_presence": ExpectedPresence,
        "expected_disposition": Decision,
        "review_state": ReviewState,
    }
    for field, enum_type in enum_contracts.items():
        property_schema = _as_string_mapping(
            properties[field], f"{schema_path}: properties.{field}"
        )
        actual = property_schema.get("enum")
        expected = [member.value for member in enum_type]
        if actual != expected:
            raise GoldenCorpusError(
                f"{schema_path}: {field} enum drifted from runtime: "
                f"expected {expected}, got {actual}"
            )

    features_schema = _as_string_mapping(
        properties["features"], f"{schema_path}: properties.features"
    )
    feature_items = _as_string_mapping(
        features_schema.get("items"), f"{schema_path}: properties.features.items"
    )
    actual_features = feature_items.get("enum")
    expected_features = [member.value for member in Feature]
    if actual_features != expected_features:
        raise GoldenCorpusError(f"{schema_path}: feature enum drifted from runtime")


def _load_manifest(path: Path) -> CorpusManifest:
    raw = _load_json_object(path.read_text(encoding="utf-8"), str(path))
    _require_exact_keys(
        raw,
        {
            "schema_version",
            "zed_commit",
            "sample_file",
            "sample_count",
            "sample_sha256",
            "source_files_sha256",
            "minimum_counts",
        },
        str(path),
    )
    schema_version = _require_int(raw, "schema_version", str(path))
    if schema_version != 2:
        raise GoldenCorpusError(f"{path}: unsupported schema_version {schema_version}")

    zed_commit = _require_string(raw, "zed_commit", str(path))
    if len(zed_commit) != 40 or any(
        char not in "0123456789abcdef" for char in zed_commit
    ):
        raise GoldenCorpusError(f"{path}: zed_commit must be a full lowercase Git SHA")

    sample_file = _require_string(raw, "sample_file", str(path))
    if PurePosixPath(sample_file).name != sample_file:
        raise GoldenCorpusError(f"{path}: sample_file must be a local filename")

    source_hashes_raw = _require_mapping(raw, "source_files_sha256", str(path))
    source_hashes: dict[PurePosixPath, str] = {}
    for source_path_raw, sha256_raw in source_hashes_raw.items():
        source_path = _parse_source_path(source_path_raw, str(path))
        if not isinstance(sha256_raw, str) or not _is_sha256(sha256_raw):
            raise GoldenCorpusError(
                f"{path}: invalid source SHA-256 for {source_path_raw}"
            )
        source_hashes[source_path] = sha256_raw

    minimum_counts_raw = _require_mapping(raw, "minimum_counts", str(path))
    requirements: list[CoverageRequirement] = []
    for dimension, values_raw in minimum_counts_raw.items():
        values = _as_string_mapping(values_raw, f"{path}: minimum_counts.{dimension}")
        for value, minimum_raw in values.items():
            if (
                not isinstance(minimum_raw, int)
                or isinstance(minimum_raw, bool)
                or minimum_raw < 0
            ):
                raise GoldenCorpusError(
                    f"{path}: minimum_counts.{dimension}.{value} "
                    "must be a non-negative integer"
                )
            requirements.append(CoverageRequirement(dimension, value, minimum_raw))

    sample_sha256 = _require_string(raw, "sample_sha256", str(path))
    if not _is_sha256(sample_sha256):
        raise GoldenCorpusError(f"{path}: sample_sha256 must be lowercase SHA-256")

    return CorpusManifest(
        schema_version=schema_version,
        zed_commit=zed_commit,
        sample_file=sample_file,
        sample_count=_require_positive_int(raw, "sample_count", str(path)),
        sample_sha256=sample_sha256,
        source_files_sha256=source_hashes,
        minimum_counts=tuple(requirements),
    )


def _load_samples(path: Path, text: str) -> Sequence[GoldenSample]:
    samples: list[GoldenSample] = []
    for jsonl_line, line_text in enumerate(text.splitlines(), start=1):
        if not line_text.strip():
            raise GoldenCorpusError(f"{path}:{jsonl_line}: blank lines are not allowed")
        context = f"{path}:{jsonl_line}"
        samples.append(parse_sample(_load_json_object(line_text, context), context))
    return samples


def parse_sample(raw: Mapping[str, object], context: str) -> GoldenSample:
    _require_exact_keys(raw, set(SAMPLE_FIELDS), context)
    sample_id = _require_string(raw, "id", context)
    _validate_sample_id(sample_id, context)
    source_path = _parse_source_path(_require_string(raw, "path", context), context)

    span_raw = _require_mapping(raw, "source_span", context)
    _require_exact_keys(span_raw, {"start_byte", "end_byte"}, f"{context}: source_span")
    source_span = SourceSpan(
        start_byte=_require_int(span_raw, "start_byte", f"{context}: source_span"),
        end_byte=_require_int(span_raw, "end_byte", f"{context}: source_span"),
    )

    sink_symbol = _optional_string(raw, "sink_symbol", context)
    text_slot = _optional_string(raw, "text_slot", context)
    features = _parse_features(raw["features"], context)

    return GoldenSample(
        sample_id=sample_id,
        path=source_path,
        source_span=source_span,
        anchor=_require_string(raw, "anchor", context),
        scope=_parse_enum(SourceScope, _require_string(raw, "scope", context), context),
        subject_kind=_parse_enum(
            SubjectKind, _require_string(raw, "subject_kind", context), context
        ),
        sink_symbol=sink_symbol,
        text_slot=text_slot,
        sink_kind=_parse_enum(
            SinkKind, _require_string(raw, "sink_kind", context), context
        ),
        features=features,
        ownership=_parse_enum(
            Ownership, _require_string(raw, "ownership", context), context
        ),
        expected_presence=_parse_enum(
            ExpectedPresence,
            _require_string(raw, "expected_presence", context),
            context,
        ),
        expected_disposition=_parse_enum(
            Decision, _require_string(raw, "expected_disposition", context), context
        ),
        review_state=_parse_enum(
            ReviewState, _require_string(raw, "review_state", context), context
        ),
        rationale=_require_string(raw, "rationale", context),
    )


def _validate_corpus_invariants(corpus: GoldenCorpus, sample_path: Path) -> None:
    if len(corpus.samples) != corpus.manifest.sample_count:
        raise GoldenCorpusError(
            f"{sample_path}: expected {corpus.manifest.sample_count} samples, "
            f"got {len(corpus.samples)}"
        )

    referenced_paths = {sample.path for sample in corpus.samples}
    recorded_paths = set(corpus.manifest.source_files_sha256)
    if referenced_paths != recorded_paths:
        raise GoldenCorpusError(
            f"{sample_path}: source_files_sha256 paths differ from sample paths"
        )

    sample_ids: set[str] = set()
    subjects: set[tuple[PurePosixPath, SourceSpan, str | None, str | None]] = set()
    expected_id_prefix = f"zed-{corpus.manifest.zed_commit[:7]}-"
    for sample in corpus.samples:
        if not sample.sample_id.startswith(expected_id_prefix):
            raise GoldenCorpusError(
                f"{sample_path}: sample id {sample.sample_id!r} does not match "
                f"Zed commit prefix {expected_id_prefix!r}"
            )
        if sample.sample_id in sample_ids:
            raise GoldenCorpusError(f"{sample_path}: duplicate id {sample.sample_id}")
        sample_ids.add(sample.sample_id)

        subject = (
            sample.path,
            sample.source_span,
            sample.sink_symbol,
            sample.text_slot,
        )
        if subject in subjects:
            raise GoldenCorpusError(
                f"{sample_path}: duplicate evaluation subject for {sample.sample_id}"
            )
        subjects.add(subject)
        _validate_sample_semantics(sample, sample_path)

    counts = corpus.counts()
    for requirement in corpus.manifest.minimum_counts:
        dimension_counts = counts.get(requirement.dimension)
        if dimension_counts is None:
            raise GoldenCorpusError(
                f"manifest references unsupported count dimension "
                f"{requirement.dimension!r}"
            )
        actual = dimension_counts[requirement.value]
        if actual < requirement.minimum:
            raise GoldenCorpusError(
                f"coverage requirement {requirement.dimension}."
                f"{requirement.value} >= {requirement.minimum} failed: got {actual}"
            )


def _validate_sample_semantics(sample: GoldenSample, sample_path: Path) -> None:
    if sample.expected_presence is ExpectedPresence.CANDIDATE:
        if sample.expected_disposition not in {
            Decision.CONFIRMED,
            Decision.REVIEW_REQUIRED,
        }:
            raise GoldenCorpusError(
                f"{sample_path}: {sample.sample_id}: candidate must be confirmed "
                "or review_required"
            )
    elif sample.expected_disposition is not Decision.EXCLUDED:
        raise GoldenCorpusError(
            f"{sample_path}: {sample.sample_id}: not_candidate must be excluded"
        )

    if sample.subject_kind is SubjectKind.SINK_SLOT:
        if sample.sink_symbol is None or sample.text_slot is None:
            raise GoldenCorpusError(
                f"{sample_path}: {sample.sample_id}: sink_slot requires sink_symbol "
                "and text_slot"
            )
    if sample.text_slot is not None and sample.sink_symbol is None:
        raise GoldenCorpusError(
            f"{sample_path}: {sample.sample_id}: text_slot requires sink_symbol"
        )
    if sample.subject_kind is SubjectKind.SCOPE_EXCLUSION:
        if sample.scope is SourceScope.PRODUCTION:
            raise GoldenCorpusError(
                f"{sample_path}: {sample.sample_id}: scope_exclusion cannot be production"
            )
        if sample.expected_presence is not ExpectedPresence.NOT_CANDIDATE:
            raise GoldenCorpusError(
                f"{sample_path}: {sample.sample_id}: scope_exclusion must be not_candidate"
            )
    if sample.review_state is ReviewState.DISPUTED:
        if sample.expected_disposition is not Decision.REVIEW_REQUIRED:
            raise GoldenCorpusError(
                f"{sample_path}: {sample.sample_id}: disputed samples must require review"
            )


def _validate_sample_id(sample_id: str, context: str) -> None:
    parts = sample_id.split("-")
    if (
        len(parts) != 3
        or parts[0] != "zed"
        or len(parts[1]) != 7
        or any(char not in "0123456789abcdef" for char in parts[1])
        or len(parts[2]) != 4
        or not parts[2].isdigit()
    ):
        raise GoldenCorpusError(f"{context}: invalid sample id {sample_id!r}")


def _parse_source_path(value: str, context: str) -> PurePosixPath:
    source_path = PurePosixPath(value)
    if source_path.is_absolute() or ".." in source_path.parts:
        raise GoldenCorpusError(f"{context}: path must stay within the Zed checkout")
    if not source_path.parts or source_path.parts[0] != "crates":
        raise GoldenCorpusError(f"{context}: path must start with crates/")
    if source_path.suffix != ".rs":
        raise GoldenCorpusError(f"{context}: path must reference a Rust source file")
    return source_path


def _optional_string(raw: Mapping[str, object], key: str, context: str) -> str | None:
    value = raw[key]
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise GoldenCorpusError(f"{context}: {key} must be a non-empty string or null")
    return value


def _parse_features(value: object, context: str) -> frozenset[Feature]:
    if not isinstance(value, list) or not value:
        raise GoldenCorpusError(f"{context}: features must be a non-empty array")
    features: set[Feature] = set()
    for raw_feature in value:
        if not isinstance(raw_feature, str):
            raise GoldenCorpusError(f"{context}: feature values must be strings")
        features.add(_parse_enum(Feature, raw_feature, f"{context}: features"))
    if len(features) != len(value):
        raise GoldenCorpusError(f"{context}: duplicate features are not allowed")
    return frozenset(features)


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _zed_commit(zed_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(zed_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise GoldenCorpusError(
            f"cannot resolve Zed commit at {zed_root}: {error}"
        ) from error
    return result.stdout.strip()


def _crate_name(path: PurePosixPath) -> str:
    return path.parts[1] if len(path.parts) > 1 else "unknown"


def _load_json_object(text: str, context: str) -> dict[str, object]:
    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise GoldenCorpusError(f"{context}: invalid JSON: {error.msg}") from error
    return _as_string_mapping(raw, context)


def _as_string_mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise GoldenCorpusError(f"{context}: expected a JSON object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise GoldenCorpusError(f"{context}: object keys must be strings")
        result[key] = item
    return result


def _require_exact_keys(
    raw: Mapping[str, object], expected: set[str], context: str
) -> None:
    actual = set(raw)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    raise GoldenCorpusError(
        f"{context}: invalid fields; missing={missing}, unknown={unknown}"
    )


def _require_string(raw: Mapping[str, object], key: str, context: str) -> str:
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise GoldenCorpusError(f"{context}: {key} must be a non-empty string")
    return value


def _require_int(raw: Mapping[str, object], key: str, context: str) -> int:
    value = raw[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise GoldenCorpusError(f"{context}: {key} must be an integer")
    return value


def _require_positive_int(raw: Mapping[str, object], key: str, context: str) -> int:
    value = _require_int(raw, key, context)
    if value <= 0:
        raise GoldenCorpusError(f"{context}: {key} must be positive")
    return value


def _require_mapping(
    raw: Mapping[str, object], key: str, context: str
) -> dict[str, object]:
    return _as_string_mapping(raw[key], f"{context}: {key}")


def _parse_enum[EnumT: StrEnum](
    enum_type: type[EnumT], value: str, context: str
) -> EnumT:
    try:
        return enum_type(value)
    except ValueError as error:
        allowed = ", ".join(member.value for member in enum_type)
        raise GoldenCorpusError(
            f"{context}: invalid {enum_type.__name__} {value!r}; allowed: {allowed}"
        ) from error
