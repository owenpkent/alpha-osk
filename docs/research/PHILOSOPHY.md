# Alpha-OSK Philosophy

This document states what Alpha-OSK believes and why, so that a decision made
six months from now can be checked against the same reasoning that produced
the ones already here.

It is the "why" layer. It deliberately holds no measurements and no
implementation rules, because both live somewhere better:

| For | Read |
|-----|------|
| Measurements, evaluation, limitations | [`docs/WHITEPAPER.md`](../WHITEPAPER.md) |
| The rules a change must not break | [`CLAUDE.md`](../../CLAUDE.md) |
| Per-algorithm design | [`docs/architecture/`](../architecture/) |
| Where an idea came from | [`INNOVATION_SOURCES.md`](INNOVATION_SOURCES.md) |

When this document and any of those disagree, they are right and this is
stale. Fix it here.

---

## The constraint everything descends from

Alpha-OSK is a window on the same desktop as the application being typed
into. **It must never take focus.** Taking focus to accept a click would take
it away from the text field the keystroke is destined for.

That single sentence is the origin of most of the non-obvious design in this
project. It is why in-app text entry (the prediction editor, the snippets
editor, the key-action editor) is routed through an explicit edit mode and a
pair of signals rather than through Qt focus. It is why modifiers are sticky
and lockable rather than chorded. It is why context staleness has to be
*detected*, through six independent signals, rather than simply observed. It
is why there is no keyboard shortcut anywhere: the user has no keyboard.

Nearly every question of the form "why is this done the hard way" has this as
its answer. Ask it first.

---

## Principles

### 1. Accessibility is the architecture, not a feature

There is no accessibility layer to bolt on, because there is nothing
underneath it. The keyboard is for people whose primary input is a pointer,
and the author is one of them: a wheelchair user with muscular dystrophy who
uses it daily as his only text input.

The practical test is not "can this be used with a mouse" but "can this be
used by someone whose pointer is slow, imprecise, and expensive to move."
Those are different bars. The second one rules out gestures that a mouse can
technically perform.

### 2. Effort is the currency

Every click costs the user something real. That makes effort the unit the
design optimises, and it decides arguments that would otherwise be aesthetic:

- A taller or wider target is a cheaper click, which is why the function row
  fills the grid rather than lining up with the columns beneath it, and why
  the three sections share one height so the panel keys grow into it.
- A click that lands between two keys and types nothing is worse than a click
  on the wrong key, because a wrong key is recoverable by the prediction
  engine and a dead click is not. Every key therefore claims a share of the
  gap around it.
- A gesture that must be *held* is the one thing a pointer cannot reliably
  do here. Swipe typing was built and then removed for this reason, and
  push-to-talk dictation was never built for it. Move mode splits "drag the
  window" into two clicks with a free hand in between for the same reason.
- Five readable suggestions beat eight truncated ones, so the prediction bar
  drops low-ranked pills rather than eliding any of them.

### 3. Local-first, and off-network by default

Predictions, learning, and analytics never leave the machine. There is no
cloud round trip on the typing path, no GPU, and no LLM. This is not a
performance position, it is a privacy one: this is a category where the data
is unusually sensitive (passwords, medical correspondence, intimate
messages), and the way to keep it safe is not to send it anywhere.

What follows from that:

- Privacy mode suppresses learning *and* the live context signal, so nothing
  typed into a password field reaches the model, the analytics, or the
  visualisation. Detection fails open by design, which is why the manual
  control exists and why the UI says so when auto-detection has no backend.
- The diagnostic log may never contain typed content. It is the file users
  attach to bug reports.
