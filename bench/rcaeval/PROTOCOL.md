# RCAEval protocol (fixed before the test sets were scored)

**What is measured.** For each injected failure, AgentOps ranks the services most likely to be the
one the fault was injected into. RCAEval's own metrics: AC@1 (right service ranked first), AC@3,
and Avg@5. Dataset: RCAEval (Pham et al., MIT licence), 735 cases on three open-source demo apps
(Online Boutique, Sock Shop, Train Ticket).

**Split.**
- Development: RE1 (375 cases, metrics only). Every design choice was made looking at RE1 only.
- Test: RE2 (270 cases, metrics, logs and traces) and RE3 (90 code-level faults). Scored once,
  with the code frozen at the commit that adds this file. Their schemas were read (column names)
  and the extractor was crash-tested on one case per slice, without reading labels or computing
  any score.

**No shortcuts.** The benchmark injects faults into only five services per app, so a ranker that
knows those five scores well without reading telemetry (arXiv 2609.27069). This method never sees
a case name or the label index; every service that emits telemetry is a candidate; no code
mentions a service name. The model step is run twice: once with real service names, and once with
every name replaced by a random alias per case, so no prior about names can help. The
anonymised run is the headline.

**Arms reported, all from the same single run:**
1. metrics statistics only (code)
2. metrics + traces + logs statistics (code). The trace and log signals were designed without
   any labelled case to tune on (RE1 has none).
3. statistics + model step, anonymised names (headline)
4. statistics + model step, real names

**Model step.** Claude Opus, reading `prompt.md` and a digest of the top 8 services with numbered
evidence. Its answer must name services in the case; otherwise the statistics ranking is used for
that case and the fallback is counted.

**Dev results that set this freeze (RE1).** Statistics only: AC@1 OB 0.896, SS 0.952, TT 0.880.
On a 64-case dev sample (all 34 statistics misses plus 30 random hits) the model step was right
first on 47 (real names) and 41 (anonymised) against 30 for statistics, and turned no correct
answer wrong in the named run.

**Published comparison.** RCAEval paper (arXiv 2412.17015, Table 6, RE2 Train Ticket): best
multi-source BARO AC@1 0.69, Avg@5 0.81. Independent rerun of the released methods (arXiv
2606.29159, Acc@1): RE2 best published method per app OB 0.667 (CIRCA), SS 0.778 (CIRCA),
TT 0.800 (SimpleRCA); RE1 best OB 0.736, SS 0.848, TT 0.560 (BARO). No published score for RE3
per app and no LLM-agent score on RCAEval were found.
