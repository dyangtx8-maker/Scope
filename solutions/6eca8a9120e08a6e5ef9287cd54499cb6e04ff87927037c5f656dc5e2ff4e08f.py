# problem_id: 6eca8a9120e08a6e5ef9287cd54499cb6e04ff87927037c5f656dc5e2ff4e08f
# source_language: python
# phase: 3
# entrypoint: capture_binders
# verified: True

def capture_binders(nodes, expr_root, target, replacement_root):
    """
    nodes: list of term records as described.
    expr_root, replacement_root: indices into nodes (expr_root) and separate tree (replacement_root)
    target: string name to be replaced
    Returns list of declaration IDs that may capture the replacement.
    """

    # ---------- 1. compute free names in the replacement tree ----------
    # replacement tree is separate from expression graph; we can treat it similarly.
    # We'll traverse it with a stack of bound names.
    free_names = set()

    # Build adjacency for replacement (children indices are within same list? assume same nodes list)
    # The problem states replacement-rooted term is a tree, distinct from expression graph.
    # We'll just traverse using the same nodes list; the roots are disjoint.
    stack = [(replacement_root, [])]  # (node, list of (name, id) bindings currently active)
    while stack:
        idx, bound = stack.pop()
        node = nodes[idx]
        kind = node[0]

        if kind == "var":
            name = node[1]
            if not any(b[0] == name for b in bound):
                free_names.add(name)

        elif kind == "call":
            for child in node[1]:
                stack.append((child, bound))

        elif kind == "bind":
            decls, body = node[1], node[2]
            new_bound = bound + [(d[1], d[0]) for d in decls]  # (name, id)
            stack.append((body, new_bound))

        elif kind == "let":
            decl, value, body = node[1], node[2], node[3]
            # value sees current bound, body sees added decl
            stack.append((value, bound))
            stack.append((body, bound + [(decl[1], decl[0])]))

        elif kind == "match":
            scrut, shared, arms = node[1], node[2], node[3]
            # scrutinee outside shared
            stack.append((scrut, bound))
            # each arm: shared + arm-specific bindings
            for arm in arms:
                arm_kind = arm[0]
                if arm_kind == "ctor":
                    fields, auxiliaries, body = arm[1], arm[2], arm[3]
                    arm_bound = bound + [(d[1], d[0]) for d in shared] + [(f[1], f[0]) for f in fields]
                elif arm_kind == "zero":
                    auxiliaries, body = arm[1], arm[2]
                    arm_bound = bound + [(d[1], d[0]) for d in shared]
                elif arm_kind == "succ":
                    pred, auxiliaries, body = arm[1], arm[2], arm[3]
                    arm_bound = bound + [(d[1], d[0]) for d in shared] + [(pred[1], pred[0])]
                else:
                    continue
                # auxiliaries are outside shared, so just traverse them with current bound
                for aux in auxiliaries:
                    stack.append((aux, bound))
                stack.append((body, arm_bound))
        # other kinds not present

    # ---------- 2. traverse expression graph, looking for replaceable target vars ----------
    result_ids = set()

    # We'll perform an explicit DFS that carries the current stack of active declarations.
    # Each stack entry: (name, id)
    # To avoid recursion depth issues, use a list as our own stack of frames.
    # Frame: (node_idx, state, decls_to_pop, iterator)
    # state 0 = just entered node, need to handle based on kind.
    # For nodes with multiple children we store iterator index.

    # Helper to push a new frame
    def push_frame(idx, decls_to_pop, children):
        # children is list of child indices to process after current
        stack_frames.append([idx, 0, decls_to_pop, children, None])

    stack_frames = []
    # start with expr_root, no declarations to pop, children empty (will be set in processing)
    push_frame(expr_root, [], [])

    while stack_frames:
        frame = stack_frames[-1]
        idx, state, decls_to_pop, children, extra = frame

        node = nodes[idx]
        kind = node[0]

        if state == 0:
            # entering node
            if kind == "var":
                name = node[1]
                # check if target and not bound
                if name == target and not any(d[0] == target for d in decls_to_pop):
                    # capture: any declaration on current stack whose name is free in replacement
                    for dname, did in decls_to_pop:
                        if dname in free_names:
                            result_ids.add(did)
                # nothing to traverse further
                stack_frames.pop()
                continue

            elif kind == "call":
                # children use same stack
                child_idxs = node[1]
                # push children in reverse order for correct processing
                for c in reversed(child_idxs):
                    push_frame(c, decls_to_pop.copy(), [])
                frame[1] = 1  # mark processed
                continue

            elif kind == "bind":
                decls, body = node[1], node[2]
                # push declarations onto stack
                new_stack = decls_to_pop + [(d[1], d[0]) for d in decls]
                # after body, need to pop them (just discard new_stack)
                push_frame(body, new_stack, [])
                frame[1] = 1
                continue

            elif kind == "let":
                decl, value, body = node[1], node[2], node[3]
                # value first with current stack
                push_frame(value, decls_to_pop.copy(), [])
                # then body with added decl
                push_frame(body, decls_to_pop + [(decl[1], decl[0])], [])
                frame[1] = 1
                continue

            elif kind == "match":
                scrut, shared, arms = node[1], node[2], node[3]
                # process scrutinee first
                push_frame(scrut, decls_to_pop.copy(), [])
                # then each arm
                for arm in reversed(arms):
                    arm_kind = arm[0]
                    if arm_kind == "ctor":
                        fields, auxiliaries, body = arm[1], arm[2], arm[3]
                        arm_stack = decls_to_pop + [(d[1], d[0]) for d in shared] + [(f[1], f[0]) for f in fields]
                    elif arm_kind == "zero":
                        auxiliaries, body = arm[1], arm[2]
                        arm_stack = decls_to_pop + [(d[1], d[0]) for d in shared]
                    elif arm_kind == "succ":
                        pred, auxiliaries, body = arm[1], arm[2], arm[3]
                        arm_stack = decls_to_pop + [(d[1], d[0]) for d in shared] + [(pred[1], pred[0])]
                    else:
                        continue
                    # auxiliaries are outside shared
                    for aux in auxiliaries:
                        push_frame(aux, decls_to_pop.copy(), [])
                    push_frame(body, arm_stack, [])
                frame[1] = 1
                continue

            else:
                # unknown kind, just pop
                stack_frames.pop()
                continue

        else:
            # finished processing children of this node
            stack_frames.pop()
            continue

    return sorted(result_ids)
