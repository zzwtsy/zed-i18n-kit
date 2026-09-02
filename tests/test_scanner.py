import subprocess
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from zed_i18n_kit.cst_calibration import (
    CST_CALIBRATION_CASES,
    CstCalibrationMode,
)
from zed_i18n_kit.golden import Decision, SourceSpan
from zed_i18n_kit.scan_profiles import PROTOTYPE_SCAN_PROFILE
from zed_i18n_kit.scan_result import (
    ScanResultError,
    serialize_scan_result,
    validate_scan_snapshot,
)
from zed_i18n_kit.scanner import scan_sources

SOURCE_PATH = PurePosixPath("crates/demo/src/lib.rs")


def test_prototype_scanner_extracts_slots_provenance_and_scope(
    tmp_path: Path,
) -> None:
    zed_root = tmp_path / "zed"
    source_path = zed_root / SOURCE_PATH
    source_path.parent.mkdir(parents=True)
    source = b"""
fn production() {
    let detail = format!("Problem: {}", err);
    window.prompt(Level::Warning, "Question?", Some(&detail), &["OK", "Cancel"], cx);
    let label = format!("{} - {}", author, subject);
    Label::new(label);
    NotLabel::new("wrong sink");
    h.child("Visible");
    h.child("...");
    LanguageServerPromptRequest::new(level, params.message, vec![], name, tx);
}

#[cfg(test)]
fn fixture() { Label::new("test-only"); }
"""
    source_path.write_bytes(source)
    _initialize_git_repository(zed_root)

    profile = replace(PROTOTYPE_SCAN_PROFILE, source_paths=(SOURCE_PATH,))
    first = scan_sources(zed_root, profile=profile)
    second = scan_sources(zed_root, profile=profile)

    assert serialize_scan_result(first) == serialize_scan_result(second)
    assert len(first.occurrences) == 8
    assert all(
        probe.status.value == "passed" for probe in first.metadata.capability_probes
    )
    assert all(
        source[occurrence.primary_span.start_byte : occurrence.primary_span.end_byte]
        != b'"test-only"'
        for occurrence in first.occurrences
    )

    by_source = {
        source[
            occurrence.primary_span.start_byte : occurrence.primary_span.end_byte
        ]: occurrence
        for occurrence in first.occurrences
    }
    detail = by_source[b"&detail"]
    assert detail.text_slot == "arg[2].Some"
    assert detail.syntax_kind == "reference_expression"
    assert detail.disposition is Decision.REVIEW_REQUIRED
    provenance_text = {
        source[item.source_span.start_byte : item.source_span.end_byte]
        for item in detail.provenance
    }
    assert b'format!("Problem: {}", err)' in provenance_text
    assert b'"Problem: {}"' in provenance_text

    assert by_source[b'"Visible"'].disposition is Decision.CONFIRMED
    assert by_source[b'"..."'].disposition is Decision.EXCLUDED
    assert b'"wrong sink"' not in by_source
    assert by_source[b"params.message"].text_slot == "arg[1]"
    validate_scan_snapshot(first, zed_root)


def test_cst_calibration_contract_has_exactly_sixteen_cases() -> None:
    assert len(CST_CALIBRATION_CASES) == 16
    assert {item.sample_id[-4:] for item in CST_CALIBRATION_CASES} == {
        f"{number:04d}" for number in range(251, 267)
    }
    assert (
        sum(
            item.mode is CstCalibrationMode.SMALLEST_CONTAINING
            for item in CST_CALIBRATION_CASES
        )
        == 0
    )


def test_default_scanner_uses_workspace_discovery(tmp_path: Path) -> None:
    zed_root = tmp_path / "zed"
    second_path = PurePosixPath("crates/second/src/view.rs")
    excluded_path = PurePosixPath("crates/demo/src/tests/fixture.rs")
    sources = {
        SOURCE_PATH: 'use ui::Label; fn first() { Label::new("first"); }\n',
        second_path: 'use ui::Label; fn second() { Label::new("second"); }\n',
        excluded_path: ('use ui::Label; fn fixture() { Label::new("excluded"); }\n'),
    }
    for path, source in sources.items():
        source_path = zed_root / path
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(source, encoding="utf-8")
    _initialize_git_repository(zed_root, source_paths=tuple(sources))

    result = scan_sources(zed_root)

    assert result.metadata.scan_scope == (SOURCE_PATH, second_path)
    assert len(result.occurrences) == 2


