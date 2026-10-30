"""A Fenwick (binary indexed) tree over integer heights.

The y of the i-th block in a list is ``prefix(i)``; the block under a y is ``find(y)``; a height
change is ``set(i, h)``. All three are O(log n). Appending at the end is O(log n) too (the live end
of a conversation grows by appending); cutting the tail or inserting in the middle rebuilds in O(n)
and is rare (a unit removed, a live turn reshaped).
"""

from __future__ import annotations

from typing import Iterable, List


class Fenwick:
    __slots__ = ("_n", "_tree", "_vals")

    def __init__(self, values: Iterable[int] = ()) -> None:
        self._vals: List[int] = [int(v) for v in values]
        self._n = len(self._vals)
        self._tree: List[int] = []
        self._build()

    def _build(self) -> None:
        n = self._n
        t = [0] * (n + 1)
        vals = self._vals
        for i in range(1, n + 1):
            t[i] += vals[i - 1]
            j = i + (i & -i)
            if j <= n:
                t[j] += t[i]
        self._tree = t

    # ---- reading
    def __len__(self) -> int:
        return self._n

    def get(self, i: int) -> int:
        return self._vals[i]

    def values(self) -> List[int]:
        return list(self._vals)

    def prefix(self, i: int) -> int:
        """The sum of the first ``i`` values: the y where block ``i`` starts."""
        if i > self._n:
            i = self._n
        s = 0
        t = self._tree
        while i > 0:
            s += t[i]
            i -= i & -i
        return s

    def total(self) -> int:
        return self.prefix(self._n)

    def find(self, y: int) -> int:
        """The index of the block that contains ``y``: the largest ``i`` with ``prefix(i) <= y``,
        clamped into the list. Blocks of height 0 are skipped (the block after them wins)."""
        n = self._n
        if n == 0:
            return 0
        if y <= 0:
            return 0
        pos = 0
        rem = y
        pw = 1 << (n.bit_length() - 1)
        t = self._tree
        while pw:
            nxt = pos + pw
            if nxt <= n and t[nxt] <= rem:
                pos = nxt
                rem -= t[nxt]
            pw >>= 1
        return pos if pos < n else n - 1

    # ---- writing
    def add(self, i: int, delta: int) -> None:
        if not delta:
            return
        self._vals[i] += delta
        n = self._n
        t = self._tree
        i += 1
        while i <= n:
            t[i] += delta
            i += i & -i

    def set(self, i: int, value: int) -> int:
        """Set a height; returns the delta applied."""
        d = int(value) - self._vals[i]
        if d:
            self.add(i, d)
        return d

    def append(self, value: int) -> None:
        value = int(value)
        self._vals.append(value)
        self._n += 1
        n = self._n
        # tree[n] covers vals[n - lowbit(n) .. n-1]: the new value plus the trees that tile the rest
        s = value
        k = n - 1
        lo = n - (n & -n)
        t = self._tree
        while k > lo:
            s += t[k]
            k -= k & -k
        t.append(s)

    def truncate(self, k: int) -> None:
        """Keep the first ``k`` values."""
        if k >= self._n:
            return
        del self._vals[max(0, k) :]
        self._n = len(self._vals)
        self._build()

    def insert(self, i: int, value: int) -> None:
        self._vals.insert(i, int(value))
        self._n += 1
        self._build()

    def remove(self, i: int) -> None:
        del self._vals[i]
        self._n -= 1
        self._build()

    def replace(self, values: Iterable[int]) -> None:
        self._vals = [int(v) for v in values]
        self._n = len(self._vals)
        self._build()
