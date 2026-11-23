# Absorbing Octavium: one app for typing, playing games and playing music

**Status:** plan, written 2026-11-22. Nothing here is built. The end state
follows the owner's request of that day ("incorporate Octavium, similar to
Nimbus"); the phases, and every design marked *open*, are proposals.

It was written from a read of Octavium (`okstudio1/Octavium`) at commit
`a748a12` (2026-07-18) and of this repo at `c216e94`. The MIDI platform facts
were checked against the upstream pages on the same day (*Sources*), and the
state of the owner's machine was read, not assumed (*Measured so far*).

It is the sibling of [`NIMBUS_INTEGRATION.md`](NIMBUS_INTEGRATION.md) and
leans on it: the view that swaps with the letters, the drag-free analog
control and the `pad` action type are built once, there, and reused here.

## The end state

**One app.** Alpha-OSK grows a native MIDI instrument and, in time, does what
Octavium does: a keyboard, pads, chords, scale lock, sustain and latch, pitch
bend, faders. The features are rebuilt here to this repo's conventions, not
embedded, and the sibling app is left as it is until the owner decides its
future. This is the path dictation took with MacroVox and the controller is
taking with Nimbus.

Three reasons it is the right end state, from Octavium's own code:

- **Octavium takes the focus it needs the DAW to keep.** Nothing in it
  prevents activation (no `WS_EX_NOACTIVATE`, no `WA_ShowWithoutActivating`),
  so a click on a key makes Octavium the foreground window. Its own shortcuts
  (Z / X for octave, Q for scale, 1 / 2 / 3 for the velocity curve) work only
  while it is focused, and its player has no physical keyboard to press them
  with. This window never takes focus, and the letters are one key away, so
  the DAW's own shortcuts (space to record, Ctrl+Z to drop a take) come from
  the same window as the notes. *Read from the code, not measured.*
- **Most of what Octavium still lacks is shipped here.** Its `TODO.md` lists
  saved layouts as pending; it has no settings store (port, size, sustain and
  latch are forgotten on exit), no themes, no always-on-top, no updater and
  nothing for a switch scanner. None of that is ported; only the instrument
  is.
- **Its continuous controls are the controller's.** A pitch wheel is a
  trigger, a fader is a slider and an XY pad is a stick. Nimbus phase 2
  designs those drag-free, with the pointer confined while one is held. Built
  twice they would be two idioms for one hand.

## What Octavium is, measured

19,124 lines of Python, PySide6 **Qt Widgets** (no QML), MIT. Runtime
dependencies: `mido` 1.3.2, `python-rtmidi`, `pygame`, `pydantic`, `Pillow`.

| Part | Size | What it is |
|------|------|------------|
| MIDI output | `app/midi_io.py`, 329 lines | `mido` over `python-rtmidi`, with `pygame.midi` as a fallback. Output only: note on, note off, CC, pitch wheel. One shared port owned by the launcher. **It never creates a port**; the user installs loopMIDI first. |
| Layout model | `app/models.py`, 57 | `KeyDef` (label, note, width, height, velocity, channel, colour), `RowDef`, `Layout` (columns, gap, base octave, polyphony, scale). Pydantic models, saved nowhere. |
| Scale quantise | `app/scale.py`, 42 | Chromatic, major, minor, pentatonic, custom. A pure function. |
| Piano | `app/keyboard_widget.py` 2226, `app/piano_layout.py` | 25, 49, 61, 73, 76 or 88 keys. Octave shift, channel 1 to 16, velocity slider with soft / linear / hard curves, random velocity range, polyphony cap, sustain, latch, optional pitch-bend and mod-wheel sliders. |
| Pad grid, faders, XY | `pad_grid.py` 836, `faders.py`, `xy_fader.py` | 4 x 4 pads (momentary, sustain or latch), eight CC faders, one XY fader. |
| Chord Pad | `chord_monitor_window.py` 1903, `chord_selector.py` 1559, `chord_autofill.py` 1654 | 4 x 4 chord cards over several pages, hold to play, humanise and drift, lock and regenerate, drag and drop. The largest area by far. |
| Chord generation | `chord_suggestions.py`, `chord_progression.py`, `chord_progression_data.py` | Suggestions and a Markov progression generator over an embedded corpus of 88 progressions. An optional 30 MB chord library (MIT, third party) loads from disk. |
| Harmonic table | `harmonic_table.py`, 1291 | An isomorphic hex layout. Its own `TODO.md` says "finish". |
| Modulune | `modulune/`, about 3300 | A generative piano engine with its own window and a thread that times notes with `time.sleep`. |
| Launcher | `app/launcher.py` 514, `standalone_windows.py` | Each surface is its own window over the shared port. |
| Persistence | one file | `%APPDATA%\Octavium\chord_pad.json`. Nothing else is saved. |
| Tests | 7 files, pure logic | No GUI test. Pyright covers the pure-logic modules only. |
| Release | Inno Setup, EV-signed | Installer 1.1.2, latest tag `v1.1.1`. The PyInstaller spec is git-ignored, so the build is not reproducible from the repo. |

