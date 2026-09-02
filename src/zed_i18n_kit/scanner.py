from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path, PurePosixPath

from tree_sitter import Node

from .discovery import DiscoveryError, discover_source_paths
from .golden import Decision, SourceSpan
from .rust_cst import (
    RustCst,
    collect_parse_error_nodes,
    is_within_parse_error,
    is_within_test_scope,
    iter_named_nodes,
    parse_rust_cst,
    source_span_for_node,
)
from .rust_dataflow import (
    RustDataflowIndex,
    build_dataflow_index,
    collect_provenance,
    enclosing_function_or_root,
    find_prior_binding,
)
from .rust_macros import (
    ALLOWLISTED_EXPRESSION_MACROS,
    node_is_within_expanded_range,
    parse_allowlisted_expression_macros,
)
from .rust_receivers import (
    GPUI_ELEMENT_FACTORIES,
    ReceiverEvidence,
    resolve_receiver,
)
from .rust_symbols import (
    SourceSymbolTable,
    SymbolResolutionKind,
    build_controlled_export_index,
    build_source_symbol_table,
)
from .scan_profiles import (
    WORKSPACE_SCAN_PROFILE,
    WORKSPACE_STRUCTURAL_RULE_IDS,
    CallShape,
    ScanProfile,
    SinkRule,
    SlotExtractionStrategy,
    SymbolResolutionMode,
)
from .scan_result import (
    SCAN_RESULT_SCHEMA_VERSION,
    CapabilityProbe,
    CapabilityProbeStatus,
    ProvenanceRange,
    RuleEvidence,
    ScanMetadata,
    ScanOccurrence,
    ScanResult,
    SourceFileSnapshot,
    resolve_git_head,
)


class ScannerError(ValueError):
    """Raised when source scanning cannot preserve its result contract."""


SCANNER_SEMANTIC_BEHAVIORS = {
    "child_element_binding_filter": "v3",
    "detach_and_prompt_err_message_and_some_extraction": "v2",
    "documentation_aside_command_option_exclusion": "v1",
    "format_control_literal_exclusion": "v1",
    "inline_code_label_exclusion": "v1",
    "plain_git_literal_localizable": "v1",
    "tooltip_with_meta_title_and_meta_extraction": "v1",
}


@dataclass(frozen=True, slots=True)
class ExtractedSlot:
    value_node: Node
    text_slot: str
    disposition_override: Decision | None = None
    require_textual_provenance: bool = False


@dataclass(frozen=True, slots=True)
class RuleMatch:
    rule: SinkRule
    resolution: SymbolResolutionKind
    reason: str


_STANDALONE_OPTION_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_=-])--[A-Za-z][A-Za-z0-9_-]*(?![A-Za-z0-9_=-])"
)
_ELEMENT_RETURN_TYPE_NAMES = frozenset({"AnyElement", "IntoElement", "ParentElement"})
_FORMAT_CONTROL_ESCAPES = frozenset("nrt0")


