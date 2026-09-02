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
        == 1
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
use ui::{Button, Label, Tooltip, h_flex};
use workspace::Toast as WorkspaceToast;
use impostor::Label as FakeLabel;

fn render(window: &mut Window, cx: &mut App) {
    Label::new("Label");
    Button::new("button-id", "Button");
    Tooltip::text(dynamic_tooltip);
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

    assert len(result.occurrences) == 13
    assert by_primary[b'"Label"'].sink_symbol == "ui::Label::new"
    assert by_primary[b'"button-id"'].disposition is Decision.EXCLUDED
    assert by_primary[b'"Button"'].text_slot == "arg[1]"
    assert by_primary[b"dynamic_tooltip"].disposition is Decision.REVIEW_REQUIRED
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
