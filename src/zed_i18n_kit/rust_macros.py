from __future__ import annotations

from dataclasses import dataclass

from .golden import SourceSpan
from .rust_cst import (
    RustCst,
    collect_parse_error_nodes,
    iter_named_nodes,
    parse_rust_cst,
)

ALLOWLISTED_EXPRESSION_MACROS = frozenset({"maybe", "vec"})
_MACRO_OPENING_DELIMITERS = {
    "maybe": frozenset({b"(", b"{"}),
    "vec": frozenset({b"["}),
}


@dataclass(frozen=True, slots=True)
class ExpandedExpressionMacros:
    """A same-offset secondary CST for allowlisted expression macros."""

    tree: RustCst
    ranges: tuple[SourceSpan, ...]
    parse_error_ranges: tuple[SourceSpan, ...]


def parse_allowlisted_expression_macros(
    tree: RustCst,
) -> ExpandedExpressionMacros | None:
    """Reparse allowlisted macro bodies without changing source byte offsets.

    Tree-sitter exposes Rust macro bodies as opaque token trees. For an
    expression macro such as ``vec![...]``, replacing the ``vec!`` prefix with
    the same number of spaces leaves a valid array expression. Parsing that
    same-length source recovers the inner Rust CST while every byte range still
    maps directly to the original UTF-8 source.
    """

    rewritten = bytearray(tree.source)
    ranges: list[SourceSpan] = []
    for invocation in iter_named_nodes(tree.root, node_type="macro_invocation"):
        macro = invocation.child_by_field_name("macro")
        token_tree = next(
            (
                child
                for child in invocation.named_children
                if child.type == "token_tree"
            ),
            None,
        )
        if macro is None or token_tree is None:
            continue
        macro_name = (
            tree.text(macro).decode("utf-8", errors="replace").rsplit("::", 1)[-1]
        )
        if macro_name not in ALLOWLISTED_EXPRESSION_MACROS:
            continue
        if (
            tree.source[token_tree.start_byte : token_tree.start_byte + 1]
            not in _MACRO_OPENING_DELIMITERS[macro_name]
        ):
            continue
        for index in range(invocation.start_byte, token_tree.start_byte):
            if rewritten[index] not in {10, 13}:
                rewritten[index] = 32
        ranges.append(SourceSpan(token_tree.start_byte, token_tree.end_byte))

    if not ranges:
        return None

    parsed = parse_rust_cst(bytes(rewritten))
    # Nodes use coordinates from the same-length rewritten buffer. Consumers
    # must still read symbol and evidence text from the original source.
    mapped = RustCst(source=tree.source, tree=parsed.tree)
    parse_error_ranges = tuple(
        SourceSpan(node.start_byte, node.end_byte)
        for node in collect_parse_error_nodes(mapped)
        if any(_contains(span, node.start_byte, node.end_byte) for span in ranges)
    )
    return ExpandedExpressionMacros(
        tree=mapped,
        ranges=tuple(sorted(set(ranges))),
        parse_error_ranges=parse_error_ranges,
    )


def node_is_within_expanded_range(
    start_byte: int, end_byte: int, ranges: tuple[SourceSpan, ...]
) -> bool:
    return any(_contains(span, start_byte, end_byte) for span in ranges)


def span_is_within_expanded_range(
    span: SourceSpan, ranges: tuple[SourceSpan, ...]
) -> bool:
    return any(
        expanded.start_byte <= span.start_byte and expanded.end_byte >= span.end_byte
        for expanded in ranges
    )


def _contains(span: SourceSpan, start_byte: int, end_byte: int) -> bool:
    return span.start_byte <= start_byte and span.end_byte >= end_byte
