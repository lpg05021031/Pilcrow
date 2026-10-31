"""Following the live end, and when to stop.

Pure state: ``Follow`` says whether the view is following new output (the reader is at the end) and
turns the person's scroll events and the content's growth into one answer: where the scroll value
should be. The view applies it; the easing towards the target runs on the clock.
"""

from __future__ import annotations

from dataclasses import dataclass

NEAR_END = 60  # within this many px of the end counts as "at the end"
LEAVE_UP = 8  # scrolling up by more than this stops following


@dataclass
class Follow:
    following: bool = True
    #: set by the view: the last value it set itself (its own sets never count as the person leaving)
    last_own: int = -1

    def on_user_scroll(self, value: int, previous: int, maximum: int) -> None:
        """A wheel, drag, key or touch moved the view to ``value``."""
        if maximum - value <= NEAR_END:
            self.following = True
        elif value < previous - LEAVE_UP:
            self.following = False

    def on_jump_to_end(self) -> None:
        self.following = True

    def target(self, maximum: int, current: int) -> int:
        """Where the view should be after content changed: the end while following, else unchanged."""
        return maximum if self.following else current

    def shows_latest(self, value: int, maximum: int, viewport_h: int, grew: bool) -> bool:
        """The "Latest" pill: not following, and the end is at least a screen away (or content arrived)."""
        if self.following:
            return False
        return (maximum - value) > max(NEAR_END, viewport_h // 2) and grew
