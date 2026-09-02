from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, replace
from importlib.resources import files
from importlib.resources.abc import Traversable

from .evaluation import (
    EvaluationError,
    EvaluationMetrics,
    EvaluationRate,
    evaluate_scan_result,
    occurrence_matches_sample,
)
from .golden import GoldenCorpus, GoldenSample, ReviewState
from .review import ReviewError, ReviewResult, reconcile_review_result
from .scan_profiles import WORKSPACE_BUILTIN_RULES, WORKSPACE_STRUCTURAL_RULE_IDS
from .scan_result import CapabilityProbeStatus, ScanResult, validate_scan_result
from .schema_resources import SchemaResource
from .unlabeled_audit import (
    AuditBundle,
    AuditDisposition,
    AuditOutcome,
    AuditResult,
    UnlabeledAuditError,
    reconcile_audit_result,
)

FREEZE_POLICY_SCHEMA_VERSION = 1
DEFAULT_FREEZE_POLICY_NAME = "zed-builtin-v1.freeze-policy.json"
METRIC_NAMES = frozenset(
    {
        "auto_confirm_precision",
        "auto_confirm_coverage",
        "candidate_recall",
        "unsafe_promotion_rate",
        "exclusion_leakage",
    }
)
POLICY_FIELDS = frozenset(
    {
        "schema_version",
        "review_set_id",
        "audit_set_id",
        "zed_commit",
        "corpus_sample_sha256",
        "tool_version",
        "rule_pack_version",
        "config_hash",
        "rule_ids",
        "required_passed_probes",
        "allowed_failed_probes",
        "minimum_reviewed_samples",
        "minimum_audited_samples",
        "strata",
        "metrics",
        "maximum_unmatched_samples",
        "maximum_ambiguous_samples",
        "maximum_disputed_samples",
        "maximum_corpus_gaps",
        "maximum_indeterminate",
        "maximum_unsafe_promotions",
        "maximum_candidate_exclusion_mismatches",
    }
)
METRIC_POLICY_FIELDS = frozenset({"minimum_denominator", "minimum", "maximum"})
STRATUM_FIELDS = frozenset({"dimension", "value", "minimum"})
STRATUM_DIMENSIONS = frozenset(
    {"expected_disposition", "ownership", "subject_kind", "feature", "rule_id"}
)


class FreezeGateError(ValueError):
    """Raised when a freeze policy or gate input is structurally invalid."""


@dataclass(frozen=True, slots=True)
class MetricPolicy:
    minimum_denominator: int
    minimum: float | None
    maximum: float | None


@dataclass(frozen=True, slots=True)
class StratumPolicy:
    dimension: str
    value: str
    minimum: int


@dataclass(frozen=True, slots=True)
class FreezePolicy:
    review_set_id: str
    audit_set_id: str
    zed_commit: str
    corpus_sample_sha256: str
    tool_version: str
    rule_pack_version: str
    config_hash: str
    rule_ids: tuple[str, ...]
    required_passed_probes: tuple[str, ...]
    allowed_failed_probes: tuple[str, ...]
    minimum_reviewed_samples: int
    minimum_audited_samples: int
    strata: tuple[StratumPolicy, ...]
    metrics: Mapping[str, MetricPolicy]
    maximum_unmatched_samples: int
    maximum_ambiguous_samples: int
    maximum_disputed_samples: int
    maximum_corpus_gaps: int
    maximum_indeterminate: int
    maximum_unsafe_promotions: int
    maximum_candidate_exclusion_mismatches: int


