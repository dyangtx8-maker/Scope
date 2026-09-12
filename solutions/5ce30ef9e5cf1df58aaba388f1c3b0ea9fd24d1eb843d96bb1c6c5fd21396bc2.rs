// problem_id: 5ce30ef9e5cf1df58aaba388f1c3b0ea9fd24d1eb843d96bb1c6c5fd21396bc2
// phase: 3
// entrypoint: main
// verified: True

use std::collections::HashMap;
use std::io::{self, Read};

#[derive(Clone)]
enum Cell {
    Int(i64),
    Obj(u32),
}

/* ---------- fast scanner ---------- */
struct Scanner {
    input: Vec<u8>,
    pos: usize,
}
impl Scanner {
    fn new() -> Self {
        let mut input = String::new();
        io::stdin().read_to_string(&mut input).unwrap();
        Self {
            input: input.into_bytes(),
            pos: 0,
        }
    }
    fn is_ws(b: u8) -> bool {
        b == b' ' || b == b'\n' || b == b'\r' || b == b'\t' || b == 0x0b || b == 0x0c
    }
    fn next_token(&mut self) -> Option<&[u8]> {
        while self.pos < self.input.len() && Self::is_ws(self.input[self.pos]) {
            self.pos += 1;
        }
        if self.pos >= self.input.len() {
            return None;
        }
        let start = self.pos;
        while self.pos < self.input.len() && !Self::is_ws(self.input[self.pos]) {
            self.pos += 1;
        }
        Some(&self.input[start..self.pos])
    }
    fn next_u32(&mut self) -> u32 {
        std::str::from_utf8(self.next_token().unwrap())
            .unwrap()
            .parse::<u32>()
            .unwrap()
    }
    fn next_usize(&mut self) -> usize {
        std::str::from_utf8(self.next_token().unwrap())
            .unwrap()
            .parse::<usize>()
            .unwrap()
    }
    fn next_i64(&mut self) -> i64 {
        std::str::from_utf8(self.next_token().unwrap())
            .unwrap()
            .parse::<i64>()
            .unwrap()
    }
    fn next_char(&mut self) -> u8 {
        self.next_token().unwrap()[0]
    }
    fn next_string(&mut self) -> String {
        String::from_utf8(self.next_token().unwrap().to_vec()).unwrap()
    }
}

/* ---------- linked list for activation order ---------- */
struct Node {
    id: u32,
    prev: Option<usize>,
    next: Option<usize>,
}
struct ActList {
    nodes: Vec<Node>,
    head: Option<usize>,
    tail: Option<usize>,
    idx_of: HashMap<u32, usize>,
}
impl ActList {
    fn new() -> Self {
        Self {
            nodes: Vec::new(),
            head: None,
            tail: None,
            idx_of: HashMap::new(),
        }
    }
    fn push_back(&mut self, id: u32) {
        let idx = self.nodes.len();
        let prev = self.tail;
        let node = Node { id, prev, next: None };
        self.nodes.push(node);
        if let Some(p) = prev {
            self.nodes[p].next = Some(idx);
        } else {
            self.head = Some(idx);
        }
        self.tail = Some(idx);
        self.idx_of.insert(id, idx);
    }
    fn remove(&mut self, id: u32) {
        if let Some(&idx) = self.idx_of.get(&id) {
            let (prev, next) = {
                let n = &self.nodes[idx];
                (n.prev, n.next)
            };
            if let Some(p) = prev {
                self.nodes[p].next = next;
            } else {
                self.head = next;
            }
            if let Some(nx) = next {
                self.nodes[nx].prev = prev;
            } else {
                self.tail = prev;
            }
            self.idx_of.remove(&id);
        }
    }
    fn pop_tail(&mut self) -> Option<u32> {
        let tail_idx = self.tail?;
        let id = self.nodes[tail_idx].id;
        let prev = self.nodes[tail_idx].prev;
        if let Some(p) = prev {
            self.nodes[p].next = None;
            self.tail = Some(p);
        } else {
            self.head = None;
            self.tail = None;
        }
        self.idx_of.remove(&id);
        Some(id)
    }
    fn ids_in_order(&self) -> Vec<u32> {
        let mut res = Vec::new();
        let mut cur = self.head;
        while let Some(idx) = cur {
            res.push(self.nodes[idx].id);
            cur = self.nodes[idx].next;
        }
        res
    }
}

