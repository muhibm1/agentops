"""The measurement harness: the run outcome shape both systems produce, the deterministic
evaluators, and the model judge. The pinned dataset files live in the top-level `evals/`.

Evaluators are plain functions with LangSmith's `(inputs, outputs, reference_outputs)`
signature; nothing in this package imports LangSmith (ADR 0001).
"""
