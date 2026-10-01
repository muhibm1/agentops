---
id: escape-model-text-in-terminal
trigger: "when a CLI prints model-sourced or log-sourced strings to a terminal"
confidence: 0.5
domain: security
source: 2026-10-01-increment-1-scaffold
scope: project
---

## Action
Pass every such string through one escape helper (control chars, newlines, bidi) and constrain id-like fields with a pattern; cover the error path as well as the report path.

## Evidence
- reviews/wh-security-reviewer.md: medium cli.py:123-131 forged remediation line; error-path escaping left open as a low.
