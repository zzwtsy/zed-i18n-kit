from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from .golden import (
    Decision,
    ExpectedPresence,
    GoldenCorpus,
    GoldenCorpusError,
    GoldenSample,
    SourceSpan,
    SubjectKind,
)


@dataclass(frozen=True, slots=True)
class ProvenanceRange:
    path: PurePosixPath
    source_span: SourceSpan


@dataclass(frozen=True, slots=True)
class ScanOccurrence:
    occurrence_id: str
    path: PurePosixPath
    source_span: SourceSpan
    sink_symbol: str | None
    text_slot: str | None
    disposition: Decision
    provenance: tuple[ProvenanceRange, ...] = ()


@dataclass(frozen=True, slots=True)
class ScanResult:
    schema_version: int
    zed_commit: str
    occurrences: tuple[ScanOccurrence, ...]


@dataclass(frozen=True, slots=True)
class Rate:
    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        if self.denominator == 0:
            return None
        return self.numerator / self.denominator


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    auto_confirm_precision: Rate
    candidate_recall: Rate
    unsafe_promotion_rate: Rate
    exclusion_leakage: Rate
    unmatched_sample_count: int
    unmatched_occurrence_count: int
    ambiguous_sample_count: int

    @property
    def unmatched_count(self) -> int:
        return (
            self.unmatched_sample_count
            + self.unmatched_occurrence_count
            + self.ambiguous_sample_count
        )


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    metrics: EvaluationMetrics
    predictions: dict[str, Decision]
    unmatched_sample_ids: tuple[str, ...]
    unmatched_occurrence_ids: tuple[str, ...]
    ambiguous_sample_ids: tuple[str, ...]


def evaluate_scan_result(
    corpus: GoldenCorpus, scan_result: ScanResult
) -> EvaluationReport:
    if scan_result.schema_version != 1:
        raise GoldenCorpusError(
            f"unsupported scan-result schema_version {scan_result.schema_version}"
        )
    if scan_result.zed_commit != corpus.manifest.zed_commit:
        raise GoldenCorpusError(
            f"scan-result Zed commit mismatch: expected "
            f"{corpus.manifest.zed_commit}, got {scan_result.zed_commit}"
        )

    occurrence_ids: set[str] = set()
    for occurrence in scan_result.occurrences:
        if not occurrence.occurrence_id.strip():
            raise GoldenCorpusError("scan-result occurrence_id cannot be empty")
        if occurrence.occurrence_id in occurrence_ids:
            raise GoldenCorpusError(
                f"duplicate scan-result occurrence_id {occurrence.occurrence_id!r}"
            )
        occurrence_ids.add(occurrence.occurrence_id)
        if occurrence.text_slot is not None and occurrence.sink_symbol is None:
            raise GoldenCorpusError(
                f"{occurrence.occurrence_id}: text_slot requires sink_symbol"
            )

    predictions: dict[str, Decision] = {}
    matched_occurrence_ids: set[str] = set()
    unmatched_sample_ids: list[str] = []
    ambiguous_sample_ids: list[str] = []
    for sample in corpus.samples:
        matches = [
            occurrence
            for occurrence in scan_result.occurrences
            if _occurrence_matches_sample(occurrence, sample)
        ]
        if not matches:
            if sample.expected_presence is ExpectedPresence.CANDIDATE:
                unmatched_sample_ids.append(sample.sample_id)
            continue
        if len(matches) > 1:
            ambiguous_sample_ids.append(sample.sample_id)
            continue
        occurrence = matches[0]
        predictions[sample.sample_id] = occurrence.disposition
        matched_occurrence_ids.add(occurrence.occurrence_id)

    unmatched_occurrence_ids = tuple(
        occurrence.occurrence_id
        for occurrence in scan_result.occurrences
        if occurrence.occurrence_id not in matched_occurrence_ids
    )
    metrics = _calculate_metrics(
        corpus,
        predictions,
        unmatched_sample_count=len(unmatched_sample_ids),
        unmatched_occurrence_count=len(unmatched_occurrence_ids),
        ambiguous_sample_count=len(ambiguous_sample_ids),
    )
    return EvaluationReport(
        metrics=metrics,
        predictions=predictions,
        unmatched_sample_ids=tuple(unmatched_sample_ids),
        unmatched_occurrence_ids=unmatched_occurrence_ids,
        ambiguous_sample_ids=tuple(ambiguous_sample_ids),
    )


def _occurrence_matches_sample(
    occurrence: ScanOccurrence, sample: GoldenSample
) -> bool:
    if occurrence.sink_symbol != sample.sink_symbol:
        return False
    if occurrence.text_slot != sample.text_slot:
        return False

    if sample.subject_kind is SubjectKind.EXPRESSION_ORIGIN:
        return ProvenanceRange(sample.path, sample.source_span) in occurrence.provenance
    return (
        occurrence.path == sample.path and occurrence.source_span == sample.source_span
    )


def _calculate_metrics(
    corpus: GoldenCorpus,
    predictions: dict[str, Decision],
    *,
    unmatched_sample_count: int,
    unmatched_occurrence_count: int,
    ambiguous_sample_count: int,
) -> EvaluationMetrics:
    predicted_confirmed = 0
    safe_confirmed = 0
    expected_candidates = 0
    discovered_candidates = 0
    expected_review = 0
    unsafe_promotions = 0
    expected_exclusions = 0
    leaked_exclusions = 0

    for sample in corpus.samples:
        prediction = predictions.get(sample.sample_id)
        predicted_as_candidate = prediction in {
            Decision.CONFIRMED,
            Decision.REVIEW_REQUIRED,
        }
        if prediction is Decision.CONFIRMED:
            predicted_confirmed += 1
            if sample.expected_disposition is Decision.CONFIRMED:
                safe_confirmed += 1

        if sample.expected_presence is ExpectedPresence.CANDIDATE:
            expected_candidates += 1
            if predicted_as_candidate:
                discovered_candidates += 1
        else:
            expected_exclusions += 1
            if predicted_as_candidate:
                leaked_exclusions += 1

        if sample.expected_disposition is Decision.REVIEW_REQUIRED:
            expected_review += 1
            if prediction is Decision.CONFIRMED:
                unsafe_promotions += 1

    return EvaluationMetrics(
        auto_confirm_precision=Rate(safe_confirmed, predicted_confirmed),
        candidate_recall=Rate(discovered_candidates, expected_candidates),
        unsafe_promotion_rate=Rate(unsafe_promotions, expected_review),
        exclusion_leakage=Rate(leaked_exclusions, expected_exclusions),
        unmatched_sample_count=unmatched_sample_count,
        unmatched_occurrence_count=unmatched_occurrence_count,
        ambiguous_sample_count=ambiguous_sample_count,
    )
