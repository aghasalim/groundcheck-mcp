# Groundcheck — verification with no model in the loop

[![ci](https://github.com/aghasalim/groundcheck-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/aghasalim/groundcheck-mcp/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

An MCP connector that checks whether a claim's **grounding is real** — the quote
is actually on the page, the arXiv id resolves, the code prints what it's said
to, the number is right — with **no language model anywhere in the verification
path**. Works in any MCP host: Claude, Gemini, or another.


---

## Abstract

Assistant "fact-checking" almost always means asking a second model whether the
first was right. That relocates the error rather than removing it, because the
checker hallucinates too. This is an MCP connector that verifies grounding against
reality instead: five tools that fetch a page, resolve an identifier, execute a
snippet, grep a codebase or evaluate an expression, and return one of three
verdicts with the concrete evidence attached.

The scope is deliberately narrow and stated as such. Groundcheck confirms that the
evidence a claim rests on is real and says what it is quoted to say. It does not
judge whether a claim is semantically true — "this quote is on the cited page" is
checkable, "the page's argument is correct" is not, and it returns `unverifiable`
rather than guessing.

No language model is involved in any verdict.

**Contributions.** (i) Verification grounded in sources rather than in a second
model's opinion. (ii) A three-verdict contract with an explicit `unverifiable`, so
refusal is a first-class outcome. (iii) Auditable results — every verdict carries
the quote, stdout, matching line or computed value it was based on.

---

## 1. Why this, and why it's hard

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

```mermaid
flowchart LR
    A["Assistant makes<br/>a claim"] --> M{"Groundcheck<br/>MCP server"}

    M --> Q["check_quote<br/><i>fetch the page</i>"]
    M --> C["check_citation<br/><i>query arXiv / Crossref</i>"]
    M --> X["check_code<br/><i>run in a subprocess</i>"]
    M --> R["check_repo<br/><i>grep the files</i>"]
    M --> T["check_math<br/><i>evaluate an AST</i>"]

    Q & C --> NET[("the live web")]
    X & R --> LOCAL[("local filesystem<br/>and interpreter")]
    T --> PURE[("arithmetic")]

    NET & LOCAL & PURE --> V{"verdict"}
    V --> OK["checked"]
    V --> NO["refuted"]
    V --> UNK["unverifiable"]

    classDef src fill:#4d4d4d,stroke:#2b2b2b,color:#fff
    classDef good fill:#1a9850,stroke:#0f6b33,color:#fff
    classDef bad fill:#b2182b,stroke:#7f0f20,color:#fff
    classDef warn fill:#f4a582,stroke:#c06a4f,color:#000
    class NET,LOCAL,PURE src
    class OK good
    class NO bad
    class UNK warn
```

No box in that diagram is a language model. Every verdict terminates at something
that can be looked up, executed or computed.

## 2. The tools

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

### 2.1 A live run

![real verdicts from a live run](docs/verdicts.png)

Every row above is an actual call to the same function the server exposes,
including the network-dependent arXiv lookups. The refutations are real
refutations rather than illustrations of one — the first row is the `3.7 x 1400`
error from section 3, reproduced.

## 3. It caught a mistake in its own author's work

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

## 4. Use it

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

## 5. Security

`check_code` **executes the code you give it** in a subprocess. It uses list-form
`subprocess` (no shell, so nothing to inject) and kills on timeout, but it is
**not** sandboxed from the network or filesystem. Only pass code you would run
yourself. The other four tools are read-only (HTTP GET, file read, arithmetic).

## 6. Limitations

- **Grounding, not truth.** By design — see Scope above.
- **Quote matching is exact (whitespace-normalised).** A paraphrase that means
  the same thing returns `refuted`, because "means the same" needs a judge and a
  judge is what this tool refuses to be. Match the literal text.
- **JS-rendered pages.** `check_quote` reads the served HTML; a quote injected by
  client-side JavaScript won't be found. It fails safe (`refuted`), never a false
  `checked`.
- **arXiv/Crossref only** for citations. Other registries aren't wired up yet.

## 7. Licence

MIT — see [LICENSE](LICENSE).