@dataclass(frozen=True, slots=True)
class FreezeGateReport:
    review_set_id: str
    rule_pack_version: str
    tool_version: str
    zed_commit: str
    corpus_sample_sha256: str
    config_hash: str
    capability_probe_statuses: Mapping[str, str]
    reviewer_id: str | None
    reviewed_sample_count: int
    missing_sample_count: int
    disputed_sample_count: int
    stratum_counts: Mapping[tuple[str, str], int]
    metrics: EvaluationMetrics
    failures: tuple[str, ...]
    audit_set_id: str
    audit_reviewer_id: str | None
    audit_sample_count: int
    audit_decision_count: int
    missing_audit_sample_count: int
    audit_expected_disposition_counts: Mapping[AuditDisposition, int]
    audit_outcome_counts: Mapping[AuditOutcome, int]

    @property
    def audited_sample_count(self) -> int:
        return self.audit_decision_count

    @property
    def audit_missing_sample_count(self) -> int:
        return self.missing_audit_sample_count

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def freeze_status(self) -> str:
        return "frozen" if self.passed else "reviewing"


def default_freeze_policy_resource() -> Traversable:
    resource = files("zed_i18n_kit").joinpath("rule_packs", DEFAULT_FREEZE_POLICY_NAME)
    if not resource.is_file():
        raise FileNotFoundError(
            f"packaged freeze policy does not exist: {DEFAULT_FREEZE_POLICY_NAME}"
        )
    return resource


def load_freeze_policy(path: SchemaResource) -> FreezePolicy:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FreezeGateError(f"cannot load freeze policy {path}: {error}") from error
    raw = _as_mapping(value, str(path))
    _require_exact_keys(raw, POLICY_FIELDS, str(path))
    schema_version = _require_int(raw, "schema_version", str(path), minimum=1)
    if schema_version != FREEZE_POLICY_SCHEMA_VERSION:
        raise FreezeGateError(f"{path}: unsupported schema_version {schema_version}")

    review_set_id = _require_string(raw, "review_set_id", str(path))
    if not _valid_review_set_id(review_set_id):
        raise FreezeGateError(f"{path}: invalid review_set_id {review_set_id!r}")
    audit_set_id = _require_string(raw, "audit_set_id", str(path))
    if not _valid_review_set_id(audit_set_id):
        raise FreezeGateError(f"{path}: invalid audit_set_id {audit_set_id!r}")
    required_passed = _require_string_tuple(raw, "required_passed_probes", str(path))
    allowed_failed = _require_string_tuple(raw, "allowed_failed_probes", str(path))
    overlap = set(required_passed) & set(allowed_failed)
    if overlap:
        raise FreezeGateError(
            f"{path}: probes cannot be both required and allowed to fail: "
            + ",".join(sorted(overlap))
        )

    strata_value = raw["strata"]
    if not isinstance(strata_value, list) or not strata_value:
        raise FreezeGateError(f"{path}: strata must be a non-empty array")
    strata: list[StratumPolicy] = []
    stratum_keys: set[tuple[str, str]] = set()
    for index, item in enumerate(strata_value):
        context = f"{path}: strata[{index}]"
        item_raw = _as_mapping(item, context)
        _require_exact_keys(item_raw, STRATUM_FIELDS, context)
        dimension = _require_string(item_raw, "dimension", context)
        if dimension not in STRATUM_DIMENSIONS:
            raise FreezeGateError(f"{context}: unsupported dimension {dimension!r}")
        stratum = StratumPolicy(
            dimension=dimension,
            value=_require_string(item_raw, "value", context),
            minimum=_require_int(item_raw, "minimum", context, minimum=1),
        )
        key = (stratum.dimension, stratum.value)
        if key in stratum_keys:
            raise FreezeGateError(
                f"{path}: duplicate stratum {dimension}:{stratum.value}"
            )
        stratum_keys.add(key)
        strata.append(stratum)

    metrics_raw = _as_mapping(raw["metrics"], f"{path}: metrics")
    if set(metrics_raw) != METRIC_NAMES:
        raise FreezeGateError(f"{path}: metric policies drifted from runtime")
    metrics = {
        name: _parse_metric_policy(metrics_raw[name], f"{path}: metrics.{name}")
        for name in sorted(METRIC_NAMES)
    }

    return FreezePolicy(
        review_set_id=review_set_id,
        audit_set_id=audit_set_id,
        zed_commit=_require_lower_hex(raw, "zed_commit", 40, str(path)),
        corpus_sample_sha256=_require_lower_hex(
            raw, "corpus_sample_sha256", 64, str(path)
        ),
        tool_version=_require_string(raw, "tool_version", str(path)),
        rule_pack_version=_require_string(raw, "rule_pack_version", str(path)),
        config_hash=_require_lower_hex(raw, "config_hash", 64, str(path)),
        rule_ids=_require_string_tuple(raw, "rule_ids", str(path)),
        required_passed_probes=required_passed,
        allowed_failed_probes=allowed_failed,
        minimum_reviewed_samples=_require_int(
            raw, "minimum_reviewed_samples", str(path), minimum=1
        ),
        minimum_audited_samples=_require_int(
            raw, "minimum_audited_samples", str(path), minimum=1
        ),
        strata=tuple(strata),
        metrics=metrics,
        maximum_unmatched_samples=_require_int(
            raw, "maximum_unmatched_samples", str(path), minimum=0
        ),
        maximum_ambiguous_samples=_require_int(
            raw, "maximum_ambiguous_samples", str(path), minimum=0
        ),
        maximum_disputed_samples=_require_int(
            raw, "maximum_disputed_samples", str(path), minimum=0
        ),
        maximum_corpus_gaps=_require_int(
            raw, "maximum_corpus_gaps", str(path), minimum=0
        ),
        maximum_indeterminate=_require_int(
            raw, "maximum_indeterminate", str(path), minimum=0
        ),
        maximum_unsafe_promotions=_require_int(
            raw, "maximum_unsafe_promotions", str(path), minimum=0
        ),
        maximum_candidate_exclusion_mismatches=_require_int(
            raw, "maximum_candidate_exclusion_mismatches", str(path), minimum=0
        ),
    )


