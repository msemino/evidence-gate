# evidence-gate

Fail the build when a published page claims something the repository does not back.

Standard library only. No install step, no dependency supply chain, one file.

```bash
python evidence_gate.py --rules rules.toml
```

---

## Why this exists

On 2026-07-16 I audited my own portfolio and found that **seven of its landing pages described
test suites that do not exist**. The same invented `tests/test_robustness.py` appeared in six
repositories. All seven carried the same date — *"Measured 2026-07-07"* — with coverage figures and
latencies nobody had ever run. One page went as far as *"measured, not estimated."*

They had been live for **nine days**. During those nine days my CV and a cover letter linked to
those exact pages, explicitly inviting the reader to go and audit them.

The pages were generated in a batch from a template and nobody checked them line by line.

Three more things surfaced the same afternoon:

- **Three landings had a "View the code" button** pointing at repositories that publish only a
  `LICENSE` and a `README`. An architecture write-up is real work and sells as such — what breaks
  trust is letting a reader infer an implementation, then losing them on the next click.
- **A hardware detail leaked into seven public READMEs** while the CV and the landings used the
  careful phrasing. The rule existed. It was being enforced on two of the three surfaces. *A rule
  applied only where people look is a coat of paint, not a rule.*
- **A local clone was stale**, so documenting from it described software that no longer existed.
  And `raw.githubusercontent.com` cheerfully returns HTTP 200 for branches that never existed.

The fix that day was manual. This is the part that shouldn't be.

**The rule was already written down. Writing it down is what failed.**

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

Every target is fetched fresh from `origin` before checking. That is not caution, it is the second
half of the same incident: the clone on disk was behind, and a page validated against it would have
passed while lying. Whichever branch the remote hands back is the one that counts, so `main` versus
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

Exit code is `1` if anything reached FAIL, so it drops into CI unchanged. A workflow is in
`.github/workflows/evidence-gate.yml`.

`rules.toml` is gitignored. A denylist is a list of the things you do not publish, so committing
it publishes them.

---

## Tested in both directions

```bash
python tests/test_gate.py
```

Two fixtures. The red one reproduces each real mistake and must trip all five checks and exit `1`.
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
