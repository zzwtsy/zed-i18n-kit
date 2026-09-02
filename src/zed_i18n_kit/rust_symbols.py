from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from tree_sitter import Node

from .rust_cst import RustCst, is_within_test_scope, iter_named_nodes, parse_rust_cst


class SymbolResolutionKind(StrEnum):
    EXACT = "exact"
    CANDIDATE = "candidate"


@dataclass(frozen=True, slots=True, order=True)
class ImportBinding:
    local_name: str
    target: str


@dataclass(frozen=True, slots=True, order=True)
class ExportBinding:
    module: str
    local_name: str
    target: str


@dataclass(frozen=True, slots=True)
class ControlledExportIndex:
    """Explicit exports from the fixed UI preludes used by scanner rules."""

    bindings: tuple[ExportBinding, ...]
    indexed_modules: tuple[str, ...]
    failures: tuple[str, ...]

    def targets_for(self, module: str, local_name: str) -> tuple[str, ...] | None:
        if module not in self.indexed_modules:
            return None
        return tuple(
            binding.target
            for binding in self.bindings
            if binding.module == module and binding.local_name == local_name
        )


@dataclass(frozen=True, slots=True)
class SymbolResolution:
    kind: SymbolResolutionKind
    target: str
    evidence: str


@dataclass(frozen=True, slots=True)
class _Scope:
    namespace_start: int
    namespace_end: int
    block_start: int
    block_end: int
    block_depth: int


@dataclass(frozen=True, slots=True)
class _ScopedImport:
    binding: ImportBinding
    scope: _Scope


@dataclass(frozen=True, slots=True)
class _ScopedWildcard:
    target: str
    scope: _Scope


@dataclass(frozen=True, slots=True)
class _ScopedDeclaration:
    name: str
    scope: _Scope