def test_workspace_scanner_uses_typed_symbols_slots_and_receivers(
    tmp_path: Path,
) -> None:
    zed_root = tmp_path / "zed"
    source_path = zed_root / SOURCE_PATH
    source_path.parent.mkdir(parents=True)
    source = b"""
use gpui::{Window, div};
use notifications::status_toast::StatusToast;
use project::LanguageServerPromptRequest;
use project_panel::ValidationState;
use settings_ui::SettingsWindow;
use ui::{Button, Label, Tooltip, h_flex};
use workspace::Toast as WorkspaceToast;
use impostor::Label as FakeLabel;

fn render(
    window: &mut Window,
    settings_window: &mut SettingsWindow,
    task: Task,
    cx: &mut App,
) {
    Label::new("Label");
    Button::new("button-id", "Button");
    Tooltip::text(dynamic_tooltip);
    Tooltip::simple("Simple", cx);
    Tooltip::for_action_in("Action", &action, &focus, cx);
    Tooltip::with_meta("Title", None, "Description", cx);
    h_flex().child("Child");
    h_flex().child(dynamic_element);
    business_object.child("Not UI");
    div().aria_label("Accessible");
    unknown_widget.aria_label(dynamic_label);
    window.prompt(
        Level::Info,
        "Question?",
        Some(&detail),
        &["Yes"],
        cx,
    );
    WorkspaceToast::new(id, "Saved");
    StatusToast::new("Finished", cx, |this, _| this);
    LanguageServerPromptRequest::new(level, protocol_message);
    settings_window.push_dynamic_sub_page("Settings Page", body);
    ValidationState::Error(format!("Invalid: {error}"));
    task.detach_and_prompt_err("Failed", window, cx, move |error| {
        Some(format!("Failure detail: {error}"))
    });
    FakeLabel::new("Wrong symbol");
}
"""
    source_path.write_bytes(source)
    _initialize_git_repository(zed_root)

    result = scan_sources(zed_root)
    by_primary = {
        source[item.primary_span.start_byte : item.primary_span.end_byte]: item
        for item in result.occurrences
    }

    assert len(result.occurrences) == 21
    assert by_primary[b'"Label"'].sink_symbol == "ui::Label::new"
    assert by_primary[b'"button-id"'].disposition is Decision.EXCLUDED
    assert by_primary[b'"Button"'].text_slot == "arg[1]"
    assert by_primary[b"dynamic_tooltip"].disposition is Decision.REVIEW_REQUIRED
    assert by_primary[b'"Simple"'].sink_symbol == "ui::Tooltip::simple"
    assert by_primary[b'"Action"'].sink_symbol == "ui::Tooltip::for_action_in"
    assert by_primary[b'"Title"'].sink_symbol == "ui::Tooltip::with_meta"
    assert by_primary[b'"Title"'].text_slot == "arg[0]"
    assert by_primary[b'"Description"'].sink_symbol == "ui::Tooltip::with_meta"
    assert by_primary[b'"Description"'].text_slot == "arg[2]"
    assert by_primary[b'"Child"'].sink_symbol == "gpui::ParentElement::child"
    assert b"dynamic_element" not in by_primary
    assert b'"Not UI"' not in by_primary
    assert by_primary[b'"Accessible"'].disposition is Decision.CONFIRMED
    assert by_primary[b"dynamic_label"].disposition is Decision.REVIEW_REQUIRED
    assert by_primary[b'"Question?"'].text_slot == "arg[1]"
    assert by_primary[b"&detail"].text_slot == "arg[2].Some"
    assert by_primary[b'"Yes"'].text_slot == "arg[3][0]"
    assert by_primary[b'"Saved"'].sink_symbol == "workspace::Toast::new"
    assert by_primary[b'"Finished"'].sink_symbol == (
        "notifications::status_toast::StatusToast::new"
    )
    assert by_primary[b"protocol_message"].sink_symbol == (
        "project::LanguageServerPromptRequest::new"
    )
    assert by_primary[b'"Settings Page"'].sink_symbol == (
        "settings_ui::SettingsWindow::push_dynamic_sub_page"
    )
    assert by_primary[b'format!("Invalid: {error}")'].sink_symbol == (
        "project_panel::ValidationState::Error"
    )
    assert by_primary[b'format!("Failure detail: {error}")'].sink_symbol == (
        "gpui::Task::detach_and_prompt_err"
    )
    assert by_primary[b'format!("Failure detail: {error}")'].text_slot == (
        "arg[3].Some"
    )
    assert by_primary[b'"Failed"'].text_slot == "arg[0]"
    assert b'"Wrong symbol"' not in by_primary
    assert any(
        probe.probe_id == "cfg-aware-import-resolution"
        and probe.status.value == "passed"
        for probe in result.metadata.capability_probes
    )
    assert any(
        probe.probe_id == "typed-builtin-rules" and probe.status.value == "passed"
        for probe in result.metadata.capability_probes
    )
    assert all(item.evidence[0].rule_id.endswith("-v1") for item in result.occurrences)


