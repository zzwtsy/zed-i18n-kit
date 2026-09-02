import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from zed_i18n_kit.golden import (
    GoldenCorpusError,
    load_corpus,
    validate_checkout,
    validate_schema_contract,
)
from zed_i18n_kit.schema_resources import (
    GOLDEN_CORPUS_SCHEMA_NAME,
    schema_resource,
)

PROJECT_ROOT = Path(__file__).parents[1]
CORPUS_DIR = PROJECT_ROOT / "corpus/zed-ui-text/v2"
SCHEMA_RESOURCE = schema_resource(GOLDEN_CORPUS_SCHEMA_NAME)


def test_repository_corpus_has_baseline_and_risk_boundaries() -> None:
    corpus = load_corpus(CORPUS_DIR)

    assert corpus.manifest.schema_version == 2
    assert corpus.manifest.zed_commit == "2551721adb5b5187bc27cfae0fbe47f0ed4c5397"
    assert len(corpus.samples) == 266
    assert corpus.counts()["expected_disposition"] == {
        "confirmed": 149,
        "review_required": 46,
        "excluded": 71,
    }
    assert corpus.counts()["expected_presence"] == {
        "candidate": 195,
        "not_candidate": 71,
    }
    assert corpus.counts()["ownership"]["user"] == 2
    assert corpus.counts()["ownership"]["protocol"] == 12
    assert corpus.counts()["feature"]["concatenation"] == 2

    samples = {sample.sample_id: sample for sample in corpus.samples}
    assert "\n" in samples["zed-2551721-0260"].anchor
    assert samples["zed-2551721-0255"].text_slot == "arg[3][0]"
    assert samples["zed-2551721-0256"].text_slot == "arg[3][1]"
    assert samples["zed-2551721-0257"].text_slot == "arg[3][2]"


def test_schema_matches_runtime_contract() -> None:
    validate_schema_contract(SCHEMA_RESOURCE)


def test_schema_drift_is_rejected(tmp_path: Path) -> None:
    schema: dict[str, object] = json.loads(SCHEMA_RESOURCE.read_text(encoding="utf-8"))
    required = schema["required"]
    assert isinstance(required, list)
    required.remove("review_state")
    drifted_schema = tmp_path / "schema.json"
    drifted_schema.write_text(json.dumps(schema), encoding="utf-8")

    with pytest.raises(GoldenCorpusError, match="required fields drifted"):
        validate_schema_contract(drifted_schema)


def test_loader_rejects_invalid_presence_disposition_pair(tmp_path: Path) -> None:
    sample = _valid_sample()
    sample["expected_presence"] = "candidate"
    sample["expected_disposition"] = "excluded"
    corpus_dir = _write_corpus(tmp_path, sample)

    with pytest.raises(GoldenCorpusError, match="candidate must be confirmed"):
        load_corpus(corpus_dir)


def test_loader_rejects_schema_version_1(tmp_path: Path) -> None:
    corpus_dir = _write_corpus(tmp_path, _valid_sample())
    manifest_path = corpus_dir / "manifest.json"
    manifest: dict[str, object] = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(GoldenCorpusError, match="unsupported schema_version 1"):
        load_corpus(corpus_dir)


def test_checkout_validates_relevant_file_hash_and_exact_span(
    tmp_path: Path,
) -> None:
    zed_root = tmp_path / "zed"
    source_path = zed_root / "crates/demo/src/lib.rs"
    source_path.parent.mkdir(parents=True)
    source_bytes = 'fn render() { Label::new("删除"); }\n'.encode()
    source_path.write_bytes(source_bytes)
    _run_git(zed_root, "init")
    _run_git(zed_root, "add", "crates/demo/src/lib.rs")
    _run_git(
        zed_root,
        "-c",
        "user.name=Corpus Test",
        "-c",
        "user.email=corpus@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    commit = _run_git(zed_root, "rev-parse", "HEAD").stdout.strip()
    anchor = '"删除"'
    start_byte = source_bytes.index(anchor.encode())
    sample = _valid_sample()
    sample["id"] = f"zed-{commit[:7]}-0001"
    sample["anchor"] = anchor
    sample["source_span"] = {
        "start_byte": start_byte,
        "end_byte": start_byte + len(anchor.encode()),
    }
    corpus_dir = _write_corpus(
        tmp_path / "data",
        sample,
        commit=commit,
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
    )

    validate_checkout(load_corpus(corpus_dir), zed_root)

    source_path.write_bytes(source_bytes.replace("删除".encode(), "保留".encode()))
    with pytest.raises(GoldenCorpusError, match="source SHA-256 mismatch"):
        validate_checkout(load_corpus(corpus_dir), zed_root)


def _valid_sample() -> dict[str, object]:
    return {
        "id": "zed-aaaaaaa-0001",
        "path": "crates/demo/src/lib.rs",
        "source_span": {"start_byte": 25, "end_byte": 33},
        "anchor": '"Delete"',
        "scope": "production",
        "subject_kind": "sink_slot",
        "sink_symbol": "ui::Label::new",
        "text_slot": "arg[0]",
        "sink_kind": "visible_text",
        "features": ["direct_literal", "builder_argument"],
        "ownership": "product",
        "expected_presence": "candidate",
        "expected_disposition": "confirmed",
        "review_state": "single_review",
        "rationale": "fixture",
    }


def _write_corpus(
    parent: Path,
    sample: dict[str, object],
    *,
    commit: str = "a" * 40,
    source_sha256: str = "b" * 64,
) -> Path:
    corpus_dir = parent / "corpus"
    corpus_dir.mkdir(parents=True)
    sample_bytes = (
        json.dumps(sample, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode()
    (corpus_dir / "samples.jsonl").write_bytes(sample_bytes)
    manifest = {
        "schema_version": 2,
        "zed_commit": commit,
        "sample_file": "samples.jsonl",
        "sample_count": 1,
        "sample_sha256": hashlib.sha256(sample_bytes).hexdigest(),
        "source_files_sha256": {"crates/demo/src/lib.rs": source_sha256},
        "minimum_counts": {},
    }
    (corpus_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return corpus_dir


def _run_git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
