# problem_id: 2beff58fa923cf9e2df2b235a24c42021d9c9553f19fcfd01e46c7fae4987efb
# source_language: python
# phase: 2
# entrypoint: refresh_references
# verified: True

from typing import List, Dict, Any, Tuple, Optional

def refresh_references(
    initial: List[Dict[str, Any]],
    snapshots: List[List[Dict[str, Any]]],
    references: List[Dict[str, Any]],
) -> List[Tuple[str, str, Optional[str], Optional[str]]]:
    """
    Implements the reference refresh logic described in the prompt.
    """

    # ---------- helpers ----------
    def parse_name(text: str) -> Tuple[Optional[str], Optional[int], bool]:
        """
        Returns (base_name, version, is_type). If parsing fails, returns (None, None, _).
        """
        if not text:
            return None, None, False

        parts = text.split(".")
        terminal = parts[-1]

        # check terminal suffix for function/value
        if terminal and terminal[0].islower():
            # possible @version suffix
            if "@" in terminal:
                name, ver = terminal.split("@", 1)
                if not ver.isdigit() or (ver != "0" and ver.startswith("0")):
                    return None, None, False
                version = int(ver)
                terminal = name
            else:
                version = None
            is_type = False
        elif terminal and terminal[0].isupper():
            # type terminal, no suffix allowed
            if "@" in terminal:
                return None, None, False
            version = None
            is_type = True
        else:
            return None, None, False

        # validate identifiers
        for ident in parts[:-1] + [terminal]:
            if not ident or not all(c.isalnum() or c == "_" for c in ident):
                return None, None, False
            if ident[0].isdigit():
                return None, None, False

        base_name = ".".join(parts[:-1] + [terminal])
        return base_name, version, is_type

    def make_key(namespace: str, name: str, version: Optional[int]) -> Tuple[str, str, Optional[int]]:
        return (namespace, name, version)

    # ---------- build cumulative snapshots ----------
    # snapshots_cum[i] = dict mapping (namespace, name, version) -> hash after applying snapshots[0..i-1]
    # i.e., snapshot 0 is initial state
    cum_maps: List[Dict[Tuple[str, str, Optional[int]], str]] = []
    cur: Dict[Tuple[str, str, Optional[int]], str] = {}
    for entry in initial:
        cur[make_key(entry["namespace"], entry["name"], entry.get("version"))] = entry["hash"]
    cum_maps.append(cur.copy())
    for batch in snapshots:
        for upd in batch:
            key = make_key(upd["namespace"], upd["name"], upd.get("version"))
            if upd["hash"] is None:
                cur.pop(key, None)
            else:
                cur[key] = upd["hash"]
        cum_maps.append(cur.copy())

    # ---------- process each reference ----------
    results: List[Tuple[str, str, Optional[str], Optional[str]]] = []

    for ref in references:
        orig_ns: str = ref["namespace"]
        text: str = ref["text"]
        context: str = ref["context"]
        location: Optional[str] = ref["location"]
        stored_hash: Optional[str] = ref["hash"]
        start: int = ref["start"]

        # syntax validation
        base_name, version, is_type = parse_name(text)
        if base_name is None:
            results.append(("invalid", orig_ns, location, stored_hash))
            continue

        # location‑authoritative case
        if location is not None:
            # the reference never moves; just look up its exact key each snapshot
            key = make_key(orig_ns, location, version)
            cur_hash = stored_hash
            for snap_idx in range(start, len(cum_maps)):
                snap = cum_maps[snap_idx]
                if key in snap:
                    cur_hash = snap[key]
            results.append(("located", orig_ns, location, cur_hash))
            continue

        # ----- generate candidate names -----
        # context prefixes: longest to shortest, plus empty
        ctx_parts = context.split(".") if context else []
        prefixes = [".".join(ctx_parts[:i]) for i in range(len(ctx_parts), 0, -1)]
        prefixes.append("")  # empty prefix

        candidates: List[Tuple[str, str, Optional[int]]] = []  # (namespace, name, version)

        if is_type:
            # only type namespace, version always None
            for pre in prefixes:
                name = f"{pre}.{base_name}" if pre else base_name
                candidates.append(("type", name, None))
        else:
            # value references: first value candidates, then function candidates
            for pre in prefixes:
                name = f"{pre}.{base_name}" if pre else base_name
                candidates.append(("value", name, version))
            for pre in prefixes:
                name = f"{pre}.{base_name}" if pre else base_name
                candidates.append(("function", name, version))

        # ----- scan snapshots -----
        found = False
        final_ns = orig_ns
        final_loc: Optional[str] = None
        final_hash: Optional[str] = None

        for snap_idx in range(start, len(cum_maps)):
            snap = cum_maps[snap_idx]
            for ns, name, ver in candidates:
                key = make_key(ns, name, ver)
                if key in snap:
                    final_ns = ns
                    final_loc = name
                    final_hash = snap[key]
                    found = True
                    break
            if found:
                break

        if found:
            results.append(("located", final_ns, final_loc, final_hash))
        else:
            results.append(("missing", orig_ns, None, None))

    return results
