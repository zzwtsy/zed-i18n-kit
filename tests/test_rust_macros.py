from zed_i18n_kit.rust_cst import iter_named_nodes, parse_rust_cst
from zed_i18n_kit.rust_macros import parse_allowlisted_expression_macros


def test_secondary_macro_cst_preserves_utf8_byte_offsets() -> None:
    source = 'fn render() { let 名称 = "值"; vec![Label::new(format!("你好 {名称}"))]; }\n'.encode()
    tree = parse_rust_cst(source)

    expanded = parse_allowlisted_expression_macros(tree)

    assert expanded is not None
    label_call = next(
        node
        for node in iter_named_nodes(expanded.tree.root, node_type="call_expression")
        if expanded.tree.text(node).startswith(b"Label::new")
    )
    assert source[label_call.start_byte : label_call.end_byte] == (
        'Label::new(format!("你好 {名称}"))'.encode()
    )
    assert expanded.parse_error_ranges == ()


def test_unknown_expression_macro_is_not_reparsed() -> None:
    tree = parse_rust_cst(b'fn render() { unknown![Label::new("text")]; }\n')

    assert parse_allowlisted_expression_macros(tree) is None
