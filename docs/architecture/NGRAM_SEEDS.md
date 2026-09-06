# Base context seeds, and the ARPA format

Where `NgramPredictor`'s **base** bigram and trigram tables come from, why the
hand-written ones are not enough, and how to convert a public language model
into them.

This is the base half of the split described in the *Context tables* section
of `CLAUDE.md`. The user half is learned from typing and persisted; the base
half is rebuilt from `data/` at every launch and never written back. Nothing
here touches the user half.

## The problem, measured

The shipped seeds are hand-curated and small:

```
data/common_bigrams.txt     754 lines  ->    260 prefixes,  1,091 edges
data/common_trigrams.txt    740 lines  ->    330 prefixes,    616 edges
vocabulary                                 18,989 words
rebuild cost at launch                        1.8 ms
```

**260 prefixes against 18,989 words.** For about 98.6% of the words a user can
type, the base model has no context at all, so next-word prediction after them
falls back to raw unigram frequency. There is also no sentence-start model of
any kind, so after every full stop the engine is guessing from nothing.

The curated pairs themselves are good. Compared side by side against a model
trained on 141M words, `how -> is, can, do, about` reads better than the
model's `how -> much, do, to, many` for this app's register. The problem is not
their quality, it is that there are 260 of them.

## ARPA format

The interchange format for n-gram language models. Every toolkit worth using
reads and writes it: SRILM, KenLM, IRSTLM, CMU-Cambridge SLM.

**The name is historical and means nothing about the content.** It is named
after ARPA, the US Advanced Research Projects Agency (now DARPA), which funded
the speech recognition programs in the 1980s and 90s where the format was
defined. SRILM's manual calls it "the ARPA (or Doug Paul) format".

### Structure

Plain text. A header declaring the counts, one section per order, an end
marker. Taken from a real model:

```
\data\
ngram 1=20000
ngram 2=2081918
ngram 3=2596135

\1-grams:
-1.648551	a	-1.251909
-99	<s>	-2.333811

\2-grams:
-2.192367	<s> a	-0.6479963
-6.40905	<s> a's

\3-grams:
-1.933684	<s> <unk> a

\end\
```

Up to three tab-separated columns:

| Column | Meaning |
|---|---|
| 1 | `log10 P(last word given the words before it)` |
| 2 | the n-gram itself, words space-separated |
| 3 | `log10` backoff weight, optional |

Log base 10 for two reasons: multiplying thousands of small probabilities
underflows, and in log space the backoff multiply below becomes an add.

### Backoff is the interesting column

An ARPA file ships **smoothing already computed**. Column 3 is what makes an
unseen n-gram resolvable instead of zero:

```
P(z | a b) = P_listed(a b z)              if that trigram is in the file
           = backoff(a b) * P(z | b)      otherwise, recursing down the orders
```

A missing backoff weight means 1.0, so 0 in log space. Only n-grams that are a
prefix of some longer n-gram carry one, which is why the highest order never
has a third column: there is nothing longer to fall back from.

Verified against the model we use, implementing the rule directly:

```
P(you | how are)          0.50307681   exact 3-gram
P(things | how are)       0.02345616   exact 3-gram
P(therapy | physical)     0.02312336   exact 2-gram
P(appointment | make an)  0.04692994   exact 3-gram
P(zebra | how are)        0.00000023   backoff from 3-gram
```

Half of everything following "how are" is "you", and `zebra` decays through
backoff rather than returning zero.

