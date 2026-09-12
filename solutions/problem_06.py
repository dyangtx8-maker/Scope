# problem_id: 5cb294c18288f6ea4e50d59fd9831610232b6df28cc1a84b7d23e3d1eb5c7bb1
# source_language: python
# phase: 2
# entrypoint: validate_build
# verified: True

from __future__ import annotations
from typing import List, Tuple, Union, Iterator, Dict
from functools import lru_cache

Descriptor = Union[str, Tuple[str, int], Tuple[str, 'Descriptor', int]]
SchemaTerm = Tuple[str, ...]          # ("field", name, desc) or ("repeat", t, r)
Operation = Tuple


def validate_build(schemas: List[Tuple[SchemaTerm, ...]],
                   root: int,
                   operations: List[Operation]) -> int:
    # ---------- helpers ----------
    def is_primitive(d: Descriptor) -> bool:
        return isinstance(d, str) and d in ("int", "text")

    # descriptor equality with caching
    @lru_cache(maxsize=None)
    def desc_eq(a: Descriptor, b: Descriptor) -> bool:
        if a is b:
            return True
        if type(a) != type(b):
            return False
        if isinstance(a, str):
            return a == b
        # tuple descriptors
        if a[0] == "record":
            return a[1] == b[1]
        if a[0] == "array":
            return desc_eq(a[1], b[1]) and a[2] == b[2]
        return False

    # ---------- pre‑compute schema statistics ----------
    n = len(schemas)
    total_fields: List[int] = [0] * n          # total flattened fields (may be huge)
    primitive_only: List[bool] = [True] * n   # true iff every flattened field is primitive

    for s in range(n):
        tot = 0
        prim = True
        for term in schemas[s]:
            if term[0] == "field":
                tot += 1
                if not is_primitive(term[2]):
                    prim = False
            else:  # repeat
                _, t, r = term
                tot += total_fields[t] * r
                if not primitive_only[t]:
                    prim = False
        total_fields[s] = tot
        primitive_only[s] = prim

    # ---------- lazy iterator over a record ----------
    class RecordIter:
        __slots__ = ("stack",)

        def __init__(self, schema_id: int):
            # each frame: [sid, idx, repeat_left, repeat_schema]
            # repeat_left == 0 means we are not currently inside a repeat term
            self.stack: List[List] = [[schema_id, 0, 0, None]]

        # get next (name, descriptor); raise StopIteration if exhausted
        def __next__(self) -> Tuple[str, Descriptor]:
            while self.stack:
                sid, idx, rep_left, rep_schema = self.stack[-1]
                terms = schemas[sid]

                # finished current schema instance?
                if idx >= len(terms):
                    self.stack.pop()
                    continue

                # if we have just finished all repetitions of a repeat term,
                # advance to the next term
                if rep_left == 0 and rep_schema is not None:
                    self.stack[-1][1] = idx + 1          # move past repeat term
                    self.stack[-1][3] = None
                    continue

                term = terms[idx]
                if term[0] == "field":
                    # consume this field
                    self.stack[-1][1] = idx + 1
                    return term[1], term[2]            # name, descriptor
                else:  # repeat
                    _, t, r = term
                    if rep_left == 0:
                        # start repetitions
                        self.stack[-1][2] = r
                        self.stack[-1][3] = t
                        rep_left = r
                    # we have at least one repetition left
                    # decrement counter for the repetition we are about to process
                    self.stack[-1][2] = rep_left - 1
                    # push inner schema
                    self.stack.append([t, 0, 0, None])
                    continue
            raise StopIteration

        # consume k primitive fields; return True iff possible (state advanced)
        def consume_primitive(self, k: int) -> bool:
            while k > 0:
                if not self.stack:
                    return False
                sid, idx, rep_left, rep_schema = self.stack[-1]
                terms = schemas[sid]

                if idx >= len(terms):
                    self.stack.pop()
                    continue

                if rep_left == 0 and rep_schema is not None:
                    # all repetitions of the repeat term finished
                    self.stack[-1][1] = idx + 1
                    self.stack[-1][3] = None
                    continue

                term = terms[idx]
                if term[0] == "field":
                    desc = term[2]
                    if not is_primitive(desc):
                        return False
                    # consume one primitive field
                    self.stack[-1][1] = idx + 1
                    k -= 1
                    continue
                else:  # repeat
                    _, t, r = term
                    if rep_left == 0:
                        self.stack[-1][2] = r
                        self.stack[-1][3] = t
                        rep_left = r
                    # now rep_left > 0
                    if primitive_only[t]:
                        per = total_fields[t]          # fields per repetition
                        # how many whole repetitions can we skip?
                        whole = min(rep_left, k // per)
                        if whole:
                            k -= whole * per
                            rep_left -= whole
                            self.stack[-1][2] = rep_left
                            if rep_left == 0:
                                # move past repeat term after consuming all repetitions
                                self.stack[-1][1] = idx + 1
                                self.stack[-1][3] = None
                            continue
                        # need to go inside a single repetition
                        # push inner schema for one repetition
                        self.stack[-1][2] = rep_left - 1
                        self.stack.append([t, 0, 0, None])
                        continue
                    else:
                        # non‑primitive schema: must descend one repetition at a time
                        self.stack[-1][2] = rep_left - 1
                        self.stack.append([t, 0, 0, None])
                        continue
            return True

        def exhausted(self) -> bool:
            # iterator is exhausted iff no frames left
            # (or the only remaining frames are finished)
            while self.stack:
                sid, idx, rep_left, rep_schema = self.stack[-1]
                terms = schemas[sid]
                if idx >= len(terms):
                    self.stack.pop()
                    continue
                if rep_left == 0 and rep_schema is not None:
                    # repeat term finished, but we haven't advanced idx yet
                    self.stack[-1][1] = idx + 1
                    self.stack[-1][3] = None
                    continue
                # there is still something to produce
                return False
            return True

    # ---------- container stack ----------
    # each element is a dict with keys:
    #   type: "record" or "array"
    #   iter: RecordIter (for record) or None
    #   remaining: int (capacity left for array) or None
    #   elem_desc: descriptor (for array) or None
    container_stack: List[Dict] = []

    # start with the root record opened
    container_stack.append({
        "type": "record",
        "iter": RecordIter(root),
        "remaining": None,
        "elem_desc": None,
    })

    # ---------- main loop ----------
    for op_idx, op in enumerate(operations, start=1):
        if not container_stack:
            return op_idx  # any operation after everything closed is invalid

        cur = container_stack[-1]

        if op[0] == "put":
            _, name, p = op
            if cur["type"] == "record":
                try:
                    exp_name, exp_desc = next(cur["iter"])
                except StopIteration:
                    return op_idx
                if exp_name != name or not desc_eq(exp_desc, p):
                    return op_idx
            else:  # array
                if name is not None:
                    return op_idx
                if cur["remaining"] is None or cur["remaining"] <= 0:
                    return op_idx
                if not desc_eq(cur["elem_desc"], p):
                    return op_idx
                cur["remaining"] -= 1

        elif op[0] == "open":
            _, name, d = op
            if cur["type"] == "record":
                try:
                    exp_name, exp_desc = next(cur["iter"])
                except StopIteration:
                    return op_idx
                if exp_name != name or not desc_eq(exp_desc, d):
                    return op_idx
            else:  # array
                if name is not None:
                    return op_idx
                if cur["remaining"] is None or cur["remaining"] <= 0:
                    return op_idx
                if not desc_eq(cur["elem_desc"], d):
                    return op_idx
                cur["remaining"] -= 1

            # push the opened container
            if d[0] == "record":
                container_stack.append({
                    "type": "record",
                    "iter": RecordIter(d[1]),
                    "remaining": None,
                    "elem_desc": None,
                })
            else:  # array
                container_stack.append({
                    "type": "array",
                    "iter": None,
                    "remaining": d[2],
                    "elem_desc": d[1],
                })

        elif op[0] == "default":
            _, k = op
            if cur["type"] == "record":
                # need at least k primitive fields ahead
                if not cur["iter"].consume_primitive(k):
                    return op_idx
            else:  # array
                if not is_primitive(cur["elem_desc"]):
                    return op_idx
                if cur["remaining"] is None or cur["remaining"] < k:
                    return op_idx
                cur["remaining"] -= k

        elif op[0] == "close":
            if cur["type"] == "record":
                if not cur["iter"].exhausted():
                    return op_idx
            # pop current container
            container_stack.pop()
        else:
            # unknown operation (should not happen)
            return op_idx

    # after processing all operations
    if not container_stack:
        return 0
    else:
        return len(operations) + 1
