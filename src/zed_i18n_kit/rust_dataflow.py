from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from tree_sitter import Node

from .rust_cst import RustCst, iter_named_nodes, source_span_for_node
from .scan_result import ProvenanceRange


@dataclass(frozen=True, slots=True)
class RustDataflowIndex:
    function_results: dict[str, tuple[Node, ...]]
    self_field_initializers: dict[tuple[str, str], tuple[Node, ...]]
    textual_spans: frozenset[tuple[int, int]]


def build_dataflow_index(tree: RustCst) -> RustDataflowIndex:
    definitions: dict[str, list[Node]] = {}
    field_initializers: dict[tuple[str, str], list[Node]] = {}
    for node in iter_named_nodes(tree.root):
        if node.type == "function_item":
            name = node.child_by_field_name("name")
            if name is not None:
                function_name = _node_text(name, tree.source)
                definitions.setdefault(function_name, []).append(node)
        if node.type not in {"field_initializer", "shorthand_field_initializer"}:
            continue
        impl_item = _enclosing_node(node, "impl_item")
        if impl_item is None:
            continue
        self_type = impl_item.child_by_field_name("type")
        if self_type is None:
            continue
        field_name, value = _field_initializer_name_and_value(node, tree.source)
        if field_name is None or value is None:
            continue
        if not _has_local_textual_origin(value, tree):
            continue
        key = (_node_text(self_type, tree.source), field_name)
        field_initializers.setdefault(key, []).append(value)

    function_results = {
        name: _function_result_nodes(items[0])
        for name, items in definitions.items()
        if len(items) == 1
    }
    return RustDataflowIndex(
        function_results=function_results,
        self_field_initializers={
            key: _unique_nodes(values) for key, values in field_initializers.items()
        },
        textual_spans=frozenset(
            (node.start_byte, node.end_byte)
            for node in iter_named_nodes(tree.root)
            if node.type in {"macro_invocation", "raw_string_literal", "string_literal"}
        ),
    )


