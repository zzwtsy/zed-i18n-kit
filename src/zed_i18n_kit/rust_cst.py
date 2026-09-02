from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import tree_sitter_rust
from tree_sitter import Language, Node, Parser, Tree

from .golden import SourceSpan


class RustCstError(ValueError):
    """Raised when Rust source cannot satisfy the CST contract."""


@dataclass(frozen=True, slots=True)
class RustCst:
    source: bytes
    tree: Tree

    @property
    def root(self) -> Node:
        return self.tree.root_node

    @property
    def has_errors(self) -> bool:
        return self.root.has_error

    def text(self, node: Node) -> bytes:
        return self.source[node.start_byte : node.end_byte]

    def exact_named_node(self, span: SourceSpan) -> Node | None:
        node = self.root.named_descendant_for_byte_range(span.start_byte, span.end_byte)
        while node is not None:
            if node.start_byte == span.start_byte and node.end_byte == span.end_byte:
                return node
            node = node.parent
        return None

    def smallest_named_node_containing(self, span: SourceSpan) -> Node | None:
        node = self.root.named_descendant_for_byte_range(span.start_byte, span.end_byte)
        while node is not None:
            if node.start_byte <= span.start_byte and node.end_byte >= span.end_byte:
                return node
            node = node.parent
        return None


def parse_rust_cst(source: bytes) -> RustCst:
    try:
        language = Language(tree_sitter_rust.language())
        parser = Parser(language)
        tree = parser.parse(source)
    except (TypeError, ValueError) as error:
        raise RustCstError(
            f"cannot initialize Rust Tree-sitter parser: {error}"
        ) from error
    if tree.root_node.type != "source_file":
        raise RustCstError(
            f"Rust parser returned unexpected root node {tree.root_node.type!r}"
        )
    return RustCst(source=source, tree=tree)


def iter_named_nodes(node: Node, *, node_type: str | None = None) -> Iterator[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        if node_type is None or current.type == node_type:
            yield current
        stack.extend(reversed(current.named_children))


def collect_parse_error_nodes(tree: RustCst) -> tuple[Node, ...]:
    if not tree.has_errors:
        return ()
    found: list[Node] = []
    stack = [tree.root]
    while stack:
        node = stack.pop()
        if node.is_error or node.is_missing:
            found.append(node)
            continue
        stack.extend(
            child
            for child in reversed(node.named_children)
            if child.has_error or child.is_error or child.is_missing
        )
    return tuple(found)


def is_within_parse_error(node: Node) -> bool:
    current: Node | None = node
    while current is not None:
        if current.is_error or current.is_missing:
            return True
        current = current.parent
    return False


def is_within_test_scope(node: Node, source: bytes) -> bool:
    current: Node | None = node
    while current is not None:
        if current.type == "mod_item":
            name = current.child_by_field_name("name")
            if name is not None and _node_text(name, source) == "tests":
                return True
        for attribute in _attached_attributes(current):
            if _attribute_requires_test(attribute, source):
                return True
        current = current.parent
    return False


def source_span_for_node(node: Node) -> SourceSpan:
    return SourceSpan(start_byte=node.start_byte, end_byte=node.end_byte)


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _attached_attributes(node: Node) -> Iterator[Node]:
    sibling = node.prev_named_sibling
    while sibling is not None and sibling.type in {
        "attribute_item",
        "inner_attribute_item",
    }:
        yield sibling
        sibling = sibling.prev_named_sibling


def _attribute_requires_test(attribute_item: Node, source: bytes) -> bool:
    if not attribute_item.named_children:
        return False
    attribute = attribute_item.named_children[0]
    if attribute.type != "attribute" or not attribute.named_children:
        return False
    name = attribute.named_children[0]
    attribute_name = _node_text(name, source)
    if attribute_name == "test":
        return True
    if attribute_name != "cfg":
        return False
    arguments = attribute.child_by_field_name("arguments")
    if arguments is None:
        return False
    return True not in _cfg_possible_values_outside_test_scope(
        arguments.named_children, source
    )


def _cfg_possible_values_outside_test_scope(
    expression: Sequence[Node], source: bytes
) -> frozenset[bool]:
    if len(expression) == 1 and expression[0].type == "identifier":
        if _node_text(expression[0], source) == "test":
            return frozenset({False})
        return frozenset({False, True})

    if (
        len(expression) == 2
        and expression[0].type == "identifier"
        and expression[1].type == "string_literal"
        and _node_text(expression[0], source) == "feature"
        and _node_text(expression[1], source) == '"test-support"'
    ):
        return frozenset({False})

    if (
        len(expression) == 2
        and expression[0].type == "identifier"
        and expression[1].type == "token_tree"
    ):
        operator = _node_text(expression[0], source)
        operands = tuple(
            _cfg_possible_values_outside_test_scope(operand, source)
            for operand in _split_cfg_operands(expression[1])
        )
        if operator == "all" and operands:
            return _combine_cfg_all(operands)
        if operator == "any" and operands:
            return _combine_cfg_any(operands)
        if operator == "not" and len(operands) == 1:
            return frozenset(not value for value in operands[0])

    return frozenset({False, True})


def _split_cfg_operands(token_tree: Node) -> tuple[tuple[Node, ...], ...]:
    operands: list[tuple[Node, ...]] = []
    current: list[Node] = []
    for child in token_tree.children:
        if child.type == ",":
            if current:
                operands.append(tuple(current))
                current = []
        elif child.is_named:
            current.append(child)
    if current:
        operands.append(tuple(current))
    return tuple(operands)


def _combine_cfg_all(operands: tuple[frozenset[bool], ...]) -> frozenset[bool]:
    possible: set[bool] = set()
    if all(True in operand for operand in operands):
        possible.add(True)
    if any(False in operand for operand in operands):
        possible.add(False)
    return frozenset(possible)


def _combine_cfg_any(operands: tuple[frozenset[bool], ...]) -> frozenset[bool]:
    possible: set[bool] = set()
    if any(True in operand for operand in operands):
        possible.add(True)
    if all(False in operand for operand in operands):
        possible.add(False)
    return frozenset(possible)
