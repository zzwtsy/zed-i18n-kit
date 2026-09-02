import argparse
from pathlib import Path

from zed_i18n_kit.cst_calibration import (
    CstCalibrationError,
    CstCalibrationResult,
    validate_cst_calibration,
)
from zed_i18n_kit.cst_canonical import CanonicalCstError, validate_corpus_cst
from zed_i18n_kit.evaluation import (
    EvaluationError,
    EvaluationRate,
    evaluate_scan_result,
)
from zed_i18n_kit.golden import (
    Decision,
    ExpectedPresence,
    GoldenCorpus,
    GoldenCorpusError,
    load_corpus,
    validate_checkout,
    validate_schema_contract,
)
from zed_i18n_kit.rust_cst import RustCstError
from zed_i18n_kit.scan_profiles import PROTOTYPE_SCAN_PROFILE
from zed_i18n_kit.scan_result import (
    ScanResultError,
    parse_scan_result_json,
    serialize_scan_result,
    validate_scan_result_schema,
    validate_scan_snapshot,
)
from zed_i18n_kit.scanner import ScannerError, scan_sources
from zed_i18n_kit.schema_resources import (
    GOLDEN_CORPUS_SCHEMA_NAME,
    SCAN_RESULT_SCHEMA_NAME,
    schema_resource,
)


class ScanEvaluationContractError(ValueError):
    """Raised when the scan and evaluation contracts produce inconsistent output."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the scan-result evaluation contract"
    )
    parser.add_argument("--corpus", type=Path, default=Path("corpus/zed-ui-text/v2"))
    parser.add_argument("--zed", type=Path, default=Path("local/zed"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        corpus = load_corpus(args.corpus)
        validate_schema_contract(schema_resource(GOLDEN_CORPUS_SCHEMA_NAME))
        validate_scan_result_schema(schema_resource(SCAN_RESULT_SCHEMA_NAME))
        validate_checkout(corpus, args.zed)
        validate_corpus_cst(corpus, args.zed)
        calibration_results = validate_cst_calibration(corpus, args.zed)
        initial_scan_result = scan_sources(args.zed, profile=PROTOTYPE_SCAN_PROFILE)
        repeated_scan_result = scan_sources(args.zed, profile=PROTOTYPE_SCAN_PROFILE)
        serialized_scan_result = serialize_scan_result(initial_scan_result)
        if serialized_scan_result != serialize_scan_result(repeated_scan_result):
            raise ScanEvaluationContractError(
                "repeated scans are not byte-for-byte deterministic"
            )
        if (
            serialize_scan_result(parse_scan_result_json(serialized_scan_result))
            != serialized_scan_result
        ):
            raise ScanEvaluationContractError(
                "scan-result JSON round trip is not deterministic"
            )
        validate_scan_snapshot(initial_scan_result, args.zed)
        report = evaluate_scan_result(corpus, initial_scan_result)
        _validate_calibration_sample_predictions(
            corpus, report.sample_predictions, calibration_results
        )
    except (
        CstCalibrationError,
        CanonicalCstError,
        EvaluationError,
        GoldenCorpusError,
        ScanEvaluationContractError,
        RustCstError,
        ScannerError,
        ScanResultError,
        OSError,
        UnicodeError,
    ) as error:
        print(f"scan evaluation contract check failed: {error}")
        return 1

    print(
        f"validated {len(calibration_results)} CST fixtures and "
        f"{len(initial_scan_result.occurrences)} deterministic occurrences"
    )
    for probe in initial_scan_result.metadata.capability_probes:
        print(f"probe {probe.probe_id}: {probe.status.value} - {probe.details}")
    print(
        "evaluation: "
        f"samples={report.evaluated_sample_count}, "
        f"matched={len(report.sample_predictions)}, "
        f"unmatched={len(report.unmatched_sample_ids)}, "
        f"ambiguous={len(report.ambiguous_sample_ids)}, "
        f"unlabeled={len(report.unlabeled_occurrence_ids)}"
    )
    print(
        "metrics: "
        "precision="
        f"{_format_rate(report.observational_metrics.auto_confirm_precision)}, "
        "coverage="
        f"{_format_rate(report.observational_metrics.auto_confirm_coverage)}, "
        f"recall={_format_rate(report.observational_metrics.candidate_recall)}, "
        "unsafe="
        f"{_format_rate(report.observational_metrics.unsafe_promotion_rate)}, "
        "leakage="
        f"{_format_rate(report.observational_metrics.exclusion_leakage)}"
    )
    print(
        "independently reviewed metrics available: "
        f"{str(report.has_independently_reviewed_metrics).lower()} "
        f"({report.independently_reviewed_sample_count} independently reviewed samples)"
    )
    return 0


def _validate_calibration_sample_predictions(
    corpus: GoldenCorpus,
    sample_predictions: dict[str, Decision],
    calibration_results: tuple[CstCalibrationResult, ...],
) -> None:
    samples = {sample.sample_id: sample for sample in corpus.samples}
    for calibration_result in calibration_results:
        sample = samples[calibration_result.sample_id]
        prediction = sample_predictions.get(sample.sample_id)
        if (
            sample.expected_presence is ExpectedPresence.CANDIDATE
            and prediction is None
        ):
            raise ScanEvaluationContractError(
                f"{sample.sample_id}: candidate risk fixture was not matched"
            )
        if (
            sample.expected_presence is ExpectedPresence.NOT_CANDIDATE
            and prediction not in {None, Decision.EXCLUDED}
        ):
            raise ScanEvaluationContractError(
                f"{sample.sample_id}: exclusion fixture leaked as {prediction}"
            )


def _format_rate(rate: EvaluationRate) -> str:
    if rate.value is None:
        return f"undefined ({rate.numerator}/{rate.denominator})"
    return f"{rate.value:.4f} ({rate.numerator}/{rate.denominator})"


if __name__ == "__main__":
    raise SystemExit(main())
