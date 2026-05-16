# macOS Plan & Build Guide

End-to-end plan for running, bundling, and shipping Alpha-OSK on macOS.
Counterparts: [LINUX.md](LINUX.md), [WINDOWS.md](WINDOWS.md).

> **Status:** Phase 1 (run from source, type into other apps) is wired up.
> Phases 2–4 (build pipeline polish, signing & notarization, auto-update,
> auto password detection) are scoped below as a punch list.

---

## Running from source

```bash
# Clone, cd, then:
python3 run.py
```

`run.py` creates `venv/`, installs `PySide6` + `pyobjc-framework-Quartz`
+ `pyobjc-framework-Cocoa` (gated on `sys_platform == "darwin"` in
`requirements.txt`), and launches `python -m src.keyboard_app`.

**First-run prompt.** The first time the keyboard tries to post a key
event, macOS shows:

> *"Terminal" (or "Python", or "Alpha-OSK") would like to control this
> computer using accessibility features.*

Open **System Settings → Privacy & Security → Accessibility** and
enable the listed app. When running from source via `python run.py`,
the entry will be your terminal app (Terminal, iTerm, Cursor, etc.) —
not "Alpha-OSK". After a signed `.app` build (phase 3), the entry
becomes "Alpha-OSK" directly.

Until the grant is in place, `CGEventPost` silently no-ops: the OSK UI
works, but keystrokes don't reach other apps. Logs surface a
`Quartz CGEvent allocation/post failed` warning on the first attempt.

---

## Platform-parity features

What's already running on macOS vs the other backends:

| Feature | macOS | Mechanism |
|---------|-------|-----------|
| Key synthesis (chars, chords, specials) | ✅ | `Quartz.CGEventCreateKeyboardEvent` + `CGEventKeyboardSetUnicodeString` |
| **Pid-targeted delivery** (the OSK works at all) | ✅ | `CGEventPostToPid(target_pid, ev)`, target tracked via `NSWorkspaceDidActivateApplicationNotification` observer in the synthesizer — focus-independent. See *Design decisions* for why naive `CGEventPost` failed. |
| Sticky-modifier hold / release | ✅ | Post a keycode-only modifier event with no matching key-up; mirror state in `_held_mods` so per-event `CGEventSetFlags` stays consistent |
| Defensive modifier release on startup | ✅ | `MacOSKeySynthesizer.reset_modifier_state()` posts key-up for ⇧⌃⌥⌘ — safe no-op when the user isn't physically holding any |
| Atomic prediction replacement (`replace_text`) | ✅ | `Shift+Left × N` chord sequence then `send_text` — same model as Linux/Windows so empty-field bugs (Slack composer) don't re-emerge |
| App-switch context reset | ✅ | 250 ms poll on `NSWorkspace.frontmostApplication().processIdentifier()` — the pid stands in for HWND/X11 window id |
| App stays out of the user's way (no Dock / no Cmd+Tab) | ✅ | `NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)` in `keyboard_app.py`. NOTE: Accessory does *not* block click-activation — pid-routed delivery handles that; Accessory just removes Dock/switcher presence. |
| Floating across Spaces and over fullscreen apps | ✅ | NSWindow `setLevel:NSFloatingWindowLevel` + `collectionBehavior: CanJoinAllSpaces \| Transient \| FullScreenAuxiliary` + `hidesOnDeactivate: NO` |
| `"win"` modifier maps to ⌘ Command | ✅ | `_MOD_INFO["win"]` → `kVK_Command` + `kCGEventFlagMaskCommand`. Mirrors how `"win"` → Super on Linux: each platform's primary shortcut modifier |
| Compatibility Mode auto-detection | ⛔ | Stays off on macOS. The Windows-only IDE / RDP whitelist lives behind `sys.platform != "win32"` in `_window_needs_compat_mode` |
| Password-field auto-detection | ⛔ | Falls back to `_NullDetector`. Manual title-bar toggle works. AXUIElement walk is the planned implementation — see *Phase 4* below |
| Auto-update | ⛔ | Windows-only path, unchanged. Mac users update via re-running the installer / `brew upgrade alpha-osk` once a Homebrew tap exists |
| Code signing / notarization | ⛔ | `.app` is unsigned today — open via right-click → Open. See *Phase 3* |

