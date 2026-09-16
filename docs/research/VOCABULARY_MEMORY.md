# Vocabulary memory and Gboard research

Research date: 2026-09-15. Baseline code: `cb101da`, on
`feat/vocab-next-release`. Implemented milestones are packed SymSpell, packed
prefix lookup, bounded n-gram candidate search, and a licensed expansion from
18,989 to 83,307 base words.
The later measurement sections supersede the initial projections.

## Recommendation

Compact the existing correction indexes before choosing a vocabulary ceiling.
Start with lossless storage changes: an immutable shipped index using word IDs
and contiguous arrays, plus a small mutable layer for personal vocabulary and
frequency changes. Then measure the expanded vocabulary on the existing engine.

The earlier estimate of an additional 100-150 MB for roughly 64K words assumed
the current Python representations. It is an extrapolation, not a measured 64K
build or an intrinsic cost of that vocabulary size.

## Local measurements and their limits

Windows, Python 3.12, current repository code, `tracemalloc`, MiB (2^20 bytes).
An isolated experiment constructed `NgramPredictor()` and called
`load_base_dictionary()`, sorted its words by frequency, then measured
`FuzzyRecognizer.set_frequencies()` and a fully constructed `PrefixIndex` on
successively larger subsets. Imports and the original n-gram dictionary were
created before tracing. No GUI or personal model was loaded.

| Words indexed | Live prefixes | Retained Python allocations | Peak allocations |
|---|---:|---:|---:|
| 5,000 | 13,186 | 12.9 MiB | 16.2 MiB |
| 10,000 | 25,143 | 25.4 MiB | 31.9 MiB |
| 18,989 | 46,586 | 44.5 MiB | 56.6 MiB |

A second isolated run measured allocation stages and reproduced 44.52 MiB
retained, 56.63 MiB peak: 0.02 MiB for the empty fuzzy generator, 0.08 MiB for
its initial base dictionary, 36.88 MiB to merge the full frequencies and build
SymSpell, then 7.55 MiB to construct the prefix index (rounded stages).

These figures include the measured frequency mapping, fuzzy structures and
prefix index. They exclude GUI/Qt, generated context tables, PPM, personal
learning and most n-gram storage. They are not Windows working-set or private
commit measurements. A native extension's allocations would also require a
process-level measurement; comparing its `tracemalloc` result alone would be
misleading.

The forum 64K unigram download returned HTTP 403 during the earlier experiment.
The initial experiment therefore did not measure an expanded vocabulary.
The implemented expansion below uses ESDB instead. Published
context-table measurements are separately recorded in
[`NGRAM_SEEDS.md`](../architecture/NGRAM_SEEDS.md).

## What Google's publications establish

