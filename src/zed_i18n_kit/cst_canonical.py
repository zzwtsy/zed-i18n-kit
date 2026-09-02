from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from tree_sitter import Node

from .golden import GoldenCorpus, GoldenSample, SourceSpan, SubjectKind
from .rust_cst import (
    RustCst,
    RustCstError,
    is_within_parse_error,
    is_within_test_scope,
    iter_named_nodes,
    parse_rust_cst,
    source_span_for_node,
)
from .rust_macros import (
    parse_allowlisted_expression_macros,
    span_is_within_expanded_range,
)
from .rust_receivers import resolve_receiver
from .rust_symbols import (
    SourceSymbolTable,
    SymbolResolutionKind,
    build_controlled_export_index,
    build_source_symbol_table,
)
from .scan_profiles import ReceiverRequirement


class CanonicalCstError(ValueError):
    """Raised when a corpus span is not a semantic canonical CST span."""


@dataclass(frozen=True, slots=True)
class CanonicalCstResult:
    """The semantic CST node required by one corpus sample."""

    sample_id: str
    path: PurePosixPath
    subject_kind: SubjectKind
    node_kind: str
    source_span: SourceSpan


@dataclass(frozen=True, slots=True)
class _SlotCandidate:
    call: Node
    value: Node


_ORIGIN_NODE_TYPES = frozenset(
    {
        "array_expression",
        "await_expression",
        "binary_expression",
        "boolean_literal",
        "char_literal",
        "call_expression",
        "closure_expression",
        "field_expression",
        "float_literal",
        "identifier",
        "if_expression",
        "index_expression",
        "integer_literal",
        "macro_invocation",
        "match_expression",
        "parenthesized_expression",
        "raw_string_literal",
        "reference_expression",
        "scoped_identifier",
        "string_literal",
        "struct_expression",
        "tuple_expression",
        "unary_expression",
    }
)
_INVALID_CONTAINER_TYPES = frozenset(
    {
        "arguments",
        "expression_statement",
        "let_declaration",
        "source_file",
        "token_tree",
    }
)
_SLOT_PATTERN = re.compile(r"^arg\[(?P<argument>\d+)](?P<tail>.*)$")
_ARRAY_PATTERN = re.compile(r"^\[(?P<index>\d+)]$")
_OPAQUE_WRAPPER_MACROS = frozenset({"maybe"})


def validate_corpus_cst(
    corpus: GoldenCorpus, zed_root: Path
) -> tuple[CanonicalCstResult, ...]:
    """Validate every corpus span against its subject-specific CST contract.

    This check deliberately does not use an arbitrary ``named_descendant`` as
    proof of correctness.  A sink sample must be the value node at the declared
    call slot, an origin sample must be an actual source expression, and an
    exclusion must be an exact node in its declared non-production scope.
    """

    export_index = build_controlled_export_index(zed_root)
    parsed_sources: dict[
        PurePosixPath,
        tuple[RustCst, SourceSymbolTable, RustCst | None, tuple[SourceSpan, ...]],
    ] = {}
    results: list[CanonicalCstResult] = []
    failures: list[str] = []
    for sample in corpus.samples:
        try:
            parsed = parsed_sources.get(sample.path)
            if parsed is None:
                tree = _read_source(zed_root, sample.path)
                expanded = parse_allowlisted_expression_macros(tree)
                parsed = (
                    tree,
                    build_source_symbol_table(
                        tree, sample.path, export_index=export_index
                    ),
                    expanded.tree if expanded is not None else None,
                    expanded.ranges if expanded is not None else (),
                )
                parsed_sources[sample.path] = parsed
            tree, symbol_table, expanded_tree, expanded_ranges = parsed
            sample_tree = (
                expanded_tree
                if expanded_tree is not None
                and span_is_within_expanded_range(sample.source_span, expanded_ranges)
                else tree
            )
            result = _validate_sample(sample, sample_tree, symbol_table)
        except CanonicalCstError as error:
            failures.append(str(error))
        else:
            results.append(result)

    if failures:
        rendered = "\n".join(f"- {failure}" for failure in failures)
        raise CanonicalCstError(
            f"canonical CST validation failed for {len(failures)} samples:\n{rendered}"
        )
    return tuple(results)


