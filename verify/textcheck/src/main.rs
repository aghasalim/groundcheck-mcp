//! An independent implementation of the check_quote text pipeline, in Rust.
//!
//! quote_on_page is a fetch followed by three steps that decide the verdict:
//! drop script and style blocks, replace every remaining tag with a space,
//! collapse whitespace and lowercase, then look for the quote as a substring.
//! Only the fetch needs the network. The three steps that actually decide are
//! pure text handling, and in Python they are three regular expressions whose
//! behaviour I asserted in tests I wrote from the same assumptions.
//!
//! This reimplements them by hand, no regex crate and no crates at all, and
//! must agree with verify/cases_text.tsv on every verdict.
//!
//! It then does the part Python is too slow to bother with: a randomised
//! property test of the promise the tool makes, that matching is insensitive to
//! whitespace and case. It draws word spans out of the fixtures, rewraps them
//! with random whitespace and random case, and requires the verdict to stay
//! `checked`. A single counterexample means the promise in the README is false.
//!
//!     cargo run --release --quiet -- <repo-root>

use std::env;
use std::fs;
use std::path::Path;
use std::process::ExitCode;

const FUZZ_ITERATIONS: u64 = 200_000;

/// xorshift64*, so the fuzz is reproducible and needs no crate.
struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_f491_4f6c_dd1d)
    }
    fn below(&mut self, n: usize) -> usize {
        (self.next() % n as u64) as usize
    }
}

/// Drop `<script ...> ... </script>` and the same for style, exactly as the
/// non greedy regex does: an opening tag with no `>` inside it, then the
/// nearest closing tag. If there is no closing tag the block is left alone,
/// which is what a regex with no match does too.
fn strip_blocks(html: &str) -> String {
    // ascii lowercase, so byte offsets into `lower` stay valid in `html`
    let lower = html.to_ascii_lowercase();
    let bytes = html.as_bytes();
    let mut out = String::with_capacity(html.len());
    let mut i = 0usize;
    while i < bytes.len() {
        let rest = &lower[i..];
        let tag = if rest.starts_with("<script") {
            Some("script")
        } else if rest.starts_with("<style") {
            Some("style")
        } else {
            None
        };
        match tag {
            Some(name) => {
                // the opening tag must close with `>` and contain no `>`
                match lower[i..].find('>') {
                    Some(open_end) => {
                        let after = i + open_end + 1;
                        let close = format!("</{}>", name);
                        match lower[after..].find(&close) {
                            Some(off) => {
                                out.push(' ');
                                i = after + off + close.len();
                            }
                            None => {
                                out.push(html[i..i + 1].chars().next().unwrap());
                                i += 1;
                            }
                        }
                    }
                    None => {
                        out.push_str(&html[i..]);
                        i = bytes.len();
                    }
                }
            }
            None => {
                let ch = html[i..].chars().next().unwrap();
                out.push(ch);
                i += ch.len_utf8();
            }
        }
    }
    out
}

/// Replace every `<...>` with a space. The space matters: a tag between two
/// words is a word boundary, so dropping it outright would glue them together.
fn strip_tags(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut depth = false;
    for ch in s.chars() {
        match ch {
            '<' if !depth => {
                depth = true;
                out.push(' ');
            }
            '>' if depth => depth = false,
            _ if !depth => out.push(ch),
            _ => {}
        }
    }
    out
}

/// Collapse whitespace runs to one space, trim, lowercase.
fn norm(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut pending = false;
    for ch in s.chars() {
        if ch.is_whitespace() {
            pending = !out.is_empty();
        } else {
            if pending {
                out.push(' ');
                pending = false;
            }
            for lower in ch.to_lowercase() {
                out.push(lower);
            }
        }
    }
    out
}

fn verdict(needle: &str, page: &str) -> &'static str {
    if needle.trim().chars().count() < 8 {
        return "unverifiable";
    }
    let text = norm(&strip_tags(&strip_blocks(page)));
    if text.contains(&norm(needle)) {
        "checked"
    } else {
        "refuted"
    }
}

