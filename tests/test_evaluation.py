import json
from dataclasses import replace
from pathlib import PurePosixPath

import pytest

from zed_i18n_kit.evaluation import (
    EvaluationError,
    EvaluationRate,
    evaluate_scan_result,
    serialize_evaluation_report,
)
from zed_i18n_kit.golden import (
    CorpusManifest,
    Decision,
    ExpectedPresence,
    Feature,
    GoldenCorpus,
    GoldenSample,
    Ownership,
    ReviewState,
    SinkKind,
    SourceScope,
    SourceSpan,
    SubjectKind,
)
from zed_i18n_kit.scan_result import (
    CapabilityProbe,
    CapabilityProbeStatus,
    ProvenanceRange,
    RuleEvidence,
    ScanMetadata,
    ScanOccurrence,
    ScanResult,
    SourceFileSnapshot,
)

COMMIT = "a" * 40
PATH = PurePosixPath("crates/demo/src/lib.rs")
OUTSIDE_PATH = PurePosixPath("crates/outside/src/lib.rs")
SOURCE_HASH = "b" * 64


def test_metrics_match_hand_calculated_confusion_matrix() -> None:
    samples = (
        _sample(
            "0001",
            SourceSpan(0, 4),
            Decision.CONFIRMED,
            review_state=ReviewState.INDEPENDENTLY_REVIEWED,
        ),
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

    metrics = report.observational_metrics
    assert metrics.auto_confirm_precision.value == pytest.approx(0.5)
    assert metrics.auto_confirm_coverage.value == pytest.approx(1.0)
    assert metrics.candidate_recall.value == pytest.approx(2 / 3)
    assert metrics.unsafe_promotion_rate.value == pytest.approx(0.5)
    assert metrics.exclusion_leakage.value == pytest.approx(1.0)
    assert metrics.unmatched_sample_count == 1
    assert metrics.unlabeled_occurrence_count == 1
    assert metrics.ambiguous_sample_count == 0
    assert metrics.alignment_failure_count == 1
    assert report.unlabeled_occurrence_ids == ("unexpected",)
    assert report.has_independently_reviewed_metrics
    assert report.independently_reviewed_metrics is not None
    assert report.independently_reviewed_metrics.auto_confirm_precision == (
        EvaluationRate(1, 1)
    )
    assert report.independently_reviewed_metrics.auto_confirm_coverage == (
        EvaluationRate(1, 1)
    )


def test_expression_origin_null_constraints_match_non_null_occurrence() -> None:
    sample = replace(
        _sample(
            "0001",
            SourceSpan(10, 14),
            Decision.REVIEW_REQUIRED,
            subject_kind=SubjectKind.EXPRESSION_ORIGIN,
        ),
        sink_symbol=None,
        text_slot=None,
    )
    occurrence = _occurrence(
        "one",
        SourceSpan(100, 104),
        Decision.REVIEW_REQUIRED,
        provenance=(ProvenanceRange(PATH, SourceSpan(10, 14)),),
    )

    report = evaluate_scan_result(_corpus((sample,)), _scan_result((occurrence,)))

    assert report.sample_predictions == {sample.sample_id: Decision.REVIEW_REQUIRED}
    assert report.unmatched_sample_ids == ()
    assert report.unlabeled_occurrence_ids == ()


def test_expression_origin_non_null_constraints_still_filter() -> None:
    sample = _sample(
        "0001",
        SourceSpan(10, 14),
        Decision.REVIEW_REQUIRED,
        subject_kind=SubjectKind.EXPRESSION_ORIGIN,
    )
    occurrence = replace(
        _occurrence(
            "one",
            SourceSpan(100, 104),
            Decision.REVIEW_REQUIRED,
            provenance=(ProvenanceRange(PATH, SourceSpan(10, 14)),),
        ),
        sink_symbol="gpui::Window::prompt",
        text_slot="arg[1]",
    )

    report = evaluate_scan_result(_corpus((sample,)), _scan_result((occurrence,)))

    assert report.sample_predictions == {}
    assert report.unmatched_sample_ids == (sample.sample_id,)
    assert report.unlabeled_occurrence_ids == (occurrence.occurrence_id,)


def test_absent_not_candidate_is_a_correct_exclusion_not_unmatched() -> None:
    sample = _sample(
        "0001",
        SourceSpan(0, 4),
        Decision.EXCLUDED,
        presence=ExpectedPresence.NOT_CANDIDATE,
    )

    report = evaluate_scan_result(_corpus((sample,)), _scan_result(()))

    assert report.unmatched_sample_ids == ()
    assert report.observational_metrics.exclusion_leakage == EvaluationRate(0, 1)
    assert report.observational_metrics.alignment_failure_count == 0


def test_duplicate_matches_are_ambiguous_but_not_unlabeled() -> None:
    sample = _sample("0001", SourceSpan(0, 4), Decision.CONFIRMED)
    occurrences = (
        _occurrence("one", SourceSpan(0, 4), Decision.CONFIRMED),
        _occurrence("two", SourceSpan(0, 4), Decision.CONFIRMED),
    )

    report = evaluate_scan_result(_corpus((sample,)), _scan_result(occurrences))

    assert report.ambiguous_sample_ids == (sample.sample_id,)
    assert report.observational_metrics.ambiguous_sample_count == 1
    assert report.unlabeled_occurrence_ids == ()


def test_scope_limits_samples_and_metric_denominators() -> None:
    in_scope = _sample("0001", SourceSpan(0, 4), Decision.CONFIRMED)
    out_of_scope = replace(
        _sample("0002", SourceSpan(10, 14), Decision.CONFIRMED), path=OUTSIDE_PATH
    )
    corpus = _corpus((in_scope, out_of_scope), include_outside=True)

    report = evaluate_scan_result(corpus, _scan_result(()))

    assert report.evaluated_sample_count == 1
    assert report.observational_metrics.candidate_recall.denominator == 1
    assert report.unmatched_sample_ids == (in_scope.sample_id,)


def test_scan_scope_may_include_sources_outside_corpus_manifest() -> None:
    corpus = _corpus((_sample("0001", SourceSpan(0, 4), Decision.CONFIRMED),))
    base_result = _scan_result(())
    workspace_metadata = replace(
        base_result.metadata,
        scan_scope=(PATH, OUTSIDE_PATH),
        source_files=(
            SourceFileSnapshot(PATH, SOURCE_HASH),
            SourceFileSnapshot(OUTSIDE_PATH, "e" * 64),
        ),
    )

    report = evaluate_scan_result(corpus, ScanResult(1, workspace_metadata, ()))

    assert report.evaluated_sample_count == 1
    assert report.unmatched_sample_ids == (corpus.samples[0].sample_id,)


def test_single_review_has_undefined_coverage_and_no_reviewed_metrics() -> None:
    sample = _sample("0001", SourceSpan(0, 4), Decision.CONFIRMED)
    occurrence = _occurrence("one", SourceSpan(0, 4), Decision.REVIEW_REQUIRED)

    report = evaluate_scan_result(_corpus((sample,)), _scan_result((occurrence,)))

    assert report.observational_metrics.auto_confirm_coverage.denominator == 0
    assert report.observational_metrics.auto_confirm_coverage.value is None
    assert not report.has_independently_reviewed_metrics
    assert report.independently_reviewed_metrics is None


def test_serialized_report_uses_observational_names_without_gate_claims() -> None:
    report = evaluate_scan_result(_corpus(()), _scan_result(()))

    payload = json.loads(serialize_evaluation_report(report))

    assert payload["report_kind"] == "prototype-observational-v1"
    assert payload["has_independently_reviewed_metrics"] is False
    assert payload["independently_reviewed_metrics"] is None
    assert payload["observational_metrics"]["alignment_failure_count"] == 0
    assert payload["sample_predictions"] == {}
    assert "blocking_metrics" not in payload
    assert "metrics" not in payload
    assert "predictions" not in payload


def test_evaluator_rejects_commit_and_source_snapshot_mismatch() -> None:
    corpus = _corpus((_sample("0001", SourceSpan(0, 4), Decision.CONFIRMED),))
    wrong_commit = replace(_scan_result(()).metadata, zed_commit="c" * 40)
    with pytest.raises(EvaluationError, match="commit mismatch"):
        evaluate_scan_result(corpus, ScanResult(1, wrong_commit, ()))

    wrong_hash = replace(
        _scan_result(()).metadata,
        source_files=(SourceFileSnapshot(PATH, "d" * 64),),
    )
    with pytest.raises(EvaluationError, match="differs from corpus"):
        evaluate_scan_result(corpus, ScanResult(1, wrong_hash, ()))


def _sample(
    suffix: str,
    span: SourceSpan,
    disposition: Decision,
    *,
    presence: ExpectedPresence = ExpectedPresence.CANDIDATE,
    subject_kind: SubjectKind = SubjectKind.SINK_SLOT,
    review_state: ReviewState = ReviewState.SINGLE_REVIEW,
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
        review_state=review_state,
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
        primary_span=span,
        syntax_kind="string_literal",
        sink_symbol="ui::Label::new",
        text_slot="arg[0]",
        disposition=disposition,
        provenance=provenance,
        evidence=(RuleEvidence("fixture", "fixture evidence"),),
    )


def _corpus(
    samples: tuple[GoldenSample, ...], *, include_outside: bool = False
) -> GoldenCorpus:
    source_hashes = {PATH: SOURCE_HASH}
    if include_outside:
        source_hashes[OUTSIDE_PATH] = "e" * 64
    return GoldenCorpus(
        manifest=CorpusManifest(
            schema_version=2,
            zed_commit=COMMIT,
            sample_file="samples.jsonl",
            sample_count=len(samples),
            sample_sha256="0" * 64,
            source_files_sha256=source_hashes,
            minimum_counts=(),
        ),
        samples=samples,
    )


def _scan_result(occurrences: tuple[ScanOccurrence, ...]) -> ScanResult:
    metadata = ScanMetadata(
        zed_commit=COMMIT,
        tool_version="0.1.0",
        rule_pack_version="gpui-prototype-v1",
        config_hash="f" * 64,
        scan_scope=(PATH,),
        source_files=(SourceFileSnapshot(PATH, SOURCE_HASH),),
        capability_probes=(
            CapabilityProbe(
                "rust-grammar", CapabilityProbeStatus.PASSED, "grammar loaded"
            ),
        ),
    )
    return ScanResult(schema_version=1, metadata=metadata, occurrences=occurrences)
