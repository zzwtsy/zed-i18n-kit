from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pytest

from zed_i18n_kit.cst_canonical import (
    CanonicalCstError,
    _call_matches_symbol,
    _canonical_origin,
    validate_corpus_cst,
)
from zed_i18n_kit.golden import (
    CorpusManifest,
    Decision,
    ExpectedPresence,
    Feature,
    GoldenCorpus,
    GoldenSample,
    Ownership,
    ReviewState,
    SinkKind,
    SourceScope,
    SourceSpan,
    SubjectKind,
)

DEFAULT_SOURCE_PATH = PurePosixPath("crates/demo/src/lib.rs")


@dataclass(frozen=True)
class _Fixture:
    root: Path
    path: PurePosixPath
    source: bytes


def test_sink_slot_requires_the_actual_argument_expression(tmp_path: Path) -> None:
    fixture = _fixture(
        tmp_path, 'use ui::Label;\nfn render() { Label::new("删除"); }\n'
    )
    sample = _sample(
        fixture,
        _span(fixture.source, 'Label::new("删除")'),
        subject_kind=SubjectKind.SINK_SLOT,
        sink_symbol="ui::Label::new",
        text_slot="arg[0]",
        anchor='Label::new("删除")',
    )

    with pytest.raises(CanonicalCstError, match=r"non-canonical.*string_literal"):
        validate_corpus_cst(_corpus(sample), fixture.root)


@pytest.mark.parametrize(
    ("source", "anchor", "features"),
    (
        (
            'fn render() { show("删除"); }\n',
            'show("删除");',
            frozenset({Feature.DIRECT_LITERAL}),
        ),
        (
            'fn render() { let label = "删除"; }\n',
            'let label = "删除";',
            frozenset({Feature.LOCAL_VARIABLE}),
        ),
    ),
)
def test_expression_origin_rejects_statement_and_let_declaration_spans(
    tmp_path: Path,
    source: str,
    anchor: str,
    features: frozenset[Feature],
) -> None:
    fixture = _fixture(tmp_path, source)
    sample = _sample(
        fixture,
        _span(fixture.source, anchor),
        subject_kind=SubjectKind.EXPRESSION_ORIGIN,
        features=features,
        anchor=anchor,
    )

    with pytest.raises(CanonicalCstError, match="non-canonical"):
        validate_corpus_cst(_corpus(sample), fixture.root)


def test_canonical_sink_argument_and_utf8_span_are_accepted(tmp_path: Path) -> None:
    fixture = _fixture(
        tmp_path, 'use ui::Label;\nfn render() { Label::new("删除"); }\n'
    )
    span = _span(fixture.source, '"删除"')
    sample = _sample(
        fixture,
        span,
        subject_kind=SubjectKind.SINK_SLOT,
        sink_symbol="ui::Label::new",
        text_slot="arg[0]",
        anchor='"删除"',
    )

    result = validate_corpus_cst(_corpus(sample), fixture.root)

    assert len(result) == 1
    assert result[0].node_kind == "string_literal"
    assert result[0].source_span == span


def test_tooltip_simple_canonicalizes_direct_and_dynamic_first_arguments(
    tmp_path: Path,
) -> None:
    source = (
        "use ui::Tooltip;\n"
        "fn render() {\n"
        '    Tooltip::simple("直接提示", cx);\n'
        "    Tooltip::simple(dynamic_label, cx);\n"
        "}\n"
    )
    fixture = _fixture(tmp_path, source)
    direct = _sample(
        fixture,
        _span(fixture.source, '"直接提示"'),
        subject_kind=SubjectKind.SINK_SLOT,
        sink_symbol="ui::Tooltip::simple",
        text_slot="arg[0]",
        anchor='"直接提示"',
    )
    dynamic = _sample(
        fixture,
        _span(fixture.source, "dynamic_label"),
        subject_kind=SubjectKind.SINK_SLOT,
        sink_symbol="ui::Tooltip::simple",
        text_slot="arg[0]",
        anchor="dynamic_label",
    )

    direct_result = validate_corpus_cst(_corpus(direct), fixture.root)
    dynamic_result = validate_corpus_cst(_corpus(dynamic), fixture.root)

    assert direct_result[0].node_kind == "string_literal"
    assert dynamic_result[0].node_kind == "identifier"