def _read_source(zed_root: Path, path: PurePosixPath) -> RustCst:
    try:
        source = (zed_root / path).read_bytes()
    except OSError as error:
        raise CanonicalCstError(f"{path}: cannot read source: {error}") from error
    try:
        return parse_rust_cst(source)
    except RustCstError as error:
        raise CanonicalCstError(f"{path}: cannot parse Rust source: {error}") from error


def _validate_sample(
    sample: GoldenSample, tree: RustCst, symbol_table: SourceSymbolTable
) -> CanonicalCstResult:
    _validate_sample_span(sample, tree.source)
    if sample.subject_kind is SubjectKind.SINK_SLOT:
        node = _canonical_sink_slot(sample, tree, symbol_table)
    elif sample.subject_kind is SubjectKind.EXPRESSION_ORIGIN:
        node = _canonical_origin(sample, tree, symbol_table)
    else:
        node = _canonical_scope_exclusion(sample, tree)

    if node is None:
        raise CanonicalCstError(
            f"{sample.sample_id}: cannot identify a canonical {sample.subject_kind.value}"
        )
    if is_within_parse_error(node):
        raise CanonicalCstError(
            f"{sample.sample_id}: canonical node {node.type} at "
            f"{node.start_byte}..{node.end_byte} is inside a parse error"
        )
    if source_span_for_node(node) != sample.source_span:
        raise CanonicalCstError(
            f"{sample.sample_id}: non-canonical span {sample.source_span.start_byte}.."
            f"{sample.source_span.end_byte}; expected {node.type} "
            f"{node.start_byte}..{node.end_byte}"
        )
    return CanonicalCstResult(
        sample_id=sample.sample_id,
        path=sample.path,
        subject_kind=sample.subject_kind,
        node_kind=node.type,
        source_span=source_span_for_node(node),
    )


def _canonical_sink_slot(
    sample: GoldenSample, tree: RustCst, symbol_table: SourceSymbolTable
) -> Node | None:
    if sample.sink_symbol is None or sample.text_slot is None:
        return None
    candidates: list[_SlotCandidate] = []
    for call in iter_named_nodes(tree.root, node_type="call_expression"):
        if _ranges_overlap(call, sample.source_span) and _call_matches_symbol(
            call, sample.sink_symbol, tree, symbol_table, allow_candidate=True
        ):
            value = _slot_value(call, sample.text_slot, tree.source)
            if value is not None:
                candidates.append(_SlotCandidate(call, value))
    selected = _select_overlapping_candidate(
        candidates,
        sample.source_span,
    )
    if selected is None:
        return None
    value = selected.value
    if (
        value.type in _INVALID_CONTAINER_TYPES
        or _is_non_value_node(value)
        or not _is_expression_node(value)
        or not _node_matches_scope(sample, value, tree)
    ):
        return None
    return value


def _slot_value(call: Node, text_slot: str, source: bytes) -> Node | None:
    match = _SLOT_PATTERN.fullmatch(text_slot)
    if match is None:
        return None
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return None
    values = arguments.named_children
    argument_index = int(match.group("argument"))
    if argument_index >= len(values):
        return None
    value = values[argument_index]
    tail = match.group("tail")
    if tail == ".Some":
        if value.type != "call_expression":
            return None
        function = value.child_by_field_name("function")
        nested_arguments = value.child_by_field_name("arguments")
        if (
            function is None
            or nested_arguments is None
            or _node_text(function, source) != "Some"
            or len(nested_arguments.named_children) != 1
        ):
            return None
        if len(nested_arguments.named_children) != 1:
            return None
        return nested_arguments.named_children[0]
    array_match = _ARRAY_PATTERN.fullmatch(tail)
    if array_match is None:
        return value if not tail else None
    index = int(array_match.group("index"))
    if value.type == "reference_expression":
        referenced = value.child_by_field_name("value")
        if referenced is not None:
            value = referenced
    if value.type != "array_expression":
        return None
    items = value.named_children
    return items[index] if index < len(items) else None


