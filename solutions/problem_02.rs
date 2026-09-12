// problem_id: 1c182498c9c78c41c5b3b3e7cd6f51db7343e42da6950fd990380ebb61899db0
// phase: 2
// entrypoint: main
// verified: True

use std::io::{self, Read};

fn main() {
    // ----- read entire stdin -----
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    // Trim and parse the single integer n
    let n: u64 = input.trim().parse().expect("expected an integer");

    // ----- compute trailing zeros of n! -----
    let mut ans: u64 = 0;
    let mut cur = n / 5;
    while cur > 0 {
        ans += cur;
        cur /= 5;
    }

    // ----- output -----
    println!("{}", ans);
}
