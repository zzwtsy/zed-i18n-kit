import json
from dataclasses import replace
from pathlib import PurePosixPath

import pytest

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
from zed_i18n_kit.scan_profiles import (
    WORKSPACE_BUILTIN_RULES,
    WORKSPACE_STRUCTURAL_RULE_IDS,
)
from zed_i18n_kit.scan_result import (
    CapabilityProbe,
    CapabilityProbeStatus,
    RuleEvidence,
    ScanMetadata,
    ScanOccurrence,
    ScanResult,
    SourceFileSnapshot,
)
from zed_i18n_kit.unlabeled_audit import (
    AuditBundle,
    AuditDecision,
    AuditDisposition,
    AuditOutcome,
    AuditResult,
    AuditSample,
)

COMMIT = "a" * 40
CORPUS_SHA = "b" * 64
CONFIG_HASH = "c" * 64
PATH = PurePosixPath("crates/demo/src/lib.rs")
SOURCE_HASH = "d" * 64
REVIEW_SET_ID = "phase-1c-test"
AUDIT_SET_ID = "phase-1c-audit"


def test_packaged_policy_binds_current_corpus_and_workspace_rules() -> None:
    policy = load_freeze_policy(default_freeze_policy_resource())

    assert policy.rule_pack_version == "zed-builtin-v1"
    assert policy.tool_version == "0.1.0"
    assert policy.corpus_sample_sha256 == (
        "41525aceed25c0cf857d8e91335b5af2552f357c2d638d1103d1fb4d6c015bef"
    )
    assert policy.config_hash == (
        "96a7bfaea6e39532b439b3373ccffc7c36d8b8ff81914797a251167cc71159bd"
    )
    assert policy.rule_ids == _runtime_rule_ids()
    assert policy.metrics["auto_confirm_precision"].minimum_denominator == 100
    assert policy.review_set_id == "phase-1c-corpus-final-r3"
    assert policy.audit_set_id == "phase-1c-unlabeled-final-r3"
    assert policy.minimum_audited_samples == 100
    assert policy.maximum_corpus_gaps == 0
    assert policy.maximum_indeterminate == 0
    assert policy.maximum_unsafe_promotions == 0
    assert policy.maximum_candidate_exclusion_mismatches == 0


def test_freeze_gate_passes_only_complete_eligible_evidence() -> None:
    corpus, scan_result, review_result, policy = _gate_fixture()
    audit_bundle, audit_result = _audit_evidence(scan_result)

    report = evaluate_freeze_gate(
        corpus, scan_result, policy, review_result, audit_bundle, audit_result
    )
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
    assert payload["audit_set_id"] == AUDIT_SET_ID
    assert payload["audit_sample_count"] == 2
    expected_disposition_counts = {
        disposition.value: (2 if disposition is AuditDisposition.CONFIRMED else 0)
        for disposition in AuditDisposition
    }
    assert payload["audit_expected_disposition_counts"] == expected_disposition_counts
    assert payload["audit"]["expected_disposition_counts"] == (
        expected_disposition_counts
    )
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
    assert report.audit_expected_disposition_counts == {
        disposition: 0 for disposition in AuditDisposition
    }


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
    audit_bundle, audit_result = _audit_evidence(scan_result)
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

    report = evaluate_freeze_gate(
        corpus, scan_result, policy, review_result, audit_bundle, audit_result
    )

    assert not report.passed
    assert "disputed sample count exceeds maximum: 1 > 0" in report.failures
    assert any(
        failure.startswith("metric unsafe_promotion_rate exceeds maximum")
        for failure in report.failures
    )


def test_freeze_gate_blocks_audit_safety_mismatches_and_reports_counts() -> None:
    corpus, scan_result, review_result, policy = _gate_fixture()
    audit_bundle, audit_result = _audit_evidence(scan_result)
    unsafe = replace(
        audit_result.decisions[0],
        expected_disposition=AuditDisposition.REVIEW_REQUIRED,
    )
    mismatched = replace(
        audit_result.decisions[1],
        expected_disposition=AuditDisposition.EXCLUDED,
    )
    audit_result = replace(
        audit_result,
        decisions=(unsafe, mismatched),
    )

    report = evaluate_freeze_gate(
        corpus, scan_result, policy, review_result, audit_bundle, audit_result
    )
    payload = json.loads(serialize_freeze_gate_report(report, policy))

    assert not report.passed
    assert report.audit_sample_count == 2
    assert report.audit_decision_count == 2
    assert report.audit_expected_disposition_counts == {
        AuditDisposition.CONFIRMED: 0,
        AuditDisposition.REVIEW_REQUIRED: 1,
        AuditDisposition.EXCLUDED: 1,
        AuditDisposition.INDETERMINATE: 0,
    }
    assert report.audit_outcome_counts[AuditOutcome.UNSAFE_PROMOTION] == 1
    assert report.audit_outcome_counts[AuditOutcome.CANDIDATE_EXCLUSION_MISMATCH] == 1
    assert report.failures.count("unsafe promotion count exceeds maximum: 1 > 0") == 1
    assert (
        report.failures.count(
            "candidate/excluded mismatch count exceeds maximum: 1 > 0"
        )
        == 1
    )
    assert "unsafe promotions: one" not in report.failures
    assert "candidate/excluded mismatches: two" not in report.failures
    assert payload["failure_reasons"] == payload["failures"]


