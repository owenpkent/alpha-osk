# Dictation

Voice input. Click the microphone at the left end of the suggestion bar,
speak, click again. The live transcript renders in the suggestion bar
itself, and each finished phrase is typed into whatever app holds OS
focus, through the same verbatim-insert path a snippet uses.

Off by default, and inert until the user supplies a Deepgram API key.

Two optional passes can run the transcript through Claude on the user's own
Anthropic key (**AI cleanup** and **Write with AI**), and a short **history**
of runs is kept locally. Each is off or inert until switched on; see the
sections of those names below.

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
| `src/dictation/claude.py` | `build_request` / `parse_response` (pure), `ClaudeRequest`, `ClaudeClient` over `QNetworkAccessManager` |
| `src/dictation/prompts.py` | The cleanup and writing prompts |
| `src/dictation/history.py` | `DictationHistory`, the local store of past runs |
| `src/dictation/controller.py` | The state machine, silence and duration timers, the Claude passes |
| `src/keyboard_bridge.py` | Slots, signals, and `_insert_dictated_text` |
| `qml/Main.qml` | Mic button, transcript banner, error toast, settings wiring |
| `qml/components/UnifiedSettingsPanel.qml` | The Dictation category |
| `tests/test_dictation.py` | The controller, config and bridge |
| `tests/test_dictation_claude.py`, `tests/test_dictation_history.py` | The Claude client and the history store |

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

## Five states, not a boolean

`idle` / `connecting` / `listening` / `finishing` / `polishing`, surfaced
to QML as a string. Two booleans would permit a sixth state that does not
exist, and the UI genuinely has to render five different things:
`connecting`, `finishing` and `polishing` all have to look busy **without
looking like recording**, or the user clicks again and starts a second run.
(`polishing` is the newest: it is the wait for Claude, see below.)

`finishing` is not cosmetic. `stop()` closes the microphone immediately
(an open mic light is alarming) but keeps the socket open and sends
Deepgram a `CloseStream`, because the final fragment for the last thing
said arrives *after* that message. Closing on stop would throw away the
tail of the user's sentence, which is the part they are most likely to be
watching for. `FLUSH_GRACE_MS` (5 s) is the ceiling, so a wedged server
cannot pin the UI in `finishing`.

## AI cleanup

Off by default (`cleanup_enabled`), and live only when the user has also
saved an Anthropic key (`DictationConfig.cleanup_ready`). With it on, a
dictation run is **held back and inserted once**: finals accumulate in
`_run_text` instead of being typed phrase by phrase, the suggestion bar
shows everything so far plus the phrase in flight, and when the run ends
the whole transcript goes to Claude Haiku (`claude-haiku-5-5`, effort
`low`, 20 s timeout, `max_tokens` from the input length, capped at 8000).
The cleaned text is then inserted through the normal verbatim path.

Held back is the price of a coherent edit: cleaning one phrase at a time
cannot fix a sentence that spans two finals, and text already typed into
another app cannot be taken back. Both decisions, whether finals are
held back (`_accumulate`) and whether the finished transcript goes to
Claude (`_polish`), are made once at `start()` and `_finish_run` reads
those two fields, never the live config. The finish used to re-read
`cleanup_ready`, so cleanup switched on after the first streamed final had
been typed sent the whole run for polishing and typed the result again on
top of the phrases already on screen; turned off mid-run, a held-back run
still polishes, because the run it started is the run it finishes.

The prompt (`prompts.py`) fixes recognition slips, punctuation and
capitalisation, drops fillers only when clearly disfluent, keeps the
speaker's words, never adds content, and treats the transcript as data
inside `<transcript>` delimiters so a dictated "ignore the above" is not
obeyed. It is told the dictation language (`multi` means keep each
sentence's language) and the number format setting (`smart`, `digits` or
`words`).

## Write with AI

A second button (off by default, `writing_enabled`, needs both keys): the
user speaks an instruction ("write a polite email declining the meeting"),
and Claude Sonnet (`claude-sonnet-5-5`, effort `medium`, 60 s timeout,
`max_tokens` 8000) composes the text. The result arrives as `draftReady`,
and **a draft is never typed on its own.** It opens a preview popup on the
keyboard window with Insert, Copy and Discard, because this text was
written by a model rather than said by the user, and a wrong paste into a
live app is costly to undo. Insert goes through `insertDictationDraft`, the
same verbatim path a dictated phrase uses; Copy goes to the clipboard. An
optional style profile (`writing_style`, up to 2000 characters, newlines
kept and other control characters stripped) rides in `<style_profile>`
delimiters and describes the user's voice.

