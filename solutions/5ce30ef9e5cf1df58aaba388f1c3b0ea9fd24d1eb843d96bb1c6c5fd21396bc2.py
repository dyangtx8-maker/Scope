# problem_id: 5ce30ef9e5cf1df58aaba388f1c3b0ea9fd24d1eb843d96bb1c6c5fd21396bc2
# source_language: rust
# phase: 3
# entrypoint: solve
# verified: True

def solve(stdin: str) -> str:
    """
    Simulates a simple activation system.

    Input format (all numbers):
        Q
        cmd_1
        cmd_2
        ...
        cmd_Q

    Commands:
        1 x v   – start activation of variable `x` with value `v`.
                  The previous value (if any) is saved and can be restored.
        2 x     – get the current value of variable `x`.  Output 0 if it has
                  never been started.
        3 x     – stop the most recent activation of `x`, restoring the
                  previous value (if any).  If there is no activation, do
                  nothing.
        4 x     – stop all activations of `x`, leaving it with no value
                  (subsequent `get` will output 0).

    The function returns the concatenated outputs of all `get` commands,
    each on its own line.
    """
    it = iter(stdin.strip().splitlines())
    try:
        q = int(next(it))
    except StopIteration:
        return ""

    # stacks[x] holds the activation history for variable x (list of ints)
    stacks: dict[int, list[int]] = {}
    out_lines = []

    for _ in range(q):
        parts = next(it).split()
        if not parts:
            continue
        cmd = int(parts[0])

        if cmd == 1:                     # start x v
            _, x_str, v_str = parts
            x = int(x_str)
            v = int(v_str)
            stacks.setdefault(x, []).append(v)

        elif cmd == 2:                   # get x
            _, x_str = parts
            x = int(x_str)
            if x in stacks and stacks[x]:
                out_lines.append(str(stacks[x][-1]))
            else:
                out_lines.append("0")

        elif cmd == 3:                   # stop x
            _, x_str = parts
            x = int(x_str)
            if x in stacks and stacks[x]:
                stacks[x].pop()
                if not stacks[x]:
                    # keep the key with empty list for simplicity
                    pass

        elif cmd == 4:                   # stopall x
            _, x_str = parts
            x = int(x_str)
            stacks.pop(x, None)

        else:
            # Unknown command – ignore (problem guarantees valid input)
            pass

    return "\n".join(out_lines)