- Usage telemetry is off by default in the app, ships with an empty endpoint,
  and would send only the lifetime counters the user can already read on their
  own dashboard, never content. The installer asks separately, and since
  2026-09-08 its box is ticked: the page pays for that by stating the purpose,
  printing the exact message field by field, and taking one click to decline.
  What is not negotiable is that pairing. A ticked box on a page that showed
  none of it would be a default nobody had been given the means to refuse, and
  the argument against it is stronger than the usual one about cookies:
  ePrivacy Art 5(3) reaches software that stores a file locally and then calls
  an endpoint over the network (EDPB Guidelines 2/2023 v2.0, paras 33 and 44),
  Planet49 (C-673/17) holds that a pre-checked box is not consent, and EDPB
  Opinion 5/2019 para 40 rules out legitimate interests for that step. It ships
  ticked anyway, as a judgement call made with all of that in view rather than
  in ignorance of it, and the reasoning is recorded where the box is built.
- The one credential the system holds (a dictation API key) is deliberately
  excluded from the backup archive, because an archive is made to be carried
  between machines.

### 4. Measure before believing, and report what the measurement says

Assumptions inherited from good systems are still assumptions. Several of
this project's have not survived contact with a benchmark, and the results
are published rather than quietly dropped:

- The character model inherited from the Dasher lineage contributed nothing
  to word prediction while costing five and a half times the latency. It is
  out of the merge.
- The spatial error-correction layer is worth nothing on clean input. It
  earns its place only under pointer error, which happens to be the condition
  this population types in.
- Learning the user's pointer bias, which simulated beautifully, was worth a
  few tenths of a point end to end. It shipped anyway, for stated reasons,
  and the modest number is recorded next to it.
- The four user-selectable merge strategies are separated by less than the
  noise floor. The setting stays, and the whitepaper says plainly that we
  cannot demonstrate any choice beats the default.

A negative result about our own work is the most useful kind, because it is
the only kind that stops the next person spending a week on it.

### 5. One route is not a route

A control reachable by exactly one gesture is unreachable to anyone without
that gesture. Dwell-click software, switch access, head and eye trackers, and
single-button adaptive mice all produce a left click and nothing else.

So right-click is always a shortcut and never the only way in, and a setting
must never be able to remove the only entry point to an unrelated feature.
Colour obeys the same rule from the other direction: with nine themes
shipping, a colour that carries meaning has to carry it on all of them, or it
is decoration on some and information on others.

### 6. A familiar surface with intelligence underneath

This is where Alpha-OSK parts company with its most direct influence. Dasher
showed that an interface organised by probability can be extraordinarily
efficient, and paid for it with a novel paradigm the user has to learn.

Alpha-OSK takes the opposite trade: an ordinary QWERTY keyboard with nothing
to learn, and puts the probability underneath, in the prediction bar and in
the decoder that reads a click as evidence rather than as a keypress. Neither
choice is better in the abstract. They suit different input devices, and
Dasher's suits continuous pointing (eyes, head) in a way a key grid never
will.

The corollary is a discipline: novelty on the surface has to justify itself
against the cost of learning it, and almost never can. Novelty underneath is
free.

### 7. Say plainly what is not known

The system has one long-term user, who is also its author. There is no user
study. Keystroke savings measured by simulation is an upper bound on benefit
and not a measure of it, because a real user has to notice a suggestion, read
it, decide, and land a pointer on it.

All of that is stated in the whitepaper's own limitations section rather than
buried, and the study protocol and consent form were published before
enrolment so the analysis cannot be chosen after the data is seen. An
accessibility tool that overstates its evidence is asking a vulnerable
population to take its word for something, which is exactly the wrong way
round.

---

## Things we changed our minds about

The list matters more than any single entry, because it is the evidence that
the principles above are load-bearing rather than decorative.

