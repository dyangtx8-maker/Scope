// problem_id: 5d02bd0e16ab63c540232452b60e8e6cf36d01e0820cb6b422b63c1a31091718
// phase: 3
// entrypoint: main
// verified: True

use std::io::{self, Read};

fn main() {
    // Read the entire stdin into a string (binary data is also fine)
    let mut input = Vec::new();
    io::stdin().read_to_end(&mut input).expect("Failed to read stdin");

    const MOD: u64 = 1_000_000_007;
    const BASE: u64 = 91_138_233; // a large odd base, fits in u64

    let mut hash: u64 = 0;
    for &byte in &input {
        hash = (hash.wrapping_mul(BASE) + byte as u64) % MOD;
    }

    // Print the hash followed by a newline
    println!("{}", hash);
}
