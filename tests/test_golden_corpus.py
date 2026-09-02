import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from zed_i18n_kit.golden import GoldenCorpusError, load_corpus, validate_checkout

PROJECT_ROOT = Path(__file__).parents[1]
CORPUS_DIR = PROJECT_ROOT / "corpus/zed-ui-text/v1"


def test_repository_corpus_has_fixed_size_and_required_boundaries() -> None:
    corpus = load_corpus(CORPUS_DIR)

    assert corpus.manifest.zed_commit == "2551721adb5b5187bc27cfae0fbe47f0ed4c5397"
    assert len(corpus.samples) == 250
    assert corpus.counts()["decision"] == {
        "confirmed": 140,
        "review_required": 50,
        "excluded": 60,
    }
    assert corpus.counts()["scope"] == {
        "production": 220,
        "test": 10,
        "component_preview": 10,
        "example": 10,
    }


def test_loader_rejects_unknown_sample_fields(tmp_path: Path) -> None:
    corpus_dir = _write_corpus(
        tmp_path,
        [_valid_sample() | {"unexpected": True}],
    )

    with pytest.raises(GoldenCorpusError, match=r"unknown=\['unexpected'\]"):
        load_corpus(corpus_dir)


def test_loader_rejects_sample_content_not_recorded_by_manifest(tmp_path: Path) -> None:
    corpus_dir = _write_corpus(tmp_path, [_valid_sample()])
    sample_path = corpus_dir / "samples.jsonl"
    sample_path.write_text(
        sample_path.read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )

    with pytest.raises(GoldenCorpusError, match="SHA-256 mismatch"):
        load_corpus(corpus_dir)


def test_checkout_validation_checks_commit_and_source_anchor(tmp_path: Path) -> None:
    zed_root = tmp_path / "zed"
    source_path = zed_root / "crates/demo/src/lib.rs"
    source_path.parent.mkdir(parents=True)
    source_path.write_text('fn render() { Label::new("Delete"); }\n', encoding="utf-8")
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
    sample = _valid_sample()
    sample["id"] = f"zed-{commit[:7]}-0001"
    corpus_dir = _write_corpus(tmp_path / "data", [sample], commit=commit)

    validate_checkout(load_corpus(corpus_dir), zed_root)

    source_path.write_text('fn render() { Label::new("Keep"); }\n', encoding="utf-8")
    with pytest.raises(GoldenCorpusError, match=r"anchor .* not found"):
        validate_checkout(load_corpus(corpus_dir), zed_root)


def _valid_sample() -> dict[str, object]:
    return {
        "id": "zed-2551721-0001",
        "path": "crates/demo/src/lib.rs",
        "line": 1,
        "anchor": '"Delete"',
        "scope": "production",
        "sink_symbol": "ui::Label::new[0]",
        "sink_kind": "visible_text",
        "features": ["direct_literal", "builder_argument"],
        "ownership": "product",
        "decision": "confirmed",
        "rationale": "fixture",
    }


def _write_corpus(
    parent: Path,
    samples: list[dict[str, object]],
    *,
    commit: str = "a" * 40,
) -> Path:
    corpus_dir = parent / "corpus"
    corpus_dir.mkdir(parents=True)
    sample_bytes = (
        "\n".join(
            json.dumps(sample, ensure_ascii=False, separators=(",", ":"))
            for sample in samples
        )
        + "\n"
    ).encode()
    (corpus_dir / "samples.jsonl").write_bytes(sample_bytes)
    manifest = {
        "schema_version": 1,
        "zed_commit": commit,
        "sample_file": "samples.jsonl",
        "sample_count": len(samples),
        "sample_sha256": hashlib.sha256(sample_bytes).hexdigest(),
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
