from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path, PurePosixPath

from tree_sitter import Node

from .discovery import DiscoveryError, discover_source_paths
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
from .rust_dataflow import collect_provenance
from .rust_receivers import ReceiverEvidence, resolve_receiver
from .rust_symbols import (
    SourceSymbolTable,
    SymbolResolutionKind,
    build_source_symbol_table,
)
from .scan_profiles import (
    WORKSPACE_SCAN_PROFILE,
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
    disposition_override: Decision | None = None


@dataclass(frozen=True, slots=True)
class RuleMatch:
    rule: SinkRule
    resolution: SymbolResolutionKind
    reason: str


def scan_sources(
    zed_root: Path,
    *,
    profile: ScanProfile = WORKSPACE_SCAN_PROFILE,
) -> ScanResult:
    source_paths = _resolve_source_paths(zed_root, profile)

    occurrences: list[ScanOccurrence] = []
    snapshots: list[SourceFileSnapshot] = []
    parse_failures: list[str] = []
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
            build_source_symbol_table(tree, path)
            if profile.symbol_resolution is SymbolResolutionMode.IMPORT_AWARE
            else None
        )
        occurrences.extend(_scan_source(path, tree, profile, symbol_table))
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
) -> tuple[ScanOccurrence, ...]:
    occurrences: list[ScanOccurrence] = []
    for call in iter_named_nodes(tree.root, node_type="call_expression"):
        if (
            call.has_error
            or is_within_parse_error(call)
            or is_within_test_scope(call, tree.source)
        ):
            continue
        match = _match_rule(call, tree, profile, symbol_table)
        if match is None:
            continue
        for extracted in _extract_slots(match.rule, call, tree.source):
            if (
                extracted.disposition_override is Decision.EXCLUDED
                and match.resolution is SymbolResolutionKind.CANDIDATE
            ):
                continue
            primary = extracted.value_node
            disposition = _classify_disposition(
                primary,
                tree.source,
                resolution=match.resolution,
                override=extracted.disposition_override,
            )
            occurrence_id = _occurrence_id(
                path, match.rule.sink_symbol, extracted.text_slot, primary
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
                    provenance=collect_provenance(tree, path, primary),
                    evidence=(
                        RuleEvidence(
                            match.rule.rule_id,
                            f"{match.reason}; slot {extracted.text_slot} at "
                            f"{call.start_byte}",
                        ),
                    ),
                )
            )
    return tuple(occurrences)


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


def _classify_disposition(
    node: Node,
    source: bytes,
    *,
    resolution: SymbolResolutionKind,
    override: Decision | None,
) -> Decision:
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
