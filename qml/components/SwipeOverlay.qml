import QtQuick 2.15

/*
 * SwipeOverlay
 * ============
 * Transparent input layer that sits on top of the main keyboard rows.
 * When `enabled` (driven by the user's swipe-typing setting), it
 * intercepts all mouse activity in the keyboard area and decides per
 * gesture whether it was a tap or a swipe:
 *
 *   • Tap   → distance < swipeThreshold → look up the KeyButton at the
 *             release point and call its keyPressed signal directly
 *             (delayed activation — fires on release, not press).
 *   • Swipe → distance ≥ swipeThreshold → forward the entire path to
 *             the Python bridge for shape-matching against the dictionary.
 *
 * When `enabled` is false the MouseArea is invisible to events
 * (visible:false → no hit testing) so KeyButtons handle their own
 * presses normally.
 *
 * TWO REGISTRIES, ON PURPOSE
 * --------------------------
 * `keyRegistry` is the swipe recogniser's key-centre map and holds single
 * character keys only: a "backspace" centre would be a phantom letter in
 * every SHARK²-style shape match and would corrupt decoding for real words.
 * `tapRegistry` holds *every* key under the overlay and exists only for
 * hit-testing presses.
 *
 * They used to be one list, and the tap fall-through resolved through the
 * char-only one, so under the overlay Backspace, Enter, Tab, the arrows,
 * the modifiers, ?123 and the Number Row's Esc all hit-tested against a
 * list that structurally could not contain them, and were silently
 * swallowed. Turning swipe on took away the one key an imprecise typist
 * needs most, with nothing on screen to say why. Widening `keyRegistry` is
 * the tempting wrong fix; splitting the two consumers is the right one.
 *
 * SPECIAL KEYS PRESS, THEY DO NOT TAP
 * -----------------------------------
 * A gesture starting on a non-character key can never become a legitimate
 * swipe (the recogniser pre-filters candidates by start key, and only
 * character keys are ever swipe starts). So a press over a special key
 * activates it *immediately* and holds it, which is what restores
 * hold-to-repeat on Backspace and the arrows. Restoring only the tap would
 * have left "hold to delete a word" broken, which for a mouse-driven OSK is
 * most of the value of Backspace.
 *
 * Character keys keep the deferred, activate-on-release behaviour, because
 * for them the press genuinely is ambiguous until the gesture ends.
 */

