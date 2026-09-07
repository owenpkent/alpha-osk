# The paper build

Turns `docs/WHITEPAPER.md` into a typeset PDF for arXiv.

```bash
python docs/paper/build.py             # regenerate the .tex and compile
python docs/paper/build.py --tex-only  # regenerate only, no LaTeX needed
```

Needs `pandoc` on PATH, plus one of `tectonic`, `latexmk`, `xelatex` or
`pdflatex`. On Windows: `winget install JohnMacFarlane.Pandoc`. Tectonic is the
easiest LaTeX side because it fetches its own packages on first run.

## What is authored and what is generated

**`docs/WHITEPAPER.md` is the source of truth.** Everything else here is either
a hand-written wrapper or a build product.

| File | |
|---|---|
| `alpha-osk.tex` | Authored. Preamble, title block, bibliography, appendix. |
| `build.py` | Authored. The converter. |
| `body.tex`, `abstract.tex` | **Generated.** Rewritten on every build. |
| `figures/` | **Generated.** Copied from `assets/screenshots/`. |
| `alpha-osk.pdf` and the LaTeX side files | **Generated.** |

The generated files are gitignored and carry a "do not edit" banner, because an
edit to them survives exactly until the next build and then vanishes with no
warning. Edit the markdown.

## Three things the converter has to do that are not obvious

Each of these was a silent failure, meaning the build reported success and the
PDF was wrong. They are the reason this is a script rather than a pandoc
one-liner.

**The figures are HTML.** The markdown wraps every figure in
`<p align="center">` so it renders on GitHub. Pandoc passes raw HTML straight
through and the LaTeX writer then discards it, so a plain conversion loses all
five figures and says nothing. `convert_figures` rewrites them as markdown
images first.

**The headings start at `##`.** The markdown has one `#` title, which the
LaTeX title block replaces, so the body's top level is `##`. Pandoc maps that to
`\subsection`, and the paper comes out with no sections at all. Hence
`--shift-heading-level-by=-1`.

**Nineteen Unicode characters are not in the T1 fonts.** LaTeX does not fail on
a character it cannot set; it drops it and notes "Missing character" in the log.
Before `UNICODE_FIXES` the PDF was missing 78 characters, including all 35
arrows in `Settings -> Appearance` paths and every subscript in the
interpolation formula. The mapping is to macros rather than to a Unicode font
because that keeps a `pdflatex` submission possible, which arXiv still accepts.

## The cross-reference check

The markdown numbers its own headings (`## 8. Evaluation`) and the paper cites
them in prose (`§8.3`). LaTeX strips those numbers and renumbers from scratch,
so if the markdown's numbering has a gap, every citation past the gap points at
the wrong section and nothing warns you. `check_cross_references` runs on every
build and reports both dangling references and non-sequential numbering. It
caught exactly that bug once, where section 1 ran 1.1, 1.2, 1.4, 1.5.

If you add or reorder a section, renumber the markdown headings and the `§`
references to match, then rebuild and read the check's line.

## Submitting to arXiv

1. `python docs/paper/build.py` and confirm a clean log: no "Missing character",
   no overfull boxes, and the cross-reference line reporting no problems.
2. Upload `alpha-osk.tex`, `body.tex`, `abstract.tex` and `figures/`. arXiv
   compiles the source itself; do not upload the PDF alongside it.
3. Primary category `cs.HC`, cross-list `cs.CL`.
4. A first submission to a category needs an endorsement from someone already
   published there. Start that early; it is the only step gated on a person.
5. Licence: CC BY 4.0. It is compatible with the evaluation corpus's own licence
   (see below) and it is what lets other people build on the work.

## Attribution obligation

The evaluation corpus and the shipped context seeds both derive from Vertanen
and Kristensson's work and are used under **CC BY 4.0**, which requires
attribution. They are cited in the bibliography, in §3.3 (the seeds), and in
§8.1 (the corpus). Do not remove those citations. `scripts/bench/data/README.md`
carries the corpus provenance in full.