fn unescape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut chars = s.chars();
    while let Some(ch) = chars.next() {
        if ch != '\\' {
            out.push(ch);
            continue;
        }
        match chars.next() {
            Some('n') => out.push('\n'),
            Some('t') => out.push('\t'),
            Some('\\') => out.push('\\'),
            Some(other) => {
                out.push('\\');
                out.push(other);
            }
            None => out.push('\\'),
        }
    }
    out
}

/// Rewrap a phrase with random whitespace and random case. Matching is
/// supposed to be blind to both.
fn scramble(phrase: &str, rng: &mut Rng) -> String {
    let ws = [" ", "  ", "\n", "\t", " \n   ", "\r\n"];
    let mut out = String::new();
    for (i, word) in phrase.split(' ').enumerate() {
        if i > 0 {
            out.push_str(ws[rng.below(ws.len())]);
        }
        for ch in word.chars() {
            if rng.next() & 1 == 0 {
                for c in ch.to_uppercase() {
                    out.push(c);
                }
            } else {
                out.push(ch);
            }
        }
    }
    out
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    if args.len() != 2 {
        eprintln!("usage: textcheck <repo-root>");
        return ExitCode::from(2);
    }
    let root = Path::new(&args[1]);
    let cases_path = root.join("verify/cases_text.tsv");
    let table = match fs::read_to_string(&cases_path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("cannot read {}: {}", cases_path.display(), e);
            return ExitCode::from(2);
        }
    };

    let mut rows = 0usize;
    let mut bad = 0usize;
    let mut fixtures: Vec<String> = Vec::new();
    for (n, line) in table.lines().enumerate() {
        if n == 0 || line.is_empty() {
            continue;
        }
        let field: Vec<&str> = line.split('\t').collect();
        if field.len() != 4 {
            println!("  line {}: expected 4 fields, found {}", n + 1, field.len());
            bad += 1;
            rows += 1;
            continue;
        }
        let (id, fixture, needle, want) = (field[0], field[1], unescape(field[2]), field[3]);
        let page = match fs::read_to_string(root.join("verify/fixtures").join(fixture)) {
            Ok(p) => p,
            Err(e) => {
                println!("  {}: cannot read fixture {}: {}", id, fixture, e);
                bad += 1;
                rows += 1;
                continue;
            }
        };
        let got = verdict(&needle, &page);
        rows += 1;
        if got != want {
            println!("  {} ({}): python says {}, Rust says {}", id, fixture, want, got);
            bad += 1;
        }
        if !fixtures.contains(&page) {
            fixtures.push(page);
        }
    }

    if rows == 0 {
        println!("no cases read from {}", cases_path.display());
        return ExitCode::from(1);
    }
    if bad > 0 {
        println!("Rust disagrees with Python on {} of {} check_quote cases", bad, rows);
        return ExitCode::from(1);
    }
    println!("Rust reproduces all {} check_quote verdicts", rows);

    // The property: a quote taken out of a page still matches after being
    // rewrapped and recased. Anything else makes the README's claim false.
    let mut rng = Rng(0x9e37_79b9_7f4a_7c15);
    let mut counterexamples = 0u64;
    let mut checked = 0u64;
    for page in &fixtures {
        let text = norm(&strip_tags(&strip_blocks(page)));
        let words: Vec<&str> = text.split(' ').filter(|w| !w.is_empty()).collect();
        if words.len() < 3 {
            continue;
        }
        for _ in 0..FUZZ_ITERATIONS {
            let start = rng.below(words.len());
            let span = 1 + rng.below(std::cmp::min(8, words.len() - start));
            let phrase = words[start..start + span].join(" ");
            if phrase.trim().chars().count() < 8 {
                continue;
            }
            let quote = scramble(&phrase, &mut rng);
            checked += 1;
            if verdict(&quote, page) != "checked" {
                if counterexamples < 3 {
                    println!("  whitespace counterexample: {:?}", quote);
                }
                counterexamples += 1;
            }
        }
    }
    if counterexamples > 0 {
        println!(
            "{} of {} rewrapped quotes failed to match, matching is not whitespace blind",
            counterexamples, checked
        );
        return ExitCode::from(1);
    }
    println!(
        "{} randomly rewrapped and recased quotes from the fixtures all still match",
        checked
    );
    ExitCode::SUCCESS
}