def evaluate_freeze_gate(
    corpus: GoldenCorpus,
    scan_result: ScanResult,
    policy: FreezePolicy,
    review_result: ReviewResult | None,
    audit_bundle: AuditBundle | None = None,
    audit_result: AuditResult | None = None,
) -> FreezeGateReport:
    validate_scan_result(scan_result)
    failures: list[str] = []
    _check_identity(corpus, scan_result, policy, failures)
    _check_capabilities(scan_result, policy, failures)

    audit_set_id = policy.audit_set_id
    audit_reviewer_id: str | None = None
    audit_sample_count = 0
    audit_decision_count = 0
    missing_audit_sample_count = 0
    audit_expected_disposition_counts: Mapping[AuditDisposition, int] = {
        disposition: 0 for disposition in AuditDisposition
    }
    audit_outcome_counts: Mapping[AuditOutcome, int] = {
        outcome: 0 for outcome in AuditOutcome
    }
    if audit_bundle is None or audit_result is None:
        failures.append("independent unlabeled audit bundle and result are required")
    else:
        audit_reviewer_id = audit_result.reviewer_id
        audit_sample_count = len(audit_bundle.occurrences)
        audit_decision_count = len(audit_result.decisions)
        if _check_audit_identity(
            scan_result, policy, audit_bundle, audit_result, failures
        ):
            try:
                audit_reconciliation = reconcile_audit_result(
                    audit_bundle, audit_result, scan_result
                )
            except UnlabeledAuditError as error:
                failures.append(f"audit evidence is invalid: {error}")
            else:
                missing_audit_sample_count = len(
                    audit_reconciliation.missing_occurrence_ids
                )
                audit_outcome_counts = audit_reconciliation.outcome_counts
                audit_expected_disposition_counts = (
                    audit_reconciliation.expected_disposition_counts
                )
                failures.extend(audit_reconciliation.structural_failure_reasons)
                if (
                    len(audit_reconciliation.corpus_gap_occurrence_ids)
                    > policy.maximum_corpus_gaps
                ):
                    failures.append(
                        "corpus gap count exceeds maximum: "
                        f"{len(audit_reconciliation.corpus_gap_occurrence_ids)} > "
                        f"{policy.maximum_corpus_gaps}"
                    )
                if (
                    len(audit_reconciliation.indeterminate_occurrence_ids)
                    > policy.maximum_indeterminate
                ):
                    failures.append(
                        "indeterminate audit count exceeds maximum: "
                        f"{len(audit_reconciliation.indeterminate_occurrence_ids)} > "
                        f"{policy.maximum_indeterminate}"
                    )
                if (
                    len(audit_reconciliation.unsafe_promotion_occurrence_ids)
                    > policy.maximum_unsafe_promotions
                ):
                    failures.append(
                        "unsafe promotion count exceeds maximum: "
                        f"{len(audit_reconciliation.unsafe_promotion_occurrence_ids)} > "
                        f"{policy.maximum_unsafe_promotions}"
                    )
                if (
                    len(
                        audit_reconciliation.candidate_exclusion_mismatch_occurrence_ids
                    )
                    > policy.maximum_candidate_exclusion_mismatches
                ):
                    failures.append(
                        "candidate/excluded mismatch count exceeds maximum: "
                        f"{len(audit_reconciliation.candidate_exclusion_mismatch_occurrence_ids)} > "
                        f"{policy.maximum_candidate_exclusion_mismatches}"
                    )
    if audit_sample_count < policy.minimum_audited_samples:
        failures.append(
            "audited sample count below minimum: "
            f"{audit_sample_count} < {policy.minimum_audited_samples}"
        )

    reviewer_id: str | None = None
    agreed_ids: tuple[str, ...] = ()
    disputed_count = 0
    missing_count = len(corpus.samples)
    if review_result is None:
        failures.append("independent review result is required")
    else:
        reviewer_id = review_result.reviewer_id
        try:
            reconciliation = reconcile_review_result(
                corpus, review_result, review_set_id=policy.review_set_id
            )
        except ReviewError as error:
            failures.append(f"review evidence is invalid: {error}")
        else:
            agreed_ids = reconciliation.agreed_sample_ids
            disputed_count = len(reconciliation.disputed_sample_ids)
            missing_count = len(reconciliation.missing_sample_ids)

    samples_by_id = {sample.sample_id: sample for sample in corpus.samples}
    scan_scope = set(scan_result.metadata.scan_scope)
    reviewed_samples = tuple(
        samples_by_id[sample_id]
        for sample_id in agreed_ids
        if samples_by_id[sample_id].path in scan_scope
    )
    if len(reviewed_samples) < policy.minimum_reviewed_samples:
        failures.append(
            "independently reviewed in-scope sample count below minimum: "
            f"{len(reviewed_samples)} < {policy.minimum_reviewed_samples}"
        )
    if disputed_count > policy.maximum_disputed_samples:
        failures.append(
            f"disputed sample count exceeds maximum: {disputed_count} > "
            f"{policy.maximum_disputed_samples}"
        )

    stratum_counts = _count_strata(reviewed_samples, scan_result)
    for requirement in policy.strata:
        actual = stratum_counts[requirement.dimension, requirement.value]
        if actual < requirement.minimum:
            failures.append(
                f"stratum {requirement.dimension}:{requirement.value} below minimum: "
                f"{actual} < {requirement.minimum}"
            )

    metrics = _evaluate_reviewed_metrics(
        corpus, scan_result, reviewed_samples, failures
    )
    for name in sorted(METRIC_NAMES):
        rate = getattr(metrics, name)
        requirement = policy.metrics[name]
        if rate.denominator < requirement.minimum_denominator:
            failures.append(
                f"metric {name} denominator below minimum: "
                f"{rate.denominator} < {requirement.minimum_denominator}"
            )
            continue
        assert rate.value is not None
        if requirement.minimum is not None and rate.value < requirement.minimum:
            failures.append(
                f"metric {name} below minimum: {rate.value:.6f} < "
                f"{requirement.minimum:.6f}"
            )
        if requirement.maximum is not None and rate.value > requirement.maximum:
            failures.append(
                f"metric {name} exceeds maximum: {rate.value:.6f} > "
                f"{requirement.maximum:.6f}"
            )
    if metrics.unmatched_sample_count > policy.maximum_unmatched_samples:
        failures.append(
            "unmatched sample count exceeds maximum: "
            f"{metrics.unmatched_sample_count} > {policy.maximum_unmatched_samples}"
        )
    if metrics.ambiguous_sample_count > policy.maximum_ambiguous_samples:
        failures.append(
            "ambiguous sample count exceeds maximum: "
            f"{metrics.ambiguous_sample_count} > {policy.maximum_ambiguous_samples}"
        )

    return FreezeGateReport(
        review_set_id=policy.review_set_id,
        rule_pack_version=policy.rule_pack_version,
        tool_version=policy.tool_version,
        zed_commit=policy.zed_commit,
        corpus_sample_sha256=policy.corpus_sample_sha256,
        config_hash=policy.config_hash,
        capability_probe_statuses={
            probe.probe_id: probe.status.value
            for probe in scan_result.metadata.capability_probes
        },
        reviewer_id=reviewer_id,
        reviewed_sample_count=len(reviewed_samples),
        missing_sample_count=missing_count,
        disputed_sample_count=disputed_count,
        stratum_counts=dict(stratum_counts),
        metrics=metrics,
        failures=tuple(failures),
        audit_set_id=audit_set_id,
        audit_reviewer_id=audit_reviewer_id,
        audit_sample_count=audit_sample_count,
        audit_decision_count=audit_decision_count,
        missing_audit_sample_count=missing_audit_sample_count,
        audit_expected_disposition_counts=audit_expected_disposition_counts,
        audit_outcome_counts=audit_outcome_counts,
    )