def _canonical_origin(
    sample: GoldenSample, tree: RustCst, symbol_table: SourceSymbolTable
) -> Node | None:
    exact = tree.exact_named_node(sample.source_span)
    if (
        exact is not None
        and _is_valid_origin_node(exact, sample, tree, symbol_table)
        and _origin_matches_sink_constraint(exact, sample, tree, symbol_table)
    ):
        if _node_matches_scope(sample, exact, tree):
            return exact

    # If the recorded range is an enclosing sink expression, recover the
    # value at the declared slot.  A valid exact origin above still wins: an
    # origin may be nested in a format or concatenation expression and need
    # not equal the complete sink argument.
    if sample.sink_symbol is not None:
        sink_sources = [
            _source_argument_of_sink(call, sample, tree, symbol_table)
            for call in iter_named_nodes(tree.root, node_type="call_expression")
            if _ranges_overlap(call, sample.source_span)
            and _call_matches_symbol(
                call,
                sample.sink_symbol,
                tree,
                symbol_table,
                allow_candidate=True,
            )
        ]
        selected_sink_source = _select_node(
            [
                node
                for node in sink_sources
                if node is not None
                and _is_valid_origin_node(node, sample, tree, symbol_table)
            ],
            sample.source_span,
            tree.source,
        )
        if selected_sink_source is not None and _node_matches_scope(
            sample, selected_sink_source, tree
        ):
            return selected_sink_source

    # The source expression is preferred over its enclosing let or statement.
    preferred_types: tuple[str, ...] = ()
    feature_values = {feature.value for feature in sample.features}
    if "format_template" in feature_values:
        preferred_types = ("macro_invocation",)
    elif "log_or_diagnostic" in feature_values:
        preferred_types = ("macro_invocation",)
    elif "match_expression" in feature_values:
        preferred_types = ("match_expression",)
    elif "if_expression" in feature_values:
        preferred_types = ("if_expression",)

    container = tree.smallest_named_node_containing(sample.source_span)
    structural_candidates = _origin_candidates_in_container(
        container, preferred_types, sample, tree, symbol_table
    )
    if structural_candidates:
        selected = _select_node(structural_candidates, sample.source_span, tree.source)
        if (
            selected is not None
            and _origin_matches_sink_constraint(selected, sample, tree, symbol_table)
            and _node_matches_scope(sample, selected, tree)
        ):
            return selected

    candidate_root = tree.smallest_named_node_containing(sample.source_span)
    candidates = [
        node
        for node in iter_named_nodes(candidate_root or tree.root)
        if _ranges_overlap(node, sample.source_span)
        and _is_valid_origin_node(node, sample, tree, symbol_table)
        and _origin_matches_sink_constraint(node, sample, tree, symbol_table)
        and _node_matches_scope(sample, node, tree)
    ]
    if preferred_types:
        preferred = [node for node in candidates if node.type in preferred_types]
        if preferred:
            candidates = preferred
    selected = _select_node(candidates, sample.source_span, tree.source)
    if selected is not None:
        return selected

    # A source span can point at the beginning of a let declaration or a
    # statement.  Its value/expression is still a valid source expression.
    while container is not None:
        if container.type == "let_declaration":
            value = container.child_by_field_name("value")
            if value is not None:
                if (
                    _is_valid_origin_node(value, sample, tree, symbol_table)
                    and _origin_matches_sink_constraint(
                        value, sample, tree, symbol_table
                    )
                    and _node_matches_scope(sample, value, tree)
                ):
                    return value
                source = _source_argument_of_sink(value, sample, tree, symbol_table)
                if source is not None and _node_matches_scope(sample, source, tree):
                    return source
        elif container.type == "expression_statement":
            expression = _expression_statement_value(container)
            if expression is not None:
                if (
                    _is_valid_origin_node(expression, sample, tree, symbol_table)
                    and _origin_matches_sink_constraint(
                        expression, sample, tree, symbol_table
                    )
                    and _node_matches_scope(sample, expression, tree)
                ):
                    return expression
                source = _source_argument_of_sink(
                    expression, sample, tree, symbol_table
                )
                if source is not None and _node_matches_scope(sample, source, tree):
                    return source
        elif container.type == "call_expression":
            source = _source_argument_of_sink(container, sample, tree, symbol_table)
            if source is not None and _node_matches_scope(sample, source, tree):
                return source
        container = container.parent
    return None