def scan_sources(
    zed_root: Path,
    *,
    profile: ScanProfile = WORKSPACE_SCAN_PROFILE,
) -> ScanResult:
    source_paths = _resolve_source_paths(zed_root, profile)
    export_index = (
        build_controlled_export_index(zed_root)
        if profile.symbol_resolution is SymbolResolutionMode.IMPORT_AWARE
        else None
    )

    occurrences: list[ScanOccurrence] = []
    snapshots: list[SourceFileSnapshot] = []
    parse_failures: list[str] = []
    expanded_macro_count = 0
    expanded_macro_failures: list[str] = []
    for path in source_paths:
        try:
            source = (zed_root / path).read_bytes()
        except OSError as error:
            raise ScannerError(f"cannot read scan source {path}: {error}") from error
        tree = parse_rust_cst(source)
        parse_error_nodes = collect_parse_error_nodes(tree)
        if parse_error_nodes:
            rendered = ",".join(
                f"bytes {error_node.start_byte}..{error_node.end_byte}"
                for error_node in parse_error_nodes
            )
            parse_failures.append(f"{path}({rendered})")
        # The Python binding ties Node lifetime to its Tree. Release the temporary
        # nodes before the loop replaces the owning RustCst.
        del parse_error_nodes
        snapshots.append(SourceFileSnapshot(path, hashlib.sha256(source).hexdigest()))
        symbol_table = (
            build_source_symbol_table(tree, path, export_index=export_index)
            if profile.symbol_resolution is SymbolResolutionMode.IMPORT_AWARE
            else None
        )
        dataflow_index = build_dataflow_index(tree)
        occurrences.extend(
            _scan_source(
                path,
                tree,
                profile,
                symbol_table,
                dataflow_index,
            )
        )
        occurrences.extend(_scan_structural_origins(path, tree, dataflow_index))
        expanded = parse_allowlisted_expression_macros(tree)
        if expanded is not None:
            expanded_macro_count += len(expanded.ranges)
            expanded_macro_failures.extend(
                f"{path}(bytes {span.start_byte}..{span.end_byte})"
                for span in expanded.parse_error_ranges
            )
            occurrences.extend(
                _scan_source(
                    path,
                    expanded.tree,
                    profile,
                    symbol_table,
                    build_dataflow_index(expanded.tree),
                    include_ranges=expanded.ranges,
                )
            )
        del tree

    occurrence_ids = [occurrence.occurrence_id for occurrence in occurrences]
    if len(set(occurrence_ids)) != len(occurrence_ids):
        raise ScannerError("scanner produced duplicate occurrence IDs")

    parse_status = (
        CapabilityProbeStatus.FAILED if parse_failures else CapabilityProbeStatus.PASSED
    )
    parse_details = (
        "parse errors outside matched nodes: " + "; ".join(parse_failures)
        if parse_failures
        else f"all {len(source_paths)} source files parsed without errors"
    )
    capability_probes = [
        CapabilityProbe(
            "tree-sitter-rust-binding",
            CapabilityProbeStatus.PASSED,
            f"tree-sitter {version('tree-sitter')}; "
            f"tree-sitter-rust {version('tree-sitter-rust')}",
        ),
        CapabilityProbe("prototype-error-free-parse", parse_status, parse_details),
    ]
    if profile.symbol_resolution is SymbolResolutionMode.IMPORT_AWARE:
        rule_ids = ",".join(sorted(rule.rule_id for rule in profile.sink_rules))
        rule_counts = Counter(
            evidence.rule_id
            for occurrence in occurrences
            for evidence in occurrence.evidence
        )
        missing_rule_ids = tuple(
            sorted(
                rule.rule_id
                for rule in profile.sink_rules
                if not rule_counts[rule.rule_id]
            )
        )
        rule_probe_status = (
            CapabilityProbeStatus.FAILED
            if missing_rule_ids
            else CapabilityProbeStatus.PASSED
        )
        rendered_counts = ",".join(
            f"{rule_id}={rule_counts[rule_id]}" for rule_id in sorted(rule_counts)
        )
        rule_probe_details = (
            f"resolved occurrences by rule: {rendered_counts}"
            if not missing_rule_ids
            else f"no resolved occurrence for: {','.join(missing_rule_ids)}; "
            f"resolved occurrences by rule: {rendered_counts or 'none'}"
        )
        capability_probes.extend(
            (
                CapabilityProbe(
                    "cfg-aware-import-resolution",
                    CapabilityProbeStatus.PASSED,
                    "explicit imports, aliases, local declarations, wildcard candidates, "
                    "and test-only cfg exclusions enabled",
                ),
                CapabilityProbe(
                    "typed-builtin-rules",
                    rule_probe_status,
                    f"enabled {len(profile.sink_rules)} builtin rules ({rule_ids}); "
                    f"{rule_probe_details}",
                ),
                CapabilityProbe(
                    "controlled-export-index",
                    CapabilityProbeStatus.FAILED
                    if export_index is None or export_index.failures
                    else CapabilityProbeStatus.PASSED,
                    "indexed ui::prelude and gpui::prelude public exports"
                    if export_index is not None and not export_index.failures
                    else "controlled export index unavailable: "
                    + "; ".join(
                        export_index.failures if export_index else ("disabled",)
                    ),
                ),
                CapabilityProbe(
                    "allowlisted-expression-macro-cst",
                    CapabilityProbeStatus.FAILED
                    if expanded_macro_failures
                    else CapabilityProbeStatus.PASSED,
                    f"secondary CST parsed {expanded_macro_count} "
                    f"{','.join(sorted(ALLOWLISTED_EXPRESSION_MACROS))} macros"
                    if not expanded_macro_failures
                    else "parse errors intersect expanded macros: "
                    + "; ".join(expanded_macro_failures),
                ),
                CapabilityProbe(
                    "structured-ui-origin-boundaries",
                    CapabilityProbeStatus.PASSED,
                    "enabled " + ",".join(WORKSPACE_STRUCTURAL_RULE_IDS),
                ),
            )
        )

    metadata = ScanMetadata(
        zed_commit=resolve_git_head(zed_root),
        tool_version=version("zed-i18n-kit"),
        rule_pack_version=profile.rule_pack_version,
        config_hash=_config_hash(profile, source_paths),
        scan_scope=source_paths,
        source_files=tuple(snapshots),
        capability_probes=tuple(capability_probes),
    )
    return ScanResult(
        schema_version=SCAN_RESULT_SCHEMA_VERSION,
        metadata=metadata,
        occurrences=tuple(occurrences),
    )


