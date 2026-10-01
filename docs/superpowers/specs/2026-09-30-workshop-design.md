# Workshop: building an approval boundary in LangGraph, live

Status: draft for owner review, 2026-09-30. Companion to `2026-09-29-agentops-design.md`; recorded
after the cookbook exists.

## 1. Why this exists

Deployed engineers run workshops for customer teams. A recording of me teaching the hardest part
of AgentOps to other engineers is the closest public evidence I can produce of that skill, and it
is the artifact a hiring manager can watch in fifteen minutes.

## 2. Audience and outcome

Engineers who have built a LangGraph agent and are about to let it change something real. By the
end they can add an approval interrupt, resume without redoing work, make the side effect
idempotent, and debug a bad run by forking from a checkpoint.

## 3. Format

Fifteen minutes, screen and voice, one take per section, edited together. Recorded by you; I
write the script, the code each section starts from, and the slides.

| Minute | Section | On screen |
|---|---|---|
| 0 to 1 | The problem: the first wrong action is the expensive one | One slide, then AgentOps's graph diagram |
| 1 to 4 | Add `interrupt()` to a three-node graph and resume with `Command` | Editor and terminal, built live from a starter file |
| 4 to 7 | Kill the process mid-apply and watch the change happen twice; then fix it with an idempotency key | Terminal split with the ledger |
| 7 to 10 | Reject and edit, and why an edit is re-validated by the graph | The operator console |
| 10 to 13 | The red-herring scenario: fork from the hypothesis checkpoint and find the real cause | Console timeline, both branches |
| 13 to 15 | What I would check before shipping this to a customer, and where to find the code | One slide with the checklist and links |

## 4. Deliverables

- `workshop/` in AgentOps: the starter file for each section, the finished file, the slides as
  Markdown, and the script.
- The recording on YouTube or LinkedIn, your choice, linked from the AgentOps README and the
  portfolio site.
- A one-page handout (the checklist from minute 13) that stands on its own.

## 5. Script principles

- Every line of code typed on screen is in the starter or finished file, so a viewer can follow
  along exactly.
- Each section ends with something visibly working, never a promise.
- The script is in my voice: plain, confident, specific. It says what I learned building AgentOps,
  including what I got wrong first.
- Nothing proprietary from my day job appears, including in the data-pipeline scenarios.

## 6. Dependencies

The cookbook (for sections 2 and 3), the operator console (sections 4 and 5), and a quiet
afternoon with a decent microphone. If the console is not ready, sections 4 and 5 use the CLI and
the recording ships anyway.