/* ---------- activation data ---------- */
struct Activation {
    writes: Vec<(u32, u32, Cell)>, // (object, attribute, old value)
}

/* ---------- path resolution ---------- */
fn resolve_path(
    root: u32,
    attrs: &[u32],
    cells: &HashMap<(u32, u32), Cell>,
) -> (u32, u32) {
    let mut cur = root;
    if attrs.is_empty() {
        panic!("path length is always >= 1");
    }
    for &a in &attrs[..attrs.len() - 1] {
        let cell = cells.get(&(cur, a)).expect("intermediate cell missing");
        match cell {
            Cell::Obj(o) => cur = *o,
            Cell::Int(_) => panic!("intermediate cell not an object reference"),
        }
    }
    (cur, *attrs.last().unwrap())
}

/* ---------- main ---------- */
fn main() {
    let mut sc = Scanner::new();

    let _n = sc.next_u32(); // not needed directly
    let k = sc.next_usize();
    let q = sc.next_usize();

    // cells map
    let mut cells: HashMap<(u32, u32), Cell> = HashMap::with_capacity(k * 2);
    for _ in 0..k {
        let obj = sc.next_u32();
        let attr = sc.next_u32();
        let kind = sc.next_char(); // b'I' or b'O'
        let cell = if kind == b'I' {
            Cell::Int(sc.next_i64())
        } else {
            Cell::Obj(sc.next_u32())
        };
        cells.insert((obj, attr), cell);
    }

    // activations
    let mut act_map: HashMap<u32, Activation> = HashMap::new();
    let mut act_list = ActList::new();

    let mut output = String::new();

    for _ in 0..q {
        let cmd = sc.next_string();
        match cmd.as_str() {
            "START" => {
                let id = sc.next_u32();
                let m = sc.next_usize();
                let mut writes = Vec::with_capacity(m);
                for _ in 0..m {
                    let root = sc.next_u32();
                    let l = sc.next_usize();
                    let mut attrs = Vec::with_capacity(l);
                    for _ in 0..l {
                        attrs.push(sc.next_u32());
                    }
                    let kind = sc.next_char();
                    let new_cell = if kind == b'I' {
                        Cell::Int(sc.next_i64())
                    } else {
                        Cell::Obj(sc.next_u32())
                    };
                    let (obj, attr) = resolve_path(root, &attrs, &cells);
                    let old = cells.get(&(obj, attr)).cloned().expect("cell exists");
                    writes.push((obj, attr, old));
                    cells.insert((obj, attr), new_cell);
                }
                act_map.insert(id, Activation { writes });
                act_list.push_back(id);
            }
            "STOP" => {
                let id = sc.next_u32();
                if let Some(act) = act_map.remove(&id) {
                    for (obj, attr, old) in act.writes.iter().rev() {
                        cells.insert((*obj, *attr), old.clone());
                    }
                    act_list.remove(id);
                }
            }
            "STOPALL" => {
                while let Some(id) = act_list.pop_tail() {
                    if let Some(act) = act_map.remove(&id) {
                        for (obj, attr, old) in act.writes.iter().rev() {
                            cells.insert((*obj, *attr), old.clone());
                        }
                    }
                }
            }
            "GET" => {
                let root = sc.next_u32();
                let l = sc.next_usize();
                let mut attrs = Vec::with_capacity(l);
                for _ in 0..l {
                    attrs.push(sc.next_u32());
                }
                let (obj, attr) = resolve_path(root, &attrs, &cells);
                let cell = cells.get(&(obj, attr)).expect("target cell exists");
                match cell {
                    Cell::Int(v) => {
                        output.push_str(&format!("I {}\n", v));
                    }
                    Cell::Obj(o) => {
                        output.push_str(&format!("O {}\n", o));
                    }
                }
            }
            "STACK" => {
                let ids = act_list.ids_in_order();
                output.push_str(&format!("{}", ids.len()));
                for id in ids {
                    output.push(' ');
                    output.push_str(&id.to_string());
                }
                output.push('\n');
            }
            _ => {}
        }
    }

    print!("{}", output);
}