def test_tooltip_simple_canonical_rejects_same_name_impostor(tmp_path: Path) -> None:
    source = (
        'use impostor::Tooltip;\nfn render() { Tooltip::simple("伪造提示", cx); }\n'
    )
    fixture = _fixture(tmp_path, source)
    sample = _sample(
        fixture,
        _span(fixture.source, '"伪造提示"'),
        subject_kind=SubjectKind.SINK_SLOT,
        sink_symbol="ui::Tooltip::simple",
        text_slot="arg[0]",
        anchor='"伪造提示"',
    )

    with pytest.raises(CanonicalCstError, match="cannot identify"):
        validate_corpus_cst(_corpus(sample), fixture.root)


def test_sink_slot_resolves_option_and_array_value_paths(tmp_path: Path) -> None:
    source = (
        "use gpui::Window;\n"
        "fn render(window: &mut Window, detail: &str) { "
        'window.prompt(Level::Info, "问题", Some(&detail), &["是", "否"], cx); }\n'
    )
    fixture = _fixture(tmp_path, source)
    values = (
        ('"问题"', "arg[1]", "string_literal"),
        ("&detail", "arg[2].Some", "reference_expression"),
        ('"是"', "arg[3][0]", "string_literal"),
        ('"否"', "arg[3][1]", "string_literal"),
    )
    samples = [
        _sample(
            fixture,
            _span(fixture.source, anchor),
            subject_kind=SubjectKind.SINK_SLOT,
            sink_symbol="gpui::Window::prompt",
            text_slot=text_slot,
            anchor=anchor,
        )
        for anchor, text_slot, _ in values
    ]

    result = validate_corpus_cst(_corpus(*samples), fixture.root)

    assert [item.node_kind for item in result] == [
        node_kind for _, _, node_kind in values
    ]


def test_origin_sink_slot_constraint_does_not_cross_a_neighboring_argument(
    tmp_path: Path,
) -> None:
    fixture = _fixture(
        tmp_path,
        "use ui::Button;\n"
        'fn render(name: &str) { Button::new(format!("identity {name}"), "标签"); }\n',
    )
    sample = _sample(
        fixture,
        _span(fixture.source, 'format!("identity {name}")'),
        subject_kind=SubjectKind.EXPRESSION_ORIGIN,
        sink_symbol="ui::Button::new",
        text_slot="arg[1]",
        features=frozenset({Feature.FORMAT_TEMPLATE}),
        anchor='format!("identity {name}")',
    )

    with pytest.raises(CanonicalCstError, match=r"cannot identify.*expression_origin"):
        validate_corpus_cst(_corpus(sample), fixture.root)


def test_origin_sink_constraint_ignores_unrelated_enclosing_calls(
    tmp_path: Path,
) -> None:
    source = (
        "use ui::Label;\n"
        "fn render(cx: &mut Cx, name: &str) {\n"
        "    cx.spawn_in(window, async move {\n"
        '        let text = format!("你好 {name}");\n'
        "        save(text);\n"
        "    });\n"
        "}\n"
    )
    fixture = _fixture(tmp_path, source)
    expression = 'format!("你好 {name}")'
    sample = _sample(
        fixture,
        _span(fixture.source, expression),
        subject_kind=SubjectKind.EXPRESSION_ORIGIN,
        sink_symbol="ui::Label::new",
        features=frozenset({Feature.FORMAT_TEMPLATE}),
        anchor=expression,
    )

    result = validate_corpus_cst(_corpus(sample), fixture.root)

    assert result[0].node_kind == "macro_invocation"


@pytest.mark.parametrize(
    ("source", "target", "expected"),
    (
        (
            'use ui::Label;\nfn render() { Label::new("标签"); }\n',
            "ui::Label::new",
            True,
        ),
        (
            'use ui::Label as UiLabel;\nfn render() { UiLabel::new("标签"); }\n',
            "ui::Label::new",
            True,
        ),
        ('fn render() { ui::Label::new("标签"); }\n', "ui::Label::new", True),
        (
            'use domain::Label;\nfn render() { Label::new("领域值"); }\n',
            "ui::Label::new",
            False,
        ),
        (
            'use gpui::div;\nfn render() { div().child("标签"); }\n',
            "gpui::ParentElement::child",
            True,
        ),
        (
            'fn render(value: Other) { value.child("领域值"); }\n',
            "gpui::ParentElement::child",
            False,
        ),
    ),
)
def test_sink_symbol_requires_unique_import_or_receiver_evidence(
    tmp_path: Path,
    source: str,
    target: str,
    expected: bool,
) -> None:
    fixture = _fixture(tmp_path, source)
    from zed_i18n_kit.rust_cst import iter_named_nodes, parse_rust_cst
    from zed_i18n_kit.rust_symbols import build_source_symbol_table

    tree = parse_rust_cst(fixture.source)
    symbol_table = build_source_symbol_table(tree, fixture.path)
    call = next(iter_named_nodes(tree.root, node_type="call_expression"))

    assert _call_matches_symbol(call, target, tree, symbol_table) is expected


