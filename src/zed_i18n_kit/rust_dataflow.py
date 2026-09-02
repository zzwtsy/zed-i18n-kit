from __future__ import annotations

from pathlib import PurePosixPath

from tree_sitter import Node

from .rust_cst import RustCst, iter_named_nodes, source_span_for_node
from .scan_result import ProvenanceRange


def collect_provenance(
    tree: RustCst, path: PurePosixPath, primary: Node
) -> tuple[ProvenanceRange, ...]:
    ranges: set[ProvenanceRange] = set()
    visited: set[tuple[int, int, str]] = set()
    scope = enclosing_function_or_root(primary, tree.root)
    _visit_provenance_node(
        tree,
        path,
        primary,
        scope,
        ranges,
        visited,
        record=True,
    )
    return tuple(sorted(ranges))


def find_prior_binding(identifier: Node, scope: Node, source: bytes) -> Node | None:
    name = _node_text(identifier, source)
    current: Node | None = identifier
    while current is not None:
        sibling = current.prev_named_sibling
        while sibling is not None:
            if sibling.type == "let_declaration":
                pattern = sibling.child_by_field_name("pattern")
                value = sibling.child_by_field_name("value")
                if (
                    pattern is not None
                    and value is not None
                    and _node_text(pattern, source) == name
                ):
                    return value
            sibling = sibling.prev_named_sibling
        if current == scope:
            break
        current = current.parent
    return None


def enclosing_function_or_root(node: Node, root: Node) -> Node:
    current: Node | None = node
    while current is not None:
        if current.type == "function_item":
            return current
        current = current.parent
    return root


def _visit_provenance_node(
    tree: RustCst,
    path: PurePosixPath,
    node: Node,
    scope: Node,
    ranges: set[ProvenanceRange],
    visited: set[tuple[int, int, str]],
    *,
    record: bool,
) -> None:
    key = (node.start_byte, node.end_byte, node.type)
    if key in visited:
        return
    visited.add(key)
    if record:
        ranges.add(ProvenanceRange(path, source_span_for_node(node)))

    if node.type == "identifier":
        for definition in _find_prior_values(node, scope, tree.source):
            _visit_provenance_node(
                tree, path, definition, scope, ranges, visited, record=True
            )
        return
    if node.type in {"reference_expression", "field_expression"}:
        value = node.child_by_field_name("value")
        if value is not None:
            _visit_provenance_node(
                tree, path, value, scope, ranges, visited, record=False
            )
        return

    for child in node.named_children:
        _visit_provenance_node(
            tree,
            path,
            child,
            scope,
            ranges,
            visited,
            record=child.type
            in {"macro_invocation", "string_literal", "raw_string_literal"},
        )


def _find_prior_values(
    identifier: Node, scope: Node, source: bytes
) -> tuple[Node, ...]:
    name = _node_text(identifier, source)
    binding = find_prior_binding(identifier, scope, source)
    values: list[Node] = [binding] if binding is not None else []
    lower_bound = binding.start_byte if binding is not None else scope.start_byte

    for assignment in iter_named_nodes(scope, node_type="assignment_expression"):
        if enclosing_function_or_root(assignment, scope) != scope or not (
            lower_bound < assignment.start_byte < identifier.start_byte
        ):
            continue
        left = assignment.child_by_field_name("left")
        right = assignment.child_by_field_name("right")
        if left is not None and right is not None and _node_text(left, source) == name:
            values.append(right)

    for call in iter_named_nodes(scope, node_type="call_expression"):
        if enclosing_function_or_root(call, scope) != scope or not (
            lower_bound < call.start_byte < identifier.start_byte
        ):
            continue
        function = call.child_by_field_name("function")
        arguments = call.child_by_field_name("arguments")
        if function is None or arguments is None or function.type != "field_expression":
            continue
        receiver = function.child_by_field_name("value")
        method = function.child_by_field_name("field")
        if (
            receiver is not None
            and method is not None
            and _node_text(receiver, source) == name
            and _node_text(method, source) == "push_str"
            and arguments.named_children
        ):
            values.append(arguments.named_children[0])

    return tuple(
        sorted(
            {
                (value.start_byte, value.end_byte, value.type): value
                for value in values
            }.values(),
            key=lambda value: (value.start_byte, value.end_byte, value.type),
        )
    )


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
