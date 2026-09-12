// problem_id: 4e49a099fd84fad956c856b0bb613db05b87316550730c4ac1329a2f26775465
// phase: 3
// entrypoint: main
// verified: True

use std::io::{self, Read};

const BASE: u64 = 911;

/// true if `b` is an ASCII whitespace (the token separator)
#[inline]
fn is_ws(b: u8) -> bool {
    matches!(b, 0x09..=0x0D | 0x20)
}

/// fast signed integer parser
fn parse_i64(data: &[u8], pos: &mut usize) -> i64 {
    while *pos < data.len() && is_ws(data[*pos]) {
        *pos += 1;
    }
    let mut sign = 1i64;
    if data[*pos] == b'-' {
        sign = -1;
        *pos += 1;
    }
    let mut val: i64 = 0;
    while *pos < data.len() && !is_ws(data[*pos]) {
        val = val * 10 + (data[*pos] - b'0') as i64;
        *pos += 1;
    }
    val * sign
}

/// classification helpers
#[inline]
fn is_control(cp: u32) -> bool {
    (0x0000..=0x001F).contains(&cp) || (0x007F..=0x009F).contains(&cp)
}
#[inline]
fn is_attachment(cp: u32) -> bool {
    (0x0300..=0x036F).contains(&cp)
        || (0x1AB0..=0x1AFF).contains(&cp)
        || (0x1DC0..=0x1DFF).contains(&cp)
        || (0x20D0..=0x20FF).contains(&cp)
        || (0xFE00..=0xFE0F).contains(&cp)
        || (0xFE20..=0xFE2F).contains(&cp)
        || (0x1F3FB..=0x1F3FF).contains(&cp)
        || (0xE0100..=0xE01EF).contains(&cp)
}
#[inline]
fn is_emoji(cp: u32) -> bool {
    (0x2600..=0x27BF).contains(&cp) || (0x1F000..=0x1FAFF).contains(&cp)
}
#[inline]
fn is_ri(cp: u32) -> bool {
    (0x1F1E6..=0x1F1FF).contains(&cp)
}

