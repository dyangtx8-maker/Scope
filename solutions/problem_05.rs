// problem_id: 4e49a099fd84fad956c856b0bb613db05b87316550730c4ac1329a2f26775465
// phase: 2
// entrypoint: main
// verified: True

use std::io::{self, Read};

fn utf16_len(c: char) -> usize {
    if (c as u32) <= 0xFFFF { 1 } else { 2 }
}

// convert possibly negative index to a position in [0, g]
fn to_pos(idx: i64, g: usize) -> usize {
    let mut p = if idx >= 0 {
        idx as usize
    } else {
        (g as i64 + idx) as usize
    };
    if p > g {
        p = g;
    }
    p
}

fn main() {
    // ----- read whole input -----
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();

    // first line = the string S (may contain spaces)
    let mut lines = input.lines();
    let s = lines.next().unwrap_or("").to_string();

    // the rest: Q and the queries, split by whitespace
    let mut rest_iter = lines.flat_map(|l| l.split_whitespace());

    let q: usize = rest_iter.next().unwrap().parse().unwrap();

    // ----- preprocessing -----
    let chars: Vec<char> = s.chars().collect();
    let g = chars.len();

    // prefix of UTF‑16 offsets
    let mut pref_utf16 = Vec::with_capacity(g + 1);
    pref_utf16.push(0usize);
    for &c in &chars {
        let nxt = pref_utf16.last().unwrap() + utf16_len(c);
        pref_utf16.push(nxt);
    }

    // rolling hash (mod 2^64) and powers
    const BASE: u64 = 91138233;
    let mut pow = Vec::with_capacity(g + 1);
    pow.push(1u64);
    for _ in 0..g {
        let nxt = pow.last().unwrap().wrapping_mul(BASE);
        pow.push(nxt);
    }

    let mut hash = Vec::with_capacity(g + 1);
    hash.push(0u64);
    for &c in &chars {
        let nxt = hash
            .last()
            .unwrap()
            .wrapping_mul(BASE)
            .wrapping_add(c as u64);
        hash.push(nxt);
    }

    // helper to get hash of [l, r)
    let get_hash = |l: usize, r: usize, hash: &Vec<u64>, pow: &Vec<u64>| -> u64 {
        let len = r - l;
        hash[r]
            .wrapping_sub(hash[l].wrapping_mul(pow[len]))
    };

    // ----- answer queries -----
    let mut out = String::new();

    for _ in 0..q {
        let typ: i32 = rest_iter.next().unwrap().parse().unwrap();
        let i_raw: i64 = rest_iter.next().unwrap().parse().unwrap();
        let j_raw: i64 = rest_iter.next().unwrap().parse().unwrap();

        match typ {
            1 => {
                let a = to_pos(i_raw, g);
                let b = to_pos(j_raw, g);
                let x = pref_utf16[a];
                let y = if b < a { x } else { pref_utf16[b] };
                out.push_str(&format!("{} {}\n", x, y));
            }
            2 => {
                let a = to_pos(i_raw, g);
                let b = to_pos(j_raw, g);
                let max_len = std::cmp::min(g - a, g - b);
                // binary search for longest equal prefix length in graphemes
                let mut low = 0usize;
                let mut high = max_len;
                while low < high {
                    let mid = (low + high + 1) / 2;
                    let ha = get_hash(a, a + mid, &hash, &pow);
                    let hb = get_hash(b, b + mid, &hash, &pow);
                    if ha == hb {
                        low = mid;
                    } else {
                        high = mid - 1;
                    }
                }
                let ans = pref_utf16[a + low] - pref_utf16[a];
                out.push_str(&format!("{}\n", ans));
            }
            _ => unreachable!(),
        }
    }

    print!("{}", out);
}
