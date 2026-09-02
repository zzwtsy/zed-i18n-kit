from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from .golden import GoldenCorpus, SourceSpan
from .rust_cst import RustCst, parse_rust_cst, source_span_for_node

CST_CALIBRATION_SAMPLE_PREFIX = "zed-2551721-"


class CstCalibrationError(ValueError):
    """Raised when CST calibration cannot preserve its fixture contract."""


class CstCalibrationMode(StrEnum):
    EXACT = "exact"
    SMALLEST_CONTAINING = "smallest_containing"


@dataclass(frozen=True, slots=True)
class CstCalibrationCase:
    sample_id: str
    mode: CstCalibrationMode
    node_kind: str


@dataclass(frozen=True, slots=True)
class CstCalibrationResult:
    sample_id: str
    path: PurePosixPath
    node_kind: str
    source_span: SourceSpan
    exact: bool
    parse_has_errors: bool


CST_CALIBRATION_CASES = tuple(
    CstCalibrationCase(f"{CST_CALIBRATION_SAMPLE_PREFIX}{suffix}", mode, node_kind)
    for suffix, mode, node_kind in (
        ("0251", CstCalibrationMode.EXACT, "string_literal"),
        ("0252", CstCalibrationMode.EXACT, "string_literal"),
        ("0253", CstCalibrationMode.EXACT, "string_literal"),
        ("0254", CstCalibrationMode.EXACT, "reference_expression"),
        ("0255", CstCalibrationMode.EXACT, "string_literal"),
        ("0256", CstCalibrationMode.EXACT, "string_literal"),
        ("0257", CstCalibrationMode.EXACT, "string_literal"),
        ("0258", CstCalibrationMode.EXACT, "string_literal"),
        ("0259", CstCalibrationMode.EXACT, "string_literal"),
        ("0260", CstCalibrationMode.SMALLEST_CONTAINING, "raw_string_literal"),
        ("0261", CstCalibrationMode.EXACT, "string_literal"),
        ("0262", CstCalibrationMode.EXACT, "reference_expression"),
        ("0263", CstCalibrationMode.EXACT, "reference_expression"),
        ("0264", CstCalibrationMode.EXACT, "macro_invocation"),
        ("0265", CstCalibrationMode.EXACT, "macro_invocation"),
        ("0266", CstCalibrationMode.EXACT, "field_expression"),
    )
)


def validate_cst_calibration(
    corpus: GoldenCorpus, zed_root: Path
) -> tuple[CstCalibrationResult, ...]:
    samples = {sample.sample_id: sample for sample in corpus.samples}
    missing = sorted(
        case.sample_id
        for case in CST_CALIBRATION_CASES
        if case.sample_id not in samples
    )
    if missing:
        raise CstCalibrationError(f"CST calibration samples are missing: {missing}")

    parsed_sources: dict[PurePosixPath, RustCst] = {}
    results: list[CstCalibrationResult] = []
    for case in CST_CALIBRATION_CASES:
        sample = samples[case.sample_id]
        parsed_source = parsed_sources.get(sample.path)
        if parsed_source is None:
            try:
                source = (zed_root / sample.path).read_bytes()
            except OSError as error:
                raise CstCalibrationError(
                    f"cannot read calibration source {sample.path}: {error}"
                ) from error
            parsed_source = parse_rust_cst(source)
            parsed_sources[sample.path] = parsed_source

        exact_node = parsed_source.exact_named_node(sample.source_span)
        if case.mode is CstCalibrationMode.EXACT:
            node = exact_node
            exact = True
        else:
            node = parsed_source.smallest_named_node_containing(sample.source_span)
            exact = exact_node is not None
        if node is None:
            raise CstCalibrationError(
                f"{sample.sample_id}: cannot locate {case.mode} CST node"
            )
        if node.type != case.node_kind:
            raise CstCalibrationError(
                f"{sample.sample_id}: expected {case.node_kind}, got "
                f"{node.type} at {node.start_byte}..{node.end_byte}"
            )
        if case.mode is CstCalibrationMode.SMALLEST_CONTAINING and exact:
            raise CstCalibrationError(
                f"{sample.sample_id}: scope fixture unexpectedly became an exact node"
            )
        results.append(
            CstCalibrationResult(
                sample_id=sample.sample_id,
                path=sample.path,
                node_kind=node.type,
                source_span=source_span_for_node(node),
                exact=exact,
                parse_has_errors=parsed_source.has_errors,
            )
        )
    return tuple(results)