def _scan_source(
    path: PurePosixPath,
    tree: RustCst,
    profile: ScanProfile,
    symbol_table: SourceSymbolTable | None,
    dataflow_index: RustDataflowIndex,
    *,
    include_ranges: tuple[SourceSpan, ...] = (),
) -> tuple[ScanOccurrence, ...]:
    occurrences: list[ScanOccurrence] = []
    element_function_names = _element_returning_function_names(tree)
    for call in iter_named_nodes(tree.root, node_type="call_expression"):
        if include_ranges and not node_is_within_expanded_range(
            call.start_byte, call.end_byte, include_ranges
        ):
            continue
        if (
            call.has_error
            or is_within_parse_error(call)
            or is_within_test_scope(call, tree.source)
        ):
            continue
        match = _match_rule(call, tree, profile, symbol_table)
        if match is None:
            continue
        if match.rule.target_symbol == "ui::Label::new" and _is_inline_code_label(
            call, tree.source
        ):
            continue
        for extracted in _extract_slots(
            match.rule,
            call,
            tree.source,
            tree=tree,
            symbol_table=symbol_table,
            element_function_names=element_function_names,
        ):
            if (
                extracted.disposition_override is Decision.EXCLUDED
                and match.resolution is SymbolResolutionKind.CANDIDATE
            ):
                continue
            primary = extracted.value_node
            if (
                extracted.require_textual_provenance
                and primary.type == "identifier"
                and not _identifier_has_supported_text_binding(primary, tree)
            ):
                continue
            provenance = collect_provenance(tree, path, primary, index=dataflow_index)
            if extracted.require_textual_provenance and not _has_textual_provenance(
                dataflow_index, provenance
            ):
                continue
            contextual_override = _contextual_disposition_override(
                match.rule, call, primary, tree.source
            )
            override = (
                extracted.disposition_override
                if extracted.disposition_override is not None
                else contextual_override
            )
            disposition = _classify_disposition(
                primary,
                tree.source,
                resolution=match.resolution,
                override=override,
            )
            occurrence_id = _occurrence_id(
                path, match.rule.sink_symbol, extracted.text_slot, primary
            )
            evidence_reason = (
                f"{match.reason}; slot {extracted.text_slot} at {call.start_byte}"
            )
            if _is_format_control_literal(primary, tree.source):
                evidence_reason += "; literal contains only formatting control escapes"
            if contextual_override is Decision.EXCLUDED:
                evidence_reason += (
                    "; documentation_aside callback contains a standalone "
                    "long-option token"
                )
            occurrences.append(
                ScanOccurrence(
                    occurrence_id=occurrence_id,
                    path=path,
                    primary_span=source_span_for_node(primary),
                    syntax_kind=primary.type,
                    sink_symbol=match.rule.sink_symbol,
                    text_slot=extracted.text_slot,
                    disposition=disposition,
                    provenance=provenance,
                    evidence=(
                        RuleEvidence(
                            match.rule.rule_id,
                            evidence_reason,
                        ),
                    ),
                )
            )
    return tuple(occurrences)


def _scan_structural_origins(
    path: PurePosixPath,
    tree: RustCst,
    dataflow_index: RustDataflowIndex,
) -> tuple[ScanOccurrence, ...]:
    occurrences: list[ScanOccurrence] = []
    for expression in iter_named_nodes(tree.root, node_type="struct_expression"):
        if is_within_test_scope(expression, tree.source):
            continue
        name = expression.child_by_field_name("name")
        if name is None or _node_text(name, tree.source) != "project::Event::Toast":
            continue
        message = _struct_field_value(expression, "message", tree.source)
        if message is not None:
            occurrences.append(
                _structural_occurrence(
                    path,
                    tree,
                    dataflow_index,
                    message,
                    sink_symbol="project::Event::Toast::message",
                    text_slot="field[message]",
                    rule_id="project-event-toast-message-v1",
                    reason="matched project::Event::Toast message field",
                )
            )

    for call in iter_named_nodes(tree.root, node_type="call_expression"):
        if is_within_test_scope(call, tree.source):
            continue
        function = call.child_by_field_name("function")
        arguments = call.child_by_field_name("arguments")
        enclosing_function = enclosing_function_or_root(call, tree.root)
        return_type = enclosing_function.child_by_field_name("return_type")
        if (
            function is None
            or arguments is None
            or _node_text(function, tree.source) != "Err"
            or return_type is None
            or "SharedString" not in _node_text(return_type, tree.source)
        ):
            continue
        for argument in arguments.named_children:
            for origin in _text_origin_nodes(argument):
                occurrences.append(
                    _structural_occurrence(
                        path,
                        tree,
                        dataflow_index,
                        origin,
                        sink_symbol="core::result::Result::Err",
                        text_slot="arg[0]",
                        rule_id="shared-string-result-error-v1",
                        reason=(
                            "matched Err text origin in a function whose error "
                            "contract contains SharedString"
                        ),
                    )
                )
    return tuple(occurrences)


