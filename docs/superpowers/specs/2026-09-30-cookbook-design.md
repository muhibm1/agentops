# Cookbook: an approval boundary with idempotent side effects

Status: draft for owner review, 2026-09-30. Companion to `2026-09-29-agentops-design.md`; written
in AgentOps week two, after the crash-resume demo works.

## 1. Why this exists

Every team that puts an agent in front of a real system eventually writes the same three things:
a pause for a human, a resume that does not redo the work, and a side effect that must happen
exactly once even when the process dies halfway through it. LangGraph's documentation covers the
first two well. The third is spread across four pages (graph API re-execution, functional API
idempotency, the interrupts side-effect rule, fault tolerance), and as of 2026-09-29 there is an
open issue (langchain-ai/langgraph#8702) and an unmerged docs pull request (langchain-ai/docs#5666)
asking for exactly this guidance. No official example combines a fan-out, an approval interrupt,
an idempotent external mutation and a fork from a checkpoint.

The cookbook is that example, extracted from AgentOps until it fits in one file. It is the most
direct way I know to show LangChain's team that I understand their runtime at the level their
customers need, and an upstream contribution is evidence nobody can fake.

## 2. Deliverables

1. `examples/approval_boundary/` in AgentOps: one runnable Python file under 250 lines, a README,
   and a test that kills the process between approval and apply and asserts the effect landed
   once. No model call, so anyone can run it without a key.
2. A blog-style write-up in the AgentOps repository (`docs/cookbook-approval-boundary.md`) and a
   LinkedIn article adapted from it.
3. An upstream contribution, proposed rather than assumed: first a comment on #8702 linking the
   example and offering it, then a pull request in whatever form the maintainers ask for (a docs
   page, an example notebook, or a section in the fault-tolerance page).

## 3. What the example teaches, in order

1. A node re-runs from its start on resume and on retry, and a checkpoint is never written
   mid-node. Show it: a counter outside the graph increments twice for one logical execution.
2. Therefore the side effect needs a key derived from durable state, not from the process:
   `sha256(thread_id + canonical(proposal))`.
3. Therefore the executor reads before it writes, and checks the external system's own record,
   because absence of local state is not proof the effect did not happen.
4. `durability="sync"` on the step before the side effect, and what it costs.
5. The approval interrupt, with approve, reject and edit, and why an edit is re-validated.
6. Forking from the checkpoint before the proposal to show the boundary holds on a branch too.

Each step is a short paragraph and a few lines of the one file, with the output the reader will
see when they run it.

## 4. Correctness bar

Every claim in the write-up is either demonstrated by the example's own test or cites the
LangGraph documentation page it comes from, with the version it was checked against. Anything I
believe but could not confirm is marked as such. A maintainer reviewing it should find nothing to
correct, which is the standard for asking them to merge it.

## 5. Steps and timing

| Step | Work | Size |
|---|---|---|
| 1 | Extract the example and its test from AgentOps; run on Linux and Windows | Half a day |
| 2 | Write the cookbook text | Half a day |
| 3 | Comment on #8702; wait for a maintainer response | Owner posts; review time is theirs |
| 4 | Open the pull request in the requested form | Half a day after a response |

The comment and the pull request go out under your GitHub account, so you post them. I draft both.

## 6. Risks

- The docs PR #5666 merges first and covers the same ground. Then the example becomes a companion
  to the merged page and the pitch changes from "here is the guidance" to "here is it running".
- Maintainers decline external examples. The write-up and the example in AgentOps still stand on
  their own; the upstream attempt is upside, not a dependency.
