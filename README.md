# Groundcheck — verification with no model in the loop

[![ci](https://github.com/aghasalim/groundcheck-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/aghasalim/groundcheck-mcp/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

An MCP connector that checks whether a claim's **grounding is real** — the quote
is actually on the page, the arXiv id resolves, the code prints what it's said
to, the number is right — with **no language model anywhere in the verification
path**. Works in any MCP host: Claude, Gemini, or another.

## Why this, and why it's hard

Every "fact-check" built into an assistant today ultimately asks *a second model*
whether the first one was right. That doesn't verify anything — it relocates the
error, because the checker hallucinates too. The genuinely hard, under-attempted
thing is verification grounded in **reality** rather than in another model's
opinion. That's all this does, and it does only that.

**Scope, stated honestly, because over-claiming would defeat the point.**
Groundcheck confirms that the *evidence* a claim rests on is real and says what
it's quoted to say. It does **not** judge whether a claim is semantically true —
"this quote is on the cited page" is checkable; "the page's argument is correct"
is not, and no amount of pretending makes it so. Every tool returns one of three
verdicts, and it says `unverifiable` rather than guess:

| verdict | meaning |
|---|---|
| `checked` | the grounding was confirmed against a real source |
| `refuted` | the source exists and contradicts the claim (wrong number, missing quote, dead id, failing code) |
| `unverifiable` | no source, or it needs judgement this tool refuses to fake |

## The tools

| tool | verifies | how (no LLM) |
|---|---|---|
| `check_quote(quote, url)` | an exact quote is on a page | fetch the page, match the text |
| `check_citation(identifier)` | an arXiv id or DOI resolves | query arXiv / Crossref, return the real title |
| `check_code(snippet, expected_output)` | code prints what's claimed | run it in a subprocess, compare stdout |
| `check_repo(pattern, path)` | a string/regex is in a codebase | grep the files, return real matching lines |
| `check_math(expression, claimed_result)` | arithmetic is correct | evaluate an AST (no `eval`), compare |

Every result is `{status, method, evidence, detail}` — `evidence` is the concrete
thing found (the quote, the stdout, the matching line, the computed value), so a
verdict is auditable, not a black box.

## It caught a mistake in its own author's work

`check_citation` exists because fabricated-but-plausible arXiv ids kept slipping
into research write-ups — an id that looks right and resolves to nothing.
`check_math` exists because `3.7 × 1400` was written as `8880` in a hardware deck
(it's 5180). `check_repo` is the generalisation of a profile-README claim-checker
that verifies every quoted number against its source repo. Each tool is a failure
that actually happened, turned into a check.

There's one honest wrinkle worth reporting: while testing, I assumed arXiv
`2606.01992` was fabricated and expected `refuted` — the tool returned `checked`.
**The tool was right and I was wrong**: it's a real June-2026 paper. The verifier
did its job against my own bad assumption, which is the entire reason to ground
verification in a source rather than a hunch.

## Use it

```bash
pip install -e .          # or: pip install -r requirements.txt
python -m pytest tests/   # 18 tests, no network needed (mocked transport)
```

**Claude / Claude Code** — add to your MCP config:

```json
{
  "mcpServers": {
    "groundcheck": { "command": "python", "args": ["-m", "src.groundcheck.server"] }
  }
}
```

**Gemini CLI / any MCP host** — same stdio server; point your host's MCP config
at `python -m src.groundcheck.server`. MCP is the reason one connector serves
both.

## Security

`check_code` **executes the code you give it** in a subprocess. It uses list-form
`subprocess` (no shell, so nothing to inject) and kills on timeout, but it is
**not** sandboxed from the network or filesystem. Only pass code you would run
yourself. The other four tools are read-only (HTTP GET, file read, arithmetic).

## Limitations

- **Grounding, not truth.** By design — see Scope above.
- **Quote matching is exact (whitespace-normalised).** A paraphrase that means
  the same thing returns `refuted`, because "means the same" needs a judge and a
  judge is what this tool refuses to be. Match the literal text.
- **JS-rendered pages.** `check_quote` reads the served HTML; a quote injected by
  client-side JavaScript won't be found. It fails safe (`refuted`), never a false
  `checked`.
- **arXiv/Crossref only** for citations. Other registries aren't wired up yet.

## License

MIT — see [LICENSE](LICENSE).