@dataclass(frozen=True, slots=True)
class SourceSymbolTable:
    crate_name: str
    imports: tuple[ImportBinding, ...]
    wildcard_imports: tuple[str, ...]
    local_declarations: tuple[str, ...]
    _scoped_wildcards: tuple[_ScopedWildcard, ...]
    _scoped_declarations: tuple[_ScopedDeclaration, ...]
    _imports_by_name: Mapping[str, tuple[_ScopedImport, ...]]
    _export_index: ControlledExportIndex | None = None

    def resolve_target(
        self,
        observed_symbol: str,
        target_symbol: str,
        *,
        at: Node | None = None,
    ) -> SymbolResolution | None:
        observed = _symbol_segments(observed_symbol)
        target = _symbol_segments(target_symbol)
        if not observed or not target:
            return None

        normalized_observed = _normalize_crate_path(observed, self.crate_name)
        if normalized_observed == target:
            return SymbolResolution(
                SymbolResolutionKind.EXACT,
                target_symbol,
                f"direct path {observed_symbol!r}",
            )

        imported_candidates = tuple(
            sorted(
                {
                    (*_symbol_segments(binding.target), *observed[1:])
                    for binding in self._visible_imports(observed[0], at)
                }
            )
        )
        if target in imported_candidates:
            rendered = ", ".join("::".join(path) for path in imported_candidates)
            kind = (
                SymbolResolutionKind.EXACT
                if imported_candidates == (target,)
                else SymbolResolutionKind.CANDIDATE
            )
            return SymbolResolution(
                kind,
                target_symbol,
                f"import candidates for {observed[0]!r}: {rendered}",
            )
        if imported_candidates:
            return None

        has_local_declaration = self._has_visible_local_declaration(observed[0], at)
        if has_local_declaration:
            local_candidate = (self.crate_name, observed[0], *observed[1:])
            if local_candidate == target:
                return SymbolResolution(
                    SymbolResolutionKind.EXACT,
                    target_symbol,
                    f"local declaration {observed[0]!r} in crate {self.crate_name!r}",
                )
            return None

        visible_wildcards = self._visible_wildcards(at)
        indexed_candidates: set[tuple[str, ...]] = set()
        has_unknown_wildcard = False
        if self._export_index is not None:
            for wildcard in visible_wildcards:
                exported_targets = self._export_index.targets_for(wildcard, observed[0])
                if exported_targets is None:
                    has_unknown_wildcard = has_unknown_wildcard or (
                        _wildcard_can_expose_target(observed, target, (wildcard,))
                    )
                    continue
                indexed_candidates.update(
                    (*_symbol_segments(exported_target), *observed[1:])
                    for exported_target in exported_targets
                )
            if target in indexed_candidates:
                rendered = ", ".join(
                    "::".join(path) for path in sorted(indexed_candidates)
                )
                kind = (
                    SymbolResolutionKind.EXACT
                    if indexed_candidates == {target} and not has_unknown_wildcard
                    else SymbolResolutionKind.CANDIDATE
                )
                return SymbolResolution(
                    kind,
                    target_symbol,
                    f"controlled wildcard export candidates: {rendered}",
                )
            if indexed_candidates:
                return None
        if _wildcard_can_expose_target(observed, target, visible_wildcards):
            rendered = ", ".join(f"{path}::*" for path in visible_wildcards)
            return SymbolResolution(
                SymbolResolutionKind.CANDIDATE,
                target_symbol,
                f"wildcard import candidate from {rendered}",
            )
        return None

    def _visible_imports(
        self, local_name: str, at: Node | None
    ) -> tuple[ImportBinding, ...]:
        if at is None:
            return tuple(
                binding for binding in self.imports if binding.local_name == local_name
            )
        location_scope = _scope_for_node(at)
        applicable = tuple(
            scoped
            for scoped in self._imports_by_name.get(local_name, ())
            if _scope_applies(scoped.scope, location_scope, at.start_byte)
        )
        if not applicable:
            return ()
        nearest_depth = max(scoped.scope.block_depth for scoped in applicable)
        return tuple(
            scoped.binding
            for scoped in applicable
            if scoped.scope.block_depth == nearest_depth
        )

    def _visible_wildcards(self, at: Node | None) -> tuple[str, ...]:
        if at is None:
            return self.wildcard_imports
        location_scope = _scope_for_node(at)
        return tuple(
            scoped.target
            for scoped in self._scoped_wildcards
            if _scope_applies(scoped.scope, location_scope, at.start_byte)
        )

    def _has_visible_local_declaration(self, name: str, at: Node | None) -> bool:
        if at is None:
            return name in self.local_declarations
        namespace = _scope_for_node(at)
        return any(
            declaration.name == name
            and _scope_applies(declaration.scope, namespace, at.start_byte)
            for declaration in self._scoped_declarations
        )


def build_source_symbol_table(
    tree: RustCst,
    path: PurePosixPath,
    *,
    export_index: ControlledExportIndex | None = None,
) -> SourceSymbolTable:
    crate_name = _crate_name(path)
    imports: list[ImportBinding] = []
    wildcard_imports: list[str] = []
    local_declarations: set[str] = set()
    scoped_imports: list[_ScopedImport] = []
    scoped_wildcards: list[_ScopedWildcard] = []
    scoped_declarations: list[_ScopedDeclaration] = []
    declaration_types = {"enum_item", "struct_item", "trait_item", "type_item"}
    for node in iter_named_nodes(tree.root):
        if node.type == "use_declaration":
            if is_within_test_scope(node, tree.source):
                continue
            argument = node.child_by_field_name("argument")
            if argument is not None:
                declaration_imports: list[ImportBinding] = []
                declaration_wildcards: list[str] = []
                _collect_use_bindings(
                    argument,
                    (),
                    crate_name,
                    declaration_imports,
                    declaration_wildcards,
                    tree.source,
                )
                scope = _scope_for_node(node)
                imports.extend(declaration_imports)
                wildcard_imports.extend(declaration_wildcards)
                scoped_imports.extend(
                    _ScopedImport(binding, scope) for binding in declaration_imports
                )
                scoped_wildcards.extend(
                    _ScopedWildcard(target, scope) for target in declaration_wildcards
                )
        elif node.type in declaration_types and not is_within_test_scope(
            node, tree.source
        ):
            name = node.child_by_field_name("name")
            if name is not None:
                declaration_name = _node_text(name, tree.source)
                local_declarations.add(declaration_name)
                namespace = _scope_for_node(node)
                scoped_declarations.append(
                    _ScopedDeclaration(declaration_name, namespace)
                )
    return SourceSymbolTable(
        crate_name=crate_name,
        imports=tuple(sorted(set(imports))),
        wildcard_imports=tuple(sorted(set(wildcard_imports))),
        local_declarations=tuple(sorted(local_declarations)),
        _scoped_wildcards=tuple(scoped_wildcards),
        _scoped_declarations=tuple(scoped_declarations),
        _imports_by_name=_index_imports(scoped_imports),
        _export_index=export_index,
    )


