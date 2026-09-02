from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path, PurePosixPath

from tree_sitter import Node

from .golden import Decision
from .rust_cst import (
    RustCst,
    collect_parse_error_nodes,
    is_within_parse_error,
    is_within_test_scope,
    iter_named_nodes,
    parse_rust_cst,
    source_span_for_node,
)
from .scan_profiles import (
    PROTOTYPE_SCAN_PROFILE,
    CallShape,
    ScanProfile,
    SinkRule,
    SlotExtractionStrategy,
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


@dataclass(frozen=True, slots=True)
class ExtractedSlot:
    value_node: Node
    text_slot: str


def scan_sources(
    zed_root: Path,
    *,
    profile: ScanProfile = PROTOTYPE_SCAN_PROFILE,
) -> ScanResult:
    if not profile.source_paths:
        raise ScannerError("scan profile source paths cannot be empty")

    occurrences: list[ScanOccurrence] = []
    snapshots: list[SourceFileSnapshot] = []
    parse_failures: list[str] = []
    for path in sorted(profile.source_paths):
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
        occurrences.extend(_scan_source(path, tree, profile.sink_rules))
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
        else f"all {len(profile.source_paths)} source files parsed without errors"
    )
    metadata = ScanMetadata(
        zed_commit=resolve_git_head(zed_root),
        tool_version=version("zed-i18n-kit"),
        rule_pack_version=profile.rule_pack_version,
        config_hash=_config_hash(profile),
        scan_scope=tuple(sorted(profile.source_paths)),
        source_files=tuple(snapshots),
        capability_probes=(
            CapabilityProbe(
                "tree-sitter-rust-binding",
                CapabilityProbeStatus.PASSED,
                f"tree-sitter {version('tree-sitter')}; "
                f"tree-sitter-rust {version('tree-sitter-rust')}",
            ),
            CapabilityProbe("prototype-error-free-parse", parse_status, parse_details),
        ),
    )
    return ScanResult(
        schema_version=SCAN_RESULT_SCHEMA_VERSION,
        metadata=metadata,
        occurrences=tuple(occurrences),
    )


def _scan_source(
    path: PurePosixPath, tree: RustCst, sink_rules: tuple[SinkRule, ...]
) -> tuple[ScanOccurrence, ...]:
    occurrences: list[ScanOccurrence] = []
    for call in iter_named_nodes(tree.root, node_type="call_expression"):
        if (
            call.has_error
            or is_within_parse_error(call)
            or is_within_test_scope(call, tree.source)
        ):
            continue
        rule = _match_rule(call, tree.source, sink_rules)
        if rule is None:
            continue
        for extracted in _extract_slots(rule, call, tree.source):
            primary = extracted.value_node
            disposition = _classify_disposition(primary, tree.source)
            occurrence_id = _occurrence_id(
                path, rule.sink_symbol, extracted.text_slot, primary
            )
            occurrences.append(
                ScanOccurrence(
                    occurrence_id=occurrence_id,
                    path=path,
                    primary_span=source_span_for_node(primary),
                    syntax_kind=primary.type,
                    sink_symbol=rule.sink_symbol,
                    text_slot=extracted.text_slot,
                    disposition=disposition,
                    provenance=_collect_provenance(tree, path, primary),
                    evidence=(
                        RuleEvidence(
                            rule.rule_id,
                            f"matched {rule.call_shape.value} suffix "
                            f"{rule.callee_suffix!r} at {call.start_byte}",
                        ),
                    ),
                )
            )
    return tuple(occurrences)


def _match_rule(
    call: Node, source: bytes, sink_rules: tuple[SinkRule, ...]
) -> SinkRule | None:
    function = call.child_by_field_name("function")
    if function is None:
        return None
    function_text = _node_text(function, source)
    for rule in sink_rules:
        if rule.call_shape is CallShape.METHOD:
            if function.type != "field_expression":
                continue
            field = function.child_by_field_name("field")
            if field is not None and _node_text(field, source) == rule.callee_suffix:
                return rule
        elif _matches_path_suffix(function_text, rule.callee_suffix):
            return rule
    return None


