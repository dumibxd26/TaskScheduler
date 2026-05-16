# Thesis source

Build:

```sh
cd thesis
pdflatex thesis.tex
pdflatex thesis.tex      # second pass for refs / ToC
```

Or, if you have `latexmk`:

```sh
latexmk -pdf thesis.tex
```

Required logos under `pics/` (the title-page template expects them):

- `pics/upb-logo.jpg` — UPB logo
- `pics/cs-logo.pdf`  — CS/AC department logo

If you don't have them yet, the document will still compile but the
title page will show "missing file" placeholders. Drop them in and
recompile.

Things to fill in before submission:

- `\Name` — your full name (top of `thesis.tex`)
- `\Advisor` — advisor name
- `\Year` — defense year
- Chapter "Results" is intentionally left empty until the experiments
  on the two-host cluster are run.
