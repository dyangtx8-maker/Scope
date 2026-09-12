# problem_id: 6e43a08ec05a97ff0489d2f75736b87e39e02d8691f594495efc6cd83b485c98
# source_language: python
# phase: 2
# entrypoint: normalize_protection
# verified: True

from bisect import bisect_left
from typing import List, Tuple, Dict


class LineTransform:
    """
    Maintains a piece‑wise linear mapping from original line numbers
    (1‑based) to current line numbers after a sequence of insert/delete
    operations.  Each piece is stored as (orig_start, orig_end, shift)
    meaning that for any original i in [orig_start, orig_end]
    the current coordinate is i + shift.
    """

    __slots__ = ("max_coord", "segments")

    def __init__(self, max_coord: int):
        self.max_coord = max_coord
        # initially one segment, shift = 0
        self.segments: List[Tuple[int, int, int]] = [(1, max_coord, 0)]

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _new_interval(self, seg: Tuple[int, int, int]) -> Tuple[int, int]:
        o1, o2, sh = seg
        return o1 + sh, o2 + sh

    def _clip(self) -> None:
        """remove parts that moved beyond max_coord"""
        new_segs = []
        for o1, o2, sh in self.segments:
            n1, n2 = o1 + sh, o2 + sh
            if n1 > self.max_coord:
                continue
            if n2 > self.max_coord:
                # truncate
                o2 = self.max_coord - sh
                n2 = self.max_coord
            new_segs.append((o1, o2, sh))
        self.segments = new_segs

    # ------------------------------------------------------------------ #
    # public operations
    # ------------------------------------------------------------------ #
    def insert(self, index: int, delta: int) -> None:
        """insert delta lines before `index` (1‑based)"""
        i = 0
        while i < len(self.segments):
            o1, o2, sh = self.segments[i]
            n1, n2 = o1 + sh, o2 + sh
            if n2 < index:
                i += 1
                continue
            if n1 >= index:
                break
            # split the segment because index lies inside it
            left_len = index - n1
            left_o2 = o1 + left_len - 1
            left = (o1, left_o2, sh)
            right_o1 = left_o2 + 1
            right = (right_o1, o2, sh + delta)
            self.segments[i] = left
            self.segments.insert(i + 1, right)
            i += 2
            break

        # all following segments shift forward
        for j in range(i, len(self.segments)):
            o1, o2, sh = self.segments[j]
            self.segments[j] = (o1, o2, sh + delta)

        self._clip()

    def delete(self, index: int, d: int) -> None:
        """delete d lines starting at `index` (1‑based)"""
        del_start = index
        del_end = index + d - 1
        new_segments: List[Tuple[int, int, int]] = []

        for o1, o2, sh in self.segments:
            n1, n2 = o1 + sh, o2 + sh

            if n2 < del_start:
                # completely before deletion range
                new_segments.append((o1, o2, sh))
                continue

            if n1 > del_end:
                # completely after deletion range – shift left
                new_segments.append((o1, o2, sh - d))
                continue

            # overlap exists – possibly split into left/right parts
            # left part (if any)
            if n1 < del_start:
                left_len = del_start - n1
                left_o2 = o1 + left_len - 1
                new_segments.append((o1, left_o2, sh))

            # right part (if any)
            if n2 > del_end:
                right_len = n2 - del_end
                # original rows belonging to the right part are the last `right_len` rows
                right_o1 = o2 - right_len + 1
                # after deletion they start at `del_start`
                right_shift = del_start - right_o1
                new_segments.append((right_o1, o2, right_shift))

            # overlapping rows are discarded

        self.segments = new_segments
        self._clip()

    # ------------------------------------------------------------------ #
    # query
    # ------------------------------------------------------------------ #
    def map_interval(self, l: int, r: int) -> List[Tuple[int, int]]:
        """Map original inclusive interval [l, r] to a list of current intervals."""
        if l > r:
            return []
        # binary search for first segment that may intersect
        lo, hi = 0, len(self.segments)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.segments[mid][1] < l:
                lo = mid + 1
            else:
                hi = mid
        res: List[Tuple[int, int]] = []
        i = lo
        while i < len(self.segments):
            o1, o2, sh = self.segments[i]
            if o1 > r:
                break
            ov_l = max(l, o1)
            ov_r = min(r, o2)
            if ov_l <= ov_r:
                res.append((ov_l + sh, ov_r + sh))
            i += 1
        return res


def normalize_protection(
    ranges: List[Tuple[int, int, int, int]],
    edits: List[Tuple[str, int, int]],
    max_row: int,
    max_col: int,
) -> List[Tuple[int, int, int, int]]:
    """
    Apply the sequence of edits to the protected rectangles and return the
    final protected union in the canonical row‑run form.
    """
    row_tf = LineTransform(max_row)
    col_tf = LineTransform(max_col)

    for axis, idx, delta in edits:
        if axis == "row":
            if delta > 0:
                row_tf.insert(idx, delta)
            else:
                row_tf.delete(idx, -delta)
        else:  # column
            if delta > 0:
                col_tf.insert(idx, delta)
            else:
                col_tf.delete(idx, -delta)

    # ------------------------------------------------------------------ #
    # Transform each input rectangle into possibly several rectangles
    # ------------------------------------------------------------------ #
    transformed: List[Tuple[int, int, int, int]] = []
    for r1, c1, r2, c2 in ranges:
        row_ints = row_tf.map_interval(r1, r2)
        col_ints = col_tf.map_interval(c1, c2)
        if not row_ints or not col_ints:
            continue
        for nr1, nr2 in row_ints:
            for nc1, nc2 in col_ints:
                transformed.append((nr1, nc1, nr2, nc2))

    if not transformed:
        return []

    # ------------------------------------------------------------------ #
    # Sweep rows, maintaining a multiset of column intervals
    # ------------------------------------------------------------------ #
    events: Dict[int, List[Tuple[int, Tuple[int, int]]]] = {}
    for r1, c1, r2, c2 in transformed:
        events.setdefault(r1, []).append((1, (c1, c2)))          # add
        events.setdefault(r2 + 1, []).append((-1, (c1, c2)))    # remove

    sorted_rows = sorted(events.keys())
    active_counts: Dict[Tuple[int, int], int] = {}
    result: List[Tuple[int, int, int, int]] = []

    prev_row = None
    for row in sorted_rows:
        if prev_row is not None and prev_row < row:
            if active_counts:
                # compute merged column intervals from active set
                intervals = sorted([iv for iv, cnt in active_counts.items() if cnt > 0])
                merged: List[Tuple[int, int]] = []
                for a, b in intervals:
                    if not merged or a > merged[-1][1] + 1:
                        merged.append([a, b])
                    else:
                        merged[-1][1] = max(merged[-1][1], b)
                for c1, c2 in merged:
                    result.append((prev_row, c1, row - 1, c2))
        # process events at this row
        for typ, (c1, c2) in events[row]:
            key = (c1, c2)
            active_counts[key] = active_counts.get(key, 0) + typ
            if active_counts[key] == 0:
                del active_counts[key]
        prev_row = row

    # No need to handle tail because after the last event the active set is empty
    # (all rectangles have been closed).

    # ------------------------------------------------------------------ #
    # Final sorting as required
    # ------------------------------------------------------------------ #
    result.sort()
    return result
