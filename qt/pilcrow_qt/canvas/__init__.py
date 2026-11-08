"""The canvas engine: a scene of blocks positioned by Fenwick trees and painted into one viewport.

See qt/DESIGN.md. ``fenwick``, ``estimate``, ``anchor`` and ``scene`` are pure Python; ``paint``,
``nodes`` and the blocks use QtGui only; ``view`` is the one QtWidgets module.
"""
