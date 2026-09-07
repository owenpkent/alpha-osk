# Alpha-OSK User Study Protocol

**Status:** draft, not yet open for enrolment.
**Version:** 0.1 (2026-09-07).
**Run by:** Owen Kent, the project author. No institutional affiliation, no IRB. See *Ethics posture*.

This document is published **before** anyone enrols, and is versioned in git so that
any change between now and the first participant is visible in the history. That is
deliberate and it is doing a specific job: it is the substitute for the
preregistration and the ethics review that an affiliated researcher would have, and
it is the only thing that stops the analysis being chosen after the data is seen.

---

## 1. The gap this closes

The whitepaper (`docs/WHITEPAPER.md`, section 8) reports that the prediction engine
avoids **49.1% and 50.4%** of keystrokes on the two held-out splits of an AAC-like
corpus, offers the correct next word before any of its letters are typed about three
times in ten, and costs a median 2.9 ms per keystroke.

The whitepaper also says, in its own words, what is wrong with that number:

> KSR is a property of the engine under an idealised user who notices every suggestion
> the instant it appears and never mis-clicks the pill. Real users scan the bar,
> sometimes miss an offer, and pay a visual-search cost that typing the next letter
> does not carry. The number is therefore an upper bound on benefit rather than an
> estimate of it, and the gap between the two is exactly what a user study would
> measure.

And section 9 states the limitation plainly: there is no user study, no participants,
no measured entry rate, and the system has one long-term user who is also its author.

**This study measures that gap and nothing more ambitious.** It is not an attempt to
show the keyboard is good. It is an attempt to find out how much of a simulated 50%
survives contact with a person, which is a question that can come back with an
uncomfortable answer, and the protocol is written so that an uncomfortable answer is
reportable rather than avoidable.

## 2. Research questions

| | Question | Primary outcome |
|---|---|---|
| **RQ1** | How much of the offline keystroke saving is realised in use? | Realised savings (%) against the 49.1 / 50.4% benchmark |
| **RQ2** | Does prediction increase text entry rate, and by how much? | Entry rate (WPM), prediction on vs off |
| **RQ3** | What does prediction cost in accuracy and in perceived effort? | Uncorrected error rate; raw NASA-TLX |
| **RQ4** | Does the benefit depend on pointer precision? | Entry-rate difference regressed on the participant's own measured pointer error |

RQ1 is the headline. RQ2 is the one with a genuine chance of a null or negative
result, and section 3 explains why that is the expected outcome rather than a
disappointing one.

RQ4 exists because the engine has one component, the spatial prefix beam, that the
whitepaper already reports as **worth nothing on clean input and worth eight to nine
points of keystroke savings under pointer error**. If that finding is real, the
benefit should scale with how imprecise the participant's pointing is, and this study
can check that against real pointers instead of a simulated one. The pointer-bias
table the app already learns per physical key slot (`src/prediction/pointer_model.py`)
gives a per-participant precision measure for free, with no extra task.

## 3. What prior work predicts

The expectation going in should be set by the literature, not by the benchmark.

- **Koester and Levine (1996)** measured word prediction with users with spinal cord
  injury and found that keystroke savings did **not** translate into text generation
  rate, and for some users prediction was slower than typing without it. The cost is
  the visual search and the decision: reading a list of candidates takes time that
  pressing the next key does not.
- **Trnka and McCoy (2008)** showed that keystroke savings and observed rate
  improvement diverge substantially, and argued that reporting savings alone
  overstates benefit. The whitepaper already cites this against its own numbers.
- **Soukoreff and MacKenzie (2003)** give the error metrics used here and the argument
  for reporting corrected and uncorrected error separately, since a technique can look
  accurate simply because the participant spent the time fixing it.

So the honest prior is: **realised savings well below 49%, and an entry-rate effect
that may be small, absent, or negative.** If the study returns that, it is a finding,
and the protocol commits in advance to publishing it as one. The interesting question
in that case becomes RQ4, whether the benefit is concentrated in the participants
whose pointing is least precise, which is the population the design is actually for.

