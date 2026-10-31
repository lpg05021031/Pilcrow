"""The confirm dialog (ConfirmDialog in web/src/components/Overlays.tsx): a title, a line of body, an
optional list of what is affected, an optional checkbox, Cancel and the action."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from ... import theme
from ...store import ConfirmSpec, store
from ..ui.bits import Check, button
from .parts import Well
from .sheet import Sheet


class ConfirmDialog(Sheet):
    def __init__(self, window: QWidget, spec: ConfirmSpec) -> None:
        super().__init__(window, "confirm", None, width=440, title=spec.title)
        self.spec = spec
        self.checkbox: Optional[QCheckBox] = None
        if spec.body:
            body = QLabel(spec.body)
            body.setWordWrap(True)
            theme.tint(body, fg="text_2")
            self.body_lay.addWidget(body)
        if spec.list:
            well = Well(theme.R2)
            wl = QVBoxLayout(well)
            wl.setContentsMargins(12, 8, 12, 8)
            wl.setSpacing(2)
            for x in spec.list[:12]:
                lb = QLabel(x)
                lb.setFont(theme.font_mono())
                lb.setStyleSheet(f"font-size: {theme.FS_SMALL}px;")
                wl.addWidget(lb)
            if len(spec.list) > 12:
                more = QLabel(f"and {len(spec.list) - 12} more")
                theme.tint(more, fg="text_3")
                wl.addWidget(more)
            self.body_lay.addWidget(well)
        if spec.checkbox:
            self.checkbox = Check(str(spec.checkbox.get("label", "")))
            self.checkbox.setChecked(bool(spec.checkbox.get("value")))
            self.body_lay.addWidget(self.checkbox)
        self.cancel_btn = button("Cancel", on_click=self.reject)
        self.cancel_btn.setAccessibleName("Cancel")
        self.confirm_btn = button(spec.confirm, kind="danger" if spec.danger else "primary", on_click=self.confirm)
        self.confirm_btn.setAccessibleName(spec.confirm)
        self.confirm_btn.setDefault(True)
        self.confirm_btn.setProperty("autofocus", True)
        self.add_footer(self.cancel_btn, self.confirm_btn)

    def checked(self) -> bool:
        return bool(self.checkbox is not None and self.checkbox.isChecked())

    def confirm(self) -> None:
        checked = self.checked()
        self.reject()  # closes through the store, like the web's close() before onConfirm
        self.spec.on_confirm(checked)

    def submit(self) -> None:
        self.confirm()

    _ = store