def test_workspace_scanner_resolves_tooltip_simple_and_rejects_same_name_impostor(
    tmp_path: Path,
) -> None:
    zed_root = tmp_path / "zed"
    source_path = zed_root / SOURCE_PATH
    source_path.parent.mkdir(parents=True)
    source = b"""
use ui::Tooltip;
use impostor::Tooltip as FakeTooltip;

fn render(dynamic_label: SharedString) {
    Tooltip::simple("Simple tooltip", cx);
    Tooltip::simple(dynamic_label, cx);
    FakeTooltip::simple("Not a UI tooltip", cx);
}
"""
    source_path.write_bytes(source)
    _initialize_git_repository(zed_root)

    result = scan_sources(zed_root)
    by_primary = {
        source[item.primary_span.start_byte : item.primary_span.end_byte]: item
        for item in result.occurrences
    }

    assert set(by_primary) == {b'"Simple tooltip"', b"dynamic_label"}
    direct = by_primary[b'"Simple tooltip"']
    assert direct.sink_symbol == "ui::Tooltip::simple"
    assert direct.text_slot == "arg[0]"
    assert direct.disposition is Decision.CONFIRMED
    assert direct.evidence[0].rule_id == "ui-tooltip-simple-v1"
    assert "resolved function 'Tooltip::simple' to 'ui::Tooltip::simple'" in (
        direct.evidence[0].reason
    )
    assert "slot arg[0]" in direct.evidence[0].reason
    assert by_primary[b"dynamic_label"].disposition is Decision.REVIEW_REQUIRED


def test_workspace_scanner_excludes_long_option_only_in_documentation_aside_labels(
    tmp_path: Path,
) -> None:
    zed_root = tmp_path / "zed"
    source_path = zed_root / SOURCE_PATH
    source_path.parent.mkdir(parents=True)
    source = b"""
use ui::Label;

fn render() {
    ContextMenuEntry::new("skip-hooks")
        .documentation_aside(DocumentationSide::Left, |_| {
            Label::new("git commit --no-verify").into_any_element()
        });
    ContextMenuEntry::new("short-option")
        .documentation_aside(DocumentationSide::Left, |_| {
            Label::new("--skip-hooks").into_any_element()
        });
    ContextMenuEntry::new("joined-option")
        .documentation_aside(DocumentationSide::Left, |_| {
            Label::new("git--no-verify").into_any_element()
        });
    Label::new("outside --no-verify");
}
"""
    source_path.write_bytes(source)
    _initialize_git_repository(zed_root)

    result = scan_sources(zed_root)
    by_primary = {
        source[item.primary_span.start_byte : item.primary_span.end_byte]: item
        for item in result.occurrences
    }

    assert len(by_primary) == 4
    assert by_primary[b'"git commit --no-verify"'].disposition is Decision.EXCLUDED
    assert by_primary[b'"--skip-hooks"'].disposition is Decision.EXCLUDED
    assert by_primary[b'"git--no-verify"'].disposition is Decision.CONFIRMED
    assert by_primary[b'"outside --no-verify"'].disposition is Decision.CONFIRMED
    for primary in (b'"git commit --no-verify"', b'"--skip-hooks"'):
        reason = by_primary[primary].evidence[0].reason
        assert (
            "documentation_aside callback contains a standalone long-option token"
            in (reason)
        )
    assert (
        "standalone long-option token"
        not in by_primary[b'"git--no-verify"'].evidence[0].reason
    )
    assert (
        "standalone long-option token"
        not in by_primary[b'"outside --no-verify"'].evidence[0].reason
    )


