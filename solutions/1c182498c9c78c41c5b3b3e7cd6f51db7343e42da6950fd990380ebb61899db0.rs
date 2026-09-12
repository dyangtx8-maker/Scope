// problem_id: 1c182498c9c78c41c5b3b3e7cd6f51db7343e42da6950fd990380ebb61899db0
// phase: 3
// entrypoint: main
// verified: True

use std::io::{self, BufRead, Write};

fn utf8_len(u: u32) -> u64 {
    match u {
        0x0000..=0x007F => 1,
        0x0080..=0x07FF => 2,
        0x0800..=0xFFFF => 3,
        _ => 4,
    }
}

fn main() {
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());
    let stdin = io::stdin();
    let mut lines = stdin.lock().lines();

    // Read first line: N S Q C H
    let first_line = lines.next().unwrap().unwrap();
    let mut first_iter = first_line.split_whitespace();
    let n: usize = first_iter.next().unwrap().parse().unwrap();
    let s: usize = first_iter.next().unwrap().parse().unwrap();
    let q: usize = first_iter.next().unwrap().parse().unwrap();
    let c: u64 = first_iter.next().unwrap().parse().unwrap();
    let h: u64 = first_iter.next().unwrap().parse().unwrap();

    // Read N hex Unicode scalars
    // They may be split across multiple lines, so read tokens until we have N
    let mut chars = Vec::with_capacity(n);
    while chars.len() < n {
        let line = lines.next().unwrap().unwrap();
        for token in line.split_whitespace() {
            let val = u32::from_str_radix(token, 16).unwrap();
            // Surrogates are invalid, but problem states input is valid.
            chars.push(val);
            if chars.len() == n {
                break;
            }
        }
    }

    // Compute payload lengths per character
    let mut payloads = Vec::with_capacity(n);
    for &ch in &chars {
        payloads.push(utf8_len(ch));
    }

    // Read S segment lengths
    let mut seglens = Vec::with_capacity(s);
    while seglens.len() < s {
        let line = lines.next().unwrap().unwrap();
        for token in line.split_whitespace() {
            let val = token.parse::<usize>().unwrap();
            seglens.push(val);
            if seglens.len() == s {
                break;
            }
        }
    }

    // Compute segment boundaries: segment_ends[i] = 1-based index of last char in segment i
    // segment_starts[i] = 1-based index of first char in segment i
    let mut segment_ends = Vec::with_capacity(s);
    let mut segment_starts = Vec::with_capacity(s);
    let mut acc = 0usize;
    for &l in &seglens {
        acc += l;
        segment_ends.push(acc);
    }
    let mut prev = 0usize;
    for &end in &segment_ends {
        segment_starts.push(prev + 1);
        prev = end;
    }

    // Precompute prefix sums of payload bytes for characters (1-based indexing)
    // prefix_payloads[i] = sum of payloads[0..i-1]
    let mut prefix_payloads = Vec::with_capacity(n + 1);
    prefix_payloads.push(0u64);
    for &p in &payloads {
        prefix_payloads.push(prefix_payloads.last().unwrap() + p);
    }

    // For each character, store which segment it belongs to (0-based segment index)
    // We can find segment by binary search on segment_ends
    // To speed queries, build a vector seg_of_char: usize for each char index
    let mut seg_of_char = vec![0usize; n];
    {
        let mut seg_idx = 0;
        for i in 0..n {
            while seg_idx < s && i + 1 > segment_ends[seg_idx] {
                seg_idx += 1;
            }
            seg_of_char[i] = seg_idx;
        }
    }

    // For each segment, precompute prefix sums of payloads inside that segment
    // We'll store segment prefix sums as Vec<Vec<u64>>: segment_prefixes[seg][pos_in_seg]
    // pos_in_seg is 0-based inside segment, prefix sums length = segment length + 1
    let mut segment_prefixes = Vec::with_capacity(s);
    let mut start_idx = 0;
    for &seg_len in &seglens {
        let mut seg_pref = Vec::with_capacity(seg_len + 1);
        seg_pref.push(0);
        for i in 0..seg_len {
            seg_pref.push(seg_pref.last().unwrap() + payloads[start_idx + i]);
        }
        segment_prefixes.push(seg_pref);
        start_idx += seg_len;
    }

    // Read and process Q queries
    // Each query: L R B (1-based indices)
    // For each query, simulate transmission as per problem statement.

    // We'll implement a function to process one query efficiently:
    // Because N and Q can be up to 5e5, and C,B,H up to 1e18,
    // we must avoid O(R-L+1) per query.
    //
    // Approach:
    // - We process segments fully or partially.
    // - Within a segment, we can binary search how many characters fit into chunks.
    // - Chunks are limited by C payload bytes max.
    // - Chunk boundaries:
    //   * Cannot cross segment boundary.
    //   * Each chunk max C payload bytes.
    //   * Chunk header H bytes added per chunk.
    // - Transmission stops before a character if adding it (and header if new chunk) exceeds B.
    //
    // We must find how many characters from L..R can be sent.
    //
    // Implementation details:
    // - We'll iterate over segments overlapping [L,R].
    // - For each segment, find the subrange inside [L,R].
    // - For that subrange, split into chunks of max C payload bytes.
    // - For each chunk, header H bytes added.
    // - We accumulate wire bytes = sum payload + chunks * H.
    // - Stop when next character would exceed B.
    //
    // To avoid O(n) per query, we binary search how many characters fit in each segment subrange.
    // For chunking inside segment subrange:
    //   We can binary search how many characters fit into chunks with max C payload bytes.
    //   Because chunk boundaries are at segment boundaries or when chunk payload reaches C.
    //
    // We'll implement a helper to find max prefix length in segment subrange that fits in budget.

    // Helper: given segment index seg, subrange [l,r] inside segment (1-based inside segment),
    // and budget left for wire bytes,
    // find max prefix length of characters in [l,r] that can be sent without exceeding budget.
    //
    // We must consider chunk headers and chunk payload limits.
    //
    // We'll simulate chunking greedily:
    // - Start new chunk with 0 payload.
    // - Add characters until chunk payload + next char payload > C, then start new chunk.
    // - Each chunk adds H header bytes.
    //
    // We want to find max prefix length k (0 <= k <= r-l+1) so that:
    //   total_payload + chunks * H <= budget
    //
    // We'll binary search k, then check feasibility.

    // To check feasibility for k characters in segment subrange [l,r]:
    // - We split first k characters into chunks of max C payload bytes.
    // - Count chunks and sum payload.
    // - Check if sum_payload + chunks*H <= budget.

    // To do this efficiently, we precompute prefix sums of payloads in segment.
    // Then we can binary search chunk boundaries inside k characters.

    // We'll implement a function to count chunks for first k characters in segment subrange:
    // chunk boundaries occur when sum of payloads in chunk > C.
    // We can greedily find chunk ends by binary searching prefix sums.

    // Because chunk payload limit C can be large (up to 1e18), and segment length up to N,
    // we must do O(log segment_len) per chunk boundary search.
    // But total chunks per segment subrange is at most ceil(total_payload/C).
    // Since C can be large, chunks per segment subrange is small or moderate.
    //
    // To avoid complexity, we do a two-level binary search:
    // - Outer binary search on k (number of chars to send)
    // - For each k, count chunks by greedy splitting using prefix sums and binary search.

    // Implement helper functions:

    // Find max prefix length k in [l,r] (1-based inside segment) that fits budget
    fn max_prefix_fit(
        seg_pref: &[u64],
        l: usize,
        r: usize,
        c: u64,
        h: u64,
        budget: u64,
    ) -> usize {
        // Binary search on k in [0..=r-l+1]
        let max_len = r - l + 1;
        if max_len == 0 {
            return 0;
        }
        let mut low = 0usize;
        let mut high = max_len;
        while low < high {
            let mid = (low + high + 1) / 2;
            if fits(seg_pref, l, mid, c, h, budget) {
                low = mid;
            } else {
                high = mid - 1;
            }
        }
        low
    }

    // Check if first k characters starting at l fit budget
    // seg_pref: prefix sums for segment (0-based, length seg_len+1)
    // l: 1-based start inside segment
    // k: number of characters to check
    // c: max chunk payload
    // h: header size
    // budget: max wire bytes allowed
    fn fits(seg_pref: &[u64], l: usize, k: usize, c: u64, h: u64, budget: u64) -> bool {
        if k == 0 {
            return true;
        }
        let end = l + k - 1;
        let total_payload = seg_pref[end] - seg_pref[l - 1];
        // Count chunks by greedy splitting:
        // chunk payload <= c
        // We'll find chunk boundaries by binary searching prefix sums.

        // We'll iterate over chunk starts:
        // chunk_start = l initially
        // find chunk_end = max pos in [chunk_start..end] with payload sum <= c
        // sum payload in [chunk_start..x] = seg_pref[x] - seg_pref[chunk_start-1]
        // binary search x in [chunk_start..end]

        let mut chunks = 0u64;
        let mut pos = l;
        while pos <= end {
            // binary search max x in [pos..end] with seg_pref[x]-seg_pref[pos-1] <= c
            let mut low = pos;
            let mut high = end;
            let base = seg_pref[pos - 1];
            while low < high {
                let mid = (low + high + 1) / 2;
                let val = seg_pref[mid] - base;
                if val <= c {
                    low = mid;
                } else {
                    high = mid - 1;
                }
            }
            chunks += 1;
            pos = low + 1;
        }
        // total wire bytes = total_payload + chunks * h
        // Check if <= budget
        total_payload + chunks * h <= budget
    }

    // For each query:
    // We'll iterate over segments overlapping [L,R].
    // For each segment subrange, find max prefix length that fits budget left.
    // Accumulate results.
    // Stop when no more characters can be sent.

    for _ in 0..q {
        let line = lines.next().unwrap().unwrap();
        let mut it = line.split_whitespace();
        let L: usize = it.next().unwrap().parse().unwrap();
        let R: usize = it.next().unwrap().parse().unwrap();
        let B: u64 = it.next().unwrap().parse().unwrap();

        if B == 0 {
            // Cannot send anything
            writeln!(
                out,
                "0 0 0 0 0 0 LIMIT"
            )
            .unwrap();
            continue;
        }

        // Find first segment containing L
        // seg_of_char is 0-based segment index for each char
        let mut seg_idx = seg_of_char[L - 1];
        let mut pos = L; // current position in [L,R]
        let mut budget_left = B;
        let mut total_payload = 0u64;
        let mut total_wire = 0u64;
        let mut total_chars = 0usize;
        let mut total_chunks = 0u64;
        let mut last_pos = 0usize;
        let mut last_chunk_payload = 0u64;

        // We'll track chunk payload for current chunk to handle chunk boundaries across segments
        // But problem states chunk cannot cross segment boundary, so chunk ends at segment boundary.
        // So chunking resets at each segment boundary.

        // For each segment overlapping [L,R]:
        while seg_idx < s && pos <= R {
            // segment boundaries (1-based)
            let seg_start = segment_starts[seg_idx];
            let seg_end = segment_ends[seg_idx];
            // subrange inside segment overlapping [L,R]
            let sub_l = if pos > seg_start { pos - seg_start + 1 } else { 1 };
            let sub_r = if R < seg_end { R - seg_start + 1 } else { seg_end - seg_start + 1 };
            let seg_len = sub_r - sub_l + 1;

            if seg_len == 0 {
                seg_idx += 1;
                continue;
            }

            // Find max prefix length k in [sub_l..sub_r] that fits budget_left
            let k = max_prefix_fit(&segment_prefixes[seg_idx], sub_l, sub_r, c, h, budget_left);

            if k == 0 {
                // Cannot send any char in this segment subrange
                break;
            }

            // Compute chunks and payload for k chars
            // Recompute chunks count and payload sum for k chars
            let total_payload_seg = segment_prefixes[seg_idx][sub_l + k - 1] - segment_prefixes[seg_idx][sub_l - 1];

            // Count chunks for k chars
            let mut chunks = 0u64;
            let mut p = sub_l;
            while p <= sub_l + k - 1 {
                let mut low = p;
                let mut high = sub_l + k - 1;
                let base = segment_prefixes[seg_idx][p - 1];
                while low < high {
                    let mid = (low + high + 1) / 2;
                    let val = segment_prefixes[seg_idx][mid] - base;
                    if val <= c {
                        low = mid;
                    } else {
                        high = mid - 1;
                    }
                }
                chunks += 1;
                p = low + 1;
            }

            let wire_bytes_seg = total_payload_seg + chunks * h;

            // Update totals
            total_payload += total_payload_seg;
            total_wire += wire_bytes_seg;
            total_chunks += chunks;
            total_chars += k;
            last_pos = pos + k - 1;

            // last chunk payload is payload of last chunk in this segment subrange
            // Find last chunk payload:
            // last chunk starts at some position, ends at last_pos inside segment subrange
            // We'll find last chunk start by scanning backwards:
            // But to avoid O(k), we do binary search:
            // The last chunk is the last chunk in the k chars.
            // We find the start of last chunk by binary searching for minimal x in [sub_l..last_char_pos]
            // such that payload sum from x to last_char_pos <= c and payload sum from x-1 to last_char_pos > c or x == sub_l

            let last_char_pos = sub_l + k - 1;
            let mut low = sub_l;
            let mut high = last_char_pos;
            let mut last_chunk_start = sub_l;
            while low <= high {
                let mid = (low + high) / 2;
                let payload_sum = segment_prefixes[seg_idx][last_char_pos] - segment_prefixes[seg_idx][mid - 1];
                if payload_sum <= c {
                    last_chunk_start = mid;
                    high = mid.checked_sub(1).unwrap_or(0);
                    if high == 0 && mid == 1 {
                        break;
                    }
                } else {
                    low = mid + 1;
                }
            }
            last_chunk_payload = segment_prefixes[seg_idx][last_char_pos] - segment_prefixes[seg_idx][last_chunk_start - 1];

            budget_left = budget_left.saturating_sub(wire_bytes_seg);
            pos += k;
            if k < seg_len {
                // Could not send full segment subrange, stop
                break;
            }
            seg_idx += 1;
        }

        // Determine cause and last transmitted position
        let cause = if last_pos == R {
            "END"
        } else if total_chars == 0 {
            // no chars sent
            "LIMIT"
        } else {
            "LIMIT"
        };

        if total_chars == 0 {
            // no chars sent
            writeln!(
                out,
                "0 0 0 0 0 0 LIMIT"
            )
            .unwrap();
            continue;
        }

        // Output:
        // payload_bytes wire_bytes characters last chunks last_chunk_payload_bytes cause
        writeln!(
            out,
            "{} {} {} {} {} {} {}",
            total_payload,
            total_wire,
            total_chars,
            last_pos,
            total_chunks,
            last_chunk_payload,
            cause
        )
        .unwrap();
    }
}
