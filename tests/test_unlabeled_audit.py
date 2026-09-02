import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath

import pytest

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
    RuleEvidence,
    ScanMetadata,
    ScanOccurrence,
    ScanResult,
    SourceFileSnapshot,
)
from zed_i18n_kit.schema_resources import (
    UNLABELED_AUDIT_BUNDLE_SCHEMA_NAME,
    UNLABELED_AUDIT_RESULT_SCHEMA_NAME,
    schema_resource,
)
from zed_i18n_kit.unlabeled_audit import (
    AUDIT_SAMPLE_FIELDS,
    AuditBundle,
    AuditClassification,
    UnlabeledAuditError,
    build_audit_bundle,
    parse_audit_bundle_json,
    parse_audit_result_json,
    reconcile_audit_result,
    serialize_audit_bundle,
    serialize_audit_reconciliation,
    validate_audit_schema_contracts,
)

COVERED_PATH = PurePosixPath("crates/covered/src/lib.rs")
UNCOVERED_PATH = PurePosixPath("crates/uncovered/src/lib.rs")
AUDIT_SET_ID = "phase-1c-unlabeled-test"


def test_audit_bundle_is_deterministic_blind_and_prioritizes_new_paths(
    tmp_path: Path,
) -> None:
    corpus, scan_result, zed_root = _audit_fixture(tmp_path)

    first = build_audit_bundle(
        corpus,
        scan_result,
        zed_root,
        audit_set_id=AUDIT_SET_ID,
        sample_size=2,
    )
    second = build_audit_bundle(
        corpus,
        scan_result,
        zed_root,
        audit_set_id=AUDIT_SET_ID,
        sample_size=2,
    )
    serialized = serialize_audit_bundle(first)
    payload = json.loads(serialized)

    assert serialized == serialize_audit_bundle(second)
    assert parse_audit_bundle_json(serialized) == first
    assert len(first.occurrences) == 2
    assert {sample.path for sample in first.occurrences} == {UNCOVERED_PATH}
    forbidden = {
        "disposition",
        "prediction",
        "rule_id",
        "evidence",
        "sink_symbol",
        "text_slot",
    }
    for occurrence in payload["occurrences"]:
        assert set(occurrence) == AUDIT_SAMPLE_FIELDS
        assert forbidden.isdisjoint(occurrence)


def test_audit_bundle_uses_all_available_occurrences_when_request_is_larger(
    tmp_path: Path,
) -> None:
    corpus, scan_result, zed_root = _audit_fixture(tmp_path)

    bundle = build_audit_bundle(
        corpus,
        scan_result,
        zed_root,
        audit_set_id=AUDIT_SET_ID,
        sample_size=20,
    )

    assert len(bundle.occurrences) == 3
    assert {sample.occurrence_id for sample in bundle.occurrences} == {
        "covered-unlabeled",
        "uncovered-confirmed",
        "uncovered-review",
    }


def test_audit_result_reconciliation_counts_classes_and_reports_missing(
    tmp_path: Path,
) -> None:
    corpus, scan_result, zed_root = _audit_fixture(tmp_path)
    bundle = build_audit_bundle(
        corpus,
        scan_result,
        zed_root,
        audit_set_id=AUDIT_SET_ID,
        sample_size=3,
    )
    payload = _result_payload(bundle)
    decisions = payload["decisions"]
    assert isinstance(decisions, list)
    decisions.pop()

    report = reconcile_audit_result(
        bundle, parse_audit_result_json(json.dumps(payload))
    )
    serialized = json.loads(serialize_audit_reconciliation(report))

    assert not report.is_complete
    assert len(report.missing_occurrence_ids) == 1
    assert sum(report.classification_counts.values()) == 2
    assert serialized["complete"] is False


def test_audit_result_rejects_unknown_fields_duplicates_and_identity_drift(
    tmp_path: Path,
) -> None:
    corpus, scan_result, zed_root = _audit_fixture(tmp_path)
    bundle = build_audit_bundle(
        corpus,
        scan_result,
        zed_root,
        audit_set_id=AUDIT_SET_ID,
        sample_size=3,
    )
    payload = _result_payload(bundle)
    decisions = payload["decisions"]
    assert isinstance(decisions, list)
    first = decisions[0]
    assert isinstance(first, dict)
    first["prediction"] = "confirmed"
    with pytest.raises(UnlabeledAuditError, match="fields differ"):
        parse_audit_result_json(json.dumps(payload))

    payload = _result_payload(bundle)
    decisions = payload["decisions"]
    assert isinstance(decisions, list)
    first = decisions[0]
    assert isinstance(first, dict)
    decisions.append(dict(first))
    with pytest.raises(UnlabeledAuditError, match="duplicate decision"):
        parse_audit_result_json(json.dumps(payload))

    payload = _result_payload(bundle)
    payload["scan_config_hash"] = "f" * 64
    with pytest.raises(UnlabeledAuditError, match="scan config hash mismatch"):
        reconcile_audit_result(bundle, parse_audit_result_json(json.dumps(payload)))


def test_audit_result_rejects_unknown_occurrence_and_classification(
    tmp_path: Path,
) -> None:
    corpus, scan_result, zed_root = _audit_fixture(tmp_path)
    bundle = build_audit_bundle(
        corpus,
        scan_result,
        zed_root,
        audit_set_id=AUDIT_SET_ID,
        sample_size=3,
    )
    payload = _result_payload(bundle)
    decisions = payload["decisions"]
    assert isinstance(decisions, list)
    first = decisions[0]
    assert isinstance(first, dict)
    first["classification"] = "looks-fine"
    with pytest.raises(UnlabeledAuditError, match="invalid AuditClassification"):
        parse_audit_result_json(json.dumps(payload))

    payload = _result_payload(bundle)
    decisions = payload["decisions"]
    assert isinstance(decisions, list)
    first = decisions[0]
    assert isinstance(first, dict)
    first["occurrence_id"] = "unknown-occurrence"
    with pytest.raises(UnlabeledAuditError, match="unknown occurrence IDs"):
        reconcile_audit_result(bundle, parse_audit_result_json(json.dumps(payload)))


