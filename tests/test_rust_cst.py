from zed_i18n_kit.golden import SourceSpan
from zed_i18n_kit.rust_cst import (
    collect_parse_error_nodes,
    is_within_test_scope,
    iter_named_nodes,
    parse_rust_cst,
)


def test_exact_and_smallest_containing_nodes_use_utf8_byte_ranges() -> None:
    source = 'fn demo() { label("删除"); }\n'.encode()
    tree = parse_rust_cst(source)
    anchor = '"删除"'.encode()
    start = source.index(anchor)
    exact_span = SourceSpan(start, start + len(anchor))

    exact = tree.exact_named_node(exact_span)
    partial = tree.smallest_named_node_containing(
        SourceSpan(start + 1, start + len(anchor) - 1)
    )

    assert exact is not None
    assert exact.type == "string_literal"
    assert partial is not None
    assert partial.type == "string_content"
    assert tree.text(exact) == anchor


def test_parse_errors_are_exposed_without_discarding_safe_subtrees() -> None:
    source = b'fn demo() { let value = ; label("Still parsed"); }'
    tree = parse_rust_cst(source)

    assert tree.has_errors
    assert collect_parse_error_nodes(tree)
    assert any(
        tree.text(node) == b'"Still parsed"'
        for node in iter_named_nodes(tree.root, node_type="string_literal")
    )


def test_cfg_test_and_tests_module_are_classified_as_test_scope() -> None:
    source = b"""
#[cfg(test)]
fn fixture() { Label::new("fixture"); }
mod tests { fn nested() { Label::new("nested"); } }
#[cfg(all(feature = "x", test))]
fn test_required_in_all() { Label::new("all-test"); }
#[cfg(not(not(test)))]
fn nested_not_test() { Label::new("nested-not-test"); }
#[cfg(any(test, feature = "test-support"))]
fn explicit_test_support() { Label::new("test-support"); }
#[cfg(any(test, target_os = "linux"))]
fn test_not_required_in_any() { Label::new("any-production"); }
#[cfg(not(test))]
fn explicit_non_test() { Label::new("non-test"); }
fn production() { Label::new("product"); }
"""
    tree = parse_rust_cst(source)
    calls = tuple(iter_named_nodes(tree.root, node_type="call_expression"))

    assert [is_within_test_scope(call, source) for call in calls] == [
        True,
        True,
        True,
        True,
        True,
        False,
        False,
        False,
    ]
