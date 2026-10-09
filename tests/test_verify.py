"""Tests for the verification core -- the instrument.

The offline verifiers (code, repo, math) are tested against ground truth. The
network ones (quote, citation) are tested against a mock HTTP transport, so the
suite is deterministic and runs in CI with no network -- a verifier whose own
tests depend on a flaky external call is not one you would trust.

The load-bearing property throughout: a false claim must return `refuted`, not
`checked`. A verifier that says "checked" too easily is worse than none, because
it launders a hallucination into a confirmation.
"""
import httpx

from src.groundcheck import verify
from src.groundcheck.verify import CHECKED, REFUTED, UNVERIFIABLE


# --- offline: math --------------------------------------------------------
def test_math_true_is_checked():
    assert verify.math_holds("3.7 * 1400", 5180).status == CHECKED


def test_math_false_is_refuted_not_checked():
    """The exact slide error from the drone deck: 3.7*1400 is 5180, not 8880."""
    v = verify.math_holds("3.7 * 1400", 8880)
    assert v.status == REFUTED
    assert "5180" in v.evidence


def test_math_rejects_code_execution():
    """A safe calculator, not eval: names and calls must not evaluate."""
    assert verify.math_holds("__import__('os').getpid()", 1).status == UNVERIFIABLE


def test_math_overflow_is_unverifiable_not_checked():
    """Found by the differential test in verify/, not by me.

    An overflowing expression has no value to compare a claim against. It used
    to raise on `1e300**2`, and worse, `abs(inf - claimed) <= tol * inf` holds
    for every claimed, so `1e300*1e300` confirmed any claim at all.
    """
    for expression in ("1e300**2", "1e300*1e300", "10**1000"):
        assert verify.math_holds(expression, 1.0).status == UNVERIFIABLE


# --- offline: code --------------------------------------------------------
def test_code_matching_output_is_checked():
    assert verify.code_prints("print(2+2)", "4").status == CHECKED


def test_code_wrong_output_is_refuted():
    v = verify.code_prints("print(2+2)", "5")
    assert v.status == REFUTED and "4" in v.evidence


def test_code_that_errors_is_refuted_with_traceback():
    v = verify.code_prints("raise ValueError('boom')", "anything")
    assert v.status == REFUTED and "boom" in v.evidence


def test_code_timeout_is_unverifiable_not_hung():
    v = verify.code_prints("import time; time.sleep(30)", "x", timeout=1.0)
    assert v.status == UNVERIFIABLE


# --- offline: repo --------------------------------------------------------
def test_repo_finds_a_real_string(tmp_path):
    (tmp_path / "readme.md").write_text("private leaderboard 0.9086 measured\n")
    v = verify.repo_contains("0.9086", str(tmp_path))
    assert v.status == CHECKED and "0.9086" in v.evidence


def test_repo_missing_string_is_refuted(tmp_path):
    (tmp_path / "a.txt").write_text("nothing relevant here\n")
    assert verify.repo_contains("0.9999", str(tmp_path)).status == REFUTED


def test_repo_missing_path_is_unverifiable():
    assert verify.repo_contains("x", "/no/such/path/here").status == UNVERIFIABLE


# --- network, mocked: quote ----------------------------------------------
def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_quote_present_is_checked():
    c = _client(lambda req: httpx.Response(
        200, text="<html><body>the cat sat on the mat today</body></html>"))
    v = verify.quote_on_page("the cat sat on the mat", "https://x.test", client=c)
    assert v.status == CHECKED


def test_quote_absent_is_refuted():
    """The 'verified' clause that was never on the page -- caught."""
    c = _client(lambda req: httpx.Response(200, text="<html>unrelated text</html>"))
    v = verify.quote_on_page("a sentence that is not here at all", "https://x.test", client=c)
    assert v.status == REFUTED


def test_quote_matches_across_whitespace_and_tags():
    c = _client(lambda req: httpx.Response(
        200, text="<p>the cat\n   sat</p><p>on the mat</p>"))
    v = verify.quote_on_page("the cat sat on the mat", "https://x.test", client=c)
    assert v.status == CHECKED


def test_quote_matches_through_html_entities():
    """A real quote with an apostrophe, served as &#8217; and &amp;, still matches."""
    c = _client(lambda req: httpx.Response(
        200, text="<p>It&#8217;s the model&rsquo;s fault &amp; nobody else&#39;s</p>"))
    v = verify.quote_on_page("It\u2019s the model\u2019s fault & nobody else's",
                             "https://x.test", client=c)
    assert v.status == CHECKED


def test_quote_too_short_is_refused():
    c = _client(lambda req: httpx.Response(200, text="a b"))
    assert verify.quote_on_page("a b", "https://x.test", client=c).status == UNVERIFIABLE


def test_quote_http_error_is_unverifiable():
    c = _client(lambda req: httpx.Response(404, text="not found"))
    assert verify.quote_on_page("some long quote here", "https://x.test", client=c).status == UNVERIFIABLE


# --- network, mocked: citation -------------------------------------------
_ARXIV_HIT = ('<feed><entry><title>Correctness is not Faithfulness in RAG '
              'Attributions</title></entry></feed>')
_ARXIV_MISS = '<feed></feed>'


def test_real_arxiv_id_resolves_with_title():
    c = _client(lambda req: httpx.Response(200, text=_ARXIV_HIT))
    v = verify.citation_resolves("2412.18004", client=c)
    assert v.status == CHECKED and "Faithfulness" in v.evidence


def test_fabricated_arxiv_id_is_refuted():
    """The recurring failure: a plausible id that resolves to nothing."""
    c = _client(lambda req: httpx.Response(200, text=_ARXIV_MISS))
    assert verify.citation_resolves("2606.01992", client=c).status == REFUTED


def test_non_identifier_is_unverifiable():
    c = _client(lambda req: httpx.Response(200, text=""))
    assert verify.citation_resolves("just some words", client=c).status == UNVERIFIABLE


def test_doi_with_arxiv_like_digits_goes_to_crossref():
    """10.1145/3292500.3330701 contains "2500.33307"; it must not be sent to arXiv."""
    seen = []

    def handler(req):
        seen.append(req.url.host)
        if req.url.host == "api.crossref.org":
            return httpx.Response(200, json={"message": {"title": ["A KDD paper"]}})
        return httpx.Response(200, text=_ARXIV_MISS)

    v = verify.citation_resolves("10.1145/3292500.3330701", client=_client(handler))
    assert v.status == CHECKED and v.method == "citation_resolves[doi]"
    assert seen == ["api.crossref.org"]