def test_workspace_scanner_downgrades_wildcard_symbols_and_traces_mutations(
    tmp_path: Path,
) -> None:
    zed_root = tmp_path / "zed"
    source_path = zed_root / SOURCE_PATH
    source_path.parent.mkdir(parents=True)
    source = b"""
use ui::prelude::*;

fn render(flag: bool) {
    let mut message = if flag { "Enabled" } else { "Disabled" };
    message.push_str("!");
    message = format!("State: {message}");
    Label::new(message);
    Button::new("candidate-id", "Candidate label");
}
"""
    source_path.write_bytes(source)
    _initialize_git_repository(zed_root)

    result = scan_sources(zed_root)

    assert len(result.occurrences) == 2
    occurrence = next(
        item for item in result.occurrences if item.sink_symbol == "ui::Label::new"
    )
    assert occurrence.disposition is Decision.REVIEW_REQUIRED
    assert "wildcard import candidate" in occurrence.evidence[0].reason
    provenance_text = {
        source[item.source_span.start_byte : item.source_span.end_byte]
        for item in occurrence.provenance
    }
    assert b'if flag { "Enabled" } else { "Disabled" }' in provenance_text
    assert b'"Enabled"' in provenance_text
    assert b'"Disabled"' in provenance_text
    assert b'"!"' in provenance_text
    assert b'format!("State: {message}")' in provenance_text
    assert all(
        source[item.primary_span.start_byte : item.primary_span.end_byte]
        != b'"candidate-id"'
        for item in result.occurrences
    )


def test_workspace_scanner_distinguishes_element_and_text_child_bindings(
    tmp_path: Path,
) -> None:
    zed_root = tmp_path / "zed"
    source_path = zed_root / SOURCE_PATH
    source_path.parent.mkdir(parents=True)
    source = b"""
use ui::{Label, div, h_flex};

fn render(flag: bool, cx: &App) {
    let text = "Visible child";
    let formatted = format!("Dynamic child: {flag}");
    let text_branch = if flag { "First child" } else { "Second child" };
    let element = h_flex().child("Nested element");
    let conditional_element = if flag {
        div().child("First element")
    } else {
        Label::new("Second element")
    };

    div().child(text);
    div().child(formatted);
    div().child(text_branch);
    div().child(element);
    div().child(conditional_element);
    Label::new("Code value").inline_code(cx);
    Label::new("git operation failed");
    Label::new("Visible label");
}
"""
    source_path.write_bytes(source)
    _initialize_git_repository(zed_root)

    result = scan_sources(zed_root)
    by_text = {
        source[item.primary_span.start_byte : item.primary_span.end_byte]: item
        for item in result.occurrences
    }

    assert b"text" in by_text
    assert b"formatted" in by_text
    assert b"text_branch" in by_text
    assert b"element" not in by_text
    assert b"conditional_element" not in by_text
    assert b'"Code value"' not in by_text
    assert by_text[b'"git operation failed"'].disposition is Decision.CONFIRMED
    assert by_text[b'"Visible label"'].disposition is Decision.CONFIRMED
    assert by_text[b"text"].disposition is Decision.REVIEW_REQUIRED
    assert by_text[b"formatted"].disposition is Decision.REVIEW_REQUIRED


def test_workspace_scanner_excludes_element_returning_match_child_but_keeps_text_match(
    tmp_path: Path,
) -> None:
    zed_root = tmp_path / "zed"
    source_path = zed_root / SOURCE_PATH
    source_path.parent.mkdir(parents=True)
    source = b"""
use ui::div;

fn render_title_view() -> AnyElement { div() }
fn render_text() -> String { String::from("helper text") }
struct Label;
fn render_business_label() -> Label { Label }

fn render(flag: bool) {
    div().child(match flag {
        true => "Visible branch",
        false => render_title_view(),
    });
    div().child(match flag {
        true => "First text",
        false => render_text(),
    });
    div().child(match flag {
        true => "Business text",
        false => render_business_label(),
    });
}
"""
    source_path.write_bytes(source)
    _initialize_git_repository(zed_root)

    result = scan_sources(zed_root)
    children = [
        item
        for item in result.occurrences
        if item.sink_symbol == "gpui::ParentElement::child"
    ]

    assert len(children) == 2
    texts = [
        source[item.primary_span.start_byte : item.primary_span.end_byte]
        for item in children
    ]
    assert any(b"render_text" in text for text in texts)
    assert any(b"render_business_label" in text for text in texts)
    assert all(b"render_title_view" not in text for text in texts)
    assert all(item.disposition is Decision.REVIEW_REQUIRED for item in children)
    assert all("slot arg[0]" in item.evidence[0].reason for item in children)


