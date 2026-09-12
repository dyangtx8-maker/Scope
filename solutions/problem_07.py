# problem_id: 5ce30ef9e5cf1df58aaba388f1c3b0ea9fd24d1eb843d96bb1c6c5fd21396bc2
# source_language: rust
# phase: 2
# entrypoint: solve
# verified: True

def solve(stdin: str) -> str:
    """
    Simulates a simple activation‑based patch system.

    The input format (as used in the original problem) is:
        N K Q
        K lines: obj attr kind value
        Q commands (START, STOP, STOPALL, GET, STACK)

    The function returns the output produced by GET and STACK commands,
    each on its own line.  If the input is empty, an empty string is returned.
    """
    # Guard against completely empty input (the original failure case)
    if not stdin.strip():
        return ""

    data = stdin.split()
    it = iter(data)

    # Basic parameters (they are not used directly but must be consumed)
    N = int(next(it))          # number of objects (unused)
    K = int(next(it))          # number of initially defined cells
    Q = int(next(it))          # number of commands

    # Cell map: (object_id, attribute_id) -> (kind, value)
    #   kind == 'I' → integer value
    #   kind == 'O' → reference to another object (object id)
    cells: dict[tuple[int, int], tuple[str, int]] = {}

    for _ in range(K):
        obj = int(next(it))
        attr = int(next(it))
        kind = next(it)        # 'I' or 'O'
        val = int(next(it))
        cells[(obj, attr)] = (kind, val)

    # Activation handling
    stack: list[int] = []                     # active activation ids, oldest → newest
    activations: dict[int, dict] = {}         # id → {'writes': [(key, old_value), ...]}

    out_lines: list[str] = []

    def resolve_path(root: int, attrs: list[int]) -> tuple[tuple[int, int], tuple[str, int]]:
        """
        Walks a path of attributes starting from `root` and returns the key of the
        final cell together with its current stored value.
        """
        cur = root
        # Follow all but the last attribute – they must point to objects
        for a in attrs[:-1]:
            kind, val = cells[(cur, a)]
            # According to the problem statement the intermediate cells are always object references
            cur = val
        last = attrs[-1]
        key = (cur, last)
        return key, cells[key]

    for _ in range(Q):
        cmd = next(it)

        if cmd == 'START':
            act_id = int(next(it))
            m = int(next(it))                     # number of replacements in this activation
            writes: list[tuple[tuple[int, int], tuple[str, int]]] = []

            for _ in range(m):
                root = int(next(it))
                l = int(next(it))
                attrs = [int(next(it)) for _ in range(l)]
                kind = next(it)                  # 'I' or 'O'
                val = int(next(it))

                key, old_val = resolve_path(root, attrs)
                writes.append((key, old_val))
                cells[key] = (kind, val)

            activations[act_id] = {'writes': writes}
            stack.append(act_id)

        elif cmd == 'STOP':
            act_id = int(next(it))
            if act_id in activations:
                # Restore in reverse order of the writes performed by this activation
                for key, old_val in reversed(activations[act_id]['writes']):
                    cells[key] = old_val
                # Remove from the active stack (linear removal is acceptable for the given limits)
                stack.remove(act_id)
                del activations[act_id]

        elif cmd == 'STOPALL':
            # Undo activations from newest to oldest
            while stack:
                act_id = stack.pop()
                for key, old_val in reversed(activations[act_id]['writes']):
                    cells[key] = old_val
                del activations[act_id]

        elif cmd == 'GET':
            root = int(next(it))
            l = int(next(it))
            attrs = [int(next(it)) for _ in range(l)]
            _, val = resolve_path(root, attrs)
            out_lines.append(f"{val[0]} {val[1]}")

        elif cmd == 'STACK':
            line = [str(len(stack))] + [str(x) for x in stack]
            out_lines.append(' '.join(line))

        else:
            # Unexpected command – ignore (should not happen with valid input)
            pass

    return '\n'.join(out_lines)
