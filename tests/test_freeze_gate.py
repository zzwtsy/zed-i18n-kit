import json
from dataclasses import replace
from pathlib import PurePosixPath

from zed_i18n_kit.freeze_gate import (
    FreezePolicy,
    MetricPolicy,
    StratumPolicy,
    default_freeze_policy_resource,
    evaluate_freeze_gate,
    load_freeze_policy,
    serialize_freeze_gate_report,
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
from zed_i18n_kit.review import ReviewDecision, ReviewResult
from zed_i18n_kit.scan_profiles import WORKSPACE_BUILTIN_RULES
from zed_i18n_kit.scan_result import (
    CapabilityProbe,
    CapabilityProbeStatus,
    RuleEvidence,
    ScanMetadata,
    ScanOccurrence,
    ScanResult,
    SourceFileSnapshot,
)

COMMIT = "a" * 40
CORPUS_SHA = "b" * 64
CONFIG_HASH = "c" * 64
PATH = PurePosixPath("crates/demo/src/lib.rs")
SOURCE_HASH = "d" * 64
REVIEW_SET_ID = "phase-1c-test"


def test_packaged_policy_binds_current_corpus_and_workspace_rules() -> None:
    policy = load_freeze_policy(default_freeze_policy_resource())

    assert policy.rule_pack_version == "zed-builtin-v1"
    assert policy.tool_version == "0.1.0"
    assert policy.corpus_sample_sha256 == (
        "01eccfad9d4c38ff08856bca151e9ae9b2a6c99a505ef9d0c6f731960727e81f"
    )
    assert policy.rule_ids == tuple(
        sorted(rule.rule_id for rule in WORKSPACE_BUILTIN_RULES)
    )
    assert policy.metrics["auto_confirm_precision"].minimum_denominator == 100


def test_freeze_gate_passes_only_complete_eligible_evidence() -> None:
    corpus, scan_result, review_result, policy = _gate_fixture()

    report = evaluate_freeze_gate(corpus, scan_result, policy, review_result)
    payload = json.loads(serialize_freeze_gate_report(report, policy))

    assert report.passed
    assert report.freeze_status == "frozen"
    assert report.reviewed_sample_count == 3
    assert report.metrics.auto_confirm_precision.value == 1
    assert report.metrics.auto_confirm_coverage.value == 1
    assert report.metrics.candidate_recall.value == 1
    assert report.metrics.unsafe_promotion_rate.value == 0
    assert report.metrics.exclusion_leakage.value == 0
    assert payload["passed"] is True
    assert payload["failures"] == []
    assert payload["corpus_sample_sha256"] == CORPUS_SHA
    assert payload["rule_ids"] == list(policy.rule_ids)
    assert payload["capability_probes"] == [
        {
            "probe_id": "grammar",
            "requirement": "required_passed",
            "status": "passed",
        }
    ]
    assert payload["metrics"]["auto_confirm_precision"]["requirement"] == {
        "maximum": None,
        "minimum": 0.99,
        "minimum_denominator": 1,
    }


def test_freeze_gate_without_independent_result_fails_closed() -> None:
    corpus, scan_result, _, policy = _gate_fixture()

    report = evaluate_freeze_gate(corpus, scan_result, policy, None)

    assert not report.passed
    assert report.freeze_status == "reviewing"
    assert report.reviewed_sample_count == 0
    assert "independent review result is required" in report.failures
    assert any("denominator below minimum" in failure for failure in report.failures)


def test_freeze_gate_rejects_small_denominator_even_with_perfect_rate() -> None:
    corpus, scan_result, review_result, policy = _gate_fixture()
    metrics = dict(policy.metrics)
    metrics["auto_confirm_precision"] = MetricPolicy(2, 0.99, None)

    report = evaluate_freeze_gate(
        corpus, scan_result, replace(policy, metrics=metrics), review_result
    )

    assert not report.passed
    assert (
        "metric auto_confirm_precision denominator below minimum: 1 < 2"
        in report.failures
    )


def test_freeze_gate_blocks_unsafe_promotion_and_review_disagreement() -> None:
    corpus, scan_result, review_result, policy = _gate_fixture()
    promoted = replace(scan_result.occurrences[1], disposition=Decision.CONFIRMED)
    scan_result = replace(
        scan_result,
        occurrences=(scan_result.occurrences[0], promoted),
    )
    disputed_decision = replace(review_result.decisions[0], ownership=Ownership.MIXED)
    review_result = replace(
        review_result,
        decisions=(disputed_decision, *review_result.decisions[1:]),
    )

    report = evaluate_freeze_gate(corpus, scan_result, policy, review_result)

    assert not report.passed
    assert "disputed sample count exceeds maximum: 1 > 0" in report.failures
    assert any(
        failure.startswith("metric unsafe_promotion_rate exceeds maximum")
        for failure in report.failures
    )


def test_freeze_gate_reports_identity_and_capability_drift() -> None:
    corpus, scan_result, review_result, policy = _gate_fixture()
    failed_probe = replace(
        scan_result.metadata.capability_probes[0],
        status=CapabilityProbeStatus.FAILED,
    )
    metadata = replace(
        scan_result.metadata,
        tool_version="0.2.0",
        config_hash="e" * 64,
        capability_probes=(failed_probe,),
    )

    report = evaluate_freeze_gate(
        corpus, replace(scan_result, metadata=metadata), policy, review_result
    )

    assert not report.passed
    assert any("tool version mismatch" in failure for failure in report.failures)
    assert any("config hash mismatch" in failure for failure in report.failures)
    assert "required capability probe did not pass: grammar" in report.failures
    assert "unapproved capability probe failure: grammar" in report.failures


def _gate_fixture() -> tuple[GoldenCorpus, ScanResult, ReviewResult, FreezePolicy]:
    samples = (
        _sample("0001", SourceSpan(0, 4), Decision.CONFIRMED),
        _sample("0002", SourceSpan(10, 14), Decision.REVIEW_REQUIRED),
        _sample(
            "0003",
            SourceSpan(20, 24),
            Decision.EXCLUDED,
            presence=ExpectedPresence.NOT_CANDIDATE,
        ),
    )
    corpus = GoldenCorpus(
        CorpusManifest(
            schema_version=2,
            zed_commit=COMMIT,
            sample_file="samples.jsonl",
            sample_count=3,
            sample_sha256=CORPUS_SHA,
            source_files_sha256={PATH: SOURCE_HASH},
            minimum_counts=(),
        ),
        samples,
    )
    occurrences = (
        _occurrence("one", SourceSpan(0, 4), Decision.CONFIRMED),
        _occurrence("two", SourceSpan(10, 14), Decision.REVIEW_REQUIRED),
    )
    scan_result = ScanResult(
        1,
        ScanMetadata(
            zed_commit=COMMIT,
            tool_version="0.1.0",
            rule_pack_version="zed-builtin-v1",
            config_hash=CONFIG_HASH,
            scan_scope=(PATH,),
            source_files=(SourceFileSnapshot(PATH, SOURCE_HASH),),
            capability_probes=(
                CapabilityProbe(
                    "grammar", CapabilityProbeStatus.PASSED, "fixture passed"
                ),
            ),
        ),
        occurrences,
    )
    review_result = ReviewResult(
        review_set_id=REVIEW_SET_ID,
        zed_commit=COMMIT,
        corpus_sample_sha256=CORPUS_SHA,
        reviewer_id="reviewer",
        decisions=tuple(
            ReviewDecision(
                sample_id=sample.sample_id,
                expected_presence=sample.expected_presence,
                expected_disposition=sample.expected_disposition,
                ownership=sample.ownership,
                rationale="independent fixture assessment",
            )
            for sample in samples
        ),
    )
    metrics = {
        "auto_confirm_precision": MetricPolicy(1, 0.99, None),
        "auto_confirm_coverage": MetricPolicy(1, 0.5, None),
        "candidate_recall": MetricPolicy(1, 0.95, None),
        "unsafe_promotion_rate": MetricPolicy(1, None, 0),
        "exclusion_leakage": MetricPolicy(1, None, 0),
    }
    policy = FreezePolicy(
        review_set_id=REVIEW_SET_ID,
        zed_commit=COMMIT,
        corpus_sample_sha256=CORPUS_SHA,
        tool_version="0.1.0",
        rule_pack_version="zed-builtin-v1",
        config_hash=CONFIG_HASH,
        rule_ids=tuple(sorted(rule.rule_id for rule in WORKSPACE_BUILTIN_RULES)),
        required_passed_probes=("grammar",),
        allowed_failed_probes=(),
        minimum_reviewed_samples=3,
        strata=(
            StratumPolicy("expected_disposition", "confirmed", 1),
            StratumPolicy("expected_disposition", "review_required", 1),
            StratumPolicy("expected_disposition", "excluded", 1),
            StratumPolicy("rule_id", "ui-label-new-v1", 2),
        ),
        metrics=metrics,
        maximum_unmatched_samples=0,
        maximum_ambiguous_samples=0,
        maximum_disputed_samples=0,
    )
    return corpus, scan_result, review_result, policy


def _sample(
    suffix: str,
    span: SourceSpan,
    disposition: Decision,
    *,
    presence: ExpectedPresence = ExpectedPresence.CANDIDATE,
) -> GoldenSample:
    return GoldenSample(
        sample_id=f"zed-aaaaaaa-{suffix}",
        path=PATH,
        source_span=span,
        anchor="text",
        scope=SourceScope.PRODUCTION,
        subject_kind=SubjectKind.SINK_SLOT,
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
    occurrence_id: str, span: SourceSpan, disposition: Decision
) -> ScanOccurrence:
    return ScanOccurrence(
        occurrence_id=occurrence_id,
        path=PATH,
        primary_span=span,
        syntax_kind="string_literal",
        sink_symbol="ui::Label::new",
        text_slot="arg[0]",
        disposition=disposition,
        provenance=(),
        evidence=(RuleEvidence("ui-label-new-v1", "fixture"),),
    )
