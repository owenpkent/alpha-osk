// Key colouring by role: the whole colour engine behind
// Settings -> Appearance -> Key Colours.
//
// A key's fill can encode what the key *does* (types a letter, holds a
// modifier, destroys text, commits) rather than only which surface it
// happens to sit on. `roleMap()` turns a scheme id plus the active theme
// into one {role: {fill, ink, bar}} table, and Main.qml hands that table to
// every keyboard surface. "off" returns null, which is how each surface
// keeps its own historical tint.
//
// TWO RULES GOVERN EVERYTHING HERE, and both exist because nine themes ship.
//
// 1. No hue is ever a literal. Several themes carry a pale accent
//    (Blackboard, Spaceship), one is light outright (Typewriter) and one is
//    near-black (Spaceship), so a fixed "#4a9eff for navigation" is either
//    invisible or garish on about half of them. Every family hue is ROTATED
//    OFF THE THEME'S OWN ACCENT, so a Vaporwave board gets vaporwave role
//    colours and a Forest board gets forest ones.
//
// 2. The rotation happens in OKLCh, not HSL. Rotating hue in HSL holds the
//    *number* L constant while perceived lightness swings wildly (HSL yellow
//    at L=50% is far brighter than HSL blue at L=50%), so an evenly-spaced
//    HSL palette produces bands where some shout and others whisper. OKLCh
//    is perceptually uniform, so holding L and C while rotating h gives
//    hues that genuinely read as equal weight. That is the difference
//    between a palette that looks designed and one that looks assigned.
//
// The family hues additionally take their lightness FROM THE KEY COLOUR
// ITSELF, so a wash changes a keycap's hue and chroma without moving its
// brightness. The board keeps one even tone and the bands differ only in
// colour, which is what stops "colour by function" from reading as confetti.

// ---------------------------------------------------------------- basics

function hex(s) {
    s = String(s).replace("#", "")
    if (s.length === 3)
        s = s[0] + s[0] + s[1] + s[1] + s[2] + s[2]
    return Qt.rgba(parseInt(s.substr(0, 2), 16) / 255,
                   parseInt(s.substr(2, 2), 16) / 255,
                   parseInt(s.substr(4, 2), 16) / 255, 1)
}

function clamp01(v) { return v < 0 ? 0 : (v > 1 ? 1 : v) }

// A linear blend of `target` into `base`. Qt.tint over an alpha is exactly
// that, and reusing it keeps this in step with accentWashFor's own maths.
function mixToward(base, target, t) {
    return Qt.tint(base, Qt.rgba(target.r, target.g, target.b, t))
}

// ------------------------------------------------------- WCAG contrast
//
// The single copy in the project: Main.qml's relativeLuminance /
// contrastRatio / accentWashFor all delegate here. Two copies of a
// luminance rule is how the two drift apart, which this codebase has
// already paid for once (see the `luminance` note in Main.qml).

function relativeLuminance(c) {
    function channel(v) {
        return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
    }
    return 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b)
}

