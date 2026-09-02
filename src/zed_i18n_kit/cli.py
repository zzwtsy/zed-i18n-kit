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
from .golden import (
    GoldenCorpusError,
    load_corpus,
    validate_checkout,
    validate_schema_contract,
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
    SCAN_RESULT_SCHEMA_NAME,
    schema_resource,
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
    except (
        CliError,
        EvaluationError,
        GoldenCorpusError,
        RustCstError,
        ScannerError,
        ScanResultError,
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
    validate_checkout(corpus, zed_root)
    print(
        f"validated {len(corpus.samples)} corpus samples against "
        f"Zed {corpus.manifest.zed_commit}"
    )
    return 0


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