def _source_argument_of_sink(
    node: Node,
    sample: GoldenSample,
    tree: RustCst,
    symbol_table: SourceSymbolTable,
) -> Node | None:
    if node.type != "call_expression" or sample.sink_symbol is None:
        return None
    if not _call_matches_symbol(
        node, sample.sink_symbol, tree, symbol_table, allow_candidate=True
    ):
        return None
    source = tree.source
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return None
    values = arguments.named_children
    if sample.text_slot is not None:
        argument = _slot_value(node, sample.text_slot, source)
        if (
            argument is not None
            and _sample_can_recover_sink_source(sample, node, argument)
            and _is_valid_origin_node(argument, sample, tree, symbol_table)
        ):
            return argument
        return None
    argument_index = _text_argument_index(sample.sink_symbol)
    if argument_index is None or argument_index >= len(values):
        return None
    argument = values[argument_index]
    if _sample_can_recover_sink_source(
        sample, node, argument
    ) and _is_valid_origin_node(argument, sample, tree, symbol_table):
        return argument
    return None


def _sample_can_recover_sink_source(
    sample: GoldenSample, call: Node, source_argument: Node
) -> bool:
    """Keep a sink fallback tied to the recorded call/source relationship."""

    if _ranges_overlap(source_argument, sample.source_span):
        return True
    function = call.child_by_field_name("function")
    if function is None:
        return False
    span = sample.source_span
    starts_at_call = span.start_byte <= function.start_byte
    return starts_at_call and (
        span.end_byte <= source_argument.start_byte
        or span.end_byte >= function.end_byte
    )


def _text_argument_index(sink_symbol: str) -> int | None:
    if sink_symbol.endswith("Window::prompt"):
        return 1
    if sink_symbol.endswith("StatusToast::new"):
        return 0
    if sink_symbol.endswith("Toast::new"):
        return 1
    if sink_symbol.endswith("Button::new"):
        return 1
    if sink_symbol.endswith("Label::new"):
        return 0
    if sink_symbol.endswith(("Tooltip::text", "Tooltip::simple")):
        return 0
    if sink_symbol.endswith("LanguageServerPromptRequest::new"):
        return 1
    return None


def _origin_candidates_in_container(
    container: Node | None,
    preferred_types: tuple[str, ...],
    sample: GoldenSample,
    tree: RustCst,
    symbol_table: SourceSymbolTable,
) -> list[Node]:
    if container is None:
        return []
    roots: list[Node] = []
    if container.type == "let_declaration":
        value = container.child_by_field_name("value")
        if value is not None:
            roots.append(value)
    elif container.type == "expression_statement":
        expression = _expression_statement_value(container)
        if expression is not None:
            roots.append(expression)
    elif container.type == "call_expression":
        roots.append(container)
    return [
        node
        for root in roots
        for node in iter_named_nodes(root)
        if node.type in preferred_types
        and _ranges_overlap(node, sample.source_span)
        and not _is_non_value_node(node)
        and _is_valid_origin_node(node, sample, tree, symbol_table)
    ]