Item {
    id: swipeRoot
    // Geometry (x / y / width / height) is set by the parent — see
    // Main.qml's swipeOverlay block, which positions us over mainKeyboard
    // without going through anchors-on-a-layout-child.
    visible: enabled        // hides AND disables hit testing when off
    z: 50                   // above KeyButtons but below dialogs/popups

    property bool enabled: false
    property real swipeThreshold: 60   // pixels — below this, treat as tap
    property var keyboardBridge: null   // injected by Main.qml
    // Swipe key centres: single character keys ONLY. See the header.
    property var keyRegistry: []        // [{ item, kd }] — populated by Main.qml
    // Hit-testing: every key under the overlay, specials included.
    property var tapRegistry: []        // [{ item, kd }], populated by Main.qml

    // Recorded points for the current gesture, in overlay-local coords.
    property var _points: []
    property bool _isSwipe: false
    // The special key currently held by this gesture, if any.
    property var _heldKey: null
    // True for the whole of a gesture that began on a special key, and NOT
    // cleared when the pointer drags off that key. `_heldKey` alone is not
    // enough: dragging off releases the key and nulls it, and without this
    // flag the rest of the gesture would fall back into the ordinary
    // press-drag-release logic, so sliding off Backspace onto "g" and
    // releasing would type a "g", and sliding far enough would be decoded as
    // a swipe that began on Backspace. Dragging off must abort, fully.
    property bool _gestureIsHold: false

    signal swipeStarted()
    signal swipeEnded()

    // Ribbon overlay — light trail of the user's swipe path
    Canvas {
        id: trail
        anchors.fill: parent
        visible: swipeRoot._isSwipe
        opacity: 0.6

        property var pts: []

        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            if (pts.length < 2) return
            ctx.lineCap = "round"
            ctx.lineJoin = "round"
            ctx.lineWidth = 4
            ctx.strokeStyle = "#4a9eff"
            ctx.beginPath()
            ctx.moveTo(pts[0].x, pts[0].y)
            for (var i = 1; i < pts.length; i++) {
                ctx.lineTo(pts[i].x, pts[i].y)
            }
            ctx.stroke()
        }
    }

    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: false
        preventStealing: true        // hold the gesture across the full swipe

        onPressed: function(mouse) {
            swipeRoot._points = [{ x: mouse.x, y: mouse.y }]
            swipeRoot._isSwipe = false
            trail.pts = swipeRoot._points
            trail.requestPaint()

            // A press that lands on a special key is a key hold, not a
            // possible swipe start, so activate it now rather than deferring
            // to release. That is what gives Backspace and the arrows their
            // auto-repeat back.
            swipeRoot._gestureIsHold = false
            var hit = swipeRoot._findKeyAt(mouse.x, mouse.y, swipeRoot.tapRegistry)
            if (hit && hit.item && !swipeRoot._isSwipeStart(mouse.x, mouse.y)
                && hit.item.externalPress) {
                var local = hit.item.mapFromItem(swipeRoot, mouse.x, mouse.y)
                // Claim the gesture even if the debounce swallowed this
                // particular press: it began on a special key either way, and
                // letting it fall through to the swipe/tap logic is what would
                // type a stray character.
                swipeRoot._gestureIsHold = true
                if (hit.item.externalPress(local.x, local.y))
                    swipeRoot._heldKey = hit.item
            }
        }

        onPositionChanged: function(mouse) {
            // A gesture that began on a special key stays a key hold for its
            // whole life, even after the pointer leaves the key. Dragging off
            // aborts, the same escape hatch KeyButton gives you without the
            // overlay: press the wrong key, slide away, nothing happens.
            if (swipeRoot._gestureIsHold) {
                if (swipeRoot._heldKey
                    && !swipeRoot._isOver(swipeRoot._heldKey, mouse.x, mouse.y))
                    swipeRoot._releaseHeld()
                return
            }

            swipeRoot._points.push({ x: mouse.x, y: mouse.y })
            // Promote to swipe once total movement exceeds the threshold.
            if (!swipeRoot._isSwipe) {
                var first = swipeRoot._points[0]
                var dx = mouse.x - first.x
                var dy = mouse.y - first.y
                if (Math.sqrt(dx * dx + dy * dy) > swipeRoot.swipeThreshold) {
                    swipeRoot._isSwipe = true
                    swipeRoot.swipeStarted()
                }
            }
            if (swipeRoot._isSwipe) {
                trail.pts = swipeRoot._points
                trail.requestPaint()
            }
        }

        onReleased: function(mouse) {
            if (swipeRoot._gestureIsHold) {
                // Either the key is still held (release it, normal case) or
                // the pointer already dragged off it (already released, and
                // this branch is what stops the release being re-read as a
                // tap on whatever now sits under the cursor).
                swipeRoot._releaseHeld()
            } else if (swipeRoot._isSwipe) {
                if (swipeRoot.keyboardBridge) {
                    var raw = swipeRoot._points.map(function(p) { return [p.x, p.y] })
                    swipeRoot.keyboardBridge.processSwipe(raw)
                }
                swipeRoot.swipeEnded()
            } else {
                // Treat as a tap. Hit-tests the FULL registry, not the swipe
                // key-centre map: resolving through the char-only list is what
                // made every special key a dead tap.
                var hit = swipeRoot._findKeyAt(mouse.x, mouse.y, swipeRoot.tapRegistry)
                if (hit && hit.item && hit.item.keyPressed) {
                    hit.item.keyPressed()
                }
            }
            swipeRoot._gestureIsHold = false
            swipeRoot._isSwipe = false
            swipeRoot._points = []
            trail.pts = []
            trail.requestPaint()
        }

        onCanceled: {
            swipeRoot._releaseHeld()
            swipeRoot._gestureIsHold = false
            swipeRoot._isSwipe = false
            swipeRoot._points = []
            trail.pts = []
            trail.requestPaint()
        }
    }

    function _isOver(item, x, y) {
        // `visible` is not decoration here. A KeyButton inside a hidden panel
        // is still constructed and still registers, so the Number Row's keys
        // sit in the registry with stale geometry whenever that panel is
        // switched off. Without this check they can claim a press meant for
        // the key actually on screen underneath.
        if (!item || !item.visible) return false
        var p = swipeRoot.mapFromItem(item, 0, 0)
        return x >= p.x && x <= p.x + item.width
            && y >= p.y && y <= p.y + item.height
    }

    // Is this point on a key the swipe recogniser could legitimately start
    // from? Only character keys are ever swipe starts, so everything else is
    // safe to activate on press.
    function _isSwipeStart(x, y) {
        return swipeRoot._findKeyAt(x, y, swipeRoot.keyRegistry) !== null
    }

    function _releaseHeld() {
        if (swipeRoot._heldKey && swipeRoot._heldKey.externalRelease)
            swipeRoot._heldKey.externalRelease()
        swipeRoot._heldKey = null
    }

    function _findKeyAt(x, y, registry) {
        var reg = registry || swipeRoot.keyRegistry
        for (var i = 0; i < reg.length; i++) {
            var entry = reg[i]
            if (!entry || !entry.item) continue
            if (swipeRoot._isOver(entry.item, x, y))
                return entry
        }
        return null
    }
}