def _structural_occurrence(
    path: PurePosixPath,
    tree: RustCst,
    dataflow_index: RustDataflowIndex,
    primary: Node,
    *,
    sink_symbol: str,
    text_slot: str,
    rule_id: str,
    reason: str,
) -> ScanOccurrence:
    return ScanOccurrence(
        occurrence_id=_occurrence_id(path, sink_symbol, text_slot, primary),
        path=path,
        primary_span=source_span_for_node(primary),
        syntax_kind=primary.type,
        sink_symbol=sink_symbol,
        text_slot=text_slot,
        disposition=Decision.REVIEW_REQUIRED,
        provenance=collect_provenance(tree, path, primary, index=dataflow_index),
        evidence=(RuleEvidence(rule_id, reason),),
    )


def _struct_field_value(
    expression: Node, field_name: str, source: bytes
) -> Node | None:
    for initializer in iter_named_nodes(expression, node_type="field_initializer"):
        name = initializer.child_by_field_name("field")
        value = initializer.child_by_field_name("value")
        if (
            name is not None
            and value is not None
            and _node_text(name, source) == field_name
        ):
            return value
    return None


def _text_origin_nodes(expression: Node) -> tuple[Node, ...]:
    return tuple(
        node
        for node in iter_named_nodes(expression)
        if node.type in {"macro_invocation", "raw_string_literal", "string_literal"}
        and not any(
            ancestor.type
            in {"macro_invocation", "raw_string_literal", "string_literal"}
            for ancestor in _ancestors_until(node.parent, expression)
        )
    )


def _ancestors_until(node: Node | None, boundary: Node) -> tuple[Node, ...]:
    ancestors: list[Node] = []
    current = node
    while current is not None and current != boundary:
        ancestors.append(current)
        current = current.parent
    return tuple(ancestors)


def _match_rule(
    call: Node,
    tree: RustCst,
    profile: ScanProfile,
    symbol_table: SourceSymbolTable | None,
) -> RuleMatch | None:
    function = call.child_by_field_name("function")
    if function is None:
        return None
    function_text = _node_text(function, tree.source)
    has_direct_function_name = any(
        rule.call_shape is CallShape.FUNCTION
        and _matches_path_suffix(function_text, rule.callee_suffix)
        for rule in profile.sink_rules
    )
    for rule in profile.sink_rules:
        if profile.symbol_resolution is SymbolResolutionMode.PROTOTYPE_SUFFIX:
            if _matches_prototype_rule(rule, function, function_text, tree.source):
                return RuleMatch(
                    rule,
                    SymbolResolutionKind.EXACT,
                    f"matched {rule.call_shape.value} suffix {rule.callee_suffix!r}",
                )
            continue

        if rule.call_shape is CallShape.METHOD:
            if function.type != "field_expression":
                continue
            field = function.child_by_field_name("field")
            receiver = function.child_by_field_name("value")
            if (
                field is None
                or receiver is None
                or _node_text(field, tree.source) != rule.callee_suffix
            ):
                continue
            receiver_evidence = resolve_receiver(
                receiver,
                rule.receiver_requirement,
                tree,
                symbol_table,
            )
            if receiver_evidence is None:
                if not rule.allow_unresolved_receiver:
                    continue
                receiver_evidence = ReceiverEvidence(
                    SymbolResolutionKind.CANDIDATE,
                    "receiver type unresolved; method name retained as review candidate",
                )
            return RuleMatch(
                rule,
                receiver_evidence.resolution,
                f"resolved method {rule.callee_suffix!r}: {receiver_evidence.reason}",
            )

        if symbol_table is None or rule.target_symbol is None:
            continue
        if has_direct_function_name and not _matches_path_suffix(
            function_text, rule.callee_suffix
        ):
            continue
        if function_text.rsplit("::", 1)[-1] != rule.target_symbol.rsplit("::", 1)[-1]:
            continue
        resolution = symbol_table.resolve_target(
            function_text, rule.target_symbol, at=function
        )
        if resolution is not None:
            return RuleMatch(
                rule,
                resolution.kind,
                f"resolved function {function_text!r} to {resolution.target!r} "
                f"({resolution.evidence})",
            )
    return None