def _is_valid_origin_node(
    node: Node,
    sample: GoldenSample,
    tree: RustCst,
    symbol_table: SourceSymbolTable,
) -> bool:
    if not _is_expression_node(node) or _is_origin_syntax_node(node):
        return False
    if sample.sink_symbol and node.type == "call_expression":
        if _call_matches_symbol(
            node,
            sample.sink_symbol,
            tree,
            symbol_table,
            allow_candidate=True,
        ):
            return False
    if _is_wrapper_macro(node, sample, tree.source):
        return False
    if _is_sink_wrapper(node, sample, tree, symbol_table):
        return False
    return not _contains_sink_call(node, sample, tree, symbol_table)


def _is_origin_syntax_node(node: Node) -> bool:
    """Return whether a node is syntax for a call/path rather than a value.

    A Rust CST exposes every identifier in a scoped path separately.  The
    first segment of ``StatusToast::new`` and the enum path in
    ``PromptLevel::Info`` therefore look like ordinary identifiers unless the
    surrounding path role is checked explicitly.
    """

    if _is_non_value_node(node):
        return True
    current: Node | None = node
    while current is not None:
        if current.type == "scoped_identifier":
            return True
        current = current.parent
    return False


def _origin_matches_sink_constraint(
    node: Node,
    sample: GoldenSample,
    tree: RustCst,
    symbol_table: SourceSymbolTable,
) -> bool:
    """Check a sink/slot constraint when the origin is inside that sink call.

    Origins obtained through a local binding or another provenance edge have
    no enclosing sink call and are intentionally left to the data-flow layer.
    A node that is already inside a matching call, however, must belong to the
    declared value path rather than a neighboring identity or action slot.
    """

    if sample.sink_symbol is None:
        return True
    current = node.parent
    while current is not None:
        if current.type == "call_expression" and _call_matches_symbol(
            current,
            sample.sink_symbol,
            tree,
            symbol_table,
            allow_candidate=True,
        ):
            if sample.text_slot is not None:
                value = _slot_value(current, sample.text_slot, tree.source)
                return value is not None and _node_contains(value, node)
            arguments = current.child_by_field_name("arguments")
            if arguments is None:
                return False
            return any(
                _node_contains(argument, node) for argument in arguments.named_children
            )
        current = current.parent
    return True


def _is_expression_node(node: Node) -> bool:
    if node.type in _INVALID_CONTAINER_TYPES:
        return False
    return (
        node.type in _ORIGIN_NODE_TYPES
        or node.type.endswith("_expression")
        or node.type.endswith("_literal")
    )


def _expression_statement_value(node: Node) -> Node | None:
    """Return an expression statement's only named child.

    The Rust grammar does not expose the statement expression through a
    ``child_by_field_name`` field.  Using the named-child invariant keeps the
    enclosing semicolon out of canonical spans without depending on grammar
    internals that are not present in the locked Tree-sitter release.
    """

    named_children = node.named_children
    return named_children[0] if len(named_children) == 1 else None


def _validate_sample_span(sample: GoldenSample, source: bytes) -> None:
    span = sample.source_span
    if span.end_byte > len(source):
        raise CanonicalCstError(
            f"{sample.sample_id}: source span ends at {span.end_byte}, "
            f"past source byte length {len(source)}"
        )
    try:
        anchor = source[span.start_byte : span.end_byte].decode("utf-8")
    except UnicodeDecodeError as error:
        raise CanonicalCstError(
            f"{sample.sample_id}: source span does not follow UTF-8 boundaries"
        ) from error
    if anchor != sample.anchor:
        raise CanonicalCstError(
            f"{sample.sample_id}: source span contains {anchor!r}, "
            f"expected anchor {sample.anchor!r}"
        )