| Removed or reversed | Why |
|---|---|
| Swipe / glide typing | A sustained precise drag is the one gesture this user cannot reliably make. Its overlay also owned every press in its bounds, which turned any key it did not know about into a dead tap. |
| LLM re-ranking on by default | Contradicted local-first, and the n-gram plus spatial stack did not need it. The code remains, disabled, and the dependency is not installed. |
| PPM word candidates in the merge | Measured worthless, and five and a half times the latency. |
| Three-tier proper-noun auto-capitalisation | Fired on ordinary English words and on forms the user had typed lowercase. Pills now mirror what was actually typed. |
| Six built-in vocabulary packs | At 200 to 400 words each they were thinner than the user's own learning after three uses of a phrase. Import still works. |
| A composite "prediction quality score" | A user can act on "you saved 4.2 hours". Nobody can act on "73 out of 100". |
| Six named fuzzy-recognition profiles | The user could not tell which one they wanted. One tuned default replaced them. |
| An edit-mode toggle on the function row | Replaced by a settings page: bigger targets, no mode to escape, and the only surface that shows an assignment the user has forgotten making. |
| The full-size symbol layer | A second route to a set the symbol picker already reached, charged against the two widest keys on the space row. The space bar got the room. |

---

## Influences

Alpha-OSK is assembled from other people's ideas, and it is worth being
specific about which. These are paraphrases of the arguments, not quotations.

- **Dasher** (Ward, Blackwell and MacKay, Cambridge Inference Group)
  established the variable-order character model as the workhorse of
  accessible text entry, and made the deeper argument this project inherits
  wholesale: that text entry is a decoding problem, that probability should
  shape the interface, and that designing for the most constrained user
  produces a better system for everyone. We train the same class of model.
  Section 8.3 of the whitepaper reports where our measurements disagree with
  it, which is itself a form of respect.
- **AOSP LatinIME** supplies the treatment of weighted edit distance, and the
  rule that the literal typed word competes against its corrections. This is
  why "thru" and "lol" survive.
- **VelociTap and the statistical-decoding line** (Kristensson and Zhai;
  Vertanen) supply the idea that a touch is evidence rather than a keypress.
  Our mid-word prefix beam is a small member of that family, with the
  difference that our noise comes from one person's motor system and is
  therefore learnable per user.
- **Presage** supplies the layered framing: several redundant predictors,
  merged.
- **SymSpell** (Garbe) supplies the deletion-index lookup in the whole-word
  correction path.
- **The AAC rate-enhancement literature** (Higginbotham; Trnka and McCoy)
  supplies both the primary metric and the caution about it.

---

## For developers

Five questions worth asking of a change, in the order they usually bite:

1. **Does it take focus, or assume we have it?** If yes, it will not work.
2. **What does it cost in clicks?** Count them for the slowest plausible
   pointer, not yours.
3. **Is there a second route to it?** Right-click, hover and drag are
   shortcuts, never the only way.
4. **What is the evidence?** For anything touching the prediction engine,
   run the benchmark. A plausible mechanism is not a result.
5. **Where is the near-miss test?** Every positive case in this suite is
   paired with the case it must still reject, because a rule that accepts
   everything passes a positive-only test perfectly.

And one rule that is not a question, because it is the failure this codebase
keeps having: **when two places need the same behaviour, they call one
method.** Parallel blocks drift, and they drift silently. The sticky-modifier
release, the verbatim-insert prologue, the prediction-bar refresh and the
typing-context reset were each two hand-written copies that had already
disagreed with each other before anyone noticed.

---

## References

- Ward, D. J., Blackwell, A. F., and MacKay, D. J. C. (2000). *Dasher: a data
  entry interface using continuous gestures and language models.* UIST '00.
- MacKay, D. J. C., and Ward, D. J. (2002). *Fast hands-free writing by gaze
  direction.* Nature 418, 838.
- Cleary, J., and Witten, I. (1984). *Data compression using adaptive coding
  and partial string matching.* IEEE Transactions on Communications.
- Trnka, K., and McCoy, K. F. (2008). *Evaluating word prediction: framing
  keystroke savings.* ACL-HLT.
- Higginbotham, D. J., et al. (2007). *The application of computational
  linguistics to AAC.* AAC 23(1).
- Dasher project documentation: <https://dasher.at>

Alpha-OSK is MIT licensed. Full citations for the prediction stack are in the
whitepaper's reference list.
