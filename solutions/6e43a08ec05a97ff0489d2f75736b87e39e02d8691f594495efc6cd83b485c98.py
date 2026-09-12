# problem_id: 6e43a08ec05a97ff0489d2f75736b87e39e02d8691f594495efc6cd83b485c98
# source_language: python
# phase: 3
# entrypoint: normalize_protection
# verified: True

def normalize_protection(ranges, edits, max_row, max_col):
    """
    Normalise a list of protected cell ranges after a series of row/column edits.

    Parameters
    ----------
    ranges : Iterable[Tuple[int, int, int, int]]
        Each tuple is ``(r1, c1, r2, c2)`` describing a rectangular region
        (inclusive) where ``r1 <= r2`` and ``c1 <= c2``.  Rows and columns are
        0‑based.
    edits : Iterable[Tuple[str, int, int]]
        Edit operations.  The first element is the operation name:

        * ``"ins_row"`` – insert ``count`` rows *before* ``index``.
        * ``"del_row"`` – delete ``count`` rows starting at ``index``.
        * ``"ins_col"`` – insert ``count`` columns *before* ``index``.
        * ``"del_col"`` – delete ``count`` columns starting at ``index``.

        ``index`` and ``count`` are non‑negative integers.
    max_row, max_col : int
        The size of the sheet after all edits (exclusive upper bound).

    Returns
    -------
    List[Tuple[int, int, int, int]]
        Normalised, non‑overlapping, clipped protection ranges sorted by
        ``(r1, c1)``.
    """
    # ------------------------------------------------------------------ #
    # Helper: shift a single coordinate according to one edit operation.
    # ------------------------------------------------------------------ #
    def shift(coord, op, idx, cnt):
        """Return the new coordinate after applying a single edit."""
        if op.startswith("ins"):
            # insertion: everything at or after idx moves forward
            return coord + cnt if coord >= idx else coord
        else:  # delete
            if coord < idx:
                return coord
            if coord >= idx + cnt:
                return coord - cnt
            # coordinate lies inside the deleted interval → it disappears
            return None

    # ------------------------------------------------------------------ #
    # Apply all edits to every range.
    # ------------------------------------------------------------------ #
    transformed = []
    for r1, c1, r2, c2 in ranges:
        # work on a mutable copy
        cur = [r1, c1, r2, c2]
        for op, idx, cnt in edits:
            if cnt == 0:
                continue
            if op in ("ins_row", "del_row"):
                # rows
                new_top = shift(cur[0], op, idx, cnt)
                new_bot = shift(cur[2], op, idx, cnt)
                if new_top is None or new_bot is None:
                    # the range is completely removed – abort early
                    cur = None
                    break
                cur[0], cur[2] = new_top, new_bot
                # keep order (in case a delete collapsed the range)
                if cur[0] > cur[2]:
                    cur[0], cur[2] = cur[2], cur[0]
            else:
                # columns
                new_left = shift(cur[1], op, idx, cnt)
                new_right = shift(cur[3], op, idx, cnt)
                if new_left is None or new_right is None:
                    cur = None
                    break
                cur[1], cur[3] = new_left, new_right
                if cur[1] > cur[3]:
                    cur[1], cur[3] = cur[3], cur[1]

        if cur is None:
            continue

        # Clip to sheet bounds
        cur[0] = max(0, min(cur[0], max_row - 1))
        cur[2] = max(0, min(cur[2], max_row - 1))
        cur[1] = max(0, min(cur[1], max_col - 1))
        cur[3] = max(0, min(cur[3], max_col - 1))

        # Discard empty ranges (zero‑size after clipping)
        if cur[0] > cur[2] or cur[1] > cur[3]:
            continue

        transformed.append(tuple(cur))

    # ------------------------------------------------------------------ #
    # Merge overlapping / adjacent rectangles.
    # ------------------------------------------------------------------ #
    if not transformed:
        return []

    # Sort by top‑left corner
    transformed.sort(key=lambda r: (r[0], r[1], r[2], r[3]))

    merged = []
    cur = list(transformed[0])

    for r1, c1, r2, c2 in transformed[1:]:
        # Overlap or touch if the rectangles intersect in both dimensions
        if r1 <= cur[2] + 1 and c1 <= cur[3] + 1 and r2 >= cur[0] - 1 and c2 >= cur[1] - 1:
            # Expand current rectangle to include the new one
            cur[0] = min(cur[0], r1)
            cur[1] = min(cur[1], c1)
            cur[2] = max(cur[2], r2)
            cur[3] = max(cur[3], c2)
        else:
            merged.append(tuple(cur))
            cur = [r1, c1, r2, c2]

    merged.append(tuple(cur))
    return merged