---

## Design decisions

### Why pyobjc (not ctypes against ApplicationServices)
Quartz's C API works through ctypes in principle, but the boilerplate
for `CGEventRef` lifetime, `CFString` bridging, and NSWindow / NSView
traversal is large and brittle. pyobjc wraps the same calls with a
real ObjC runtime bridge — `objc.objc_object(c_void_p=int(view))`
gives us a real `NSView`, `.window()` gives us a real `NSWindow`, and
the rest reads like Cocoa. The dependency is ~25 MB across Quartz +
Cocoa subpackages and they're conditionally installed via PEP 508
markers, so Linux/Windows wheels are unaffected.

### Why character path uses `CGEventKeyboardSetUnicodeString`
Plain text via Unicode injection is the macOS equivalent of Windows
`KEYEVENTF_UNICODE` / Linux `xdotool type`. It's the right path for
chat composers, browsers, and editors. The same caveat applies as on
Windows: a small set of apps that read raw scancodes (DirectInput
games, some VirtualBox setups) won't see the events. None of those
are accessibility-critical and the alternative — kVK lookups for
every printable character under the user's active keyboard layout —
is layout-specific work we'd have to redo per locale.

Chord keys and special keys go through the keycode path (`kVK_*` +
`CGEventSetFlags`) because the target app reads the keycode for
shortcut dispatch, not the character.

### Why "win" maps to ⌘ Command
The OSK has Ctrl / Alt / Shift / Win modifier buttons inherited from
its Windows / Linux origin. On macOS:

- ⌘ Command is the primary shortcut modifier (Cmd+C, Cmd+Q, Cmd+Tab).
- ⌃ Control is rare in app shortcuts (some emacs-style cursor moves).
- ⌥ Option is the accented-character / "alternate" modifier.
- ⇧ Shift is the same everywhere.

Mapping the "Win" button to ⌘ gives the user the muscle-memory
equivalent of "the big modifier" on each platform — same pattern as
mapping "win" → Super on Linux. The native name `"cmd"` is also
accepted in `_MOD_INFO` so future Mac-aware callsites can be
explicit. Ctrl, Alt, Shift route to their literal macOS equivalents
unchanged.

### Why NSWindow tuning is in addition to Qt flags, not instead
`Qt.WindowDoesNotAcceptFocus` maps to `-canBecomeKeyWindow: NO` on
macOS — the window can't become the keyboard-input target. But Qt
doesn't surface window level / collection behavior / `hidesOnDeactivate`
as flags, and we need all three: float over normal windows
(FloatingWindowLevel), follow the user across Spaces and fullscreen
apps (`CanJoinAllSpaces | FullScreenAuxiliary`), and stay visible
when focus moves elsewhere (`hidesOnDeactivate: NO`).

These three settings are applied in `_apply_macos_window_flags()` via
pyobjc, runs once at startup after the QML root is shown. The
function silently no-ops if pyobjc is missing — the OSK still works,
it just won't follow Spaces / fullscreen.

### Focus theft and why pid-targeted delivery was the only fix
**This was the longest debugging session of the macOS port.** Going
through the rabbit hole because the right answer is non-obvious and
the wrong answers look plausible.

The bug: keystrokes from the OSK weren't reaching TextEdit, even
though `AXIsProcessTrusted()` returned True and `CGEventPost` did not
error. A standalone diagnostic (`scripts/mac_keysend_diag.py`)
confirmed bare-Quartz delivery worked — `CGEventPost` posted to
whichever app was frontmost at that moment. So the OSK-specific
failure mode was: clicking on the OSK *makes the OSK frontmost*, and
then `CGEventPost` delivers to the OSK itself.

Things that didn't fix it (so we don't waste time again):

