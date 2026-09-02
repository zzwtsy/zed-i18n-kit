import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
DOCUMENTATION_ROOTS = (
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / "AGENTS.md",
    REPOSITORY_ROOT / "docs",
    REPOSITORY_ROOT / ".github",
)


def documentation_files() -> Iterator[Path]:
    for root in DOCUMENTATION_ROOTS:
        if root.is_file():
            yield root
        elif root.is_dir():
            yield from sorted(root.rglob("*.md"))


def local_link_targets(markdown_file: Path) -> Iterator[tuple[str, Path]]:
    content = markdown_file.read_text(encoding="utf-8")
    for match in MARKDOWN_LINK.finditer(content):
        raw_target = match.group(1).strip()
        if raw_target.startswith("<") and raw_target.endswith(">"):
            raw_target = raw_target[1:-1]
        parsed_target = urlsplit(raw_target)
        if parsed_target.scheme or not parsed_target.path:
            continue
        relative_path = Path(unquote(parsed_target.path))
        yield match.group(1), markdown_file.parent / relative_path


def test_relative_documentation_links_resolve() -> None:
    missing_links: list[str] = []
    for markdown_file in documentation_files():
        for raw_target, resolved_target in local_link_targets(markdown_file):
            if not resolved_target.exists():
                source = markdown_file.relative_to(REPOSITORY_ROOT)
                missing_links.append(f"{source}: {raw_target}")

    assert missing_links == []
