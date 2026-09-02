from pathlib import Path, PurePosixPath

from zed_i18n_kit.rust_cst import iter_named_nodes, parse_rust_cst
from zed_i18n_kit.rust_symbols import (
    ImportBinding,
    SymbolResolutionKind,
    build_controlled_export_index,
    build_source_symbol_table,
)


def test_symbol_table_flattens_imports_aliases_and_ignores_test_cfg() -> None:
    source = b"""
use ui::{Button, Label as UiLabel, prelude::*};
use crate::Toast as LocalToast;
#[cfg(test)]
use impostor::Tooltip;
use ui::Tooltip;
"""
    table = build_source_symbol_table(
        parse_rust_cst(source), PurePosixPath("crates/workspace/src/view.rs")
    )

    assert table.imports == (
        ImportBinding("Button", "ui::Button"),
        ImportBinding("LocalToast", "workspace::Toast"),
        ImportBinding("Tooltip", "ui::Tooltip"),
        ImportBinding("UiLabel", "ui::Label"),
    )
    assert table.wildcard_imports == ("ui::prelude",)
    assert table.resolve_target("UiLabel::new", "ui::Label::new") is not None
    assert table.resolve_target("LocalToast::new", "workspace::Toast::new") is not None
    assert table.resolve_target("Tooltip::text", "impostor::Tooltip::text") is None


def test_symbol_resolution_distinguishes_exact_candidate_and_similar_names() -> None:
    source = b"""
#[cfg(target_os = "linux")]
use ui::Label;
#[cfg(not(target_os = "linux"))]
use alternate::Label;
use ui::prelude::*;
use domain::NotLabel;
"""
    table = build_source_symbol_table(
        parse_rust_cst(source), PurePosixPath("crates/demo/src/lib.rs")
    )

    conditional = table.resolve_target("Label::new", "ui::Label::new")
    wildcard = table.resolve_target("Button::new", "ui::Button::new")

    assert conditional is not None
    assert conditional.kind is SymbolResolutionKind.CANDIDATE
    assert conditional.evidence == (
        "import candidates for 'Label': alternate::Label::new, ui::Label::new"
    )
    assert wildcard is not None
    assert wildcard.kind is SymbolResolutionKind.CANDIDATE
    assert table.resolve_target("NotLabel::new", "ui::Label::new") is None


def test_local_declaration_resolves_to_current_crate() -> None:
    source = b"""
pub struct LanguageServerPromptRequest;
fn demo() {
    LanguageServerPromptRequest::new(level, message);
}
"""
    table = build_source_symbol_table(
        parse_rust_cst(source), PurePosixPath("crates/project/src/lsp_store.rs")
    )

    resolution = table.resolve_target(
        "LanguageServerPromptRequest::new",
        "project::LanguageServerPromptRequest::new",
    )

    assert resolution is not None
    assert resolution.kind is SymbolResolutionKind.EXACT
    assert resolution.evidence == (
        "local declaration 'LanguageServerPromptRequest' in crate 'project'"
    )


def test_parent_module_wildcard_keeps_known_symbol_as_candidate() -> None:
    table = build_source_symbol_table(
        parse_rust_cst(b'use super::*; fn demo() { Tooltip::text("Help"); }'),
        PurePosixPath("crates/agent_ui/src/conversation_view/thread_view.rs"),
    )

    resolution = table.resolve_target("Tooltip::text", "ui::Tooltip::text")

    assert resolution is not None
    assert resolution.kind is SymbolResolutionKind.CANDIDATE
    assert resolution.evidence == "wildcard import candidate from super::*"


def test_imports_and_declarations_do_not_leak_between_inline_modules() -> None:
    source = b"""
mod imported {
    use ui::Label;
    fn render() { Label::new("UI"); }
}
mod unrelated {
    struct Label;
    fn render() { Label::new("Domain value"); }
}
"""
    tree = parse_rust_cst(source)
    table = build_source_symbol_table(tree, PurePosixPath("crates/demo/src/lib.rs"))
    calls = tuple(iter_named_nodes(tree.root, node_type="call_expression"))

    imported = table.resolve_target("Label::new", "ui::Label::new", at=calls[0])
    unrelated = table.resolve_target("Label::new", "ui::Label::new", at=calls[1])

    assert imported is not None
    assert imported.kind is SymbolResolutionKind.EXACT
    assert unrelated is None


def test_explicit_non_ui_import_shadows_wildcard_candidate() -> None:
    source = b"""
use ui::prelude::*;
use domain::Label;
fn render() { Label::new("Domain value"); }
"""
    tree = parse_rust_cst(source)
    table = build_source_symbol_table(tree, PurePosixPath("crates/demo/src/lib.rs"))
    call = next(iter_named_nodes(tree.root, node_type="call_expression"))

    assert table.resolve_target("Label::new", "ui::Label::new", at=call) is None


def test_controlled_export_index_only_upgrades_a_unique_target(
    tmp_path: Path,
) -> None:
    ui_prelude = tmp_path / "crates/ui/src/prelude.rs"
    gpui_prelude = tmp_path / "crates/gpui/src/prelude.rs"
    ui_prelude.parent.mkdir(parents=True)
    gpui_prelude.parent.mkdir(parents=True)
    ui_prelude.write_text(
        "pub use crate::Label;\npub use alternate::Label;\n",
        encoding="utf-8",
    )
    gpui_prelude.write_text("pub use crate::div;\n", encoding="utf-8")
    export_index = build_controlled_export_index(tmp_path)
    source = b'use ui::prelude::*; fn render() { Label::new("text"); }'
    tree = parse_rust_cst(source)
    table = build_source_symbol_table(
        tree,
        PurePosixPath("crates/demo/src/lib.rs"),
        export_index=export_index,
    )
    call = next(iter_named_nodes(tree.root, node_type="call_expression"))

    resolution = table.resolve_target("Label::new", "ui::Label::new", at=call)

    assert resolution is not None
    assert resolution.kind is SymbolResolutionKind.CANDIDATE


def test_controlled_export_index_upgrades_one_explicit_export(tmp_path: Path) -> None:
    ui_prelude = tmp_path / "crates/ui/src/prelude.rs"
    gpui_prelude = tmp_path / "crates/gpui/src/prelude.rs"
    ui_prelude.parent.mkdir(parents=True)
    gpui_prelude.parent.mkdir(parents=True)
    ui_prelude.write_text("pub use crate::Label;\n", encoding="utf-8")
    gpui_prelude.write_text("pub use crate::div;\n", encoding="utf-8")
    export_index = build_controlled_export_index(tmp_path)
    source = b'use ui::prelude::*; fn render() { Label::new("text"); }'
    tree = parse_rust_cst(source)
    table = build_source_symbol_table(
        tree,
        PurePosixPath("crates/demo/src/lib.rs"),
        export_index=export_index,
    )
    call = next(iter_named_nodes(tree.root, node_type="call_expression"))

    resolution = table.resolve_target("Label::new", "ui::Label::new", at=call)

    assert resolution is not None
    assert resolution.kind is SymbolResolutionKind.EXACT
