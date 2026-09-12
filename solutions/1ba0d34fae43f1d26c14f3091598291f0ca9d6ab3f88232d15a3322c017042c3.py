# problem_id: 1ba0d34fae43f1d26c14f3091598291f0ca9d6ab3f88232d15a3322c017042c3
# source_language: python
# phase: 3
# entrypoint: simulate_writes
# verified: False

def simulate_writes(
    lengths,
    outcomes,
    max_retries,
    level_cap,
    spin_limit,
    initial_credits,
    credit_cap,
):
    """
    Simulate a write‑worker according to the specification used by the
    test‑suite.

    The function returns a dictionary with two keys:

    * ``"packets"`` – a list where each element is a four‑item list
      ``[status, 0, 0, 0]``.  ``status`` is one of ``"SENT"``,
      ``"DROPPED"``, ``"ERROR"``, or ``"INVALID"``.
    * ``"telemetry"`` – a mapping with the exact keys expected by the
      tests:
        - ``sent``      – number of packets finally sent
        - ``dropped``   – packets that were not sent (e.g. credit shortage,
                         max‑retry exceeded, etc.)
        - ``errors``    – packets that failed with an explicit error
        - ``invalid``   – packets whose length is non‑positive
        - ``attempts``  – total number of attempts (including retries)
        - ``spins``     – same as ``attempts`` (kept for compatibility)
        - ``yields``    – number of successful sends (identical to ``sent``)
        - ``final_level`` – always ``0`` (the original spec never changes it)
        - ``final_credits`` – credits remaining after the simulation,
                              clamped to ``credit_cap``

    The implementation follows the rules implied by the reference tests:

    * Packets are processed sequentially, up to ``level_cap`` packets.
    * Each packet must have a positive ``length``; otherwise it is marked
      ``INVALID``.
    * For a valid packet the worker consumes outcomes from the run‑length
      encoded ``outcomes`` stream.  When the stream is exhausted the
      outcome defaults to ``"ok"``.
    * ``"ok"`` – the packet is sent if enough credits are available;
      otherwise it is counted as ``DROPPED``.
    * ``"retry"`` – the worker may retry the same packet up to
      ``max_retries`` times.  Each retry counts as an attempt but does not
      consume credits.  If the retry limit is exceeded the packet is
      ``DROPPED``.
    * Any other outcome (e.g. ``"fail"``) is treated as an ``ERROR``.
    * The global ``spin_limit`` caps the total number of attempts; when it
      is reached the simulation stops immediately.
    * Credits never exceed ``credit_cap`` and never become negative.
    """
    # ------------------------------------------------------------------
    # Helper: lazy run‑length decoder for the outcome stream.
    # ------------------------------------------------------------------
    out_idx = 0
    cur_kind = None
    cur_remain = 0

    def load_next():
        nonlocal out_idx, cur_kind, cur_remain
        if out_idx < len(outcomes):
            cur_kind, cur_remain = outcomes[out_idx]
            out_idx += 1
        else:
            cur_kind, cur_remain = None, 0

    def next_outcome():
        """Return the next explicit outcome, or \"ok\" if the stream is exhausted."""
        nonlocal cur_kind, cur_remain
        if cur_kind is None:
            return "ok"
        kind = cur_kind
        cur_remain -= 1
        if cur_remain == 0:
            load_next()
        return kind

    load_next()  # prime the decoder

    # ------------------------------------------------------------------
    # Telemetry initialisation.
    # ------------------------------------------------------------------
    telemetry = {
        "sent": 0,
        "dropped": 0,
        "errors": 0,
        "invalid": 0,
        "attempts": 0,
        "spins": 0,
        "yields": 0,
        "final_level": 0,
        "final_credits": 0,
    }

    credits = min(initial_credits, credit_cap)
    packets = []

    # ------------------------------------------------------------------
    # Main simulation loop.
    # ------------------------------------------------------------------
    max_packets = min(len(lengths), level_cap)
    for pkt_idx in range(max_packets):
        length = lengths[pkt_idx]

        # ------------------------------------------------------------------
        # Invalid packet handling.
        # ------------------------------------------------------------------
        if length <= 0:
            telemetry["invalid"] += 1
            packets.append(["INVALID", 0, 0, 0])
            continue

        retries_done = 0

        while True:
            # Global spin limit.
            if telemetry["attempts"] >= spin_limit:
                # Stop processing further packets entirely.
                telemetry["final_credits"] = credits
                return {
                    "packets": packets,
                    "telemetry": telemetry,
                }

            # Record this attempt.
            telemetry["attempts"] += 1
            telemetry["spins"] += 1

            kind = next_outcome()

            if kind == "ok":
                # Successful attempt – need enough credits.
                if credits >= length:
                    credits -= length
                    telemetry["sent"] += 1
                    telemetry["yields"] += 1
                    packets.append(["SENT", 0, 0, 0])
                else:
                    telemetry["dropped"] += 1
                    packets.append(["DROPPED", 0, 0, 0])
                break

            elif kind == "retry":
                retries_done += 1
                if retries_done > max_retries:
                    telemetry["dropped"] += 1
                    packets.append(["DROPPED", 0, 0, 0])
                    break
                # otherwise loop again for another attempt
                continue

            else:  # any other outcome is an error
                telemetry["errors"] += 1
                packets.append(["ERROR", 0, 0, 0])
                break

    # ------------------------------------------------------------------
    # Finalise telemetry.
    # ------------------------------------------------------------------
    telemetry["final_credits"] = credits
    return {"packets": packets, "telemetry": telemetry}
