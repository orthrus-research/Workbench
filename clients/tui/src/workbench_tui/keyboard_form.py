"""Arrow-key form navigation with an explicit edit step for Textual screens."""

from __future__ import annotations

from typing import Any, ClassVar

from textual import events, work
from textual.app import ComposeResult
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Checkbox, Footer, Header, Input, OptionList, Select, Static
from textual.widgets.option_list import Option


class ChoicePicker(ModalScreen[Any | None]):
    """A short keyboard list for one form choice."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, label: str, options: list[tuple[str, Any]], current: Any) -> None:
        super().__init__()
        self.label = label
        self.sub_title = label
        self.options = options
        self.current = current

    def compose(self) -> ComposeResult:
        yield Header(icon="W")
        yield Static(self.label, classes="screen-heading")
        yield Static("↑/↓ Choose  ·  Enter Confirm  ·  Esc Cancel", classes="keyboard-hint")
        yield OptionList(
            *(Option(label, id=f"choice-{index}") for index, (label, _) in enumerate(self.options)),
            id="keyboard-choice-list",
        )
        yield Footer()

    def on_mount(self) -> None:
        choices = self.query_one("#keyboard-choice-list", OptionList)
        choices.focus()
        selected = next((index for index, (_, value) in enumerate(self.options)
                         if value == self.current), 0)
        choices.highlighted = selected

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "keyboard-choice-list" and event.option_id:
            index = int(event.option_id.removeprefix("choice-"))
            self.dismiss(self.options[index][1])

    def action_cancel(self) -> None:
        self.dismiss(None)


class KeyboardFormScreen(Screen[None]):
    """Keep form focus on navigation until Enter opens a field for editing."""

    KEYBOARD_FIELDS: ClassVar[tuple[str, ...]] = ()
    KEYBOARD_CANCEL: ClassVar[str | None] = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._keyboard_index = 0
        self._keyboard_editing: str | None = None
        self._keyboard_original = ""

    def start_keyboard_navigation(self) -> None:
        def ready() -> None:
            self.app.set_focus(None)
            self._keyboard_highlight(0)

        self.call_after_refresh(ready)

    def on_screen_resume(self, event: events.ScreenResume) -> None:
        self.call_after_refresh(self._keyboard_return)

    def _keyboard_items(self) -> list[Any]:
        result = []
        for name in self.KEYBOARD_FIELDS:
            match = self.query(f"#{name}")
            if match:
                widget = match.first()
                if not widget.disabled and widget.display:
                    result.append(widget)
        return result

    def _keyboard_highlight(self, index: int) -> None:
        for name in self.KEYBOARD_FIELDS:
            for widget in self.query(f"#{name}"):
                widget.remove_class("keyboard-selected")
        items = self._keyboard_items()
        if not items:
            return
        self._keyboard_index = index % len(items)
        current = items[self._keyboard_index]
        current.add_class("keyboard-selected")
        current.scroll_visible()

    def _keyboard_move(self, offset: int) -> None:
        items = self._keyboard_items()
        if not items:
            return
        highlighted = next((index for index, widget in enumerate(items)
                            if widget.has_class("keyboard-selected")), self._keyboard_index)
        self._keyboard_highlight(highlighted + offset)

    def _keyboard_return(self) -> None:
        self._keyboard_editing = None
        for widget in self.query(".keyboard-editing"):
            widget.remove_class("keyboard-editing")
        self.app.set_focus(None)
        items = self._keyboard_items()
        if items and not any(widget.has_class("keyboard-selected") for widget in items):
            self._keyboard_highlight(min(self._keyboard_index, len(items) - 1))

    def on_key(self, event: events.Key) -> None:
        key = event.key
        focused = self.app.focused
        if isinstance(focused, OptionList):
            # A focused list owns its arrow and Enter keys; form navigation
            # resumes when focus returns to a field or button.
            if key == "escape" and self.KEYBOARD_CANCEL is not None:
                cancel = self.query_one(f"#{self.KEYBOARD_CANCEL}", Button)
                if not cancel.disabled:
                    cancel.press()
                event.stop()
            return
        if (self._keyboard_editing is None and isinstance(focused, Input)
                and focused.id in self.KEYBOARD_FIELDS):
            self._keyboard_editing = focused.id
            self._keyboard_original = focused.value
            focused.add_class("keyboard-editing")
        if self._keyboard_editing is not None:
            if key == "escape":
                active = self.query_one(f"#{self._keyboard_editing}", Input)
                active.value = self._keyboard_original
                self._keyboard_return()
                event.stop()
            return
        if key in {"up", "k"}:
            self._keyboard_move(-1)
            event.stop()
        elif key in {"down", "j"}:
            self._keyboard_move(1)
            event.stop()
        elif key == "enter":
            items = self._keyboard_items()
            if not items:
                return
            current = next((widget for widget in items if widget.has_class("keyboard-selected")),
                           items[min(self._keyboard_index, len(items) - 1)])
            if isinstance(current, Input):
                self._keyboard_editing = current.id
                self._keyboard_original = current.value
                current.add_class("keyboard-editing")
                current.focus()
            elif isinstance(current, Select):
                self._keyboard_choose(current.id)
            elif isinstance(current, Checkbox):
                current.value = not current.value
            elif isinstance(current, Button):
                current.press()
            event.stop()
        elif key == "escape" and self.KEYBOARD_CANCEL is not None:
            cancel = self.query_one(f"#{self.KEYBOARD_CANCEL}", Button)
            if not cancel.disabled:
                cancel.press()
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == self._keyboard_editing:
            self._keyboard_return()
            event.stop()

    @work(exclusive=True, group="keyboard-choice")
    async def _keyboard_choose(self, field_id: str) -> None:
        field = self.query_one(f"#{field_id}", Select)
        options = [(str(label), value) for label, value in field._options]
        if not options:
            return
        selected = await self.app.push_screen_wait(
            ChoicePicker(field_id.replace("-", " ").title(), options, field.value)
        )
        if selected is not None:
            field.value = selected
        self._keyboard_return()