def _matches_prototype_rule(
    rule: SinkRule, function: Node, function_text: str, source: bytes
) -> bool:
    if rule.call_shape is CallShape.METHOD:
        if function.type != "field_expression":
            return False
        field = function.child_by_field_name("field")
        return field is not None and _node_text(field, source) == rule.callee_suffix
    return _matches_path_suffix(function_text, rule.callee_suffix)


def _extract_slots(
    rule: SinkRule,
    call: Node,
    source: bytes,
    *,
    tree: RustCst | None = None,
    symbol_table: SourceSymbolTable | None = None,
    element_function_names: frozenset[str] | None = None,
) -> tuple[ExtractedSlot, ...]:
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return ()
    values = arguments.named_children
    if rule.slot_extraction is SlotExtractionStrategy.CHILD_LITERAL:
        if (
            values
            and tree is not None
            and _child_value_is_element(
                values[0],
                tree,
                symbol_table,
                element_function_names=element_function_names,
            )
        ):
            return ()
        if values and values[0].type in {"string_literal", "raw_string_literal"}:
            return (ExtractedSlot(values[0], "arg[0]"),)
        if values and values[0].type in {
            "binary_expression",
            "field_expression",
            "identifier",
            "if_expression",
            "match_expression",
            "reference_expression",
        }:
            return (
                ExtractedSlot(
                    values[0],
                    "arg[0]",
                    require_textual_provenance=True,
                ),
            )
        return ()
    if rule.slot_extraction is SlotExtractionStrategy.BUTTON:
        slots: list[ExtractedSlot] = []
        if values:
            slots.append(ExtractedSlot(values[0], "arg[0]", Decision.EXCLUDED))
        if len(values) > 1:
            slots.append(ExtractedSlot(values[1], "arg[1]"))
        return tuple(slots)
    if rule.slot_extraction is SlotExtractionStrategy.FIRST_ARGUMENT:
        return (ExtractedSlot(values[0], "arg[0]"),) if values else ()
    if rule.slot_extraction is SlotExtractionStrategy.SECOND_ARGUMENT:
        return (ExtractedSlot(values[1], "arg[1]"),) if len(values) > 1 else ()
    if rule.slot_extraction is SlotExtractionStrategy.THIRD_ARGUMENT:
        return (ExtractedSlot(values[2], "arg[2]"),) if len(values) > 2 else ()
    if rule.slot_extraction is SlotExtractionStrategy.TOOLTIP_WITH_META:
        slots: list[ExtractedSlot] = []
        if values:
            slots.append(ExtractedSlot(values[0], "arg[0]"))
        if len(values) > 2:
            slots.append(ExtractedSlot(values[2], "arg[2]"))
        return tuple(slots)
    if rule.slot_extraction is SlotExtractionStrategy.DETACH_AND_PROMPT_ERR:
        if not values:
            return ()
        slots = [ExtractedSlot(values[0], "arg[0]")]
        if values[-1].type == "closure_expression":
            closure_index = len(values) - 1
            slots.extend(
                ExtractedSlot(value, f"arg[{closure_index}].Some")
                for value in _some_return_values(values[-1], source)
            )
        return tuple(slots)

    slots: list[ExtractedSlot] = []
    if len(values) > 1:
        slots.append(ExtractedSlot(values[1], "arg[1]"))
    if len(values) > 2:
        detail = _unwrap_some(values[2], source)
        if detail is not None:
            slots.append(ExtractedSlot(detail, "arg[2].Some"))
        elif _node_text(values[2], source) != "None":
            slots.append(ExtractedSlot(values[2], "arg[2]"))
    if len(values) > 3:
        action_array = _unwrap_reference(values[3])
        if action_array.type == "array_expression":
            slots.extend(
                ExtractedSlot(action, f"arg[3][{index}]")
                for index, action in enumerate(action_array.named_children)
            )
        else:
            slots.append(ExtractedSlot(values[3], "arg[3]"))
    return tuple(slots)


def _has_textual_provenance(
    dataflow_index: RustDataflowIndex,
    provenance: tuple[ProvenanceRange, ...],
) -> bool:
    return any(
        (item.source_span.start_byte, item.source_span.end_byte)
        in dataflow_index.textual_spans
        for item in provenance
    )


def _identifier_has_supported_text_binding(identifier: Node, tree: RustCst) -> bool:
    scope = enclosing_function_or_root(identifier, tree.root)
    binding = find_prior_binding(identifier, scope, tree.source)
    return binding is not None and binding.type in {
        "binary_expression",
        "if_expression",
        "macro_invocation",
        "match_expression",
        "raw_string_literal",
        "reference_expression",
        "string_literal",
    }


