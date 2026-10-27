# Hosted configuration

What AgentOps needs from services outside this repository, by name only. No value, key or
token is ever written here; the owner records the facts below in their own words.

## Anthropic test workspace

The live smoke test (`tests/test_live_smoke.py`, run by `.github/workflows/live-smoke.yml` or
by hand) and the owner's live experiments use a key from a separate Anthropic workspace, so a
leaked or runaway test key cannot touch the app's own spend. The app key must not be reused.

| Field | Record |
|---|---|
| Secret name | `AGENTOPS_TEST_ANTHROPIC_API_KEY` (a GitHub Actions secret, and the cloud environment variable the owner sets) |
| Anthropic workspace | to be recorded by the owner |
| Monthly spend limit | to be recorded by the owner |
| Key owner | to be recorded by the owner |
| Key rotation | to be recorded by the owner (when it was last rotated and how often) |

To use the key locally or in a cloud session, pass it to the one command that needs it, never
to the whole shell:

```
ANTHROPIC_API_KEY="$AGENTOPS_TEST_ANTHROPIC_API_KEY" agentops eval --model anthropic --dev
```
