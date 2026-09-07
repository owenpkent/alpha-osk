#!/usr/bin/env python3
"""Build the arXiv paper from ``docs/WHITEPAPER.md``.

The markdown is authoritative. This script regenerates ``body.tex`` and
``abstract.tex`` from it and then runs LaTeX twice, so the table of contents
and every internal reference resolve.

    python docs/paper/build.py            # regenerate and compile
    python docs/paper/build.py --tex-only # regenerate, do not compile

Requires pandoc on PATH (or at the Windows install location below) and one of
tectonic, latexmk, xelatex or pdflatex.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent
REPO = DOCS.parent
SOURCE = DOCS / "WHITEPAPER.md"
FIGURES = HERE / "figures"

# The generated halves. Editing these by hand is a mistake the next build
# silently undoes, so the banner says so and this list is what gets rewritten.
GENERATED = ("body.tex", "abstract.tex")

BANNER = (
    "%% GENERATED FROM docs/WHITEPAPER.md BY docs/paper/build.py. DO NOT EDIT.\n"
    "%% Edit the markdown and rebuild; changes here are discarded.\n\n"
)

FIGURE_BLOCK = re.compile(
    r'<p align="center">\s*'
    r'<img\s+src="(?P<src>[^"]+)"[^>]*?/>\s*'
    r"<br\s*/>\s*<em>(?P<cap>.*?)</em>\s*"
    r"</p>",
    re.S,
)


# Characters that appear in the markdown and are absent from the T1 text
# fonts. LaTeX does not fail on these, it drops them, so the PDF loses an
# arrow or a subscript with nothing in the log but a "Missing character"
# note. Mapping them to macros is engine-independent, which the alternative
# (fontspec plus a Unicode font) is not: arXiv accepts pdflatex submissions
# and this keeps that door open.
UNICODE_FIXES = {
    "\u2192": r"$\rightarrow$",  # ->
    "\u2194": r"$\leftrightarrow$",  # <->
    "\u2212": r"$-$",  # minus sign
    "\u2264": r"$\leq$",
    "\u2265": r"$\geq$",
    "\u226b": r"$\gg$",
    "\u2248": r"$\approx$",
    "\u2026": r"\ldots{}",
    "\u27f2": r"$\circlearrowleft$",  # the clear-context button's icon
    "\u2318": r"Cmd",  # the macOS Command key
    "\u03bb": r"$\lambda$",
    "\u03c3": r"$\sigma$",
    "\u03a3": r"$\Sigma$",
    "\u03a0": r"$\Pi$",
    "\u2081": r"$_1$",
    "\u2082": r"$_2$",
    "\u2083": r"$_3$",
    "\u208b": r"$_-$",
    "\u2079": r"$^9$",
}


def fix_unicode(tex: str) -> str:
    """Replace the characters the T1 fonts drop with equivalent macros."""
    for char, macro in UNICODE_FIXES.items():
        tex = tex.replace(char, macro)
    return tex


def find_pandoc() -> str:
    found = shutil.which("pandoc")
    if found:
        return found
    fallback = Path.home() / "AppData/Local/Pandoc/pandoc.exe"
    if fallback.exists():
        return str(fallback)
    sys.exit("pandoc not found. Install it (winget install JohnMacFarlane.Pandoc).")


def find_engine() -> list[str] | None:
    """The first working LaTeX driver, preferring the ones that self-manage runs."""
    if shutil.which("tectonic"):
        return ["tectonic", "--keep-logs"]
    if shutil.which("latexmk"):
        return ["latexmk", "-pdf", "-interaction=nonstopmode"]
    for engine in ("xelatex", "pdflatex"):
        if shutil.which(engine):
            return [engine, "-interaction=nonstopmode", "-halt-on-error"]
    return None


def strip_inline_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s).replace("\n", " ").strip()


def convert_figures(md: str) -> tuple[str, int]:
    """Rewrite the HTML figure blocks as markdown images.

    Pandoc passes raw HTML straight through and the LaTeX writer then drops
    it, so leaving these alone loses all five figures with no warning.
    """
    FIGURES.mkdir(exist_ok=True)

    def repl(m: re.Match) -> str:
        src = Path(m.group("src"))
        source_path = (DOCS / src).resolve()
        if source_path.exists():
            shutil.copy(source_path, FIGURES / src.name)
        else:
            print(f"  warning: missing figure {source_path}")
        caption = strip_inline_html(m.group("cap"))
        caption = re.sub(r"^Figure\s+\d+\.\s*", "", caption)
        caption = caption.replace("%", r"\%")
        return f"![{caption}](figures/{src.name})"

    return FIGURE_BLOCK.subn(repl, md)


def run_pandoc(pandoc: str, markdown: str, *, top_level_section: bool) -> str:
    cmd = [
        pandoc,
        "--from=markdown+pipe_tables+backtick_code_blocks+tex_math_dollars",
        "--to=latex",
        "--wrap=preserve",
    ]
    if top_level_section:
        # The markdown's own top level is "##" (there is one "#" title, which
        # we do not pass through), so without the shift pandoc maps every
        # section of this paper to \subsection and the document has no
        # sections at all. The shift promotes "##" to "#" first.
        cmd += ["--shift-heading-level-by=-1", "--top-level-division=section"]
    res = subprocess.run(cmd, input=markdown, capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        sys.exit(f"pandoc failed:\n{res.stderr}")
    # \tightlist is defined only inside pandoc's own template; our preamble
    # provides it too, but dropping it keeps the generated file portable.
    return fix_unicode(res.stdout.replace("\\tightlist\n", ""))


def check_cross_references(md: str) -> int:
    """Report section references that point at no heading, and numbering gaps.

    LaTeX renumbers the sections itself, so a heading the markdown calls 1.4
    can render as 1.3, and every prose reference to it then points somewhere
    else. Nothing warns about this: the paper simply cites the wrong section.
    Both halves of that were live bugs, hence both checks.
    """
    headings, order = set(), []
    for m in re.finditer(r"^(#{2,4})\s+(\d+(?:\.\d+)*)\.?\s+(.*)$", md, flags=re.M):
        headings.add(m.group(2))
        order.append(m.group(2))

    problems = 0

    top = [int(n) for n in order if "." not in n]
    if top != list(range(1, len(top) + 1)):
        print(f"  cross-refs: top-level sections are not sequential: {top}")
        problems += 1

    for parent in sorted({n.split(".")[0] for n in order if n.count(".") == 1}, key=int):
        subs = [
            int(n.split(".")[1]) for n in order if n.startswith(f"{parent}.") and n.count(".") == 1
        ]
        if subs != list(range(1, len(subs) + 1)):
            print(f"  cross-refs: subsections of section {parent} are not sequential: {subs}")
            problems += 1

    dangling = sorted(
        {m.group(1) for m in re.finditer(r"§(\d+(?:\.\d+)*)", md)} - headings,
        key=lambda s: [int(x) for x in s.split(".")],
    )
    if dangling:
        print(f"  cross-refs: {len(dangling)} dangling: {', '.join('§' + d for d in dangling)}")
        problems += 1

    if not problems:
        print(f"cross-refs: {len(headings)} headings, all references resolve")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tex-only", action="store_true", help="regenerate the .tex, skip LaTeX")
    args = parser.parse_args()

    if not SOURCE.exists():
        sys.exit(f"missing source: {SOURCE}")

    pandoc = find_pandoc()
    md = SOURCE.read_text(encoding="utf-8")

    check_cross_references(md)

    md, n_figures = convert_figures(md)
    print(f"figures: {n_figures}")

    abstract = md[md.index("## Abstract") : md.index("## 1. Introduction and Motivation")]
    abstract = abstract.replace("## Abstract", "").strip().strip("-").strip()

    body = md[md.index("## 1. Introduction and Motivation") : md.index("## References")]
    body = re.sub(r"^---\s*$", "", body, flags=re.M)
    # Our headings carry their own numbers; LaTeX adds its own, so strip ours
    # or every heading renders as "3 3. The Prediction Engine".
    body = re.sub(r"^(#{2,4})\s+\d+(?:\.\d+)*\.?\s+", r"\1 ", body, flags=re.M)

    (HERE / "body.tex").write_text(
        BANNER + run_pandoc(pandoc, body, top_level_section=True), encoding="utf-8"
    )
    (HERE / "abstract.tex").write_text(
        BANNER + run_pandoc(pandoc, abstract, top_level_section=False), encoding="utf-8"
    )
    print("wrote body.tex, abstract.tex")

    if args.tex_only:
        return 0

    engine = find_engine()
    if engine is None:
        print("no LaTeX engine found; wrote .tex only")
        return 0

    runs = 1 if engine[0] in ("tectonic", "latexmk") else 2
    for i in range(runs):
        res = subprocess.run(
            [*engine, "alpha-osk.tex"], cwd=HERE, capture_output=True, text=True, encoding="utf-8"
        )
        if res.returncode != 0:
            tail = "\n".join((res.stdout or res.stderr).splitlines()[-40:])
            sys.exit(f"{engine[0]} failed on run {i + 1}:\n{tail}")

    pdf = HERE / "alpha-osk.pdf"
    if pdf.exists():
        print(f"built {pdf.relative_to(REPO)} ({pdf.stat().st_size / 1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