## 4. Candidate designs

Three designs were considered. All three are supported by the same scaffold: the
condition list is data, not code (see *Implementation map*), so choosing between them
is a configuration change and not a rewrite. The recommendation is Design A.

### Design A: prediction on vs off, within subjects (recommended)

Counterbalanced blocks of copy typing inside Alpha-OSK, predictions on in one block
and off in the other. About 25 minutes per participant.

- **Answers** RQ1, RQ2, RQ3 and RQ4 directly, and RQ1 exactly.
- **Within subjects**, so each participant is their own control. This matters more
  here than in most text-entry work, because motor ability varies enormously between
  participants and a between-subjects design would need a sample this population
  cannot realistically supply.
- **Cost:** cannot say anything about how Alpha-OSK compares to what the user already
  has. The claim it supports is "the prediction engine is worth this much", not "this
  keyboard is better than that keyboard".

**Recommended** because it is the smallest design that closes the stated gap, and
because session length is the binding constraint in a population where fatigue is a
primary symptom rather than a nuisance variable.

### Design B: add a baseline arm against the OS keyboard

Design A plus a third block on the Windows on-screen keyboard (`osk.exe`). About 40
minutes.

- **Answers** everything A does, plus "is this better than what ships with Windows".
- **Feasible without instrumenting `osk.exe`**, which is the non-obvious part: the
  task harness is a separate window that timestamps text as it arrives, so it measures
  whatever keyboard produced it. No hooking, no injection, no cooperation from the
  keyboard under test.
- **Cost:** 15 more minutes on a fatiguing task, materially higher dropout, and a
  third block makes full counterbalancing need six orders rather than two, so order
  effects are estimated far less well at realistic sample sizes.

Worth doing as a **second study** once A has a result, or as an optional extra block
for participants who say they have capacity, analysed separately and reported as
exploratory.

### Design C: longitudinal field study only

No task battery. Participants use the keyboard normally for several weeks; passive
counters plus a short periodic survey.

- **Answers** questions about sustained real use, retention and self-reported benefit,
  with the highest ecological validity and the lowest burden of the three.
- **Cost:** confounded by construction. There is no controlled comparison, so it
  cannot isolate the prediction engine's contribution, which means it leaves the
  whitepaper's stated gap exactly where it is.

Best treated as **complementary rather than alternative**. The anonymous telemetry
channel (section 10) already collects most of what C would collect, from a much larger
and cheaper sample, so C's marginal value over telemetry-plus-a-survey is small.

## 5. Two constraints this codebase imposes

Both were found by reading the implementation, both invalidate the obvious way to run
Design A, and neither is discoverable from the study design alone.

### 5.1 "Predictions off" must not be the existing settings toggle

`qml/Main.qml` binds the suggestion bar's height to the setting:

```qml
Layout.preferredHeight: root.suggestionsEnabled ? predPillHeight + 4 : 0
```

Turning suggestions off **collapses the bar to zero height and moves every key on the
keyboard up by that amount.** Using that toggle for the off condition would confound
prediction with a change in key geometry, and geometry is exactly what a pointing task
is sensitive to. The measured difference would be part prediction and part relearning
where the keys are, in unknown proportion, in a population whose pointing accuracy is
the thing under study.

**Requirement:** the off condition keeps the bar present, reserved and empty. The
study mode must suppress the *contents* of the bar without touching its height, and
the harness must assert equal key geometry across conditions before a session is
counted as valid.

### 5.2 Learning must be frozen for the whole session

`HybridPredictor.learn()` has no gate, and the model updates on every completed word.
Privacy mode is not a substitute: it suppresses learning but also suppresses
prediction entirely and replaces the pills with "Learning paused", so it changes the
condition under test.

