# problem_id: 5d02bd0e16ab63c540232452b60e8e6cf36d01e0820cb6b422b63c1a31091718
# source_language: rust
# phase: 3
# entrypoint: solve
# verified: True

def solve(stdin: str) -> str:
    import sys
    sys.setrecursionlimit(1_000_000)

    data = stdin.strip().split()
    if not data:
        return ""
    it = iter(data)
    N = int(next(it))
    Q = int(next(it))

    # Build version tree
    parent = [0] * (Q + 1)          # version 0 has no parent
    op_type = [None] * (Q + 1)
    op_args = [None] * (Q + 1)

    for i in range(1, Q + 1):
        typ = next(it)
        b = int(next(it))
        parent[i] = b
        if typ == 'R':
            s = int(next(it))
            op_type[i] = 'R'
            op_args[i] = (s,)
        elif typ == 'W':
            s = int(next(it))
            sub = next(it)
            if sub == 'E':
                op_type[i] = 'W'
                op_args[i] = (s, None)   # completion
            else:
                # sub is actually the value
                x = int(sub)
                op_type[i] = 'W'
                op_args[i] = (s, x)
        elif typ == 'D':
            k = int(next(it))
            op_type[i] = 'D'
            op_args[i] = (k,)
        elif typ == 'P':
            op_type[i] = 'P'
            op_args[i] = ()
        else:
            raise ValueError('unknown op')

    # Build adjacency list for DFS ordering
    children = [[] for _ in range(Q + 1)]
    for v in range(1, Q + 1):
        children[parent[v]].append(v)

    tin = [0] * (Q + 1)
    tout = [0] * (Q + 1)
    order = []
    def dfs(v: int):
        tin[v] = len(order)
        order.append(v)
        for c in children[v]:
            dfs(c)
        tout[v] = len(order) - 1
    dfs(0)

    # Helper to test ancestor relation
    def is_ancestor(anc: int, desc: int) -> bool:
        return tin[anc] <= tin[desc] <= tout[anc]

    # For each slot store (publish_version, is_value, value)
    slot_pub = [None] * N   # None if unpublished yet

    # State per version (p, c, d)
    p = [0] * (Q + 1)
    c = [0] * (Q + 1)
    d = [0] * (Q + 1)

    # To avoid O(N) copy per version, we will compute states lazily while traversing the tree.
    # We'll perform a DFS over the version tree, maintaining a mutable current state and a stack
    # of changes to revert when backtracking.

    out_tokens = []

    MOD = 1_000_000_007
    MUL = 911382323

    # Stack to revert slot publications
    slot_stack = []   # entries: (slot_index, previous_slot_pub)

    # Stack to revert state changes
    state_stack = []  # entries: (attr, version, old_value)

    def push_state(attr, ver, old):
        state_stack.append((attr, ver, old))

    def set_state(attr, ver, val):
        if attr == 'p':
            push_state(attr, ver, p[ver])
            p[ver] = val
        elif attr == 'c':
            push_state(attr, ver, c[ver])
            c[ver] = val
        elif attr == 'd':
            push_state(attr, ver, d[ver])
            d[ver] = val

    def revert_state(to_len):
        while len(state_stack) > to_len:
            attr, ver, old = state_stack.pop()
            if attr == 'p':
                p[ver] = old
            elif attr == 'c':
                c[ver] = old
            elif attr == 'd':
                d[ver] = old

    def revert_slots(to_len):
        while len(slot_stack) > to_len:
            idx, old = slot_stack.pop()
            slot_pub[idx] = old

    # Helper to get slot info visible in current version
    def slot_info(idx, cur_ver):
        info = slot_pub[idx]
        if info is None:
            return None
        pub_ver, is_val, val = info
        return info if is_ancestor(pub_ver, cur_ver) else None

    def dfs_version(v: int):
        # inherit state from parent
        if v != 0:
            p[v] = p[parent[v]]
            c[v] = c[parent[v]]
            d[v] = d[parent[v]]

        state_len = len(state_stack)
        slot_len = len(slot_stack)

        typ = op_type[v]
        if typ == 'R':
            s = op_args[v][0] - 1  # producer id not used further
            # reserve: assign ticket p, then p+=1
            set_state('p', v, p[v] + 1)
        elif typ == 'W':
            s, val = op_args[v]
            idx = s - 1
            # publish slot idx
            old = slot_pub[idx]
            slot_stack.append((idx, old))
            slot_pub[idx] = (v, val is not None, val if val is not None else 0)
        elif typ == 'D':
            k = op_args[v][0]
            set_state('d', v, d[v] + k)
        elif typ == 'P':
            # perform drain
            cur_c = c[v]
            cur_d = d[v]
            h = 0
            m = 0
            while cur_c < p[v]:
                info = slot_info(cur_c, v)
                if info is None:
                    # unpublished
                    break
                _, is_val, val = info
                if not is_val:
                    # completion
                    cur_c += 1
                    continue
                # value
                if cur_d == 0:
                    break
                # consume value
                cur_d -= 1
                cur_c += 1
                m += 1
                r = val % MOD
                if r < 0:
                    r += MOD
                h = (h * MUL + r) % MOD
            # update state
            set_state('c', v, cur_c)
            set_state('d', v, cur_d)

            # determine status
            if p[v] == c[v] == N:
                status = 'COMPLETE'
            elif c[v] == p[v] < N:
                status = 'WAITING'
            else:
                # c < p
                info = slot_info(c[v], v)
                if info is None:
                    status = 'BLOCKED'
                else:
                    _, is_val, _ = info
                    if is_val and d[v] == 0:
                        status = 'BACKPRESSURE'
                    else:
                        # should not happen
                        status = 'WAITING'
            out_tokens.extend(['DRAIN', str(m), str(h), status,
                               str(p[v]), str(c[v]), str(d[v])])

        # recurse
        for ch in children[v]:
            dfs_version(ch)

        # revert changes
        revert_state(state_len)
        revert_slots(slot_len)

    dfs_version(0)

    return ' '.join(out_tokens)
