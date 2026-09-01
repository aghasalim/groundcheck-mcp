#!/usr/bin/env bash
# Recompute what this repo publishes, in every language here, and require
# agreement.
#
# Every verdict this project reports comes from one implementation,
# src/groundcheck/verify.py. The tests in tests/ assert what I believed the
# answers were, in the same language, calling the same functions, so they
# cannot catch a mistake in the thing they import. verify/export_cases.py
# freezes what that implementation returns for a fixed table of inputs, and
# what follows recomputes those same verdicts from the same raw inputs in C,
# Go, R, Rust, Node and SQL. A wrong answer would have to be wrong identically
# in six languages to get through.
#
# Each check is skipped with a clear message when its toolchain is missing, so
# this runs on a laptop with only some of them. CI has all of them.
#
#     ./verify/verify.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

# Go and Node both drive the real Python, so they need an interpreter that can
# import src.groundcheck. The repo's own virtualenv if there is one.
if [ -x "$root/.venv/bin/python" ]; then
    export GROUNDCHECK_PYTHON="$root/.venv/bin/python"
else
    export GROUNDCHECK_PYTHON="${GROUNDCHECK_PYTHON:-python3}"
fi

pass=0 fail=0 skip=0

run () {
    local name="$1" tool="$2"; shift 2
    printf '\n=== %s ===\n' "$name"
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'skipped: %s is not installed\n' "$tool"
        skip=$((skip + 1)); return
    fi
    if "$@"; then pass=$((pass + 1)); else fail=$((fail + 1)); fi
}

# SQL has no way to fail a run of its own, so the comparison happens here. The
# left side is the group by over the raw case tables; the right side is the
# summary table printed in the README. They are the same three rows or the
# README is describing data that is no longer there.
check_sql () {
    local computed published violations err
    err="${TMPDIR:-/tmp}/groundcheck-sqlite-stderr"
    computed=$(sqlite3 -init verify/summary.sql :memory: "" 2>"$err")
    # A ragged row is a warning to sqlite3, not an error: it fills the missing
    # columns with NULL and carries on with an exit status of zero. So anything
    # it says about the import is a failure here.
    if [ -s "$err" ]; then
        echo "sqlite3 could not read the case tables cleanly:"
        sed 's/^/  /' "$err"
        rm -f "$err"
        return 1
    fi
    rm -f "$err"
    violations=$(printf '%s\n' "$computed" | grep '^bad: ' || true)
    if [ -n "$violations" ]; then
        echo "the case tables violate their own invariants:"
        printf '%s\n' "$violations"
        return 1
    fi
    computed=$(printf '%s\n' "$computed" | grep -v '^bad: ' | sort)
    published=$(awk -F'|' '/^\| `cases_[a-z]+\.tsv`/ {
                    gsub(/[` ]/, "", $2); gsub(/ /, "", $3); gsub(/ /, "", $4)
                    gsub(/ /, "", $5); gsub(/ /, "", $6)
                    print $2, $3, $4, $5, $6
                }' README.md | sort)
    if [ -z "$published" ]; then
        echo "no case summary table found in README.md"
        return 1
    fi
    if [ "$computed" = "$published" ]; then
        echo "SQL: the case summary in the README is the group by over the raw tables"
        printf '%s\n' "$computed" | sed 's/^/  /'
        return 0
    fi
    echo "the README's case summary is not what the tables say:"
    diff <(printf '%s\n' "$published") <(printf '%s\n' "$computed") | sed 's/^/  /'
    return 1
}

check_c () {
    cc -std=c99 -O2 -Wall -Wextra -Wpedantic -Werror \
       -o "${TMPDIR:-/tmp}/matheval" verify/matheval.c -lm || return 1
    "${TMPDIR:-/tmp}/matheval" "$root"
}

check_go () { ( cd verify/gocheck && go run . -root "$root" -python "$GROUNDCHECK_PYTHON" ); }

check_rust () { ( cd verify/textcheck && cargo run --release --quiet -- "$root" ); }

check_node () { node verify/mathfuzz.js "$root"; }

run "SQL, the published case summary"   sqlite3 check_sql
run "C, the check_math grammar"         cc      check_c
run "Go, the MCP server over stdio"     go      check_go
run "R, the check_repo hit counts"      Rscript Rscript verify/repocheck.R "$root"
run "Rust, the check_quote text pipeline" cargo check_rust
run "Node, a random-expression fuzz"    node    check_node

printf '\n%s\n' "----------------------------------------"
printf '%d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skip"
[ "$fail" -eq 0 ] || exit 1
[ "$pass" -gt 0 ] || { echo "nothing ran"; exit 1; }
