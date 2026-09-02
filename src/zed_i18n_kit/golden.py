from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath


class GoldenCorpusError(ValueError):
    """Raised when a golden corpus violates its persistent contract."""


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


@dataclass(frozen=True)
class GoldenSample:
    sample_id: str
    path: PurePosixPath
    line: int
    anchor: str
    scope: SourceScope
    sink_symbol: str | None
    sink_kind: SinkKind
    features: frozenset[Feature]
    ownership: Ownership
    decision: Decision
    rationale: str


@dataclass(frozen=True)
class CoverageRequirement:
    dimension: str
    value: str
    minimum: int


@dataclass(frozen=True)
class CorpusManifest:
    schema_version: int
    zed_commit: str
    sample_file: str
    sample_count: int
    sample_sha256: str
    minimum_counts: tuple[CoverageRequirement, ...]


@dataclass(frozen=True)
class GoldenCorpus:
    manifest: CorpusManifest
    samples: tuple[GoldenSample, ...]

    def counts(self) -> dict[str, Counter[str]]:
        counts: dict[str, Counter[str]] = {
            "decision": Counter(),
            "scope": Counter(),
            "sink_kind": Counter(),
            "ownership": Counter(),
            "feature": Counter(),
            "crate": Counter(),
            "path": Counter(),
        }
        for sample in self.samples:
            counts["decision"][sample.decision.value] += 1
            counts["scope"][sample.scope.value] += 1
            counts["sink_kind"][sample.sink_kind.value] += 1
            counts["ownership"][sample.ownership.value] += 1
            counts["crate"][_crate_name(sample.path)] += 1
            counts["path"][sample.path.as_posix()] += 1
            for feature in sample.features:
                counts["feature"][feature.value] += 1
        return counts


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

    line_cache: dict[PurePosixPath, list[str]] = {}
    for sample in corpus.samples:
        lines = line_cache.get(sample.path)
        if lines is None:
            source_path = zed_root / sample.path
            if not source_path.is_file():
                raise GoldenCorpusError(
                    f"{sample.sample_id}: source file does not exist: {sample.path}"
                )
            lines = source_path.read_text(encoding="utf-8").splitlines()
            line_cache[sample.path] = lines

        if sample.line > len(lines):
            raise GoldenCorpusError(
                f"{sample.sample_id}: line {sample.line} exceeds "
                f"{sample.path} line count {len(lines)}"
            )
        source_line = lines[sample.line - 1]
        if sample.anchor not in source_line:
            raise GoldenCorpusError(
                f"{sample.sample_id}: anchor {sample.anchor!r} not found at "
                f"{sample.path}:{sample.line}: {source_line.strip()!r}"
            )


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
            "minimum_counts",
        },
        str(path),
    )
    schema_version = _require_int(raw, "schema_version", str(path))
    if schema_version != 1:
        raise GoldenCorpusError(f"{path}: unsupported schema_version {schema_version}")

    zed_commit = _require_string(raw, "zed_commit", str(path))
    if len(zed_commit) != 40 or any(
        char not in "0123456789abcdef" for char in zed_commit
    ):
        raise GoldenCorpusError(f"{path}: zed_commit must be a full lowercase Git SHA")

    sample_file = _require_string(raw, "sample_file", str(path))
    if PurePosixPath(sample_file).name != sample_file:
        raise GoldenCorpusError(f"{path}: sample_file must be a local filename")

    minimum_counts_raw = _require_mapping(raw, "minimum_counts", str(path))
    requirements: list[CoverageRequirement] = []
    for dimension, values_raw in minimum_counts_raw.items():
        values = _as_string_mapping(values_raw, f"{path}: minimum_counts.{dimension}")
        for value, minimum_raw in values.items():
            if not isinstance(minimum_raw, int) or isinstance(minimum_raw, bool):
                raise GoldenCorpusError(
                    f"{path}: minimum_counts.{dimension}.{value} must be an integer"
                )
            if minimum_raw < 0:
                raise GoldenCorpusError(
                    f"{path}: minimum_counts.{dimension}.{value} cannot be negative"
                )
            requirements.append(
                CoverageRequirement(
                    dimension=dimension, value=value, minimum=minimum_raw
                )
            )

    return CorpusManifest(
        schema_version=schema_version,
        zed_commit=zed_commit,
        sample_file=sample_file,
        sample_count=_require_int(raw, "sample_count", str(path)),
        sample_sha256=_require_string(raw, "sample_sha256", str(path)),
        minimum_counts=tuple(requirements),
    )