1. **`Qt.WindowDoesNotAcceptFocus`.** Maps to `-canBecomeKeyWindow: NO`
   on macOS. Blocks the window from receiving keyboard input
   directly. Does NOT block click-activation of the owning app.
2. **`NSApplicationActivationPolicyAccessory`.** Removes the Dock icon
   and Cmd+Tab entry — feels like it should be the answer, but the
   Apple docs explicitly say accessory apps are "activated by clicking
   on one of its windows." It does not block click-activation either.
3. **`Qt.Tool` flag.** In Qt 5 this mapped to `NSPanel`, which honors
   `NSWindowStyleMaskNonactivatingPanel`. **Qt 6 dropped that
   mapping** for `QQuickWindow` — the QML root still comes up as
   `QNSWindow` (Qt's NSWindow subclass), and the NonactivatingPanel
   style bit is a no-op on non-panels.
4. **`[NSApp deactivate]` before each post.** Deactivation is
   asynchronous; the activation switch hasn't propagated by the time
   `CGEventPost` runs synchronously on the next line, so frontmost is
   still us.

What actually worked: **post to a specific pid, not to the frontmost
app.** `CGEventPostToPid(target_pid, event)` ignores foreground state.
The target pid is the app the user was using before they clicked the
OSK. We track this via an `NSWorkspaceDidActivateApplicationNotification`
observer in `MacOSKeySynthesizer.__init__`: every time a non-self app
activates, we record its pid. By the time the user clicks an OSK key,
`_target_pid` points at the right app, and the keystroke lands there
regardless of who's frontmost at that instant.

Implementation details:
- The observer is set up on `NSWorkspace.sharedWorkspace().notificationCenter()`
  via `addObserverForName_object_queue_usingBlock_`. We hold a strong
  reference to the block in `self._activation_observer` — without that
  the notification center stores it weakly and the observer goes silent
  after one fire.
- The cold-start edge case (user launches OSK and clicks a key without
  ever activating another app first) falls back to plain `CGEventPost`.
  In practice it doesn't matter because users tab into a target editor
  before clicking the OSK; if they don't, the first few keystrokes
  go to Alpha-OSK and they'll figure it out.
- `_post_event` is the single funnel for all CGEvent posts in this
  module — both `send_text` and `_post_keycode` route through it.
  Future modifications should preserve that funnel.

A future Qt version that restores the `Qt.Tool → NSPanel` mapping
would let us drop this whole dance and use NonactivatingPanel
directly. `_apply_macos_window_flags` already opportunistically sets
the style bit if the window happens to be a panel — that branch is
dead code today (`is_panel` is always False on Qt 6.10.x) but ready
for that scenario.

### Config dir = `~/Library/Application Support/alpha-osk`
Matches Apple's HIG for per-user app state. The directory is
`chmod 0700` because the model files contain typed-word history.
Settings (managed by `QSettings`) go through `~/Library/Preferences/`
automatically — no code change needed since Qt picks the right
backend per platform.

---

## Phase plan

### Phase 1 — Run from source ✅
- `src/platform/__init__.py` — `darwin` → `macos` branch, factory routing, config-dir resolution
- `src/platform/macos.py` — `MacOSKeySynthesizer` via Quartz
- `src/platform/password_detect.py` — macOS stub returning `_NullDetector`
- `src/keyboard_app.py` — `_apply_macos_window_flags()` for NSWindow tuning
- `src/keyboard_bridge.py` — `NSWorkspace`-based foreground tracking
- `run.py` — `IS_MACOS` branch, first-run accessibility hint
- `requirements.txt` — pyobjc deps gated by `sys_platform == "darwin"`
- `tests/test_platform.py` — assertions extended for macos branch

### Phase 2 — Build a `.app` 🟡 scaffolded, not yet exercised
- `build/macos/alpha-osk.spec` — PyInstaller spec with `BUNDLE()` producing `Alpha-OSK.app`, Info.plist (bundle id `com.okstudio1.alpha-osk`, `LSMinimumSystemVersion: 11.0`, `LSUIElement: False`, `NSHighResolutionCapable: True`)
- `build/macos/build.py` — driver mirroring `build/linux/build.py` shape; emits lockfile + CycloneDX SBOM and optional `.dmg` via `hdiutil`
- `build/macos/alpha-osk.icns` — multi-resolution app icon (~546 KB, 10 size variants from 16×16 to 1024×1024). Regenerate from `assets/logo-2048.png` when the logo changes — recipe below.
- **TODO:** actually run `python build/macos/build.py --dmg` and verify the bundle launches from `/Applications`. The spec and driver compile cleanly but a frozen `.app` hasn't been smoke-tested.

#### Regenerating `alpha-osk.icns`

`sips` and `iconutil` are both built-in on macOS — no Homebrew /
ImageMagick / Inkscape needed. Run from the repo root:

```bash
SOURCE=assets/logo-2048.png
ICONSET=build/macos/alpha-osk.iconset
mkdir -p "$ICONSET"

gen() { sips -s format png -z "$1" "$1" "$SOURCE" --out "$ICONSET/$2" >/dev/null; }
gen 16   icon_16x16.png
gen 32   icon_16x16@2x.png
gen 32   icon_32x32.png
gen 64   icon_32x32@2x.png
gen 128  icon_128x128.png
gen 256  icon_128x128@2x.png
gen 256  icon_256x256.png
gen 512  icon_256x256@2x.png
gen 512  icon_512x512.png
gen 1024 icon_512x512@2x.png

iconutil -c icns "$ICONSET" -o build/macos/alpha-osk.icns
rm -rf "$ICONSET"
```

The iconset directory is intentionally transient: the `.icns` is the
committed artefact, the iconset is rebuildable in seconds. Sourcing
from `logo-2048.png` (not `logo-1024.png`) gives sips room to
downsample cleanly at every size — sharper small icons.

### Phase 3 — Code signing & notarization
- Apply for an **Apple Developer Program** membership (~$99/yr).
- Generate a Developer ID Application certificate via Xcode → Settings → Accounts → Manage Certificates.
- Add `codesign_identity="Developer ID Application: …"` to `BUNDLE()` in `build/macos/alpha-osk.spec` (or codesign the bundle post-hoc with `codesign --deep --sign … --options runtime --entitlements …`).
- Notarize: `xcrun notarytool submit … --apple-id … --team-id … --wait`, then `xcrun stapler staple Alpha-OSK.app` and `xcrun stapler staple Alpha-OSK-X.Y.Z.dmg`.
- **Hardened Runtime entitlements file** (`build/macos/entitlements.plist`):
  - `com.apple.security.cs.allow-jit` — PySide6/QML uses Qt's V4 JIT.
  - `com.apple.security.device.audio-input` — only if voice features ever ship.
  - `com.apple.security.automation.apple-events` — false unless we script other apps directly.
  - Accessibility itself is a TCC user grant, not an entitlement.
- Update `docs/MACOS.md` § *Release checklist* with the codesign + notarize commands once they're verified end-to-end.

### Phase 4 — AXUIElement password detection
Currently `_create_detector()` returns `_NullDetector` on macOS — the
manual title-bar toggle works, but auto-detect doesn't. The proper
implementation:

1. Acquire system-wide accessible: `AXUIElementCreateSystemWide()`.
2. Read focused element via `AXUIElementCopyAttributeValue(sw, kAXFocusedUIElementAttribute)`.
3. Read its role/subrole: `kAXRoleAttribute` (`AXTextField`) and `kAXSubroleAttribute` (`AXSecureTextField`).
4. Cache result; refresh on `kAXFocusedUIElementChangedNotification` via `AXObserverCreate` running on the main run-loop.
5. Same dual-trigger pattern as Windows/Linux: 200 ms poll + per-keystroke sync check rate-limited to 50 ms.

Requires the same Accessibility TCC grant the key synthesizer needs.
Web browsers expose secure inputs correctly through AXUIElement when
the user has Accessibility enabled for the browser — confirmed for
Safari, Chrome, Firefox in casual testing.

### Phase 5 — Auto-update
Skip on Mac for now. The Windows `updater.py` flow is EV-cert
specific (`Get-AuthenticodeSignature`, NSIS silent install,
SafeNet-eToken-bound signing). Mac update paths to consider:

- **Sparkle framework** — the de-facto Mac auto-update library. Requires bundling `Sparkle.framework`, embedding an Ed25519 public key, hosting an `appcast.xml`. Mature, well-supported.
- **Homebrew tap** — `brew tap okstudio1/alpha-osk && brew install --cask alpha-osk`. Users `brew upgrade` on their own cadence. Zero in-app code.
- **Manual** — link to GitHub releases (the same `okstudio1/alpha-osk-releases` repo the Windows updater targets) from the title bar.

Recommendation: **Homebrew tap** first (lowest implementation cost,
fits the platform's expectations) → **Sparkle** if telemetry shows
users want in-app prompts. The version-check ping itself is
identical to the existing GitHub API call in `updater.py`, only the
install step changes.

---

## Quick reference

| Path | Role |
|------|------|
| `src/platform/macos.py` | Quartz key synthesizer |
| `src/keyboard_app.py::_apply_macos_window_flags` | NSWindow level / behavior / hides-on-deactivate |
| `src/keyboard_bridge.py::_get_foreground_window_id` (macOS branch) | `NSWorkspace.frontmostApplication().processIdentifier()` |
| `build/macos/alpha-osk.spec` | PyInstaller spec + `BUNDLE()` |
| `build/macos/build.py` | Build driver + `.dmg` packaging |
| `~/Library/Application Support/alpha-osk/` | Models, analytics, telemetry, packs |
| `~/Library/Preferences/com.alpha-osk.Alpha-OSK.plist` | Qt-managed settings |

## Troubleshooting

**The OSK runs but nothing types.** Two distinct failure modes look
identical from the user side:

1. **Accessibility grant missing.** Open System Settings → Privacy &
   Security → Accessibility and enable your terminal (running from
   source) or Alpha-OSK (from a built `.app`). A
   `Quartz CGEvent allocation/post failed` line in the log confirms
   this is the issue. Run `python scripts/mac_keysend_diag.py` from
   the repo root — it prints `AXIsProcessTrusted` directly.
2. **`_target_pid` never set.** Look for the
   `Installed NSWorkspace activation observer` line at startup
   (should be there), then check that a `Target app updated → X (pid=N)`
   line appears whenever you click into a different app. If the
   target-update line doesn't fire, the activation observer
   regressed — pyobjc-framework-Cocoa is the load-bearing dep here.
   Without a target pid, posts fall back to `CGEventPost` which
   delivers to whichever app is frontmost when you click the OSK
   — which is the OSK itself, so keystrokes look like they're being
   dropped.

**Keyboard vanishes when I click into another app.** `hidesOnDeactivate`
not applied — `_apply_macos_window_flags()` failed. Check the log for
"Failed to apply macOS NSWindow flags" or "pyobjc not available" and
reinstall `pyobjc-framework-Cocoa`.

**Keyboard doesn't follow me across Spaces.** Collection behavior not
applied — same root cause as above. Without pyobjc the window stays
pinned to the Space it was opened in.

**`python build/macos/build.py` fails with "must run on macOS".** The
script refuses to run on Linux/Windows because PyInstaller links
against the local Cocoa frameworks; the `.app` can only be created on
a macOS host. Move to a Mac runner (or a macOS GitHub Actions runner)
for releases.

**App icon is fuzzy or missing.** Verify `build/macos/alpha-osk.icns`
exists. If a recent logo change made the icon look stale, re-run the
`sips`/`iconutil` recipe in *Phase 2 § Regenerating `alpha-osk.icns`*.
The spec falls back to `assets/logo-1024.png` if the `.icns` is
missing, which works but renders slightly fuzzier in the Dock.
