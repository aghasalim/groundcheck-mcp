"""Grounded verification. No language model in any code path here, by design.

The whole point of this connector is that verification must not reduce to
asking a second model whether the first was right -- that only relocates the
error. So every function in this module checks a claim against *reality*: a live
HTTP fetch, a real subprocess, a filesystem grep, an arithmetic evaluation, a
registry lookup. If a verdict cannot be reached without judgement, the function
returns UNVERIFIABLE rather than guessing.

Scope, stated up front because over-claiming would defeat the purpose: these
functions check that a claim's *grounding is real* -- the quote is on the page,
the id resolves, the code prints what it is said to, the number is right. They
do NOT judge whether a claim is semantically true. "The quote appears on the
cited page" is checkable; "the page's argument is correct" is not, and pretending
otherwise is the exact failure this tool exists to avoid.
"""
from __future__ import annotations

import ast
import operator
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

CHECKED = "checked"          # grounding verified against a real source
REFUTED = "refuted"          # source exists and contradicts the claim
UNVERIFIABLE = "unverifiable"  # cannot be settled without judgement / source missing


@dataclass
class Verdict:
    status: str          # one of CHECKED / REFUTED / UNVERIFIABLE
    method: str          # exactly how it was checked, so the result is auditable
    evidence: str        # the concrete thing found (a quote, output, hit, value)
    detail: str = ""

    def dict(self) -> dict:
        return asdict(self)


# --- text normalisation ---------------------------------------------------
_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    """Collapse whitespace and lowercase, so a quote that differs only in line
    wrapping or spacing still matches. Deliberately does NOT strip punctuation:
    a quote is a quote."""
    return _WS.sub(" ", s).strip().lower()


# --- 1. quote appears on a page ------------------------------------------
def quote_on_page(quote: str, url: str, timeout: float = 20.0,
                  client: httpx.Client | None = None) -> Verdict:
    """Does `quote` actually appear in the text of `url`?

    Fetches the page, strips tags to visible text, and looks for the quote
    normalised for whitespace. This is the check that would have caught a
    'verified' clause that was never in the source.
    """
    if len(quote.strip()) < 8:
        return Verdict(UNVERIFIABLE, "quote_on_page",
                       "", "quote too short to match reliably (min 8 chars)")
    own = client is None
    client = client or httpx.Client(follow_redirects=True,
                                    headers={"user-agent": "groundcheck/1.0"})
    try:
        r = client.get(url, timeout=timeout)
        if r.status_code != 200:
            return Verdict(UNVERIFIABLE, "quote_on_page",
                           "", f"page returned HTTP {r.status_code}")
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", r.text,
                      flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        if _norm(quote) in _norm(text):
            return Verdict(CHECKED, "quote_on_page",
                           quote.strip(), f"found in {url}")
        return Verdict(REFUTED, "quote_on_page", "",
                       f"quote not present in the fetched text of {url}")
    except httpx.HTTPError as e:
        return Verdict(UNVERIFIABLE, "quote_on_page", "",
                       f"fetch failed: {type(e).__name__}")
    finally:
        if own:
            client.close()


# --- 2. an academic id resolves ------------------------------------------
def citation_resolves(identifier: str, timeout: float = 20.0,
                      client: httpx.Client | None = None) -> Verdict:
    """Does an arXiv id or DOI actually exist? Returns the real title.

    This is the check for the failure that recurred all over this author's own
    work: fabricated arXiv ids that look plausible and resolve to nothing.
    """
    own = client is None
    client = client or httpx.Client(follow_redirects=True,
                                    headers={"user-agent": "groundcheck/1.0"})
    ident = identifier.strip()
    try:
        m = re.search(r"(\d{4}\.\d{4,5})", ident)
        if m or ident.lower().startswith("arxiv"):
            arxiv_id = m.group(1) if m else ident.split(":")[-1]
            r = client.get("https://export.arxiv.org/api/query",
                           params={"id_list": arxiv_id, "max_results": 1},
                           timeout=timeout)
            titles = re.findall(r"<entry>.*?<title>(.*?)</title>", r.text, re.S)
            if r.status_code == 200 and titles:
                return Verdict(CHECKED, "citation_resolves[arxiv]",
                               _WS.sub(" ", titles[0]).strip(),
                               f"arXiv:{arxiv_id} resolves")
            return Verdict(REFUTED, "citation_resolves[arxiv]", "",
                           f"arXiv:{arxiv_id} returns no entry")
        if "/" in ident or ident.lower().startswith("10."):
            doi = ident.split("doi.org/")[-1].removeprefix("doi:").strip()
            r = client.get(f"https://api.crossref.org/works/{doi}",
                           timeout=timeout)
            if r.status_code == 200:
                title = (r.json().get("message", {}).get("title") or [""])[0]
                return Verdict(CHECKED, "citation_resolves[doi]",
                               title, f"DOI {doi} resolves")
            return Verdict(REFUTED, "citation_resolves[doi]", "",
                           f"DOI {doi} returns HTTP {r.status_code}")
        return Verdict(UNVERIFIABLE, "citation_resolves", "",
                       "not recognised as an arXiv id or DOI")
    except httpx.HTTPError as e:
        return Verdict(UNVERIFIABLE, "citation_resolves", "",
                       f"lookup failed: {type(e).__name__}")
    finally:
        if own:
            client.close()


