from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .evaluation import (
    EvaluationError,
    EvaluationRate,
    evaluate_scan_result,
    serialize_evaluation_report,
    write_evaluation_report,
)
from .freeze_gate import (
    FreezeGateError,
    default_freeze_policy_resource,
    evaluate_freeze_gate,
    load_freeze_policy,
    serialize_freeze_gate_report,
)
from .golden import (
    GoldenCorpusError,
    load_corpus,
    validate_checkout,
    validate_schema_contract,
)
from .review import (
    ReviewError,
    build_review_bundle,
    load_review_result,
    reconcile_review_result,
    serialize_review_reconciliation,
    validate_review_schema_contracts,
    write_review_bundle,
)
from .rust_cst import RustCstError
from .scan_result import (
    ScanResultError,
    load_scan_result,
    validate_scan_result_schema,
    validate_scan_snapshot,
    write_scan_result,
)
from .scanner import ScannerError, scan_sources
from .schema_resources import (
    GOLDEN_CORPUS_SCHEMA_NAME,
    REVIEW_BUNDLE_SCHEMA_NAME,
    REVIEW_RESULT_SCHEMA_NAME,
    SCAN_RESULT_SCHEMA_NAME,
    UNLABELED_AUDIT_BUNDLE_SCHEMA_NAME,
    UNLABELED_AUDIT_RESULT_SCHEMA_NAME,
    schema_resource,
)
from .unlabeled_audit import (
    UnlabeledAuditError,
    build_audit_bundle,
    load_audit_bundle,
    load_audit_result,
    reconcile_audit_result,
    serialize_audit_reconciliation,
    validate_audit_schema_contracts,
    write_audit_bundle,
)

DEFAULT_CORPUS_DIR = Path("corpus/zed-ui-text/v2")
DEFAULT_ZED_ROOT = Path("local/zed")