**This is [known gap #3](PREDICTION_NOTES.md) delivered as data.** Our
`NgramPredictor.predict` interpolates `0.5 P_tri + 0.3 P_bi + 0.2 P_uni`, which
contributes a flat zero from the trigram term on any 2-word prefix the table
has never seen. Backoff weights are the standard answer, and an ARPA file
carries them precomputed by people who trained on far more text than we will.

### Reserved tokens

- `<s>` sentence start. Its probability is `-99`, the ARPA convention for
  "never predict this". It exists only as *context*, which is why it still
  carries a backoff weight. **This is the token worth having**: it is the
  sentence-start model the engine currently lacks entirely.
- `</s>` sentence end.
- `<unk>` unknown word, holding the mass reserved for everything outside the
  vocabulary. Dropped on import.

### Further reading

- [CMUSphinx: ARPA language models](https://cmusphinx.github.io/wiki/arpaformat/)
  is the readable spec and the best starting point.
- [SRILM `ngram-format(5)`](http://www.speech.sri.com/projects/srilm/manpages/ngram-format.5.html)
  is the canonical definition, and
  [`ngram-discount(7)`](http://www.speech.sri.com/projects/srilm/manpages/ngram-discount.7.html)
  covers where the backoff weights come from (Good-Turing, Witten-Bell,
  Kneser-Ney). The model we use was built with Witten-Bell.
- [SpeechBrain `lm/arpa.py`](https://speechbrain.readthedocs.io/en/latest/_modules/speechbrain/lm/arpa.html)
  is a compact Python parser, Apache 2.0, and the closest reference to what we
  do here. [KenLM `read_arpa.cc`](https://github.com/kpu/kenlm/blob/master/lm/read_arpa.cc)
  is the production C++ one.
- [Jurafsky & Martin, SLP3 chapter 3](https://web.stanford.edu/~jurafsky/slp3/3.pdf)
  for the theory: smoothing, Katz backoff, interpolation, stupid backoff.

## Why we do not simply load one

Measured, loading the full 119.7 MB model into plain Python dicts:

```
4,698,053 probabilities + 498,541 backoff weights
14.5 s     518 MB
```

That is disqualifying for a keyboard that has to appear instantly, and a
background thread does not rescue 518 MB. KenLM would solve it in C++ and
would mean a compiled binary dependency, which this project has repeatedly
declined to take on (see the dictation-on-Qt decision in `CLAUDE.md`).

So we prune ahead of time and keep the backoff weights for what survives. The
bar shows at most five pills, so the tail of a two million edge model is
unreachable by construction and pruning costs nothing a user could observe.

## Licensing: this constrains the source more than quality does

The application is MIT. Anything shipped in `data/` has to be redistributable
under compatible terms, which means **public domain, CC0, or CC BY** with an
entry in `THIRD_PARTY_NOTICES.md`.

| | Verdict | Why |
|---|---|---|
| CC0 / public domain | ship it | no obligations |
| CC BY | ship it | attribution only |
| **CC BY-SA** | **no** | share-alike on derived data conflicts with MIT redistribution |
| **CC BY-NC** | **no** | MIT grants commercial use. Direct contradiction |
| **CC BY-ND** | **no** | pruned counts are a derivative |

There is a tempting argument that raw frequency counts are uncopyrightable
facts (*Feist*), so a share-alike condition cannot attach to them. **Do not
build on it.** EU database rights cut the other way, and MIT invites forks who
would inherit the problem.

This rules out several sources that otherwise look ideal: COCA (not
redistributable at all), Wikipedia-derived word and misspelling lists (BY-SA),
the Leipzig Corpora Collection (NC), and the Enron corpus and Norvig's n-gram
files (no licence grant of any kind).

### Sources that qualify

- **[Vertanen & Kristensson, "Forum only language models"](https://digitalcommons.mtu.edu/mobiletext/3/)**,
  CC BY 4.0. 141M words of forum text, ARPA, vocabulary sizes 5K / 20K / 64K
  and orders 1 to 4. **The 20K vocabulary is what we use**, because it matches
  our own 18,989 almost exactly. Same authors' broader
  [Recommended Language Models](https://digitalcommons.mtu.edu/mobiletext/2/)
  (504M words, 64k vocab) and
  [Mobile Text Dataset](https://digitalcommons.mtu.edu/mobiletext/1/) are also
  CC BY 4.0.
- **[A Crowdsourced Corpus of AAC-like Communications](https://www.aactext.org/imagine/)**,
  CC BY 4.0. 5K training and 551 / 563 held-out communications, split by
  author rather than by sentence. This is the natural **evaluation** set, and
  the standard one for text entry work. Two files in that release, the
  Switchboard and communication-situations test sets, carry different terms
  from their original contributors and should be left alone.
- **[Open American National Corpus](https://anc.org/)**, unrestricted, ~15M
  words including spoken transcripts, if we ever want to build tables from raw
  text we control rather than from a trained model.
- **[Google Books Ngrams v3](https://storage.googleapis.com/books/ngrams/books/datasetsv3.html)**,
  CC BY 3.0. Enormous, but book register, so only worth it for rare-word tail
  coverage.

Attribution for whatever is used goes in `THIRD_PARTY_NOTICES.md` **and** in
the generated file's own header, which `scripts/gen_seed_ngrams.py` writes from
its `--source` argument.

## The generator

`scripts/gen_seed_ngrams.py` converts an ARPA model into a seed file. The
model is an input, not a dependency: it is downloaded once, converted, and the
generated file is what ships.

```bash
python scripts/gen_seed_ngrams.py MODEL.arpa --dry-run
python scripts/gen_seed_ngrams.py MODEL.arpa --order 2 --top 5 \
    --out data/seed_bigrams.txt --source "Vertanen & Kristensson, ..., CC BY 4.0"
```

Four things it does, each for a reason:

1. **Restricts to our own vocabulary.** Our wordlist is filtered for explicit
   content and an outside model is not, so intersecting means the model can
   only ever reorder words we already accept, never introduce one. It also
   makes the pruning far more aggressive for free.
2. **Keeps the top N continuations per context.** Five pills means the tail is
   unreachable.
3. **Keeps the backoff weight for every context it retains**, in a `\backoff:`
   section. This is the half that is worth more than the counts.
4. **Converts log probabilities to counts.** See below.

`--max-contexts` keeps only the N most frequent contexts, ranked by the
context's own estimated frequency chained from the lower orders
(`log P(w1) + log P(w2 | w1)`). Ranking by the strength of the best
continuation instead would be wrong: a rare context with one near-certain
follower scores highly and is still a row almost nobody reaches. Bigrams do
not need the cap; trigrams do.

### Probabilities to counts

`NgramPredictor.bigrams` holds **counts** that `_context_probs` normalises
itself, while ARPA holds **normalised probabilities**. The conversion has to
preserve the ratios within a row, and choose a total per row.

Each retained context is given the same total base mass a single curated pair
carries (`--mass`, default 50), split across its continuations in proportion
to probability, floored at 1. So:

```
wheelchair bound 35      <- peaked, one dominant continuation
wheelchair or 6
wheelchair access 3
wheelchair lift 3
wheelchair users 3

a few 12                 <- flat, no dominant continuation
a lot 10
a little 10
a bit 9
```

**The mass is constant per context deliberately.** The obvious alternative,
scaling it by how confident the model is, interacts badly with the base/user
blend: `_context_probs` weights the user's own evidence as
`U / (U + 5 + 0.02 B)`, so a larger `B` means the base is trusted more and the
user needs more typing to override it. Scaling `B` by model confidence would
make peaked contexts, which are exactly the ones the model is most sure about,
the easiest for a single stray user typing to overturn. Constant mass keeps
the blend behaving uniformly, and keeps a machine-derived context from
outweighing a hand-curated one.

### Measured output

From the Vertanen forum 20k 3-gram model, loading the generated files back
into the dict shape `NgramPredictor` uses:

| shipped | contexts | edges | load | RAM | on disk |
|---|---|---|---|---|---|
| bigrams, top 5 | 13,437 | 65,694 | 195 ms | 7.7 MB | 1.18 MB |
| + trigrams, top 3, 20k contexts | +20,000 | 125,302 | 414 ms | 17.0 MB | 2.56 MB |
| + trigrams, top 3, 50k contexts | +50,000 | 209,825 | 729 ms | 32.8 MB | 4.65 MB |

Against today's 1,707 edges at 1.8 ms. **Bigrams alone are 38x the current
edge count and 51x the prefix coverage for 195 ms**, which is the recommended
starting point; trigrams are a separate decision that should be made on
measured prediction quality rather than on the fact that the file can be
generated.

## Measured result

`scripts/bench/ksr.py --corpus aac-dev` / `aac-test`, cold-start engine, five
pills. Each row adds to the one above it.

| | dev KSR | test KSR | dev next-word | test next-word | never predicted |
|---|---|---|---|---|---|
| curated seeds only | 47.3% | 48.7% | 22.9% | 25.2% | 15.0 / 14.3% |
| + `seed_bigrams.txt` | 48.4% | 49.8% | 26.3% | 28.4% | 13.8 / 13.3% |
| **+ `seed_trigrams.txt` (shipped)** | **49.1%** | **50.4%** | **28.7%** | **30.4%** | **13.6 / 13.0%** |
| + trigrams at 50k contexts | 49.1% | 50.7% | 28.9% | 31.1% | 13.6 / 13.0% |

**+1.8 and +1.7 points of keystroke savings, and +5.8 and +5.2 points of
next-word hit rate.** The next-word figure is the one to watch: it is the
metric these tables directly address, and it moved four times as far as KSR
did, because KSR averages it in with the mid-word completions the prefix beam
was already handling.

The two splits move together on every row, which is what makes this readable
as a real effect rather than noise. A single split's 1.4-point disagreement
with the other is the right yardstick for whether *one* number generalises; it
is the wrong one for a paired before-and-after on the same text, which holds
everything but the change constant, and it would be the wrong one twice over
for a change that reproduces on the second split.

**Trigrams stop at 20,000 contexts.** Going to 50,000 costs 2.5x the file
(3.47 MB against 1.38 MB) and 67 ms more at launch, and buys +0.0 on dev and
+0.3 on test. The frequency-ranked context prune works, in other words: the
contexts worth shipping really are the frequent ones, and the tail is tail.

Cost of the whole thing: **2.56 MB on disk and 121 ms at launch**, against
1.8 ms for the curated seeds alone. Per-keystroke latency went from 2.1 ms to
2.6 ms at p50.

## Sentence-start: a negative result, and a caveat that outweighs it

The `<s>` row conditions the first word of a sentence, replacing a fallback to
raw unigram frequency. **On the benchmark it is worth exactly nothing**: 170
of 557 first words in the top five either way, byte-identical KSR. The reason
is that the top five unigrams (`i, you, to, the, it`) already contain the
common sentence openers, and the metric only asks whether the word is in the
set, not where.

It ships anyway, and the reason is that the benchmark cannot see the case it
is for. Each line of the AAC sets is a single sentence and `normalise` strips
the punctuation, so *every* sentence start in the bench is the empty-context
case. The other case is a second sentence in the same field, where the engine
used to condition the next word on the last word of the previous sentence:

```
context                                 before                      after
'i am tired. '                          of, and, to, from, but      I, the, it, if, you
'thanks for the help. '                 me, you, with, to, the      I, the, it, if, you
'can you help me? '                     with, the, a, know, and     I, the, it, if, you
```

"of" is not a word anybody starts a sentence with. Measuring this needs an
evaluation corpus of multi-sentence text with its punctuation intact, which
is a different corpus than the one we have, so the honest summary is: no
measured gain, an obvious defect fixed, and a gap in the benchmark recorded
rather than papered over.

`NgramPredictor.use_sentence_start_context` turns it off, and
`ksr.py --conditions no-sentence-start` is the bench condition. It is inert
when no `<s>` row was loaded, so a model built without the seeds behaves
exactly as before.

## Not done yet

**Backoff weights.** The generated files carry a `\backoff:` section and
nothing reads it; `load_seed_ngrams` skips it deliberately rather than paying
load time for a table no caller uses. That is still
[known gap #3](PREDICTION_NOTES.md), and it is now a *smaller* prize than it
was: the seeds fill in 13,437 bigram and 20,000 trigram contexts directly, so
the sparse-context case backoff exists to rescue is much rarer than it was
before. Worth doing on its own merits and its own measurement, not worth
bundling here.

**A multi-sentence evaluation corpus**, per the sentence-start caveat above.
