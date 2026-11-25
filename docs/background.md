# Background

## Why this, and why it's hard

Almost every "fact-check" built into an assistant today ends up asking *a
second model* whether the first one was right. That doesn't really verify
anything. The checker hallucinates too, so the error just ends up in a
different place. Checking against reality instead of another model's opinion
is the hard part, and few tools attempt it. That's the only thing this one
does.

I want to be careful about scope, since over-claiming would undercut the whole
idea. Groundcheck confirms that the *evidence* a claim rests on is real and
says what it's quoted to say. It doesn't judge whether a claim is semantically
true. "This quote is on the cited page" is checkable. "The page's argument is
correct" isn't, and pretending won't change that. Every tool returns one of
three verdicts. When it can't tell, it says `unverifiable`.

## It caught a mistake in its own author's work

I wrote `check_citation` because made-up arXiv ids that looked plausible kept
slipping into research write-ups. They look right and resolve to nothing.
`check_math` exists because `3.7 × 1400` was written as `8880` in a hardware
deck. It's actually 5180. `check_repo` grew out of a claim-checker for my
profile README that compares every quoted number with the repo it came from.
So each tool started as a mistake that really happened.

One thing surprised me. While testing, I assumed arXiv `2606.01992` was
fabricated and expected `refuted`. The tool returned `checked`. It was right
and I was wrong, because it's a real June-2026 paper. The verifier caught my
own bad assumption, and that's exactly why I wanted verification grounded in a
source.