Without a freeze, the prediction-on block trains the model that the same block is
being scored on, and the effect is order dependent: a participant whose on-block runs
second is typing against a model that the off block has already fed. Counterbalancing
spreads that across the group but does not remove it from any individual, and it
inflates the on condition specifically.

**Requirement:** a study session freezes all model mutation at entry and restores it
on exit, so both blocks are scored against a byte-identical model. The freeze must
cover the n-gram tables, the pointer-bias model, the capitalisation table, the token
store and the analytics counters. The participant's own model must be untouched when
the session ends, both because contaminating a real user's model to run a study on
them is not acceptable, and because a participant may withdraw and their data has to
be removable without leaving residue.

**Consequence to state in the paper:** freezing learning measures the engine as a
participant meets it on day one, with a cold personal model. That understates what a
long-term user gets, since the whitepaper's own personalisation figures show the model
improving with use. The study measures first-contact benefit, and should say so.

### 5.3 The trial types into a recorder, not into an application

A trial needs three things at once: the participant types into a field we own,
the prediction engine runs exactly as it normally does, and nothing reaches the
desktop. The obvious mechanism, the existing edit-mode intercept
(`setEditMode` plus `editKeyTyped`), gives the first and third and explicitly
not the second. It returns early, skipping "password detection, analytics,
predictions" in its own comment. Predictions are the thing under study, so a
trial run through edit mode would measure an engine that was switched off.

So the redirect happens at the **synthesiser** instead.
`KeyboardBridge` funnels every character, pill, snippet and special key through
exactly three wrappers (`_send_key`, `_send_text`, `_replace_text`), which is
already the one place in that file that knows text is leaving the keyboard.
`begin_study_capture` swaps `_synth` for a `RecordingSynthesizer`, and a trial
then inherits every invariant in that file for free: suffix-only insertion, the
sticky-modifier release, the deferred auto-space, auto-capitalisation, the
context buffers, the pointer-bias model. Nothing had to grow a study branch,
which matters because `_press_char` is the most invariant-dense function in the
project and a fourth mode inside it would be a standing hazard.

Three consequences worth knowing before changing any of it:

- **The recorder keeps a caret**, because it has to behave like a text field
  rather than a log. A participant who presses Left and fixes a letter in the
  middle of a word is doing an ordinary thing, and a recorder that appended
  blindly would report a transcript nobody typed and then score it as an error.
- **Compat mode is forced off while capturing.** It exists to work around
  applications that intercept synthesised keystrokes, and a trial types into no
  application. Left on, its BackSpace-and-retype replaces a one-click pill with
  a run of backspaces, so a participant who happened to have an IDE focused
  behind the keyboard would score realised savings near zero for a reason that
  has nothing to do with them or the engine.
- **Action kind is inferred from what arrived**, not set by a flag a caller has
  to remember: one click that produced several characters is a pill, one click
  that produced one character is typing. That distinction is the entire
  measurement, and a caller who forgot to set a flag would silently score a
  pill as typing and report savings of zero.

## 6. Measures

Implemented in `src/study/metrics.py`, one function per row, with the formulas pinned
by unit tests against hand-computed values.

| Measure | Definition | Why |
|---|---|---|
| **Entry rate (WPM)** | `(len(transcribed) - 1) / seconds * 60 / 5`, timed from first input action to last | The standard text-entry rate. The `- 1` is standard: timing starts on the first press, so the first character is free |
| **Realised savings (%)** | `(1 - input_actions / len(transcribed)) * 100` | **The RQ1 outcome.** The directly measured counterpart to the benchmark's KSR, computed the same way, so the two are comparable |
| **KSPC** | `input_actions / len(transcribed)` | Goes **below 1.0** here, which is the point: one pill tap contributes many characters. On a physical keyboard KSPC is bounded below by 1 |
| **MSD error rate (%)** | `levenshtein(presented, transcribed) / max(len) * 100` | Whole-string accuracy of the final result |
| **Uncorrected error rate (%)** | `INF / (C + INF + IF) * 100` | Errors the participant left in |
| **Corrected error rate (%)** | `IF / (C + INF + IF) * 100` | Errors they paid to fix. Reported separately, because a technique can look accurate purely by costing more time |
| **Pill acceptance latency (ms)** | Gap from a pill being offered to it being taken | The visual-search cost Koester and Levine identified, measured directly rather than inferred |
| **Raw NASA-TLX** | Six unweighted 21-point scales, after each block | Perceived workload |

