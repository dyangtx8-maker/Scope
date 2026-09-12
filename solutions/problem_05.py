# problem_id: 4e49a099fd84fad956c856b0bb613db05b87316550730c4ac1329a2f26775465
# source_language: rust
# phase: 2
# entrypoint: solve
# verified: True

def solve(stdin: str) -> str:
    import sys

    # ---------- helpers ----------
    def is_control(cp: int) -> bool:
        return (0x0000 <= cp <= 0x001F) or (0x007F <= cp <= 0x009F)

    ATTACH_RANGES = [
        (0x0300, 0x036F),
        (0x1AB0, 0x1AFF),
        (0x1DC0, 0x1DFF),
        (0x20D0, 0x20FF),
        (0xFE00, 0xFE0F),
        (0xFE20, 0xFE2F),
        (0x1F3FB, 0x1F3FF),
        (0xE0100, 0xE01EF),
    ]

    def is_attachment(cp: int) -> bool:
        for lo, hi in ATTACH_RANGES:
            if lo <= cp <= hi:
                return True
        return False

    EMOJI_RANGES = [
        (0x2600, 0x27BF),
        (0x1F000, 0x1FAFF),
    ]

    def is_emoji(cp: int) -> bool:
        for lo, hi in EMOJI_RANGES:
            if lo <= cp <= hi:
                return True
        return False

    def is_regional(cp: int) -> bool:
        return 0x1F1E6 <= cp <= 0x1F1FF

    ZWJ = 0x200D

    # ---------- parse input ----------
    parts = stdin.split()
    if not parts:
        return ""
    S = parts[0]
    Q = int(parts[1])
    nums = list(map(int, parts[2:]))

    # ---------- Unicode scalar list ----------
    cps = [ord(ch) for ch in S]
    n = len(cps)

    # ---------- pre‑computations ----------
    # previous non‑attachment index
    prev_non_attach = [-1] * n
    # run length of consecutive Regional Indicators
    ri_run_len = [0] * n

    for i, cp in enumerate(cps):
        if not is_attachment(cp):
            prev_non_attach[i] = i
        else:
            prev_non_attach[i] = prev_non_attach[i - 1] if i else -1

        if is_regional(cp):
            ri_run_len[i] = (ri_run_len[i - 1] if i else 0) + 1
        else:
            ri_run_len[i] = 0

    # ---------- grapheme segmentation ----------
    starts = [0]  # scalar index where each grapheme starts
    for i in range(n - 1):
        left = cps[i]
        right = cps[i + 1]

        # rule 1
        if left == 0x0D and right == 0x0A:
            boundary = False
        # rule 2
        elif is_control(left) or is_control(right):
            boundary = True
        # rule 3
        elif is_attachment(right) or right == ZWJ:
            boundary = False
        # rule 4
        elif left == ZWJ and is_emoji(right):
            k = prev_non_attach[i - 1] if i else -1
            if k != -1 and is_emoji(cps[k]):
                boundary = False
            else:
                # fall through to rule 5 / 6
                if is_regional(left) and is_regional(right):
                    cnt = ri_run_len[i]
                    boundary = (cnt % 2 == 0)
                else:
                    boundary = True
        # rule 5
        elif is_regional(left) and is_regional(right):
            cnt = ri_run_len[i]
            boundary = (cnt % 2 == 0)
        # rule 6
        else:
            boundary = True

        if boundary:
            starts.append(i + 1)

    G = len(starts)                     # number of graphemes
    boundaries = starts + [n]           # scalar index for each boundary (0..G)
    # ---------- UTF‑16 prefix ----------
    utf16_len = [1 if cp <= 0xFFFF else 2 for cp in cps]
    pref_utf16 = [0] * (n + 1)
    for i, l in enumerate(utf16_len, 1):
        pref_utf16[i] = pref_utf16[i - 1] + l
    offset_utf16 = [pref_utf16[pos] for pos in boundaries]  # length G+1

    # ---------- grapheme byte sequences ----------
    # slice the original string by scalar indices, encode to UTF‑8
    grapheme_bytes = []
    for g in range(G):
        start = starts[g]
        end = starts[g + 1] if g + 1 < G else n
        grapheme_bytes.append(S[start:end].encode('utf-8'))

    # ---------- rolling hash preparation ----------
    MOD1 = (1 << 61) - 1
    MOD2 = (1 << 61) - 1  # use same modulus with different base
    BASE0 = 91138233          # for hashing a single grapheme's bytes
    BASE_SEQ1 = 97266353
    BASE_SEQ2 = 1000003

    def mul_mod(a, b, mod):
        return (a * b) % mod

    # token hashes
    token_h1 = []
    token_h2 = []
    for b in grapheme_bytes:
        h1 = 0
        h2 = 0
        for v in b:
            h1 = (h1 * BASE0 + v) % MOD1
            h2 = (h2 * BASE0 + v) % MOD2
        token_h1.append(h1)
        token_h2.append(h2)

    # prefix hashes for the sequence of graphemes
    pref1 = [0] * (G + 1)
    pref2 = [0] * (G + 1)
    pow1 = [1] * (G + 1)
    pow2 = [1] * (G + 1)
    for i in range(G):
        pref1[i + 1] = (pref1[i] * BASE_SEQ1 + token_h1[i]) % MOD1
        pref2[i + 1] = (pref2[i] * BASE_SEQ2 + token_h2[i]) % MOD2
        pow1[i + 1] = (pow1[i] * BASE_SEQ1) % MOD1
        pow2[i + 1] = (pow2[i] * BASE_SEQ2) % MOD2

    def range_hash(l: int, r: int):
        """return pair of hashes for grapheme range [l, r)"""
        length = r - l
        h1 = (pref1[r] - pref1[l] * pow1[length]) % MOD1
        h2 = (pref2[r] - pref2[l] * pow2[length]) % MOD2
        return h1, h2

    # ---------- query processing ----------
    out_lines = []
    it = iter(nums)
    for _ in range(Q):
        typ = next(it)
        a = next(it)
        b = next(it)

        # resolve indices
        def resolve(x: int) -> int:
            if x >= 0:
                i = x
            else:
                i = G + x
            if i < 0:
                i = 0
            elif i > G:
                i = G
            return i

        i = resolve(a)
        j = resolve(b)

        if typ == 1:
            if j >= i:
                out_lines.append(f"{offset_utf16[i]} {offset_utf16[j]}")
            else:
                out_lines.append(f"{offset_utf16[i]} {offset_utf16[i]}")
        else:  # typ == 2
            max_len = min(G - i, G - j)
            lo, hi = 0, max_len
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if range_hash(i, i + mid) == range_hash(j, j + mid):
                    lo = mid
                else:
                    hi = mid - 1
            L = lo
            # UTF‑16 length of the substring
            start_pos = boundaries[i]
            end_pos = boundaries[i + L]  # works because boundaries has length G+1
            utf16_sub = pref_utf16[end_pos] - pref_utf16[start_pos]
            out_lines.append(f"{L} {utf16_sub}")

    return "\n".join(out_lines)
