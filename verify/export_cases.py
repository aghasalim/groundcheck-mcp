"""Export the reference verdicts that every other language here must reproduce.

The verdicts in this repo all come from one implementation, src/groundcheck.
Nothing checked that the implementation is right: the tests assert what I
believed the answer was, in the same language, using the same functions. This
script runs the three offline verifiers over a fixed table of inputs and writes
what they returned, so a C, Go, Rust, R and SQL implementation can be held to
the same answers. If any of them disagrees, one of us is wrong and the harness
says so instead of quietly agreeing with itself.

    python verify/export_cases.py

The three tables it writes are tracked, so CI can corrupt one and require the
harness to notice.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "verify"
sys.path.insert(0, str(ROOT))

from src.groundcheck import verify  # noqa: E402


def esc(s: str) -> str:
    """TSV is line and tab delimited, so those two characters and the escape
    character itself are the only things that need encoding."""
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


# --- check_math -----------------------------------------------------------
# Chosen to pin the grammar (precedence, associativity, unary minus), the three
# verdicts, and both sides of the relative tolerance boundary.
MATH = [
    ("m01", "3.7*1400", 8880.0),          # the error from the hardware deck
    ("m02", "3.7*1400", 5180.0),          # what it actually is
    ("m03", "2+2", 4.0),
    ("m04", "2+3*4", 14.0),               # precedence
    ("m05", "(2+3)*4", 20.0),
    ("m06", "2**3**2", 512.0),            # ** is right associative
    ("m07", "-2**2", -4.0),               # unary minus binds looser than **
    ("m08", "2**-1", 0.5),                # unary minus inside the exponent
    ("m09", "100-40-30", 30.0),           # - is left associative
    ("m10", "7//2", 3.0),
    ("m11", "7%3", 1.0),
    ("m12", "10/4", 2.5),
    ("m13", "1/3", 0.3333333333333333),
    ("m14", "1/3", 0.3333),               # outside tolerance
    ("m15", "0.1+0.2", 0.3),              # inside tolerance, float repr noise
    ("m16", "1000000*1.0000001", 1000000.0),   # inside the relative tolerance
    ("m17", "1000000*1.00001", 1000000.0),     # outside it
    ("m18", "1e3*2", 2000.0),
    ("m19", "2**0.5", 1.4142135623730951),
    ("m20", "-5 - -3", -2.0),
    ("m21", "x+1", 1.0),                  # a name is not arithmetic
    ("m22", "2+", 0.0),                   # syntax error
    ("m23", "1/0", 0.0),                  # division by zero
    ("m24", "__import__('os').getpid()", 1.0),  # a call is not arithmetic
    ("m25", "1e300**2", 1.0),             # overflow has no value to compare
    ("m26", "1e300*1e300", 1.0),          # inf compares equal to everything
    ("m27", "10**1000", 1.0),             # an exact integer too big to be a float
]


def export_math(path: Path) -> int:
    rows = ["id\texpression\tclaimed\tstatus\tvalue"]
    for cid, expr, claimed in MATH:
        v = verify.math_holds(expr, claimed)
        value = "" if v.status == verify.UNVERIFIABLE else "%.17g" % float(v.evidence)
        rows.append("\t".join([cid, esc(expr), "%.17g" % claimed, v.status, value]))
    path.write_text("\n".join(rows) + "\n")
    return len(MATH)


# --- check_quote (the text pipeline, with the network mocked out) ---------
# quote_on_page is a fetch followed by strip-tags, normalise, substring. Only
# the fetch needs the network, so the part worth reimplementing is exercised
# against local fixtures through a mock transport.
TEXT = [
    ("t01", "page.html", "The quick brown fox jumps over the lazy dog."),
    ("t02", "page.html", "Verification confirms that the evidence a claim rests on is real"),
    ("t03", "page.html", "VERIFICATION   CONFIRMS\nthat the evidence"),
    ("t04", "page.html", "this sentence lives only inside the script block"),
    ("t05", "page.html", "marker-only-in-css"),
    ("t06", "page.html", "The quick red fox jumps over the lazy dog."),
    ("t07", "page.html", "Punctuation matters because a quote is a quote."),
    ("t08", "page.html", "the fox"),
    ("t09", "plain.txt", "No language model is involved in any verdict."),
    ("t10", "plain.txt", "every result is A STATUS, a method"),
    ("t11", "plain.txt", "no language model is involved in any verdict"),
]


def export_text(path: Path) -> int:
    fixtures = {p.name: p.read_text() for p in (OUT / "fixtures").iterdir()}

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.lstrip("/")
        return httpx.Response(200, text=fixtures[name],
                              headers={"content-type": "text/html"})

    rows = ["id\tfixture\tneedle\tstatus"]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        for cid, fixture, needle in TEXT:
            v = verify.quote_on_page(needle, f"https://fixture.invalid/{fixture}",
                                     client=client)
            rows.append(f"{cid}\t{fixture}\t{esc(needle)}\t{v.status}")
    path.write_text("\n".join(rows) + "\n")
    return len(TEXT)


# --- check_repo -----------------------------------------------------------
# Directory cases are either capped at max_hits or find nothing, so a stray
# __pycache__ entry cannot move the count. Single file cases are exact.
REPO = [
    ("r01", "math_holds", "src", False),
    ("r02", "zzz_no_such_token_zzz", "src", False),
    ("r03", "def check_", "src/groundcheck/server.py", False),
    ("r04", "asdict(self)", "src/groundcheck/verify.py", False),  # literal, not a group
    ("r05", "REFUTED = ", "src/groundcheck/verify.py", False),
    ("r06", "^class [A-Z]", "src/groundcheck/verify.py", True),   # regex, not a literal
    ("r07", "zzz_no_[a-z]+_zzz", "src/groundcheck/verify.py", True),
    ("r08", "anything", "src/does/not/exist", False),
    ("r09", "[", "src/groundcheck/verify.py", True),              # bad regex
    ("r10", "import httpx", "src/groundcheck/verify.py", False),
]


def export_repo(path: Path) -> int:
    rows = ["id\tpattern\tpath\tregex\tstatus\thits"]
    for cid, pattern, rel, is_regex in REPO:
        v = verify.repo_contains(pattern, str(ROOT / rel), is_regex=is_regex)
        hits = 0 if not v.evidence else len(v.evidence.splitlines())
        rows.append(f"{cid}\t{esc(pattern)}\t{rel}\t{int(is_regex)}\t{v.status}\t{hits}")
    path.write_text("\n".join(rows) + "\n")
    return len(REPO)


def main() -> None:
    n_math = export_math(OUT / "cases_math.tsv")
    n_text = export_text(OUT / "cases_text.tsv")
    n_repo = export_repo(OUT / "cases_repo.tsv")
    print(f"cases_math.tsv: {n_math} cases")
    print(f"cases_text.tsv: {n_text} cases")
    print(f"cases_repo.tsv: {n_repo} cases")


if __name__ == "__main__":
    main()