def test_status_toast_origin_uses_message_not_constructor_identifier(
    tmp_path: Path,
) -> None:
    source = (
        "use notifications::status_toast::StatusToast;\n"
        "fn render(message: String, cx: &mut Cx) {\n"
        "    let toast = StatusToast::new(message, cx, |this, _| {});\n"
        "}\n"
    )
    fixture = _fixture(tmp_path, source)
    sample = _sample(
        fixture,
        _span(fixture.source, "let toast = StatusToast::new(message, cx, |this, _| {"),
        subject_kind=SubjectKind.EXPRESSION_ORIGIN,
        sink_symbol="notifications::status_toast::StatusToast::new",
        anchor="let toast = StatusToast::new(message, cx, |this, _| {",
        features=frozenset({Feature.LOCAL_VARIABLE, Feature.TOAST}),
    )
    from zed_i18n_kit.rust_cst import parse_rust_cst
    from zed_i18n_kit.rust_symbols import build_source_symbol_table

    tree = parse_rust_cst(fixture.source)
    node = _canonical_origin(
        sample,
        tree,
        build_source_symbol_table(tree, fixture.path),
    )

    assert node is not None
    assert fixture.source[node.start_byte : node.end_byte] == b"message"


def test_prompt_origin_uses_secondary_cst_inside_allowlisted_wrapper(
    tmp_path: Path,
) -> None:
    source = (
        "use gpui::Window;\n"
        "use util::maybe;\n"
        "fn render(window: &mut Window, prompt: String, cx: &mut Cx) {\n"
        '    maybe!({ Some(window.prompt(PromptLevel::Info, &prompt, None, &["Restore"], cx)) });\n'
        "}\n"
    )
    fixture = _fixture(tmp_path, source)
    anchor = 'Some(window.prompt(PromptLevel::Info, &prompt, None, &["Restore"], cx))'
    sample = _sample(
        fixture,
        _span(fixture.source, anchor),
        subject_kind=SubjectKind.EXPRESSION_ORIGIN,
        sink_symbol="gpui::Window::prompt",
        anchor=anchor,
        features=frozenset({Feature.LOCAL_VARIABLE, Feature.PROMPT}),
    )
    from zed_i18n_kit.rust_cst import parse_rust_cst
    from zed_i18n_kit.rust_macros import parse_allowlisted_expression_macros
    from zed_i18n_kit.rust_symbols import build_source_symbol_table

    tree = parse_rust_cst(fixture.source)
    expanded = parse_allowlisted_expression_macros(tree)
    assert expanded is not None
    node = _canonical_origin(
        sample,
        expanded.tree,
        build_source_symbol_table(tree, fixture.path),
    )

    assert node is not None
    assert fixture.source[node.start_byte : node.end_byte] == b"&prompt"


def test_allowlisted_wrapper_secondary_cst_resolves_explicit_sink_alias(
    tmp_path: Path,
) -> None:
    source = (
        "use ui::Label as UiLabel;\n"
        "use util::maybe;\n"
        'fn render() { maybe!({ UiLabel::new("标签") }); }\n'
    )
    fixture = _fixture(tmp_path, source)
    anchor = 'UiLabel::new("标签")'
    sample = _sample(
        fixture,
        _span(fixture.source, anchor),
        subject_kind=SubjectKind.EXPRESSION_ORIGIN,
        sink_symbol="ui::Label::new",
        anchor=anchor,
        features=frozenset({Feature.LOCAL_VARIABLE}),
    )
    from zed_i18n_kit.rust_cst import parse_rust_cst
    from zed_i18n_kit.rust_macros import parse_allowlisted_expression_macros
    from zed_i18n_kit.rust_symbols import build_source_symbol_table

    tree = parse_rust_cst(fixture.source)
    expanded = parse_allowlisted_expression_macros(tree)
    assert expanded is not None
    node = _canonical_origin(
        sample,
        expanded.tree,
        build_source_symbol_table(tree, fixture.path),
    )

    assert node is not None
    assert fixture.source[node.start_byte : node.end_byte] == '"标签"'.encode()


