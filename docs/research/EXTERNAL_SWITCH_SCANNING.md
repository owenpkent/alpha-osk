# Secure external switch scanning for Alpha-OSK

## Executive summary

Switch scanning is a selection method, not a device protocol. A physical switch, keyboard key,
game-controller input, touchscreen contact, or camera gesture produces a small set of signals. A
scanner turns those signals into navigation through a changing set of targets, highlights the
current target or group, and activates the user's choice.

Existing products consistently separate six concerns:

1. acquiring and filtering switch input;
2. discovering targets;
3. ordering targets into a scan pattern;
4. presenting visual and optional auditory feedback;
5. interpreting selection and recovery commands; and
6. invoking the chosen action.

The products differ mainly in who owns the targets. An AAC app or on-screen keyboard can scan its
own cells directly. A system-wide scanner normally discovers semantic controls through the
operating system's accessibility tree, then falls back to point scanning for inaccessible or
coordinate-only content. [Apple Switch Control](https://support.apple.com/en-ca/119835),
[Android Switch Access](https://support.google.com/accessibility/android/answer/6301497),
[ChromeOS Switch Access](https://support.google.com/chromebook/answer/10274137), and
[Switchify](https://switchifyapp.com/how-it-works/) demonstrate the system-wide model.
[Windows On-Screen Keyboard](https://support.microsoft.com/en-gb/windows/use-the-on-screen-keyboard-osk-to-type-ecbb5e08-5b4e-d8c8-f794-81dbf896267a),
[Grid 3](https://hub.thinksmartbox.com/knowledgebase/using-switch-scanning-with-grid-3/),
[PRC-Saltillo software](https://documentation.prc-saltillo.com/docs/switch-scanning-setup), and
[TD Snap](https://us.tobiidynavox.com/pages/td-snap-scanning) demonstrate the app-owned model.

For Alpha-OSK, the scanner-neutral design is therefore:

- expose visible keys and prediction pills as ordinary accessible controls;
- make each control's accessible action identical to one primary click;
- give scanners stable ordering metadata and generation-sensitive identity;
- expose only visible labels and presentation state;
- preserve the keyboard's no-focus and privacy invariants; and
- let each scanner own switch capture, timing, scan strategy, highlighting, and auditory output.

On Windows, Microsoft UI Automation (UIA) is the appropriate first adapter. Qt Quick already has
the semantic properties and press action needed for this provider surface, as documented by the
[Qt Accessible QML type](https://doc.qt.io/qt-6/qml-qtquick-accessible.html). The current Qt
Windows bridge exposes those semantics through UIA, including bounds, identifiers, state, and
Invoke. Because the pinned Qt 6.11 Windows backend does not forward every geometry and structure
event needed by a live overlay, a scanner should begin with bounded cached polling and add a
one-way revision notification only if measurements require it.

This interface is scanner-neutral, but it is not client-exclusive. Accessibility APIs authorize a
class of assistive clients according to each operating system's rules. They do not prove that the
caller is one named partner. A partner-only requirement is a separate security policy and would
need a mutually authenticated local channel.

## How switch scanning normally works

### The interaction loop

A typical scan cycle is:

1. **Acquire input.** A switch interface reports press and release, often as a keyboard, mouse,
   game-controller, Bluetooth, or platform accessibility input. Software-only sources can include
   screen contact and camera gestures.
2. **Condition input.** The scanner applies hold-to-accept, release-to-accept, debounce, repeat,
   and long-press rules before treating the signal as intentional.
3. **Build a target snapshot.** The scanner uses targets it owns, an accessibility tree, or a
   geometry-based fallback.
4. **Choose a scan unit.** The unit may be one item, a row, a column, a block, a semantic group, or
   a moving line.
5. **Present the current choice.** A border, fill, magnification, progress indicator, sound, or
   spoken label tells the user what a switch press would select.
6. **Advance or select.** A timer advances automatically, or one switch advances while another
   selects. Selecting a group descends into that group. Selecting an action invokes it or opens an
   action menu.
7. **Recover.** Back, previous, cancel, stop, scan-count limits, and inactivity reset prevent a
   missed target from trapping the user.
8. **Refresh.** Any UI change produces a new target snapshot. The scanner reconciles the highlight
   without letting an old position activate a new action.

The switch is therefore only the user's low-bandwidth input. The scanner owns a state machine, and
the application being scanned supplies accurate target semantics and safe actions.

### Common scan strategies

| Strategy | Typical switch setup | Operation | Best fit | Main cost |
| --- | --- | --- | --- | --- |
| One-switch auto scan | Select | A timer advances; pressing selects | One reliable movement | Timing pressure |
| Two-switch step scan | Next and Select | One press advances; the other selects | Two reliable movements | More activations |
| Three-switch manual scan | Previous, Next, Select | User moves in either direction | Accurate recovery | More switch positions |
| Single-switch step and dwell | Next | Press advances; stopping for a dwell selects | One switch without moving timer | Selection delay |
| Inverse or hold scan | Hold or release | Holding advances; release selects or stops | Sustained activation is easier | Release timing |
| Row-column scan | Usually Select, optionally Next | Select a row, then an item within it | Regular keyboard or AAC grids | Two-stage errors |
| Block or group scan | One or more group choices | Repeatedly narrow a set | Large target collections | Group comprehension |
| Point scan | Select | Stop moving horizontal and vertical lines | UI without usable semantics | Slower and coordinate-sensitive |
| Radar scan | Select | Stop an angle, then a distance | Sparse or awkward geometry | Precision and timing |

The categories overlap. For example, row-column can advance automatically or manually.
[Android](https://support.google.com/accessibility/android/answer/6301497) uses row-column
automatically for keyboards when linear scanning is selected, while
[Grid 3](https://hub.thinksmartbox.com/knowledgebase/using-switch-scanning-with-grid-3/) and
[PRC-Saltillo](https://documentation.prc-saltillo.com/docs/switch-scanning-setup) expose row,
column, block, and cell choices directly.

### Input accommodations are core behavior

Products consistently treat timing and switch conditioning as first-class settings rather than
implementation details:

- scan interval and a longer pause on the first target;
- a finite or unlimited number of scan cycles;
- press versus release activation;
- minimum hold time before acceptance;
- ignore-repeat or debounce time;
- delay before held input repeats;
- restart location after activation or inactivity;
- auto-resume versus explicit restart; and
- long-press actions and reverse direction.

[Android Switch Access](https://support.google.com/accessibility/android/answer/6301497) exposes
scan time, first-item delay, scan count, repeat suppression, and release-to-act.
[Apple Switch Control](https://support.apple.com/guide/mac-help/mh43181/mac) exposes
hold-to-accept, ignore-repeat, repeat delay, restart position, and auto-resume.
[Grid 3](https://hub.thinksmartbox.com/knowledgebase/using-switch-scanning-with-grid-3/)
adds press or release acceptance, accidental-press filters, reverse scan, maximum cycles, and a
switch tester. No single timing value is generally correct. The architecture must allow the
scanner to own these settings without Alpha-OSK adding latency or repeat behavior of its own.

### Feedback is multimodal

The visible highlight is only one feedback channel. Mature products offer border or background
highlighting, color choices, magnification, progress toward automatic advance, selection sounds,
and spoken labels. [Grid 3](https://hub.thinksmartbox.com/knowledgebase/using-switch-scanning-with-grid-3/)
exposes all of these visual and audio variants, and
[TD Snap](https://us.tobiidynavox.com/pages/td-snap-scanning) supplies customizable auditory cues
for groups. [Android](https://support.google.com/accessibility/android/answer/6301497) and
[Apple](https://support.apple.com/guide/mac-help/mh43181/mac) also expose spoken feedback and sound
during navigation.

The provider should supply a short visible name, role, state, and geometry. The scanner should own
how those facts are announced. This avoids competing highlights or voices and lets one scanner
apply the user's preferences consistently across applications.

## Existing-product landscape

| Product | Scope | Discovery model | Scan methods and notable behavior |
| --- | --- | --- | --- |
| [Windows On-Screen Keyboard](https://support.microsoft.com/en-gb/windows/use-the-on-screen-keyboard-osk-to-type-ecbb5e08-5b4e-d8c8-f794-81dbf896267a) | Keyboard only | Own controls | Continually scans keyboard areas; selection can come from a shortcut, switch device, or simulated mouse click; includes text prediction and scan-speed control. |
| [Apple Switch Control](https://support.apple.com/en-ca/119835) | System-wide plus its own panels | Platform accessibility controls, groups, and point fallback | Item and group scanning, automatic and manual movement, point scanning, action menus, auditory feedback, custom panels, and cross-device control. |
| [Android Switch Access](https://support.google.com/accessibility/android/answer/6301497) | System-wide | Android accessibility nodes with point fallback | Linear, row-column, group, automatic, step, and point scanning; action menu; switch assignment; spoken feedback; hold and repeat accommodations. |
| [ChromeOS Switch Access](https://support.google.com/chromebook/answer/10274137) | System-wide | Platform accessibility tree with point fallback | Automatic or manual item scanning, groups, point scanning, and row-then-key scanning for the on-screen keyboard. |
| [Grid 3](https://hub.thinksmartbox.com/knowledgebase/using-switch-scanning-with-grid-3/) | AAC, computer access, and app functions | Own cells and authored groups | Cell, row-column, and block scanning; automatic, tap, and hold advance; rich highlights, audio, reverse, stop, back-one-level, and input filtering. |
| [PRC-Saltillo Unity and Dialogue AAC](https://documentation.prc-saltillo.com/docs/switch-scanning-setup) | AAC pages and app functions | Own buttons, rows, blocks, and authored flows | One- or two-switch operation; linear, row-column, block, and flow patterns; scan optimization, activation delay, scan counts, and empty-cell skipping. |
| [TD Snap Scanning](https://us.tobiidynavox.com/pages/td-snap-scanning) | AAC page set | Own buttons and designed scan groups | Pre-authored semantic groups, customizable auditory cues, highlighting and timing, frequency-oriented word lists, and layouts designed to avoid scrolling. |
| [Communicator 5](https://us.tobiidynavox.com/products/communicator-5) | AAC and Windows access | Own page sets and Windows-control tools | One- or two-switch scanning with configurable single-button, row, or column starts; accepts keyboard, joystick, or device switch-port input. |
| [Mind Express 5](https://www.jabbla.com/en/mind-express-5/) | AAC and Windows access | Own cells, scan groups, and mouse-control pages | One- or two-switch scanning, authored groups, per-group dwell, auditory feedback, and switch-driven Windows mouse and keyboard tools. |
| [Switchify](https://switchifyapp.com/how-it-works/) | System-wide Android, with PC companion work | Android accessibility items plus point and radar modes | Item, row-column, group, point, radar, automatic, and manual scanning; physical and camera switches; input filtering; action menus; PC switch forwarding. |

Three design patterns emerge.

First, app-owned scanning is highly reliable because the product knows every target and action.
Windows OSK and AAC products can also optimize layouts and groups around scanning rather than
reconstructing them from screen geometry.

Second, system-wide semantic scanning is more efficient than point scanning when applications
expose a correct accessibility tree. The scanner can speak labels, skip disabled or hidden items,
form groups, and invoke an action without steering a pointer.

Third, point scanning remains an essential fallback. It reaches canvases, remote desktops, games,
and inaccessible applications, but it cannot reliably infer role, enabled state, or whether the
same coordinate still represents the same action.

## Typical software

Switch-scanning software usually falls into four groups. A person may use more than one group on
the same device.

### Operating-system switch access

Apple Switch Control, Android Switch Access, and ChromeOS Switch Access scan controls across many
applications. They own the switch configuration, scan cursor, groups, action menu, timing, and
feedback. Applications participate by publishing good accessibility semantics. Point scanning
provides a coordinate fallback when an application does not.

Windows does not currently provide the same general system-wide switch-scanning experience in its
built-in accessibility settings. Its classic
[On-Screen Keyboard](https://support.microsoft.com/en-gb/windows/use-the-on-screen-keyboard-osk-to-type-ecbb5e08-5b4e-d8c8-f794-81dbf896267a)
can scan its own keyboard, accept a keyboard shortcut or switch-like input, and offer text
prediction. It does not document a general scanner for every application's controls.

### AAC and communication software

[Grid 3](https://thinksmartbox.com/product/grid-3/),
[Communicator 5](https://us.tobiidynavox.com/products/communicator-5),
[Mind Express 5](https://www.jabbla.com/en/mind-express/),
[TD Snap Scanning](https://us.tobiidynavox.com/pages/td-snap-scanning), and
[PRC-Saltillo scanning](https://documentation.prc-saltillo.com/docs/switch-scanning-setup) are
typical AAC products with scanning built into their own pages, keyboards, prediction areas, and
commands. Because the software owns the cells, it can author efficient groups, skip empty cells,
use frequency-sensitive layouts, and keep communication functions accessible without discovering
them from another process.

Several products extend beyond speech. Grid 3, Communicator 5, and Mind Express include Windows,
web, messaging, or environmental-control functions. Their outside-app control often uses a
scanner-owned keyboard, mouse-control page, pointer movement, or radar rather than semantic
enumeration of every control in every Windows application. For example,
[Mind Express Windows Control](https://www.jabbla.com/en/faq/access-your-windows-computer-via-mind-express/)
documents switch-driven mouse selection and an on-screen keyboard.

### Dedicated system-wide scanner applications

[Switchify](https://switchifyapp.com/how-it-works/) is a current example on Android. It scans
accessibility items, provides point and radar modes, handles physical and camera switches, and
forwards switch input to its PC companion. A dedicated scanner is where a person's scan timing,
switch mappings, filtering, overlay appearance, and spoken feedback can remain consistent across
applications.

On Windows, this category is more fragmented than on Apple, Android, and ChromeOS. That makes a
standards-based accessible provider especially useful: Alpha-OSK can work with a compatible
scanner without embedding that scanner's device mappings or scan state.

### Switch utilities and device configuration

The utility supplied with a switch interface or adaptive controller may map jacks to keyboard
keys, mouse buttons, or game-controller buttons. It usually does not perform the visual scan.
The scanning application consumes those mapped events.

For example, [Tapio](https://www.orin.com/access/tapio/tapiospecs) can output Space and Enter,
left and right mouse buttons, or joystick buttons. The
[Pretorian Simple Switch Interface](https://www.pretorianuk.com/simple-switch-interface/) can
output Space/Enter, mouse buttons, F7/F8, or gamepad buttons. Grid 3 accepts joystick, keyboard,
mouse, touchscreen, or specialist-device input, and notes that keyboard input is how most
third-party switch interfaces connect in its
[connection guide](https://hub.thinksmartbox.com/knowledgebase/how-do-i-connect-a-switch-to-grid-3/).

## Typical hardware

The most common physical setup is a normally open momentary switch with a 3.5 mm plug, connected
either to a device's built-in switch jack or to a USB or Bluetooth switch interface. The interface
usually appears to the computer as a standard Human Interface Device (HID), such as a keyboard,
mouse, or game controller. The scanning software then interprets that ordinary input as Next,
Select, Previous, Back, or another scan command.

### How the hardware plugs in

The 3.5 mm plug on a conventional adaptive switch resembles a headphone plug, but it carries a
simple switch contact rather than audio. It must connect to a socket designed for adaptive
switches, not to the computer's headphone or microphone socket.

For one wired switch:

`adaptive switch -> 3.5 mm switch socket -> USB interface -> computer USB port`

For two wired switches:

`Next switch -> switch socket 1`

`Select switch -> switch socket 2`

`two-socket interface -> USB -> computer`

Interfaces differ physically. The
[Pretorian Simple Switch Interface](https://www.pretorianuk.com/simple-switch-interface/) has two
3.5 mm switch sockets. [Tapio](https://www.orin.com/access/tapio/tapiospecs) has one 3.5 mm stereo
socket that accepts one switch directly or two mono switches through the manufacturer's
stereo-to-mono adapter. The correct adapter is therefore determined by the interface, not by the
scanning software.

The host connection then takes one of four common forms:

| Connection | Physical setup | Result seen by scanning software |
| --- | --- | --- |
| USB switch interface | Switch plugs into the interface; interface plugs into USB-A or USB-C, sometimes through an adapter | Standard HID keyboard, mouse, joystick, or platform switch events |
| Bluetooth switch or interface | Switch is built in or plugs into a Bluetooth interface; interface pairs in the operating system's Bluetooth settings | Usually keyboard-like or platform switch events |
| Built-in AAC switch port | Switch plugs directly into a 3.5 mm socket marked Switch, S1, or S2 on the communication device | Device switch event exposed to its AAC or access software |
| Adaptive controller | Switch plugs into the labeled 3.5 mm input for a controller action; controller connects through USB, Bluetooth, or its platform radio | Game-controller button or axis event |

An integrated Bluetooth product such as
[Blue2 FT](https://www.ablenetinc.com/blue2-ft/) combines the switch surfaces and wireless
interface. It can also accept separate wired switches through its two external switch jacks. A
specialty control such as the
[Breeze sip-and-puff interface](https://www.orin.com/access/docs/Breeze_oneSheet.pdf) similarly
combines the sensor and USB interface.

Tecla-e is a broader example of the same interface layer. According to the current
[tecla-e specifications](https://gettecla.com/products/tecla-e), it has a built-in light-touch
switch, two 3.5 mm stereo switch ports, a D-Sub wheelchair-control port, Bluetooth Low Energy, and
USB-C power. It can accept conventional ability switches or wheelchair driving controls and pair
with multiple host devices. Its manufacturer explicitly lists Windows as requiring compatible
scanning software.

The Tecla path for Alpha-OSK would be:

`switch or wheelchair control -> tecla-e -> Bluetooth input -> Windows scanning software -> UIA Invoke -> Alpha action`

Tecla-e supplies and routes the switch signals. The Windows scanner performs the scan and selects
the current Alpha target. Alpha does not pair with Tecla, interpret its Bluetooth packets, use its
cloud features, or depend on Tecla-specific button assignments.

After connection, the operating system normally sees ordinary input rather than a device named
"scanner." The interface may produce Space, Enter, a number key, a mouse button, or a gamepad
button. The scanning application learns or is configured for those events and assigns them to
Select, Next, Previous, or another scan command.

Interface timing also matters. Some interfaces hold the HID button for as long as the physical
switch remains closed. Others emit a short press-and-release pulse. Tapio, for example, documents
both full-duration and pulse modes. A scanner that supports hold actions or release-to-select needs
the full press and release edges, while a simple one-switch auto scan may work with a pulse. This
choice belongs in switch-interface and scanner setup, not in Alpha-OSK.

For Alpha-OSK, the normal boundary is:

`switch hardware -> interface -> scanner input -> scanner selection -> UIA Invoke -> Alpha action`

Alpha never opens the USB, Bluetooth, serial, or game-controller device for this integration. It
exposes current keyboard targets and receives one accessible activation after the external scanner
has interpreted the user's switch input.

### Common switch types

| Hardware type | Typical activation | Examples | Practical reason to choose it |
| --- | --- | --- | --- |
| Large mechanical button | Hand, fist, forearm, foot, or another broad movement | [AbleNet Big Red](https://www.ablenetinc.com/big-red/), [Buddy Button](https://www.inclusive.com/products/buddy-button-ic) | Large target with tactile and audible confirmation |
| Small mechanical button | Finger, thumb, chin, or another precise movement | Specs, Jelly Bean, Micro Light, or cup-style switches in the [AbleNet selection guide](https://www.ablenetinc.com/content/html/Downloads/Switch_Downloads/ablenet_switch_grid.pdf) | Easier mounting and lower travel or force |
| Flat light-touch membrane | Small finger, hand, head, or shallow-angle movement | [Pal Pad](https://support.inclusive.com/article/pal-pad-switch) | Very low profile and low activation force |
| Soft surface switch | Head or cheek | [Pillow Switch](https://www.inclusive.com/products/pillow-switch) | Comfortable repeated contact against the body |
| Proximity switch | Movement near a sensor, with little or no pressure | Candy Corn proximity switches, [Blue2 FT](https://www.ablenetinc.com/blue2-ft/) | Useful when physical pressing causes fatigue or is unreliable |
| Sip-and-puff | Positive and negative air pressure | [Origin Instruments Breeze](https://www.orin.com/access/docs/Breeze_oneSheet.pdf) | Two distinct signals from breath when limb movement is limited |
| Integrated wireless switch | Direct touch or proximity over Bluetooth | Blue2 FT, Solo Touch, iSwitch-class devices | Omits a separate interface and cable to the computer |
| Existing device control | Keyboard key, mouse button, gamepad button, touchscreen, or phone volume key | Built-in platform setup options | Useful for trials or when dedicated hardware is unnecessary |
| Camera or sensor gesture | Blink, wink, smile, head movement, or muscle signal | Switchify camera switches and specialist sensor products | Provides a software-defined switch when contact hardware is unsuitable |

These are categories, not a ranked shopping list. A switch that can be activated once may still be
too tiring, slow, or unstable for repeated scanning. Movement reliability, force, range, release
control, fatigue, positioning, and mounting matter more than brand. Inclusive Technology's
[switch-selection overview](https://www.inclusive.com/blogs/inclusive-insights/ian-explains-what-is-a-switch)
describes common activation sites including hand, head, chin, cheek, elbow, knee, foot, and small
finger movement, and emphasizes assessing repeatable use and placement.

### Common interfaces and hubs

| Interface type | Typical examples | What the computer receives |
| --- | --- | --- |
| Wired one- or two-switch USB interface | [Tapio](https://www.orin.com/access/tapio/tapiospecs), [Pretorian Simple Switch Interface](https://www.pretorianuk.com/simple-switch-interface/) | Keyboard, mouse, joystick, or platform switch events through standard USB HID |
| Bluetooth switch interface | [APPlicator](https://support.inclusive.com/article/applicator), Blue2-class interfaces | Configurable keyboard, mouse, media, or platform switch events |
| Multi-device Bluetooth and wheelchair hub | [tecla-e](https://gettecla.com/products/tecla-e) | Bluetooth switch input for built-in platform access or compatible scanning software |
| AAC-device switch ports | Grid Pad and Tobii Dynavox communication devices | Device-specific switch port exposed to the AAC software, often mappable to keyboard-style actions |
| Adaptive controller hub | [Xbox Adaptive Controller](https://www.xbox.com/en-US/accessories/controllers/xbox-adaptive-controller/) | Game-controller buttons and axes from external 3.5 mm switches and USB controls |
| Integrated specialty interface | Breeze sip-and-puff or a sensor with built-in USB/Bluetooth | One or more HID events without a separate switch box |

The Xbox Adaptive Controller is common in gaming and can connect many external controls through 19
3.5 mm inputs and two USB ports. It is useful when the scanner accepts game-controller events, but
it is more hardware than a one- or two-switch keyboard-scanning setup normally requires.

### Typical complete signal paths

The ordinary wired path is:

`body movement -> 3.5 mm switch -> USB interface -> HID key/button -> scanner -> highlighted target -> accessible action`

The ordinary wireless path is:

`body movement -> Bluetooth switch/interface -> HID key/button -> scanner -> highlighted target -> accessible action`

An AAC device with built-in ports omits the external interface. A camera switch omits both the
physical switch and switch interface. In every case, the scanner should consume press and release
events, apply the person's input accommodations, and issue one logical selection. Alpha-OSK should
not infer switch duration from how long an accessibility action is held, because accessible Invoke
is a one-shot action.

### Typical one- and two-switch mappings

One-switch auto scanning maps the only switch to Select. Two-switch step scanning usually maps one
switch to Next and the other to Select. Space and Enter are common interface outputs, but joystick
buttons, mouse buttons, numbers, and function keys also occur. The mapping is configuration, not a
protocol Alpha should hard-code.

For Alpha's testing, a representative hardware matrix is:

1. one wired momentary switch through a USB HID interface;
2. two wired switches through a USB HID interface;
3. one integrated Bluetooth switch;
4. keyboard keys standing in for one and two switches;
5. game-controller buttons through an adaptive-controller profile; and
6. a scanner-generated camera gesture, tested as scanner input rather than Alpha input.

This covers the common transport behaviors without requiring Alpha to own or certify each physical
switch model.

## Implications for Alpha-OSK

### What Alpha-OSK should own

Alpha-OSK is authoritative for:

- which keys and prediction pills currently exist;
- their visible labels, roles, states, bounds, and visual order;
- whether a target is hidden, disabled, or removed by privacy mode;
- the exact one-shot action associated with each target;
- action generations when a position acquires new meaning; and
- preserving no-focus, sticky-modifier, password, learning, and telemetry invariants during an
  accessible activation.

The accessible action must emit the same QML signal as a primary click. Character keys continue
through `KeyboardBridge.pressKey` or `pressKeyLiteral`, special keys through `pressSpecialKey`, and
prediction pills through `pressPrediction`. It must not call the repeat-arming helper. The
[Qt Accessible QML documentation](https://doc.qt.io/qt-6/qml-qtquick-accessible.html) states that
`Accessible.onPressAction` should have the same effect as tapping or clicking the control.

### What the scanner should own

Every external scanner remains authoritative for:

- switch device discovery and mapping;
- press, release, hold, debounce, and long-press interpretation;
- automatic, step, group, row-column, point, or other scan strategy;
- scan interval, pauses, cycles, restart, and recovery;
- visual overlay, progress, audio, and speech;
- snapshot refresh frequency and reconciliation; and
- the final user-intent decision to invoke one current target.

This boundary allows Switchify, another commercial scanner, an open-source client, or a small
personal tool to scan Alpha-OSK without requiring Alpha to understand that client's hardware or
interaction model.

### What not to combine

Do not make an accessibility Invoke start or stop Alpha's key-repeat state. Scanners already
condition switch input and may hold a switch for navigation. Mapping that hold to a keyboard key's
pointer-repeat helper could duplicate letters or destructive keys.

Do not expose prediction history, typed context, model scores, snippets, or telemetry. A visible
prediction label is sufficient to announce and select the pill. When privacy mode removes
predictions, those targets must disappear from the accessible tree before the next snapshot.

Do not require SetFocus. Alpha's window must continue to avoid OS focus so synthesized text reaches
the user's foreground application. The target can be actionable through accessibility while
accurately reporting that it does not accept keyboard focus.

## Scanner-neutral target contract

Define the semantics independently of UIA, macOS Accessibility, or AT-SPI. Each platform adapter
then maps the same model to its native accessibility API.

### Root fields

The Alpha-OSK accessible root should provide:

- product identity;
- contract major and minor version;
- current snapshot revision;
- active layout or surface identity when that affects scan order; and
- an ordered collection of visible targets.

A scanner rejects an unsupported major version. A minor version can add optional metadata without
changing existing action meaning.

### Target fields

| Semantic field | Meaning |
| --- | --- |
| Stable identity | Unique within the current accessible tree |
| Action generation | Changes whenever the target's action changes |
| Label | Short text visible to the user |
| Description | Optional clarification, never private context |
| Kind | Key, prediction, modifier, navigation, or other documented type |
| Enabled | Whether primary activation is currently accepted |
| Showing | Whether the target is visibly available to scan |
| Bounds | Platform-native global screen rectangle with documented units |
| Order | Deterministic visual order, independent of tree traversal accidents |
| Group | Optional row, section, or authored semantic group |
| Toggle state | Inactive, sticky, or locked where applicable |
| Primary action | One non-retrying activation |

Use native properties wherever the platform supplies them. Put only missing machine-readable
metadata in the accessible identifier or other stable native field.

For Windows, an identifier can follow this grammar:

`alpha-osk.target.v1/<kind>/<generation>/<layout>/<layer>/<section>/<row>/<column>/<action>`

The prediction text belongs in the accessible name, not the identifier. Fixed-width row and column
segments make lexical and numeric order agree. IDs should be unique across the exposed subtree,
even where a platform promises uniqueness only among siblings.

### Snapshot consistency and stale targets

The scanner must treat enumeration as a snapshot, not a collection of permanent coordinates.
Before activation it should confirm that the element is still enabled and showing and that its
runtime identity, stable ID, generation, and label still match the highlighted choice.

When a prediction, layout key, layer key, or programmed key acquires a different action,
Alpha-OSK should replace its accessible object and increment the action generation. An old
reference must fail or perform no action. Movement, resizing, highlight changes, and modifier-state
updates do not change action identity and should not churn the object unnecessarily.

Activation is not safe to retry automatically. If a platform call times out after dispatch, the
scanner cannot assume no action occurred. It should stop, refresh, and ask for a new user selection.

## Platform adapters

Qt provides a common application-facing accessibility layer and supports Windows accessibility,
macOS Accessibility, and Unix/X11 AT-SPI, according to its
[QAccessible documentation](https://doc.qt.io/qt-6/qaccessible.html). That common layer reduces
provider work, but client authorization, coordinate rules, event behavior, and API names remain
platform-specific.

| Platform | Native discovery and activation | Client authorization | Recommended Alpha status |
| --- | --- | --- | --- |
| Windows | UI Automation properties, tree, bounds, and Invoke | UIPI and integrity rules; UIAccess is required for the relevant higher-integrity access and has signing and protected-location requirements | First implementation, with cached polling |
| macOS | AXUIElement attributes and actions | User grants the scanner Accessibility trust; assistive use of accessibility APIs is incompatible with App Sandbox | Design mapping now, validate in the macOS port |
| Linux desktop | AT-SPI Accessible, Component, Action, Collection, Cache, and events over the accessibility bus | Desktop/session policy, with no Windows-like publisher identity guarantee in the AT-SPI contract | Prototype per supported desktop and display stack before promising parity |

On macOS, the client can test whether it is a trusted accessibility client with
[AXIsProcessTrusted](https://developer.apple.com/documentation/applicationservices/1460720-axisprocesstrusted),
inspect an application's AX elements, and request an action with
[AXUIElementPerformAction](https://developer.apple.com/documentation/applicationservices/1462091-axuielementperformaction).
Apple also documents assistive use of accessibility APIs as
[incompatible with App Sandbox](https://developer.apple.com/documentation/security/protecting-user-data-with-app-sandbox).
These rules authorize the scanner as an assistive client, not as one named partner.

On Linux, AT-SPI's
[Action.DoAction](https://gnome.pages.gitlab.gnome.org/at-spi2-core/devel-docs/doc-org.a11y.atspi.Action.html)
invokes an accessible action and
[Component.GetExtents](https://gnome.pages.gitlab.gnome.org/at-spi2-core/devel-docs/doc-org.a11y.atspi.Component.html)
returns screen-relative or window-relative pixel bounds. Qt activates AT-SPI when the desktop
accessibility status indicates that it is enabled, or when explicitly forced through its
[documented environment setting](https://doc.qt.io/qt-6/qaccessible.html). Desktop, D-Bus, X11,
and Wayland combinations still need packaged testing because the protocol alone does not prove
overlay placement or authorization behavior across every environment.

## Windows UI Automation design

### Provider feasibility

Qt Quick 6.11 exposes `Accessible.role`, `name`, `description`, `id`, state properties, and
`pressAction`. Qt's Windows platform plugin maps accessible controls to UIA providers so clients can
retrieve properties and invoke functionality across the process boundary. See Qt's
[Accessible QML type](https://doc.qt.io/qt-6/qml-qtquick-accessible.html) and Microsoft's
[UI Automation provider overview](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-providersoverview).

Alpha-OSK's custom QML controls currently need explicit accessible metadata. Each visible key and
prediction delegate should be a Button, with toggle semantics added for sticky and locked
modifiers. `Accessible.focusable` must be set consistently with the keyboard's no-focus design,
rather than accepting the Button role's default.

### Discovery, caching, and coordinates

A scanner should locate Alpha's top-level UIA window, verify the owning process and product, then
query descendants with the contract identifier prefix. Microsoft documents that
[UIA caching](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-cachingforclients)
can retrieve several properties for several elements in one cross-process call. Cached patterns
cannot perform actions, so the client must retain or reacquire a live element for the final Invoke.

UIA bounding rectangles use physical coordinates. Microsoft's
[screen-scaling guidance](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-screenscaling)
requires a UIA client to be DPI-aware and use corresponding physical cursor or overlay coordinates.
Tests must cover mixed scale factors and negative desktop coordinates for monitors left of or above
the primary display.

### Update behavior on the pinned Qt release

Qt Quick creates accessibility updates for item movement and visibility, but inspection of Qt
6.11's
[Windows UIA event dispatch](https://github.com/qt/qtbase/blob/6.11/src/plugins/platforms/windows/uiautomation/qwindowsuiaaccessibility.cpp)
shows that `LocationChanged`, `ObjectShow`, and `ObjectHide` are not forwarded. `NameChanged` is
forwarded only for combo boxes or the focused element. Alpha's keys deliberately do not take focus.

The safe first implementation is bounded polling:

1. fetch the small target subtree with one cache request;
2. compare IDs, labels, state, groups, order, and bounds with the last snapshot;
3. rebuild only the affected scan state or overlay; and
4. measure CPU use and p95 detection latency during prediction changes, layout changes, movement,
   and resizing.

If polling cannot meet the agreed latency and power targets, add a one-way local invalidation
signal containing only `{protocol, revision}`. UIA remains authoritative for data and activation.
The notification accepts no commands and carries no labels, bounds, context, or prediction text.

### Windows authorization boundary

Microsoft describes UIA as the Windows accessibility interface and UIAccess as the mechanism that
allows a qualifying assistive application to cross relevant integrity boundaries. Its
[assistive-technology security guidance](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-securityoverview)
and [secure-location policy](https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-10/security/threat-protection/security-policy-settings/user-account-control-only-elevate-uiaccess-applications-that-are-installed-in-secure-locations)
require an appropriate manifest, Authenticode signature, and protected installation location.

For Alpha's standard-user installation, the practical contract is "a scanner Windows permits to
reach this UI at the applicable integrity level," not "a scanner signed by one approved
publisher." This is important in both directions:

- a normal process without the necessary integrity cannot treat Alpha as an unrestricted input
  broker; and
- another qualifying assistive client may be able to inspect and invoke Alpha's exposed controls.

This is suitable when interoperability with assistive technologies is the policy goal. It is not
suitable if Alpha must cryptographically identify one partner before every activation.

## Evaluation of integration options

| Option | Interoperability | Security boundary | Reliability | Recommendation |
| --- | --- | --- | --- | --- |
| Native accessible controls plus cached polling | Any permitted platform accessibility client | OS accessibility and integrity policy | Pull model covers missed events | Default |
| Native controls plus one-way revision signal | Same as above | Activation remains in native accessibility API | Faster refresh without duplicated state | Add only if measured need exists |
| Alpha-owned internal scan engine | Any switch that Alpha can capture | No external activation channel | Highest control over its own UI | Separate product feature, not required for external scanners |
| Point or radar scanning | Any visible UI | Scanner ultimately synthesizes pointer input | Works without semantics but is coordinate-sensitive | Scanner fallback |
| Full duplex local activation protocol | Only clients the custom protocol authenticates | Entirely application-defined | Can provide atomic revision checks | Defer unless partner exclusivity is required |
| Loopback HTTP or WebSocket | Broad local reach by default | Token and origin design required | Easy schema, weak native discovery | Reject for v1 |
| Window messages | Windows only | Weak caller identity and awkward UIPI behavior | Poor versioning and stale-state handling | Reject |
| Shared memory plus events | Platform-specific | ACL and lifecycle design required | Fast but easy to desynchronize | Reject |

An internal scan mode and an external accessible provider are complementary. Internal scanning
would make Alpha self-contained and match the Windows OSK and AAC products, but it would also make
Alpha responsible for switch capture, timing, feedback, and configuration. The provider approach
lets users keep those personalized settings in their existing system-wide scanner.

## If exclusive activation IPC becomes necessary

A same-user pipe is insufficient for a UIAccess input provider. A process that can direct Alpha to
press keys may gain input effects it could not produce itself. Qt's Windows `QLocalServer` rejects
remote clients, but its implementation does not request `FILE_FLAG_FIRST_PIPE_INSTANCE`, so it is
not by itself a complete basis for first-instance ownership and client verification. See the
[QLocalServer documentation](https://doc.qt.io/qt-6/qlocalserver.html) and
[Windows implementation](https://github.com/qt/qtbase/blob/6.11/src/network/socket/qlocalserver_win.cpp).

A future partner-only protocol would need, at minimum:

- a native local named pipe with `PIPE_REJECT_REMOTE_CLIENTS` and
  `FILE_FLAG_FIRST_PIPE_INSTANCE`;
- an explicit DACL for the intended logon identity, never the default pipe descriptor;
- connected client PID retrieval followed by a held process handle;
- client token checks for user SID, logon session, integrity, and UIAccess;
- canonical protected image path and Authenticode publisher verification, with certificate
  rotation policy;
- reciprocal verification of the Alpha server;
- a versioned, length-prefixed schema with small frames, bounded strings and nesting, deadlines,
  connection limits, and rate limits;
- activation only by current target ID and generation, rechecked on the Qt GUI thread; and
- small enumerated results with no context, model data, paths, secrets, or exception text.

Microsoft documents
[named-pipe ACL behavior](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights),
[first-instance creation](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-createnamedpipew),
and [connected-client process identification](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getnamedpipeclientprocessid).
This design is possible, but it creates a second privileged action protocol and should not be the
default merely to avoid UIA polling.

## Product-specific case study: Switchify

Switchify is one possible client, not part of the generalized contract. Its current public product
description matches the common system-wide architecture: semantic item scanning first, point and
radar fallbacks, automatic and manual movement, action menus, multiple physical and camera input
sources, and press filtering. See
[How Switchify works](https://switchifyapp.com/how-it-works/).

The Windows packaging reviewed for issue 106 is compatible with a production UIA spike. Alpha-OSK
and Switchify both publish Windows configurations for signed UIAccess applications installed under
Program Files. The inspected Alpha-OSK 1.4.1 and Switchify 1.0.0-rc.6 installers had valid
Authenticode signatures and hashes matching their GitHub release asset digests. This verifies
those installer artifacts, not every future release or the runtime behavior of their installed
executables. See the [Alpha-OSK manifest](../../build/windows/alpha-osk.exe.manifest),
[Alpha-OSK 1.4.1 release](https://github.com/owenpkent/alpha-osk-releases/releases/tag/v1.4.1),
[Switchify packaging notes](https://github.com/switchifyapp/switchify-pc#readme), and
[Switchify UIAccess manifest](https://github.com/switchifyapp/switchify-pc/blob/main/src-tauri/windows/uiaccess.manifest).

Switchify's public UIAccess manifest does not declare a DPI mode. Its locked Tao 0.35.3 runtime
defaults DPI awareness on and requests Per-Monitor V2 on supported Windows, and no repository
override disabling that default was found. See Switchify's
[locked Rust dependencies](https://github.com/switchifyapp/switchify-pc/blob/main/src-tauri/Cargo.lock)
and Tao's
[DPI setup](https://github.com/tauri-apps/tao/blob/tao-v0.35.3/src/platform_impl/windows/dpi.rs).
The packaged client must still verify effective DPI awareness and overlay alignment at runtime.

Nothing in the provider contract should require Switchify-specific process names, identifiers,
switch mappings, scan patterns, or timing. If Switchify is replaced, forked, or joined by another
scanner, Alpha's accessible surface should remain unchanged.

## Delivery sequence

1. Add accessible metadata and one-shot press actions to key and prediction delegates.
2. Define the versioned target ID grammar, group metadata, and deterministic visual order.
3. Replace accessible objects whenever an action changes and test stale references.
4. Build a small Windows UIA conformance probe that prints one cached snapshot and can invoke one
   explicitly chosen test target.
5. Test one-switch auto, two-switch step, row-column, and auditory client prototypes against the
   same target snapshot. Alpha should require no mode-specific changes.
6. Integrate the provider into Switchify or another scanner and render its existing no-activate
   overlay from UIA physical bounds.
7. Measure polling overhead and refresh latency. Add the one-way revision signal only if the agreed
   acceptance threshold is missed.
8. Test signed production-style UIAccess builds. Unsigned debug builds do not reproduce the
   production integrity boundary.
9. Prototype the same semantic controls through macOS Accessibility and AT-SPI when those Alpha
   ports are ready, without copying the Windows identifier or coordinate assumptions blindly.

## Acceptance tests

### Provider conformance

- Every visible key and prediction appears exactly once with a unique current identity.
- Traversal or explicit order is deterministic and matches the documented visual scan order.
- Groups accurately represent sections, rows, and prediction areas without forcing one scan
  strategy.
- Hidden panels, disabled actions, and privacy-suppressed predictions are absent or accurately
  non-actionable.
- Names contain visible labels only. No typed context, scores, history, snippets, or secrets appear.
- Modifiers expose inactive, sticky, and locked state without changing action identity.

### Action behavior

- Pointer click and accessible activation reach the same bridge method for characters, literals,
  modifiers, special keys, programmed keys, page changes, and predictions.
- Accessible activation is one-shot and never arms Alpha's pointer-repeat behavior.
- Activation never gives Alpha focus or changes the foreground target.
- Password checks, privacy gates, sticky-modifier release, literal insertion, and prediction
  behavior remain identical to primary clicks.
- Holding an old prediction or changed-key reference across an update performs no action.
- An ambiguous timeout is never retried automatically.

### Scanner interoperability

- One-switch auto scan, two-switch step scan, and row-column scan can use the same provider tree.
- Previous, cancel, group exit, scanner pause, and scanner restart require no Alpha-side state.
- Visual and auditory feedback can be produced entirely from public name, state, group, order, and
  bounds.
- Prediction changes do not unexpectedly reset a scanner that is currently highlighting an
  unchanged static key.
- A scanner crash, Alpha restart, malformed tree, or notification failure leaves ordinary keyboard
  operation unchanged.

### Display and security

- Mixed-DPI monitors, negative desktop coordinates, keyboard movement, and resizing keep the
  external highlight aligned.
- A client outside the platform's applicable accessibility authorization cannot use the provider
  to cross an integrity or consent boundary.
- Discovery verifies the provider process and product before displaying or invoking targets.
- Privacy mode removes prediction targets before a refreshed snapshot can expose them.

## Evidence boundaries

The product comparison describes documented capabilities, not a comparative usability trial. No
scan strategy, speed, or grouping is universally best, and clinical configuration should be based
on the person's movements, fatigue, sensory needs, and communication goals.

The detailed event-gap conclusion is specific to Qt 6.11's Windows backend and must be rechecked
when Qt changes. The Windows security conclusion depends on the actual runtime token, signature,
manifest, installation path, and machine policy. Repository configuration alone does not prove
those runtime facts.

The macOS and Linux sections establish architectural mappings from official APIs. They are not
claims of packaged Alpha-OSK parity. End-to-end behavior must be tested on each supported desktop,
display server, permission configuration, and scale arrangement.

## Reference index

Platform scanning:

- Microsoft: [Windows On-Screen Keyboard](https://support.microsoft.com/en-gb/windows/use-the-on-screen-keyboard-osk-to-type-ecbb5e08-5b4e-d8c8-f794-81dbf896267a)
- Apple: [Switch Control](https://support.apple.com/en-ca/119835) and
  [Mac Switch Control settings](https://support.apple.com/guide/mac-help/mh43181/mac)
- Google: [Android Switch Access setup](https://support.google.com/accessibility/android/answer/6301490),
  [Android scanning settings](https://support.google.com/accessibility/android/answer/6301497),
  and [ChromeOS Switch Access](https://support.google.com/chromebook/answer/10274137)

AAC and scanning software:

- Smartbox: [Grid 3 scanning](https://hub.thinksmartbox.com/knowledgebase/using-switch-scanning-with-grid-3/)
- PRC-Saltillo: [switch-scanning setup](https://documentation.prc-saltillo.com/docs/switch-scanning-setup)
- Tobii Dynavox: [TD Snap Scanning](https://us.tobiidynavox.com/pages/td-snap-scanning) and
  [Communicator 5](https://us.tobiidynavox.com/products/communicator-5)
- Jabbla: [Mind Express 5](https://www.jabbla.com/en/mind-express-5/) and
  [Windows Control](https://www.jabbla.com/en/faq/access-your-windows-computer-via-mind-express/)
- Switchify: [product behavior](https://switchifyapp.com/how-it-works/) and
  [open-source Android implementation](https://github.com/switchifyapp/switchify-android)

Typical hardware and interfaces:

- AbleNet: [switch selection guide](https://www.ablenetinc.com/content/html/Downloads/Switch_Downloads/ablenet_switch_grid.pdf),
  [Big Red](https://www.ablenetinc.com/big-red/), and [Blue2 FT](https://www.ablenetinc.com/blue2-ft/)
- Origin Instruments: [Tapio USB interface](https://www.orin.com/access/tapio/tapiospecs) and
  [Breeze sip-and-puff interface](https://www.orin.com/access/docs/Breeze_oneSheet.pdf)
- Pretorian: [Simple Switch Interface](https://www.pretorianuk.com/simple-switch-interface/) and
  [APPlicator Bluetooth interface](https://support.inclusive.com/article/applicator)
- Tecla: [tecla-e product and port specifications](https://gettecla.com/products/tecla-e) and
  [device compatibility](https://gettecla.com/pages/tecla-e)
- Microsoft: [Xbox Adaptive Controller](https://www.xbox.com/en-US/accessories/controllers/xbox-adaptive-controller/)

Provider APIs and security:

- Qt: [Accessible QML](https://doc.qt.io/qt-6/qml-qtquick-accessible.html) and
  [QAccessible platform support](https://doc.qt.io/qt-6/qaccessible.html)
- Microsoft: [UIA provider model](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-providersoverview),
  [caching](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-cachingforclients),
  [screen scaling](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-screenscaling),
  and [assistive-technology security](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-securityoverview)
- Apple: [trusted accessibility clients](https://developer.apple.com/documentation/applicationservices/1460720-axisprocesstrusted)
  and [performing AX actions](https://developer.apple.com/documentation/applicationservices/1462091-axuielementperformaction)
- GNOME: [AT-SPI Action](https://gnome.pages.gitlab.gnome.org/at-spi2-core/devel-docs/doc-org.a11y.atspi.Action.html)
  and [AT-SPI Component](https://gnome.pages.gitlab.gnome.org/at-spi2-core/devel-docs/doc-org.a11y.atspi.Component.html)
