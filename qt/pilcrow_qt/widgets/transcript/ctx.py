"""What every row of a conversation needs to know (a port of ToolCtx in tools/ToolCard.tsx).

One ``ToolCtx`` per session view, rebuilt (new object) whenever the pending permissions or the
view mode change, so rows can compare identity to decide whether to re-render.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

Json = dict


@dataclass(frozen=True)
class ToolCtx:
    session_id: str
    cwd: str
    #: tool_use id → PermissionRequest (the server's dict), for the tools waiting on the person
    pending: Dict[str, Json] = field(default_factory=dict)
    #: nesting depth inside Agent cards (0 at the top level)
    depth: int = 0
    #: 'normal' | 'thinking' | 'verbose'
    view: str = "normal"

    def with_depth(self, depth: int) -> ToolCtx:
        return ToolCtx(self.session_id, self.cwd, self.pending, depth, self.view)
