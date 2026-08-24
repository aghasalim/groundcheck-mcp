"""Draw the README figures by actually running the verifiers.

Nothing here is illustrative. Every verdict in the output figure is produced by
calling the same functions the MCP server exposes, on inputs chosen to exercise
each of the three outcomes. Network-dependent tools are attempted and reported as
attempted, so a run without connectivity is visibly a run without connectivity
rather than a quietly different picture.

    python scripts/make_figures.py
"""

from __future__ import annotations

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


def main() -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    print(f"-> {verdicts(FIGURES / 'verdicts.png').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