def test_workspace_scanner_excludes_pure_format_control_literals_with_explanatory_evidence(
    tmp_path: Path,
) -> None:
    zed_root = tmp_path / "zed"
    source_path = zed_root / SOURCE_PATH
    source_path.parent.mkdir(parents=True)
    source = rb"""
use ui::{Label, div};
use ui::prelude::*;

fn render() {
    div().child("\n");
    div().child("\\n");
    div().child(r"\n");
    div().child(r#"\n"#);
    h_flex().child("\t");
    div().child("\\n remains text");
    div().child(r#"\n remains text"#);
    Label::new("\\n");
}
"""
    source_path.write_bytes(source)
    _initialize_git_repository(zed_root)

    result = scan_sources(zed_root)
    by_primary = {
        (
            source[item.primary_span.start_byte : item.primary_span.end_byte],
            item.sink_symbol,
        ): item
        for item in result.occurrences
    }

    controls = [
        by_primary[b'"\\n"', "gpui::ParentElement::child"],
        by_primary[b'"\\\\n"', "gpui::ParentElement::child"],
        by_primary[b'r"\\n"', "gpui::ParentElement::child"],
        by_primary[b'r#"\\n"#', "gpui::ParentElement::child"],
        by_primary[b'"\\t"', "gpui::ParentElement::child"],
        by_primary[b'"\\\\n"', "ui::Label::new"],
    ]
    assert all(item.disposition is Decision.EXCLUDED for item in controls)
    assert all(
        "literal contains only formatting control escapes" in item.evidence[0].reason
        for item in controls
    )
    text = by_primary[b'"\\\\n remains text"', "gpui::ParentElement::child"]
    assert text.disposition is Decision.CONFIRMED
    hash_text = by_primary[b'r#"\\n remains text"#', "gpui::ParentElement::child"]
    assert hash_text.disposition is Decision.CONFIRMED


def test_detach_and_prompt_err_extracts_messages_and_some_return_values(
    tmp_path: Path,
) -> None:
    zed_root = tmp_path / "zed"
    source_path = zed_root / SOURCE_PATH
    source_path.parent.mkdir(parents=True)
    source = b"""
fn render(task: Task, window: &Window, cx: &App, flag: bool) {
    task.detach_and_prompt_err("Direct", window, cx, |error, _, _| {
        Some(format!("Direct detail: {error}"))
    });
    task.detach_and_prompt_err("Block", window, cx, |error, _, _| {
        if flag {
            Some(format!("If detail: {error}"))
        } else {
            None
        }
    });
    task.detach_and_prompt_err("Match", window, cx, |error, _, _| match error {
        _ => Some(format!("Match detail: {error}")),
    });
    task.detach_and_prompt_err("None", window, cx, |_, _, _| None);
}
"""
    source_path.write_bytes(source)
    _initialize_git_repository(zed_root)

    result = scan_sources(zed_root)
    by_text = {
        source[item.primary_span.start_byte : item.primary_span.end_byte]: item
        for item in result.occurrences
        if item.sink_symbol == "gpui::Task::detach_and_prompt_err"
    }

    assert set(by_text) == {
        b'"Direct"',
        b'"Block"',
        b'"Match"',
        b'"None"',
        b'format!("Direct detail: {error}")',
        b'format!("If detail: {error}")',
        b'format!("Match detail: {error}")',
    }
    assert {
        primary for primary, item in by_text.items() if item.text_slot == "arg[0]"
    } == {b'"Direct"', b'"Block"', b'"Match"', b'"None"'}
    assert {
        primary for primary, item in by_text.items() if item.text_slot == "arg[3].Some"
    } == {
        b'format!("Direct detail: {error}")',
        b'format!("If detail: {error}")',
        b'format!("Match detail: {error}")',
    }
    assert all(
        item.disposition is Decision.CONFIRMED
        for item in by_text.values()
        if item.text_slot == "arg[0]"
    )
    assert all(
        item.syntax_kind == "macro_invocation"
        for item in by_text.values()
        if item.text_slot == "arg[3].Some"
    )
    assert all(
        b"Some("
        not in source[item.primary_span.start_byte : item.primary_span.end_byte]
        for item in by_text.values()
    )
    assert all(
        any(
            source[provenance.source_span.start_byte : provenance.source_span.end_byte]
            == primary
            for provenance in item.provenance
        )
        for primary, item in by_text.items()
    )


