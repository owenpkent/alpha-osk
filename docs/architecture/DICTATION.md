# Dictation

Voice input. Click the microphone at the left end of the suggestion bar,
speak, click again. The live transcript renders in the suggestion bar
itself, and each finished phrase is typed into whatever app holds OS
focus, through the same verbatim-insert path a snippet uses.

Off by default, and inert until the user supplies a Deepgram API key.

```
mic click
  -> KeyboardBridge.toggleDictation()
    -> DictationController.start()
      -> MicrophoneCapture  (QAudioSource -> 16 kHz mono s16 PCM)
      -> DeepgramStream     (QWebSocket -> wss://api.deepgram.com/v1/listen)
    <- interim  -> dictationTranscriptChanged -> suggestion bar
    <- is_final -> KeyboardBridge._insert_dictated_text() -> _send_literal_text()
```

## Why it is built entirely on Qt

`QAudioSource` (QtMultimedia) and `QWebSocket` (QtWebSockets) both ship
inside the PySide6 wheel the build already bundles, so the whole feature
costs **zero new Python dependencies** and nothing new for PyInstaller to
carry or the EV cert to sign. That was a constraint worth meeting rather
than a happy accident: the obvious alternatives each drag a native
dependency behind them (`sounddevice` needs PortAudio) or an event loop
that is not Qt's (`websockets` needs asyncio, and therefore a worker
thread and a cross-thread queue between the audio callback and the
socket).

Two consequences follow, and both are better than the alternative rather
than merely cheaper:

- **Qt resamples for us.** `QAudioSource` takes the format you ask for,
  not the device's native one, so the recogniser always sees 16 kHz mono
  signed-16-bit PCM whether the microphone is a 48 kHz interface or a
  44.1 kHz headset. MacroVox sends the device's native rate and declares
  it; that works, but it makes every recogniser parameter a variable and
  leaves an exotic device on an untested path. Fixing the format at the
  capture boundary makes the wire format a constant. 16 kHz is also what
  speech models are trained at, so this is not a compromise: 48 kHz would
  cost three times the bandwidth for no accuracy.
- **There is no thread.** Audio arrives on the Qt UI thread and is
  written straight to a socket that lives on the same thread. At 16 kHz
  mono the whole stream is 32 kB/s and the per-chunk work is a memcpy, so
  the keystroke path is not at risk.

## Files

| Path | Role |
|------|------|
| `src/dictation/config.py` | `dictation.json`, including the API key |
| `src/dictation/audio.py` | `MicrophoneCapture`, the peak detector |
| `src/dictation/providers.py` | `TranscriptionStream` seam, `DeepgramStream` |
| `src/dictation/controller.py` | The state machine, silence and duration timers |
| `src/keyboard_bridge.py` | Slots, signals, and `_insert_dictated_text` |
| `qml/Main.qml` | Mic button, transcript banner, error toast, settings wiring |
| `qml/components/UnifiedSettingsPanel.qml` | The Dictation category |
| `tests/test_dictation.py` | All of the above |

## The interaction model is a toggle, not push-to-talk

