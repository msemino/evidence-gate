# evidence-gate

Fail the build when a published page claims something the repository does not back.

Standard library only. No install step, no dependency supply chain, one file.

```bash
python evidence_gate.py --rules rules.toml
```

---

## Why this exists

When a language model writes documentation, the sentences that go wrong are not the vague
ones. They are the **confident, checkable** ones, and they read exactly like the true
sentences around them:

- **A test suite nobody wrote.** A test file name, a coverage figure and a latency, written
  with the same fluency as everything else — sometimes with *"measured, not estimated"*
  beside them. Pages built from one template carry the same invented file all at once.
- **A "View the code" button** over a repository that publishes only a `LICENSE` and a
  `README`. An architecture write-up is real work and sells as such — what breaks trust is
  letting a reader infer an implementation, then losing them on the next click.
- **A detail that must stay private**, kept out of the landing page and the CV, surviving
  in a README or a code comment. *A rule applied only where people look is a coat of paint,
  not a rule.*
- **A stale local clone.** Documenting from it describes software that no longer exists.
  And `raw.githubusercontent.com` cheerfully returns HTTP 200 for branches that never existed.

"No source in the repository, it does not get written" is a good rule, and on its own it
fails: enforcing it depends on a person reading every line of every page, every time.

**Writing the rule down is not enough. This makes the build enforce it.**

---

## What it checks

| Check | Fails when | Default |
|---|---|---|
| `denylist` | A term that must never reach a public surface appears on one | FAIL |
| `path-claim` | The page names a file path that is not in the repo | FAIL |
| `test-claim` | The page names a test that appears in no source file | FAIL |
| `code-promise` | The page says "view the code" and the repo publishes almost none | FAIL |
| `measured-claim` | A number is presented as measured with no source marker | WARN |

Two deliberate calibrations, both of them lessons rather than conveniences:

- **A bare filename warns; a path with a directory fails.** `tests/test_robustness.py` can only be
  a file in this repo. `whatsapp-web.js` might be a library. The dividing line is how ambiguous the
  token is — not a hand-maintained list of exceptions, which is the thing that rots.
- **A sentence in the negative is not a claim.** *"There is no coverage measurement in this
  repository, so this page will not quote you one"* is exactly the writing the rule wants. A gate
  that punishes it teaches you to stop being explicit about your ceilings.

To mark a number as sourced, put the source next to it:

```html
<!-- source: runs/2026-08-04-rtx.json -->
<p>Measured throughput: 10.5 tok/s.</p>
```

---

## It verifies against the remote, not your disk

Every target is fetched fresh from `origin` before checking. That is not caution: a clone on disk
that is behind validates a page while it lies. Whichever branch the remote hands back is the one that counts, so `main` versus
`master` never has to be configured.

Pages can be local files (checked before they ship) or URLs (checked as actually served — what a
reader really clicks).

---

## Running it

```bash
cp rules.example.toml rules.toml     # then edit
python evidence_gate.py --rules rules.toml
python evidence_gate.py --rules rules.toml --only my-project --json report.json
python evidence_gate.py --page landing/index.html --repo ../my-repo
```

Exit code is `1` if anything reached FAIL, so it drops into CI unchanged. Copy
`templates/evidence-gate.yml` into the repository that holds your pages — that is where the gate
belongs, because that is where a page can start claiming something.

It lives in `templates/` rather than in this repository's own `.github/workflows/` for a reason
worth naming: it needs a `rules.toml`, and `rules.toml` is gitignored here. Left as an active
workflow it failed on every push — a permanently red check on the project whose entire argument is
that a green check should mean something. This repository's own CI (`.github/workflows/ci.yml`)
runs the fixture suite instead, which is the part that can actually be verified here.

`rules.toml` is gitignored. A denylist is a list of the things you do not publish, so committing
it publishes them.

---

## Tested in both directions

```bash
python tests/test_gate.py
```

Two fixtures. The red one reproduces each failure mode and must trip all five checks and exit `1`.
The green one is the honest version of the same page — correct hardware phrasing, a file that
exists, a sourced number, a ceiling declared out loud — and must pass in silence.

The second fixture is the one that matters. A check only ever seen passing tells you nothing; a
check only ever seen failing tells you nothing either. You need to know it can tell them apart.

---

## What this does not prove

This verifies **verifiable claims**. It reads a page, extracts the things that can be checked
against a working tree, and checks them.

It cannot tell you whether a sentence is true. *"Designed for high availability"* passes, because
there is nothing in it to check. A number with a `<!-- source: -->` marker passes because the
marker is there — the gate does not open the file and re-derive the number.

So it closes one specific hole: **the confident, checkable, fabricated detail** — the test name,
the file path, the measured figure, the promise of code. That is the hole I actually fell into, and
it is the one an LLM writing your copy will find again, because the failure mode is not malice.
It is completion. Text flows, one more sentence sounds right, and the sentence is furniture.

Everything else still needs a human who is willing to be wrong.

---

## License

MIT.
