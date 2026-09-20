# Development workflow

Two rules govern how this project gets built. They are not suggestions — a phase that skips them is
not finished, regardless of whether the feature appears to work.

> **1. Phases are strictly sequential.** No work begins on phase N+1 until phase N has passed its
> exit gate. No "I'll just scaffold the next bit while I'm here."
>
> **2. Every phase runs the loop until it comes out clean:**
> **code → tests → code review → fix → repeat.**

---

## The loop

Inside a phase, work proceeds one section at a time (the numbered sections in each phase document),
and each section runs the full loop before the next one starts.

```
   ┌─────────────────────────────────────────────┐
   │                                             │
   ▼                                             │
 code  ──►  tests  ──►  code review  ──►  fix ───┘
                │              │
                │              │   no findings left
                ▼              ▼
             failing?      ───────►  section done
             fix, rerun
```

### 1. Code

Implement one section from the phase's todo list. Tick items as they land. Resist pulling work
forward from a later section — a section that grows halfway into the next one can't be reviewed
cleanly, and the review step is where this workflow earns its keep.

### 2. Tests

Write the tests named in the phase document's test table, then:

```bash
docker compose exec api pytest                    # all
docker compose exec api pytest tests/test_x.py    # one file
docker compose exec api pytest -k concurrent      # one case
```

Tests are written **in the same pass as the code**, not batched up at the end of the phase. A phase
whose tests all arrive on the last day is a phase whose tests were written to match whatever the
code happened to do.

Green means green. A skipped or xfailed test is a finding, not a pass — either fix it or write down
in the phase doc why it's deliberately deferred.

### 3. Code review

```
/code-review high          # correctness bugs + reuse / simplification findings
/security-review           # before closing any phase that touches auth, scoping, or deployment
```

Review the diff for the section just written. `/security-review` is mandatory for Phase 1 (auth and
the `scoped()` helper) and Phase 4 (production config, secrets, nginx), and worth running any time
an endpoint's ownership checks change.

### 4. Fix

Every finding gets one of two outcomes — fixed, or written down in the phase document with the
reason it was accepted. Nothing gets silently dropped. Then re-run tests and re-review.

**Leave the loop when a review pass produces no findings above `low`, and the suite is green.**

That threshold is a revision, made during phase 1.1 after six review passes on the scaffold
returned 9 → 7 → 3 → 2 → 3 findings without converging. The passes were not spinning: they caught
an inverted timezone example, a `pg_isready` race that would have flaked every clean rebuild, and a
missing `vite-env.d.ts`. But each new pass kept surfacing *preventive* low-severity items about code
that did not exist yet, so a strict "zero findings" bar does not terminate on a large,
documentation-heavy diff.

So: **every `medium` or above is fixed before the section closes.** A `low` is fixed when it is
cheap, and otherwise written into the phase document with the reason — the same "fixed or recorded,
never silently dropped" rule as before. Sections carrying real logic — auth, ownership scoping,
session state, analytics bucketing — hold to the strict bar: loop until a pass returns nothing at
all.

---

## Exit gate

A phase is done when all of the following hold. Check them in order; each depends on the one above.

- [ ] Every todo item in the phase document is ticked, or explicitly struck with a written reason
- [ ] `pytest` is fully green — no skips, no xfails, no "flaky, just rerun it"
- [ ] A final `/code-review high` over the whole phase diff returns no unaddressed findings
- [ ] `/security-review` run and clean, for phases touching auth, data scoping, or deployment
- [ ] Every box in the phase's **Definition of done** verified by hand in a browser — not inferred
      from passing tests
- [ ] The app starts clean from scratch: `docker compose down -v && docker compose up --build`,
      then migrate, register a new user, and walk the phase's main flow
- [ ] Work committed on a phase branch and merged

Only then does the next phase begin.

### Why the clean-volume check is on the list

A phase is easy to "finish" against a database that accumulated its schema through the phase's own
trial and error. Tearing the volume down and rebuilding is what catches the migration that only
works because of a column you added by hand, or the seed the app silently depends on. Do it every
phase, not just at the end.

---

## Git

One branch per phase, `phase-1-foundation` … `phase-4-goals-production`, off `main`. Commit at the
end of each *section*, not each phase — a phase-sized commit is unreviewable and impossible to
bisect. Merge to `main` only after the exit gate passes, so `main` always holds a working app.

Commit messages carry **no `Co-Authored-By` or `Claude-Session` trailers** — the message ends with its last line of actual content.

---

## When a phase's plan turns out to be wrong

It will happen — a design decision meets reality and loses. Do not quietly deviate. Update the phase
document with what changed and why, then continue. The documents are the record of what was actually
built; a plan that silently drifts from the code is worse than no plan, because the next session
trusts it.

If the change affects a later phase's assumptions, update that phase's document in the same pass,
while the reasoning is fresh.
