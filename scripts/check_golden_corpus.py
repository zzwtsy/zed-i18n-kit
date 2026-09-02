import argparse
from pathlib import Path

from zed_i18n_kit.cst_canonical import CanonicalCstError, validate_corpus_cst
from zed_i18n_kit.golden import GoldenCorpusError, load_corpus, validate_checkout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the phase 0 golden corpus")
    parser.add_argument(
        "--corpus",
        type=Path,
        default=Path("corpus/zed-ui-text/v2"),
        help="versioned corpus directory",
    )
    parser.add_argument(
        "--zed",
        type=Path,
        default=Path("local/zed"),
        help="Zed checkout pinned by the corpus manifest",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        corpus = load_corpus(args.corpus)
        validate_checkout(corpus, args.zed)
        validate_corpus_cst(corpus, args.zed)
    except (CanonicalCstError, GoldenCorpusError, OSError, UnicodeError) as error:
        print(f"golden corpus validation failed: {error}")
        return 1

    print(
        f"validated {len(corpus.samples)} samples against "
        f"Zed {corpus.manifest.zed_commit} (schema v{corpus.manifest.schema_version})"
    )
    for dimension, values in sorted(corpus.counts().items()):
        if dimension == "path":
            print(f"path: {len(values)} source files")
            continue
        rendered = ", ".join(
            f"{value}={count}" for value, count in sorted(values.items())
        )
        print(f"{dimension}: {rendered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
