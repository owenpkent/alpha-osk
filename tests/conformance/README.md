# Cross-backend conformance harness

Proves the **Python** (`src/prediction/`) and **C++** (`cpp/prediction/`)
prediction engines produce identical output for the same model + same input, so a
feature ported between backends can be verified mechanically: *run the fixtures,
green == parity.* This is the mechanism that makes "move features back and forth"
between the two backends safe. See [`docs/architecture/BACKEND_PARITY.md`](../../docs/architecture/BACKEND_PARITY.md).

## The contract

Both backends implement one line-oriented protocol: **JSONL in on stdin, JSONL out
on stdout, one output line per input line, `id` preserved.** No model paths, no
GUI, no network.

Request line:

```json
{"id": "prefix-th", "mode": "predict", "context": "th", "n": 5}
{"id": "fuzzy-teh", "mode": "autocorrect", "typed_word": "teh", "context": ""}
```

Result line:

```json
{"id": "prefix-th", "mode": "predict", "result": ["the", "this", "that", "they", "there"]}
{"id": "fuzzy-teh", "mode": "autocorrect", "corrected": "the"}
```

`corrected` is `null` when no autocorrect fires. Both backends load a **pinned
model directory** passed on the command line (not the user's live model) so state
is deterministic; an empty dir means "cold-start from the shared `data/` files",
which are identical across a checkout.

## Files

| File | Role |
|---|---|
| `run_backend.py` | Reference (Python) backend runner. `--model-dir <dir>`, reads stdin JSONL, writes stdout JSONL. |
| `fixtures/contexts.jsonl` | Shared test cases, each tagged with a `pillar` (ngram / ppm / fuzzy). |
| `test_conformance.py` | pytest: self-checks the Python reference is deterministic, and (when a C++ binary is provided) diffs the two backends per fixture. |

## Running

```bash
# Python-only self-check (always runs; proves the harness + reference are stable):
python -m pytest tests/conformance/

# Cross-backend diff (once a C++ conformance binary exists):
ALPHA_OSK_CPP_BIN=/path/to/alpha-osk python -m pytest tests/conformance/ -v
```

Without `ALPHA_OSK_CPP_BIN` the cross-backend test **skips** with a clear reason.
Fixtures whose `pillar` is not yet in `CPP_PORTED_PILLARS` (currently `{"ngram"}`)
are treated as expected-divergence and don't fail the run, so the suite doubles as
a live parity tracker: as the C++ PPM/fuzzy pillars land, add them to that set and
the harness starts enforcing them.

## What the C++ backend needs (small, not built yet)

`cpp/main.cpp` already has a headless `--selftest` path that loads the real model,
constructs a `HybridPredictor`, and prints predictions for a *hardcoded* context
list. The conformance harness needs one more entry point that reads **arbitrary**
contexts from stdin and emits the JSONL result contract above. It's ~15 lines
modelled on the `--selftest` block:

```cpp
// in cpp/main.cpp, before the QApplication path:
if (argc > 1 && QString(argv[1]) == "--conformance") {
    QString modelDir = /* parse "--model-dir <dir>" from argv */;
    HybridPredictor predictor;               // point its paths at modelDir
    QTextStream in(stdin), out(stdout);
    for (QString line = in.readLine(); !line.isNull(); line = in.readLine()) {
        if (line.trimmed().isEmpty()) continue;
        QJsonObject req = QJsonDocument::fromJson(line.toUtf8()).object();
        QJsonObject res;
        res["id"]   = req["id"];
        res["mode"] = req["mode"];
        if (req["mode"].toString() == "autocorrect") {
            QString c = predictor.checkAutocorrect(req["typed_word"].toString(),
                                                   req["context"].toString());
            res["corrected"] = c.isEmpty() ? QJsonValue(QJsonValue::Null) : c;
        } else {
            QStringList r = predictor.predict(req["context"].toString(),
                                              req.value("n").toInt(5));
            res["result"] = QJsonArray::fromStringList(r);
        }
        out << QJsonDocument(res).toJson(QJsonDocument::Compact) << "\n";
        out.flush();
    }
    return 0;
}
```

The one gap to close first: `HybridPredictor`'s C++ constructor resolves model/data
paths internally via `paths::`. Give it a way to accept an explicit model dir (ctor
arg or an env override the `--conformance` path sets) so both backends load the
*same* pinned model. Until that lands, the harness runs the Python half and skips
the diff.

## Caveat: n-gram-only today

Per `cpp/prediction/HybridPredictor.h`, the C++ engine is n-gram-only right now
(PPM/fuzzy stubbed), which contradicts the "done" status in
`docs/build/CPP_WINDOWS.md`. That discrepancy is exactly what this harness exists to
resolve empirically — the `ngram`-tagged fixtures are the ones expected to match
first; `ppm`/`fuzzy` fixtures will diverge until those pillars are genuinely ported.
