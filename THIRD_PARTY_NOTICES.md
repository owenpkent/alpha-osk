# Third-party notices

Alpha-OSK is MIT licensed (see [`LICENSE`](LICENSE)). It also carries a small
amount of third-party material, listed here with the notices those licences
require.

This file covers material **embedded in the source tree or the shipped
build**. Python dependencies are not repeated here: they are pinned in
`requirements.txt`, enumerated per release in the CycloneDX SBOM that ships
alongside every installer, and retain their own licences.

---

## Feather

Several small controls draw Feather icons. Their path data is embedded verbatim
in [`qml/Main.qml`](qml/Main.qml) rather than bundled as an asset, because it is
fed to a `Canvas` via `ctx.path` (see `qml/components/StrokeIcon.qml`).

- Project: https://github.com/feathericons/feather
- Icons: `rotate-ccw` (clear-context button), `bookmark` (Snippets),
  `smile` (Symbols & Emoji), `mic` (Dictation), `x` (close buttons)
- Licence: MIT
- Icons in use, all in [`qml/Main.qml`](qml/Main.qml):
  - `rotate-ccw`, the clear-context button in the suggestion bar
  - `bookmark`, the Snippets buttons in the suggestion bar and the title bar
  - `mic`, the dictation buttons in the suggestion bar and the title bar
  - `chevron-left` / `chevron-right`, the Snippets window's back control and
    its page pager

The one place the source is not reproduced character for character is `smile`:
`StrokeIcon` takes path data only, so that icon's `<circle>` is written as the
equivalent pair of arcs. Everything else, including the zero-length lines
Feather uses for the eyes, is upstream's own data.

```
The MIT License (MIT)

Copyright (c) 2013-2023 Cole Bemis

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## Word lists

The base vocabulary in [`data/`](data/) is derived from the Google 10 000 /
20 000 English word-frequency lists, filtered for explicit content. See
[`docs/WHITEPAPER.md`](docs/WHITEPAPER.md) for how they are used and why no
domain packs ship by default.

---

## Adding to this file

If you embed third-party code, icons, fonts or data, add a section here with
the project, what is used, the licence, and the notice text the licence
requires. Keep the notice verbatim: paraphrasing a licence is not the same as
including it.