@pytest.mark.parametrize(
    ("expression", "feature", "node_kind"),
    (
        ('if flag { "启用" } else { "停用" }', Feature.IF_EXPRESSION, "if_expression"),
        (
            'match flag { true => "启用", false => "停用" }',
            Feature.MATCH_EXPRESSION,
            "match_expression",
        ),
        ('format!("你好 {name}")', Feature.FORMAT_TEMPLATE, "macro_invocation"),
    ),
)
def test_expression_origin_accepts_rhs_if_match_and_macro(
    tmp_path: Path,
    expression: str,
    feature: Feature,
    node_kind: str,
) -> None:
    source = f"fn render(flag: bool, name: &str) {{ let text = {expression}; }}\n"
    fixture = _fixture(tmp_path, source)
    sample = _sample(
        fixture,
        _span(fixture.source, expression),
        subject_kind=SubjectKind.EXPRESSION_ORIGIN,
        features=frozenset({feature}),
        anchor=expression,
    )

    result = validate_corpus_cst(_corpus(sample), fixture.root)

    assert result[0].node_kind == node_kind


def test_scope_exclusion_accepts_exact_test_example_preview_and_match_arm_nodes(
    tmp_path: Path,
) -> None:
    cases = (
        (
            PurePosixPath("crates/demo/src/lib_tests.rs"),
            '#[cfg(test)]\nfn fixture() { Label::new("测试"); }\n',
            'Label::new("测试")',
            SourceScope.TEST,
        ),
        (
            PurePosixPath("crates/demo/examples/demo.rs"),
            'fn example() { Label::new("示例"); }\n',
            'Label::new("示例")',
            SourceScope.EXAMPLE,
        ),
        (
            PurePosixPath("crates/component_preview/src/lib.rs"),
            'fn preview() { Label::new("预览"); }\n',
            'Label::new("预览")',
            SourceScope.COMPONENT_PREVIEW,
        ),
    )
    samples: list[GoldenSample] = []
    for path, source_text, anchor, scope in cases:
        fixture = _fixture(tmp_path, source_text, path=path)
        samples.append(
            _sample(
                fixture,
                _span(fixture.source, anchor),
                subject_kind=SubjectKind.SCOPE_EXCLUSION,
                scope=scope,
                features=frozenset({Feature.TEST_SCOPE}),
                anchor=anchor,
            )
        )

    result = validate_corpus_cst(
        _corpus(*samples),
        tmp_path,
    )

    assert [item.node_kind for item in result] == [
        "call_expression",
        "call_expression",
        "call_expression",
    ]


def test_scope_exclusion_recognizes_component_trait_preview_only(
    tmp_path: Path,
) -> None:
    component_fixture = _fixture(
        tmp_path,
        'struct Demo;\nimpl Component for Demo {\n    fn preview() { Label::new("预览"); }\n}\n',
        path=PurePosixPath("crates/demo/src/component.rs"),
    )
    component_sample = _sample(
        component_fixture,
        _span(component_fixture.source, 'Label::new("预览")'),
        subject_kind=SubjectKind.SCOPE_EXCLUSION,
        scope=SourceScope.COMPONENT_PREVIEW,
        features=frozenset({Feature.PREVIEW_SCOPE}),
        anchor='Label::new("预览")',
    )

    result = validate_corpus_cst(_corpus(component_sample), tmp_path)

    assert result[0].node_kind == "call_expression"

    non_component_fixture = _fixture(
        tmp_path,
        'struct Demo;\nimpl Other for Demo {\n    fn preview() { Label::new("生产"); }\n}\n',
        path=PurePosixPath("crates/demo/src/other.rs"),
    )
    non_component_sample = _sample(
        non_component_fixture,
        _span(non_component_fixture.source, 'Label::new("生产")'),
        subject_kind=SubjectKind.SCOPE_EXCLUSION,
        scope=SourceScope.COMPONENT_PREVIEW,
        features=frozenset({Feature.PREVIEW_SCOPE}),
        anchor='Label::new("生产")',
    )

    with pytest.raises(CanonicalCstError, match="cannot identify"):
        validate_corpus_cst(_corpus(non_component_sample), tmp_path)


