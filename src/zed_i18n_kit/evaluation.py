from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .golden import (
    Decision,
    ExpectedPresence,
    GoldenCorpus,
    GoldenSample,
    ReviewState,
    SubjectKind,
)
from .scan_result import (
    ProvenanceRange,
    ScanOccurrence,
    ScanResult,
    validate_scan_result,
)


class EvaluationError(ValueError):
    """Raised when a valid scan result cannot be compared with a corpus."""


@dataclass(frozen=True, slots=True)
class EvaluationRate:
    numerator: int
    denominator: int

    @property
    def value(self) -> float | None:
        if self.denominator == 0:
            return None
        return self.numerator / self.denominator


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    auto_confirm_precision: EvaluationRate
    auto_confirm_coverage: EvaluationRate
    candidate_recall: EvaluationRate
    unsafe_promotion_rate: EvaluationRate
    exclusion_leakage: EvaluationRate
    unmatched_sample_count: int
    unlabeled_occurrence_count: int
    ambiguous_sample_count: int

    @property
    def alignment_failure_count(self) -> int:
        return self.unmatched_sample_count + self.ambiguous_sample_count


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    observational_metrics: EvaluationMetrics
    independently_reviewed_metrics: EvaluationMetrics | None
    sample_predictions: dict[str, Decision]
    unmatched_sample_ids: tuple[str, ...]
    ambiguous_sample_ids: tuple[str, ...]
    unlabeled_occurrence_ids: tuple[str, ...]
    evaluated_sample_count: int
    independently_reviewed_sample_count: int

    @property
    def has_independently_reviewed_metrics(self) -> bool:
        return self.independently_reviewed_metrics is not None


