# problem_id: 1dea328020722d9c3737e123e64dafc670e9bcbc8a9e0d3163eb6352fa2d9710
# source_language: python
# phase: 3
# entrypoint: track_indicator
# verified: True

import random
from typing import List, Tuple, Optional

# ---------- Treap node ----------
class Node:
    __slots__ = ("id", "prio", "left", "right", "parent", "size", "rev")
    def __init__(self, identifier: int):
        self.id: int = identifier
        self.prio: int = random.randint(1, 1 << 30)
        self.left: Optional["Node"] = None
        self.right: Optional["Node"] = None
        self.parent: Optional["Node"] = None
        self.size: int = 1
        self.rev: bool = False

# ---------- Helper functions ----------
def _size(node: Optional[Node]) -> int:
    return node.size if node else 0

def _update(node: Node) -> None:
    node.size = 1 + _size(node.left) + _size(node.right)
    if node.left:
        node.left.parent = node
    if node.right:
        node.right.parent = node

def _push(node: Node) -> None:
    if node.rev:
        node.left, node.right = node.right, node.left
        if node.left:
            node.left.rev ^= True
        if node.right:
            node.right.rev ^= True
        node.rev = False

# ---------- Split / Merge ----------
def _split(root: Optional[Node], k: int) -> Tuple[Optional[Node], Optional[Node]]:
    """split first k nodes to left part"""
    if not root:
        return None, None
    _push(root)
    if k <= _size(root.left):
        left, new_left = _split(root.left, k)
        root.left = new_left
        if new_left:
            new_left.parent = root
        _update(root)
        if left:
            left.parent = None
        return left, root
    else:
        new_right, right = _split(root.right, k - _size(root.left) - 1)
        root.right = new_right
        if new_right:
            new_right.parent = root
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
    _push(a)
    _push(b)
    if a.prio < b.prio:
        a.right = _merge(a.right, b)
        if a.right:
            a.right.parent = a
        _update(a)
        a.parent = None
        return a
    else:
        b.left = _merge(a, b.left)
        if b.left:
            b.left.parent = b
        _update(b)
        b.parent = None
        return b

# ---------- Ancestor handling ----------
def _push_path(node: Node) -> None:
    """push lazy flags from root down to this node"""
    stack = []
    cur = node
    while cur:
        stack.append(cur)
        cur = cur.parent
    for n in reversed(stack):
        _push(n)

def _index(node: Node) -> int:
    """zero‑based index of node in the current tree"""
    _push_path(node)
    idx = _size(node.left)
    while node.parent:
        if node is node.parent.right:
            idx += 1 + _size(node.parent.left)
        node = node.parent
    return idx

def _leftmost(node: Node) -> Node:
    _push(node)
    while node.left:
        node = node.left
        _push(node)
    return node

def _rightmost(node: Node) -> Node:
    _push(node)
    while node.right:
        node = node.right
        _push(node)
    return node

def _next(node: Node) -> Optional[Node]:
    _push_path(node)
    if node.right:
        return _leftmost(node.right)
    while node.parent and node is node.parent.right:
        node = node.parent
    return node.parent

def _prev(node: Node) -> Optional[Node]:
    _push_path(node)
    if node.left:
        return _rightmost(node.left)
    while node.parent and node is node.parent.left:
        node = node.parent
    return node.parent

# ---------- Build treap from a list of ids ----------
def _build(ids: List[int], id_to_node: dict) -> Optional[Node]:
    root: Optional[Node] = None
    for identifier in ids:
        nd = Node(identifier)
        id_to_node[identifier] = nd
        root = _merge(root, nd)
    return root

# ---------- Main function ----------
def track_indicator(initial_ids: List[int],
                   selected_index: int,
                   operations: List[Tuple]) -> List[int]:
    # mapping identifier -> node
    id_node: dict[int, Node] = {}

    # build initial treap
    root = _build(initial_ids, id_node)

    # selected node reference
    selected: Optional[Node] = None
    if selected_index != -1:
        # obtain node at that index
        left, rest = _split(root, selected_index)
        sel, right = _split(rest, 1)
        selected = sel
        # reassemble
        root = _merge(_merge(left, sel), right)

    result: List[int] = []

    for op in operations:
        typ = op[0]

        if typ == "insert":
            _, p, ids = op
            # split at p
            left, right = _split(root, p)
            mid = _build(list(ids), id_node)
            root = _merge(_merge(left, mid), right)

            # selection rule when strip was empty
            if selected is None:
                # first inserted becomes selected
                # find leftmost node of mid (which is ids[0])
                selected = id_node[ids[0]]

        elif typ == "remove":
            _, ids = op
            remove_set = set(ids)

            # determine new selection if needed
            if selected and selected.id in remove_set:
                # look rightward first
                cand = _next(selected)
                while cand and cand.id in remove_set:
                    cand = _next(cand)
                if cand is None:
                    # look leftward
                    cand = _prev(selected)
                    while cand and cand.id in remove_set:
                        cand = _prev(cand)
                selected = cand  # may be None

            # delete each node
            for identifier in ids:
                node = id_node.pop(identifier)
                # isolate node
                idx = _index(node)
                left, tmp = _split(root, idx)
                _, right = _split(tmp, 1)   # discard the node
                root = _merge(left, right)

        elif typ == "reverse":
            _, left_pos, right_pos = op
            left_part, rest = _split(root, left_pos)
            mid, right_part = _split(rest, right_pos - left_pos)
            if mid:
                mid.rev ^= True
            root = _merge(_merge(left_part, mid), right_part)

        # record current selected index
        if selected is None:
            result.append(-1)
        else:
            result.append(_index(selected))

    return result
