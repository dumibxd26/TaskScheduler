# Thesis Source

The paper documents nine-policy exploratory simulation, fresh-seed
confirmation, exact Legacy weight tuning and factor checks, the earlier
six-trial Kind pilot, and the accepted 27-trial, three-policy Kind topology
matrix. It explicitly treats all Kind workers as same-host containers and
marks physical multi-machine k3s validation as pending.

Build with `latexmk` so references and the table of contents converge:

```sh
cd thesis
latexmk -pdf -interaction=nonstopmode -halt-on-error thesis.tex
```

The validated build produces `thesis.pdf` with no undefined citations or
references. Page count changes as final result tables are reconciled. MiKTeX
may still report its own update reminder.

Optional title-page assets:

- `pics/upb-logo.jpg`
- `pics/cs-logo.pdf`

The document compiles without them and renders labeled placeholders instead.

The title metadata in `thesis.tex` is set to Bogdan Dumitrescu,
Prof. dr. ing. Radu-Ioan Ciobanu, and 2026.

Do not edit reported numbers from memory. Their authoritative inputs are:

- `../results/legacy-weight-search.json`
- `../results/legacy-factor-ablation.json`
- `../results/legacy-factor-stress.json`
- `../results/final-all-legacy-20seed/`
- `../results/final-all-workload-20seed/`
- `../results/confirmatory-single-seeds200-249/`
- `../results/confirmatory-workload-seeds200-249/`
- `../results/final-evidence-synthesis.json`
- `../results/k8s-kind/policy-comparison.json` (earlier pilot)
- `../results/k8s-kind-topologies/summary.json` and `summary.md` (strictly
	accepted final same-host topology matrix)

The engineering rationale and evidence boundaries are recorded in
`../ENGINEERING_JOURNAL.md`.
