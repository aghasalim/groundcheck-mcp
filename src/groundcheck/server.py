"""Groundcheck MCP connector.

Exposes the grounded verifiers in verify.py as MCP tools, so any MCP-speaking
assistant -- Claude, Gemini, or another -- can call them mid-answer. The tool
layer is deliberately thin: it validates inputs and forwards to verify.py, which
holds all the logic and has no MCP dependency, so the verification core is
testable without a running server.

Every tool returns the same shape -- {status, method, evidence, detail} -- so a
caller can treat "was this grounded" uniformly regardless of which check ran.
`status` is one of: checked (grounding confirmed against a real source),
refuted (source exists and contradicts the claim), unverifiable (no source, or
needs judgement this tool refuses to fake).
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import verify

mcp = MCPServer(
    "groundcheck",
    instructions=(
        "Grounded fact-checking with no LLM in the verification path. Use these "
        "tools to confirm that a claim's GROUNDING is real before stating it: "
        "that a quote is actually on the cited page, an arXiv id or DOI resolves, "
        "a code snippet really prints what it is said to, a pattern occurs in a "
        "repo, or an arithmetic result is correct. These tools verify grounding, "
        "not semantic truth: a 'checked' verdict means the evidence is real and "
        "says what was quoted, not that the underlying claim is correct."
    ),
)


@mcp.tool()
def check_quote(quote: str, url: str) -> dict:
    """Verify that an exact quote appears on a web page.

    Fetches `url` and confirms `quote` is present in its visible text. Use before
    attributing a statement to a source. Returns refuted if the page loads but
    the quote is absent -- i.e. the attribution is fabricated.
    """
    return verify.quote_on_page(quote, url).dict()


@mcp.tool()
def check_citation(identifier: str) -> dict:
    """Verify that an arXiv id or DOI actually resolves, and return its real title.

    Pass an arXiv id (e.g. '2412.18004' or 'arXiv:2412.18004') or a DOI. A
    'checked' verdict includes the genuine title, so a plausible-looking but
    fabricated identifier is caught as 'refuted'.
    """
    return verify.citation_resolves(identifier).dict()


@mcp.tool()
def check_code(snippet: str, expected_output: str) -> dict:
    """Run a self-contained Python snippet and confirm its stdout contains
    `expected_output`.

    WARNING: this executes the code. Only pass code you would run yourself; it is
    not sandboxed from the network or filesystem. Returns refuted if the snippet
    errors or prints something other than claimed.
    """
    return verify.code_prints(snippet, expected_output).dict()


@mcp.tool()
def check_repo(pattern: str, path: str, regex: bool = False) -> dict:
    """Confirm that a string (or regex) actually occurs in files under a local path.

    Use to verify a claim about what a codebase or document contains -- 'the
    README reports 0.9086', 'the function is defined in src/' -- rather than
    trusting the assertion. Returns the real matching lines.
    """
    return verify.repo_contains(pattern, path, is_regex=regex).dict()


@mcp.tool()
def check_math(expression: str, claimed_result: float) -> dict:
    """Confirm an arithmetic expression equals a claimed result.

    Evaluates `expression` (numeric literals and + - * / // % ** only) and
    compares to `claimed_result`. Catches arithmetic a model asserted without
    computing.
    """
    return verify.math_holds(expression, claimed_result).dict()


def main() -> None:
    """Entry point: serve over stdio, the transport every MCP host supports."""
    mcp.run()


if __name__ == "__main__":
    main()