def _load_samples(path: Path, text: str) -> Sequence[GoldenSample]:
    samples: list[GoldenSample] = []
    for jsonl_line, line_text in enumerate(text.splitlines(), start=1):
        if not line_text.strip():
            raise GoldenCorpusError(f"{path}:{jsonl_line}: blank lines are not allowed")
        context = f"{path}:{jsonl_line}"
        raw = _load_json_object(line_text, context)
        samples.append(_parse_sample(raw, context))
    return samples


def _parse_sample(raw: Mapping[str, object], context: str) -> GoldenSample:
    _require_exact_keys(
        raw,
        {
            "id",
            "path",
            "line",
            "anchor",
            "scope",
            "sink_symbol",
            "sink_kind",
            "features",
            "ownership",
            "decision",
            "rationale",
        },
        context,
    )
    sample_id = _require_string(raw, "id", context)
    sample_id_parts = sample_id.split("-")
    if (
        len(sample_id_parts) != 3
        or sample_id_parts[0] != "zed"
        or len(sample_id_parts[1]) != 7
        or any(char not in "0123456789abcdef" for char in sample_id_parts[1])
        or len(sample_id_parts[2]) != 4
        or not sample_id_parts[2].isdigit()
    ):
        raise GoldenCorpusError(f"{context}: invalid sample id {sample_id!r}")

    source_path = PurePosixPath(_require_string(raw, "path", context))
    if source_path.is_absolute() or ".." in source_path.parts:
        raise GoldenCorpusError(f"{context}: path must stay within the Zed checkout")
    if not source_path.parts or source_path.parts[0] != "crates":
        raise GoldenCorpusError(f"{context}: path must start with crates/")

    anchor = _require_string(raw, "anchor", context)
    if "\n" in anchor or "\r" in anchor:
        raise GoldenCorpusError(f"{context}: anchor must fit on one source line")

    sink_symbol_raw = raw["sink_symbol"]
    if sink_symbol_raw is not None and not isinstance(sink_symbol_raw, str):
        raise GoldenCorpusError(f"{context}: sink_symbol must be a string or null")

    features_raw = raw["features"]
    if not isinstance(features_raw, list) or not features_raw:
        raise GoldenCorpusError(f"{context}: features must be a non-empty array")
    features: set[Feature] = set()
    for feature_raw in features_raw:
        if not isinstance(feature_raw, str):
            raise GoldenCorpusError(f"{context}: feature values must be strings")
        features.add(_parse_enum(Feature, feature_raw, f"{context}: features"))
    if len(features) != len(features_raw):
        raise GoldenCorpusError(f"{context}: duplicate features are not allowed")

    return GoldenSample(
        sample_id=sample_id,
        path=source_path,
        line=_require_positive_int(raw, "line", context),
        anchor=anchor,
        scope=_parse_enum(SourceScope, _require_string(raw, "scope", context), context),
        sink_symbol=sink_symbol_raw,
        sink_kind=_parse_enum(
            SinkKind, _require_string(raw, "sink_kind", context), context
        ),
        features=frozenset(features),
        ownership=_parse_enum(
            Ownership, _require_string(raw, "ownership", context), context
        ),
        decision=_parse_enum(
            Decision, _require_string(raw, "decision", context), context
        ),
        rationale=_require_string(raw, "rationale", context),
    )


def _validate_corpus_invariants(corpus: GoldenCorpus, sample_path: Path) -> None:
    if len(corpus.samples) != corpus.manifest.sample_count:
        raise GoldenCorpusError(
            f"{sample_path}: expected {corpus.manifest.sample_count} samples, "
            f"got {len(corpus.samples)}"
        )

    sample_ids: set[str] = set()
    locations: set[tuple[PurePosixPath, int, str]] = set()
    for sample in corpus.samples:
        expected_id_prefix = f"zed-{corpus.manifest.zed_commit[:7]}-"
        if not sample.sample_id.startswith(expected_id_prefix):
            raise GoldenCorpusError(
                f"{sample_path}: sample id {sample.sample_id!r} does not match "
                f"Zed commit prefix {expected_id_prefix!r}"
            )
        if sample.sample_id in sample_ids:
            raise GoldenCorpusError(f"{sample_path}: duplicate id {sample.sample_id}")
        sample_ids.add(sample.sample_id)

        location = (sample.path, sample.line, sample.anchor)
        if location in locations:
            raise GoldenCorpusError(
                f"{sample_path}: duplicate source occurrence "
                f"{sample.path}:{sample.line} {sample.anchor!r}"
            )
        locations.add(location)

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