An **input action** is one physical click the participant paid for: a character key, a
backspace, a special key, or a prediction pill. An offer is instrumentation and never
counts. That single definition is what makes realised savings comparable to the
offline KSR, so it lives in one place (`metrics.input_actions`) and every measure
derives from it.

**Raw TLX, not full TLX.** The full instrument adds 15 pairwise comparisons to derive
weights. That is 15 extra pointer operations per block from participants for whom
pointer operations are the scarce resource, to produce a weighting that the literature
generally finds makes little difference. The raw version is validated and is what is
used here.

## 7. Materials

**Phrase set.** MacKenzie and Soukoreff's (2003) 500-phrase set is the standard
instrument and is used for the bulk of trials, so results are comparable with the rest
of the text-entry literature. It is supplemented with phrases drawn from the AAC-like
corpus used in the offline evaluation, because the standard set's register is generic
English and this system is used for correspondence and medical communication.

**Contamination is the trap here.** Phrases used in the study must be **held out from
everything the shipped model was built from**: the training corpus, the curated bigram
and trigram seeds, and the vocabulary packs. A phrase the engine was seeded on
produces inflated savings that look like a result. The phrase selection step asserts
disjointness against the seed data and fails loudly rather than warning, and the
selected phrases are committed alongside the analysis so the check is reproducible.

**Phrase assignment** is randomised per participant from the eligible pool, with the
same phrases never repeated across conditions within a participant (which would
measure memory) and the per-condition sets balanced for length and for the proportion
of words present in the base vocabulary.

## 8. Procedure

Remote, unmoderated, on the participant's own machine, with no video call and no
observer. That is a deliberate accessibility decision rather than a convenience one:
requiring a scheduled call with a stranger excludes people on fatigue grounds and on
anxiety grounds, and an observer changes how people type.

1. **Invitation and consent.** `docs/research/STUDY_CONSENT.md`, rendered in the app.
   Operable with the keyboard itself. No signature, no typed name, no email required.
2. **Background questionnaire.** Short. Pointing device, whether any assistive
   technology is used alongside, how long they have used an on-screen keyboard, and a
   free-text field they may leave empty. No diagnosis is asked for and none is needed.
3. **Setup and geometry check.** The harness records window size, layout, compact
   view and key geometry, and refuses to proceed if the two conditions would not have
   identical geometry (see 5.1).
4. **Practice.** Five phrases, not scored, in each condition, so the comparison is not
   between a familiar and an unfamiliar interface. Practice trials are recorded and
   marked, never silently dropped.
5. **Blocks.** Two blocks of 10 scored phrases, order counterbalanced across
   participants. Learning frozen throughout (see 5.2).
6. **Raw TLX** after each block.
7. **Break between blocks**, participant-controlled, with no timer and no prompt to
   hurry. The session may be **resumed on another day**: block boundaries are the
   checkpoints, and a session resumed later is flagged in the data so it can be
   excluded in a sensitivity analysis.
8. **Debrief and submission.** The participant sees their own numbers before deciding
   to submit, and submission is a separate explicit action. Nothing is transmitted
   before that point.

**Stopping is free at any point**, from any screen, with a control that is always
visible, and stopping offers to discard what has been collected so far.

## 9. Participants, recruitment, and what the sample can support

**Inclusion:** adults who use a mouse, trackball, head pointer, eye tracker, joystick
or other pointing device as their primary text input, on Windows or Linux. No
diagnosis requirement: the design constraint is how someone types, not why.

**Exclusion:** none beyond the above. Notably the author is excluded from
participating in his own study.