# --- 3. code produces the claimed output ---------------------------------
def code_prints(snippet: str, expected: str, timeout: float = 15.0) -> Verdict:
    """Run `snippet` in a fresh subprocess; does its stdout contain `expected`?

    List-form subprocess (never shell=True), so there is no shell to inject
    into. It still *executes arbitrary code* -- that is inherent to checking
    'does this code do what it claims', and the README says plainly to run only
    code you would run yourself. Network is not sandboxed.
    """
    try:
        proc = subprocess.run([sys.executable, "-c", snippet],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Verdict(UNVERIFIABLE, "code_prints", "",
                       f"execution exceeded {timeout}s")
    out = proc.stdout
    if proc.returncode != 0:
        return Verdict(REFUTED, "code_prints", proc.stderr.strip()[:400],
                       f"exited {proc.returncode}, did not run cleanly")
    if _norm(expected) in _norm(out):
        return Verdict(CHECKED, "code_prints", out.strip()[:400],
                       "expected text found in stdout")
    return Verdict(REFUTED, "code_prints", out.strip()[:400],
                   "ran cleanly but stdout does not contain the expected text")


# --- 4. a pattern exists in a repo ---------------------------------------
def repo_contains(pattern: str, root: str, is_regex: bool = False,
                  max_hits: int = 5) -> Verdict:
    """Does `pattern` occur in any file under `root`? Returns real hits.

    The check for 'this number/claim is actually in the README/source', not
    merely asserted about it -- which is the discipline the author's own
    check_claims.py applies to one profile, generalised.
    """
    base = Path(root).expanduser()
    if not base.exists():
        return Verdict(UNVERIFIABLE, "repo_contains", "",
                       f"path does not exist: {root}")
    try:
        rx = re.compile(pattern if is_regex else re.escape(pattern))
    except re.error as e:
        return Verdict(UNVERIFIABLE, "repo_contains", "", f"bad regex: {e}")
    hits: list[str] = []
    files = [base] if base.is_file() else base.rglob("*")
    anchor = base.parent if base.is_file() else base
    for f in files:
        if not f.is_file() or ".git/" in str(f):
            continue
        try:
            for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f.relative_to(anchor)}:{i}: {line.strip()[:120]}")
                    if len(hits) >= max_hits:
                        break
        except (OSError, UnicodeDecodeError):
            continue
        if len(hits) >= max_hits:
            break
    if hits:
        return Verdict(CHECKED, "repo_contains", "\n".join(hits),
                       f"{len(hits)} hit(s)" + (" (capped)" if len(hits) >= max_hits else ""))
    return Verdict(REFUTED, "repo_contains", "",
                   f"pattern not found under {root}")


# --- 5. arithmetic is right ----------------------------------------------
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
        ast.USub: operator.neg, ast.UAdd: operator.pos, ast.FloorDiv: operator.floordiv}


def _reduce_ast(node):
    """Walk an arithmetic AST to a number. Only numeric literals and the
    operators in _OPS are permitted -- no names, no calls, no attribute access.
    This is the safe alternative to Python's builtin evaluator: 'math' can be
    checked without executing arbitrary code, and without a model doing mental
    arithmetic (itself a common hallucination)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_reduce_ast(node.left), _reduce_ast(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_reduce_ast(node.operand))
    raise ValueError("only numeric literals and + - * / // % ** are allowed")


def math_holds(expression: str, claimed: float, tol: float = 1e-6) -> Verdict:
    """Is `expression` actually equal to `claimed` (within tol)?"""
    try:
        actual = _reduce_ast(ast.parse(expression, mode="eval").body)
    except (ValueError, SyntaxError, ZeroDivisionError) as e:
        return Verdict(UNVERIFIABLE, "math_holds", "", f"cannot evaluate: {e}")
    if abs(actual - claimed) <= tol * max(1.0, abs(actual)):
        return Verdict(CHECKED, "math_holds", str(actual),
                       f"{expression} = {actual}")
    return Verdict(REFUTED, "math_holds", str(actual),
                   f"{expression} = {actual}, not {claimed}")