def _is_non_value_node(node: Node) -> bool:
    """Exclude identifiers that are syntax labels rather than expressions."""

    parent = node.parent
    if parent is None:
        return False
    if parent.type in {"closure_parameters", "match_pattern"}:
        return True
    if parent.child_by_field_name("name") == node:
        return True
    if (
        parent.type in {"let_declaration", "parameter"}
        and parent.child_by_field_name("pattern") == node
    ):
        return True
    if (
        parent.type == "field_expression"
        and parent.child_by_field_name("field") == node
    ):
        return True
    if parent.type == "scoped_identifier" and parent.named_children[-1] == node:
        return True
    if parent.type == "scoped_identifier":
        grandparent = parent.parent
        if (
            grandparent is not None
            and grandparent.type == "call_expression"
            and grandparent.child_by_field_name("function") == parent
        ):
            return True
    if (
        parent.type == "call_expression"
        and parent.child_by_field_name("function") == node
    ):
        return True
    if (
        parent.type == "macro_invocation"
        and parent.child_by_field_name("macro") == node
    ):
        return True
    return False


def _is_wrapper_macro(node: Node, sample: GoldenSample, source: bytes) -> bool:
    if node.type != "macro_invocation":
        return False
    macro = node.child_by_field_name("macro")
    if macro is None:
        return False
    macro_name = _node_text(macro, source)
    if macro_name.rsplit("::", 1)[-1] in _OPAQUE_WRAPPER_MACROS:
        return True
    feature_values = {feature.value for feature in sample.features}
    if "format_template" in feature_values and macro_name.endswith("format"):
        return False
    if "log_or_diagnostic" in feature_values and any(
        macro_name.endswith(name)
        for name in ("error", "warn", "debug", "info", "trace")
    ):
        return False
    return macro.start_byte < sample.source_span.start_byte


def _is_sink_wrapper(
    node: Node,
    sample: GoldenSample,
    tree: RustCst,
    symbol_table: SourceSymbolTable,
) -> bool:
    if node.type != "call_expression":
        return False
    return sample.sink_symbol is not None and _call_matches_symbol(
        node,
        sample.sink_symbol,
        tree,
        symbol_table,
        allow_candidate=True,
    )


def _contains_sink_call(
    node: Node,
    sample: GoldenSample,
    tree: RustCst,
    symbol_table: SourceSymbolTable,
) -> bool:
    if sample.sink_symbol is None:
        return False
    for call in iter_named_nodes(node, node_type="call_expression"):
        if (
            call.type == node.type
            and call.start_byte == node.start_byte
            and call.end_byte == node.end_byte
        ):
            continue
        if _call_matches_symbol(
            call,
            sample.sink_symbol,
            tree,
            symbol_table,
            allow_candidate=True,
        ):
            return True
    return False


def _canonical_scope_exclusion(sample: GoldenSample, tree: RustCst) -> Node | None:
    exact = tree.exact_named_node(sample.source_span)
    if exact is not None and _is_valid_scope_node(exact, sample, tree):
        return exact

    container = tree.smallest_named_node_containing(sample.source_span)
    while container is not None:
        if container.type == "let_declaration":
            value = container.child_by_field_name("value")
            if value is not None:
                selected = _select_scope_node(
                    [
                        candidate
                        for candidate in iter_named_nodes(value)
                        if _ranges_overlap(candidate, sample.source_span)
                        and _is_valid_scope_node(candidate, sample, tree)
                    ],
                    sample.source_span,
                    tree.source,
                )
                if selected is not None:
                    return selected
        elif container.type == "expression_statement":
            expression = _expression_statement_value(container)
            if expression is not None:
                selected = _select_scope_node(
                    [
                        candidate
                        for candidate in iter_named_nodes(expression)
                        if _ranges_overlap(candidate, sample.source_span)
                        and _is_valid_scope_node(candidate, sample, tree)
                    ],
                    sample.source_span,
                    tree.source,
                )
                if selected is not None:
                    return selected
        elif container.type == "match_arm":
            expression_candidates = [
                node
                for child in container.named_children
                if child.type != "match_pattern"
                for node in iter_named_nodes(child)
                if node.type in _ORIGIN_NODE_TYPES
                and _ranges_overlap(node, sample.source_span)
                and _is_valid_scope_node(node, sample, tree)
            ]
            selected = _select_scope_node(
                expression_candidates, sample.source_span, tree.source
            )
            if selected is not None:
                return selected
        container = container.parent

    candidates = [
        node
        for node in iter_named_nodes(tree.root)
        if _is_scope_candidate_node(node)
        and _ranges_overlap(node, sample.source_span)
        and _is_valid_scope_node(node, sample, tree)
    ]
    return _select_scope_node(candidates, sample.source_span, tree.source)


