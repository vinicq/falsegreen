# Data-collection protocol (toward a paper on AI-generated false-positive tests)

Internal, reproducible. The aim is an empirical dataset for a paper on unit tests
that pass green without protecting anything, with a focus on tests written by AI.

## Two datasets

### Dataset A: scanner findings on real projects (observational, started)
Real open-source projects, scanner run, findings recorded with metadata. Built by
`research/collect.py` (one CSV row per project: tests, findings, high, web/browser, per-code
counts). Seeded with 8 Python projects (`data/dataset-python.csv`). Purpose: characterize
how often each smell appears, the false-positive rate, and where (layer). Not about
AI authorship; this is the tool-behavior baseline.

### Dataset B: AI-generated tests (controlled, the core of the paper)
The defensible way to study AI-written tests is to generate them ourselves, so they
are labeled by construction and reproducible. Hunting GitHub for "AI-written tests"
has no reliable label and is rejected as the primary source.

Protocol:
1. Sample real production functions/modules from real projects (the same projects in
   Dataset A, or a curated set). Record the source, the function, its intended
   behavior (an independent oracle: docstring/spec where available).
2. Generate unit tests for each with several models: a small/local one (Haiku, and
   optionally Phi-4/Llama to match the literature) and a larger one (a frontier
   Claude/GPT). Fix prompt, temperature, context, and record them.
3. Run falsegreen (scanner + semantic pass) on the generated tests.
4. Manually label each generated test: effective / rotten-green (assertion never
   runs) / wrong-oracle (case 12/18) / weak / fine. This is the ground truth.
5. Measure: how often each model produces a false-positive/rotten-green test, and how
   well falsegreen catches them (precision, recall), small model vs large.

Secondary, supplementary (weak): observational signals of AI authorship in the wild,
`Co-authored-by: Copilot` trailers, PRs mentioning Copilot/ChatGPT/Cursor, repos using
AI test-gen tools (Qodo/Codium, CoverUp, TestPilot). Selection bias and low precision;
use only to triangulate, never as the main claim.

## Reuse, do not reinvent
Published LLM-generated-test corpora exist with replication packages: Ouedraogo et al.
2024 (arXiv:2410.10628), the diffusion-of-test-smells work, EvoSuite-vs-LLM studies.
Reuse where licenses allow before generating fresh data.

## Reproducibility
- `research/collect.py`: the runnable harness for Dataset A.
- Record per generation: model, version, prompt, temperature, date, source function.
- Keep everything under version control in a replication package before submission.

## Status
Dataset A seeded (8 Python projects). Dataset B not started. Tracking: issue #42.
The honest paper-tier assessment (tool/ERA now; full empirical needs the labeled
benchmark + baseline + the small-vs-large LLM study) is in #42.