def test_freeze_gate_requires_audit_evidence_and_sample_floor() -> None:
    corpus, scan_result, review_result, policy = _gate_fixture()
    policy = replace(policy, minimum_audited_samples=3)

    report = evaluate_freeze_gate(corpus, scan_result, policy, review_result)

    assert not report.passed
    assert report.audit_sample_count == 0
    assert (
        "independent unlabeled audit bundle and result are required" in report.failures
    )
    assert "audited sample count below minimum: 0 < 3" in report.failures
    assert report.failures.count("audited sample count below minimum: 0 < 3") == 1


def test_freeze_gate_reports_audit_sample_floor_once_with_evidence() -> None:
    corpus, scan_result, review_result, policy = _gate_fixture()
    audit_bundle, audit_result = _audit_evidence(scan_result)
    policy = replace(policy, minimum_audited_samples=3)

    report = evaluate_freeze_gate(
        corpus, scan_result, policy, review_result, audit_bundle, audit_result
    )

    failure = "audited sample count below minimum: 2 < 3"
    assert report.failures.count(failure) == 1


@pytest.mark.parametrize(
    (
        "policy_field",
        "decision_index",
        "expected_disposition",
        "corpus_gap",
        "outcome",
        "strict_failure",
    ),
    [
        (
            "maximum_corpus_gaps",
            0,
            AuditDisposition.CONFIRMED,
            True,
            AuditOutcome.CORPUS_GAP,
            "corpus gaps: one",
        ),
        (
            "maximum_indeterminate",
            0,
            AuditDisposition.INDETERMINATE,
            False,
            AuditOutcome.INDETERMINATE,
            "indeterminate audit decisions: one",
        ),
        (
            "maximum_unsafe_promotions",
            0,
            AuditDisposition.REVIEW_REQUIRED,
            False,
            AuditOutcome.UNSAFE_PROMOTION,
            "unsafe promotions: one",
        ),
        (
            "maximum_candidate_exclusion_mismatches",
            1,
            AuditDisposition.EXCLUDED,
            False,
            AuditOutcome.CANDIDATE_EXCLUSION_MISMATCH,
            "candidate/excluded mismatches: two",
        ),
    ],
)
@pytest.mark.parametrize("maximum", [0, 1])
def test_freeze_gate_applies_audit_semantic_limits(
    policy_field: str,
    decision_index: int,
    expected_disposition: AuditDisposition,
    corpus_gap: bool,
    outcome: AuditOutcome,
    strict_failure: str,
    maximum: int,
) -> None:
    corpus, scan_result, review_result, policy = _gate_fixture()
    audit_bundle, audit_result = _audit_evidence(scan_result)
    decisions = list(audit_result.decisions)
    decisions[decision_index] = replace(
        decisions[decision_index],
        expected_disposition=expected_disposition,
        corpus_gap=corpus_gap,
    )
    audit_result = replace(audit_result, decisions=tuple(decisions))
    policy = replace(policy, **{policy_field: maximum})

    report = evaluate_freeze_gate(
        corpus, scan_result, policy, review_result, audit_bundle, audit_result
    )

    assert report.audit_outcome_counts[outcome] == 1
    threshold_name = {
        "maximum_corpus_gaps": "corpus gap",
        "maximum_indeterminate": "indeterminate audit",
        "maximum_unsafe_promotions": "unsafe promotion",
        "maximum_candidate_exclusion_mismatches": "candidate/excluded mismatch",
    }[policy_field]
    threshold_failure = f"{threshold_name} count exceeds maximum: 1 > {maximum}"
    if maximum == 0:
        assert report.failures.count(threshold_failure) == 1
        assert strict_failure not in report.failures
    else:
        assert threshold_failure not in report.failures
        assert strict_failure not in report.failures
        assert report.passed