_CONTROLLED_EXPORT_SOURCES = (
    ("gpui::prelude", PurePosixPath("crates/gpui/src/prelude.rs"), "gpui"),
    ("ui::prelude", PurePosixPath("crates/ui/src/prelude.rs"), "ui"),
)


def build_controlled_export_index(zed_root: Path) -> ControlledExportIndex:
    """Parse the fixed checkout's public UI prelude exports.

    Only the two declared prelude modules participate. Unknown wildcard
    modules remain unresolved candidates, and multiple exported targets never
    upgrade a symbol to exact.
    """

    direct: dict[str, set[ImportBinding]] = {}
    wildcard_exports: dict[str, set[str]] = {}
    indexed_modules: list[str] = []
    failures: list[str] = []
    for module, relative_path, crate_name in _CONTROLLED_EXPORT_SOURCES:
        source_path = zed_root / relative_path
        try:
            source = source_path.read_bytes()
        except OSError as error:
            failures.append(f"{relative_path}: {error}")
            continue
        tree = parse_rust_cst(source)
        if tree.has_errors:
            failures.append(f"{relative_path}: Rust CST contains parse errors")
            continue
        module_bindings: list[ImportBinding] = []
        module_wildcards: list[str] = []
        for declaration in iter_named_nodes(tree.root, node_type="use_declaration"):
            if not tree.text(declaration).lstrip().startswith(b"pub use "):
                continue
            argument = declaration.child_by_field_name("argument")
            if argument is not None:
                _collect_use_bindings(
                    argument,
                    (),
                    crate_name,
                    module_bindings,
                    module_wildcards,
                    source,
                )
        direct[module] = {
            binding for binding in module_bindings if binding.local_name != "_"
        }
        wildcard_exports[module] = set(module_wildcards)
        indexed_modules.append(module)

    resolved: dict[str, set[ImportBinding]] = {
        module: set(bindings) for module, bindings in direct.items()
    }
    for _ in range(len(indexed_modules)):
        changed = False
        for module in indexed_modules:
            before = len(resolved[module])
            for wildcard in wildcard_exports[module]:
                resolved[module].update(resolved.get(wildcard, ()))
            changed = changed or len(resolved[module]) != before
        if not changed:
            break

    bindings = tuple(
        sorted(
            ExportBinding(module, binding.local_name, binding.target)
            for module, module_bindings in resolved.items()
            for binding in module_bindings
        )
    )
    return ControlledExportIndex(
        bindings=bindings,
        indexed_modules=tuple(sorted(indexed_modules)),
        failures=tuple(failures),
    )


def _collect_use_bindings(
    node: Node,
    prefix: tuple[str, ...],
    crate_name: str,
    imports: list[ImportBinding],
    wildcard_imports: list[str],
    source: bytes,
) -> None:
    if node.type == "scoped_use_list":
        path = node.child_by_field_name("path")
        use_list = node.child_by_field_name("list")
        if path is None or use_list is None:
            return
        scoped_prefix = (*prefix, *_path_segments(path, source))
        for child in use_list.named_children:
            _collect_use_bindings(
                child,
                scoped_prefix,
                crate_name,
                imports,
                wildcard_imports,
                source,
            )
        return

    if node.type == "use_list":
        for child in node.named_children:
            _collect_use_bindings(
                child,
                prefix,
                crate_name,
                imports,
                wildcard_imports,
                source,
            )
        return

    if node.type == "use_as_clause":
        path = node.child_by_field_name("path")
        alias = node.child_by_field_name("alias")
        if path is None or alias is None:
            return
        target = _joined_use_path(prefix, path, crate_name, source)
        if target:
            imports.append(ImportBinding(_node_text(alias, source), target))
        return

    if node.type == "use_wildcard":
        wildcard_path = (*prefix, *_path_segments(node, source))
        normalized = _normalize_crate_path(wildcard_path, crate_name)
        if normalized:
            wildcard_imports.append("::".join(normalized))
        return

    if node.type == "self":
        normalized = _normalize_crate_path(prefix, crate_name)
        if normalized:
            imports.append(ImportBinding(normalized[-1], "::".join(normalized)))
        return

    path = (*prefix, *_path_segments(node, source))
    normalized = _normalize_crate_path(path, crate_name)
    if normalized:
        imports.append(ImportBinding(normalized[-1], "::".join(normalized)))


