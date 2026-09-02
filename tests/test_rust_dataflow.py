from pathlib import PurePosixPath

from tree_sitter import Node

from zed_i18n_kit.rust_cst import RustCst, iter_named_nodes, parse_rust_cst
from zed_i18n_kit.rust_dataflow import build_dataflow_index, collect_provenance
from zed_i18n_kit.scan_result import ProvenanceRange

PATH = PurePosixPath("crates/demo/src/lib.rs")


def test_provenance_projects_unique_local_function_struct_field() -> None:
    source = b"""
struct Prompt { message: String, detail: String }
fn build(flag: bool) -> Prompt {
    let message = if flag { "Delete?" } else { "Trash?" };
    let detail = "Cannot be undone";
    Prompt { message, detail }
}
fn render(window: &mut Window, flag: bool, cx: &mut Cx) {
    let prompt = build(flag);
    window.prompt(Level::Info, &prompt.message, Some(&prompt.detail), &[], cx);
}
"""
    tree = parse_rust_cst(source)
    primary = _exact_text_node(tree, source, b"&prompt.message")

    provenance = collect_provenance(
        tree, PATH, primary, index=build_dataflow_index(tree)
    )
    texts = _provenance_texts(source, provenance)

    assert b'if flag { "Delete?" } else { "Trash?" }' in texts
    assert b'"Delete?"' in texts
    assert b'"Trash?"' in texts
    assert b'"Cannot be undone"' not in texts


def test_provenance_crosses_same_type_impl_field_initializer() -> None:
    source = b"""
struct View { current_text: String }
impl View {
    fn new(line: usize) -> Self {
        let current_text = format!("Current line: {line}");
        Self { current_text: current_text.into() }
    }
}
impl Render for View {
    fn render(&self) { Label::new(self.current_text.clone()); }
}
"""
    tree = parse_rust_cst(source)
    primary = _exact_text_node(tree, source, b"self.current_text.clone()")

    provenance = collect_provenance(
        tree, PATH, primary, index=build_dataflow_index(tree)
    )

    assert b'format!("Current line: {line}")' in _provenance_texts(source, provenance)


def test_tuple_binding_retains_conditional_rhs_and_branch_literals() -> None:
    source = b"""
fn render(window: &mut Window, flag: bool, cx: &mut Cx) {
    let (message, action) = if flag {
        ("Delete?", "Delete")
    } else {
        ("Trash?", "Trash")
    };
    window.prompt(Level::Info, message, None, &[action], cx);
}
"""
    tree = parse_rust_cst(source)
    primary = tuple(
        node
        for node in iter_named_nodes(tree.root, node_type="identifier")
        if tree.text(node) == b"action"
    )[-1]

    provenance = collect_provenance(
        tree, PATH, primary, index=build_dataflow_index(tree)
    )
    texts = _provenance_texts(source, provenance)

    assert any(text.startswith(b"if flag") for text in texts)
    assert b'"Delete"' in texts
    assert b'"Trash"' in texts


def _exact_text_node(tree: RustCst, source: bytes, text: bytes) -> Node:
    return next(
        node
        for node in iter_named_nodes(tree.root)
        if source[node.start_byte : node.end_byte] == text
    )


def _provenance_texts(
    source: bytes, provenance: tuple[ProvenanceRange, ...]
) -> set[bytes]:
    return {
        source[item.source_span.start_byte : item.source_span.end_byte]
        for item in provenance
    }
