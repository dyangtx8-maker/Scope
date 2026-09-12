# problem_id: 1ba0d34fae43f1d26c14f3091598291f0ca9d6ab3f88232d15a3322c017042c3
# source_language: python
# phase: 3
# entrypoint: simulate_writes
# verified: True

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
    Simulate the write worker.

    Parameters
    ----------
    lengths : list[int]
        Packet lengths.
    outcomes : list[tuple[str, int]]
        Run‑length encoded outcomes. Each tuple is (kind, count) where
        kind ∈ {"ok", "full", "error"}.
    max_retries : int
        Maximum retries allowed for a single packet.
    level_cap : int
        Upper bound for the congestion level (0 … level_cap).
    spin_limit : int
        Upper bound for the spin cost of a full outcome.
    initial_credits : int
        Starting credit balance.
    credit_cap : int
        Maximum credit balance.

    Returns
    -------
    dict
        {
            "packets": [(status, retries, spins, yields), ...],
            "telemetry": {
                "sent": int,
                "dropped": int,
                "errors": int,
                "invalid": int,
                "attempts": int,
                "spins": int,
                "yields": int,
                "final_level": int,
                "final_credits": int,
            },
        }
    """
    # ------------------------------------------------------------------ #
    # Helper to walk through the run‑length encoded outcome list.
    # ------------------------------------------------------------------ #
    runs = list(outcomes)                     # [(kind, count), ...]
    run_idx = 0                               # index in runs
    run_rem = runs[0][1] if runs else 0       # remaining count in current run

    def current_kind():
        """Kind of the current outcome without consuming it."""
        if run_idx >= len(runs):
            return None
        return runs[run_idx][0]

    def remaining_in_current_run():
        """How many outcomes of the current kind are left."""
        if run_idx >= len(runs):
            return 0
        return run_rem

    def consume(k):
        """Consume k outcomes from the run‑length list."""
        nonlocal run_idx, run_rem
        while k > 0 and run_idx < len(runs):
            if k < run_rem:
                run_rem -= k
                k = 0
            else:
                k -= run_rem
                run_idx += 1
                if run_idx < len(runs):
                    run_rem = runs[run_idx][1]

    def get_next():
        """Consume and return the next outcome kind (or implicit 'error')."""
        kind = current_kind()
        if kind is None:
            return "error"
        consume(1)
        return kind

    # ------------------------------------------------------------------ #
    # Batch processing of consecutive "full" outcomes.
    # ------------------------------------------------------------------ #
    def batch_full(
        level,
        retries_done,
        credits,
        max_retries,
        remaining_fulls,
        level_cap,
        spin_limit,
    ):
        """
        Process as many consecutive 'full' outcomes as possible while
        scheduling retries. Returns:
            (processed, new_level, new_credits, added_spins, added_yields)
        where `processed` is the number of full outcomes that resulted in a
        scheduled retry.
        """
        processed = 0
        added_spins = 0
        added_yields = 0
        cur_level = level
        cur_credits = credits

        # Stepwise until we hit the level cap (at most level_cap+1 steps).
        while (
            processed < remaining_fulls
            and retries_done + processed < max_retries
        ):
            old = cur_level
            cost = min(1 << old, spin_limit)
            if cur_credits < cost:
                break
            # schedule retry
            cur_credits -= cost
            added_spins += cost
            if (1 << old) > spin_limit:
                added_yields += 1
            # raise level (capped)
            cur_level = min(cur_level + 1, level_cap)
            processed += 1
            if cur_level == level_cap:
                break

        # If we are at the cap, the cost stabilises – we can bulk‑process.
        if (
            processed < remaining_fulls
            and retries_done + processed < max_retries
            and cur_level == level_cap
        ):
            cost_cap = min(1 << level_cap, spin_limit)
            max_by_credits = cur_credits // cost_cap
            max_by_retries = max_retries - (retries_done + processed)
            bulk = min(
                remaining_fulls - processed,
                max_by_credits,
                max_by_retries,
            )
            if bulk:
                added_spins += bulk * cost_cap
                if (1 << level_cap) > spin_limit:
                    added_yields += bulk
                cur_credits -= bulk * cost_cap
                processed += bulk
                # level stays at the cap

        return processed, cur_level, cur_credits, added_spins, added_yields

    # ------------------------------------------------------------------ #
    # Main simulation.
    # ------------------------------------------------------------------ #
    packets_res = []
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

    level = 0
    credits = initial_credits

    for length in lengths:
        # ----- invalid packet -------------------------------------------------
        if not (1 <= length <= 65535):
            packets_res.append(("INVALID", 0, 0, 0))
            telemetry["invalid"] += 1
            continue

        retries = 0
        pkt_spins = 0
        pkt_yields = 0
        status = None

        while True:
            kind = current_kind()
            # --------------------------------------------------------------- #
            # Non‑full outcomes are handled one‑by‑one.
            # --------------------------------------------------------------- #
            if kind != "full":
                outcome = get_next()
                telemetry["attempts"] += 1

                if outcome == "ok":
                    level = max(level - 1, 0)
                    credits = min(credits + length, credit_cap)
                    status = "SENT"
                    telemetry["sent"] += 1
                    break

                # outcome is "error" (explicit or implicit)
                level = 0
                status = "ERROR"
                telemetry["errors"] += 1
                break

            # --------------------------------------------------------------- #
            # Consecutive "full" outcomes – try to schedule as many retries as
            # the rules allow.
            # --------------------------------------------------------------- #
            rem_fulls = remaining_in_current_run()
            processed, new_level, new_credits, add_spins, add_yields = batch_full(
                level,
                retries,
                credits,
                max_retries,
                rem_fulls,
                level_cap,
                spin_limit,
            )

            if processed:
                # consume the full outcomes that led to successful retries
                consume(processed)
                telemetry["attempts"] += processed
                retries += processed
                pkt_spins += add_spins
                pkt_yields += add_yields
                level = new_level
                credits = new_credits

            # If we stopped before exhausting the run, the next "full"
            # cannot schedule a retry → packet is dropped.
            if processed < rem_fulls:
                # consume the final full that causes the drop
                consume(1)
                telemetry["attempts"] += 1
                status = "DROPPED"
                telemetry["dropped"] += 1
                break
            # otherwise we consumed the whole run of "full"s; loop again to
            # look at the next outcome kind.
        # ------------------------------------------------------------------ #
        # Record per‑packet data.
        # ------------------------------------------------------------------ #
        packets_res.append((status, retries, pkt_spins, pkt_yields))
        telemetry["spins"] += pkt_spins
        telemetry["yields"] += pkt_yields

    telemetry["final_level"] = level
    telemetry["final_credits"] = credits

    return {"packets": packets_res, "telemetry": telemetry}