def _is_valid_scope_node(node: Node, sample: GoldenSample, tree: RustCst) -> bool:
    if node.type in _INVALID_CONTAINER_TYPES:
        return False
    if node.type not in _ORIGIN_NODE_TYPES and node.type != "match_arm":
        return False
    if _is_non_value_node(node):
        return False
    return _node_matches_scope(sample, node, tree)


def _is_scope_candidate_node(node: Node) -> bool:
    return _is_expression_node(node) or node.type == "match_arm"


def _node_matches_scope(sample: GoldenSample, node: Node, tree: RustCst) -> bool:
    if sample.scope.value == "production":
        path_parts = set(sample.path.parts)
        return (
            not is_within_test_scope(node, tree.source)
            and not _is_test_source_path(sample.path)
            and "examples" not in path_parts
            and "component_preview" not in path_parts
        )
    if sample.scope.value == "test":
        return _is_declared_test_scope(node, sample, tree)
    if sample.scope.value == "example":
        return "examples" in sample.path.parts
    if sample.scope.value == "component_preview":
        return "component_preview" in sample.path.parts or _has_preview_ancestor(
            node, tree.source
        )
    return False


def _is_declared_test_scope(node: Node, sample: GoldenSample, tree: RustCst) -> bool:
    if is_within_test_scope(node, tree.source):
        return True
    return _is_test_source_path(sample.path)


def _is_test_source_path(path: PurePosixPath) -> bool:
    return (
        "tests" in path.parts
        or path.name == "tests.rs"
        or path.name.endswith("_tests.rs")
    )


def _has_preview_ancestor(node: Node, source: bytes) -> bool:
    current: Node | None = node
    while current is not None:
        if current.type == "macro_invocation":
            function = current.child_by_field_name("macro")
            if function is not None and _node_text(function, source).rsplit("::", 1)[
                -1
            ] in {"component", "component_preview"}:
                return True
        if current.type == "function_item":
            name = current.child_by_field_name("name")
            declaration_list = current.parent
            impl_item = declaration_list.parent if declaration_list else None
            trait = (
                impl_item.child_by_field_name("trait")
                if impl_item is not None and impl_item.type == "impl_item"
                else None
            )
            if (
                name is not None
                and _node_text(name, source) == "preview"
                and trait is not None
                and _node_text(trait, source).rsplit("::", 1)[-1] == "Component"
            ):
                return True
        current = current.parent
    return False


def _call_matches_symbol(
    call: Node,
    symbol: str,
    tree: RustCst,
    symbol_table: SourceSymbolTable,
    *,
    allow_candidate: bool = False,
) -> bool:
    function = call.child_by_field_name("function")
    if function is None:
        return False
    source = tree.source
    if function.type != "field_expression":
        observed = _node_text(function, source)
        resolution = symbol_table.resolve_target(observed, symbol, at=function)
        return resolution is not None and (
            allow_candidate or resolution.kind is SymbolResolutionKind.EXACT
        )

    field = function.child_by_field_name("field")
    receiver = function.child_by_field_name("value")
    if field is None or receiver is None:
        return False
    if _node_text(field, source) != symbol.rsplit("::", 1)[-1]:
        return False
    requirement = _receiver_requirement_for_symbol(symbol)
    if requirement is None:
        return False
    evidence = resolve_receiver(receiver, requirement, tree, symbol_table)
    if evidence is None:
        return allow_candidate and symbol.endswith("::InteractiveElement::aria_label")
    return allow_candidate or evidence.resolution is SymbolResolutionKind.EXACT


