"""Draw the README figures by actually running the verifiers.

Nothing here is illustrative. Every verdict in the output figure is produced by
calling the same functions the MCP server exposes, on inputs chosen to exercise
each of the three outcomes. Network-dependent tools are attempted and reported as
attempted, so a run without connectivity is visibly a run without connectivity
rather than a quietly different picture.

    python scripts/make_figures.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
FIGURES = ROOT / "docs"

sys.path.insert(0, str(ROOT / "src"))
from groundcheck import verify  # noqa: E402

STATUS_COLOURS = {
    "checked": "#1a9850",
    "refuted": "#b2182b",
    "unverifiable": "#f4a582",
    "error": "#7f7f7f",
}


def run_cases() -> list[tuple[str, str, dict]]:
    """Exercise every verifier, including the failure paths."""
    cases: list[tuple[str, str, dict]] = []

    def add(tool: str, description: str, verdict) -> None:
        cases.append((tool, description, verdict.dict()))

    add("check_math", "3.7 x 1400 = 8880  (the deck's claim)",
        verify.math_holds("3.7*1400", 8880))
    add("check_math", "3.7 x 1400 = 5180  (the truth)",
        verify.math_holds("3.7*1400", 5180))
    add("check_repo", "'math_holds' appears in src/",
        verify.repo_contains("math_holds", str(ROOT / "src")))
    add("check_repo", "'quantum_flux' appears in src/",
        verify.repo_contains("quantum_flux", str(ROOT / "src")))
    add("check_code", "print(2+2) outputs 4",
        verify.code_prints("print(2+2)", "4"))
    add("check_code", "print(2+2) outputs 5",
        verify.code_prints("print(2+2)", "5"))
    try:
        add("check_citation", "arXiv 1706.03762 resolves",
            verify.citation_resolves("1706.03762"))
        add("check_citation", "arXiv 9999.99999 resolves",
            verify.citation_resolves("9999.99999"))
    except Exception as exc:  # offline run
        cases.append(("check_citation", "network unavailable",
                      {"status": "error", "method": "citation_resolves",
                       "evidence": type(exc).__name__, "detail": str(exc)[:60]}))
    return cases


def verdicts(out: Path) -> Path:
    """Real verdicts from a real run, including the ones that refute."""
    cases = run_cases()

    figure, ax = plt.subplots(figsize=(12, 0.62 * len(cases) + 1.6))
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, len(cases) + 1)

    for index, (tool, description, result) in enumerate(reversed(cases)):
        y = index + 0.5
        colour = STATUS_COLOURS.get(result["status"], "#7f7f7f")
        ax.add_patch(plt.Rectangle((0.005, y - 0.32), 0.115, 0.64,
                                   color=colour, alpha=0.9))
        ax.text(0.0625, y, result["status"], ha="center", va="center",
                fontsize=9, color="white", fontweight="bold", family="monospace")
        ax.text(0.135, y, tool, va="center", fontsize=9, family="monospace",
                color="0.15")
        ax.text(0.30, y, description, va="center", fontsize=9, color="0.3")
        evidence = str(result.get("detail") or result.get("evidence") or "")
        evidence = evidence.split("\n")[0][:58]
        ax.text(0.66, y, evidence, va="center", fontsize=8, family="monospace",
                color="0.45")

    ax.text(0.005, len(cases) + 0.45,
            "verdict        tool             input"
            "                              what it found",
            fontsize=8, family="monospace", color="0.5")
    ax.set_title(
        "Every row is a live call to the same function the MCP server exposes.\n"
        "The refutations are real refutations, not illustrations of one.",
        fontsize=10, pad=14,
    )
    figure.tight_layout()
    figure.savefig(out, dpi=110, bbox_inches="tight")
    plt.close(figure)
    return out


def case_corpus(out: Path) -> Path:
    """Verdict split of the tracked case tables, counted straight from the TSVs."""
    tables = [("check_math", "cases_math.tsv"), ("check_repo", "cases_repo.tsv"),
              ("check_quote", "cases_text.tsv")]
    order = ["checked", "refuted", "unverifiable"]
    figure, ax = plt.subplots(figsize=(7, 2.8))
    for row, (tool, name) in enumerate(reversed(tables)):
        with open(ROOT / "verify" / name, newline="") as fh:
            statuses = [r["status"] for r in csv.DictReader(fh, delimiter="\t")]
        left = 0
        for status in order:
            n = statuses.count(status)
            if n:
                ax.barh(row, n, left=left, color=STATUS_COLOURS[status], height=0.6)
                ax.text(left + n / 2, row, str(n), ha="center", va="center",
                        color="white", fontsize=9, fontweight="bold")
            left += n
        ax.text(left + 0.4, row, f"{len(statuses)} cases", va="center", fontsize=8, color="0.4")
    ax.set_yticks(range(len(tables)))
    ax.set_yticklabels([f"{t}\n{n}" for t, n in reversed(tables)], family="monospace", fontsize=8)
    ax.set_xlabel("cases")
    ax.set_xlim(0, 31)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    handles = [plt.Rectangle((0, 0), 1, 1, color=STATUS_COLOURS[st]) for st in order]
    ax.legend(handles, order, ncol=3, frameon=False, fontsize=8, loc="lower right", bbox_to_anchor=(1, 1))
    figure.tight_layout()
    figure.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(figure)
    return out


def harness(out: Path) -> Path:
    """How verify/verify.sh holds the one Python implementation to account."""
    figure, ax = plt.subplots(figsize=(9, 4.6))
    ax.axis("off")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 7)

    def box(x, y, w, h, text, fc="#f2f2f2", ec="0.55", size=8, mono=False, color="0.15"):
        ax.add_patch(plt.Rectangle((x, y), w, h, facecolor=fc, edgecolor=ec, lw=0.8))
        ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=size,
                color=color, family="monospace" if mono else None)

    def arrow(x0, y0, x1, y1):
        ax.annotate("", (x1, y1), (x0, y0),
                    arrowprops=dict(arrowstyle="->", color="0.45", lw=0.8))

    box(0.1, 4.6, 2.2, 1.0, "src/groundcheck/verify.py\nthe one implementation", mono=True)
    box(0.1, 2.9, 2.2, 1.0, "verify/export_cases.py\nfreezes its verdicts", mono=True)
    arrow(1.2, 4.6, 1.2, 3.9)
    tables = ["cases_math.tsv  27", "cases_repo.tsv  10", "cases_text.tsv  11"]
    for i, t in enumerate(tables):
        box(0.1, 1.9 - i * 0.6, 2.2, 0.5, t, fc="white", mono=True)
    arrow(1.2, 2.9, 1.2, 2.4)

    checks = [
        ("SQL", "README case summary = GROUP BY over the tables"),
        ("C", "re-implements the check_math grammar, 27 verdicts"),
        ("Go", "drives the real MCP server over stdio, replays cases"),
        ("R", "check_repo verdicts and hit counts, 10 cases"),
        ("Rust", "check_quote text pipeline, 11 cases + rewrap fuzz"),
        ("Node", "random-expression fuzz against check_math"),
    ]
    for i, (lang, what) in enumerate(checks):
        y = 6.0 - i * 1.0
        box(3.2, y, 0.8, 0.6, lang, fc="#4d4d4d", ec="#2b2b2b", color="white", size=9)
        ax.text(4.15, y + 0.3, what, va="center", fontsize=8, color="0.25")
        arrow(2.3, 1.4, 3.2, y + 0.3)
        arrow(8.0, y + 0.3, 8.35, 3.5)

    box(8.35, 3.75, 1.55, 0.9, "all agree\nCI passes", fc=STATUS_COLOURS["checked"],
        ec="#0f6b33", color="white", size=9)
    box(8.35, 2.35, 1.55, 0.9, "any differs\nbuild stops", fc=STATUS_COLOURS["refuted"],
        ec="#7f0f20", color="white", size=9)
    ax.text(0.1, 0.05, "CI also flips m04 (2+3*4 = 14) to refuted and requires the harness to fail. "
            "No language model in any step.", fontsize=8, color="0.4")
    ax.set_title("verify/verify.sh: every published verdict recomputed in six other languages",
                 fontsize=10, loc="left")
    figure.tight_layout()
    figure.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(figure)
    return out


def main() -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    print(f"-> {verdicts(FIGURES / 'verdicts.png').relative_to(ROOT)}")
    print(f"-> {case_corpus(FIGURES / 'cases.png').relative_to(ROOT)}")
    print(f"-> {harness(FIGURES / 'harness.png').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
