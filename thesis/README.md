# Thesis Source

`thesis.tex` is the dissertation in the format of the original project
description (UPB, Faculty of Automatic Control and Computers): introduction
(context, problem, motivation, objectives, research questions and
hypotheses, proposed solution), state of the art, architecture and
implementation, experimental evaluation, limitations and future directions,
conclusions, and appendices (corrections relative to the earlier version,
reproduction commands). It reports the corrected simulator and control plane,
the three-phase simulation evidence, HEFT-RA, the mechanism studies, and the
same-host Kind validation with its calibrated simulator twin. Physical
multi-machine validation is explicitly out of scope.

Build with `latexmk` so references and the table of contents converge:

```sh
cd thesis
latexmk -pdf -interaction=nonstopmode -halt-on-error thesis.tex
```

Optional title-page assets (the document compiles without them and shows
labelled placeholders):

- `pics/upb-logo.jpg`
- `pics/cs-logo.pdf`

Do not edit reported numbers from memory. Their authoritative inputs are:

- `../results/final-evidence-synthesis.json` and `.md` (all simulation phases)
- `../results/sim/` (raw closed-DAG and workload records)
- `../results/mechanism-studies.json` and `.md`
- `../results/legacy-weight-search.json`
- `../results/legacy-factor-ablation.json`, `../results/legacy-factor-stress.json`
- `../results/contextual-policy-search.json`
- `../results/kind-validation/summary.json` and `.md` (live matrix and twin)

The step-by-step history, including every correction, is in
`../ENGINEERING_JOURNAL.md`.