def collect_provenance(
    tree: RustCst,
    path: PurePosixPath,
    primary: Node,
    *,
    index: RustDataflowIndex | None = None,
) -> tuple[ProvenanceRange, ...]:
    dataflow_index = index if index is not None else build_dataflow_index(tree)
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
        dataflow_index,
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
                    and _pattern_binds_name(pattern, name, source)
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
    index: RustDataflowIndex,
    *,
    record: bool,
) -> None:
    key = (node.start_byte, node.end_byte, node.type)
    if key in visited:
        if record:
            ranges.add(ProvenanceRange(path, source_span_for_node(node)))
        return
    visited.add(key)
    if record:
        ranges.add(ProvenanceRange(path, source_span_for_node(node)))

    if node.type == "identifier":
        for definition in _find_prior_values(node, scope, tree.source):
            _visit_provenance_node(
                tree, path, definition, scope, ranges, visited, index, record=True
            )
        return
    if node.type == "reference_expression":
        value = node.child_by_field_name("value")
        if value is not None:
            _visit_provenance_node(
                tree,
                path,
                value,
                scope,
                ranges,
                visited,
                index,
                record=False,
            )
        return
    if node.type == "field_expression":
        value = node.child_by_field_name("value")
        field = node.child_by_field_name("field")
        if value is None:
            return
        if field is not None:
            projected = _project_local_call_field(
                value,
                _node_text(field, tree.source),
                scope,
                tree,
                index,
            )
            if projected:
                for definition in projected:
                    _visit_provenance_node(
                        tree,
                        path,
                        definition,
                        enclosing_function_or_root(definition, tree.root),
                        ranges,
                        visited,
                        index,
                        record=True,
                    )
                return
            for definition in _find_self_field_initializers(
                node, _node_text(field, tree.source), tree, index
            ):
                _visit_provenance_node(
                    tree,
                    path,
                    definition,
                    enclosing_function_or_root(definition, tree.root),
                    ranges,
                    visited,
                    index,
                    record=True,
                )
        _visit_provenance_node(
            tree, path, value, scope, ranges, visited, index, record=True
        )
        return

    if node.type == "call_expression" and record:
        for result in _local_function_results(node, tree, index):
            _visit_provenance_node(
                tree,
                path,
                result,
                enclosing_function_or_root(result, tree.root),
                ranges,
                visited,
                index,
                record=True,
            )

    for child in node.named_children:
        _visit_provenance_node(
            tree,
            path,
            child,
            scope,
            ranges,
            visited,
            index,
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


def _pattern_binds_name(pattern: Node, name: str, source: bytes) -> bool:
    return any(
        node.type == "identifier" and _node_text(node, source) == name
        for node in iter_named_nodes(pattern)
    )


def _project_local_call_field(
    value: Node,
    field_name: str,
    scope: Node,
    tree: RustCst,
    index: RustDataflowIndex,
) -> tuple[Node, ...]:
    if value.type != "identifier":
        return ()
    binding = find_prior_binding(value, scope, tree.source)
    if binding is None or binding.type != "call_expression":
        return ()
    projected: list[Node] = []
    for result in _local_function_results(binding, tree, index):
        for candidate in iter_named_nodes(result):
            if candidate.type == "shorthand_field_initializer":
                identifier = next(
                    (
                        child
                        for child in candidate.named_children
                        if child.type == "identifier"
                    ),
                    None,
                )
                if (
                    identifier is not None
                    and _node_text(identifier, tree.source) == field_name
                ):
                    projected.append(identifier)
            elif candidate.type == "field_initializer":
                name = candidate.child_by_field_name("field")
                field_value = candidate.child_by_field_name("value")
                if (
                    name is not None
                    and field_value is not None
                    and _node_text(name, tree.source) == field_name
                ):
                    projected.append(field_value)
    return _unique_nodes(projected)


def _find_self_field_initializers(
    field_expression: Node,
    field_name: str,
    tree: RustCst,
    index: RustDataflowIndex,
) -> tuple[Node, ...]:
    receiver = field_expression.child_by_field_name("value")
    if receiver is None or _node_text(receiver, tree.source) != "self":
        return ()
    impl_item = _enclosing_node(field_expression, "impl_item")
    if impl_item is None:
        return ()
    self_type = impl_item.child_by_field_name("type")
    if self_type is None:
        return ()
    return index.self_field_initializers.get(
        (_node_text(self_type, tree.source), field_name), ()
    )


def _local_function_results(
    call: Node, tree: RustCst, index: RustDataflowIndex
) -> tuple[Node, ...]:
    function = call.child_by_field_name("function")
    if function is None:
        return ()
    function_name = _node_text(function, tree.source).rsplit("::", 1)[-1]
    return index.function_results.get(function_name, ())


def _function_result_nodes(definition: Node) -> tuple[Node, ...]:
    body = definition.child_by_field_name("body")
    if body is None or not body.named_children:
        return ()
    results: list[Node] = [body.named_children[-1]]
    for return_expression in iter_named_nodes(body, node_type="return_expression"):
        if return_expression.named_children:
            results.append(return_expression.named_children[-1])
    return _unique_nodes(results)


def _field_initializer_name_and_value(
    initializer: Node, source: bytes
) -> tuple[str | None, Node | None]:
    if initializer.type == "shorthand_field_initializer":
        identifier = next(
            (
                child
                for child in initializer.named_children
                if child.type == "identifier"
            ),
            None,
        )
        return (
            (_node_text(identifier, source), identifier)
            if identifier is not None
            else (None, None)
        )
    name = initializer.child_by_field_name("field")
    value = initializer.child_by_field_name("value")
    return (_node_text(name, source) if name is not None else None, value)


def _has_local_textual_origin(value: Node, tree: RustCst) -> bool:
    textual_types = {"macro_invocation", "raw_string_literal", "string_literal"}
    if any(node.type in textual_types for node in iter_named_nodes(value)):
        return True
    scope = enclosing_function_or_root(value, tree.root)
    for identifier in iter_named_nodes(value, node_type="identifier"):
        binding = find_prior_binding(identifier, scope, tree.source)
        if binding is not None and binding.type in {
            "binary_expression",
            "if_expression",
            "macro_invocation",
            "match_expression",
            "raw_string_literal",
            "string_literal",
        }:
            return True
    return False


def _enclosing_node(node: Node, node_type: str) -> Node | None:
    current: Node | None = node
    while current is not None:
        if current.type == node_type:
            return current
        current = current.parent
    return None


def _unique_nodes(nodes: list[Node]) -> tuple[Node, ...]:
    return tuple(
        sorted(
            {
                (node.start_byte, node.end_byte, node.type): node for node in nodes
            }.values(),
            key=lambda node: (node.start_byte, node.end_byte, node.type),
        )
    )


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