class CliError(ValueError):
    """Raised when CLI arguments would violate an input safety boundary."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zed-i18n-kit",
        description="Analyze Zed/GPUI UI text without modifying the input checkout",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser(
        "scan", help="scan production Rust sources without modifying the checkout"
    )
    scan_parser.add_argument("--zed", type=Path, default=DEFAULT_ZED_ROOT)
    scan_parser.add_argument("--output", type=Path, required=True)

    evaluate_parser = subparsers.add_parser(
        "evaluate", help="evaluate a persisted scan result against corpus v2"
    )
    evaluate_parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_DIR)
    evaluate_parser.add_argument("--scan-result", type=Path, required=True)
    evaluate_parser.add_argument("--zed", type=Path, default=DEFAULT_ZED_ROOT)
    evaluate_parser.add_argument(
        "--output",
        type=Path,
        help="write the deterministic observational report instead of stdout",
    )

    corpus_check_parser = subparsers.add_parser(
        "corpus-check", help="validate corpus v2 and its pinned Zed checkout"
    )
    corpus_check_parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_DIR)
    corpus_check_parser.add_argument("--zed", type=Path, default=DEFAULT_ZED_ROOT)

    review_export_parser = subparsers.add_parser(
        "review-export", help="export a deterministic blind independent-review bundle"
    )
    review_export_parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_DIR)
    review_export_parser.add_argument("--zed", type=Path, default=DEFAULT_ZED_ROOT)
    review_export_parser.add_argument("--review-set", required=True)
    review_export_parser.add_argument("--output", type=Path, required=True)

    review_check_parser = subparsers.add_parser(
        "review-check", help="validate and reconcile an independent-review result"
    )
    review_check_parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_DIR)
    review_check_parser.add_argument("--review-result", type=Path, required=True)
    review_check_parser.add_argument("--review-set", required=True)

    freeze_check_parser = subparsers.add_parser(
        "freeze-check", help="evaluate the fail-closed rule-pack freeze gate"
    )
    freeze_check_parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_DIR)
    freeze_check_parser.add_argument("--scan-result", type=Path, required=True)
    freeze_check_parser.add_argument("--zed", type=Path, default=DEFAULT_ZED_ROOT)
    freeze_check_parser.add_argument("--review-result", type=Path)
    freeze_check_parser.add_argument(
        "--policy",
        type=Path,
        help="override the packaged zed-builtin-v1 freeze policy",
    )

    audit_export_parser = subparsers.add_parser(
        "audit-export", help="export a blind stratified unlabeled-occurrence audit"
    )
    audit_export_parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_DIR)
    audit_export_parser.add_argument("--scan-result", type=Path, required=True)
    audit_export_parser.add_argument("--zed", type=Path, default=DEFAULT_ZED_ROOT)
    audit_export_parser.add_argument("--audit-set", required=True)
    audit_export_parser.add_argument("--sample-size", type=int, required=True)
    audit_export_parser.add_argument("--output", type=Path, required=True)

    audit_check_parser = subparsers.add_parser(
        "audit-check", help="validate and reconcile an unlabeled-occurrence audit"
    )
    audit_check_parser.add_argument("--audit-bundle", type=Path, required=True)
    audit_check_parser.add_argument("--audit-result", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "scan":
            return _run_scan(args.zed, args.output)
        if args.command == "evaluate":
            return _run_evaluate(
                args.corpus,
                args.scan_result,
                args.zed,
                args.output,
            )
        if args.command == "corpus-check":
            return _run_corpus_check(args.corpus, args.zed)
        if args.command == "review-export":
            return _run_review_export(
                args.corpus, args.zed, args.review_set, args.output
            )
        if args.command == "review-check":
            return _run_review_check(args.corpus, args.review_result, args.review_set)
        if args.command == "freeze-check":
            return _run_freeze_check(
                args.corpus,
                args.scan_result,
                args.zed,
                args.review_result,
                args.policy,
            )
        if args.command == "audit-export":
            return _run_audit_export(
                args.corpus,
                args.scan_result,
                args.zed,
                args.audit_set,
                args.sample_size,
                args.output,
            )
        if args.command == "audit-check":
            return _run_audit_check(args.audit_bundle, args.audit_result)
    except (
        CliError,
        EvaluationError,
        FreezeGateError,
        GoldenCorpusError,
        ReviewError,
        RustCstError,
        ScannerError,
        ScanResultError,
        UnlabeledAuditError,
        OSError,
        UnicodeError,
    ) as error:
        print(f"zed-i18n-kit: {error}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command {args.command!r}")


def _run_scan(zed_root: Path, output: Path) -> int:
    _ensure_output_outside_checkout(output, zed_root)
    scan_result = scan_sources(zed_root)
    validate_scan_snapshot(scan_result, zed_root)
    write_scan_result(output, scan_result)
    failed_probes = sum(
        probe.status.value == "failed"
        for probe in scan_result.metadata.capability_probes
    )
    print(
        f"wrote {len(scan_result.occurrences)} occurrences to {output} "
        f"({failed_probes} failed capability probes)"
    )
    return 0


def _run_evaluate(
    corpus_dir: Path,
    scan_result_path: Path,
    zed_root: Path,
    output: Path | None,
) -> int:
    if output is not None:
        _ensure_output_outside_checkout(output, zed_root)
    corpus = load_corpus(corpus_dir)
    scan_result = load_scan_result(scan_result_path)
    validate_scan_snapshot(scan_result, zed_root)
    report = evaluate_scan_result(corpus, scan_result)
    if output is None:
        print(serialize_evaluation_report(report), end="")
    else:
        write_evaluation_report(output, report)
        print(f"wrote observational evaluation report to {output}")
    print(
        "evaluation: "
        f"samples={report.evaluated_sample_count}, "
        f"unmatched={len(report.unmatched_sample_ids)}, "
        f"ambiguous={len(report.ambiguous_sample_ids)}, "
        f"unlabeled={len(report.unlabeled_occurrence_ids)}, "
        "auto_confirm_coverage="
        f"{_format_rate(report.observational_metrics.auto_confirm_coverage)}",
        file=sys.stderr if output is None else sys.stdout,
    )
    return 0


def _run_corpus_check(corpus_dir: Path, zed_root: Path) -> int:
    corpus = load_corpus(corpus_dir)
    validate_schema_contract(schema_resource(GOLDEN_CORPUS_SCHEMA_NAME))
    validate_scan_result_schema(schema_resource(SCAN_RESULT_SCHEMA_NAME))
    validate_review_schema_contracts(
        schema_resource(REVIEW_BUNDLE_SCHEMA_NAME),
        schema_resource(REVIEW_RESULT_SCHEMA_NAME),
    )
    validate_audit_schema_contracts(
        schema_resource(UNLABELED_AUDIT_BUNDLE_SCHEMA_NAME),
        schema_resource(UNLABELED_AUDIT_RESULT_SCHEMA_NAME),
    )
    validate_checkout(corpus, zed_root)
    print(
        f"validated {len(corpus.samples)} corpus samples against "
        f"Zed {corpus.manifest.zed_commit}"
    )
    return 0


def _run_review_export(
    corpus_dir: Path,
    zed_root: Path,
    review_set_id: str,
    output: Path,
) -> int:
    _ensure_output_outside_checkout(output, zed_root)
    corpus = load_corpus(corpus_dir)
    bundle = build_review_bundle(corpus, zed_root, review_set_id=review_set_id)
    write_review_bundle(output, bundle)
    print(
        f"wrote {len(bundle.samples)} blind review samples to {output} "
        f"for review set {bundle.review_set_id}"
    )
    return 0


def _run_review_check(
    corpus_dir: Path,
    review_result_path: Path,
    review_set_id: str,
) -> int:
    corpus = load_corpus(corpus_dir)
    result = load_review_result(review_result_path)
    report = reconcile_review_result(corpus, result, review_set_id=review_set_id)
    print(serialize_review_reconciliation(report), end="")
    print(
        "review reconciliation: "
        f"agreed={len(report.agreed_sample_ids)}, "
        f"disputed={len(report.disputed_sample_ids)}, "
        f"missing={len(report.missing_sample_ids)}",
        file=sys.stderr,
    )
    return 0 if report.is_complete_agreement else 1


def _run_freeze_check(
    corpus_dir: Path,
    scan_result_path: Path,
    zed_root: Path,
    review_result_path: Path | None,
    policy_path: Path | None,
) -> int:
    corpus = load_corpus(corpus_dir)
    scan_result = load_scan_result(scan_result_path)
    validate_scan_snapshot(scan_result, zed_root)
    review_result = (
        load_review_result(review_result_path)
        if review_result_path is not None
        else None
    )
    policy = load_freeze_policy(
        policy_path if policy_path is not None else default_freeze_policy_resource()
    )
    report = evaluate_freeze_gate(corpus, scan_result, policy, review_result)
    print(serialize_freeze_gate_report(report, policy), end="")
    print(
        f"freeze gate: status={report.freeze_status}, "
        f"reviewed={report.reviewed_sample_count}, failures={len(report.failures)}",
        file=sys.stderr,
    )
    return 0 if report.passed else 1


def _run_audit_export(
    corpus_dir: Path,
    scan_result_path: Path,
    zed_root: Path,
    audit_set_id: str,
    sample_size: int,
    output: Path,
) -> int:
    _ensure_output_outside_checkout(output, zed_root)
    corpus = load_corpus(corpus_dir)
    scan_result = load_scan_result(scan_result_path)
    bundle = build_audit_bundle(
        corpus,
        scan_result,
        zed_root,
        audit_set_id=audit_set_id,
        sample_size=sample_size,
    )
    write_audit_bundle(output, bundle)
    print(
        f"wrote {len(bundle.occurrences)} blind unlabeled occurrences to {output} "
        f"for audit set {bundle.audit_set_id}"
    )
    return 0


def _run_audit_check(audit_bundle_path: Path, audit_result_path: Path) -> int:
    bundle = load_audit_bundle(audit_bundle_path)
    result = load_audit_result(audit_result_path)
    report = reconcile_audit_result(bundle, result)
    print(serialize_audit_reconciliation(report), end="")
    print(
        f"unlabeled audit: reviewed={len(result.decisions)}, "
        f"missing={len(report.missing_occurrence_ids)}",
        file=sys.stderr,
    )
    return 0 if report.is_complete else 1


def _format_rate(rate: EvaluationRate) -> str:
    if rate.value is None:
        return f"undefined ({rate.numerator}/{rate.denominator})"
    return f"{rate.value:.4f} ({rate.numerator}/{rate.denominator})"


def _ensure_output_outside_checkout(output: Path, zed_root: Path) -> None:
    try:
        resolved_checkout = zed_root.resolve()
        resolved_output = output.resolve()
    except (OSError, RuntimeError) as error:
        raise CliError(f"cannot resolve output safety boundary: {error}") from error
    if (
        resolved_output == resolved_checkout
        or resolved_checkout in resolved_output.parents
    ):
        raise CliError(
            f"output path must stay outside the input Zed checkout: {output}"
        )