@pytest.mark.parametrize(
    ("target", "field", "value", "failure"),
    [
        (
            "bundle",
            "audit_set_id",
            "phase-1c-drift",
            "audit bundle audit set mismatch",
        ),
        (
            "result",
            "audit_set_id",
            "phase-1c-drift",
            "audit result audit set mismatch",
        ),
        ("bundle", "tool_version", "0.2.0", "audit bundle tool version mismatch"),
        ("result", "tool_version", "0.2.0", "audit result tool version mismatch"),
        (
            "bundle",
            "rule_pack_version",
            "zed-builtin-v2",
            "audit bundle rule pack version mismatch",
        ),
        (
            "result",
            "rule_pack_version",
            "zed-builtin-v2",
            "audit result rule pack version mismatch",
        ),
        ("scan", "tool_version", "0.2.0", "scan-result tool version mismatch"),
        (
            "scan",
            "rule_pack_version",
            "zed-builtin-v2",
            "scan-result rule pack version mismatch",
        ),
        (
            "policy",
            "audit_set_id",
            "phase-1c-drift",
            "audit bundle audit set mismatch",
        ),
        ("policy", "tool_version", "0.2.0", "scan-result tool version mismatch"),
        (
            "policy",
            "rule_pack_version",
            "zed-builtin-v2",
            "scan-result rule pack mismatch",
        ),
    ],
)
def test_freeze_gate_rejects_identity_drift_at_each_boundary(
    target: str,
    field: str,
    value: str,
    failure: str,
) -> None:
    corpus, scan_result, review_result, policy = _gate_fixture()
    audit_bundle, audit_result = _audit_evidence(scan_result)

    if target == "bundle":
        audit_bundle = replace(audit_bundle, **{field: value})
    elif target == "result":
        audit_result = replace(audit_result, **{field: value})
    elif target == "scan":
        metadata = replace(scan_result.metadata, **{field: value})
        scan_result = replace(scan_result, metadata=metadata)
    else:
        policy = replace(policy, **{field: value})

    report = evaluate_freeze_gate(
        corpus, scan_result, policy, review_result, audit_bundle, audit_result
    )

    assert not report.passed
    assert any(reason.startswith(failure) for reason in report.failures)


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
        rule_ids=_runtime_rule_ids(),
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
        audit_set_id=AUDIT_SET_ID,
        minimum_audited_samples=2,
        maximum_corpus_gaps=0,
        maximum_indeterminate=0,
        maximum_unsafe_promotions=0,
        maximum_candidate_exclusion_mismatches=0,
    )
    return corpus, scan_result, review_result, policy


def _runtime_rule_ids() -> tuple[str, ...]:
    return tuple(
        sorted(
            (
                *(rule.rule_id for rule in WORKSPACE_BUILTIN_RULES),
                *WORKSPACE_STRUCTURAL_RULE_IDS,
            )
        )
    )


def _audit_evidence(
    scan_result: ScanResult,
) -> tuple[AuditBundle, AuditResult]:
    samples = tuple(
        AuditSample(
            occurrence_id=occurrence.occurrence_id,
            path=occurrence.path,
            primary_span=occurrence.primary_span,
            context_span=occurrence.primary_span,
            source_context="text",
            syntax_kind=occurrence.syntax_kind,
        )
        for occurrence in scan_result.occurrences
    )
    bundle = AuditBundle(
        audit_set_id=AUDIT_SET_ID,
        zed_commit=scan_result.metadata.zed_commit,
        corpus_sample_sha256=CORPUS_SHA,
        scan_config_hash=scan_result.metadata.config_hash,
        tool_version=scan_result.metadata.tool_version,
        rule_pack_version=scan_result.metadata.rule_pack_version,
        sample_size_requested=len(samples),
        occurrences=samples,
    )
    decisions = tuple(
        AuditDecision(
            occurrence_id=occurrence.occurrence_id,
            expected_disposition=AuditDisposition.CONFIRMED,
            corpus_gap=False,
            rationale="independent fixture assessment",
        )
        for occurrence in scan_result.occurrences
    )
    result = AuditResult(
        audit_set_id=AUDIT_SET_ID,
        zed_commit=scan_result.metadata.zed_commit,
        corpus_sample_sha256=CORPUS_SHA,
        scan_config_hash=scan_result.metadata.config_hash,
        tool_version=scan_result.metadata.tool_version,
        rule_pack_version=scan_result.metadata.rule_pack_version,
        reviewer_id="audit-reviewer",
        decisions=decisions,
    )
    return bundle, result


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