How a note is played there today: press a key and it sounds until release,
or drag across keys for a glissando (the widget grabs the mouse), or switch
on sustain or latch. Right-click latches a single note, on by default. A
chord card sounds only while it is held.

## What carries over, and how

| Octavium | Here | How |
|----------|------|-----|
| `midi_io.py` | **Rebuilt** | A standard-library `ctypes` client for WinMM (`midiOutOpen`, `midiOutShortMsg`) as `src/platform/midi_out.py`, the way `padbus_client.py` is a `ctypes` client. `mido`, `python-rtmidi` and `pygame` are not taken: dictation set the precedent of zero new dependencies, and PySide6 has no MIDI module to build on (checked: 6.11.1, 64 modules, none). |
| Scale quantise, velocity curves | **Ported** | About 60 lines of pure functions into `src/midi/`, with Octavium's scale tests; the curves get tests they do not have there. |
| `KeyDef` / `Layout` | **Already here** | Layout JSON on the unit grid. `MODULAR_LAYOUTS.md` already reserves a `midi` action type; it becomes native instead of "via Octavium bridge". |
| Piano keys, pads | **Rebuilt** | On `KeyButton`. See *The keys*. |
| Sustain, latch | **Rebuilt** | Tap, and right-click to latch with the `lockBar` cue: the modifier idiom, which Octavium arrived at independently. |
| Drag glissando | **Not ported** | A sustained precise drag. A drag-free glide (pick up, sweep, put down) is possible later and is not promised. |
| Pitch bend, mod wheel, faders, XY | **Reused** | The drag-free analog control from Nimbus phase 2. Nothing analog is built here first. |
| Chord cards | **Redesigned** | Hold-to-play becomes tap for a timed chord and right-click to latch. Pages and locking carry over. |
| Suggestions, autofill, Markov generator | **Ported, later** | Pure logic. The provenance and licence of the 88-progression corpus are checked before it enters this repo. |
| Optional 30 MB chord library | **Not ported** | Not shipped. An importer could come later and would be an import path under the hardening rules. |
| Keyboard shortcuts (Z, X, Q, 1 to 3) | **Become keys** | They need focus this window never has. |
| Multi-window launcher | **Not ported** | One window, one view at a time. |
| `chord_pad.json` | **Converted** | An importer, written to the import-hardening rules from the first line. |
| Harmonic table | **Open** | Unfinished there. An isomorphic grid suits the unit grid better than a piano does, so it may arrive as the grid layout of *The keys* and not as a port. |
| Modulune | **Not ported** | A generative engine is a different product from an instrument. The owner's call at the parity review. |
| MIDI input | **Not planned** | Octavium has none either. |
| Inno Setup installer, Gumroad listing | **Not ported** | One NSIS installer, one release repo. |
| Always on Top, themes, settings, updater, scanning | **Already here** | Octavium has none of them. |

