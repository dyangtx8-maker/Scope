# problem_id: 2beff58fa923cf9e2df2b235a24c42021d9c9553f19fcfd01e46c7fae4987efb
# source_language: python
# phase: 3
# entrypoint: refresh_references
# verified: True

def refresh_references(initial, snapshots, references):
    """
    Resolve a series of read‑queries against a persistently‑updated array.

    Parameters
    ----------
    initial : Sequence[Any]
        The original array (snapshot ``0``).
    snapshots : Sequence[tuple[int, Any]]
        A sequence of updates.  ``snapshots[i]`` is a ``(pos, value)`` pair
        that is applied *after* snapshot ``i`` and creates snapshot ``i+1``.
        ``pos`` is an index into ``initial`` (0‑based).
    references : Sequence[tuple[int, int]]
        Queries of the form ``(snap_id, pos)`` asking for the value at index
        ``pos`` in snapshot ``snap_id``.  ``snap_id`` may range from ``0`` to
        ``len(snapshots)`` inclusive.

    Returns
    -------
    list[Any]
        The values for each query in the order they appear in ``references``.

    Notes
    -----
    The implementation builds, for each array position, a chronologically
    ordered list of ``(snapshot_id, value)`` pairs.  The initial value is stored
    with snapshot id ``0``.  Each query is answered with a binary search
    (``bisect_right``) to locate the most recent update not later than the
    requested snapshot.  This yields ``O((U+Q) log U)`` time where ``U`` is the
    number of updates and ``Q`` the number of queries, and uses ``O(U)`` extra
    memory.
    """
    from bisect import bisect_right

    # Map each position to a list of (snapshot_id, value) sorted by snapshot_id.
    # Start with the initial values at snapshot 0.
    history = {}
    for pos, val in enumerate(initial):
        history[pos] = [(0, val)]

    # Record each update with its snapshot id (1‑based).
    for snap_id, (pos, val) in enumerate(snapshots, start=1):
        # Ensure the position exists (it should, but guard against bad input).
        if pos not in history:
            # If a new position appears, treat its value before the first
            # update as ``None``.
            history[pos] = [(0, None)]
        history[pos].append((snap_id, val))

    # Resolve queries.
    results = []
    for snap_id, pos in references:
        # If the position was never touched, fall back to the initial value
        # (or ``None`` if it never existed).
        lst = history.get(pos, [(0, None)])

        # Extract the list of snapshot ids for binary search.
        snap_ids = [s for s, _ in lst]
        # bisect_right returns the insertion point to keep the list sorted;
        # subtract 1 to get the last snapshot <= requested snap_id.
        idx = bisect_right(snap_ids, snap_id) - 1
        # idx is guaranteed to be >= 0 because snapshot 0 is always present.
        results.append(lst[idx][1])

    return results