Only the writing model sends the server-side fallback beta header and
`"fallbacks": "default"`, so Anthropic retries a spurious refusal on
another model rather than handing the user a "declined" they cannot easily
rephrase their way around. Haiku has no fallback target. No request sends
`thinking` or sampling parameters (these models reject them) or an
assistant prefill.

## Speech is never lost to the LLM

This is the rule the controller is built around. Held-back text exists
nowhere but in `_run_text`, so every failure path delivers or keeps it:

- **Cleanup fails** (bad key, rate limit, timeout, refusal, truncation,
  empty reply, no connection): the raw words are inserted anyway and the
  error says so ("AI cleanup failed (reason). Inserted as spoken.").
- **Write with AI fails**: nothing is typed, but the spoken instruction is
  recorded in history, so the user can see it again. With history off (or
  if the store refuses the write) there is no durable copy, so the message
  must not promise one: `_record` reports whether anything was kept, and
  when nothing was, the raw instruction is emitted as `draftReady` so the
  preview shows it to be read back or copied, and the error says that
  instead of "saved in History". The preview is the one surface that can
  hold it without writing to disk against the user's setting.
- **The Deepgram stream drops mid-run** while finals are held back:
  `_on_stream_failed` finishes the run and delivers what was said, rather
  than cancelling and discarding it. A held-back run must not be worse off
  than a streamed one, where the phrases already typed would have stayed.
- **The microphone fails mid-run** (unplugged, claimed by another app):
  `_on_capture_failed` *stops* rather than cancels once anything has been
  said. The socket is still up, so the provider flushes the tail of the
  last phrase and the run ends through the ordinary finish: a held-back
  run is delivered (or polished), a streamed one gets its history record.
  Only a run with nothing said yet is torn down. It used to cancel
  outright, which cleared a held-back run nobody had typed yet.
- **A tap on the mic while `polishing` does nothing.** The words are
  already with Claude, and treating a stray tap as a cancel would throw
  them away.

The one exception is the one that matters: **privacy mode calls `cancel()`,
which aborts the in-flight Claude request and delivers nothing.** A spoken
password must not be finished by a model on the way out.

`stream_inserts = False` is implemented by the same machinery: with it off
and no cleanup, the run is held back and inserted once, raw.

## Claude is called over raw HTTPS on Qt, not through the SDK

`claude.py` posts to `https://api.anthropic.com/v1/messages` with
`QNetworkAccessManager`. The `anthropic` package would be a new dependency
to freeze and sign, and `requests` would need a thread; the Qt route keeps
the zero-new-dependency, no-thread property the rest of this package has.
`build_request` and `parse_response` are pure functions (no Qt), so
headers, bodies and every error mapping are tested without a network.
`ClaudeRequest` is one-shot, owns a timeout timer, and fires no signal
after `abort()`. `ClaudeClient.send` asserts the host is
`api.anthropic.com`. The controller takes the client by constructor
argument, so tests substitute a fake and never reach the network.

`parse_response` keeps only `text` blocks (a `thinking`, `fallback` or any
later block type is not the reply), rejects a body over 1 MB, and maps each
status class to a short fixed sentence rather than echoing the server's
message, which can quote the input.

## History

The last 20 runs (`history_enabled`, **on** by default) are kept in
`<config>/dictation_history/`: an `index.json` plus one 16 kHz mono WAV per
run, so a run can be played back, copied, reprocessed through cleanup or
deleted. It exists because a held-back run and a failed cleanup are exactly
when the user wants the original words back. It is local and nothing in it
is sent anywhere on its own; the one outbound path is Reprocess, below.

Caps and validation (`history.py`): 20 entries, 200 MB of audio in total,
a 2 MB index checked by `stat()` before it is opened, 20 000 characters per
text field. Ids are `secrets.token_hex(8)` and validated against
`^[0-9a-f]{16}$` on load; **the WAV path is always derived from the
validated id, never read from the index**, so a hand-edited index cannot
point playback or delete at another file. A bad entry is dropped on load;
a WAV with no entry is deleted on load, but only if its name is exactly
`<valid-id>.wav`. The store never raises on an I/O failure (it logs without
content and reports False). A cancelled run is not recorded; a run whose
cleanup failed is, with `cleaned` false.