**Recruitment:** the study page at `alphaosk.com/study`, the project's existing
users, and disability and AT communities. Recruitment in this population is slow, and
the whitepaper already says so.

**Target n = 12 to 20.** Be honest about what that supports. A paired comparison at
n = 12 has roughly 80% power for a large effect (d = 0.8) and is badly underpowered
for a medium one (d = 0.5 needs about 34). This study is therefore **powered to detect
a large effect and not powered to demonstrate absence of a small one.**

The consequence is committed to in advance, in section 11: the analysis is reported as
**estimation with confidence intervals**, not as a significance verdict, and a null
result will be reported as "the interval is consistent with anything between X and Y",
never as "no effect". Underpowered null results dressed as findings are the standard
failure of small accessibility studies and this one is not going to add to the pile.

## 10. Relationship to the anonymous telemetry channel

These are two separate things and the documentation keeps them separate.

| | Telemetry | This study |
|---|---|---|
| Consent | Installer checkbox or Settings, unchecked by default | Full consent form in the app |
| Data | Ten lifetime counters, weekly | Per-trial timing and text |
| Sample | Anyone who opts in | 12 to 20 recruited participants |
| Answers | Sustained use at scale, RQ-adjacent | RQ1 to RQ4 directly |

Telemetry is the closest thing to Design C, at a much larger sample and much lower
cost, which is the main reason Design C is not recommended as a standalone study. Its
counters are exactly the ten already shown on the in-app dashboard, it never carries
content, and its full description is in `docs/architecture/TELEMETRY.md` and
`docs/PRIVACY.md`.

## 11. Analysis plan

Committed in advance. Deviations are recorded in section 14.

**Primary (RQ1).** Realised savings per participant in the prediction-on condition,
reported as a mean with a 95% CI, and compared against the benchmark's 49.1 / 50.4%
as a reference line rather than as a null hypothesis. The headline output is the
**shortfall**: benchmark minus realised, with its interval.

**RQ2.** Paired comparison of entry rate, on vs off, within participant. Report the
mean difference, its 95% CI and a standardised effect size. A paired t-test is
reported for convention, alongside a Wilcoxon signed-rank test, since n is small and
normality is not assumed. **The interval is the result; the p-value is decoration.**

**RQ3.** Uncorrected and corrected error rate and raw TLX, same paired treatment. No
multiplicity correction across RQ2 and RQ3, because these are pre-specified separate
questions rather than a family of tests hunting for one; that choice is stated here
rather than defended afterwards.

**RQ4.** Entry-rate difference regressed on the participant's own pointer imprecision,
taken from the learned pointer-bias table. With n under 20 this is **exploratory and
labelled as such**, and reported as a scatter plot with a fitted line and its interval
rather than as a coefficient with a p-value.

**Order effects** are checked by including block order as a factor and reported
whether or not they are significant.

**Exclusions**, all pre-specified: a trial whose transcription differs from the
presented phrase by more than 50% MSD is treated as an abandoned trial and dropped,
which is the standard convention; a participant who completes fewer than 6 scored
phrases in either block is dropped from the paired analysis and reported separately.
Sessions resumed on a later day are kept, with a sensitivity analysis excluding them.

## 12. Data handling and privacy

The whole system is off-network by default and this study does not change that.

- **No name, email, address, IP or date of birth is collected.** Participants are
  identified by a random code generated on their machine.
- **Typed text is recorded only for the presented phrases**, which are supplied by the
  study and are not the participant's own writing. Nothing the participant types
  outside a trial is recorded at any time.
- **Nothing is transmitted until the participant reviews their own data and presses
  submit.** The bundle is a single JSON file they can open and read first.
- **Withdrawal**: at any time before submission, discarding is immediate and local.
  After submission, the random code is the handle for deletion on request.
- **The participant's own prediction model is never modified** by a session, and never
  transmitted, in whole or in part (see 5.2).