def _extract_slots(
    rule: SinkRule, call: Node, source: bytes
) -> tuple[ExtractedSlot, ...]:
    arguments = call.child_by_field_name("arguments")
    if arguments is None:
        return ()
    values = arguments.named_children
    if rule.slot_extraction is SlotExtractionStrategy.CHILD_LITERAL:
        if values and values[0].type in {"string_literal", "raw_string_literal"}:
            return (ExtractedSlot(values[0], "arg[0]"),)
        return ()
    if rule.slot_extraction is SlotExtractionStrategy.FIRST_ARGUMENT:
        return (ExtractedSlot(values[0], "arg[0]"),) if values else ()
    if rule.slot_extraction is SlotExtractionStrategy.SECOND_ARGUMENT:
        return (ExtractedSlot(values[1], "arg[1]"),) if len(values) > 1 else ()

    slots: list[ExtractedSlot] = []
    if len(values) > 1:
        slots.append(ExtractedSlot(values[1], "arg[1]"))
    if len(values) > 2:
        detail = _unwrap_some(values[2], source)
        if detail is not None:
            slots.append(ExtractedSlot(detail, "arg[2].Some"))
    if len(values) > 3:
        action_array = _unwrap_reference(values[3])
        if action_array.type == "array_expression":
            slots.extend(
                ExtractedSlot(action, f"arg[3][{index}]")
                for index, action in enumerate(action_array.named_children)
            )
    return tuple(slots)


def _unwrap_some(node: Node, source: bytes) -> Node | None:
    if node.type != "call_expression":
        return None
    function = node.child_by_field_name("function")
    arguments = node.child_by_field_name("arguments")
    if (
        function is None
        or arguments is None
        or _node_text(function, source) != "Some"
        or len(arguments.named_children) != 1
    ):
        return None
    return arguments.named_children[0]


def _unwrap_reference(node: Node) -> Node:
    if node.type != "reference_expression":
        return node
    value = node.child_by_field_name("value")
    return value if value is not None else node


def _classify_disposition(node: Node, source: bytes) -> Decision:
    if node.type not in {"string_literal", "raw_string_literal"}:
        return Decision.REVIEW_REQUIRED
    text = _node_text(node, source)
    if any(character.isalpha() for character in text):
        return Decision.CONFIRMED
    return Decision.EXCLUDED


def _collect_provenance(
    tree: RustCst, path: PurePosixPath, primary: Node
) -> tuple[ProvenanceRange, ...]:
    ranges: set[ProvenanceRange] = set()
    visited: set[tuple[int, int, str]] = set()
    scope = _enclosing_function_or_root(primary, tree.root)
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
        binding = _find_prior_binding(node, scope, tree.source)
        if binding is not None:
            _visit_provenance_node(
                tree, path, binding, scope, ranges, visited, record=True
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


def _find_prior_binding(identifier: Node, scope: Node, source: bytes) -> Node | None:
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


def _enclosing_function_or_root(node: Node, root: Node) -> Node:
    current: Node | None = node
    while current is not None:
        if current.type == "function_item":
            return current
        current = current.parent
    return root


def _occurrence_id(
    path: PurePosixPath, sink_symbol: str, text_slot: str, node: Node
) -> str:
    identity = (
        f"{path.as_posix()}|{sink_symbol}|{text_slot}|{node.start_byte}|{node.end_byte}"
    )
    return "occ-" + hashlib.sha256(identity.encode()).hexdigest()[:20]


def _config_hash(profile: ScanProfile) -> str:
    payload = {
        "rules": [
            {
                "callee_suffix": rule.callee_suffix,
                "rule_id": rule.rule_id,
                "call_shape": rule.call_shape.value,
                "sink_symbol": rule.sink_symbol,
                "slot_extraction": rule.slot_extraction.value,
            }
            for rule in profile.sink_rules
        ],
        "source_paths": sorted(path.as_posix() for path in profile.source_paths),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _matches_path_suffix(function_text: str, suffix: str) -> bool:
    return function_text == suffix or function_text.endswith(f"::{suffix}")