function contrastRatio(a, b) {
    var la = relativeLuminance(a)
    var lb = relativeLuminance(b)
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

// Walk the tint strength down from `start` until the theme's own text
// colour clears 4.5:1 on the result. A scheme therefore cannot cost
// legibility on any theme: where a wash would bury the label it yields
// instead. Same rule and same reason as the compact view's accent keys.
function washFor(base, hue, start, text) {
    for (var a = start; a > 0.005; a -= 0.01) {
        var candidate = mixToward(base, hue, a)
        if (contrastRatio(text, candidate) >= 4.5)
            return candidate
    }
    return base
}

// ------------------------------------------------------------- OKLab
//
// Björn Ottosson's OKLab, the perceptually uniform space this engine
// rotates hue in. Coefficients are the published ones; kept verbatim so
// they can be diffed against the reference.

function _cbrt(v) {
    return v < 0 ? -Math.pow(-v, 1 / 3) : Math.pow(v, 1 / 3)
}
function _toLinear(v) {
    return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
}
function _fromLinear(v) {
    return v <= 0.0031308 ? v * 12.92 : 1.055 * Math.pow(v, 1 / 2.4) - 0.055
}

function toOklch(color) {
    var r = _toLinear(color.r), g = _toLinear(color.g), b = _toLinear(color.b)
    var l = _cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    var m = _cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    var s = _cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    var L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s
    var A = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
    var B = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
    var h = Math.atan2(B, A) * 180 / Math.PI
    return { L: L, C: Math.sqrt(A * A + B * B), h: h < 0 ? h + 360 : h }
}

function _oklchToRgb(L, C, hDeg) {
    var h = hDeg * Math.PI / 180
    var A = C * Math.cos(h), B = C * Math.sin(h)
    var l = L + 0.3963377774 * A + 0.2158037573 * B
    var m = L - 0.1055613458 * A - 0.0638541728 * B
    var s = L - 0.0894841775 * A - 1.2914855480 * B
    l = l * l * l; m = m * m * m; s = s * s * s
    return {
        r: 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        g: -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        b: -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    }
}

// Build an sRGB colour from OKLCh, reducing chroma until the result fits in
// gamut. Clamping the channels instead would silently shift the hue, which
// on a dark theme (Spaceship's key colour sits at L 0.18) is exactly where
// the requested chroma is unreachable and the shift would be worst.
function fromOklch(L, C, h) {
    for (var c = C; c > 0.004; c -= 0.006) {
        var v = _oklchToRgb(L, c, h)
        if (v.r >= -0.001 && v.r <= 1.001
                && v.g >= -0.001 && v.g <= 1.001
                && v.b >= -0.001 && v.b <= 1.001)
            return Qt.rgba(clamp01(_fromLinear(v.r)),
                           clamp01(_fromLinear(v.g)),
                           clamp01(_fromLinear(v.b)), 1)
    }
    var g = _oklchToRgb(L, 0, h)
    return Qt.rgba(clamp01(_fromLinear(g.r)),
                   clamp01(_fromLinear(g.g)),
                   clamp01(_fromLinear(g.b)), 1)
}

// Shortest signed distance from a to b around the 360° wheel.
function hueDelta(a, b) {
    var d = (b - a) % 360
    if (d > 180) d -= 360
    if (d < -180) d += 360
    return d
}
function hueDistance(a, b) { return Math.abs(hueDelta(a, b)) }

// ------------------------------------------------------------- the hues
//
// Three roles carry meaning that must survive a theme change, so their hue
// is ANCHORED rather than derived: a Backspace that came out green on one
// theme would be worse than no colour at all. They are still pulled toward
// the theme's accent (a clamped rotation, so red stays red) and rebuilt at
// the theme's own lightness and chroma, which is what keeps them from
// looking imported.
var ANCHORS = {
    kill:   "#d94f3d",   // destructive: Backspace, Del
    commit: "#3fbf6a",   // go: Enter
    mod:    "#e0a33a"    // held state: Shift, Ctrl, Alt, Win, Caps
}

// How far an anchored hue is allowed to travel toward the accent. 0.25 of
// the way, never more than 22°, which is inside the band where a hue keeps
// its name: enough to sit in the theme's world, not enough to stop meaning
// "stop" or "go".
var ANCHOR_PULL = 0.25
var ANCHOR_PULL_MAX = 22

// The four arbitrary families. Nothing about navigation is inherently
// green, so these are pure theme derivation.
var FAMILY_ROLES = ["edit", "nav", "fn", "op"]

// Where the family hues are allowed to sit, in OKLCh lightness.
//
// The hue's lightness comes from the key colour so a wash moves hue and
// chroma without moving brightness (see roleHues), but that fails at the
// ends of the scale: sRGB holds almost no chroma near white or black, so on
// the Light theme, whose key colour is #ffffff, every family hue gamut-fit
// its way to pure white and the whole scheme collapsed to one fill. The
// clamp keeps the even-brightness property for the seven themes it applies
// to and rescues the two at the extremes.
var HUE_L_MIN = 0.38
var HUE_L_MAX = 0.84

// Place the family hues by farthest-point dispersion: repeatedly take the
// wheel position furthest from everything already spoken for.
//
// This replaced a rigid tetrad rotated off the accent, which read as
// tidier colour theory and was measurably worse. A square is four hues at
// a fixed 90°, so its only freedom is one phase angle, and with the accent
// plus three anchored hues to avoid there are themes where no phase fits:
// on Amethyst the best available put `kill` 12° from `edit`, which means
// Backspace and Tab were the same colour on the one scheme whose whole
// purpose is telling them apart. Dispersion has a free choice per hue, so
// it degrades gracefully instead of hitting a wall.
//
// Deterministic: a 5° sweep, first maximum wins ties.
function _disperseHues(reserved, count) {
    var chosen = []
    for (var n = 0; n < count; n++) {
        var best = 0, bestScore = -1
        for (var h = 0; h < 360; h += 5) {
            var worst = 360, i
            for (i = 0; i < reserved.length; i++)
                worst = Math.min(worst, hueDistance(h, reserved[i]))
            for (i = 0; i < chosen.length; i++)
                worst = Math.min(worst, hueDistance(h, chosen[i]))
            if (worst > bestScore) { bestScore = worst; best = h }
        }
        chosen.push(best)
    }
    return chosen
}

// Every role hue, resolved against one theme.
//
// `L` comes from the KEY COLOUR, not from the hue's own lightness: the hue
// is a tint source mixed into a keycap, so matching the keycap's lightness
// means a wash moves hue and chroma while leaving brightness alone. All the
// bands then carry the same weight and the board keeps one even tone.
//
// `C` follows the theme's accent, floored so a near-grey accent still
// yields tellable hues and capped so a neon one does not produce eleven
// competing signals.
function roleHues(key, accent) {
    var k = toOklch(key)
    var a = toOklch(accent)
    var L = Math.max(HUE_L_MIN, Math.min(HUE_L_MAX, k.L))
    var C = Math.max(0.085, Math.min(0.19, a.C * 0.95))

    var hues = {}
    var reserved = [a.h]

    for (var name in ANCHORS) {
        var anchorH = toOklch(hex(ANCHORS[name])).h
        var pull = hueDelta(anchorH, a.h) * ANCHOR_PULL
        if (pull > ANCHOR_PULL_MAX) pull = ANCHOR_PULL_MAX
        if (pull < -ANCHOR_PULL_MAX) pull = -ANCHOR_PULL_MAX
        var h = (anchorH + pull + 360) % 360
        hues[name] = h
        reserved.push(h)
    }

    // Assigned in clockwise order from the accent rather than in the order
    // dispersion happened to pick them, so a role keeps the same
    // relationship to the accent on every theme instead of hopping around
    // the wheel when the accent moves a few degrees.
    var placed = _disperseHues(reserved, FAMILY_ROLES.length)
    placed.sort(function (x, y) {
        return ((x - a.h + 360) % 360) - ((y - a.h + 360) % 360)
    })
    for (var i = 0; i < FAMILY_ROLES.length; i++)
        hues[FAMILY_ROLES[i]] = placed[i]

    var out = {}
    for (var role in hues)
        out[role] = fromOklch(L, C, hues[role])
    return out
}

// A role hue turned into legible INK on a given ground, for the scheme that
// spends colour on the legend rather than the fill. Walks the hue toward
// the ground's opposite pole until it clears 4.5:1, so it is the same
// promise the washes make, made the other way round.
function legibleInk(hue, ground, fallback) {
    var toward = relativeLuminance(ground) > 0.35 ? Qt.rgba(0, 0, 0, 1)
                                                  : Qt.rgba(1, 1, 1, 1)
    for (var a = 0; a <= 0.92; a += 0.04) {
        var c = mixToward(hue, toward, a)
        if (contrastRatio(c, ground) >= 4.5)
            return c
    }
    return fallback
}

// Text stepped back toward its own ground, but never past 4.5:1. Used for
// the one legend that is deliberately quieter than the rest.
function _dimmedInk(text, ground, strength) {
    for (var t = strength; t > 0.005; t -= 0.02) {
        var c = mixToward(text, ground, t)
        if (contrastRatio(c, ground) >= 4.5)
            return c
    }
    return text
}

// ------------------------------------------------------------- schemes

var SCHEMES = ["off", "mono", "twotone", "bands", "ink", "signal"]

var ROLES = ["alpha", "digit", "punct", "mod", "edit", "kill",
             "commit", "nav", "fn", "op", "toggle", "pill"]

// Build the {role: {fill, ink, bar}} table for one scheme and one theme.
// Returns null for "off", which every surface reads as "keep your own
// historical tint": that is what makes the default byte-identical to the
// board that shipped before this existed.
function roleMap(scheme, key, background, text, accent) {
    if (!scheme || scheme === "off" || SCHEMES.indexOf(scheme) < 0)
        return null

    var clear = Qt.rgba(0, 0, 0, 0)
    var hues = roleHues(key, accent)
    var map = {}

    function put(role, fill, ink, bar) {
        map[role] = {
            fill: fill,
            ink: ink === undefined ? text : ink,
            bar: bar === undefined ? clear : bar
        }
    }
    // A lightness step toward the page behind the keys. Direction-agnostic:
    // on a light theme it lightens, on a dark one it darkens, and either
    // way it reads as one step away from the alpha field.
    function step(t) { return mixToward(key, background, t) }
    function tint(role, strength) { return washFor(key, hues[role], strength, text) }
    function accented(strength) { return washFor(key, accent, strength, text) }

    if (scheme === "mono") {
        // No hue at all: a key's job is a lightness step. The safest scheme
        // on every theme, and the least learnable at a glance.
        put("alpha", key)
        put("digit", key)
        put("punct", step(0.30))
        put("nav", step(0.30))
        put("op", step(0.30))
        // Modifiers took a theme-accent wash here for one revision and it
        // was reversed on sight: it put a standing blue-grey on Caps and
        // both Shifts, which is a keyboard with colour on it, not a
        // monochrome one.  A modifier's THEME COLOUR IS ITS CLICK COLOUR
        // (KeyButton paints `accentColor` while it is active and
        // `keyPressedColor` while it is held, both theme-derived), so the
        // resting cap has no reason to carry it as well.
        put("mod", step(0.52))
        put("edit", step(0.52))
        put("kill", step(0.52))
        put("fn", step(0.52))
        // The one key that steps the other way, toward the ink, so the
        // commit key is the brightest thing on a monochrome board.
        put("commit", washFor(key, text, 0.20, text))
        put("toggle", accented(0.55))
        // Flat, exactly the letters' own colour.  Lifting it toward the ink
        // (tried at 0.14) greys the pill out against the board and leaves
        // the row looking faded; the accent border is what says "tappable",
        // and it does not need help from the fill.
        put("pill", key)
    } else if (scheme === "twotone") {
        // One boundary only: keys that type a character, and keys that do
        // something. The smallest change that still answers the question.
        var util = washFor(step(0.42), accent, 0.16, text)
        put("alpha", key)
        put("digit", key)
        put("punct", key)
        put("mod", util)
        put("edit", util)
        put("kill", util)
        put("nav", util)
        put("fn", util)
        put("op", util)
        put("commit", accented(0.42))
        put("toggle", accented(0.62))
        put("pill", accented(0.24))
    } else if (scheme === "bands") {
        // A hue per family, every one of them rotated off this theme's own
        // accent. The most learnable and the most ink.
        put("alpha", key)
        put("digit", step(0.20))
        put("punct", step(0.40))
        put("mod", tint("mod", 0.34))
        put("edit", tint("edit", 0.30))
        put("kill", tint("kill", 0.32))
        put("nav", tint("nav", 0.30))
        put("fn", tint("fn", 0.30))
        put("op", tint("op", 0.28))
        put("commit", tint("commit", 0.38))
        put("toggle", accented(0.62))
        // A pill is an offer to commit a word, so it borrows the commit
        // hue at about half strength: related to Enter without competing
        // with it.
        put("pill", tint("commit", 0.20))
    } else if (scheme === "ink") {
        // One flat field; the role rides on the legend colour and a
        // hairline under it. The calmest board, and the only scheme whose
        // contrast cannot be affected by the theme's key colour at all.
        put("alpha", key)
        put("digit", key)
        // Punctuation is dimmed rather than hued, so the dim has to be
        // contrast-guarded like every fill is: an ungated 0.22 put
        // Vaporwave's punctuation at 4.32:1, under the bar this file
        // promises everywhere else. The bar under it is not text and owes
        // no ratio, so it keeps the full dim.
        put("punct", key, _dimmedInk(text, key, 0.22),
            mixToward(text, key, 0.55))
        var inkRoles = ["mod", "edit", "kill", "nav", "fn", "op", "commit"]
        for (var i = 0; i < inkRoles.length; i++) {
            var r = inkRoles[i]
            var ink = legibleInk(hues[r], key, text)
            put(r, key, ink, ink)
        }
        put("toggle", accented(0.62))
        put("pill", key, legibleInk(hues["commit"], key, text))
    } else if (scheme === "signal") {
        // Flat everywhere except the three places a wrong click costs
        // something: held state, destroyed text, committed input.
        put("alpha", key)
        put("digit", key)
        put("punct", key)
        put("nav", key)
        put("fn", key)
        put("op", key)
        put("edit", key)
        put("mod", tint("mod", 0.26))
        put("kill", tint("kill", 0.30))
        put("commit", tint("commit", 0.32))
        put("toggle", accented(0.62))
        // Flat, like everything else this scheme does not warn about.
        put("pill", key)
    }

    return map
}

// The role a layout-JSON key description belongs to. The JSON says "char"
// for a letter, a digit and a bracket alike, and those are three different
// jobs, so the key itself has to be read as well as its type.
function roleForKey(kd) {
    if (!kd)
        return "alpha"
    if (kd.type === "modifier")
        return "mod"
    // A compact layer key (?123, =\<) swaps the page rather than typing;
    // it belongs with Tab and Esc.
    if (kd.type === "layer")
        return "edit"
    if (kd.type === "special") {
        switch (kd.action) {
        case "backspace":
        case "delete":
            return "kill"
        case "return":
        case "enter":
            return "commit"
        default:
            return "edit"
        }
    }
    var k = kd.key || ""
    if (k.length === 1) {
        if (k >= "0" && k <= "9")
            return "digit"
        if ((k >= "a" && k <= "z") || (k >= "A" && k <= "Z"))
            return "alpha"
    }
    return "punct"
}
