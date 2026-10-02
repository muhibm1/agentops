---
id: gate-approval-without-reading
trigger: "when the owner gives a standing instruction to approve every gate and the packet is not read by a human"
confidence: 0.7
domain: workflow
source: 2026-10-01-increment-3-act-pause-persist-resume
scope: project
---

## Action
Reviewers and the main session must carry the check the human would have made: list open lows and undone items in the approval note. Retro should record which of those the owner has still not seen.

## Evidence
- approvals.md G2, G4, G5: "the owner did not read this brief/packet". G4 notes D25 (AgentShield scan) NOT done, 5 lows open.

## Evidence (added by 2026-10-02-increment-4-no-spend-measurement-harness)
- approvals.md G2, G4, G5: "the owner did not read" each packet again. The G4 rejection (R2b) came from the main session's own reading of a reviewer low, which shows the main-session check works when it is done. Still unseen by the owner: open lows (outcome.py already_applied, python lows 4-5, silent executor traceback, security R1 CORS simple POST) and D25/D26.