Click to start, click to stop. Holding a button down for the duration of
a sentence is exactly the gesture this app exists to avoid: a sustained,
precise hold is the single thing a mouse-driven user with imprecise motor
control cannot reliably make, which is the same argument that removed
swipe typing (issue #39).

Two automatic stops sit behind the click, because a toggle whose "off"
click misses leaves the microphone live and, on a metered API, billing:

- a **silence timeout** (default 4 s, disableable), the one the user
  notices, which ends a run shortly after they stop speaking;
- a **wall-clock ceiling** (default 120 s), which is the backstop for the
  silence detector itself failing, e.g. a room noisy enough that nothing
  ever reads as silence.

Both are configurable, because "a few seconds" is a guess about someone
else's speech rhythm.

## Four states, not a boolean

`idle` / `connecting` / `listening` / `finishing`, surfaced to QML as a
string. Two booleans would permit a fifth state that does not exist, and
the UI genuinely has to render four different things: `connecting` and
`finishing` both have to look busy **without looking like recording**, or
the user clicks again and starts a second run.

`finishing` is not cosmetic. `stop()` closes the microphone immediately
(an open mic light is alarming) but keeps the socket open and sends
Deepgram a `CloseStream`, because the final fragment for the last thing
said arrives *after* that message. Closing on stop would throw away the
tail of the user's sentence, which is the part they are most likely to be
watching for. `FLUSH_GRACE_MS` (2.5 s) is the ceiling, so a wedged server
cannot pin the UI in `finishing`.

## The transcript occupies the suggestion bar

Only the phrase **currently being spoken** is shown. A finalised phrase
leaves the banner and is typed into the app, so what is on screen is
exactly what has *not* been typed yet, and it stays short by construction
rather than by truncation.

It elides on the **left**, which is the opposite of every other elide in
`Main.qml` and is the whole point: while speaking, the tail is what you
are checking.

The pills are hidden for the duration (`predRow.visible` and the
Repeater's model both gate on `!root.dictationActive`). They cannot share
the row, and a pill during dictation would be about a prefix the user is
no longer typing.

## The mic button, and the left edge

It sits at the **left** end of the bar for the same reason the snippets
bookmark was put to the left of the clear-context ring: the ring is
pressed from muscle memory and must not move, and a third control in that
right-hand `Row` would push it. The left end is the only edge of the bar
with nothing on it, so this costs no existing target its position.

`predBar.micReserve` mirrors `clearCtxReserve` and is **zero unless
dictation is switched on**, which is load-bearing rather than an
optimisation: `predRow.x` centres the pills in
`width - micReserve - clearCtxReserve`, so a reserve of 0 collapses that
expression back to exactly the single-reserve one it grew from. A user
who never turns dictation on gets byte-identical bar geometry.

`computeFit` is passed `clearCtxReserve + micReserve` as its single
`reserve` argument rather than gaining a tenth parameter. The fitter has
no notion of *which side* a reserve sits on; it only needs to know how
much of the bar the pills may not have. Keeping the signature keeps the
no-elide guarantee true once a button is taking space off the left, which
is what `tests/test_qml_prediction_bar.py` is asserting.

**There is a title-bar mirror** (`dictationTitleBarButton`), visible only
when `suggestionsEnabled` is false. The suggestion bar collapses to zero
height with that setting, taking everything in it, and an unrelated
setting must not be able to remove the only way into a feature. This is
the precedent `snippetsTitleBarButton` set, and the hole the
clear-context button still has.

Both icons are Feather's `mic` drawn through `StrokeIcon`, never a glyph:
on Windows a small glyph commonly resolves through Segoe UI Emoji, which
renders in colour and ignores the `color` property outright.

**The busy pulse drives a `pulse` property, never `opacity` directly.**
`SequentialAnimation on <property>` takes ownership of what it animates
and does not give it back when it stops, so animating `opacity` destroyed
the `ready ? 1.0 : 0.45` binding on the first connect and left the button
parked wherever the loop happened to be, which for half of each cycle is
nearly transparent. A separate property carrying no binding is a thing an
animation can own safely; `opacity` multiplies it in, and
`onRunningChanged` resets it rather than trusting where the loop stopped.
The same applies to any future animation on a bound property here.
Guarded by `test_a_busy_pulse_does_not_eat_the_opacity_binding`, which
proves the binding is *live* afterwards by changing what it depends on,
rather than merely reading 1.0, which a dead property could also do.

## Insertion mirrors `insertSnippet` exactly

`_insert_dictated_text` is a verbatim insert and carries every invariant
that path documents, in the same order:

- `_release_sticky_modifiers()` **before** the send. A held Shift would
  otherwise deliver the whole sentence in capitals and a held Ctrl would
  make every character a chord; `_make_char_scancode_events` only knows
  not to *add* a redundant Shift wrap, it cannot cancel a standing hold.
- The send goes through `_send_literal_text`, i.e. inside
  `_without_held_modifiers()`, which drops the holds for the duration and
  restores them. That is what keeps a right-click **lock** intact.
- `_consume_auto_cap()`, because a dictated phrase is the next thing
  typed and therefore takes the capital. Leaving the flag armed hands the
  same capital to a later unrelated character.
- A deferred auto-space is settled with `prose=True`: a spoken phrase is
  prose.
- **Not gated on privacy mode.** Same rule as every other insert path:
  the user asked for this text. Privacy is enforced by stopping the run
  (below), upstream of the typing.

### Spacing between phrases

Deepgram returns phrases with no surrounding whitespace, so consecutive
finals would run together (`hello worldhow are you`). One space is
prepended, decided from **what is actually on screen**
(`_context_buffer + _current_word`) rather than from a "have I inserted
yet" flag, so a phrase following text the user typed by hand spaces
correctly too. Suppressed after an opening bracket or quote.

### The buffers are updated, the model is not taught

`_context_buffer` and `_sentence_buffer` are appended to (same 200-char
window every other append uses) so the next-word predictions that come
back after dictation are built on what is really in front of the caret.
That is not merely about suggestion quality: those buffers are what the
insert path *measures against*, and a buffer disagreeing with the screen
is how a later pill tap eats text the user typed.

Two details that are easy to get wrong, and one was:

- **The deferred auto-space is not re-added here.**
  `_take_deferred_space` already mirrors it into `_context_buffer`
  itself, so appending it again put two spaces in the buffer where the
  screen has one. Guarded by
  `test_a_deferred_space_is_mirrored_once_not_twice`, which asserts
  buffer-equals-screen rather than counting spaces, so it states the
  property and not the symptom.
- **The append is suppressed in privacy mode**, exactly as
  `pressPrediction`'s own append is: the buffer feeds `predict()` and
  `learn_from_selection()` on the next call. The insert still happens,
  because the user asked for the text; only the memory of it is
  withheld.

To be exact about the scope of the claim: what is guaranteed is that
*dictation's own contribution* lands in the buffer as it was typed. The
buffer is not a byte-perfect mirror of the screen in general, and never
has been. A bare digit run, for instance, is dropped by the punctuation
branch of `_press_char` before dictation is involved at all, so after
typing `total 42.` the buffer already reads `total .`. That is
pre-existing, sits in the word model's own accounting, and is out of
scope here; do not "fix" it as part of a dictation change.

The prediction model is deliberately **not** taught from dictated text.
Those words came out of Deepgram's vocabulary rather than the user's
typing, and feeding them into `user_vocab` is a prediction-engine change
that deserves its own decision rather than riding in on this one.

## Privacy mode cancels the run

`_enter_privacy_mode` calls `DictationController.cancel()`, not `stop()`.
The difference is the point: `cancel` does not wait for the provider to
flush, so a run that was under way when the caret landed on a password
field cannot deliver one more sentence afterwards.

Privacy mode has to mean "stop doing this", not "stop starting new ones",
the same rule `_withdraw_snippet_offer` follows. The reason it is
stronger here than for the buffer scrub is that the thing being prevented
is **streaming a spoken password to a third party**, not a bad
suggestion.

`toggleDictation` also refuses outright while privacy mode is on, and
says why.

**`cancel()` disconnects the controller's own handlers before aborting
the stream, and the disconnect is the load-bearing half.** Clearing
`self._stream` only stops us *sending* to the provider; it does nothing
about the provider still *telling us* things, and a websocket can have a
`textMessageReceived` already sitting in the Qt event queue at the moment
of teardown. Without the disconnect, a cancelled run went on to emit one
more finalised phrase and type it into whatever field the user had moved
to. This is not something to leave to the provider being well-behaved
about its own teardown. Guarded by
`tests/test_dictation.py::TestTheRunLifecycle::test_cancelling_drops_what_is_in_flight`,
which is the test that found it.

## The API key

Stored in `dictation.json` in the config dir, written atomically
(tempfile-then-rename) on every mutation, the same way `snippets.json`
is.

**It is deliberately absent from the Data Backup archive.** The archive
exists to be carried between machines and handed around, which is the
opposite of what a credential wants; `telemetry.json` is excluded for the
same class of reason. Do **not** add `dictation.json` to `_MODEL_FILES`
in `src/data_export.py`. `TestTheKeyNeverLeavesTheMachine` asserts the
absence, so re-adding it fails loudly rather than quietly shipping a key
inside every backup a user makes.

**On Windows the key is wrapped with DPAPI** (`CryptProtectData` via
ctypes, no new dependency), which ties the ciphertext to the user
account: a copied `dictation.json` is inert on another machine or under
another login. This is not a claim that the key is safe from code already
running as the user, because nothing on a desktop is and a keyring would
be no better there. What it buys is that the key is not sitting in
plaintext in a file that gets synced, screen-shared, or attached to a bug
report. On Linux and macOS there is no equivalent that avoids a new
dependency, so it is plaintext with the file mode narrowed to 0600, and
`key_protected` records which form is on disk so a file written on either
platform still loads on the other.

The key is never logged, never returned to QML in the clear
(`getDictationSettings` returns `hasKey` plus a masked preview), and
never placed in the websocket URL: Deepgram authenticates the upgrade
request, so it rides in an `Authorization` header, where a proxy or
server access log along the way will not capture it.

## No automatic reconnect, on purpose

A dropped socket mid-utterance means the audio spoken during the gap is
gone. A reconnect that hides that produces a transcript with an invisible
hole in it: words the user said, believes they said, and cannot see are
missing. For dictation that is worse than a visible failure, so a drop
ends the run and says so through `dictationErrorToast`.

Errors are mapped to sentences with a next step in them
(`_friendly_error`). Nobody can act on
`QAbstractSocket::RemoteHostClosedError`; they can act on "check your key
in Settings" and "check your connection", and those two cover
essentially every real failure.

A socket that closes **before any transcript arrived** is reported as a
probable key problem, because otherwise the button spins back to idle
having said nothing at all, which is indistinguishable from a click that
did not register. A close after a successful run is silent.

## Nothing here logs what was said

Same rule as the rest of the keystroke path, and for the same reason: the
diagnostic log is what users attach to bug reports, and a dictated
sentence is exactly as private as a typed one. The controller logs states
and the bridge logs `len(phrase)`; no module in `src/dictation/` ever
interpolates transcript text into a log record.

## Bridge surface

Signals (QML binds these in `Main.qml`'s `Connections { target: keyboard }`):

| Signal | Carries |
|--------|---------|
| `dictationStateChanged(str)` | `idle` / `connecting` / `listening` / `finishing` |
| `dictationTranscriptChanged(str)` | the in-flight phrase, empty when there is none |
| `dictationLevelChanged(float)` | mic peak, 0.0 to 1.0, for the button's ring |
| `dictationError(str)` | a user-facing sentence; always paired with a return to idle |

Slots:

| Slot | Notes |
|------|-------|
| `toggleDictation()` | The only control the UI needs. Refuses in privacy mode, with a reason. |
| `getDictationSettings() -> QVariantMap` | Everything the settings view renders, in one call. **Never returns the key in the clear**: `hasKey` plus a masked preview. Deliberately excludes the device list. |
| `getDictationDevices() -> QStringList` | Separate because it walks the drivers (~29 ms). Called when the settings view opens, not at startup. |
| `setDictationApiKey(str) -> bool` | **Honour the bool.** The store can refuse the write, and a green "Saved" over a key that never landed sends the user away believing dictation is set up. |
| `clearDictationApiKey() -> bool` | Cancels any live run too. |
| `setDictationEnabled(bool)` | Cancels a live run when switching off, the failure `setSnippetDetection(False)` learned. |
| `setDictationModel/Language/Device(str)` | Model and language are validated against the allow-lists in `config.py`; an unknown value is ignored rather than stored. |
| `setDictationMaxSeconds(int)` / `setDictationSilenceSeconds(int)` | Clamped to the ranges in `config.py`. |
| `setDictationStreamInserts(bool)` | Stored; see *Known gaps*. |
| `setDictationKeyterms(str)` | Newline-separated; deduplicated, flattened and bounded on the way in. |

Every setter persists immediately, the same way `SnippetStore` does, so there is no on-quit save path to wire up.

**These settings deliberately do not follow the 8-step `appSettings` recipe** in `CLAUDE.md`. They live in `dictation.json` because the API key has to, and a record split across two stores is a record whose halves drift. The one exception is `savedDictationEnabled`, which *is* mirrored into `appSettings`: QML has to decide whether to reserve the bar's left edge before it has asked the bridge anything, and a button that pops in a frame late shifts every pill sideways under a pointer already moving. `Main.qml`'s `refreshDictation(withDevices)` pulls the rest of the record in on startup and on each settings-panel open.

## Known gaps

- **The API key field takes physical-keyboard or pasted input only.**
  Alpha-OSK cannot hold OS focus, so typing into a `TextField` with the
  OSK needs the `setEditMode(true)` + `editKeyTyped` plumbing the
  prediction-edit popup and the snippets editor use. Wiring that for the
  settings panel is a follow-up. Pasting a key is the expected path
  anyway (nobody types 40 hex characters by hand, least of all here).
- **Dictation is a no-op while an edit popup is open.** Routing a phrase
  into the snippets editor or the prediction-edit field would need an
  "insert this string" edit signal; both surfaces currently take one
  character at a time. Dropping the phrase beats typing it into the app
  behind the popup, which is not where the user is looking.
- **`stream_inserts` is stored and surfaced but the accumulate-then-
  insert-once branch is not implemented.** Every run currently types each
  phrase as it finalises.
- **One provider.** The `TranscriptionStream` contract is four signals
  and three methods precisely so a second one does not require touching
  the controller or the bridge.
- **The frozen build has not been exercised with a real microphone yet.**
  `PySide6.QtMultimedia` / `QtNetwork` / `QtWebSockets` are named in both
  PyInstaller specs, but naming a *module* is not the same as bundling
  its Qt **plugins**: QtMultimedia needs its `multimedia` plugin
  directory present to open a device at all. The app already links
  QtMultimedia for the key-click `QSoundEffect`, so the plugin is
  probably already coming along, but "probably" is not a release
  criterion. Before the first release that ships this, launch the built
  exe and complete one dictation run; a missing plugin shows up as
  "The microphone could not be opened." rather than as a crash.

## Relationship to MacroVox

MacroVox (`C:\Users\owenp\dev\MacroVox`) is the same developer's
standalone dictation app and was the reference for the Deepgram
parameters: `model` / `punctuate` / `smart_format` / `interim_results`,
with `keyterm` (not the legacy `keywords`, which nova-3 rejects) for
custom vocabulary. It is Rust and TypeScript throughout, so nothing was
ported directly; what was taken is the proven parameter set and the
`{transcript, is_final}` contract, which MacroVox arrived at
independently in its own provider-seam plan.

Three things are deliberately different:

| | MacroVox | Alpha-OSK |
|---|---|---|
| Audio format | device native, declared to Deepgram | fixed 16 kHz mono s16 |
| Interim results | requested, then ignored | requested and shown live |
| Delivery | clipboard, then a synthetic Ctrl+V | typed via the verbatim-insert path |

`docs/roadmap/MACROVOX_INTEGRATION.md` describes the *other* direction,
launching and coordinating with MacroVox as a separate process. Native
dictation does not replace that plan; the two answer different questions
(the integration plan is about MacroVox's AI cleanup and agentic writing,
which this does not attempt).
