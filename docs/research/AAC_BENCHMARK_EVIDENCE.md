# AAC benchmark evidence and plan

**Audit date:** 16 September 2026.
**Scope:** documentation audit of Alpha-OSK main at `6a1a6de`; no new benchmark
runs, model training, participant collection or runtime changes. Historical
whitepaper measurements retain their original configuration. Vocabulary PR #128
at `116d43f` is a separate configuration and does not supply new pointer-learning
evidence.

## Evidence and dataset contract

Will Wade's [correction article](https://willwa.de/2025/06/25/correcting-text-in-aac-or.html)
links [Correct-A-Sentence](https://github.com/AceCentre/Correct-A-Sentence).
The relevant dataset is
[`willwade/aac_datasetv2`](https://huggingface.co/datasets/willwade/aac_datasetv2/tree/f4f042210341b5d2605f24fbabf7246f96d694ed),
not the later, separate `AACConversations` release.

The inspected v2 snapshot has **24,550 rows**, split into **19,640 train / 4,910 test**,
with no validation split. Its 28 fields are:

| Type | Fields |
|---|---|
| Integer | `turn_number`, `template_id` |
| List of strings | `context_speakers`, `context_utterances` |
| String | `conversation_id`, `language_code`, `scene`, `speaker`, `utterance`, `utterance_intended`, `next_turn_speaker`, `next_turn_utterance`, `model`, `provider`, `minimally_corrected`, `fully_corrected` |
| Twelve strings | `noisy_{qwerty,abc,frequency}_{minimal,light,moderate,severe}` |

There are 39 language/locale codes, not necessarily 39 distinct languages.
These are generated conversations, not recorded AAC typing sessions. The
[project dataset card](https://github.com/Smartbox-Assistive-Technology/AACDataSet/blob/5e6d9e0621ef5bdaf6de1b41521a8d3236ac061c/huggingface/dataset_card.md)
describes template/LLM generation and declares CC BY 4.0. The
[v2 card](https://huggingface.co/datasets/willwade/aac_datasetv2/blob/f4f042210341b5d2605f24fbabf7246f96d694ed/README.md)
does not itself carry a licence declaration. Confirm that the project declaration
covers this exact artifact before redistribution or training; do not substitute the
licence of a later dataset. Model/provider fields exist but the inspected viewer
includes `unknown`, so they do not fully establish generation provenance.

The [v2 preparation script](https://github.com/Smartbox-Assistive-Technology/AACDataSet/blob/e76052a00946fd77d599d0ff85e43e5a8d41c33d/huggingface/scripts/prepare_multilingual_dataset.py)
flattens AAC turns, retains up to three preceding turns and a following turn, then
uses an 80/20 row-level split with seed 42. It does not group by conversation.
Conversation IDs use language and an index that restarts per input file. Audit
actual ID collisions, duplicates and shared conversations before constructing a
grouped split; overlap is a risk from the code, not a measured overlap percentage.
No manifest connects every uploaded row to a pinned generator, layout and seed.

## What the typo generator actually does

The [augmentation script](https://github.com/Smartbox-Assistive-Technology/AACDataSet/blob/bffa5c3d0f05db9fc3ea63f2de54d1614d04ffcb/scripts/augment_aac_data.py)
creates each noisy variant independently for AAC turns.

- Severity rates are 0.05, 0.15, 0.25 and 0.35. For length `L`, it requests
  `max(1, round(rate * L))` edits, capped at `floor(0.75 * L)`, with a one-edit
  exception for lengths at most two. Empty input stays empty.
- Distinct original character positions are sampled and processed in descending
  order. The caller chooses uniformly between adjacent substitution, deletion and
  insertion. Although the function supports transposition, this caller omits it.
- Substitution samples a nonempty cell from the eight surrounding grid cells.
  Insertion samples any nonempty layout cell, including punctuation and underscore,
  not just a neighbour. Lookup uppercases the character and maps space to underscore.
  Inserted letters normally become lowercase.
- Deletion avoids removing the final remaining character. Unsupported adjacency can
  produce a no-op; an unchanged output triggers an extra attempted change.
  Underscores are not converted back to spaces.

These percentages are edit-attempt settings, not measured CER. The correction
fields are also programmatic: `minimally_corrected` capitalises an initial letter and
adds final punctuation if absent; `fully_corrected` applies that operation to
intended text. They are not independent human-reviewed corrections.

**Layout provenance limits reproduction.** The inspected
[language layout module](https://github.com/Smartbox-Assistive-Technology/AACDataSet/blob/5237cf2b3d9ffe31026e8e6bd83f44e0cc74e2f2/lib/language_keyboards.py)
was committed after the v2 upload. It creates unstaggered 3-by-10 grids. In this
path, English `abc` takes the stored QWERTY order rather than sorting letters;
the fallback path differs. Frequency layouts sort by character frequency, unknown
languages fall back to English, and long alphabets can be truncated. Do not claim
that today's layout code exactly reproduces v2, or that a `qwerty` column models
Alpha-OSK's rendered geometry.

## What can be compared

Text pairs can test correction and noisy-prefix robustness. They cannot evaluate
continuous pointer offsets, physical-slot learning, dwell, scanning or user effort:
there are no coordinates, intended-key labels, participant histories or event timing.

| Outcome | Comparable test | Required qualification |
|---|---|---|
| Prediction-bar KSR | Same candidate count, replay and selection/space/repair costs | Oracle selection measures opportunities, not human savings |
| Correction accuracy | Same word/sentence unit and output policy; top-1 and top-k separate | Include failures and clean-input damage |
| WER / CER | `(S + D + I) / reference length`, with identical tokenisation/normalisation | Report noisy input and corrected output; preserve raw scores too |
| Latency | p50/p95 at the same operation boundary and hardware | Per-key calls, sentence decoding, cold start and UI latency are different |
| User effort | Measured actions, repairs, dwell/scan cycles, time and workload | Requires a user study, not string-pair inference |

The existing [KSR harness](../../scripts/bench/ksr.py) reads intended text, uses
reference previous-word context, and assigns baseline cost to words it never
predicts. It does not charge repairs or carry unresolved mistakes forward. A new
adapter must declare whether it preserves that historical policy or evaluates a
different, complete text-entry policy. Such results need separate labels.

For v2, use `utterance` as the reference for recovery from injected noise.
Capitalisation/punctuation restoration and expansion toward `utterance_intended`
are separate tasks. Feed only preceding context, never `next_turn_*`, intended
text, corrected targets or another noisy sibling as model input. Preserve noisy
whitespace and Unicode; silently applying the current word-only normalisation
would remove errors being scored. Insertions and deletions also require explicit
prefix/reference alignment for a KSR replay. Invented key-centre coordinates
would constitute another simulation, not recovered pointer data.

## Decoder comparisons and strongest current claim

- **Alpha-OSK:** learned n-gram counts, dictionary-prefix spatial beam, heuristic
  source merging and separate whole-word correction. It is statistical software,
  but no sequence-to-sequence correction model is trained on noisy/clean pairs.
- **Small local correction model:** Correct-A-Sentence includes a
  [T5 training path](https://github.com/AceCentre/Correct-A-Sentence/blob/main/helper-scripts/model/train-script-happy.py)
  and a [60.5M-parameter model](https://huggingface.co/willwade/t5-small-spoken-typo).
  It learns sentence transformations from paired text; ONNX provides an offline
  deployment route. Its training mixture includes COMM2, not proof of overlap with
  Alpha-OSK's 2011 COMM split. Audit overlap and model/data rights separately.
  CPU latency, memory and fidelity on target devices remain measurements to make.
- **Text Slinger:** the [0.2.6 package](https://pypi.org/project/textslinger/0.2.6/)
  exposes language-model next-character and next-word predictions using multiple
  backends. It is a candidate LM baseline or component, not a documented spatial
  decoder or drop-in sentence-correction model.
- **VelociTap:** the [CHI 2015 paper](https://www.keithv.com/pub/velocitap/velocitap.pdf)
  jointly decodes sentence-length touchscreen coordinates using spatial, character
  and word models, including insertion/deletion and space hypotheses. Its
  server-based mobile studies and large language models do not establish small
  on-device performance or AAC effectiveness.

Correct-A-Sentence also has an algorithmic
[word-segmentation/spelling path](https://github.com/AceCentre/Correct-A-Sentence/blob/main/sentence_correction_service.py).
The article's small example comparison is not a benchmark against Alpha-OSK.
Its [evaluation script](https://github.com/AceCentre/Correct-A-Sentence/blob/main/examples/example.py)
skips the first CSV row and excludes failed (`None`) predictions from accuracy.
Its normalised string similarity divides edit distance by the longer string,
which is not reference-normalised CER. Aggregate request time is not controlled
per-key latency. A shared evaluator must count all attempts and report failures.

**Strongest defensible pointer claim:** in one seeded synthetic biased-pointer
replay on AAC-like text, continuous positions added 0.5 percentage points of KSR,
and online mean-bias subtraction added 0.4. The simulation generates intended
targets, but the learner receives only reported keys and offsets. Real presses
can update the same runtime model; effectiveness for real users or later sessions
has not been demonstrated. The table stores means, not precision. Likewise,
`--learn-half` measures in-domain corpus exposure, not one person's history; an
exact-test exposure row is neither a perfect-user model nor a performance bound.

## Staged benchmark plan

1. **Establish data eligibility.** Pin dataset, model and generator revisions;
   resolve v2 licence coverage; audit duplicates, template families, conversations
   and possible training overlap. Build train/dev/test groups before tuning, with
   every noisy sibling in the same group. Start with supported English locales.
2. **Implement a text-only evaluator.** Compare identity/no-change, Alpha-OSK's
   actual word-correction path, the algorithmic sentence baseline and a small
   trained model under the same targets. Report exact match, WER/CER, failures and
   clean-input harm by layout, severity and error type. A composed sentence adapter
   must be labelled as such; it is not an existing Alpha-OSK sentence decoder.
3. **Evaluate prediction separately.** Add a causally aligned noisy-prefix adapter;
   fix bar size and action costs. Report historical-policy KSR separately from
   repair-inclusive replay. Do not train or tune on final test examples.
4. **Test spatial adaptation synthetically.** Use actual layout geometry, multiple
   seeds, zero bias, several directions/noise levels, drift and layout changes.
   Compare key-only, position-only, online adaptation and frozen learned state.
   Report learning curves and disjoint later-sequence performance. Resample at
   conversation/author level for text and across pointer seeds; the 1.3-point
   dev/test difference is not a statistical noise threshold.
5. **Measure devices and users.** Record CPU, model format, threads, RAM, startup,
   p50/p95 per-key and per-sentence latency on named low-power hardware. Real-user
   adaptation needs a separately specified, consented chronological click/intent
   dataset and within-person comparisons. No such collection is implemented here.
   The [study protocol](STUDY_PROTOCOL.md) records the current instrumentation and
   learning-isolation gaps before enrolment.

Proposed cases include clean text, deliberate abbreviations and telegraphic AAC;
names, numbers and negation that must retain meaning; adjacent and distant
substitutions; omissions, repeats and transpositions; missing/extra spaces and
apostrophes; very short strings and no-op injections; unsupported characters and
locales; grouped duplicate/sibling leakage; and synthetic zero-bias, stable-bias,
drifting and remapped pointers. Score task accuracy, prediction savings, resource
cost and observed user effort separately. None of this plan is evidence of a gain
until implemented and measured.
