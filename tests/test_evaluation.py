from pathlib import PurePosixPath

import pytest

from zed_i18n_kit.evaluation import (
    ProvenanceRange,
    ScanOccurrence,
    ScanResult,
    evaluate_scan_result,
)
from zed_i18n_kit.golden import (
    CorpusManifest,
    Decision,
    ExpectedPresence,
    Feature,
    GoldenCorpus,
    GoldenCorpusError,
    GoldenSample,
    Ownership,
    ReviewState,
    SinkKind,
    SourceScope,
    SourceSpan,
    SubjectKind,
)

COMMIT = "a" * 40
PATH = PurePosixPath("crates/demo/src/lib.rs")


def test_metrics_match_hand_calculated_confusion_matrix() -> None:
    samples = (
        _sample("0001", SourceSpan(0, 4), Decision.CONFIRMED),
        _sample(
            "0002",
            SourceSpan(10, 14),
            Decision.REVIEW_REQUIRED,
            subject_kind=SubjectKind.EXPRESSION_ORIGIN,
        ),
        _sample(
            "0003",
            SourceSpan(20, 24),
            Decision.EXCLUDED,
            presence=ExpectedPresence.NOT_CANDIDATE,
        ),
        _sample("0004", SourceSpan(30, 34), Decision.REVIEW_REQUIRED),
    )
    occurrences = (
        _occurrence("one", SourceSpan(0, 4), Decision.CONFIRMED),
        _occurrence(
            "two",
            SourceSpan(100, 104),
            Decision.CONFIRMED,
            provenance=(ProvenanceRange(PATH, SourceSpan(10, 14)),),
        ),
        _occurrence("three", SourceSpan(20, 24), Decision.REVIEW_REQUIRED),
        _occurrence("unexpected", SourceSpan(40, 44), Decision.CONFIRMED),
    )

    report = evaluate_scan_result(_corpus(samples), _scan_result(occurrences))

    assert report.metrics.auto_confirm_precision.numerator == 1
    assert report.metrics.auto_confirm_precision.denominator == 2
    assert report.metrics.auto_confirm_precision.value == pytest.approx(0.5)
    assert report.metrics.candidate_recall.value == pytest.approx(2 / 3)
    assert report.metrics.unsafe_promotion_rate.value == pytest.approx(0.5)
    assert report.metrics.exclusion_leakage.value == pytest.approx(1.0)
    assert report.metrics.unmatched_sample_count == 1
    assert report.metrics.unmatched_occurrence_count == 1
    assert report.metrics.ambiguous_sample_count == 0
    assert report.metrics.unmatched_count == 2


def test_expression_origin_requires_exact_provenance() -> None:
    sample = _sample(
        "0001",
        SourceSpan(10, 14),
        Decision.REVIEW_REQUIRED,
        subject_kind=SubjectKind.EXPRESSION_ORIGIN,
    )
    occurrence = _occurrence("one", SourceSpan(10, 14), Decision.REVIEW_REQUIRED)

    report = evaluate_scan_result(_corpus((sample,)), _scan_result((occurrence,)))

    assert report.predictions == {}
    assert report.unmatched_sample_ids == (sample.sample_id,)
    assert report.unmatched_occurrence_ids == (occurrence.occurrence_id,)


def test_absent_not_candidate_is_a_correct_exclusion_not_unmatched() -> None:
    sample = _sample(
        "0001",
        SourceSpan(0, 4),
        Decision.EXCLUDED,
        presence=ExpectedPresence.NOT_CANDIDATE,
    )

    report = evaluate_scan_result(_corpus((sample,)), _scan_result(()))

    assert report.unmatched_sample_ids == ()
    assert report.metrics.exclusion_leakage.numerator == 0
    assert report.metrics.exclusion_leakage.denominator == 1
    assert report.metrics.unmatched_count == 0


def test_duplicate_exact_matches_are_reported_as_ambiguous() -> None:
    sample = _sample("0001", SourceSpan(0, 4), Decision.CONFIRMED)
    occurrences = (
        _occurrence("one", SourceSpan(0, 4), Decision.CONFIRMED),
        _occurrence("two", SourceSpan(0, 4), Decision.CONFIRMED),
    )

    report = evaluate_scan_result(_corpus((sample,)), _scan_result(occurrences))

    assert report.ambiguous_sample_ids == (sample.sample_id,)
    assert report.metrics.ambiguous_sample_count == 1
    assert report.metrics.unmatched_occurrence_count == 2


def test_evaluator_rejects_wrong_commit() -> None:
    corpus = _corpus((_sample("0001", SourceSpan(0, 4), Decision.CONFIRMED),))

    with pytest.raises(GoldenCorpusError, match="commit mismatch"):
        evaluate_scan_result(
            corpus,
            ScanResult(schema_version=1, zed_commit="b" * 40, occurrences=()),
        )


def _sample(
    suffix: str,
    span: SourceSpan,
    disposition: Decision,
    *,
    presence: ExpectedPresence = ExpectedPresence.CANDIDATE,
    subject_kind: SubjectKind = SubjectKind.SINK_SLOT,
) -> GoldenSample:
    return GoldenSample(
        sample_id=f"zed-aaaaaaa-{suffix}",
        path=PATH,
        source_span=span,
        anchor="text",
        scope=SourceScope.PRODUCTION,
        subject_kind=subject_kind,
        sink_symbol="ui::Label::new",
        text_slot="arg[0]",
        sink_kind=SinkKind.VISIBLE_TEXT,
        features=frozenset({Feature.DIRECT_LITERAL}),
        ownership=Ownership.PRODUCT,
        expected_presence=presence,
        expected_disposition=disposition,
        review_state=ReviewState.SINGLE_REVIEW,
        rationale="fixture",
    )


def _occurrence(
    occurrence_id: str,
    span: SourceSpan,
    disposition: Decision,
    *,
    provenance: tuple[ProvenanceRange, ...] = (),
) -> ScanOccurrence:
    return ScanOccurrence(
        occurrence_id=occurrence_id,
        path=PATH,
        source_span=span,
        sink_symbol="ui::Label::new",
        text_slot="arg[0]",
        disposition=disposition,
        provenance=provenance,
    )


def _corpus(samples: tuple[GoldenSample, ...]) -> GoldenCorpus:
    return GoldenCorpus(
        manifest=CorpusManifest(
            schema_version=2,
            zed_commit=COMMIT,
            sample_file="samples.jsonl",
            sample_count=len(samples),
            sample_sha256="0" * 64,
            source_files_sha256={},
            minimum_counts=(),
        ),
        samples=samples,
    )


def _scan_result(occurrences: tuple[ScanOccurrence, ...]) -> ScanResult:
    return ScanResult(schema_version=1, zed_commit=COMMIT, occurrences=occurrences)