The [2017 decoder paper](https://arxiv.org/abs/1704.03987) describes combining
touch-location probabilities, a key-to-word lexicon and language scores through
finite-state transducers (FSTs). Beam search keeps plausible paths, and graph
composition happens on demand to limit memory. Error transitions account for
missing and extra input. Google's
[accompanying explanation](https://research.google/blog/the-machine-intelligence-behind-gboard/)
also describes a compact lexicon and neural spatial models.

The [2024 Neural Search Space paper](https://arxiv.org/html/2410.15575v1) describes
a 170K-word lexicon and a 30K-word neural language model, with unigram fallback
for words outside that model. It prunes unlikely expansions and stores graph
arcs together in a reusable array. The deployed neural model is a small LSTM.
These are dated published designs, not a complete specification of every
current Gboard installation, and they do not establish its total RAM use.

Google's [2024 training report](https://research.google/blog/advances-in-private-training-for-production-on-device-language-models/)
describes on-device neural next-word models trained using federated learning
with differential privacy. That training system is distinct from the storage
and search techniques considered here.

Our engineering inference: broad lexical coverage can coexist with a smaller
context model, and compact storage can support both. Alpha-OSK's current local
n-gram and spatial scoring can benefit from these ideas.

## Options for Alpha-OSK

### 1. Pack the SymSpell deletion index first

[`symspell.py`](../../src/prediction/symspell.py) maps deletion strings to Python
lists of references to words. It already limits deletion generation to the
first seven characters and two edits. Adding words increases the number of
keys and postings; it does not enumerate every possible misspelling. At fixed
prefix length and edit distance, deletion generation per word is bounded.

Represent the immutable base as:

- One word table, with integer word IDs and a frequency array.
- A compact deletion-key dictionary or sorted, indexed byte storage.
- One contiguous array of word IDs, with an offset range for each deletion key.
- A separate mutable index for learned words and frequency overrides.

This can preserve candidate membership, distance checks and ranking. Replacing
string references with ordinary Python integers alone is insufficient: the
arrays must store packed integers to remove object and per-list overhead.
Handle equal scores deterministically and consult learned frequencies during
ranking. Suppression and personal updates must apply across both layers.

The [upstream SymSpell project](https://github.com/wolfgarbe/SymSpell) documents
the memory/speed tradeoff of prefix indexing. Its historical headline savings
cannot be applied again to our code, which already uses that technique.

The current deletion index contains 200,879 distinct keys and 416,205 word
references. The keys occupy 1,023,266 UTF-8 bytes before object overhead. A
lossless packed payload with two offset arrays and 32-bit word IDs would use:

```text
key bytes                 1,023,266
key offsets                 803,520 = 4 * (200,879 + 1)
posting offsets             803,520 = 4 * (200,879 + 1)
posting word IDs          1,664,820 = 4 * 416,205
total                     4,295,126 bytes = 4.10 MiB
```

Both offset arrays are needed: one locates the key bytes and the other locates
that key's candidate range. This is a calculated storage budget, not a working
replacement's measured memory. Add the word table, frequencies, search
structure, alignment, wrappers and mutable overlay. The underlying lexicon
text itself is 132,561 bytes. The comparison with roughly 37 MiB for the current
generator/SymSpell structures establishes a useful optimization opportunity,
not an exact end-to-end saving.

### 2. Give the prefix beam a compact dictionary representation

[`prefix_beam.py`](../../src/prediction/prefix_beam.py) stores live prefix strings,
child tables, a frequency map, sorted words, and cached short-prefix completions.
A packed trie or radix trie could store shared prefixes as nodes and edges,
with word IDs for terminal entries and cached completions. Preserve the current
spatial beam and its ranking while changing the representation underneath it.

The measured index has 46,586 live prefixes, 32,985 child rows and 26,943
cached completion entries across 8,859 prefixes. Its prefix strings contain
305,357 UTF-8 bytes; the much larger retained allocation includes Python
containers, object headers and cached tuples.

[Minimal acyclic automata](https://aclanthology.org/J00-1002/) can additionally
merge equivalent suffix subgraphs. A plain packed trie is the simpler initial
candidate because prefix-specific top completions and mutable frequencies
complicate a minimized graph.

[MARISA](https://marisa-trie.readthedocs.io/en/latest/tutorial.html) provides
static compact tries, word IDs and memory-mapped loading. It is a candidate
for storage experiments. Its Python API is not a direct replacement for the
beam's node-by-node child traversal. Check that interface, Windows/Linux
packaging and PyInstaller support before choosing it. Documentation warns that
memory mapping can trade RAM residency for random I/O latency; touched pages
still consume memory.

### 3. Pack and cap the base context tables

Store immutable context rows as word IDs, offsets and packed counts. Keep the
user's floating-point counts and decay mutable. Preserve the existing
base/user blend and merged-view invariants rather than silently switching the
scoring model.

Continue pruning generated continuations and limiting retained contexts.
Adding a word to completion/correction does not require adding every possible
bigram or trigram involving it. Personal learning can still acquire its useful
contexts.

[KenLM's data-structure documentation](https://kheafield.com/code/kenlm/structures/)
illustrates packed tries, pointer compression and optional probability
quantization. The ideas are relevant, but integrating KenLM would also change
the scoring and dependency boundary. Start with lossless count packing;
quantization needs separate ranking-quality evaluation.

### 4. Consider trie-based edit search after storage changes

Search a compact dictionary with a bounded edit-distance state, generating
candidate paths at query time instead of retaining a deletion index.
[Schulz and Mihov](https://www.cis.uni-muenchen.de/download/cis-berichte/01-127.pdf)
describe lexicon search using Levenshtein automata, including extensions for
transpositions.

This could eliminate the largest correction index, but it trades memory for
query work and requires matching our actual edit-distance semantics. A plain
Levenshtein implementation would lose the existing transposition behavior.
Our spatial scores, completion behavior, short-prefix rescue and autocorrect
confidence rules also need to remain intact. This is a larger algorithm change
than packing SymSpell.

### Changes that need explicit quality or latency evidence

- Shortening SymSpell's indexed prefix can shrink storage but increase
  candidate collisions and distance calculations. Benchmark it independently.
- Limiting full correction to common words saves memory but weakens correction
  for rare words, including potentially valuable accessibility vocabulary.
- A dictionary made of Python node objects can consume substantial memory.
  Choosing a trie algorithm does not by itself ensure compact storage.
- Neural models introduce training and inference costs. Their use by Gboard
  does not establish that replacing our engine would reduce memory.

## Proposed experiment and acceptance criteria

1. Prototype lossless packed SymSpell storage and compare candidate sets,
   ordering and learned-word updates against the existing implementation.
2. Measure 19K, intermediate and proposed full vocabularies with an eager
   correction index and a warmed prefix index. Record native/process memory,
   startup peak, steady state and cold/warm query latency, including p95/p99.
3. Run the existing whole-word and mid-word fuzzy benchmarks, including omitted,
   doubled, neighboring and transposed input. Include rare accessibility words.
4. Evaluate keystroke savings and next-word accuracy on `aac-dev`; use
   `aac-test` for the final held-out comparison. Report the corpus with every
   number and keep all experiments isolated from the live user model.
5. Verify personal learning, suppression, boosting, reload, clear-data and pack
   lifecycle behavior. A compact base must not make user updates wait for a
   full rebuild.

A useful experiment target is whether the proposed larger vocabulary can fit
within today's roughly 44.5 MiB correction/completion allocation budget. This
is a target to test, not a promised result.

## First implementation milestone: lossless packed correction index

The user authorized implementation after this research on 2026-09-15. The first
milestone changes SymSpell's storage representation. Vocabulary, prefix length,
edit distance, correction scores, merge strategy, privacy behavior and user-data
formats retain their current semantics. PrefixIndex and context-table packing
are follow-on milestones, evaluated independently.

### Representation and lookup

Use only Python's standard library: `array`, `bytes` and normal dictionaries.
No native extension, additional runtime dependency or generated binary asset is
required for this milestone.

The first `prepare()` or nonempty `lookup()` freezes the words accumulated so
far into an immutable deletion index. Maintain:

1. A word-ID table referencing the existing word strings.
2. Concatenated UTF-8 deletion keys and 32-bit boundary offsets.
3. Contiguous 32-bit candidate word IDs and posting boundaries.
4. An open-addressed 32-bit hash table containing deletion-key IDs plus one;
   zero denotes an empty slot. Keep its load at or below one half.

Hash the original Unicode key and resolve collisions by checking the complete
encoded key. Hash collisions must never discard a key or candidate. Python's
process-randomized hash is appropriate because the table is built and consumed
inside one process. Persisting this representation would require a separate
format/design and is outside this milestone.

For the measured 200,879 keys, a power-of-two hash table has 524,288 slots,
adding 2 MiB to the 4.10 MiB key/offset/posting payload. Word references,
frequencies, wrappers and incremental entries add further memory. This is a
more realistic engineering budget than treating the 4.10 MiB payload as the
complete index.

Sorted deletion keys with binary search were considered. They avoid the hash
table but introduce several Python-level comparisons per query variant.
Open addressing spends a small fixed array to keep expected lookup work close
to the existing dictionary approach. Timing will determine whether this is a
good tradeoff on the supported interpreter.

### Construction and incremental learning

Avoid constructing the complete old list-based index as an intermediate.
Count deletion occurrences in a first pass, allocate the packed buffers, then
regenerate variants in a second pass to fill the posting ranges. Populate each
range in word insertion order. Construct temporary buffers locally and publish
the completed index only after the build succeeds.

Known-word frequency changes update the existing frequency map. A new word
added after preparation goes into a small mutable deletion dictionary. A query
visits immutable candidates first and incremental candidates second for each
variant. This preserves the insertion order of the former single dictionary.
Repeated preparation remains a no-op, so a learned word does not trigger a
full rebuild on the typing path.

Existing reset/reload paths replace the SymSpell instance and therefore rebuild
from the replacement vocabulary. This naturally folds the current vocabulary
into the packed part. The first milestone does not add automatic periodic
compaction of the incremental part; unusually large in-session imports need
separate measurements before adding such a policy.

### Behavioral compatibility

Keep the same input normalization, variant generation, distance calculation and
sorting rules. Equal-distance/equal-frequency results currently preserve
candidate discovery order. Do not replace this with alphabetical tie-breaking
as an incidental storage change. Compare ordered results against the baseline
implementation in the same process, where both see the same hash seed.

Cover exact matches, omitted/extra/substituted/transposed characters, errors
around the indexed-prefix boundary, repeated letters, apostrophes, Unicode,
empty dictionaries, query distance overrides, new deletion buckets, updates to
existing buckets, and frequency changes in both index layers. Small independent
edit-distance oracles complement direct baseline comparisons.

### Measurement protocol

Compare the same shipped vocabulary and query set on the baseline and packed
implementations. Report retained Python allocations and build peak separately
from process memory. Memory instrumentation must be disabled for latency
measurements. Warm the index before timing queries; report build time and
lookup median/p95/p99 independently. Use fresh processes for process-memory
comparisons to avoid allocator retention from an earlier implementation.

Check ordered correction results first, then the fuzzy/learning regression
tests and full repository gate. Record actual measurements and limitations
below when the implementation has been evaluated. Expanding vocabulary is a
separate quality experiment after the representation change passes.

## First milestone measurements

Measured on Windows with 64-bit CPython 3.12.10 against baseline commit
`cb101dabdccf8a658180279aa46e55cd9c4a58ac`. Reproduce the comparison from the
worktree using its Python environment:

```text
python scripts/bench/vocab_memory.py --reference cb101da --n 300 --seed 7
```

The benchmark uses 18,989 shipped words, 300 sampled source words and 1,208
queries covering single substitutions, omissions, repeated letters,
transpositions, literal words and misses. All **2,428 ordered result comparisons**
matched, including a second pass after adding words and raising frequencies.
The regression tests separately cover Unicode, hash collisions, prefix
boundaries, distance overrides, empty bases and equal-score behavior.

| SymSpell-only measurement | Baseline | Packed | Change |
|---|---:|---:|---:|
| Retained Python allocations | 34.84 MiB | 7.59 MiB | -78.2% |
| Peak Python allocations during build | 35.04 MiB | 30.50 MiB | -13.0% |
| Untraced process RSS increase during build | 36.84 MiB | 9.74 MiB | -73.6% |
| Untraced Windows private-memory increase | 36.80 MiB | 8.20 MiB | -77.7% |
| Build time | 231.0 ms | 417.2 ms | +186.2 ms |
| Lookup median | 0.226 ms | 0.239 ms | +0.013 ms |
| Lookup p95 | 1.329 ms | 1.325 ms | -0.004 ms |
| Lookup p99 | 2.027 ms | 2.089 ms | +0.062 ms |

These timings are one controlled comparison, not a statistical claim about
small percentage differences. Build time is clearly higher because of the
second variant-generation pass and packing. The small query-time differences
need interpretation as absolute milliseconds, with further evaluation on the
actual expanded vocabulary before release.

Tracing and timing run in separate fresh worker processes. Optional `psutil`
measurements are taken only in the untraced worker: process memory sampled
while tracing includes the tracer's own bookkeeping and is unsuitable for this
comparison. RSS/private figures are post-build deltas, not startup peaks or
complete app totals. The script requires no additional dependency; these
process figures are omitted if `psutil` is unavailable.

Repeating the earlier combined fuzzy-plus-PrefixIndex allocation experiment
on the new implementation produced **17.24 MiB retained and 32.64 MiB peak**,
compared with **44.5 MiB retained and 56.6 MiB peak** before. The vocabulary and
46,586 live prefixes were unchanged. This is about a **61% reduction** in the
measured combined Python allocations, even though the prefix index itself is
unchanged. It excludes the GUI, context tables, PPM and the original n-gram
dictionary, as described in the baseline method.

These first-milestone results establish packed correction on the original
vocabulary. The following sections cover the subsequent prefix compression
and the measured expanded dictionary.

Validation on 2026-09-15 passed the complete `python check.py` gate: Ruff lint,
Ruff formatting, mypy under both `linux` and `win32`, and the full parallel
pytest suite (114.6 seconds total in this environment). The benchmark script
also passed its own Ruff lint/format checks because `check.py` covers `src/`
and `tests/`, not `scripts/`. Prepared-versus-incremental ordering checks were
also exercised under hash seeds 1, 2 and 3. All model experiments used shipped
data and all benchmark/reference scratch files used scoped system temporary
directories. The test gate used its own temporary base directory, cleaned on
completion.

## Implemented prefix storage

`packed_prefixes.py` replaces the live-prefix set, child dictionary, and
short-prefix completion dictionary with UTF-8 key and child buffers, uint32
offsets and word IDs, and an open-addressed lookup table. Full key equality
resolves hash collisions; uint64 hash fingerprints skip unnecessary text
comparisons. A bounded cache retains at most 4,096 successful prefix lookups.
The frequency dictionary and sorted word list remain for exact long-prefix
scanning, including the existing `max_scan` limit.

New personal words add live prefixes and child edges to a mutable overlay.
Frequency increases materialize only affected short-prefix top rows.
Completion ordering, ties, Unicode, and insertion behavior are checked against
the original implementation, including forced hash collisions. These indexes
are process-local, never a persisted user-data format.

On the original 18,989 words, `prefix_memory.py --reference cb101da
--legacy-vocabulary cb101da` compared 46,618 distinct prefix states and 2,956
beam outputs across initial and updated dictionaries, with no mismatches.
Isolated prefix-index retained allocations fell from 7.52 to 2.57 MiB (65.8%).
Build peak increased from 19.66 to 20.72 MiB because construction still uses
temporary Python containers. Untraced construction took 58.6 versus 101.9 ms;
combined public-query median/p95 were 0.9/2.4 versus 2.0/3.7 microseconds.
Those measurements use the same words and do not claim that vocabulary growth
has no latency cost. The bounded cache can add roughly 0.35 MiB after warming.

## Implemented vocabulary expansion

The base vocabulary now has **83,307 words**, up from 18,989. There are 64,291
source-derived additions and 27 curated care, accessibility, and software
terms, including caregiver, dystrophy, screenreader, telehealth, GitHub, and
TypeScript (stored lowercase, with the existing casing policy unchanged).

The source is [ESDB's official American English plain-wordlist release
2026.02.25](https://sourceforge.net/projects/wordlist/files/speller/2026.02.25/wordlist-en_US-2026.02.25.zip/download),
size 60, release commit `7e99eda`. The [maintainer's documentation](https://wordlist.aspell.net/dicts/)
distinguishes this standard list from size 70, whose rarer entries can create
spelling ambiguity. ESDB is a spelling resource, not a conversational corpus.
Its order does not establish frequency or next-word likelihood.

`scripts/gen_vocabulary.py` verifies the archive SHA-256 before reading its
wordlist and writes both the data and its provenance manifest. It retains all
remaining size-60 entries after filtering, without random sampling or a
word-length cutoff. Filters exclude uppercase forms, possessives, unsupported
characters, fragments, existing vocabulary, and the explicitly reviewed stems
and inflections in `data/explicit_stems.txt`. Matching is exact, so
cockpit, peacock, dictionary, and medical vocabulary are not removed merely
because they contain an ambiguous substring. This is a reviewed exclusion
list, not a claim of exhaustive content classification. The flat source lacks
part-of-speech and usage labels, so some uncommon inflections and slang remain.

The complete original copyright/source notices are in `data/licenses/ESDB.txt`.
The data directory is already included by all three packaging specifications,
so the notices accompany the derived dictionary in packaged builds. The new
wordlist is 695,332 bytes before any package compression.

Reproduce after downloading the source archive:

```text
python scripts/gen_vocabulary.py wordlist-en_US-2026.02.25.zip --out data/english-expanded.txt --base data/google-10000-english-usa-no-swears.txt --base data/google-20000-supplement.txt --base data/base_dictionary.txt
```

Each source-derived addition contributes **one base count**, versus the
existing ranked vocabulary's much larger common-word counts. Curated additions
get 25 counts. Neither path writes personal counts. `LanguageProfile` owns the
optional supplementary path. The shipped base survives replacement of a saved
model, and both exact and fuzzy prediction consult that base alongside the
loaded vocabulary. Upgrades and backup reloads therefore retain new coverage
without overwriting learned counts or repopulating rejected saved entries.
Clear Learned Data
rebuilds the same base vocabulary. No user-data schema changes are required.

Vocabulary growth also invalidated one shortcut: a two-letter mis-click can
become a live prefix of a rare new word. The hybrid now allows short-prefix
fuzzy completion when fewer than five valid exact suggestions can reach the
bar. This fixed floor preserves main's pill-count-independent rescue rule.
Suppressed candidates do not fill that quota.
One letter still does not trigger fuzzy rescue, and the standalone fuzzy API
keeps its original default. This prevents new words such as `pwned` from
disabling help for a mistyped `pe`.

## Combined memory with the expanded vocabulary

Run `python scripts/bench/vocab_growth.py --reference cb101da`. Every scenario
runs in fresh processes, with tracing separate from timing/process-memory
sampling. The original dictionary is extracted from the reference commit;
the new supplementary profile path is disabled for the two baseline rows.
Both fuzzy indexes are materialized by the same first prefix request.

| Scenario | Words | Retained allocations | Build peak | RSS build increase | Index build and first prefix |
|---|---:|---:|---:|---:|---:|
| Original indexes, original vocabulary | 18,989 | 44.12 MiB | 56.23 MiB | 58.66 MiB | 0.311 s |
| Packed indexes, original vocabulary | 18,989 | 12.00 MiB | 32.24 MiB | 15.56 MiB | 0.547 s |
| Packed indexes, expanded vocabulary | 83,307 | 48.79 MiB | 122.87 MiB | 53.26 MiB | 2.477 s |

This is **4.39 times the words for 10.6% more retained index allocations**
than the original implementation. The unchanged vocabulary costs 72.8% less.
The expanded build's Windows private-memory increase was 53.39 MiB, versus
59.23 MiB originally; allocator behavior makes this different from traced
retained allocations. Neither metric is a complete app RAM measurement.
The benchmark excludes the n-gram dictionary, context tables, PPM, and GUI,
and its one warm-up request does not fill the bounded prefix cache.

The tradeoff is explicit: expansion increases construction time and temporary
peak memory. Offline-built packed artifacts or a streaming builder could
address that in a later change. The current formats remain entirely in memory
and require no native library or installation-time compilation.

Hybrid startup and full dictionary rebuilds explicitly prepare the prefix
index after final frequency injection. Construction is therefore paid during
initialization or reload rather than the first typed prefix. The standalone
fuzzy API retains lazy preparation for callers using only whole-word
correction. A layout switch reuses the packed dictionary and refreshes spatial
emissions. Preparation generates no synthetic text or prediction request.

## Avoiding a full vocabulary scan on every click

The expansion initially exposed the next bottleneck: the n-gram scorer built
and scanned a set containing every base word for each request. A profile on
83,307 words found about five million prefix checks in just 60 mid-word calls.
Next-word scoring spent most of its time scoring and sorting base words that
could never reach the bar. Compact fuzzy storage alone does not solve this.

The implemented candidate search keeps **all personal, corpus, and context
candidates** and only the best requested number of matching base words. For ordinary
nonnegative mixture weights, a base-only word's score is a fixed nonnegative
multiple of its base frequency. A word below that base cutoff cannot displace
the better base candidates, whose personal/context contributions can only
raise their scores. The full probability formula still scores every retained
candidate. Unusual negative counts or weights, and nonpositive result limits,
use the full-scan fallback.

A sorted list of existing word references gives exact prefix ranges via
binary search. A small heap selects the highest-frequency words in that
range. Next-word top rows are cached only for requests of at most 32 words.
A versioned base dictionary invalidates both caches on key or count changes,
including direct mutations through update, pop, clear, and in-place union.
Replacing it with an ordinary dictionary remains supported via uncached
rebuilds. The sorted reference list adds approximately **0.64 MiB** at this
vocabulary size, outside the fuzzy-only memory table above; it copies no word
strings. Personal counts are read fresh on each request.

Integration with main's skipped-apostrophe matching also indexes the small
subset of base words containing apostrophes by their stripped spelling.
Searching both prefix ranges keeps contractions such as `i'll` reachable from
`ill`, including words supported only by the base. This auxiliary cache is
additional to the word-reference measurement above. Corpus-prior candidates
remain in the sparse candidate set alongside personal and context evidence.

Equal scores now use lexical ordering. The previous set iteration made
low-frequency ties depend on Python's randomized hash seed; the expanded tail
exposed this in the cross-process determinism test. This tie rule is an
intentional behavior change, separate from the lossless packed-index changes.
Independent brute-force comparisons cover random models, equal-frequency
tails, sparse context and user evidence, Unicode bounds, unusual weights,
mutation, learning, clear, and reload.

`python scripts/bench/ngram_candidates.py --reference cb101da` compares the
original and current scorers on **the same expanded snapshot**, with current
caches warmed and old/new call order alternated over nine paired repeats.
It measures only n-gram prediction, without fuzzy search or hybrid merging:

| Request | Original median / p95 | New median / p95 |
|---|---:|---:|
| Mid-word (22 contexts, 198 samples each) | 8.388 / 10.995 ms | 0.190 / 0.453 ms |
| Next-word (10 contexts, 90 samples each) | 47.908 / 72.432 ms | 0.578 / 0.811 ms |

The improvement is large enough to be meaningful despite normal workstation
timing noise. These are warmed microbenchmarks, not a promise about every
prefix, user history, device, or first-keystroke cost.

## Prediction-quality evaluation at `714db10`

Fresh temporary models, five pills, `ksr.py --conditions full --mis-click`.
The source vocabulary and its priors were not selected from either AAC split.
The development split was used to check regressions; the test split was run
against the expansion before integrating subsequent main changes. Results are
potential click savings for an ideal user, not a measured accessibility study.

| Corpus / condition | Original 18,989-word base | Expanded 83,307-word base |
|---|---:|---:|
| AAC dev, clean KSR | 49.1% | 49.6% |
| AAC dev, one second-character slip per word | 45.3% | 45.6% |
| AAC test, clean KSR | 50.4% | 50.7% |
| AAC test, one second-character slip per word | 46.7% | 46.9% |

Next-word hit rates remained 28.7% on dev and 30.4% on test. The fraction never
predicted during clean typing fell from 13.6% to 12.5% on dev and 13.0% to 12.1%
on test. The aggregate KSR gains are **below the project's 1.4-point noise
yardstick**: the supported conclusion is broader coverage without a material
aggregate regression, not a proven large improvement in conversational typing.
Direct regression cases separately check the intended care/software vocabulary.

Hybrid prediction medians were 0.5 ms clean and 0.6 ms with slips on both
splits. The corresponding p95 values were 4.2/4.5 ms on dev and 4.3/4.7 ms on
test. Earlier unoptimized expansion measurements ran alongside tests and were
slower; they are not used for a controlled timing ratio. The paired n-gram
microbenchmark above isolates the algorithm's effect more reliably.

## Validation before integrating main

The source at `714db10` passed every `check.py` stage on Windows: Ruff lint,
formatting, mypy for Linux, mypy for Windows, and the complete pytest suite.
The run took 400.9 seconds, including 396.7 seconds for pytest. The check
runner's subprocess output was captured explicitly because its Windows
background-process flag otherwise hides child output in this environment;
the commands and pass criteria were unchanged. The vocabulary generator and
four new benchmark scripts also passed separate Ruff lint and formatting
checks because `check.py` scopes those checks to `src/` and `tests/`.

Regression coverage includes ordered packed-index parity, collisions and
Unicode, personal-word overlays, startup and layout-switch preparation,
expanded vocabulary across reload and clear, deterministic score ties,
candidate pruning against an independent full scorer, and the existing
corrupt-model loading checks. Tests and benchmarks used scoped temporary
models and cleaned their scratch directories. No live learned model was
modified. These checks do not substitute for a packaged-release smoke test
or a full GUI process-memory measurement.

## Integration with current main for the PR

The PR branch integrates main at `f0cdb5a`, including the corpus-prior split,
explicit prediction-edit learning, skipped-apostrophe matching, and the fixed
five-candidate short-prefix rescue floor. Candidate pruning retains every
corpus-prior candidate, and a small additional base index covers apostrophe-
stripped prefix matches. Fuzzy frequency construction visits merged, base,
and corpus dictionaries in stable order. The earlier packed-index memory
measurements still describe the storage design; they are not new full-app
measurements after this integration.

Fresh temporary-model AAC runs after integration reproduced every rounded
quality figure in the `714db10` table above: dev 49.6% clean / 45.6% slip, test
50.7% clean / 46.9% slip, with unchanged next-word hit and never-predicted rates.
No constants were retuned. These runs overlapped the full test gate, so their
timing values are recorded only as observations: dev median 0.8/1.0 ms and
p95 5.4/5.6 ms, test median 0.9/1.2 ms and p95 6.3/7.2 ms (clean/slip).
They do not provide a controlled comparison with the earlier latency figures.

The integrated source passed the complete `check.py` gate in 312.9 seconds:
Ruff, formatting, mypy for both Linux and Windows, and pytest (308.0 seconds).
The generator and four benchmark scripts also passed their separate Ruff and
format checks. Focused integration tests cover corpus-only candidates,
base-only contractions, and corpus-adjusted score ties. Test models and
benchmark models were temporary, and their scratch directories were removed.

## Final integration with UI Automation support

The published PR head `e7fde34` contains the vocabulary expansion and the
validated integration described above. Main subsequently advanced to
`3a9347f`, adding UI Automation support. This final merge retains that upstream
work together with the 83,307-word vocabulary, packed indexes, candidate-search
optimizations, research documentation, and benchmark tooling.

The only merge conflict was in `tests/test_fuzzy_prefix_beam.py`. Its resolution
keeps main's `built_beam` helper and its callers, while preserving the vocabulary
branch's preparation, layout-reuse, dead-prefix, and short-prefix regression
tests. The helper makes beam initialization independent of test order.

After resolving that conflict, Ruff lint, formatting, mypy for Linux and
Windows, all tests changed by the UI Automation update, and conformance checks
passed. Those runs completed before finalization resumed. The complete
`check.py` gate and AAC quality runs recorded above apply to `e7fde34`; no new
AAC result or full-gate timing is claimed for this final merge. The completed
checks were not repeated for this documentation-only wrap-up.

The finalization did not launch the keyboard, touch live learned data, merge
the PR, or publish a release.