def test_unlabeled_audit_schemas_match_runtime_contracts() -> None:
    validate_audit_schema_contracts(
        schema_resource(UNLABELED_AUDIT_BUNDLE_SCHEMA_NAME),
        schema_resource(UNLABELED_AUDIT_RESULT_SCHEMA_NAME),
    )


def _audit_fixture(tmp_path: Path) -> tuple[GoldenCorpus, ScanResult, Path]:
    zed_root = tmp_path / "zed"
    sources = {
        COVERED_PATH: b'fn covered() { Label::new("labeled"); Label::new("covered"); }\n',
        UNCOVERED_PATH: (
            b'fn uncovered() { Label::new("outside"); Label::new(dynamic); }\n'
        ),
    }
    for path, source in sources.items():
        source_path = zed_root / path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(source)
    _run_git(zed_root, "init")
    _run_git(zed_root, "add", *(path.as_posix() for path in sources))
    _run_git(
        zed_root,
        "-c",
        "user.name=Audit Test",
        "-c",
        "user.email=audit@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    commit = _run_git(zed_root, "rev-parse", "HEAD").stdout.strip()
    labeled_span = _span(sources[COVERED_PATH], b'"labeled"')
    sample = GoldenSample(
        sample_id=f"zed-{commit[:7]}-0001",
        path=COVERED_PATH,
        source_span=labeled_span,
        anchor='"labeled"',
        scope=SourceScope.PRODUCTION,
        subject_kind=SubjectKind.SINK_SLOT,
        sink_symbol="ui::Label::new",
        text_slot="arg[0]",
        sink_kind=SinkKind.VISIBLE_TEXT,
        features=frozenset({Feature.DIRECT_LITERAL}),
        ownership=Ownership.PRODUCT,
        expected_presence=ExpectedPresence.CANDIDATE,
        expected_disposition=Decision.CONFIRMED,
        review_state=ReviewState.SINGLE_REVIEW,
        rationale="fixture",
    )
    corpus = GoldenCorpus(
        CorpusManifest(
            schema_version=2,
            zed_commit=commit,
            sample_file="samples.jsonl",
            sample_count=1,
            sample_sha256="b" * 64,
            source_files_sha256={
                COVERED_PATH: hashlib.sha256(sources[COVERED_PATH]).hexdigest()
            },
            minimum_counts=(),
        ),
        (sample,),
    )
    occurrences = (
        _occurrence("labeled", COVERED_PATH, labeled_span, Decision.CONFIRMED),
        _occurrence(
            "covered-unlabeled",
            COVERED_PATH,
            _span(sources[COVERED_PATH], b'"covered"'),
            Decision.CONFIRMED,
        ),
        _occurrence(
            "uncovered-confirmed",
            UNCOVERED_PATH,
            _span(sources[UNCOVERED_PATH], b'"outside"'),
            Decision.CONFIRMED,
        ),
        _occurrence(
            "uncovered-review",
            UNCOVERED_PATH,
            _span(sources[UNCOVERED_PATH], b"dynamic"),
            Decision.REVIEW_REQUIRED,
            syntax_kind="identifier",
        ),
    )
    scan_result = ScanResult(
        1,
        ScanMetadata(
            zed_commit=commit,
            tool_version="0.1.0",
            rule_pack_version="zed-builtin-v1",
            config_hash="c" * 64,
            scan_scope=tuple(sources),
            source_files=tuple(
                SourceFileSnapshot(path, hashlib.sha256(source).hexdigest())
                for path, source in sources.items()
            ),
            capability_probes=(
                CapabilityProbe(
                    "grammar", CapabilityProbeStatus.PASSED, "fixture passed"
                ),
            ),
        ),
        occurrences,
    )
    return corpus, scan_result, zed_root


def _occurrence(
    occurrence_id: str,
    path: PurePosixPath,
    span: SourceSpan,
    disposition: Decision,
    *,
    syntax_kind: str = "string_literal",
) -> ScanOccurrence:
    return ScanOccurrence(
        occurrence_id=occurrence_id,
        path=path,
        primary_span=span,
        syntax_kind=syntax_kind,
        sink_symbol="ui::Label::new",
        text_slot="arg[0]",
        disposition=disposition,
        provenance=(),
        evidence=(RuleEvidence("ui-label-new-v1", "fixture"),),
    )


def _span(source: bytes, anchor: bytes) -> SourceSpan:
    start = source.index(anchor)
    return SourceSpan(start, start + len(anchor))


def _result_payload(bundle: AuditBundle) -> dict[str, object]:
    return {
        "schema_version": 1,
        "audit_set_id": bundle.audit_set_id,
        "zed_commit": bundle.zed_commit,
        "corpus_sample_sha256": bundle.corpus_sample_sha256,
        "scan_config_hash": bundle.scan_config_hash,
        "reviewer_id": "independent-auditor",
        "decisions": [
            {
                "occurrence_id": sample.occurrence_id,
                "classification": classification.value,
                "rationale": "independent fixture assessment",
            }
            for sample, classification in zip(
                bundle.occurrences,
                AuditClassification,
                strict=False,
            )
        ],
    }


def _run_git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