Nothing is embedded. Octavium is Qt Widgets and this window is QML with
`WS_EX_NOACTIVATE`; hosting its widgets would bring a second UI toolkit, three
binary dependencies and windows that take focus.

## The three hard problems

### 1. The port (the driver problem, in a different shape)

A DAW records from a MIDI *input*. For our notes to arrive there, something
must loop an output back as an input, and **on Windows an application using
the classic API cannot create that port itself**. Microsoft's own overview:
"The existing WinMM and WinRT MIDI 1.0 APIs cannot support the newer
constructs, message scheduling, creating virtual device apps, etc." Octavium
answers with "Create a loopMIDI port first". The options, as they stand on
2026-11-22:

| Route | What it needs | State |
|-------|---------------|-------|
| **Windows' built-in synth** | Nothing. "Microsoft GS Wavetable Synth" is a WinMM output on every Windows. | Makes sound, records nowhere. It is what lets the view do something the first time it is opened. Its latency is to be measured. |
| **Windows MIDI Services loopback** | Windows 11 25H2 or later, and a loopback endpoint the *user* creates in Microsoft's MIDI Settings tool. | Generally available since 2026-02-17, rolling out in phases. The service is in the box; the tool that creates a loopback is a separate download still published as a pre-release ("In-box Preview 14"). WinMM is "replumbed" onto the service, so a `ctypes` WinMM client should see such a port. *To be measured.* |
| **loopMIDI** | A third-party installer. | The route Octavium documents. Its page lists Windows 7 to 10 and a 2022 copyright. The author's SDK page says software linking his SDK "MAY NOT BE DISTRIBUTED in any way without prior clearance", and that the driver ships only to licensees. So it can be *named*, never bundled, and its terms for the free download are re-read before the UI recommends it. |
| **Our own virtual device** | The Windows MIDI Services App SDK: C++/WinRT or C# only, an SDK runtime installed on the user's machine, an API still numbered 0.99 preview. | Not reachable from Python. A small helper executable is the long hedge, as the pad-bus fork is for Nimbus. Not before the API leaves preview. |
| **Linux** | ALSA's sequencer creates virtual ports natively. | No driver problem there. A second backend behind the same interface. |

The rule proposed, the same as the controller's:

- **Until parity: detect, never install.** The view lists the outputs that
  exist, picks a loopback when one is there, falls back to the built-in
  synth, and says in one sentence where a loopback comes from.
- **Nothing third-party goes in the installer** without its own alignment.
  That is a build-pipeline change.
- **Windows 10** gets the synth and whatever loopback the user has. Windows
  MIDI Services does not exist there.

### 2. Stuck notes and timing

A note that never gets its note-off is this feature's held modifier: it
drones until something silences it, and the player may not have a quick way
to do that. Octavium times its note-offs with `QTimer.singleShot`; its own
`KNOWN_ISSUES.md` warns that such a callback can be garbage-collected before
it fires, and its output layer swallows every exception by design.

- **One release site.** The module tracks every note it has sounded and
  every latch, and one function ends them. Shutdown, the view's Off key, a
  lost port, sleep and a panic key all call it. It sends a note-off per
  tracked note first and the controller messages (sustain off, all notes
  off) second, since not every synth honours the second kind.
- **Note latches are not modifiers.** `_release_sticky_modifiers` does not
  touch them: typing a letter, or pressing space to start the DAW recording,
  must not drop a held chord.
- **Anything timed runs on one scheduler off the Qt thread** (a strummed
  chord, humanise, the length of a tapped chord), never on chains of
  `QTimer.singleShot`. Opening a port happens on a worker.
- **A lost port is reported, never swallowed.** The view says the output is
  gone and stops showing keys as sounding.

### 3. The keys (open, needs a mockup)

