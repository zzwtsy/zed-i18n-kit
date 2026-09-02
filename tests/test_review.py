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
from zed_i18n_kit.review import (
    BUNDLE_SAMPLE_FIELDS,
    ReviewError,
    build_review_bundle,
    parse_review_result_json,
    reconcile_review_result,
    serialize_review_bundle,
    validate_review_schema_contracts,
)
from zed_i18n_kit.schema_resources import (
    REVIEW_BUNDLE_SCHEMA_NAME,
    REVIEW_RESULT_SCHEMA_NAME,
    schema_resource,
)

SOURCE_PATH = PurePosixPath("crates/demo/src/lib.rs")
REVIEW_SET_ID = "phase-1c-baseline"


def test_review_bundle_is_deterministic_blind_and_utf8_safe(tmp_path: Path) -> None:
    corpus, zed_root, source = _corpus_checkout(tmp_path)

    first = build_review_bundle(corpus, zed_root, review_set_id=REVIEW_SET_ID)
    second = build_review_bundle(corpus, zed_root, review_set_id=REVIEW_SET_ID)
    serialized = serialize_review_bundle(first)
    payload = json.loads(serialized)

    assert serialized == serialize_review_bundle(second)
    assert payload["zed_commit"] == corpus.manifest.zed_commit
    assert payload["corpus_sample_sha256"] == corpus.manifest.sample_sha256
    assert len(payload["samples"]) == 3
    forbidden = {
        "expected_presence",
        "expected_disposition",
        "ownership",
        "review_state",
        "rationale",
        "prediction",
        "disposition",
        "evidence",
    }
    for sample_payload, sample in zip(payload["samples"], first.samples, strict=True):
        assert set(sample_payload) == BUNDLE_SAMPLE_FIELDS
        assert forbidden.isdisjoint(sample_payload)
        assert (
            source[
                sample.context_span.start_byte : sample.context_span.end_byte
            ].decode()
            == sample.source_context
        )
        assert sample.anchor in sample.source_context

    context = first.samples[0].source_context
    assert context.startswith("before zero\nbefore one\n")
    assert "after one\nafter two\n" in context
    assert "after three" not in context
    assert "删除" in context


def test_review_result_parser_accepts_strict_complete_payload(tmp_path: Path) -> None:
    corpus, _, _ = _corpus_checkout(tmp_path)
    payload = _result_payload(corpus)

    result = parse_review_result_json(json.dumps(payload))

    assert result.review_set_id == REVIEW_SET_ID
    assert result.reviewer_id == "independent-reviewer"
    assert len(result.decisions) == 3


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("unknown-field", "fields differ"),
        ("invalid-enum", "invalid Decision"),
        ("duplicate-id", "duplicate decision"),
        ("illegal-combination", "candidate must be"),
        ("empty-rationale", "rationale must be a non-empty string"),
    ],
)
def test_review_result_parser_rejects_invalid_evidence(
    tmp_path: Path, mutation: str, message: str
) -> None:
    corpus, _, _ = _corpus_checkout(tmp_path)
    payload = _result_payload(corpus)
    decisions = payload["decisions"]
    assert isinstance(decisions, list)
    first = decisions[0]
    assert isinstance(first, dict)
    if mutation == "unknown-field":
        first["prediction"] = "confirmed"
    elif mutation == "invalid-enum":
        first["expected_disposition"] = "maybe"
    elif mutation == "duplicate-id":
        decisions.append(dict(first))
    elif mutation == "illegal-combination":
        first["expected_presence"] = "candidate"
        first["expected_disposition"] = "excluded"
    else:
        first["rationale"] = "  "

    with pytest.raises(ReviewError, match=message):
        parse_review_result_json(json.dumps(payload))