def test_snapshot_rejects_out_of_bounds_primary_span(tmp_path: Path) -> None:
    zed_root = tmp_path / "zed"
    source_path = zed_root / SOURCE_PATH
    source_path.parent.mkdir(parents=True)
    source_path.write_text('fn demo() { Label::new("text"); }\n', encoding="utf-8")
    _initialize_git_repository(zed_root)
    profile = replace(PROTOTYPE_SCAN_PROFILE, source_paths=(SOURCE_PATH,))
    result = scan_sources(zed_root, profile=profile)
    occurrence = result.occurrences[0]
    invalid = replace(
        occurrence,
        primary_span=SourceSpan(
            occurrence.primary_span.start_byte,
            len(source_path.read_bytes()) + 1,
        ),
    )
    invalid_result = replace(result, occurrences=(invalid,))

    with pytest.raises(ScanResultError, match="past source byte length"):
        validate_scan_snapshot(invalid_result, zed_root)


def test_scanner_skips_calls_with_parse_error_descendants(tmp_path: Path) -> None:
    zed_root = tmp_path / "zed"
    source_path = zed_root / SOURCE_PATH
    source_path.parent.mkdir(parents=True)
    source = b"""fn demo() {
    Label::new("invalid" + );
    Label::new("valid");
}
"""
    source_path.write_bytes(source)
    _initialize_git_repository(zed_root)
    profile = replace(PROTOTYPE_SCAN_PROFILE, source_paths=(SOURCE_PATH,))

    result = scan_sources(zed_root, profile=profile)

    assert len(result.occurrences) == 1
    occurrence = result.occurrences[0]
    assert (
        source[occurrence.primary_span.start_byte : occurrence.primary_span.end_byte]
        == b'"valid"'
    )
    assert any(
        probe.probe_id == "prototype-error-free-parse"
        and probe.status.value == "failed"
        for probe in result.metadata.capability_probes
    )


def test_workspace_scanner_emits_review_candidates_for_structured_ui_errors(
    tmp_path: Path,
) -> None:
    zed_root = tmp_path / "zed"
    source_path = zed_root / SOURCE_PATH
    source_path.parent.mkdir(parents=True)
    source = b"""
fn validate(name: &str) -> Result<(), SharedString> {
    Err(format!("Invalid value: {name}"))
}

fn notify(cx: &mut Cx, path: &Path) {
    cx.emit(project::Event::Toast {
        notification_id: "created".into(),
        message: format!("Created {path:?}"),
        link: None,
    });
}
"""
    source_path.write_bytes(source)
    _initialize_git_repository(zed_root)

    result = scan_sources(zed_root)
    by_rule = {
        occurrence.evidence[0].rule_id: occurrence for occurrence in result.occurrences
    }

    assert set(by_rule) == {
        "project-event-toast-message-v1",
        "shared-string-result-error-v1",
    }
    assert all(
        occurrence.disposition is Decision.REVIEW_REQUIRED
        for occurrence in by_rule.values()
    )
    assert any(
        probe.probe_id == "structured-ui-origin-boundaries"
        and probe.status.value == "passed"
        for probe in result.metadata.capability_probes
    )


def _initialize_git_repository(
    repository: Path, *, source_paths: tuple[PurePosixPath, ...] = (SOURCE_PATH,)
) -> None:
    commands = (
        ("init",),
        ("add", *(path.as_posix() for path in source_paths)),
        (
            "-c",
            "user.name=Scanner Test",
            "-c",
            "user.email=scanner@example.invalid",
            "commit",
            "-m",
            "fixture",
        ),
    )
    for arguments in commands:
        subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
