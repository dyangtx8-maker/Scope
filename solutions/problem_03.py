# problem_id: 1dea328020722d9c3737e123e64dafc670e9bcbc8a9e0d3163eb6352fa2d9710
# source_language: python
# phase: 2
# entrypoint: track_indicator
# verified: True

import random
from typing import List, Tuple, Optional

# ---------- Treap implementation ----------
class Node:
    __slots__ = ("id", "prio", "left", "right", "size", "rev", "parent")
    def __init__(self, id_: int, prio: int):
        self.id = id_
        self.prio = prio
        self.left: Optional["Node"] = None
        self.right: Optional["Node"] = None
        self.size = 1
        self.rev = False
        self.parent: Optional["Node"] = None

def _sz(node: Optional[Node]) -> int:
    return node.size if node else 0

def _update(node: Node) -> None:
    node.size = 1 + _sz(node.left) + _sz(node.right)
    if node.left:
        node.left.parent = node
    if node.right:
        node.right.parent = node

def _push(node: Optional[Node]) -> None:
    if node and node.rev:
        node.left, node.right = node.right, node.left
        if node.left:
            node.left.rev ^= True
        if node.right:
            node.right.rev ^= True
        node.rev = False

def _split(root: Optional[Node], k: int) -> Tuple[Optional[Node], Optional[Node]]:
    """split first k nodes to left part (0‑based, k may be 0 or size)"""
    if not root:
        return None, None
    _push(root)
    left_sz = _sz(root.left)
    if k <= left_sz:
        left, right = _split(root.left, k)
        root.left = right
        if right:
            right.parent = root
        _update(root)
        if left:
            left.parent = None
        return left, root
    else:
        left, right = _split(root.right, k - left_sz - 1)
        root.right = left
        if left:
            left.parent = root
        _update(root)
        if right:
            right.parent = None
        return root, right

def _merge(a: Optional[Node], b: Optional[Node]) -> Optional[Node]:
    if not a or not b:
        res = a or b
        if res:
            res.parent = None
        return res
    if a.prio < b.prio:
        _push(a)
        a.right = _merge(a.right, b)
        if a.right:
            a.right.parent = a
        _update(a)
        a.parent = None
        return a
    else:
        _push(b)
        b.left = _merge(a, b.left)
        if b.left:
            b.left.parent = b
        _update(b)
        b.parent = None
        return b

def _push_path(node: Node) -> None:
    """propagate lazy flags from root to this node"""
    stack = []
    cur = node
    while cur:
        stack.append(cur)
        cur = cur.parent
    while stack:
        _push(stack.pop())

def _index(node: Node) -> int:
    """0‑based index of node in the whole treap (assumes path is clean)"""
    _push_path(node)
    idx = _sz(node.left)
    cur = node
    while cur.parent:
        if cur is cur.parent.right:
            idx += 1 + _sz(cur.parent.left)
        cur = cur.parent
    return idx

def _kth(root: Node, k: int) -> Node:
    """return node at position k (0‑based)"""
    cur = root
    while True:
        _push(cur)
        left_sz = _sz(cur.left)
        if k < left_sz:
            cur = cur.left
        elif k == left_sz:
            return cur
        else:
            k -= left_sz + 1
            cur = cur.right

def _erase(root: Optional[Node], node: Node) -> Optional[Node]:
    """remove node from treap, return new root"""
    _push_path(node)
    left, right = node.left, node.right
    if left:
        left.parent = None
    if right:
        right.parent = None
    merged = _merge(left, right)

    parent = node.parent
    if parent is None:
        return merged
    if parent.left is node:
        parent.left = merged
    else:
        parent.right = merged
    if merged:
        merged.parent = parent

    # update ancestors
    cur = parent
    while cur:
        _update(cur)
        cur = cur.parent

    # climb to real root
    while parent.parent:
        parent = parent.parent
    return parent

# ---------- Main function ----------
def track_indicator(initial_ids: List[int],
                    selected_index: int,
                    operations: List[Tuple]) -> List[int]:
    rng = random.Random(0)          # deterministic priorities
    id_to_node = {}

    root: Optional[Node] = None

    # build initial treap
    for id_ in initial_ids:
        node = Node(id_, rng.randrange(1 << 30))
        id_to_node[id_] = node
        root = _merge(root, node)

    # initial selection
    if selected_index == -1 or not initial_ids:
        selected_id: Optional[int] = None
    else:
        sel_node = _kth(root, selected_index)  # type: ignore[arg-type]
        selected_id = sel_node.id

    result: List[int] = []

    # helper to record current indicator index
    def record():
        if selected_id is None:
            result.append(-1)
        else:
            node = id_to_node[selected_id]
            result.append(_index(node))

    for op in operations:
        typ = op[0]

        if typ == "insert":
            _, p, ids = op
            # build treap for new ids
            new_root: Optional[Node] = None
            for id_ in ids:
                node = Node(id_, rng.randrange(1 << 30))
                id_to_node[id_] = node
                new_root = _merge(new_root, node)

            # split and merge
            left, right = _split(root, p)
            root = _merge(_merge(left, new_root), right)

            # if there was no selection, select the first inserted element
            if selected_id is None and root is not None:
                selected_id = ids[0]

        elif typ == "remove":
            _, ids = op
            ids_set = set(ids)
            sel_removed = selected_id is not None and selected_id in ids_set
            sel_idx = None
            if sel_removed:
                sel_idx = _index(id_to_node[selected_id])

            # erase all listed ids
            for id_ in ids:
                node = id_to_node.pop(id_)
                root = _erase(root, node)

            # adjust selection if needed
            if sel_removed:
                if root is None:
                    selected_id = None
                else:
                    # try to keep the same index; if it fell off the end,
                    # pick the new last element
                    if sel_idx < root.size:
                        node = _kth(root, sel_idx)
                    else:
                        node = _kth(root, root.size - 1)
                    selected_id = node.id

        elif typ == "reverse":
            _, left_pos, right_pos = op
            # reverse segment [left_pos, right_pos) – right exclusive
            left, rest = _split(root, left_pos)
            mid, right = _split(rest, right_pos - left_pos)
            if mid:
                mid.rev ^= True
            root = _merge(left, _merge(mid, right))

        else:
            raise ValueError(f"unknown operation {typ}")

        record()

    return result