def test_reconciliation_classifies_agreement_dispute_and_missing(
    tmp_path: Path,
) -> None:
    corpus, _, _ = _corpus_checkout(tmp_path)
    payload = _result_payload(corpus)
    decisions = payload["decisions"]
    assert isinstance(decisions, list)
    disputed = decisions[1]
    assert isinstance(disputed, dict)
    disputed["ownership"] = "mixed"
    decisions.pop()

    report = reconcile_review_result(
        corpus,
        parse_review_result_json(json.dumps(payload)),
        review_set_id=REVIEW_SET_ID,
    )

    assert report.agreed_sample_ids == (corpus.samples[0].sample_id,)
    assert report.disputed_sample_ids == (corpus.samples[1].sample_id,)
    assert report.missing_sample_ids == (corpus.samples[2].sample_id,)
    assert not report.is_complete_agreement


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("review_set_id", "different-review-set", "review set mismatch"),
        ("zed_commit", "f" * 40, "commit mismatch"),
        ("corpus_sample_sha256", "e" * 64, "SHA-256 differs"),
    ],
)
def test_reconciliation_rejects_identity_drift(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    corpus, _, _ = _corpus_checkout(tmp_path)
    payload = _result_payload(corpus)
    payload[field] = value

    with pytest.raises(ReviewError, match=message):
        reconcile_review_result(
            corpus,
            parse_review_result_json(json.dumps(payload)),
            review_set_id=REVIEW_SET_ID,
        )


def test_reconciliation_rejects_unknown_sample_id(tmp_path: Path) -> None:
    corpus, _, _ = _corpus_checkout(tmp_path)
    payload = _result_payload(corpus)
    decisions = payload["decisions"]
    assert isinstance(decisions, list)
    first = decisions[0]
    assert isinstance(first, dict)
    first["sample_id"] = "zed-fffffff-9999"

    with pytest.raises(ReviewError, match="unknown sample IDs"):
        reconcile_review_result(
            corpus,
            parse_review_result_json(json.dumps(payload)),
            review_set_id=REVIEW_SET_ID,
        )


def test_review_schemas_match_runtime_contracts() -> None:
    validate_review_schema_contracts(
        schema_resource(REVIEW_BUNDLE_SCHEMA_NAME),
        schema_resource(REVIEW_RESULT_SCHEMA_NAME),
    )


def _corpus_checkout(tmp_path: Path) -> tuple[GoldenCorpus, Path, bytes]:
    zed_root = tmp_path / "zed"
    source_path = zed_root / SOURCE_PATH
    source_path.parent.mkdir(parents=True)
    source = (
        "before zero\n"
        "before one\n"
        'fn first() { Label::new("删除"); }\n'
        "after one\n"
        "after two\n"
        "after three\n"
        'fn second() { Label::new("Review"); }\n'
        'fn third() { log::info!("internal"); }\n'
    ).encode()
    source_path.write_bytes(source)
    _run_git(zed_root, "init")
    _run_git(zed_root, "add", SOURCE_PATH.as_posix())
    _run_git(
        zed_root,
        "-c",
        "user.name=Review Test",
        "-c",
        "user.email=review@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    commit = _run_git(zed_root, "rev-parse", "HEAD").stdout.strip()
    definitions = (
        ('"删除"', ExpectedPresence.CANDIDATE, Decision.CONFIRMED),
        ('"Review"', ExpectedPresence.CANDIDATE, Decision.REVIEW_REQUIRED),
        ('"internal"', ExpectedPresence.NOT_CANDIDATE, Decision.EXCLUDED),
    )
    samples = []
    for index, (anchor, presence, disposition) in enumerate(definitions, start=1):
        anchor_bytes = anchor.encode()
        start = source.index(anchor_bytes)
        samples.append(
            GoldenSample(
                sample_id=f"zed-{commit[:7]}-{index:04d}",
                path=SOURCE_PATH,
                source_span=SourceSpan(start, start + len(anchor_bytes)),
                anchor=anchor,
                scope=SourceScope.PRODUCTION,
                subject_kind=SubjectKind.SINK_SLOT,
                sink_symbol=("ui::Label::new" if index < 3 else "tracing::info"),
                text_slot="arg[0]",
                sink_kind=(SinkKind.VISIBLE_TEXT if index < 3 else SinkKind.NONE),
                features=frozenset(
                    {
                        Feature.DIRECT_LITERAL,
                        *(
                            (Feature.LOG_OR_DIAGNOSTIC,)
                            if index == 3
                            else (Feature.BUILDER_ARGUMENT,)
                        ),
                    }
                ),
                ownership=(Ownership.PRODUCT if index < 3 else Ownership.DEVELOPER),
                expected_presence=presence,
                expected_disposition=disposition,
                review_state=ReviewState.SINGLE_REVIEW,
                rationale="fixture label that must not enter the bundle",
            )
        )
    manifest = CorpusManifest(
        schema_version=2,
        zed_commit=commit,
        sample_file="samples.jsonl",
        sample_count=len(samples),
        sample_sha256="c" * 64,
        source_files_sha256={SOURCE_PATH: hashlib.sha256(source).hexdigest()},
        minimum_counts=(),
    )
    return GoldenCorpus(manifest, tuple(samples)), zed_root, source


def _result_payload(corpus: GoldenCorpus) -> dict[str, object]:
    return {
        "schema_version": 1,
        "review_set_id": REVIEW_SET_ID,
        "zed_commit": corpus.manifest.zed_commit,
        "corpus_sample_sha256": corpus.manifest.sample_sha256,
        "reviewer_id": "independent-reviewer",
        "decisions": [
            {
                "sample_id": sample.sample_id,
                "expected_presence": sample.expected_presence.value,
                "expected_disposition": sample.expected_disposition.value,
                "ownership": sample.ownership.value,
                "rationale": "independent assessment",
            }
            for sample in corpus.samples
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