def test_scope_exclusion_accepts_an_exact_match_arm_but_rejects_non_exact_span(
    tmp_path: Path,
) -> None:
    source = '#[cfg(test)]\nfn fixture(flag: bool) { match flag { true => Label::new("是"), false => Label::new("否") } }\n'
    path = PurePosixPath("crates/demo/src/lib_tests.rs")
    fixture = _fixture(tmp_path / "match", source, path=path)
    source_bytes = source.encode()
    match_arm_anchor = 'true => Label::new("是"),'
    exact_sample = _sample(
        fixture,
        _span(source_bytes, match_arm_anchor),
        subject_kind=SubjectKind.SCOPE_EXCLUSION,
        scope=SourceScope.TEST,
        features=frozenset({Feature.TEST_SCOPE}),
        anchor=match_arm_anchor,
    )

    result = validate_corpus_cst(_corpus(exact_sample), fixture.root)

    assert result[0].node_kind == "match_arm"

    non_exact_sample = _sample(
        fixture,
        _span(source_bytes, 'Label::new("是"),'),
        subject_kind=SubjectKind.SCOPE_EXCLUSION,
        scope=SourceScope.TEST,
        features=frozenset({Feature.TEST_SCOPE}),
        anchor='Label::new("是"),',
    )
    with pytest.raises(CanonicalCstError, match="non-canonical"):
        validate_corpus_cst(_corpus(non_exact_sample), fixture.root)


def test_scope_exclusion_rejects_a_node_outside_declared_test_scope(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, 'fn render() { Label::new("生产"); }\n')
    sample = _sample(
        fixture,
        _span(fixture.source, 'Label::new("生产")'),
        subject_kind=SubjectKind.SCOPE_EXCLUSION,
        scope=SourceScope.TEST,
        features=frozenset({Feature.TEST_SCOPE}),
        anchor='Label::new("生产")',
    )

    with pytest.raises(CanonicalCstError, match="cannot identify"):
        validate_corpus_cst(_corpus(sample), fixture.root)


def test_cfg_test_attribute_does_not_leak_to_following_production_function(
    tmp_path: Path,
) -> None:
    source = (
        "use ui::Label;\n"
        "#[cfg(test)]\n"
        'fn fixture() { Label::new("测试"); }\n'
        'fn render() { Label::new("生产"); }\n'
    )
    fixture = _fixture(tmp_path, source)
    sample = _sample(
        fixture,
        _span(fixture.source, 'Label::new("生产")'),
        subject_kind=SubjectKind.SCOPE_EXCLUSION,
        scope=SourceScope.TEST,
        features=frozenset({Feature.TEST_SCOPE}),
        anchor='Label::new("生产")',
    )

    with pytest.raises(CanonicalCstError, match="cannot identify"):
        validate_corpus_cst(_corpus(sample), fixture.root)


def _fixture(
    root: Path,
    source: str | bytes,
    *,
    path: PurePosixPath = DEFAULT_SOURCE_PATH,
) -> _Fixture:
    source_bytes = source.encode() if isinstance(source, str) else source
    source_path = root / path
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(source_bytes)
    return _Fixture(root=root, path=path, source=source_bytes)


def _span(source: bytes, text: str) -> SourceSpan:
    encoded = text.encode()
    start = source.index(encoded)
    return SourceSpan(start, start + len(encoded))


def _sample(
    fixture: _Fixture,
    span: SourceSpan,
    *,
    subject_kind: SubjectKind,
    anchor: str,
    scope: SourceScope = SourceScope.PRODUCTION,
    sink_symbol: str | None = None,
    text_slot: str | None = None,
    features: frozenset[Feature] = frozenset(),
) -> GoldenSample:
    return GoldenSample(
        sample_id="zed-aaaaaaa-0001",
        path=fixture.path,
        source_span=span,
        anchor=anchor,
        scope=scope,
        subject_kind=subject_kind,
        sink_symbol=sink_symbol,
        text_slot=text_slot,
        sink_kind=SinkKind.NONE,
        features=features,
        ownership=Ownership.PRODUCT,
        expected_presence=ExpectedPresence.NOT_CANDIDATE
        if subject_kind is SubjectKind.SCOPE_EXCLUSION
        else ExpectedPresence.CANDIDATE,
        expected_disposition=Decision.EXCLUDED
        if subject_kind is SubjectKind.SCOPE_EXCLUSION
        else Decision.REVIEW_REQUIRED,
        review_state=ReviewState.SINGLE_REVIEW,
        rationale="CST fixture",
    )


def _corpus(*samples: GoldenSample) -> GoldenCorpus:
    return GoldenCorpus(
        manifest=CorpusManifest(
            schema_version=2,
            zed_commit="a" * 40,
            sample_file="samples.jsonl",
            sample_count=len(samples),
            sample_sha256="a" * 64,
            source_files_sha256={},
            minimum_counts=(),
        ),
        samples=samples,
    )