def serialize_freeze_gate_report(report: FreezeGateReport, policy: FreezePolicy) -> str:
    payload = {
        "audit": {
            "audit_set_id": report.audit_set_id,
            "decision_count": report.audit_decision_count,
            "minimum_sample_count": policy.minimum_audited_samples,
            "expected_disposition_counts": {
                disposition.value: report.audit_expected_disposition_counts.get(
                    disposition, 0
                )
                for disposition in AuditDisposition
            },
            "outcome_counts": {
                outcome.value: report.audit_outcome_counts.get(outcome, 0)
                for outcome in AuditOutcome
            },
            "reviewer_id": report.audit_reviewer_id,
            "sample_count": report.audit_sample_count,
            "missing_sample_count": report.missing_audit_sample_count,
            "maximum_corpus_gaps": policy.maximum_corpus_gaps,
            "maximum_indeterminate": policy.maximum_indeterminate,
            "maximum_unsafe_promotions": policy.maximum_unsafe_promotions,
            "maximum_candidate_exclusion_mismatches": (
                policy.maximum_candidate_exclusion_mismatches
            ),
        },
        "audit_decision_count": report.audit_decision_count,
        "audited_sample_count": report.audited_sample_count,
        "audit_expected_disposition_counts": {
            disposition.value: report.audit_expected_disposition_counts.get(
                disposition, 0
            )
            for disposition in AuditDisposition
        },
        "audit_outcome_counts": {
            outcome.value: report.audit_outcome_counts.get(outcome, 0)
            for outcome in AuditOutcome
        },
        "audit_reviewer_id": report.audit_reviewer_id,
        "audit_sample_count": report.audit_sample_count,
        "audit_set_id": report.audit_set_id,
        "capability_probes": [
            {
                "probe_id": probe_id,
                "requirement": (
                    "required_passed"
                    if probe_id in policy.required_passed_probes
                    else "allowed_failed"
                ),
                "status": status,
            }
            for probe_id, status in sorted(report.capability_probe_statuses.items())
        ],
        "config_hash": report.config_hash,
        "corpus_sample_sha256": report.corpus_sample_sha256,
        "disputed_sample_count": report.disputed_sample_count,
        "failures": list(report.failures),
        "failure_reasons": list(report.failures),
        "freeze_status": report.freeze_status,
        "metrics": {
            name: {
                "requirement": {
                    "maximum": policy.metrics[name].maximum,
                    "minimum": policy.metrics[name].minimum,
                    "minimum_denominator": policy.metrics[name].minimum_denominator,
                },
                "result": _serialize_rate(getattr(report.metrics, name)),
            }
            for name in sorted(METRIC_NAMES)
        },
        "missing_sample_count": report.missing_sample_count,
        "missing_audit_sample_count": report.missing_audit_sample_count,
        "passed": report.passed,
        "review_set_id": report.review_set_id,
        "reviewed_sample_count": report.reviewed_sample_count,
        "reviewer_id": report.reviewer_id,
        "rule_ids": list(policy.rule_ids),
        "rule_pack_version": report.rule_pack_version,
        "strata": [
            {
                "actual": report.stratum_counts.get(
                    (requirement.dimension, requirement.value), 0
                ),
                "dimension": requirement.dimension,
                "minimum": requirement.minimum,
                "value": requirement.value,
            }
            for requirement in policy.strata
        ],
        "tool_version": report.tool_version,
        "zed_commit": report.zed_commit,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _check_audit_identity(
    scan_result: ScanResult,
    policy: FreezePolicy,
    bundle: AuditBundle,
    result: AuditResult,
    failures: list[str],
) -> bool:
    identity_valid = True
    artifact_checks = (
        ("audit set", policy.audit_set_id, bundle.audit_set_id, result.audit_set_id),
        ("Zed commit", policy.zed_commit, bundle.zed_commit, result.zed_commit),
        (
            "corpus sample SHA-256",
            policy.corpus_sample_sha256,
            bundle.corpus_sample_sha256,
            result.corpus_sample_sha256,
        ),
        (
            "scan config hash",
            policy.config_hash,
            bundle.scan_config_hash,
            result.scan_config_hash,
        ),
        (
            "tool version",
            policy.tool_version,
            bundle.tool_version,
            result.tool_version,
        ),
        (
            "rule pack version",
            policy.rule_pack_version,
            bundle.rule_pack_version,
            result.rule_pack_version,
        ),
    )
    for label, expected, bundle_value, result_value in artifact_checks:
        if bundle_value != expected:
            identity_valid = False
            failures.append(
                f"audit bundle {label} mismatch: expected {expected}, got {bundle_value}"
            )
        if result_value != expected:
            identity_valid = False
            failures.append(
                f"audit result {label} mismatch: expected {expected}, got {result_value}"
            )
        if bundle_value != result_value:
            identity_valid = False
            failures.append(
                f"audit bundle/result {label} mismatch: "
                f"{bundle_value} != {result_value}"
            )

    scan_checks = (
        ("Zed commit", policy.zed_commit, scan_result.metadata.zed_commit),
        ("scan config hash", policy.config_hash, scan_result.metadata.config_hash),
        ("tool version", policy.tool_version, scan_result.metadata.tool_version),
        (
            "rule pack version",
            policy.rule_pack_version,
            scan_result.metadata.rule_pack_version,
        ),
    )
    for label, expected, actual in scan_checks:
        if actual != expected:
            identity_valid = False
            failures.append(
                f"scan-result {label} mismatch: expected {expected}, got {actual}"
            )
    return identity_valid


def _check_identity(
    corpus: GoldenCorpus,
    scan_result: ScanResult,
    policy: FreezePolicy,
    failures: list[str],
) -> None:
    metadata = scan_result.metadata
    checks = (
        ("corpus Zed commit", corpus.manifest.zed_commit, policy.zed_commit),
        (
            "corpus sample SHA-256",
            corpus.manifest.sample_sha256,
            policy.corpus_sample_sha256,
        ),
        ("scan-result Zed commit", metadata.zed_commit, policy.zed_commit),
        ("scan-result tool version", metadata.tool_version, policy.tool_version),
        (
            "scan-result rule pack",
            metadata.rule_pack_version,
            policy.rule_pack_version,
        ),
        ("scan-result config hash", metadata.config_hash, policy.config_hash),
    )
    for label, actual, expected in checks:
        if actual != expected:
            failures.append(f"{label} mismatch: expected {expected}, got {actual}")

    runtime_rule_ids = tuple(
        sorted(
            (
                *(rule.rule_id for rule in WORKSPACE_BUILTIN_RULES),
                *WORKSPACE_STRUCTURAL_RULE_IDS,
            )
        )
    )
    if tuple(sorted(policy.rule_ids)) != runtime_rule_ids:
        failures.append("policy rule IDs differ from the runtime workspace profile")
    evidence_rule_ids = {
        evidence.rule_id
        for occurrence in scan_result.occurrences
        for evidence in occurrence.evidence
    }
    unexpected_rule_ids = tuple(sorted(evidence_rule_ids - set(policy.rule_ids)))
    if unexpected_rule_ids:
        failures.append(
            "scan-result contains rule IDs outside the freeze policy: "
            + ",".join(unexpected_rule_ids)
        )


def _check_capabilities(
    scan_result: ScanResult, policy: FreezePolicy, failures: list[str]
) -> None:
    probes = {
        probe.probe_id: probe.status for probe in scan_result.metadata.capability_probes
    }
    expected_ids = set(policy.required_passed_probes) | set(
        policy.allowed_failed_probes
    )
    if set(probes) != expected_ids:
        missing = sorted(expected_ids - set(probes))
        extra = sorted(set(probes) - expected_ids)
        failures.append(
            f"capability probe set mismatch: missing={missing}, extra={extra}"
        )
    for probe_id in policy.required_passed_probes:
        if probes.get(probe_id) is not CapabilityProbeStatus.PASSED:
            failures.append(f"required capability probe did not pass: {probe_id}")
    for probe_id, status in sorted(probes.items()):
        if (
            status is CapabilityProbeStatus.FAILED
            and probe_id not in policy.allowed_failed_probes
        ):
            failures.append(f"unapproved capability probe failure: {probe_id}")


def _evaluate_reviewed_metrics(
    corpus: GoldenCorpus,
    scan_result: ScanResult,
    reviewed_samples: tuple[GoldenSample, ...],
    failures: list[str],
) -> EvaluationMetrics:
    reviewed_corpus = GoldenCorpus(
        corpus.manifest,
        tuple(
            replace(sample, review_state=ReviewState.INDEPENDENTLY_REVIEWED)
            for sample in reviewed_samples
        ),
    )
    try:
        report = evaluate_scan_result(reviewed_corpus, scan_result)
    except EvaluationError as error:
        failures.append(f"evaluation input is invalid: {error}")
        return _empty_metrics()
    if report.independently_reviewed_metrics is None:
        return _empty_metrics()
    return report.independently_reviewed_metrics


def _count_strata(
    reviewed_samples: tuple[GoldenSample, ...], scan_result: ScanResult
) -> Counter[tuple[str, str]]:
    counts: Counter[tuple[str, str]] = Counter()
    for sample in reviewed_samples:
        counts["expected_disposition", sample.expected_disposition.value] += 1
        counts["ownership", sample.ownership.value] += 1
        counts["subject_kind", sample.subject_kind.value] += 1
        for feature in sample.features:
            counts["feature", feature.value] += 1
        matches = tuple(
            occurrence
            for occurrence in scan_result.occurrences
            if occurrence_matches_sample(occurrence, sample)
        )
        if len(matches) == 1:
            for rule_id in {item.rule_id for item in matches[0].evidence}:
                counts["rule_id", rule_id] += 1
    return counts


def _empty_metrics() -> EvaluationMetrics:
    undefined = EvaluationRate(0, 0)
    return EvaluationMetrics(
        auto_confirm_precision=undefined,
        auto_confirm_coverage=undefined,
        candidate_recall=undefined,
        unsafe_promotion_rate=undefined,
        exclusion_leakage=undefined,
        unmatched_sample_count=0,
        unlabeled_occurrence_count=0,
        ambiguous_sample_count=0,
    )


def _serialize_rate(rate: EvaluationRate) -> dict[str, float | int | None]:
    return {
        "denominator": rate.denominator,
        "numerator": rate.numerator,
        "value": rate.value,
    }


def _parse_metric_policy(value: object, context: str) -> MetricPolicy:
    raw = _as_mapping(value, context)
    _require_exact_keys(raw, METRIC_POLICY_FIELDS, context)
    minimum = _require_optional_rate(raw, "minimum", context)
    maximum = _require_optional_rate(raw, "maximum", context)
    if minimum is None and maximum is None:
        raise FreezeGateError(f"{context}: minimum or maximum must be set")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise FreezeGateError(f"{context}: minimum cannot exceed maximum")
    return MetricPolicy(
        minimum_denominator=_require_int(
            raw, "minimum_denominator", context, minimum=1
        ),
        minimum=minimum,
        maximum=maximum,
    )


def _require_optional_rate(
    raw: Mapping[str, object], key: str, context: str
) -> float | None:
    value = raw[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FreezeGateError(f"{context}: {key} must be a number or null")
    result = float(value)
    if not 0 <= result <= 1:
        raise FreezeGateError(f"{context}: {key} must be between 0 and 1")
    return result


def _as_mapping(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise FreezeGateError(f"{context}: expected a JSON object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise FreezeGateError(f"{context}: object keys must be strings")
        result[key] = item
    return result


def _require_exact_keys(
    raw: Mapping[str, object], expected: frozenset[str], context: str
) -> None:
    actual = set(raw)
    if actual != expected:
        raise FreezeGateError(
            f"{context}: fields differ; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _require_string(raw: Mapping[str, object], key: str, context: str) -> str:
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise FreezeGateError(f"{context}: {key} must be a non-empty string")
    return value


def _require_string_tuple(
    raw: Mapping[str, object], key: str, context: str
) -> tuple[str, ...]:
    value = raw[key]
    if not isinstance(value, list) or not value:
        raise FreezeGateError(f"{context}: {key} must be a non-empty array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise FreezeGateError(f"{context}: {key} values must be non-empty strings")
        result.append(item)
    if len(set(result)) != len(result):
        raise FreezeGateError(f"{context}: {key} cannot contain duplicates")
    return tuple(result)


def _require_int(
    raw: Mapping[str, object],
    key: str,
    context: str,
    *,
    minimum: int,
) -> int:
    value = raw[key]
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise FreezeGateError(
            f"{context}: {key} must be an integer greater than or equal to {minimum}"
        )
    return value


def _require_lower_hex(
    raw: Mapping[str, object], key: str, length: int, context: str
) -> str:
    value = _require_string(raw, key, context)
    if len(value) != length or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise FreezeGateError(
            f"{context}: {key} must be {length}-character lowercase hex"
        )
    return value


def _valid_review_set_id(value: str) -> bool:
    return (
        3 <= len(value) <= 64
        and value[0].isalnum()
        and value[0].isascii()
        and all(
            character.isascii()
            and (character.islower() or character.isdigit() or character == "-")
            for character in value
        )
    )
