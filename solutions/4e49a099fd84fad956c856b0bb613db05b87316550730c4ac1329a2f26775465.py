# problem_id: 4e49a099fd84fad956c856b0bb613db05b87316550730c4ac1329a2f26775465
# source_language: rust
# phase: 3
# entrypoint: solve
# verified: True

def solve(stdin: str) -> str:
    import sys

    # Helper constants and functions for classification

    # Control: U+0000..U+001F and U+007F..U+009F
    def is_control(cp):
        return (0x0000 <= cp <= 0x001F) or (0x007F <= cp <= 0x009F)

    # Attachment ranges:
    # U+0300..U+036F, U+1AB0..U+1AFF, U+1DC0..U+1DFF,
    # U+20D0..U+20FF, U+FE00..U+FE0F, U+FE20..U+FE2F,
    # U+1F3FB..U+1F3FF, U+E0100..U+E01EF
    def is_attachment(cp):
        return (
            (0x0300 <= cp <= 0x036F) or
            (0x1AB0 <= cp <= 0x1AFF) or
            (0x1DC0 <= cp <= 0x1DFF) or
            (0x20D0 <= cp <= 0x20FF) or
            (0xFE00 <= cp <= 0xFE0F) or
            (0xFE20 <= cp <= 0xFE2F) or
            (0x1F3FB <= cp <= 0x1F3FF) or
            (0xE0100 <= cp <= 0xE01EF)
        )

    # Emoji ranges:
    # U+2600..U+27BF and U+1F000..U+1FAFF
    def is_emoji(cp):
        return (0x2600 <= cp <= 0x27BF) or (0x1F000 <= cp <= 0x1FAFF)

    # Regional Indicators: U+1F1E6..U+1F1FF
    def is_regional_indicator(cp):
        return 0x1F1E6 <= cp <= 0x1F1FF

    CR = 0x000D
    LF = 0x000A
    ZWJ = 0x200D

    tokens = stdin.split()
    if len(tokens) < 2:
        # No queries, return empty string
        return ''

    S_utf8 = tokens[0]
    Q = int(tokens[1])
    queries = tokens[2:]
    if len(queries) < 3 * Q:
        # Not enough queries, return empty string
        return ''

    # S_utf8 is the string S (already decoded)
    S = S_utf8
    scalars = [ord(c) for c in S]
    n = len(scalars)

    # Encode each scalar to UTF-8 bytes and track offsets
    utf8_bytes = []
    utf8_offsets = [0]
    utf16_units = []
    for cp in scalars:
        if cp <= 0x7F:
            b = bytes([cp])
        elif cp <= 0x7FF:
            b = bytes([
                0xC0 | (cp >> 6),
                0x80 | (cp & 0x3F)
            ])
        elif cp <= 0xFFFF:
            b = bytes([
                0xE0 | (cp >> 12),
                0x80 | ((cp >> 6) & 0x3F),
                0x80 | (cp & 0x3F)
            ])
        else:
            b = bytes([
                0xF0 | (cp >> 18),
                0x80 | ((cp >> 12) & 0x3F),
                0x80 | ((cp >> 6) & 0x3F),
                0x80 | (cp & 0x3F)
            ])
        utf8_bytes.append(b)
        utf8_offsets.append(utf8_offsets[-1] + len(b))
        utf16_units.append(1 if cp <= 0xFFFF else 2)

    # Precompute prev_non_attachment_idx for rule 4
    prev_non_attachment_idx = [-1] * n
    last_non_attachment = -1
    for i in range(n):
        if not is_attachment(scalars[i]):
            last_non_attachment = i
        prev_non_attachment_idx[i] = last_non_attachment

    # Precompute ri_run_len for rule 5
    ri_run_len = [0] * n
    for i in range(n):
        if is_regional_indicator(scalars[i]):
            ri_run_len[i] = 1 + (ri_run_len[i - 1] if i > 0 else 0)
        else:
            ri_run_len[i] = 0

    boundaries = [0]
    for i in range(n - 1):
        left = scalars[i]
        right = scalars[i + 1]

        # Rule 1: no boundary between CR and LF
        if left == CR and right == LF:
            # no boundary
            continue

        # Rule 2: boundary if either is control
        if is_control(left) or is_control(right):
            boundaries.append(i + 1)
            continue

        # Rule 3: no boundary if right is attachment or ZWJ
        if is_attachment(right) or right == ZWJ:
            continue

        # Rule 4: no boundary if left is ZWJ and right is emoji and nearest non-attachment before left is emoji
        if left == ZWJ and is_emoji(right):
            if i > 0:
                prev_idx = prev_non_attachment_idx[i - 1]
                if prev_idx != -1 and is_emoji(scalars[prev_idx]):
                    continue

        # Rule 5: no boundary if both are regional indicators and count of consecutive RIs ending at left is odd
        if is_regional_indicator(left) and is_regional_indicator(right):
            count = ri_run_len[i]
            if count % 2 == 1:
                continue

        # Rule 6: boundary
        boundaries.append(i + 1)

    boundaries.append(n)
    G = len(boundaries) - 1

    grapheme_utf8_offsets = []
    grapheme_utf16_units = []
    for gi in range(G):
        start_scalar = boundaries[gi]
        end_scalar = boundaries[gi + 1]
        start_byte = utf8_offsets[start_scalar]
        end_byte = utf8_offsets[end_scalar]
        grapheme_utf8_offsets.append((start_byte, end_byte))
        units = sum(utf16_units[start_scalar:end_scalar])
        grapheme_utf16_units.append(units)

    def resolve_index(x):
        if x < 0:
            x = G + x
        if x < 0:
            x = 0
        elif x > G:
            x = G
        return x

    base = 911
    mask64 = 0xFFFFFFFFFFFFFFFF

    def hash_bytes(bs):
        h = 0
        for b in bs:
            h = (h * base + b) & mask64
        return h

    grapheme_hashes = []
    for gi in range(G):
        start_scalar = boundaries[gi]
        end_scalar = boundaries[gi + 1]
        bs = b''.join(utf8_bytes[start_scalar:end_scalar])
        h = hash_bytes(bs)
        grapheme_hashes.append(h)

    prefix_hash = [0] * (G + 1)
    pow_base = [1] * (G + 1)
    for i in range(G):
        prefix_hash[i + 1] = (prefix_hash[i] * base + grapheme_hashes[i]) & mask64
        pow_base[i + 1] = (pow_base[i] * base) & mask64

    def get_hash(l, r):
        length = r - l
        h = prefix_hash[r] - ((prefix_hash[l] * pow_base[length]) & mask64)
        return h & mask64

    prefix_utf16 = [0] * (G + 1)
    for i in range(G):
        prefix_utf16[i + 1] = prefix_utf16[i] + grapheme_utf16_units[i]

    out = []
    idx = 0
    for _ in range(Q):
        t = int(queries[idx]); idx += 1
        a = int(queries[idx]); idx += 1
        b = int(queries[idx]); idx += 1
        i = resolve_index(a)
        j = resolve_index(b)
        if t == 1:
            if j >= i:
                out.append(str(prefix_utf16[i]))
                out.append(str(prefix_utf16[j]))
            else:
                out.append(str(prefix_utf16[i]))
                out.append(str(prefix_utf16[i]))
        else:
            maxL = min(G - i, G - j)
            low, high = 0, maxL
            while low < high:
                mid = (low + high + 1) // 2
                if get_hash(i, i + mid) == get_hash(j, j + mid):
                    low = mid
                else:
                    high = mid - 1
            L = low
            units = prefix_utf16[i + L] - prefix_utf16[i]
            out.append(str(L))
            out.append(str(units))

    return ' '.join(out)
