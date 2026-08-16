import QtQuick

// A small line-art icon, drawn as stroked paths rather than typeset as a
// font glyph.
//
// Same reasoning as the clear-context (rotate-ccw) button in Main.qml: any
// glyph small enough to sit on a control is at the mercy of the host font,
// and on Windows the geometric-shape and dingbat ranges are commonly
// resolved through Segoe UI Emoji, which renders in *colour* and ignores
// the `color` property outright. The snippets window used to typeset its
// pencil and cross that way. Paths render identically everywhere and take
// the ink colour they are given, which is what lets one icon sit on nine
// themes.
//
// `paths` is SVG path data in a 24x24 box (the Feather convention), so an
// icon lifted from a published set can be kept verbatim and diffed against
// upstream.
Canvas {
    id: icon

    // Ink colour. Named `ink` rather than `color` because Canvas inherits
    // Item, which has no colour of its own, and a property named `color`
    // on a drawing surface reads like a fill.
    property color ink: "#ffffff"
    property real strokeWidth: 2
    property var paths: []
    // Fraction of the item's smaller side the 24-unit box spans.
    property real boxFraction: 1.0
    // Optical correction, in viewBox units, for a composition whose ink is
    // not centred in its own 24-unit box. Feather's rotate-ccw is the case
    // this exists for: it puts the corner arrow outside the ring, which
    // leaves the ink's centre one unit left of the box's, so centring by
    // the box leaves the icon looking off-centre. Measure it from a render
    // rather than guessing, and leave it at 0 for an icon that is already
    // centred in its box, which most are.
    property real inkOffsetX: 0

    readonly property real viewBox: 24

    implicitWidth: 18
    implicitHeight: 18
    antialiasing: true

    // Every property `onPaint` reads has to invalidate, or the canvas keeps
    // rendering the old frame until some unrelated change happens to
    // repaint it. `boxFraction` was the one missed: three of the four were
    // wired, which reads as a decision rather than the oversight it was.
    onInkChanged: requestPaint()
    onPathsChanged: requestPaint()
    onStrokeWidthChanged: requestPaint()
    onBoxFractionChanged: requestPaint()
    onInkOffsetXChanged: requestPaint()

    onPaint: {
        var ctx = getContext("2d")
        ctx.reset()
        var box = Math.min(width, height) * boxFraction
        var scale = box / viewBox
        ctx.save()
        ctx.translate((width - box) / 2 + icon.inkOffsetX * scale,
                      (height - box) / 2)
        ctx.scale(scale, scale)
        ctx.strokeStyle = icon.ink
        ctx.lineWidth = icon.strokeWidth
        ctx.lineCap = "round"
        ctx.lineJoin = "round"
        for (var i = 0; i < icon.paths.length; ++i) {
            ctx.path = icon.paths[i]
            ctx.stroke()
        }
        ctx.restore()
    }
}