def _unwrap_some(node: Node, source: bytes) -> Node | None:
    if node.type != "call_expression":
        return None
    function = node.child_by_field_name("function")
    arguments = node.child_by_field_name("arguments")
    if (
        function is None
        or arguments is None
        or not _matches_path_suffix(_node_text(function, source), "Some")
        or len(arguments.named_children) != 1
    ):
        return None
    return arguments.named_children[0]


def _some_return_values(closure: Node, source: bytes) -> tuple[Node, ...]:
    body = closure.child_by_field_name("body")
    if body is None:
        return ()

    values = list(_some_values_from_expression(body, source))
    values.extend(
        value
        for return_expression in iter_named_nodes(body, node_type="return_expression")
        for value in _some_values_from_expression(return_expression, source)
    )
    return _unique_nodes(values)


def _some_values_from_expression(node: Node, source: bytes) -> tuple[Node, ...]:
    if node.type == "expression_statement":
        return (
            _some_values_from_expression(node.named_children[0], source)
            if len(node.named_children) == 1
            else ()
        )
    if node.type == "return_expression":
        return (
            _some_values_from_expression(node.named_children[-1], source)
            if node.named_children
            else ()
        )
    if node.type == "parenthesized_expression":
        return (
            _some_values_from_expression(node.named_children[0], source)
            if len(node.named_children) == 1
            else ()
        )
    if node.type == "block":
        if not node.named_children:
            return ()
        return _some_values_from_expression(node.named_children[-1], source)
    if node.type == "call_expression":
        value = _unwrap_some(node, source)
        return (value,) if value is not None else ()
    if node.type == "if_expression":
        return tuple(
            value
            for branch in node.named_children
            if branch.type in {"block", "else_clause"}
            for value in _some_values_from_expression(branch, source)
        )
    if node.type == "else_clause":
        return tuple(
            value
            for branch in node.named_children
            for value in _some_values_from_expression(branch, source)
        )
    if node.type == "match_expression":
        return tuple(
            value
            for match_block in node.named_children
            if match_block.type == "match_block"
            for arm in iter_named_nodes(match_block, node_type="match_arm")
            for value in _some_values_from_match_arm(arm, source)
        )
    return ()


def _some_values_from_match_arm(arm: Node, source: bytes) -> tuple[Node, ...]:
    if not arm.named_children:
        return ()
    return _some_values_from_expression(arm.named_children[-1], source)


def _child_value_is_element(
    value: Node,
    tree: RustCst,
    symbol_table: SourceSymbolTable | None,
    *,
    element_function_names: frozenset[str] | None = None,
) -> bool:
    if element_function_names is None:
        element_function_names = _element_returning_function_names(tree)
    if value.type == "identifier":
        binding = find_prior_binding(
            value,
            enclosing_function_or_root(value, tree.root),
            tree.source,
        )
        if binding is None:
            return False
        value = binding
    return _expression_has_element_signal(
        value,
        tree,
        symbol_table,
        set(),
        element_function_names,
    )


def _expression_has_element_signal(
    expression: Node,
    tree: RustCst,
    symbol_table: SourceSymbolTable | None,
    visited: set[tuple[int, int, str]],
    element_function_names: frozenset[str],
) -> bool:
    key = (expression.start_byte, expression.end_byte, expression.type)
    if key in visited:
        return False
    visited.add(key)

    if expression.type == "identifier":
        binding = find_prior_binding(
            expression,
            enclosing_function_or_root(expression, tree.root),
            tree.source,
        )
        return binding is not None and _expression_has_element_signal(
            binding,
            tree,
            symbol_table,
            visited,
            element_function_names,
        )

    for node in iter_named_nodes(expression):
        if node.type != "call_expression":
            continue
        function = node.child_by_field_name("function")
        if function is None:
            continue
        if function.type == "field_expression":
            field = function.child_by_field_name("field")
            if field is not None and _node_text(field, tree.source) in {
                "child",
                "children",
                "into_any_element",
                "into_element",
            }:
                return True
            if _local_function_returns_element(node, tree, element_function_names):
                return True
            continue
        if symbol_table is None:
            if _local_function_returns_element(node, tree, element_function_names):
                return True
            continue
        function_text = _node_text(function, tree.source)
        if any(
            symbol_table.resolve_target(function_text, target, at=function) is not None
            for target in GPUI_ELEMENT_FACTORIES
        ) or _local_function_returns_element(node, tree, element_function_names):
            return True
    return False