fn main() {
    // ----- read whole stdin -------------------------------------------------
    let mut input = Vec::new();
    io::stdin().read_to_end(&mut input).unwrap();

    // ----- first token = the UTF‑8 string S ---------------------------------
    let mut pos = 0usize;
    while pos < input.len() && is_ws(input[pos]) {
        pos += 1;
    }
    let start_s = pos;
    while pos < input.len() && !is_ws(input[pos]) {
        pos += 1;
    }
    let s_bytes = &input[start_s..pos]; // slice of the original bytes

    // ----- remaining tokens are numbers ------------------------------------
    let q: usize = parse_i64(&input, &mut pos) as usize;

    // ----- decode S into code points ----------------------------------------
    let s_str = std::str::from_utf8(s_bytes).expect("invalid UTF-8");
    let mut cps: Vec<u32> = Vec::with_capacity(s_str.len());
    let mut utf8_off: Vec<usize> = Vec::with_capacity(s_str.len() + 1);
    let mut utf16_pref: Vec<u64> = Vec::with_capacity(s_str.len() + 1);
    let mut is_att_vec: Vec<bool> = Vec::with_capacity(s_str.len());

    utf8_off.push(0);
    utf16_pref.push(0);
    for ch in s_str.chars() {
        let cp = ch as u32;
        cps.push(cp);
        let len_utf8 = ch.len_utf8();
        let len_utf16 = if cp <= 0xFFFF { 1u64 } else { 2u64 };
        let last_off = *utf8_off.last().unwrap();
        utf8_off.push(last_off + len_utf8);
        let last_utf16 = *utf16_pref.last().unwrap();
        utf16_pref.push(last_utf16 + len_utf16);
        is_att_vec.push(is_attachment(cp));
    }
    let n = cps.len();

    // ----- prev_non_attachment ------------------------------------------------
    let mut prev_non_att: Vec<usize> = vec![usize::MAX; n];
    let mut last_non = usize::MAX;
    for i in 0..n {
        if !is_att_vec[i] {
            last_non = i;
        }
        prev_non_att[i] = last_non;
    }

    // ----- ri run length ----------------------------------------------------
    let mut ri_run: Vec<usize> = vec![0; n];
    for i in 0..n {
        if is_ri(cps[i]) {
            ri_run[i] = 1 + if i > 0 { ri_run[i - 1] } else { 0 };
        }
    }

    // ----- compute grapheme boundaries ---------------------------------------
    let mut boundaries: Vec<usize> = Vec::with_capacity(n + 2);
    boundaries.push(0);
    const CR: u32 = 0x000D;
    const LF: u32 = 0x000A;
    const ZWJ: u32 = 0x200D;

    for i in 0..n - 1 {
        let left = cps[i];
        let right = cps[i + 1];

        // Rule 1
        if left == CR && right == LF {
            continue;
        }
        // Rule 2
        if is_control(left) || is_control(right) {
            boundaries.push(i + 1);
            continue;
        }
        // Rule 3
        if is_att_vec[i + 1] || right == ZWJ {
            continue;
        }
        // Rule 4
        if left == ZWJ && is_emoji(right) {
            if i > 0 {
                let prev = prev_non_att[i - 1];
                if prev != usize::MAX && is_emoji(cps[prev]) {
                    continue;
                }
            }
        }
        // Rule 5
        if is_ri(left) && is_ri(right) {
            let cnt = ri_run[i];
            if cnt % 2 == 1 {
                continue;
            }
        }
        // Rule 6
        boundaries.push(i + 1);
    }
    boundaries.push(n);
    let g = boundaries.len() - 1; // number of graphemes

    // ----- per‑grapheme data -------------------------------------------------
    let mut g_hash: Vec<u64> = Vec::with_capacity(g);
    let mut pref_utf16: Vec<u64> = Vec::with_capacity(g + 1);
    pref_utf16.push(0);

    for gi in 0..g {
        let start = boundaries[gi];
        let end = boundaries[gi + 1];
        // UTF‑16 length
        let utf16_len = utf16_pref[end] - utf16_pref[start];
        let next_utf16 = pref_utf16[gi] + utf16_len;
        pref_utf16.push(next_utf16);

        // hash of the UTF‑8 bytes of the grapheme
        let mut h: u64 = 0;
        let mut idx = utf8_off[start];
        while idx < utf8_off[end] {
            h = h.wrapping_mul(BASE).wrapping_add(s_bytes[idx] as u64);
            idx += 1;
        }
        g_hash.push(h);
    }

    // ----- prefix hash over grapheme hashes ----------------------------------
    let mut pref_hash: Vec<u64> = vec![0; g + 1];
    let mut pow_base: Vec<u64> = vec![1; g + 1];
    for i in 0..g {
        pref_hash[i + 1] = pref_hash[i]
            .wrapping_mul(BASE)
            .wrapping_add(g_hash[i]);
        pow_base[i + 1] = pow_base[i].wrapping_mul(BASE);
    }

    // ----- helper closures ---------------------------------------------------
    let resolve = |x: i64| -> usize {
        let mut idx = if x < 0 { (g as i64) + x } else { x };
        if idx < 0 {
            idx = 0;
        } else if idx > g as i64 {
            idx = g as i64;
        }
        idx as usize
    };
    let get_hash = |l: usize, r: usize,
                    pref: &Vec<u64>,
                    pow: &Vec<u64>|
     -> u64 {
        let len = r - l;
        let left = pref[l].wrapping_mul(pow[len]);
        pref[r].wrapping_sub(left)
    };

    // ----- answer queries ----------------------------------------------------
    let mut out = String::new();
    for _ in 0..q {
        let typ = parse_i64(&input, &mut pos) as i32;
        let a = parse_i64(&input, &mut pos);
        let b = parse_i64(&input, &mut pos);
        let i = resolve(a);
        let j = resolve(b);
        if typ == 1 {
            if j >= i {
                out.push_str(&format!("{} {}\n", pref_utf16[i], pref_utf16[j]));
            } else {
                out.push_str(&format!("{} {}\n", pref_utf16[i], pref_utf16[i]));
            }
        } else {
            // type 2
            let max_len = std::cmp::min(g - i, g - j);
            let mut lo = 0usize;
            let mut hi = max_len;
            while lo < hi {
                let mid = (lo + hi + 1) / 2;
                let h1 = get_hash(i, i + mid, &pref_hash, &pow_base);
                let h2 = get_hash(j, j + mid, &pref_hash, &pow_base);
                if h1 == h2 {
                    lo = mid;
                } else {
                    hi = mid - 1;
                }
            }
            let l = lo;
            let utf16_len = pref_utf16[i + l] - pref_utf16[i];
            out.push_str(&format!("{} {}\n", l, utf16_len));
        }
    }

    print!("{}", out);
}