A piano is not a unit grid. Black keys overlap white ones, which collides
with rules this repo holds on purpose: every key has its own slot, hit
margins give half a gap to each neighbour, and an overlap goes to the
later-declared key. And 88 keys do not fit: at today's 962 px window a white
key would be 18 px wide. Two shapes to draw side by side:

| | Layout | Fits the unit grid | Notes on screen | Reads as an instrument |
|---|--------|--------------------|-----------------|------------------------|
| A | **Piano**, about two octaves with octave-shift keys | No: a new geometry with its own hit-testing | 24 to 29 | Yes, to anyone |
| B | **Note grid**: rows a fourth apart, as on pad controllers, with scale lock dimming or removing the out-of-scale notes | Yes: ordinary `KeyButton`s in layout JSON | 48 or more in four rows | Only to people who know grids |
| C | **Both**, switchable, grid first | | | |

Velocity is the other open question. Octavium uses a slider; click position
sets nothing. `KeyButton` already publishes where in the key the press
landed (`pressDy`), so "lower on the key is louder" costs nothing and gives a
pointer user dynamics without a second control. An Invoke from a scanner
carries no position and would use the slider's value.

## Where the instrument lives (proposed: a Music view)

The Controller view was chosen for Nimbus on 2026-11-21: the window swaps the
letter grid for the pad, one key swaps back. The same reasoning holds (an
instrument does not fit beside a 15.5u grid, and nobody plays and types in
the same second), so the proposal is a **Music view** on the same mechanism,
with pads and chords as pages of it. It is a proposal and not a decision:
with three views, how the swap key cycles or chooses is its own small
design, and belongs in the mockup with the key shapes above.

## How it sits in the existing architecture

| Concern | Rule that already exists | What the instrument does |
|---------|--------------------------|--------------------------|
| Bridge size | New surfaces split off (`TelemetryBridge`, and `GamepadBridge` in pull request 261) | Its own `MidiBridge` and `midi` context property, registered in `keyboard_app.py` and `tests/qml_context.py`. `KeyboardBridge` does not reference it. |
| Package | One directory per subsystem | `src/midi/`: scale, velocity, note state, scheduler, bridge. The port clients sit in `src/platform/`. |
| Threads | Nothing on the Qt thread blocks | Ports open on a worker. Timed events go through one scheduler thread. `shutdown()` stops it first. |
| Geometry | Unit grid, `sectionHeight`, no `QtQuick.Layouts`, `hitMarginH` / `hitMarginV` on every key | A note grid is ordinary keys. A piano, if chosen, is a new panel that derives its height from `keyH` and is passed both margins. |
| Window size | A new child of `outerLayout` joins the width model | The view is added to `maxWidthForScreen` and its test. |
| Colours | Role table from `qml/palette.js`, every fill through `washFor` | Notes take roles (natural, accidental, root, out of scale). A piano's black and white are not literals. |
| Scanning | Every visible key is a UIA `Button`, ids append-only | A `music` section feeding `scanRevision`. Latch is what makes an instrument playable from a switch. |
| Sticky state | One release site for modifiers | Its own single release site (*Stuck notes*). |
| Actions | `key_actions.py` is a registry | One new `midi` type (note, chord, CC) and one `ActionExecutor` method, so a function key can fire a chord. |
| Shutdown | `shutdown()` releases anything held | Every sounding note ends, then the port closes. |
| Port lifetime | none yet | Opened when the Music view is first shown, never at launch. Swapping to the letters keeps the port and the latches; Off closes it. The controller's rule. |
| Settings | The 8-step wiring; six cards on the home grid | The controller already asks for a seventh card. An eighth is the signal to group them (one *Instruments* card, say). The chosen port lives in a JSON file, not `Settings {}`: it is a device identity. |
| Imports | Sanitise, cap, allow-list | The `chord_pad.json` importer is an import path. |
| Data Backup | Schema changes need a `SCHEMA_VERSION` bump | Chord pages and music layouts join the archive when they exist; that is a schema change and needs alignment. |
| Tests | Autouse guards keep tests off the live desktop | A guard so no test opens a real port: the owner's hardware is on those ports. Everything runs over a fake output. |
| Telemetry | Ten named fields | No new field. A music metric would be a payload change. |
| Logging, privacy mode | No typed content in the log | Notes are not text. State changes log at DEBUG. |