def _element_returning_function_names(tree: RustCst) -> frozenset[str]:
    definitions: dict[str, list[Node]] = {}
    for definition in iter_named_nodes(tree.root, node_type="function_item"):
        name = definition.child_by_field_name("name")
        if name is not None:
            definitions.setdefault(_node_text(name, tree.source), []).append(definition)
    return frozenset(
        name
        for name, items in definitions.items()
        if len(items) == 1 and _function_return_type_is_element(items[0], tree.source)
    )


def _function_return_type_is_element(definition: Node, source: bytes) -> bool:
    return_type = definition.child_by_field_name("return_type")
    if return_type is None:
        return False
    return any(
        token in _ELEMENT_RETURN_TYPE_NAMES
        for token in re.findall(
            r"[A-Za-z_][A-Za-z0-9_]*", _node_text(return_type, source)
        )
    )


def _local_function_returns_element(
    call: Node,
    tree: RustCst,
    element_function_names: frozenset[str],
) -> bool:
    """Recognize a same-file helper whose declared result is an element.

    A `.child(match ...)` value can mix text with a helper returning an
    `AnyElement`/`IntoElement`.  The match itself is then an element value, so
    treating its whole source span as translatable text would hide the actual
    branch boundary.  Only a unique same-file function with an explicit,
    element-shaped return type is accepted; unknown helpers remain candidates.
    """

    function = call.child_by_field_name("function")
    if function is None:
        return False
    if function.type == "field_expression":
        name_node = function.child_by_field_name("field")
    elif function.type in {"identifier", "scoped_identifier"}:
        name_node = function
    else:
        return False
    if name_node is None:
        return False
    function_name = _node_text(name_node, tree.source).rsplit("::", 1)[-1]
    return function_name in element_function_names


def _is_inline_code_label(call: Node, source: bytes) -> bool:
    parent = call.parent
    if parent is None or parent.type != "field_expression":
        return False
    if parent.child_by_field_name("value") != call:
        return False
    field = parent.child_by_field_name("field")
    if field is None or _node_text(field, source) != "inline_code":
        return False
    outer_call = parent.parent
    return (
        outer_call is not None
        and outer_call.type == "call_expression"
        and outer_call.child_by_field_name("function") == parent
    )


def _contextual_disposition_override(
    rule: SinkRule, call: Node, primary: Node, source: bytes
) -> Decision | None:
    """Apply exclusions that require the enclosing UI API context.

    A command-looking label remains ordinary product text unless its literal
    is the rendered documentation aside for a menu entry.  Keeping the
    context check here prevents a command token from becoming a global string
    exclusion heuristic.
    """

    if (
        rule.target_symbol != "ui::Label::new"
        or primary.type not in {"string_literal", "raw_string_literal"}
        or _STANDALONE_OPTION_TOKEN.search(_node_text(primary, source)) is None
        or not _is_documentation_aside_callback_label(call, source)
    ):
        return None
    return Decision.EXCLUDED


def _is_documentation_aside_callback_label(call: Node, source: bytes) -> bool:
    current = call.parent
    while current is not None:
        if current.type == "call_expression":
            function = current.child_by_field_name("function")
            arguments = current.child_by_field_name("arguments")
            if (
                function is not None
                and function.type == "field_expression"
                and arguments is not None
            ):
                field = function.child_by_field_name("field")
                if (
                    field is not None
                    and _node_text(field, source) == "documentation_aside"
                    and any(
                        argument.type == "closure_expression"
                        and _node_span_contains(argument, call)
                        for argument in arguments.named_children
                    )
                ):
                    return True
        current = current.parent
    return False


def _node_span_contains(outer: Node, inner: Node) -> bool:
    return outer.start_byte <= inner.start_byte and outer.end_byte >= inner.end_byte


def _unique_nodes(nodes: list[Node]) -> tuple[Node, ...]:
    return tuple(
        sorted(
            {
                (node.start_byte, node.end_byte, node.type): node for node in nodes
            }.values(),
            key=lambda node: (node.start_byte, node.end_byte, node.type),
        )
    )


def _unwrap_reference(node: Node) -> Node:
    if node.type != "reference_expression":
        return node
    value = node.child_by_field_name("value")
    return value if value is not None else node


def _classify_disposition(
    node: Node,
    source: bytes,
    *,
    resolution: SymbolResolutionKind,
    override: Decision | None,
) -> Decision:
    if _is_format_control_literal(node, source):
        return Decision.EXCLUDED
    if resolution is SymbolResolutionKind.CANDIDATE:
        return Decision.REVIEW_REQUIRED
    if override is not None:
        return override
    if node.type not in {"string_literal", "raw_string_literal"}:
        return Decision.REVIEW_REQUIRED
    text = _node_text(node, source)
    if any(character.isalpha() for character in text):
        return Decision.CONFIRMED
    return Decision.EXCLUDED


