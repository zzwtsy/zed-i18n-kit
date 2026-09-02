from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from .discovery import DEFAULT_DISCOVERY_POLICY, DiscoveryPolicy


class CallShape(StrEnum):
    METHOD = "method"
    FUNCTION = "function"


class SlotExtractionStrategy(StrEnum):
    CHILD_LITERAL = "child_literal"
    PROMPT = "prompt"
    FIRST_ARGUMENT = "first_argument"
    SECOND_ARGUMENT = "second_argument"
    BUTTON = "button"


class SymbolResolutionMode(StrEnum):
    PROTOTYPE_SUFFIX = "prototype_suffix"
    IMPORT_AWARE = "import_aware"


class ReceiverRequirement(StrEnum):
    NONE = "none"
    GPUI_ELEMENT = "gpui_element"
    GPUI_WINDOW = "gpui_window"


@dataclass(frozen=True, slots=True)
class SinkRule:
    rule_id: str
    call_shape: CallShape
    callee_suffix: str
    sink_symbol: str
    slot_extraction: SlotExtractionStrategy
    target_symbol: str | None = None
    receiver_requirement: ReceiverRequirement = ReceiverRequirement.NONE
    allow_unresolved_receiver: bool = False


@dataclass(frozen=True, slots=True)
class ScanProfile:
    rule_pack_version: str
    source_paths: tuple[PurePosixPath, ...]
    sink_rules: tuple[SinkRule, ...]
    discovery_policy: DiscoveryPolicy | None = None
    symbol_resolution: SymbolResolutionMode = SymbolResolutionMode.PROTOTYPE_SUFFIX


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

WORKSPACE_BUILTIN_RULES = (
    SinkRule(
        "gpui-parent-child-literal-v1",
        CallShape.METHOD,
        "child",
        "gpui::ParentElement::child",
        SlotExtractionStrategy.CHILD_LITERAL,
        receiver_requirement=ReceiverRequirement.GPUI_ELEMENT,
    ),
    SinkRule(
        "gpui-window-prompt-v1",
        CallShape.METHOD,
        "prompt",
        "gpui::Window::prompt",
        SlotExtractionStrategy.PROMPT,
        receiver_requirement=ReceiverRequirement.GPUI_WINDOW,
    ),
    SinkRule(
        "gpui-interactive-aria-label-v1",
        CallShape.METHOD,
        "aria_label",
        "gpui::InteractiveElement::aria_label",
        SlotExtractionStrategy.FIRST_ARGUMENT,
        receiver_requirement=ReceiverRequirement.GPUI_ELEMENT,
        allow_unresolved_receiver=True,
    ),
    SinkRule(
        "ui-label-new-v1",
        CallShape.FUNCTION,
        "Label::new",
        "ui::Label::new",
        SlotExtractionStrategy.FIRST_ARGUMENT,
        target_symbol="ui::Label::new",
    ),
    SinkRule(
        "ui-button-new-v1",
        CallShape.FUNCTION,
        "Button::new",
        "ui::Button::new",
        SlotExtractionStrategy.BUTTON,
        target_symbol="ui::Button::new",
    ),
    SinkRule(
        "ui-tooltip-text-v1",
        CallShape.FUNCTION,
        "Tooltip::text",
        "ui::Tooltip::text",
        SlotExtractionStrategy.FIRST_ARGUMENT,
        target_symbol="ui::Tooltip::text",
    ),
    SinkRule(
        "workspace-toast-new-v1",
        CallShape.FUNCTION,
        "Toast::new",
        "workspace::Toast::new",
        SlotExtractionStrategy.SECOND_ARGUMENT,
        target_symbol="workspace::Toast::new",
    ),
    SinkRule(
        "notifications-status-toast-new-v1",
        CallShape.FUNCTION,
        "StatusToast::new",
        "notifications::status_toast::StatusToast::new",
        SlotExtractionStrategy.FIRST_ARGUMENT,
        target_symbol="notifications::status_toast::StatusToast::new",
    ),
    SinkRule(
        "project-language-server-prompt-v1",
        CallShape.FUNCTION,
        "LanguageServerPromptRequest::new",
        "project::LanguageServerPromptRequest::new",
        SlotExtractionStrategy.SECOND_ARGUMENT,
        target_symbol="project::LanguageServerPromptRequest::new",
    ),
)

WORKSPACE_SCAN_PROFILE = ScanProfile(
    rule_pack_version="zed-builtin-v1",
    source_paths=(),
    sink_rules=WORKSPACE_BUILTIN_RULES,
    discovery_policy=DEFAULT_DISCOVERY_POLICY,
    symbol_resolution=SymbolResolutionMode.IMPORT_AWARE,
)