## Phases

Each phase ends in something usable, and each is its own run of pull
requests.

| | Phase | Ends with | Depends on |
|---|-------|-----------|------------|
| 0 | **Probe.** The `ctypes` WinMM client and a script that answers the measurements below. | Numbers. No UI. | Nothing. |
| 1 | **Music view, notes.** Keys in the chosen shape, octave shift, channel, velocity, tap and latch, sustain, panic, port picker, built-in synth as the fallback. | Notes heard with nothing installed, and recorded in a DAW when a loopback exists. | 0, the mockup, and the Controller view's swap mechanism (pull request 263). |
| 2 | **Scale and pads.** Scale lock, the 4 x 4 pad page, one-tap chords, the scheduler (strum, humanise, timed chords). | Chords and drums from single taps. | 1. |
| 3 | **Continuous controls.** Pitch bend, mod wheel, faders, XY. | Expression without a held button. | Nimbus phase 2 (the drag-free control and its confinement). |
| 4 | **Layouts.** The `midi` action type, notes in layout JSON, function keys that fire notes or chords. | Custom music layouts. | `MODULAR_LAYOUTS.md` phase 1, shared with Nimbus phase 3. |
| 5 | **Chord Pad.** Pages, lock and regenerate, suggestions, the progression generator, `chord_pad.json` import. | An Octavium user can move over. | 2, and the corpus licence check. |
| 6 | **Ports.** Linux ALSA backend; on Windows, our own virtual device through a helper, if the App SDK has left preview. | No loopback to set up by hand. | A build-pipeline decision. |
| 7 | **Parity review.** Harmonic table, Modulune, glide, and what happens to Octavium. | The owner's decision on the sibling app. | 5. |

Phases 0 to 2 are small and carry no design risk beyond the key shape.
Phase 3 inherits Nimbus's risk and adds none. Phase 6 is where the
engineering risk is.

## To measure in phase 0

1. Click to sound: the delay from `midiOutShortMsg` to the built-in synth
   being heard, and to a loopback note arriving in a DAW.
2. Does a Windows MIDI Services loopback appear in `midiOutGetNumDevs`, and
   under what name? (It decides how a loopback is recognised.)
3. Can a port be opened while a DAW holds it? The new service promises
   multi-client; the old drivers did not.
4. How long does `midiOutOpen` take, and what does a send return after the
   port has gone away?
5. Does a port open from a `uiAccess` process?
6. Does an open port survive sleep and resume?

### Measured so far (2026-11-22, nothing opened, nothing sent)

Read from the owner's machine with a `ctypes` enumeration
(`midiOutGetNumDevs` / `midiOutGetDevCapsW`) and a service query:

| What | Result | What it means here |
|------|--------|--------------------|
| WinMM outputs | 4: the built-in synth and three hardware ports | A `ctypes` client enumerates with no dependency. The synth fallback exists here. |
| Loopback ports | none | Today a note from this machine can reach a DAW only through hardware. Phase 1 has to explain the loopback step. |
| Windows MIDI Service (`midisrv`) | present and running, Windows 11 26H2 | The service is in the box on this machine. Its settings tool is not. |
| loopMIDI, LoopBe | not installed | Octavium's documented route is not set up here either. |
| PySide6 MIDI module | none (6.11.1) | There is no Qt route, so the client is `ctypes`. |

That closes none of the six questions, which all need a port opened. It was
left unopened on purpose: the three hardware ports are the owner's own
instruments.

## What this plan does not promise