- **Free-text answers are reviewed before publication** and redacted if a participant
  has identified themselves in one, which people do without meaning to.

## 13. Ethics posture

**There is no IRB review and no institutional affiliation.** That is stated here, it
is stated on the study page, and it will be stated in any paper that reports this
study. It is not hidden behind a phrase like "ethics approval was not required".

What is in place instead, and it is deliberately not presented as equivalent:

1. This protocol and the consent form are **published in advance** and versioned in
   git, so the analysis cannot be chosen after the data is seen.
2. **The anonymised data and the analysis are published**, so the result can be
   checked by anyone.
3. The data is **low sensitivity by construction**: no identifiers, no personal
   writing, no diagnosis.
4. Consent, withdrawal and deletion are **implemented in the software**, not promised
   in a document.
5. Participants are **not paid**, which removes the main coercion concern in a
   population where a small payment is not trivial.

**What this costs.** ASSETS, CHI and similar venues generally expect IRB approval or a
documented equivalent for human-subjects research, and a reviewer is entitled to hold
this against the work. The realistic outcomes are that the study is reported as
practitioner evidence rather than as a formal user study, or that it is rerun under an
independent commercial IRB, or under a university collaborator's, before submission.
Section 4 of the whitepaper's future work should say which of those is being pursued.
If the study is ever rerun under review, this document is the protocol that gets
submitted.

## 14. Deviations from protocol

None yet. Every change after the first participant enrols gets an entry here with the
date, what changed and why, and the git history is the audit trail.

## 15. What gets published

In a public `alpha-osk-study` repository:

- this protocol and the consent form, at the version each participant saw
- the per-trial anonymised dataset (CSV), and the aggregate telemetry export
- the analysis as a runnable notebook, so every number in any write-up regenerates
- the phrase sets actually used, and the contamination check that cleared them

The aggregate telemetry counters are published on the same cadence by a scheduled
GitHub Action reading the worker's `/v1/aggregate` endpoint, so the public number and
the number used in analysis are the same number.

## 16. Implementation map

| Protocol section | Code |
|---|---|
| Measures (6) | `src/study/metrics.py`, `tests/test_study_metrics.py` |
| Trial capture (5.3) | `src/study/capture.py`, `KeyboardBridge.begin_study_capture` |
| Phrase sets and contamination check (7) | `src/study/phrases.py` |
| Conditions, counterbalancing, block state (4, 8) | `src/study/session.py` |
| Consent state, participant code, resume (8, 12) | `src/study/config.py` |
| Bar reserved but empty (5.1) | study mode in `qml/Main.qml`, asserted by the harness |
| Learning freeze and restore (5.2) | `HybridPredictor` freeze gate, entered by `session.py` |
| Submission bundle and review-before-send (12) | `src/study/export.py` |
| QML surface | `src/study_bridge.py`, `qml/components/StudyWindow.qml` |

The **condition list is data**, so Designs A, B and C in section 4 are configurations
of the same harness rather than three implementations.

## 17. References

- Hart, S. G., and Staveland, L. E. (1988). Development of NASA-TLX: results of
  empirical and theoretical research. *Human Mental Workload*.
- Koester, H. H., and Levine, S. P. (1996). Effect of a word prediction feature on user
  performance. *Augmentative and Alternative Communication*, 12(3).
- MacKenzie, I. S., and Soukoreff, R. W. (2003). Phrase sets for evaluating text entry
  techniques. *CHI '03 Extended Abstracts*.
- Soukoreff, R. W., and MacKenzie, I. S. (2003). Metrics for text entry research: an
  evaluation of MSD and KSPC, and a new unified error metric. *CHI '03*.
- Trnka, K., and McCoy, K. F. (2008). Evaluating word prediction: framing keystroke
  savings. *ACL-08: HLT, Short Papers*.
- Wobbrock, J. O. (2007). Measures of text entry performance. In *Text Entry Systems:
  Mobility, Accessibility, Universality*.
