# Evaluation methodology for non-deterministic agents

Status: draft for owner review, 2026-09-30. Written alongside AgentOps increment 4, published with
increment 5.

## 1. Why this exists

LangChain's Deployed Engineer posting asks for "deep familiarity designing and implementing
evaluation methodologies for non-deterministic AI systems", and "evaluation strategy review" is
one of the things those engineers advise customers on. I have done this twice: Studbook's held-out
retrieval evaluation and AgentOps's scenario evaluation. The method is the same in both, and it
deserves to be written down once, as a method another team could adopt, with my two systems as the
worked examples.

## 2. Deliverables

1. `docs/eval-methodology.md` in AgentOps, about four pages.
2. A LinkedIn article adapted from it, shorter, with the two results tables.
3. A one-page checklist version used in the workshop handout and in customer conversations.

## 3. The method, as the document will state it

1. Decide what a wrong answer costs before choosing a metric. For Studbook, an invented citation
   cost more than a refusal. For AgentOps, a wrong remediation costs more than an escalation.
   The metrics follow from that, and so does the decision to reward "I do not know".
2. Build cases with known answers, and make most of them traps. If the obvious answer is always
   right, accuracy measures nothing. Studbook had three unanswerable questions; AgentOps has seven
   trap scenarios out of eight.
3. Split dev from held-out, pin the held-out bytes with a committed checksum, and score held-out
   only when a version is frozen. Steering a change on the held-out set is the most common way an
   evaluation lies to its owner.
4. Prefer deterministic evaluators. When ground truth exists in the case file, check it in code.
   Use a model judge only for what code cannot check, and anchor the judge to the case's ground
   truth so it grades correctness rather than style.
5. Compare against a baseline, not against nothing. A score alone invites the question "compared
   to what"; a delta against the naive approach answers it.
6. Repeat and report the spread. Three repetitions on a small set is the minimum; report mean and
   range, and say plainly when a difference is inside the noise.
7. Track per-case churn between versions: which cases got better, which got worse, which moved
   both ways. An improved mean that hides a regression is not an improvement.
8. Publish the failures. A results table with no wrong answers is either a toy or a lie.

## 4. Worked examples

- Studbook, from its README and `eval/`: held-out n=20 scored after tuning was frozen; answer
  correctness 1.30 to 1.60, over-refusal 41% to 18%, trap questions 3 of 3 with nothing invented
  at every scoring; `eval/compare.py` showing the last change made three questions better and none
  worse. Every number is copied from the Studbook README at the commit cited, never re-derived.
- AgentOps: the baseline against v1 (and v2) table from increment 4, with the per-scenario
  breakdown and the cases each version still gets wrong.

## 5. Writing rules

Plain, specific, first person, numbers before adjectives. Each principle gets one paragraph and
one concrete example from the two systems. No generic advice that does not come with a case where
I applied it and what happened.

## 6. Dependencies and timing

One day, after the first AgentOps experiment has numbers. Until then the AgentOps example section
is not written, rather than written with placeholders.

## 7. Note on Studbook's status

WorkHorse's STATUS.md (2026-09-29) lists Studbook as out of use. The document describes Studbook
as the reference implementation and measurement harness it remains, with its results as recorded,
and does not imply it is in daily use.
