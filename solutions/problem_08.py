# problem_id: 5d02bd0e16ab63c540232452b60e8e6cf36d01e0820cb6b422b63c1a31091718
# source_language: rust
# phase: 2
# entrypoint: solve
# verified: False

import sys

MOD = 1_000_000_007
HASH_MULT = 911382323

# ---------- persistent segment tree for slots ----------
slot_left = [0]   # index 0 = null node
slot_right = [0]
slot_typ = [0]    # 0 = unpublished, 1 = completion, 2 = value
slot_val = [0]    # only used if typ == 2

def slot_new_node(typ, val, left=0, right=0):
    slot_left.append(left)
    slot_right.append(right)
    slot_typ.append(typ)
    slot_val.append(val)
    return len(slot_left) - 1

def slot_update(node, l, r, idx, new_typ, new_val):
    if l == r:
        return slot_new_node(new_typ, new_val)
    mid = (l + r) // 2
    if idx <= mid:
        new_left = slot_update(slot_left[node], l, mid, idx, new_typ, new_val)
        new_right = slot_right[node]
    else:
        new_left = slot_left[node]
        new_right = slot_update(slot_right[node], mid + 1, r, idx, new_typ, new_val)
    return slot_new_node(0, 0, new_left, new_right)

def slot_query(node, l, r, idx):
    if node == 0:
        return 0, 0          # unpublished
    if l == r:
        return slot_typ[node], slot_val[node]
    mid = (l + r) // 2
    if idx <= mid:
        return slot_query(slot_left[node], l, mid, idx)
    else:
        return slot_query(slot_right[node], mid + 1, r, idx)

# ---------- persistent segment tree for producer -> ticket ----------
map_left = [0]
map_right = [0]
map_val = [-1]   # default = not reserved

def map_new_node(val, left=0, right=0):
    map_left.append(left)
    map_right.append(right)
    map_val.append(val)
    return len(map_left) - 1

def map_update(node, l, r, idx, new_val):
    if l == r:
        return map_new_node(new_val)
    mid = (l + r) // 2
    if idx <= mid:
        new_left = map_update(map_left[node], l, mid, idx, new_val)
        new_right = map_right[node]
    else:
        new_left = map_left[node]
        new_right = map_update(map_right[node], mid + 1, r, idx, new_val)
    return map_new_node(-1, new_left, new_right)

def map_query(node, l, r, idx):
    if node == 0:
        return -1
    if l == r:
        return map_val[node]
    mid = (l + r) // 2
    if idx <= mid:
        return map_query(map_left[node], l, mid, idx)
    else:
        return map_query(map_right[node], mid + 1, r, idx)

# ---------- main solver ----------
def solve(stdin: str) -> str:
    it = iter(stdin.strip().splitlines())
    N_Q = next(it).split()
    N = int(N_Q[0])
    Q = int(N_Q[1])

    # version data
    root_slot = [0] * (Q + 1)
    root_map = [0] * (Q + 1)
    p = [0] * (Q + 1)
    c = [0] * (Q + 1)
    d = [0] * (Q + 1)

    out_lines = []

    for ver in range(1, Q + 1):
        parts = next(it).split()
        op = parts[0]
        b = int(parts[1])

        # start from version b
        cur_root_slot = root_slot[b]
        cur_root_map = root_map[b]
        cur_p = p[b]
        cur_c = c[b]
        cur_d = d[b]

        if op == 'R':
            s = int(parts[2]) - 1          # 0‑based producer index
            ticket = cur_p
            cur_root_map = map_update(cur_root_map, 0, N - 1, s, ticket)
            cur_p += 1

        elif op == 'W':
            s = int(parts[2]) - 1
            ticket = map_query(cur_root_map, 0, N - 1, s)
            # must be reserved
            if parts[3] == 'E':            # completion
                cur_root_slot = slot_update(cur_root_slot, 0, N - 1, ticket, 1, 0)
            else:                           # value
                x = int(parts[4])
                cur_root_slot = slot_update(cur_root_slot, 0, N - 1, ticket, 2, x)

        elif op == 'D':
            k = int(parts[2])
            cur_d += k

        elif op == 'P':
            # drain
            m = 0
            h = 0
            cc = cur_c
            dd = cur_d
            while cc < cur_p:
                typ, val = slot_query(cur_root_slot, 0, N - 1, cc)
                if typ == 0:          # unpublished
                    break
                if typ == 1:          # completion
                    cc += 1
                    continue
                # typ == 2, value
                if dd == 0:
                    break
                r = val % MOD
                h = (h * HASH_MULT + r) % MOD
                m += 1
                cc += 1
                dd -= 1
            # status
            if cur_p == cc == N:
                status = "COMPLETE"
            elif cc == cur_p and cc < N:
                status = "WAITING"
            else:   # cc < cur_p
                typ, _ = slot_query(cur_root_slot, 0, N - 1, cc)
                if typ == 0:
                    status = "BLOCKED"
                else:   # typ == 2 (value) and dd == 0
                    status = "BACKPRESSURE"
            out_lines.append(f"DRAIN {m} {h} {status} {cur_p} {cc} {dd}")
            # store new consumer index and demand
            cur_c = cc
            cur_d = dd

        # store version data
        root_slot[ver] = cur_root_slot
        root_map[ver] = cur_root_map
        p[ver] = cur_p
        c[ver] = cur_c
        d[ver] = cur_d

    return "\n".join(out_lines)

if __name__ == "__main__":
    sys.stdout.write(solve(sys.stdin.read()))