`reprocess(entry_id)` re-runs cleanup over an entry's raw text, updates the
entry in place and emits `historyChanged`. It is refused while a live run
is active and for write entries, and **it carries the same two gates a live
cleanup does**: the controller refuses it unless `cleanup_ready` (the AI
cleanup toggle, not merely a stored key: the privacy page promises that
with both AI features off Anthropic is never contacted, and a key left in
Settings is not consent to sending stored text), and the bridge refuses it
in privacy mode, since the controller cannot see that flag. The Settings
button is shown only while cleanup is on, so reaching a refusal means the
settings changed underneath it. The bridge exposes `getDictationHistory`,
`playDictationHistory` (QtMultimedia, created lazily),
`stopDictationPlayback`, `copyDictationHistory`,
`reprocessDictationHistory`, `deleteDictationHistory` and
`clearDictationHistory`; entries carry `hasAudio` but never a filesystem
path.

**The folder is deliberately absent from the Data Backup archive**: it holds
recordings of the user's voice, and the archive is a file that gets carried
and handed around. Do not add it to `data_export`'s allow-list.

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

**A held-back run shows nothing here** (the owner's call, 2026-10-24).
With Type As You Speak off, or cleanup on, `_on_interim` and `_on_final`
set no interim at all, so the banner shows its own "Listening..." label
until the run is typed in one go. It used to show everything heard so far
plus the phrase in flight, which read as "it is typing" when it was not.
The level bars beside the mic (next section) carry "it hears you" instead.
The same holds while Claude has the text: `_begin_polish` clears the
interim rather than showing the raw run, so the banner reads
"Polishing..." or "Writing..." until the cleaned text is typed or the
draft preview opens (the review of #212 caught the raw text reappearing
there). Guard: `tests/test_dictation_ai.py::TestAccumulateMode`, through
`stop()` and the provider's `closed` for both cleanup and Write with AI.

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

**The level bars are the only sign the mic hears you.** Four bars sit
beside the mic (`micLevelBars`, in `predBarMicRow`), visible only while
`dictationState === "listening"`, for a mic run or a Write run alike. The
owner picked them over a louder ring, a bottom fill, ripples and a
whole-bar wash (`docs/roadmap/dictation-speaking-indicator.html` shows
all five side by side; chosen 2026-10-24). They widen `micReserve` by
their width plus 8 px while shown; the pills are hidden for the whole run,
so only the transcript label moves, and the reserve returns to exactly
`predPillHeight + 16` at idle.

They replace a ring that nobody ever saw move, and the reason was not the
ring: it was fed the **raw peak**. Speech into a desktop mic peaks at
0.05 to 0.3 of full scale, so `10 × level` pixels was one pixel and
`0.15 + 0.5 × level` opacity was near-transparent. `audio.display_level`
maps the peak in decibels between the silence floor (`SILENCE_PEAK`,
0.015, the same constant the auto-stop reads) and `DISPLAY_CEILING`
(0.5), and the bridge's `_on_dictation_level` emits that, so every QML
consumer of `dictationLevel` reads a usable 0 to 1. The controller's
`levelChanged` stays raw: the silence auto-stop needs it. Guards:
`tests/test_dictation.py::TestTheDisplayLevel`,
`tests/test_qml_dictation.py::TestTheLevelBarsSayTheMicHearsYou`.

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

**The Anthropic key follows every rule above.** It lives in the same
`dictation.json` as `anthropic_key` with its own protected flag (DPAPI on
Windows, 0600 elsewhere), is never logged, never returned to QML (only
`hasAnthropicKey` and a masked preview), is absent from the Data Backup
archive with the rest of the file, and travels only in the `x-api-key`
request header, never in a URL. `DictationConfig.__repr__` leaks neither
key.

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

### What a failed run keeps (lessons from MacroVox, 2026-10-24)

MacroVox lost a dictation three ways in one afternoon: a stop whose wait
was shorter than the provider's flush, a stop response with no transcript
that the UI trusted over the finals it had already shown, and a failed
upload that discarded the audio with the error. The same three rules now
hold here.

- **A stream that fails after anything was heard ends like a stop.**
  `_on_stream_failed` calls `_finish_run` whenever `_run_text` is
  non-empty, not only for a held-back run: streamed finals were typed as
  they arrived, but the history record (with its audio) was owed too, and
  `cancel()` used to drop it with the socket.
- **A stream that fails before anything was transcribed keeps its audio.**
  `_keep_failed_audio` records an audio-only history entry (empty `raw`
  and `result`) when history is on and at least a second of audio was
  captured (`_MIN_KEPT_PCM_BYTES`), and the toast ends with "The audio is
  saved in History." The History row shows "Not transcribed. Play the
  audio to hear what was said." with Play and Delete only: nothing to
  copy, nothing to send to Anthropic. The same applies to the early-close
  path (`_on_stream_closed` before any transcript, the rejected-key case).
  Privacy mode's `cancel()` is still the one path that discards audio.
- **The provider's two deadlines report themselves.** `finish()` arms
  `FLUSH_GRACE_MS` (now 5 s; Deepgram answers `CloseStream` in well under
  a second, MacroVox waits 8 s, and a slow but healthy flush must not read
  as a failure). Running out of it emits `failed(FLUSH_TIMEOUT_MESSAGE)`
  rather than a quiet `closed`, so a missing tail is said out loud; the
  controller suppresses that toast only when nothing was ever heard.
  `start()` arms `CONNECT_TIMEOUT_MS` (10 s) so a dropped upgrade request
  cannot leave the mic open and the button spinning until the OS gives
  up. `failed` stands in for `closed` (never both), as before.
- `_friendly_error` maps `ssl` / `tls` / `certificate` to "Could not make
  a secure connection", the wording a proxy or inspecting firewall
  produces. `handshake` alone is not a marker: Qt says `processHandshake`
  for a plain HTTP refusal too.

Tests: `tests/test_dictation.py::TestTheProviderGivesUpOnTime` (a real
`DeepgramStream` over a fake socket), `tests/test_dictation_ai.py::
TestAFailedRunKeepsWhatWasHeard`, and the audio-only row in
`tests/test_qml_dictation_ai.py::TestTheHistoryList`.

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
| `dictationStateChanged(str)` | `idle` / `connecting` / `listening` / `finishing` / `polishing` |
| `dictationTranscriptChanged(str)` | the in-flight phrase, empty when there is none |
| `dictationLevelChanged(float)` | mic level, 0.0 to 1.0, decibel-mapped by `audio.display_level` (not the raw peak), for the level bars |
| `dictationError(str)` | a user-facing sentence; always paired with a return to idle |
| `dictationDraftReady(str)` | a Write with AI draft, for the preview popup; never typed on its own |
| `dictationHistoryChanged()` | a run was recorded, or a reprocess updated an entry |
| `dictationPlaybackChanged(str)` | the id now playing, empty when stopped |

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
| `setDictationStreamInserts(bool)` | Off holds a run back and inserts it once, raw (or cleaned, if cleanup is on). |
| `toggleAiWriting()` | Starts or stops a Write with AI run. Refused in privacy mode, when writing is off, or when either key is missing, each with a sentence naming the fix. |
| `insertDictationDraft(str) -> bool` / `copyDictationDraft(str) -> bool` | The draft popup's Insert and Copy. Insert shares the dictated phrase's insert path (`_insert_spoken_text`), is not gated on privacy mode, and teaches nothing. |
| `setDictationAnthropicKey(str) -> bool` / `clearDictationAnthropicKey() -> bool` | Honour the bool, like the Deepgram key. |
| `setDictationCleanupEnabled(bool)` / `setDictationNumberFormat(str)` | The number format is allow-listed. |
| `setDictationWritingEnabled(bool)` / `setDictationWritingStyle(str) -> bool` | |
| `setDictationHistoryEnabled(bool)` and the history slots | See *History*. |
| `setDictationKeyterms(str)` | Newline-separated; deduplicated, flattened and bounded on the way in. |

Every setter persists immediately, the same way `SnippetStore` does, so there is no on-quit save path to wire up.

**These settings deliberately do not follow the 8-step `appSettings` recipe** in `CLAUDE.md`. They live in `dictation.json` because the API key has to, and a record split across two stores is a record whose halves drift. The one exception is `savedDictationEnabled`, which *is* mirrored into `appSettings`: QML has to decide whether to reserve the bar's left edge before it has asked the bridge anything, and a button that pops in a frame late shifts every pill sideways under a pointer already moving. `Main.qml`'s `refreshDictation(withDevices)` pulls the rest of the record in on startup and on each settings-panel open.

## Known gaps

- **The API key fields take pasted input in practice.** The settings
  window cannot hold OS focus, so typing a key into a `TextField` with the
  OSK would need the edit-session plumbing the snippets editor uses. That
  is still not wired, but it is mitigated: the key fields have large Paste
  and Save buttons, so the whole path is two clicks and no typing. (Nobody
  types 40 hex characters by hand, least of all here.) Paste and Save
  clears the field first (`TextField.paste()` inserts at the caret, so a
  second tap used to save the key twice), and both key setters collapse an
  exact doubled paste as a backstop. Qt's wording for a Deepgram 401
  ("Unsupported WWW-Authenticate challenges") maps to the check-your-key
  message in `_friendly_error`.
- **Dictation is a no-op while an edit popup is open.** Routing a phrase
  into the snippets editor or the prediction-edit field would need an
  "insert this string" edit signal; both surfaces currently take one
  character at a time. Dropping the phrase beats typing it into the app
  behind the popup, which is not where the user is looking.
- **Held-back runs show nothing in the target app until they end.** The
  suggestion bar shows only "Listening..." and the level bars (the running
  transcript preview was removed at the owner's request, 2026-10-24), and
  a long run with cleanup on types nothing until the user stops and Claude
  answers (up to the 20 s timeout). That is the price of the single coherent insert (see
  *AI cleanup*); `stream_inserts` on with cleanup off keeps phrase by
  phrase typing. (The earlier gap, that `stream_inserts = False` was stored
  but not implemented, is closed.)
- **Claude is a second third party.** With cleanup or writing on, the
  transcript goes to Anthropic as well as the audio to Deepgram. Both are
  opt-in and keyed by the user; see `docs/PRIVACY.md`.
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
`{transcript, is_final}` contract.

**Its missing features have since been absorbed natively** (2026-10-11):
AI cleanup, Write with AI (MacroVox's agentive writing) and a dictation
history, all on the user's own Anthropic key. Their prompts were ported by
intent, not verbatim. Deliberately **not** ported: the global hotkey (the
mic is a button on a keyboard the user is already using), accounts, billing
and subscription gating (a bring-your-own-key tool has none), and voice
commands.

Three things stay different:

| | MacroVox | Alpha-OSK |
|---|---|---|
| Audio format | device native, declared to Deepgram | fixed 16 kHz mono s16 |
| Interim results | requested, then ignored | requested and shown live |
| Delivery | clipboard, then a synthetic Ctrl+V | typed via the verbatim-insert path |

`docs/roadmap/MACROVOX_INTEGRATION.md` keeps the history of the earlier
plan to launch and coordinate with MacroVox as a separate process, and says
which of it is now moot.

---

# Implementation notes (from CLAUDE.md)

Moved verbatim from `CLAUDE.md` on 2026-09-20, which keeps a summary of the load-bearing rules. This is the full reasoning.

## Dictation (voice input)

Click the mic at the **left end of the suggestion bar**, speak, click again. The live transcript renders in the bar itself; each finalised phrase is typed into the focused app through the verbatim-insert path. Off by default and inert without a Deepgram API key. Full write-up: `docs/architecture/DICTATION.md`. Code: `src/dictation/` (`config` / `audio` / `providers` / `controller`), `KeyboardBridge._insert_dictated_text` + the `setDictation*` slots, `qml/Main.qml` (mic button, transcript banner, `dictationErrorToast`), the Dictation category in `UnifiedSettingsPanel.qml`.

- **Built entirely on Qt, and that is a constraint rather than a coincidence: zero new Python dependencies.** `QAudioSource` (QtMultimedia) captures and **resamples for us**, so the recogniser always sees 16 kHz mono signed-16-bit PCM whatever the device natively runs at, and `QWebSocket` (QtWebSockets) speaks to the provider on the Qt event loop, so there is no worker thread, no asyncio, and no queue between the audio callback and the socket. Both already ship in the PySide6 wheel the build bundles. `sounddevice` would drag PortAudio in as a binary for PyInstaller to carry and the EV cert to sign; MacroVox sends the device's native rate and declares it, which works but makes every recogniser parameter a variable. Don't "simplify" either back.
- **`predBar.micReserve` must stay 0 whenever the mic is not on screen.** `predRow.x` centres the pills in `width - micReserve - clearCtxReserve`, so a zero reserve collapses that expression back to exactly the single-reserve one it grew from, and a user who never enables dictation gets byte-identical bar geometry. `computeFit` is passed `clearCtxReserve + micReserve` as its existing single `reserve` argument rather than gaining a tenth parameter: the fitter has no notion of which *side* a reserve sits on, only how much of the bar the pills may not have.
- **There is a title-bar mirror** (`dictationTitleBarButton`), visible only when `suggestionsEnabled` is false, for the reason `snippetsTitleBarButton` exists: the bar collapses to zero height with that setting, and an unrelated setting must not remove the only way into a feature. Both mic icons are Feather's `mic` through `StrokeIcon`, never a glyph.
- **The mic's busy pulse drives a `pulse` property, never `opacity` directly.** `SequentialAnimation on <property>` takes ownership of what it animates and never hands it back, so animating `opacity` destroyed the `ready ? 1.0 : 0.45` binding on the first connect and left the button parked wherever the loop was, which for half of each cycle is nearly transparent. A property with no binding of its own is what an animation can own safely; `opacity` multiplies it in and `onRunningChanged` resets it. Applies to any future animation on a bound property.
- **Insertion opens with `_begin_verbatim_insert()`, the same verbatim-insert prologue every other insert path calls**: `_release_sticky_modifiers()` **before** the send, a deferred auto-space settled with `prose=True`, `_consume_auto_cap()` (a dictated phrase is the next thing typed, so it takes the capital), then the send itself inside `_without_held_modifiers()` via `_send_literal_text` (so a right-click lock survives), and **the insert itself never gated on privacy mode**. It does not go the extra step through `_commit_verbatim_insert` (as `insertSnippet` / `insertGlyph` do), because its tail differs: it mirrors the insert into `_context_buffer` / `_sentence_buffer` and decides a leading space, both dictation-specific. One space is prepended between phrases, decided from what is on screen (`_context_buffer + _current_word`) rather than from a "have I inserted yet" flag, because Deepgram returns phrases with no surrounding whitespace.
- `_context_buffer` / `_sentence_buffer` are appended to so dictation's own contribution is reflected: they are what the insert path *measures against*, so a buffer disagreeing with the screen is how a later pill tap eats real text. **The deferred auto-space is not re-added there** (`_take_deferred_space` already mirrored it, and appending it again put two spaces in the buffer where the screen has one), and the append is **suppressed in privacy mode**, exactly as `pressPrediction`'s own append is. The prediction model is deliberately **not** taught from dictated words (they came from Deepgram's vocabulary, not the user's typing).
- **Privacy mode calls `cancel()`, not `stop()`**, and the difference is the point: `cancel` does not wait for the provider to flush, so a run under way when the caret landed on a password field cannot deliver one more sentence. **`cancel()` disconnects the controller's own handlers before aborting the stream, and the disconnect is the load-bearing half**: clearing `self._stream` only stops us *sending*, not the provider *telling us* things, and a websocket can have a `textMessageReceived` already queued at teardown. Without it a cancelled run typed one more phrase into whatever field the user had moved to. Found by `tests/test_dictation.py::TestTheRunLifecycle::test_cancelling_drops_what_is_in_flight`.
- **`dictation.json` is deliberately absent from the Data Backup archive** (do NOT add it to `_MODEL_FILES`), because it holds an API key and the archive exists to be carried between machines. The key is DPAPI-wrapped on Windows via ctypes, plaintext at 0600 elsewhere, never logged, never returned to QML in the clear (`getDictationSettings` gives `hasKey` plus a masked preview), and never in the websocket URL (it rides in an `Authorization` header, so no proxy log captures it). `TestTheKeyNeverLeavesTheMachine` asserts the export exclusion.
- **Toggle, not push-to-talk**, same argument as the swipe-typing removal: a sustained precise hold is the one gesture this user cannot reliably make. Two automatic stops sit behind it (a silence timeout the user notices, and a wall-clock ceiling that is the backstop for the silence detector itself failing in a noisy room), because a missed "off" click leaves the mic live and, on a metered API, billing.
- **Five states, not a boolean** (`idle`/`connecting`/`listening`/`finishing`/`polishing`): `connecting`, `finishing` and `polishing` all have to look busy **without looking like recording**, or the user clicks again and starts a second run. `finishing` is not cosmetic: `stop()` closes the mic immediately but keeps the socket open for the `CloseStream` flush, because the final fragment for the last thing said arrives *after* that message.
- **No automatic reconnect, on purpose.** Audio spoken during a gap is gone, so a silent reconnect yields a transcript with an invisible hole: words the user said, believes they said, and cannot see. A drop ends the run and says so. Errors are mapped to sentences naming a next step (`_friendly_error`); nobody can act on `QAbstractSocket::RemoteHostClosedError`.
- **Nothing in `src/dictation/` logs transcript content**, same rule as the rest of the keystroke path.
- Deepgram parameters were taken from MacroVox's production set: `model`/`punctuate`/`smart_format`/`interim_results`, with **`keyterm`** for custom vocabulary (nova-3 rejects the legacy `keywords`).
