# Benchmark evaluation sets

Held-out material for `scripts/bench/ksr.py --corpus ...`. **Nothing here is
training data and nothing here ships**: the PyInstaller spec bundles `data/`
wholesale, which is why these live under `scripts/` instead.

| File | Split | Lines |
|------|-------|-------|
| `aac_dev.txt` | development | 557 |
| `aac_test.txt` | test | 566 |

## Source

Both are verbatim from *A Crowdsourced Corpus of AAC-like Communications*,
Keith Vertanen and Per Ola Kristensson, and are used under **CC BY 4.0**.

- <https://www.aactext.org/imagine/>
- Vertanen, K. and Kristensson, P.O. (2011). "The Imagination of Crowds:
  Conversational AAC Language Modeling using Crowdsourcing and Large Data
  Sources." *Proceedings of EMNLP 2011.*

The corpus was collected on Mechanical Turk, with workers asked to invent
communications as if using a scanning interface. The splits are **by worker**
(80 / 10 / 10), not by sentence, so no author appears in more than one split.

They are kept verbatim rather than pre-normalised so they can be diffed
against upstream; `ksr.py::normalise` reduces each line to the tokens the word
engine models at load time.

**The training split is deliberately absent.** The bench measures a cold-start
engine, and a training set sitting in the repo beside the test set is an
invitation to seed the model from it and then report a number against the
matching test split. Fetch it from the source above if it is ever needed.

Two other files in that release, the Switchboard and communication-situations
test sets, carry different terms from their original contributors and are
deliberately not included here.
