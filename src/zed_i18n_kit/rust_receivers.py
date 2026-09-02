from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Node

from .rust_cst import RustCst, iter_named_nodes
from .rust_dataflow import enclosing_function_or_root, find_prior_binding
from .rust_symbols import SourceSymbolTable, SymbolResolutionKind
from .scan_profiles import ReceiverRequirement


@dataclass(frozen=True, slots=True)
class ReceiverEvidence:
    resolution: SymbolResolutionKind
    reason: str


GPUI_ELEMENT_FACTORIES = (
    "gpui::div",
    "ui::Button::new",
    "ui::IconButton::new",
    "ui::Label::new",
    "ui::container",
    "ui::div",
    "ui::h_flex",
    "ui::v_flex",
)

GPUI_ELEMENT_TYPES = (
    "gpui::Div",
    "gpui::IntoElement",
    "gpui::ParentElement",
    "ui::Button",
    "ui::Div",
    "ui::IntoElement",
    "ui::Label",
    "ui::ParentElement",
)

GPUI_WINDOW_TYPES = ("gpui::Window", "ui::Window")


def resolve_receiver(
    receiver: Node,
    requirement: ReceiverRequirement,
    tree: RustCst,
    symbol_table: SourceSymbolTable | None,
) -> ReceiverEvidence | None:
    if requirement is ReceiverRequirement.NONE:
        return ReceiverEvidence(
            SymbolResolutionKind.EXACT, "rule has no receiver requirement"
        )
    if symbol_table is None:
        return None
    scope = enclosing_function_or_root(receiver, tree.root)
    if requirement is ReceiverRequirement.GPUI_WINDOW:
        return _resolve_typed_value(
            receiver,
            tree,
            symbol_table,
            scope,
            value_targets=GPUI_WINDOW_TYPES,
            factory_targets=(),
            visited=set(),
        )
    return _resolve_typed_value(
        receiver,
        tree,
        symbol_table,
        scope,
        value_targets=GPUI_ELEMENT_TYPES,
        factory_targets=GPUI_ELEMENT_FACTORIES,
        visited=set(),
    )


def _resolve_typed_value(
    node: Node,
    tree: RustCst,
    symbol_table: SourceSymbolTable,
    scope: Node,
    *,
    value_targets: tuple[str, ...],
    factory_targets: tuple[str, ...],
    visited: set[tuple[int, int, str]],
) -> ReceiverEvidence | None:
    key = (node.start_byte, node.end_byte, node.type)
    if key in visited:
        return None
    visited.add(key)

    if node.type in {"parenthesized_expression", "reference_expression"}:
        value = node.child_by_field_name("value")
        if value is None and len(node.named_children) == 1:
            value = node.named_children[0]
        if value is not None:
            return _resolve_typed_value(
                value,
                tree,
                symbol_table,
                scope,
                value_targets=value_targets,
                factory_targets=factory_targets,
                visited=visited,
            )
        return None

    if node.type == "call_expression":
        function = node.child_by_field_name("function")
        if function is None:
            return None
        if function.type == "field_expression":
            chained_receiver = function.child_by_field_name("value")
            if chained_receiver is not None:
                return _resolve_typed_value(
                    chained_receiver,
                    tree,
                    symbol_table,
                    scope,
                    value_targets=value_targets,
                    factory_targets=factory_targets,
                    visited=visited,
                )
            return None
        function_text = _node_text(function, tree.source)
        resolution = _resolve_first_target(
            symbol_table, function_text, factory_targets, at=function
        )
        if resolution is not None:
            return ReceiverEvidence(
                resolution.resolution,
                f"receiver factory {function_text!r}: {resolution.reason}",
            )
        return None

    if node.type == "identifier":
        binding = find_prior_binding(node, scope, tree.source)
        if binding is not None:
            resolved_binding = _resolve_typed_value(
                binding,
                tree,
                symbol_table,
                scope,
                value_targets=value_targets,
                factory_targets=factory_targets,
                visited=visited,
            )
            if resolved_binding is not None:
                return ReceiverEvidence(
                    resolved_binding.resolution,
                    f"local binding {_node_text(node, tree.source)!r} -> "
                    f"{resolved_binding.reason}",
                )
        declared_type = _find_declared_type(node, scope, tree.source)
        if declared_type is not None:
            return _resolve_type_node(
                declared_type, tree.source, symbol_table, value_targets
            )
        return None

    return None


def _resolve_first_target(
    symbol_table: SourceSymbolTable,
    observed: str,
    targets: tuple[str, ...],
    *,
    at: Node,
) -> ReceiverEvidence | None:
    candidate: ReceiverEvidence | None = None
    for target in targets:
        resolution = symbol_table.resolve_target(observed, target, at=at)
        if resolution is None:
            continue
        evidence = ReceiverEvidence(
            resolution.kind,
            f"{observed!r} -> {target!r} ({resolution.evidence})",
        )
        if resolution.kind is SymbolResolutionKind.EXACT:
            return evidence
        candidate = evidence
    return candidate


def _resolve_type_node(
    type_node: Node,
    source: bytes,
    symbol_table: SourceSymbolTable,
    targets: tuple[str, ...],
) -> ReceiverEvidence | None:
    type_names = tuple(
        dict.fromkeys(
            (
                _node_text(type_node, source),
                *(
                    _node_text(node, source)
                    for node in iter_named_nodes(type_node)
                    if node.type in {"identifier", "type_identifier"}
                ),
            )
        )
    )
    for type_name in type_names:
        resolution = _resolve_first_target(
            symbol_table, type_name, targets, at=type_node
        )
        if resolution is not None:
            return ReceiverEvidence(
                resolution.resolution,
                f"declared type {type_name!r}: {resolution.reason}",
            )
    return None


def _find_declared_type(identifier: Node, scope: Node, source: bytes) -> Node | None:
    name = _node_text(identifier, source)
    current: Node | None = identifier
    while current is not None:
        sibling = current.prev_named_sibling
        while sibling is not None:
            if sibling.type == "let_declaration":
                pattern = sibling.child_by_field_name("pattern")
                declared_type = sibling.child_by_field_name("type")
                if (
                    pattern is not None
                    and declared_type is not None
                    and _node_text(pattern, source) == name
                ):
                    return declared_type
            sibling = sibling.prev_named_sibling
        if current == scope:
            break
        current = current.parent

    parameters = scope.child_by_field_name("parameters")
    if parameters is None:
        return None
    for parameter in parameters.named_children:
        if parameter.type != "parameter":
            continue
        pattern = parameter.child_by_field_name("pattern")
        declared_type = parameter.child_by_field_name("type")
        if (
            pattern is not None
            and declared_type is not None
            and _node_text(pattern, source) == name
        ):
            return declared_type
    return None


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