def _joined_use_path(
    prefix: tuple[str, ...], node: Node, crate_name: str, source: bytes
) -> str:
    segments = _path_segments(node, source)
    if segments == ("self",):
        path = prefix
    else:
        path = (*prefix, *segments)
    return "::".join(_normalize_crate_path(path, crate_name))


def _path_segments(node: Node, source: bytes) -> tuple[str, ...]:
    if node.type == "use_wildcard" and node.named_children:
        return _path_segments(node.named_children[0], source)
    return _symbol_segments(_node_text(node, source).removesuffix("::*"))


def _symbol_segments(symbol: str) -> tuple[str, ...]:
    return tuple(segment for segment in symbol.split("::") if segment)


def _normalize_crate_path(path: tuple[str, ...], crate_name: str) -> tuple[str, ...]:
    if path and path[0] == "crate":
        return (crate_name, *path[1:])
    return path


def _wildcard_can_expose_target(
    observed: tuple[str, ...],
    target: tuple[str, ...],
    wildcard_imports: tuple[str, ...],
) -> bool:
    if len(observed) >= len(target) or target[-len(observed) :] != observed:
        return False
    for wildcard in wildcard_imports:
        wildcard_segments = _symbol_segments(wildcard)
        if not wildcard_segments:
            continue
        if wildcard_segments[0] == target[0] or wildcard_segments[0] in {
            "self",
            "super",
        }:
            return True
    return False


def _scope_for_node(node: Node) -> _Scope:
    current: Node | None = node.parent
    nearest_block: Node | None = None
    block_depth = 0
    while current is not None:
        if current.type == "block":
            if nearest_block is None:
                nearest_block = current
            block_depth += 1
        is_module_declaration_list = (
            current.type == "declaration_list"
            and current.parent is not None
            and current.parent.type == "mod_item"
        )
        if current.type == "source_file" or is_module_declaration_list:
            block = nearest_block if nearest_block is not None else current
            return _Scope(
                namespace_start=current.start_byte,
                namespace_end=current.end_byte,
                block_start=block.start_byte,
                block_end=block.end_byte,
                block_depth=block_depth,
            )
        current = current.parent
    return _Scope(
        namespace_start=node.start_byte,
        namespace_end=node.end_byte,
        block_start=node.start_byte,
        block_end=node.end_byte,
        block_depth=0,
    )


def _index_imports(
    scoped_imports: list[_ScopedImport],
) -> dict[str, tuple[_ScopedImport, ...]]:
    grouped: dict[str, list[_ScopedImport]] = {}
    for scoped in scoped_imports:
        grouped.setdefault(scoped.binding.local_name, []).append(scoped)
    return {local_name: tuple(bindings) for local_name, bindings in grouped.items()}


def _scope_applies(
    binding_scope: _Scope, location_scope: _Scope, location_byte: int
) -> bool:
    return (
        binding_scope.namespace_start == location_scope.namespace_start
        and binding_scope.namespace_end == location_scope.namespace_end
        and binding_scope.block_start <= location_byte <= binding_scope.block_end
    )


def _crate_name(path: PurePosixPath) -> str:
    parts = path.parts
    if len(parts) >= 2 and parts[0] == "crates":
        return parts[1]
    return "crate"


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