def _is_format_control_literal(node: Node, source: bytes) -> bool:
    if node.type not in {"string_literal", "raw_string_literal"}:
        return False
    literal = _node_text(node, source)
    if node.type == "raw_string_literal":
        opening_quote = literal.find('"')
        if opening_quote <= 0 or literal[0] != "r":
            return False
        hash_prefix = literal[1:opening_quote]
        if any(character != "#" for character in hash_prefix):
            return False
        hash_count = len(hash_prefix)
        closing = '"' + ("#" * hash_count)
        if not literal.endswith(closing):
            return False
        content = literal[opening_quote + 1 : -len(closing)]
    else:
        if len(literal) < 2 or not literal.startswith('"') or not literal.endswith('"'):
            return False
        content = literal[1:-1]
    return _only_format_control_escapes(content)


def _only_format_control_escapes(content: str) -> bool:
    if not content:
        return False
    index = 0
    while index < len(content):
        if content[index] != "\\" or index + 1 >= len(content):
            return False
        escape = content[index + 1]
        if escape in _FORMAT_CONTROL_ESCAPES:
            index += 2
            continue
        if escape == "\\":
            index += 2
            if index < len(content) and content[index] in _FORMAT_CONTROL_ESCAPES:
                index += 1
            continue
        if escape == "x" and index + 3 < len(content):
            digits = content[index + 2 : index + 4]
            if (
                all(digit in "0123456789abcdefABCDEF" for digit in digits)
                and int(digits, 16) < 0x20
            ):
                index += 4
                continue
        if escape == "u" and index + 2 < len(content) and content[index + 2] == "{":
            closing = content.find("}", index + 3)
            if closing > index + 3:
                digits = content[index + 3 : closing]
                if all(digit in "0123456789abcdefABCDEF" for digit in digits):
                    codepoint = int(digits, 16)
                    if codepoint < 0x20 or codepoint == 0x7F:
                        index = closing + 1
                        continue
        return False
    return True


def _occurrence_id(
    path: PurePosixPath, sink_symbol: str, text_slot: str, node: Node
) -> str:
    identity = (
        f"{path.as_posix()}|{sink_symbol}|{text_slot}|{node.start_byte}|{node.end_byte}"
    )
    return "occ-" + hashlib.sha256(identity.encode()).hexdigest()[:20]


def _resolve_source_paths(
    zed_root: Path, profile: ScanProfile
) -> tuple[PurePosixPath, ...]:
    if profile.source_paths and profile.discovery_policy is not None:
        raise ScannerError(
            "scan profile cannot combine fixed source paths with discovery policy"
        )
    if profile.source_paths:
        return tuple(sorted(profile.source_paths))
    if profile.discovery_policy is not None:
        try:
            return discover_source_paths(zed_root, policy=profile.discovery_policy)
        except DiscoveryError as error:
            raise ScannerError(f"source discovery failed: {error}") from error
    raise ScannerError("scan profile must define source paths or discovery policy")


def _config_hash(profile: ScanProfile, source_paths: tuple[PurePosixPath, ...]) -> str:
    payload = {
        "rules": [
            {
                "allow_unresolved_receiver": rule.allow_unresolved_receiver,
                "callee_suffix": rule.callee_suffix,
                "call_shape": rule.call_shape.value,
                "receiver_requirement": rule.receiver_requirement.value,
                "rule_id": rule.rule_id,
                "sink_symbol": rule.sink_symbol,
                "slot_extraction": rule.slot_extraction.value,
                "target_symbol": rule.target_symbol,
            }
            for rule in profile.sink_rules
        ],
        "source_paths": [path.as_posix() for path in source_paths],
        "symbol_resolution": profile.symbol_resolution.value,
        "controlled_export_modules": ["gpui::prelude", "ui::prelude"],
        "expression_macro_allowlist": sorted(ALLOWLISTED_EXPRESSION_MACROS),
        "structural_origin_rules": list(WORKSPACE_STRUCTURAL_RULE_IDS),
        "semantic_behaviors": SCANNER_SEMANTIC_BEHAVIORS,
    }
    if profile.discovery_policy is not None:
        policy = profile.discovery_policy
        payload["discovery"] = {
            "excluded_crates": sorted(policy.excluded_crates),
            "excluded_directories": sorted(policy.excluded_directories),
            "excluded_filename_suffixes": list(policy.excluded_filename_suffixes),
            "excluded_filenames": sorted(policy.excluded_filenames),
            "include_globs": list(policy.include_globs),
        }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _matches_path_suffix(function_text: str, suffix: str) -> bool:
    return function_text == suffix or function_text.endswith(f"::{suffix}")