def _receiver_requirement_for_symbol(symbol: str) -> ReceiverRequirement | None:
    if symbol.endswith("::Window::prompt"):
        return ReceiverRequirement.GPUI_WINDOW
    if symbol.endswith(("::ParentElement::child", "::InteractiveElement::aria_label")):
        return ReceiverRequirement.GPUI_ELEMENT
    return None


def _symbol_suffix(symbol: str) -> str:
    parts = symbol.split("::")
    return "::".join(parts[-2:]) if len(parts) >= 2 else symbol


def _select_overlapping_candidate(
    candidates: list[_SlotCandidate], span: SourceSpan
) -> _SlotCandidate | None:
    applicable = [
        candidate
        for candidate in candidates
        if _ranges_overlap(candidate.call, span)
        or _ranges_overlap(candidate.value, span)
    ]
    if not applicable:
        return None
    return min(
        applicable,
        key=lambda candidate: _candidate_score(candidate.value, candidate.call, span),
    )


def _select_node(
    candidates: list[Node], span: SourceSpan, source: bytes
) -> Node | None:
    del source
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda node: (_node_score(node, span), node.start_byte),
    )


def _select_scope_node(
    candidates: list[Node], span: SourceSpan, source: bytes
) -> Node | None:
    del source
    if not candidates:
        return None
    semantic = [
        node
        for node in candidates
        if node.type
        not in {
            "field_identifier",
            "identifier",
            "scoped_identifier",
            "type_identifier",
        }
    ]
    if semantic:
        candidates = semantic
    return min(
        candidates,
        key=lambda node: (
            0
            if node.start_byte == span.start_byte and node.end_byte == span.end_byte
            else 1
            if node.start_byte == span.start_byte
            else 2
            if node.end_byte == span.end_byte
            else 3,
            abs(node.start_byte - span.start_byte) + abs(node.end_byte - span.end_byte),
            node.end_byte - node.start_byte,
        ),
    )


def _candidate_score(value: Node, call: Node, span: SourceSpan) -> tuple[int, int, int]:
    value_exact = int(
        value.start_byte == span.start_byte and value.end_byte == span.end_byte
    )
    value_contains = int(
        value.start_byte <= span.start_byte and value.end_byte >= span.end_byte
    )
    call_exact = int(
        call.start_byte == span.start_byte and call.end_byte == span.end_byte
    )
    call_contains = int(
        call.start_byte <= span.start_byte and call.end_byte >= span.end_byte
    )
    value_overlap = _overlap(value.start_byte, value.end_byte, span)
    call_overlap = _overlap(call.start_byte, call.end_byte, span)
    return (
        0
        if value_exact
        else 1
        if value_contains
        else 2
        if call_exact
        else 3
        if call_contains
        else 4,
        -value_overlap * 4 - call_overlap,
        abs(value.start_byte - span.start_byte) + abs(value.end_byte - span.end_byte),
    )


def _node_score(node: Node, span: SourceSpan) -> tuple[int, int, int]:
    exact = node.start_byte == span.start_byte and node.end_byte == span.end_byte
    contained_by_span = (
        node.start_byte >= span.start_byte and node.end_byte <= span.end_byte
    )
    contains_span = (
        node.start_byte <= span.start_byte and node.end_byte >= span.end_byte
    )
    distance = abs(node.start_byte - span.start_byte) + abs(
        node.end_byte - span.end_byte
    )
    return (
        0 if exact else 1 if contained_by_span else 2 if contains_span else 3,
        distance,
        node.end_byte - node.start_byte,
    )


def _ranges_overlap(node: Node, span: SourceSpan) -> bool:
    return _overlap(node.start_byte, node.end_byte, span) > 0


def _node_contains(outer: Node, inner: Node) -> bool:
    return outer.start_byte <= inner.start_byte and outer.end_byte >= inner.end_byte


def _overlap(start: int, end: int, span: SourceSpan) -> int:
    return max(0, min(end, span.end_byte) - max(start, span.start_byte))


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
