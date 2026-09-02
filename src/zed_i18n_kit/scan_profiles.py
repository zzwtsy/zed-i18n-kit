from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath


class CallShape(StrEnum):
    METHOD = "method"
    FUNCTION = "function"


class SlotExtractionStrategy(StrEnum):
    CHILD_LITERAL = "child_literal"
    PROMPT = "prompt"
    FIRST_ARGUMENT = "first_argument"
    SECOND_ARGUMENT = "second_argument"


@dataclass(frozen=True, slots=True)
class SinkRule:
    rule_id: str
    call_shape: CallShape
    callee_suffix: str
    sink_symbol: str
    slot_extraction: SlotExtractionStrategy


@dataclass(frozen=True, slots=True)
class ScanProfile:
    rule_pack_version: str
    source_paths: tuple[PurePosixPath, ...]
    sink_rules: tuple[SinkRule, ...]


PROTOTYPE_SINK_RULES = (
    SinkRule(
        "gpui-parent-child-literal",
        CallShape.METHOD,
        "child",
        "gpui::ParentElement::child",
        SlotExtractionStrategy.CHILD_LITERAL,
    ),
    SinkRule(
        "gpui-window-prompt",
        CallShape.METHOD,
        "prompt",
        "gpui::Window::prompt",
        SlotExtractionStrategy.PROMPT,
    ),
    SinkRule(
        "ui-label-new",
        CallShape.FUNCTION,
        "Label::new",
        "ui::Label::new",
        SlotExtractionStrategy.FIRST_ARGUMENT,
    ),
    SinkRule(
        "project-language-server-prompt",
        CallShape.FUNCTION,
        "LanguageServerPromptRequest::new",
        "project::LanguageServerPromptRequest::new",
        SlotExtractionStrategy.SECOND_ARGUMENT,
    ),
)

PROTOTYPE_SOURCE_PATHS = tuple(
    PurePosixPath(path)
    for path in (
        "crates/agent_ui/src/diagnostics.rs",
        "crates/agent_ui/src/inline_assistant.rs",
        "crates/anthropic/src/anthropic.rs",
        "crates/editor/src/editor.rs",
        "crates/git_ui/src/git_ui.rs",
        "crates/project/src/lsp_store.rs",
        "crates/project_panel/src/project_panel.rs",
        "crates/workspace/src/invalid_item_view.rs",
        "crates/workspace/src/notifications.rs",
        "crates/workspace/src/pane.rs",
    )
)

PROTOTYPE_SCAN_PROFILE = ScanProfile(
    rule_pack_version="gpui-prototype-v1",
    source_paths=PROTOTYPE_SOURCE_PATHS,
    sink_rules=PROTOTYPE_SINK_RULES,
)
