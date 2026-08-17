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
 *
 * A STATIONARY CHARACTER PRESS CAN STILL BECOME A HOLD
 * ------------------------------------------------------
 * "Deferred" cannot mean "never repeats", or the character-repeat setting
 * (Main.qml's ``characterRepeat``) is silently dead the moment swipe typing
 * is on: a KeyButton only arms its own repeat timer from ``_activate()``,
 * which the deferred path never calls. ``charHoldTimer`` bridges the gap by
 * watching a character press that has not yet moved: it is armed on press
 * for ``repeatArmDelay`` (read off the hit KeyButton, not re-derived here,
 * so the overlay automatically matches whatever floor Main.qml gave that
 * key - see KeyButton's ``repeatArmFloorMs``), and cancelled by anything
 * that makes the gesture stop being stationary: leaving the key, or moving
 * far enough to promote to a swipe. If it fires uninterrupted, the gesture
 * is promoted into the same "held" state a special key gets on press
 * (``_gestureIsHold`` / ``_heldKey``), via ``externalHoldPress`` rather than
 * ``externalPress`` since the warm-up wait has already been paid; from
 * there the ordinary held-key release path takes over and the release must
 * NOT also fire a tap.
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

    // A character-key press waiting to find out whether it is a hold: set on
    // press when the key opts into repeat (see the header note above),
    // cleared the moment the wait ends one way or another (charHoldTimer
    // fires, the pointer leaves the key, the gesture promotes to a swipe, or
    // the press is released/cancelled first). Coordinates are stashed in
    // overlay-local space at press time because charHoldTimer fires well
    // after the mouse event that started it is gone.
    property var _pendingHoldKey: null
    property real _pendingHoldX: 0
    property real _pendingHoldY: 0

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

    // Promotes a stationary character-key press into a key hold. Armed on
    // press with its interval read straight off the hit KeyButton's
    // ``repeatArmDelay`` (see the header note), so it always matches that
    // key's own warm-up, floor included. Non-repeating: a hold either fires
    // once and hands off to the ordinary held-key machinery, or is cancelled
    // before it fires and never runs again for that gesture.
    Timer {
        id: charHoldTimer
        interval: 800
        repeat: false
        onTriggered: {
            var item = swipeRoot._pendingHoldKey
            var px = swipeRoot._pendingHoldX
            var py = swipeRoot._pendingHoldY
            swipeRoot._pendingHoldKey = null
            if (!item || !item.externalHoldPress) return
            var local = item.mapFromItem(swipeRoot, px, py)
            swipeRoot._gestureIsHold = true
            if (item.externalHoldPress(local.x, local.y))
                swipeRoot._heldKey = item
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
            swipeRoot._cancelPendingHold()
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
            } else if (hit && hit.item && hit.item.enableRepeat
                       && swipeRoot._isSwipeStart(mouse.x, mouse.y)) {
                // A character key that opts into repeat: might still turn
                // into a swipe, so it cannot activate yet, but if the
                // pointer stays put for repeatArmDelay it becomes a hold
                // instead. See the header note on why this cannot be
                // "activate on press" the way a special key is.
                swipeRoot._pendingHoldKey = hit.item
                swipeRoot._pendingHoldX = mouse.x
                swipeRoot._pendingHoldY = mouse.y
                charHoldTimer.interval = hit.item.repeatArmDelay
                charHoldTimer.restart()
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

            // A hold is stationary by definition, so a pending one is
            // cancelled the moment the pointer leaves the key it started on
            // - this check must run before the swipe-promotion one below,
            // since leaving the key can happen well under swipeThreshold.
            if (swipeRoot._pendingHoldKey
                && !swipeRoot._isOver(swipeRoot._pendingHoldKey, mouse.x, mouse.y)) {
                swipeRoot._cancelPendingHold()
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
                    // Movement enough to read as a swipe also cancels a
                    // pending hold even if it never left the starting key
                    // (e.g. a diagonal drag that re-crosses it).
                    swipeRoot._cancelPendingHold()
                }
            }
            if (swipeRoot._isSwipe) {
                trail.pts = swipeRoot._points
                trail.requestPaint()
            }
        }

        onReleased: function(mouse) {
            // Always, whether or not the wait ever fired: a release before
            // charHoldTimer triggers must stay an ordinary tap (exactly one
            // character on release, via the fall-through below), and the
            // timer must not go on to fire against a gesture that already
            // ended.
            swipeRoot._cancelPendingHold()
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
            swipeRoot._cancelPendingHold()
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

    // Stops the wait for a pending character hold without touching it -
    // there is nothing to release, since externalHoldPress was never called.
    function _cancelPendingHold() {
        charHoldTimer.stop()
        swipeRoot._pendingHoldKey = null
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