- **A port of our own on Windows.** Until phase 6, and only if Microsoft's
  SDK allows it, a loopback is something the user sets up.
- **Sound of our own.** The built-in synth is Windows'. This app does not
  synthesise audio.
- **Glissando.** Octavium's drag across the keys is not ported.
- **MIDI input, MIDI 2.0, MPE.** Out of scope.
- **Modulune.** Not ported unless the owner asks at the parity review.
- **A date for retiring Octavium.** Phase 7 is a decision, not a deletion.

## Decisions that are the owner's

Taken from the request, to confirm:

- The end state is one app, as with Nimbus.

Open now:

- The key shape: piano, note grid, or both (*The keys*), from a mockup.
- Whether the instrument is a Music view on the Controller view's mechanism.
- Whether click height sets velocity.
- The order against Nimbus: after its phase 1 merges (proposed), or alongside.

Later, each at its phase:

- Which loopback the UI recommends on Windows, once measured (phase 1).
- Whether the progression corpus may ship (phase 5).
- Whether this installer ever carries a MIDI runtime or a helper (phase 6).
- Modulune, the harmonic table, and what becomes of Octavium's installer and
  listing (phase 7).

## The next step

Two things, independent of each other:

- **The probe (phase 0).** About 150 lines and a script, safe to run only
  against the built-in synth until a loopback exists.
- **The mockup.** `docs/roadmap/music-layout.html`, clickable, at today's key
  size: piano against note grid, with latch, octave shift and the view swap,
  so the open decisions above are made by clicking.

## Corrections owed to other documents

Left for the pull request that starts phase 1, so the record changes once:

- `MODULAR_LAYOUTS.md`, *Octavium Bridge*: launching Octavium panels, a
  shared port and importing its `KeyboardWidget` are superseded by this
  document. Its `midi` action stays and loses "via Octavium bridge".
- `MODULAR_LAYOUTS.md` gives Octavium's path as `C:\Users\Owen\dev\Octavium`;
  the repo is `okstudio1/Octavium`.
- `ECOSYSTEM.md`: the music icon that launches Octavium, the named-pipe
  subscription and the embedded widgets are superseded for Octavium, as they
  already are for Nimbus.
- `EXTRA_BUTTONS.md`, "Switch to Octavium": becomes the view swap.

## Sources

- Octavium at `a748a12`: `README.md`, `CLAUDE.md`, `requirements.txt`,
  `THIRD_PARTY_LICENSES.md`, `app/midi_io.py`, `app/models.py`,
  `app/scale.py`, `app/keyboard_widget.py`, `app/launcher.py`,
  `app/chord_monitor_window.py`, `app/pad_grid.py`, `app/harmonic_table.py`,
  `modulune/engine.py`, `installer/octavium.iss`, `docs/TODO.md`,
  `docs/KNOWN_ISSUES.md`, `docs/system_overview.md`.
- Windows MIDI Services: general availability announced 2026-02-17,
  <https://blogs.windows.com/windowsexperience/2026/02/17/making-music-with-midi-just-got-a-real-boost-in-windows-11/>;
  overview (what WinMM cannot do, and its re-plumbing onto the service),
  <https://microsoft.github.io/MIDI/overview/>; SDK languages,
  <https://microsoft.github.io/MIDI/sdk-overview/>; releases (tools and
  transports as "In-box Preview 14", Windows 11 25H2 and later only),
  <https://github.com/microsoft/MIDI/releases>. The repository is MIT.
- loopMIDI: <https://www.tobias-erichsen.de/software/loopmidi.html>
  (version 1.0.16.27, "Windows 7 up to Windows 10"); virtualMIDI SDK terms,
  <https://www.tobias-erichsen.de/software/virtualmidi/virtualmidi-sdk.html>.
- python-rtmidi (MIT) and RtMidi (modified MIT):
  <https://github.com/SpotlightKid/python-rtmidi>. Not used by this plan;
  recorded because Octavium depends on it.