def serialize_evaluation_report(report: EvaluationReport) -> str:
    payload = {
        "ambiguous_sample_ids": list(report.ambiguous_sample_ids),
        "has_independently_reviewed_metrics": (
            report.has_independently_reviewed_metrics
        ),
        "independently_reviewed_metrics": (
            _serialize_metrics(report.independently_reviewed_metrics)
            if report.independently_reviewed_metrics is not None
            else None
        ),
        "evaluated_sample_count": report.evaluated_sample_count,
        "independently_reviewed_sample_count": (
            report.independently_reviewed_sample_count
        ),
        "observational_metrics": _serialize_metrics(report.observational_metrics),
        "sample_predictions": {
            sample_id: disposition.value
            for sample_id, disposition in sorted(report.sample_predictions.items())
        },
        "report_kind": "prototype-observational-v1",
        "unlabeled_occurrence_ids": list(report.unlabeled_occurrence_ids),
        "unmatched_sample_ids": list(report.unmatched_sample_ids),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_evaluation_report(path: Path, report: EvaluationReport) -> None:
    serialized = serialize_evaluation_report(report)
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
        raise EvaluationError(
            f"cannot write evaluation report {path}: {error}"
        ) from error


def evaluate_scan_result(
    corpus: GoldenCorpus, scan_result: ScanResult
) -> EvaluationReport:
    validate_scan_result(scan_result)
    _validate_snapshot_identity(corpus, scan_result)

    scope = set(scan_result.metadata.scan_scope)
    evaluated_samples = tuple(
        sample for sample in corpus.samples if sample.path in scope
    )
    sample_predictions: dict[str, Decision] = {}
    labeled_occurrence_ids: set[str] = set()
    unmatched_sample_ids: list[str] = []
    ambiguous_sample_ids: list[str] = []

    for sample in evaluated_samples:
        matches = tuple(
            occurrence
            for occurrence in scan_result.occurrences
            if _occurrence_matches_sample(occurrence, sample)
        )
        labeled_occurrence_ids.update(
            occurrence.occurrence_id for occurrence in matches
        )
        if not matches:
            if sample.expected_presence is ExpectedPresence.CANDIDATE:
                unmatched_sample_ids.append(sample.sample_id)
            continue
        if len(matches) > 1:
            ambiguous_sample_ids.append(sample.sample_id)
            continue
        sample_predictions[sample.sample_id] = matches[0].disposition

    unlabeled_occurrence_ids = tuple(
        sorted(
            occurrence.occurrence_id
            for occurrence in scan_result.occurrences
            if occurrence.occurrence_id not in labeled_occurrence_ids
        )
    )
    observational_metrics = _calculate_metrics(
        evaluated_samples,
        sample_predictions,
        unmatched_sample_count=len(unmatched_sample_ids),
        unlabeled_occurrence_count=len(unlabeled_occurrence_ids),
        ambiguous_sample_count=len(ambiguous_sample_ids),
    )
    independently_reviewed_samples = tuple(
        sample
        for sample in evaluated_samples
        if sample.review_state is ReviewState.INDEPENDENTLY_REVIEWED
    )
    independently_reviewed_ids = {
        sample.sample_id for sample in independently_reviewed_samples
    }
    independently_reviewed_metrics = (
        _calculate_metrics(
            independently_reviewed_samples,
            sample_predictions,
            unmatched_sample_count=sum(
                sample_id in independently_reviewed_ids
                for sample_id in unmatched_sample_ids
            ),
            unlabeled_occurrence_count=0,
            ambiguous_sample_count=sum(
                sample_id in independently_reviewed_ids
                for sample_id in ambiguous_sample_ids
            ),
        )
        if independently_reviewed_samples
        else None
    )
    return EvaluationReport(
        observational_metrics=observational_metrics,
        independently_reviewed_metrics=independently_reviewed_metrics,
        sample_predictions=sample_predictions,
        unmatched_sample_ids=tuple(unmatched_sample_ids),
        ambiguous_sample_ids=tuple(ambiguous_sample_ids),
        unlabeled_occurrence_ids=unlabeled_occurrence_ids,
        evaluated_sample_count=len(evaluated_samples),
        independently_reviewed_sample_count=len(independently_reviewed_samples),
    )


def _validate_snapshot_identity(corpus: GoldenCorpus, scan_result: ScanResult) -> None:
    metadata = scan_result.metadata
    if metadata.zed_commit != corpus.manifest.zed_commit:
        raise EvaluationError(
            f"scan-result Zed commit mismatch: expected "
            f"{corpus.manifest.zed_commit}, got {metadata.zed_commit}"
        )

    corpus_hashes = corpus.manifest.source_files_sha256
    for snapshot in metadata.source_files:
        expected_hash = corpus_hashes.get(snapshot.path)
        if expected_hash is None:
            raise EvaluationError(
                f"scan scope path is not covered by corpus snapshot: {snapshot.path}"
            )
        if snapshot.sha256 != expected_hash:
            raise EvaluationError(
                f"{snapshot.path}: scan-result source SHA-256 differs from corpus: "
                f"expected {expected_hash}, got {snapshot.sha256}"
            )


def _occurrence_matches_sample(
    occurrence: ScanOccurrence, sample: GoldenSample
) -> bool:
    if sample.subject_kind is SubjectKind.SINK_SLOT:
        return (
            occurrence.path == sample.path
            and occurrence.primary_span == sample.source_span
            and occurrence.sink_symbol == sample.sink_symbol
            and occurrence.text_slot == sample.text_slot
        )

    if sample.subject_kind is SubjectKind.EXPRESSION_ORIGIN:
        if (
            sample.sink_symbol is not None
            and occurrence.sink_symbol != sample.sink_symbol
        ):
            return False
        if sample.text_slot is not None and occurrence.text_slot != sample.text_slot:
            return False
        return ProvenanceRange(sample.path, sample.source_span) in occurrence.provenance

    if sample.sink_symbol is not None and occurrence.sink_symbol != sample.sink_symbol:
        return False
    if sample.text_slot is not None and occurrence.text_slot != sample.text_slot:
        return False
    return (
        occurrence.path == sample.path and occurrence.primary_span == sample.source_span
    )


def _calculate_metrics(
    samples: tuple[GoldenSample, ...],
    sample_predictions: dict[str, Decision],
    *,
    unmatched_sample_count: int,
    unlabeled_occurrence_count: int,
    ambiguous_sample_count: int,
) -> EvaluationMetrics:
    predicted_confirmed = 0
    safe_confirmed = 0
    coverage_eligible = 0
    coverage_confirmed = 0
    expected_candidates = 0
    discovered_candidates = 0
    expected_review = 0
    unsafe_promotions = 0
    expected_exclusions = 0
    leaked_exclusions = 0

    for sample in samples:
        prediction = sample_predictions.get(sample.sample_id)
        predicted_as_candidate = prediction in {
            Decision.CONFIRMED,
            Decision.REVIEW_REQUIRED,
        }
        if prediction is Decision.CONFIRMED:
            predicted_confirmed += 1
            if sample.expected_disposition is Decision.CONFIRMED:
                safe_confirmed += 1

        if (
            sample.review_state is ReviewState.INDEPENDENTLY_REVIEWED
            and sample.expected_presence is ExpectedPresence.CANDIDATE
            and sample.expected_disposition is Decision.CONFIRMED
        ):
            coverage_eligible += 1
            if prediction is Decision.CONFIRMED:
                coverage_confirmed += 1

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
        auto_confirm_precision=EvaluationRate(safe_confirmed, predicted_confirmed),
        auto_confirm_coverage=EvaluationRate(coverage_confirmed, coverage_eligible),
        candidate_recall=EvaluationRate(discovered_candidates, expected_candidates),
        unsafe_promotion_rate=EvaluationRate(unsafe_promotions, expected_review),
        exclusion_leakage=EvaluationRate(leaked_exclusions, expected_exclusions),
        unmatched_sample_count=unmatched_sample_count,
        unlabeled_occurrence_count=unlabeled_occurrence_count,
        ambiguous_sample_count=ambiguous_sample_count,
    )


def _serialize_rate(rate: EvaluationRate) -> dict[str, float | int | None]:
    return {
        "denominator": rate.denominator,
        "numerator": rate.numerator,
        "value": rate.value,
    }


def _serialize_metrics(metrics: EvaluationMetrics) -> dict[str, object]:
    return {
        "ambiguous_sample_count": metrics.ambiguous_sample_count,
        "auto_confirm_coverage": _serialize_rate(metrics.auto_confirm_coverage),
        "auto_confirm_precision": _serialize_rate(metrics.auto_confirm_precision),
        "alignment_failure_count": metrics.alignment_failure_count,
        "candidate_recall": _serialize_rate(metrics.candidate_recall),
        "exclusion_leakage": _serialize_rate(metrics.exclusion_leakage),
        "unlabeled_occurrence_count": metrics.unlabeled_occurrence_count,
        "unmatched_sample_count": metrics.unmatched_sample_count,
        "unsafe_promotion_rate": _serialize_rate(metrics.unsafe_promotion_rate),
    }
