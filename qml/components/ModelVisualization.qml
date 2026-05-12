import QtQuick 2.15
import QtQuick.Layouts 1.15
import QtQuick.Controls 2.15
import QtQuick.Window 2.15

Item {
    id: vizPanel

    signal closeRequested()

    property var vizData: null
    property int currentTab: 0

    // Drill-down state. selectedWord is non-empty while the right-side
    // drill-down panel is open; wordContext holds the bridge response.
    property string selectedWord: ""
    property var wordContext: null

    // Live-context highlighting from KeyboardBridge.activeContextChanged.
    // Updated as the user types in the foreground app while the viz is
    // open; consumers (cloud + flow canvases) repaint to pulse the
    // matching node and edge.
    property string activePrevWord: ""
    property string activeCurrentWord: ""
    // Bumped on every active-context tick so the canvases can drive a
    // short pulse animation off a single rebinding (instead of needing
    // a Timer per canvas).
    property int activePulse: 0

    // Refresh data from bridge
    function refresh() {
        if (keyboard) vizData = keyboard.getVisualizationData()
    }

    function openDrillDown(word) {
        if (!keyboard || !word) return
        selectedWord = word
        wordContext = keyboard.getWordContext(word)
    }

    function closeDrillDown() {
        selectedWord = ""
        wordContext = null
    }

    Component.onCompleted: refresh()

    Connections {
        target: keyboard
        ignoreUnknownSignals: true
        function onActiveContextChanged(prev, current) {
            vizPanel.activePrevWord = prev
            vizPanel.activeCurrentWord = current
            vizPanel.activePulse = vizPanel.activePulse + 1
            // Repaint just the active highlight layers; full canvases
            // don't need to rebuild geometry.
            cloudCanvas.requestPaint()
            flowCanvas.requestPaint()
        }
    }

    Rectangle {
        anchors.fill: parent
        color: "#1a1a2e"
        radius: 10
        border.color: "#444"
        border.width: 1
        clip: true

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 0
            spacing: 0

            // -- Header / drag handle --
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 42
                color: "#16213e"
                radius: 10

                // Square off bottom corners
                Rectangle {
                    anchors.bottom: parent.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    height: 10
                    color: parent.color
                }

                MouseArea {
                    anchors.fill: parent
                    anchors.rightMargin: 40
                    cursorShape: Qt.SizeAllCursor
                    onPressed: {
                        var win = Window.window
                        if (win && win.startSystemMove) win.startSystemMove()
                    }
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 16
                    anchors.rightMargin: 8

                    Text {
                        text: "Your Language Model"
                        color: "#e0e0e0"
                        font.pixelSize: 15
                        font.weight: Font.DemiBold
                    }

                    Item { Layout.fillWidth: true }

                    // Refresh button
                    Rectangle {
                        width: 28; height: 28; radius: 14
                        color: refreshMa.containsMouse ? "#334" : "transparent"
                        Text {
                            anchors.centerIn: parent
                            text: "\u21BB"
                            color: "#aaa"
                            font.pixelSize: 16
                        }
                        MouseArea {
                            id: refreshMa
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: vizPanel.refresh()
                        }
                    }

                    // Close button
                    Rectangle {
                        width: 28; height: 28; radius: 14
                        color: closeMa.containsMouse ? "#a33" : "transparent"
                        Text {
                            anchors.centerIn: parent
                            text: "\u2715"
                            color: "#ccc"
                            font.pixelSize: 14
                        }
                        MouseArea {
                            id: closeMa
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: vizPanel.closeRequested()
                        }
                    }
                }
            }

            // -- Tab bar --
            Row {
                Layout.fillWidth: true
                Layout.preferredHeight: 38
                Layout.leftMargin: 12
                spacing: 2

                Repeater {
                    model: ["Word Cloud", "Word Flow", "Dashboard"]
                    delegate: Rectangle {
                        width: 120; height: 34
                        radius: 6
                        color: vizPanel.currentTab === index ? "#2a4a7a" : tabMa.containsMouse ? "#253050" : "#1a1a2e"
                        border.color: vizPanel.currentTab === index ? "#4a9eff" : "transparent"
                        border.width: 1

                        Text {
                            anchors.centerIn: parent
                            text: modelData
                            color: vizPanel.currentTab === index ? "#fff" : "#aaa"
                            font.pixelSize: 13
                            font.weight: vizPanel.currentTab === index ? Font.DemiBold : Font.Normal
                        }

                        MouseArea {
                            id: tabMa
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: vizPanel.currentTab = index
                        }
                    }
                }
            }

            // -- Content area --
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.margins: 8
                clip: true

                // ============ TAB 0: WORD CLOUD ============
                Canvas {
                    id: cloudCanvas
                    anchors.fill: parent
                    visible: vizPanel.currentTab === 0

                    property var circles: []

                    onPaint: {
                        var ctx = getContext("2d")
                        ctx.clearRect(0, 0, width, height)
                        for (var i = 0; i < circles.length; i++) {
                            var c = circles[i]
                            var isActive = (c.word === vizPanel.activeCurrentWord)
                                || (c.word === vizPanel.activePrevWord)
                            var isSelected = (c.word === vizPanel.selectedWord)

                            // Active-typing pulse — outer ring glow
                            if (isActive) {
                                ctx.beginPath()
                                ctx.arc(c.x, c.y, c.r + 8, 0, Math.PI * 2)
                                ctx.fillStyle = Qt.rgba(0.4, 0.8, 1.0, 0.25)
                                ctx.fill()
                            }

                            // Circle fill
                            ctx.beginPath()
                            ctx.arc(c.x, c.y, c.r, 0, Math.PI * 2)
                            ctx.fillStyle = c.color
                            ctx.fill()

                            // Border — selection > active > default.
                            if (isSelected) {
                                ctx.strokeStyle = "#ffffff"
                                ctx.lineWidth = 2.5
                            } else if (isActive) {
                                ctx.strokeStyle = "#7ec8ff"
                                ctx.lineWidth = 2
                            } else {
                                ctx.strokeStyle = Qt.lighter(c.color, 1.3)
                                ctx.lineWidth = 1
                            }
                            ctx.stroke()

                            // Word label
                            var fontSize = Math.max(9, Math.min(c.r * 0.6, 22))
                            ctx.font = "bold " + Math.round(fontSize) + "px sans-serif"
                            ctx.fillStyle = "#f0f0f0"
                            ctx.textAlign = "center"
                            ctx.textBaseline = "middle"

                            // Truncate if needed
                            var label = c.word
                            if (ctx.measureText(label).width > c.r * 1.8) {
                                while (label.length > 2 && ctx.measureText(label + "..").width > c.r * 1.8)
                                    label = label.slice(0, -1)
                                label += ".."
                            }
                            ctx.fillText(label, c.x, c.y)

                            // Count below word
                            if (c.r > 18) {
                                ctx.font = Math.max(8, fontSize * 0.6) + "px sans-serif"
                                ctx.fillStyle = Qt.rgba(1, 1, 1, 0.5)
                                ctx.fillText(c.count.toString(), c.x, c.y + fontSize * 0.7)
                            }
                        }
                    }

                    function buildCloud() {
                        if (!vizData || !vizData.words) return
                        var words = vizData.words
                        if (words.length === 0) return

                        var maxCount = words[0].count
                        var minCount = words[words.length - 1].count
                        var logMax = Math.log(maxCount + 1)
                        var logMin = Math.log(minCount + 1)
                        var cx = width / 2
                        var cy = height / 2
                        var maxR = Math.min(width, height) * 0.12
                        var minR = 14
                        var placed = []

                        // Color palette — warm to cool by frequency
                        var colors = [
                            "#ff6b6b", "#ff8e72", "#ffa94d", "#ffd43b",
                            "#69db7c", "#38d9a9", "#4dabf7", "#748ffc",
                            "#9775fa", "#da77f2", "#e599f7", "#c0a0f0"
                        ]

                        for (var i = 0; i < Math.min(words.length, 70); i++) {
                            var w = words[i]
                            var t = logMax > logMin ? (Math.log(w.count + 1) - logMin) / (logMax - logMin) : 0.5
                            var r = minR + t * (maxR - minR)
                            var colorIdx = Math.floor((1 - t) * (colors.length - 1))
                            var color = colors[Math.min(colorIdx, colors.length - 1)]

                            // Spiral placement
                            var angle = 0
                            var dist = 0
                            var step = 3
                            var x, y, collides
                            var attempts = 0
                            do {
                                x = cx + dist * Math.cos(angle)
                                y = cy + dist * Math.sin(angle)
                                angle += 0.5
                                dist += step * 0.15
                                collides = false
                                // Check bounds
                                if (x - r < 0 || x + r > width || y - r < 0 || y + r > height) {
                                    collides = true
                                } else {
                                    for (var j = 0; j < placed.length; j++) {
                                        var p = placed[j]
                                        var dx = x - p.x
                                        var dy = y - p.y
                                        if (dx * dx + dy * dy < (r + p.r + 3) * (r + p.r + 3)) {
                                            collides = true
                                            break
                                        }
                                    }
                                }
                                attempts++
                            } while (collides && attempts < 800)

                            if (!collides) {
                                placed.push({x: x, y: y, r: r, word: w.word, count: w.count, color: color})
                            }
                        }
                        circles = placed
                        requestPaint()
                    }

                    onWidthChanged: if (visible && vizData) buildCloud()
                    onHeightChanged: if (visible && vizData) buildCloud()
                    onVisibleChanged: if (visible && vizData) buildCloud()

                    MouseArea {
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: cloudHoverWord !== "" ? Qt.PointingHandCursor : Qt.ArrowCursor
                        property string cloudHoverWord: ""

                        function pickWord(mx, my) {
                            var arr = cloudCanvas.circles
                            for (var i = 0; i < arr.length; i++) {
                                var c = arr[i]
                                var dx = mx - c.x
                                var dy = my - c.y
                                if (dx * dx + dy * dy <= c.r * c.r) {
                                    return c.word
                                }
                            }
                            return ""
                        }

                        onPositionChanged: cloudHoverWord = pickWord(mouse.x, mouse.y)
                        onExited: cloudHoverWord = ""
                        onClicked: {
                            var w = pickWord(mouse.x, mouse.y)
                            if (w !== "") vizPanel.openDrillDown(w)
                        }
                    }
                }

                // ============ TAB 1: WORD FLOW ============
                Canvas {
                    id: flowCanvas
                    anchors.fill: parent
                    visible: vizPanel.currentTab === 1

                    property var nodes: []
                    property var flowEdges: []

                    onPaint: {
                        var ctx = getContext("2d")
                        ctx.clearRect(0, 0, width, height)

                        var activePrev = vizPanel.activePrevWord
                        var activeCur = vizPanel.activeCurrentWord
                        var selWord = vizPanel.selectedWord

                        // Draw edges
                        for (var i = 0; i < flowEdges.length; i++) {
                            var e = flowEdges[i]
                            var from = e.fromNode
                            var to = e.toNode
                            if (!from || !to) continue

                            var isActiveEdge = (activePrev !== "" && from.word === activePrev
                                                && activeCur !== "" && to.word === activeCur)
                            var alpha = Math.min(0.7, 0.15 + e.weight * 0.5)
                            if (isActiveEdge) {
                                ctx.strokeStyle = Qt.rgba(1.0, 0.85, 0.3, 0.95)
                                ctx.lineWidth = Math.max(2.5, e.weight * 4 + 1.5)
                            } else {
                                ctx.strokeStyle = Qt.rgba(0.4, 0.7, 1.0, alpha)
                                ctx.lineWidth = Math.max(0.5, e.weight * 3)
                            }

                            // Curved edge
                            var mx = (from.x + to.x) / 2
                            var my = (from.y + to.y) / 2
                            // Offset midpoint perpendicular to the line
                            var dx = to.x - from.x
                            var dy = to.y - from.y
                            var len = Math.sqrt(dx * dx + dy * dy)
                            if (len < 1) continue
                            var nx = -dy / len
                            var ny = dx / len
                            var curve = len * 0.15
                            var cpx = mx + nx * curve
                            var cpy = my + ny * curve

                            ctx.beginPath()
                            ctx.moveTo(from.x, from.y)
                            ctx.quadraticCurveTo(cpx, cpy, to.x, to.y)
                            ctx.stroke()

                            // Arrow head
                            var t2 = 0.85
                            var ax = (1-t2)*(1-t2)*from.x + 2*(1-t2)*t2*cpx + t2*t2*to.x
                            var ay = (1-t2)*(1-t2)*from.y + 2*(1-t2)*t2*cpy + t2*t2*to.y
                            var adx = to.x - ax
                            var ady = to.y - ay
                            var alen = Math.sqrt(adx*adx + ady*ady)
                            if (alen > 0) {
                                adx /= alen; ady /= alen
                                var arrSize = Math.max(4, e.weight * 5)
                                ctx.beginPath()
                                ctx.moveTo(ax + adx * arrSize, ay + ady * arrSize)
                                ctx.lineTo(ax - ady * arrSize * 0.5, ay + adx * arrSize * 0.5)
                                ctx.lineTo(ax + ady * arrSize * 0.5, ay - adx * arrSize * 0.5)
                                ctx.closePath()
                                ctx.fillStyle = isActiveEdge
                                    ? Qt.rgba(1.0, 0.85, 0.3, 0.95)
                                    : Qt.rgba(0.4, 0.7, 1.0, alpha)
                                ctx.fill()
                            }
                        }

                        // Draw nodes
                        for (var j = 0; j < nodes.length; j++) {
                            var n = nodes[j]
                            var isActiveNode = (n.word === activePrev) || (n.word === activeCur)
                            var isSelectedNode = (n.word === selWord)

                            // Glow — bigger + warmer for the active node.
                            var glowR = isActiveNode ? n.r + 9 : n.r + 4
                            var glowFill = isActiveNode
                                ? Qt.rgba(1.0, 0.85, 0.3, 0.35)
                                : Qt.rgba(0.3, 0.6, 1.0, 0.15)
                            ctx.beginPath()
                            ctx.arc(n.x, n.y, glowR, 0, Math.PI * 2)
                            ctx.fillStyle = glowFill
                            ctx.fill()

                            // Node circle
                            ctx.beginPath()
                            ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2)
                            ctx.fillStyle = n.color
                            ctx.fill()
                            if (isSelectedNode) {
                                ctx.strokeStyle = "#ffffff"
                                ctx.lineWidth = 2.5
                            } else if (isActiveNode) {
                                ctx.strokeStyle = "#ffd84d"
                                ctx.lineWidth = 2
                            } else {
                                ctx.strokeStyle = Qt.lighter(n.color, 1.4)
                                ctx.lineWidth = 1.5
                            }
                            ctx.stroke()

                            // Label
                            var fs = Math.max(9, Math.min(n.r * 0.55, 16))
                            ctx.font = "bold " + Math.round(fs) + "px sans-serif"
                            ctx.fillStyle = "#fff"
                            ctx.textAlign = "center"
                            ctx.textBaseline = "middle"
                            ctx.fillText(n.word, n.x, n.y)
                        }
                    }

                    function buildFlow() {
                        if (!vizData || !vizData.words || !vizData.edges) return
                        var words = vizData.words
                        var edgeData = vizData.edges
                        if (words.length === 0) return

                        // Pick top words that actually have edges
                        var edgeWords = {}
                        for (var i = 0; i < edgeData.length; i++) {
                            edgeWords[edgeData[i].from] = true
                            edgeWords[edgeData[i].to] = true
                        }

                        var nodeWords = []
                        for (var k = 0; k < words.length && nodeWords.length < 30; k++) {
                            if (edgeWords[words[k].word])
                                nodeWords.push(words[k])
                        }
                        if (nodeWords.length === 0) {
                            // Fallback: just show top words without edges
                            nodeWords = words.slice(0, 20)
                        }

                        var maxC = nodeWords[0].count
                        var cx = width / 2
                        var cy = height / 2

                        // Layout nodes in concentric circles
                        var built = []
                        var nodeMap = {}

                        // Place the most frequent word at center
                        var maxR = Math.min(width, height) * 0.06
                        var minR = 12

                        // Inner ring: top 6, outer ring: rest
                        var innerCount = Math.min(6, nodeWords.length - 1)
                        var outerCount = nodeWords.length - 1 - innerCount
                        var innerRadius = Math.min(width, height) * 0.2
                        var outerRadius = Math.min(width, height) * 0.38

                        var colors = [
                            "#e74c3c", "#e67e22", "#f1c40f", "#2ecc71",
                            "#1abc9c", "#3498db", "#9b59b6", "#e84393",
                            "#00b894", "#6c5ce7", "#fd79a8", "#74b9ff"
                        ]

                        // Center node
                        var n0 = nodeWords[0]
                        var r0 = minR + (n0.count / maxC) * (maxR - minR)
                        built.push({
                            x: cx, y: cy, r: r0,
                            word: n0.word, count: n0.count,
                            color: colors[0]
                        })
                        nodeMap[n0.word] = built[0]

                        // Inner ring
                        for (var ii = 0; ii < innerCount; ii++) {
                            var w1 = nodeWords[ii + 1]
                            var angle1 = (ii / innerCount) * Math.PI * 2 - Math.PI / 2
                            var rr1 = minR + (w1.count / maxC) * (maxR - minR)
                            var nd1 = {
                                x: cx + innerRadius * Math.cos(angle1),
                                y: cy + innerRadius * Math.sin(angle1),
                                r: rr1, word: w1.word, count: w1.count,
                                color: colors[(ii + 1) % colors.length]
                            }
                            built.push(nd1)
                            nodeMap[w1.word] = nd1
                        }

                        // Outer ring
                        for (var oi = 0; oi < outerCount; oi++) {
                            var w2 = nodeWords[oi + 1 + innerCount]
                            var angle2 = (oi / Math.max(1, outerCount)) * Math.PI * 2 - Math.PI / 2
                            var rr2 = minR + (w2.count / maxC) * (maxR - minR)
                            var nd2 = {
                                x: cx + outerRadius * Math.cos(angle2),
                                y: cy + outerRadius * Math.sin(angle2),
                                r: rr2, word: w2.word, count: w2.count,
                                color: colors[(oi + innerCount + 1) % colors.length]
                            }
                            built.push(nd2)
                            nodeMap[w2.word] = nd2
                        }

                        // Build edges with node references
                        var builtEdges = []
                        var maxEdgeCount = 1
                        for (var ei = 0; ei < edgeData.length; ei++) {
                            if (edgeData[ei].count > maxEdgeCount) maxEdgeCount = edgeData[ei].count
                        }
                        for (var ej = 0; ej < edgeData.length; ej++) {
                            var ed = edgeData[ej]
                            if (nodeMap[ed.from] && nodeMap[ed.to]) {
                                builtEdges.push({
                                    fromNode: nodeMap[ed.from],
                                    toNode: nodeMap[ed.to],
                                    weight: ed.count / maxEdgeCount
                                })
                            }
                        }

                        nodes = built
                        flowEdges = builtEdges
                        requestPaint()
                    }

                    onWidthChanged: if (visible && vizData) buildFlow()
                    onHeightChanged: if (visible && vizData) buildFlow()
                    onVisibleChanged: if (visible && vizData) buildFlow()

                    MouseArea {
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: flowHoverWord !== "" ? Qt.PointingHandCursor : Qt.ArrowCursor
                        property string flowHoverWord: ""

                        function pickNode(mx, my) {
                            var arr = flowCanvas.nodes
                            for (var i = 0; i < arr.length; i++) {
                                var n = arr[i]
                                var dx = mx - n.x
                                var dy = my - n.y
                                if (dx * dx + dy * dy <= n.r * n.r) {
                                    return n.word
                                }
                            }
                            return ""
                        }

                        onPositionChanged: flowHoverWord = pickNode(mouse.x, mouse.y)
                        onExited: flowHoverWord = ""
                        onClicked: {
                            var w = pickNode(mouse.x, mouse.y)
                            if (w !== "") vizPanel.openDrillDown(w)
                        }
                    }
                }

                // ============ TAB 2: DASHBOARD ============
                Flickable {
                    anchors.fill: parent
                    visible: vizPanel.currentTab === 2
                    contentHeight: dashCol.implicitHeight
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds

                    ColumnLayout {
                        id: dashCol
                        width: parent.width
                        spacing: 16

                        // Analytics moved here from the top of Settings —
                        // it's user-typing data, so it lives with the
                        // rest of "Your Language Model" rather than
                        // greeting the user every time they open
                        // Settings to change a config.
                        AnalyticsDashboard {
                            Layout.fillWidth: true
                        }

                        // -- Stats cards --
                        Row {
                            Layout.fillWidth: true
                            spacing: 10

                            Repeater {
                                model: {
                                    if (!vizData || !vizData.stats) return []
                                    var s = vizData.stats
                                    var a = vizData.analytics || {}
                                    return [
                                        {label: "Vocabulary", value: (s.unique_words || 0).toLocaleString(), color: "#4dabf7"},
                                        {label: "Bigrams", value: (s.bigrams || 0).toLocaleString(), color: "#69db7c"},
                                        {label: "Trigrams", value: (s.trigrams || 0).toLocaleString(), color: "#ffd43b"},
                                        {label: "Top Pick", value: (a.alltimeTopPickRate || a.topPickRate || 0) + "%", color: "#ff8e72"},
                                        {label: "Saved", value: (a.keystrokesSaved || 0).toString(), color: "#da77f2"}
                                    ]
                                }
                                delegate: Rectangle {
                                    width: (dashCol.width - 40) / 5
                                    height: 70
                                    radius: 8
                                    color: "#222244"
                                    border.color: modelData.color
                                    border.width: 1

                                    Column {
                                        anchors.centerIn: parent
                                        spacing: 4
                                        Text {
                                            anchors.horizontalCenter: parent.horizontalCenter
                                            text: modelData.value
                                            color: modelData.color
                                            font.pixelSize: 22
                                            font.weight: Font.Bold
                                        }
                                        Text {
                                            anchors.horizontalCenter: parent.horizontalCenter
                                            text: modelData.label
                                            color: "#999"
                                            font.pixelSize: 11
                                        }
                                    }
                                }
                            }
                        }

                        // -- Top Words Bar Chart --
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: barChartCol.implicitHeight + 32
                            color: "#222244"
                            radius: 8

                            ColumnLayout {
                                id: barChartCol
                                anchors.fill: parent
                                anchors.margins: 14
                                spacing: 3

                                Text {
                                    text: "Top Words"
                                    color: "#ccc"
                                    font.pixelSize: 14
                                    font.weight: Font.DemiBold
                                    Layout.bottomMargin: 6
                                }

                                Repeater {
                                    model: {
                                        if (!vizData || !vizData.words) return []
                                        return vizData.words.slice(0, 20)
                                    }
                                    delegate: RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 8

                                        Text {
                                            Layout.preferredWidth: 80
                                            text: modelData.word
                                            color: "#ddd"
                                            font.pixelSize: 12
                                            elide: Text.ElideRight
                                            horizontalAlignment: Text.AlignRight
                                        }

                                        Rectangle {
                                            Layout.fillWidth: true
                                            height: 18
                                            color: "transparent"

                                            Rectangle {
                                                height: parent.height
                                                width: {
                                                    if (!vizData || !vizData.words || vizData.words.length === 0) return 0
                                                    var maxC = vizData.words[0].count
                                                    return Math.max(2, parent.width * modelData.count / maxC)
                                                }
                                                radius: 3
                                                color: {
                                                    var colors = ["#ff6b6b", "#ff8e72", "#ffa94d", "#ffd43b", "#69db7c",
                                                                  "#38d9a9", "#4dabf7", "#748ffc", "#9775fa", "#da77f2"]
                                                    return colors[index % colors.length]
                                                }
                                                opacity: 0.85

                                                Behavior on width { NumberAnimation { duration: 300 } }
                                            }
                                        }

                                        Text {
                                            Layout.preferredWidth: 50
                                            text: modelData.count.toLocaleString()
                                            color: "#999"
                                            font.pixelSize: 11
                                        }
                                    }
                                }
                            }
                        }

                        // -- Boosted Words --
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: boostCol.implicitHeight + 28
                            color: "#222244"
                            radius: 8
                            visible: vizData && vizData.stats && vizData.stats.preferredCount > 0

                            ColumnLayout {
                                id: boostCol
                                anchors.fill: parent
                                anchors.margins: 14
                                spacing: 6

                                Text {
                                    text: "Boosted Words"
                                    color: "#ccc"
                                    font.pixelSize: 14
                                    font.weight: Font.DemiBold
                                }

                                Text {
                                    text: "Click to remove boost"
                                    color: "#777"
                                    font.pixelSize: 11
                                    Layout.bottomMargin: 2
                                }

                                Flow {
                                    Layout.fillWidth: true
                                    spacing: 6

                                    Repeater {
                                        model: (vizData && vizData.stats && vizData.stats.preferred) ? vizData.stats.preferred : []
                                        delegate: Rectangle {
                                            width: prefRow.implicitWidth + 16
                                            height: 26
                                            radius: 4
                                            color: prefMa.containsMouse ? "#1f4a2e" : "#1a2f24"
                                            border.color: prefMa.containsMouse ? "#7d7" : "#5a8"
                                            border.width: 1

                                            Row {
                                                id: prefRow
                                                anchors.centerIn: parent
                                                spacing: 4
                                                Text {
                                                    text: modelData.word + " (+" + modelData.count + ")"
                                                    color: "#9e9"
                                                    font.pixelSize: 11
                                                }
                                                Text {
                                                    text: "✕"
                                                    color: "#8c8"
                                                    font.pixelSize: 10
                                                    visible: prefMa.containsMouse
                                                }
                                            }

                                            MouseArea {
                                                id: prefMa
                                                anchors.fill: parent
                                                hoverEnabled: true
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: {
                                                    if (keyboard) keyboard.unprefer(modelData.word)
                                                    vizPanel.refresh()
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // -- Suppressed Words --
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: suppressCol.implicitHeight + 28
                            color: "#222244"
                            radius: 8
                            visible: vizData && vizData.stats &&
                                     (vizData.stats.blacklistCount > 0 || vizData.stats.dispreferenceCount > 0)

                            ColumnLayout {
                                id: suppressCol
                                anchors.fill: parent
                                anchors.margins: 14
                                spacing: 6

                                Text {
                                    text: "Suppressed Words"
                                    color: "#ccc"
                                    font.pixelSize: 14
                                    font.weight: Font.DemiBold
                                }

                                Text {
                                    text: "Click to restore"
                                    color: "#777"
                                    font.pixelSize: 11
                                    Layout.bottomMargin: 2
                                }

                                // Blacklisted
                                Text {
                                    text: "Blocked"
                                    color: "#f88"
                                    font.pixelSize: 11
                                    font.weight: Font.DemiBold
                                    visible: vizData && vizData.stats && vizData.stats.blacklist && vizData.stats.blacklist.length > 0
                                }

                                Flow {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    visible: vizData && vizData.stats && vizData.stats.blacklist && vizData.stats.blacklist.length > 0

                                    Repeater {
                                        model: (vizData && vizData.stats && vizData.stats.blacklist) ? vizData.stats.blacklist : []
                                        delegate: Rectangle {
                                            width: blkRow.implicitWidth + 16
                                            height: 26
                                            radius: 4
                                            color: blkMa.containsMouse ? "#5a2020" : "#3a2020"
                                            border.color: blkMa.containsMouse ? "#f44" : "#a33"
                                            border.width: 1

                                            Row {
                                                id: blkRow
                                                anchors.centerIn: parent
                                                spacing: 4
                                                Text {
                                                    text: modelData
                                                    color: "#f88"
                                                    font.pixelSize: 11
                                                }
                                                Text {
                                                    text: "\u2715"
                                                    color: "#f66"
                                                    font.pixelSize: 10
                                                    visible: blkMa.containsMouse
                                                }
                                            }

                                            MouseArea {
                                                id: blkMa
                                                anchors.fill: parent
                                                hoverEnabled: true
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: {
                                                    if (keyboard) keyboard.unblacklistWord(modelData)
                                                    vizPanel.refresh()
                                                }
                                            }
                                        }
                                    }
                                }

                                // Dispreferred
                                Text {
                                    text: "Downweighted"
                                    color: "#dd9"
                                    font.pixelSize: 11
                                    font.weight: Font.DemiBold
                                    visible: vizData && vizData.stats && vizData.stats.dispreference && vizData.stats.dispreference.length > 0
                                }

                                Flow {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    visible: vizData && vizData.stats && vizData.stats.dispreference && vizData.stats.dispreference.length > 0

                                    Repeater {
                                        model: (vizData && vizData.stats && vizData.stats.dispreference) ? vizData.stats.dispreference : []
                                        delegate: Rectangle {
                                            width: dispRow.implicitWidth + 16
                                            height: 26
                                            radius: 4
                                            color: dispMa.containsMouse ? "#3a3a20" : "#2a2a20"
                                            border.color: dispMa.containsMouse ? "#ee9" : "#aa7"
                                            border.width: 1

                                            Row {
                                                id: dispRow
                                                anchors.centerIn: parent
                                                spacing: 4
                                                Text {
                                                    text: modelData.word + " (" + modelData.count + ")"
                                                    color: "#dd9"
                                                    font.pixelSize: 11
                                                }
                                                Text {
                                                    text: "\u2715"
                                                    color: "#cc8"
                                                    font.pixelSize: 10
                                                    visible: dispMa.containsMouse
                                                }
                                            }

                                            MouseArea {
                                                id: dispMa
                                                anchors.fill: parent
                                                hoverEnabled: true
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: {
                                                    if (keyboard) keyboard.undisprefer(modelData.word)
                                                    vizPanel.refresh()
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // -- Bigram Connections Table --
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: bigramCol.implicitHeight + 28
                            color: "#222244"
                            radius: 8

                            ColumnLayout {
                                id: bigramCol
                                anchors.fill: parent
                                anchors.margins: 14
                                spacing: 3

                                Text {
                                    text: "Top Word Pairs"
                                    color: "#ccc"
                                    font.pixelSize: 14
                                    font.weight: Font.DemiBold
                                    Layout.bottomMargin: 6
                                }

                                Repeater {
                                    model: {
                                        if (!vizData || !vizData.edges) return []
                                        return vizData.edges.slice(0, 15)
                                    }
                                    delegate: RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 6

                                        Text {
                                            Layout.preferredWidth: 80
                                            text: modelData.from
                                            color: "#4dabf7"
                                            font.pixelSize: 12
                                            horizontalAlignment: Text.AlignRight
                                        }

                                        Text {
                                            text: "\u2192"
                                            color: "#666"
                                            font.pixelSize: 12
                                        }

                                        Text {
                                            Layout.preferredWidth: 80
                                            text: modelData.to
                                            color: "#69db7c"
                                            font.pixelSize: 12
                                        }

                                        Rectangle {
                                            Layout.fillWidth: true
                                            height: 14
                                            color: "transparent"

                                            Rectangle {
                                                height: parent.height
                                                width: {
                                                    if (!vizData || !vizData.edges || vizData.edges.length === 0) return 0
                                                    var maxE = vizData.edges[0].count
                                                    return Math.max(2, parent.width * modelData.count / maxE)
                                                }
                                                radius: 3
                                                color: "#4a9eff"
                                                opacity: 0.6
                                            }
                                        }

                                        Text {
                                            Layout.preferredWidth: 30
                                            text: modelData.count
                                            color: "#888"
                                            font.pixelSize: 11
                                        }
                                    }
                                }
                            }
                        }

                        Item { Layout.preferredHeight: 8 }
                    }
                }

                // Empty state
                Column {
                    anchors.centerIn: parent
                    spacing: 8
                    visible: !vizData || !vizData.words || vizData.words.length === 0

                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: "No model data yet"
                        color: "#888"
                        font.pixelSize: 18
                    }
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: "Start typing to build your language model"
                        color: "#666"
                        font.pixelSize: 13
                    }
                }

                // -- Drill-down side panel --
                // Slides in from the right when the user clicks a word
                // in either the cloud or flow tab. Shows top
                // successors / predecessors / trigram windows for the
                // selected word, sourced from KeyboardBridge.getWordContext.
                Rectangle {
                    id: drillDownPanel
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    anchors.right: parent.right
                    width: 260
                    color: "#16213e"
                    border.color: "#2a4a7a"
                    border.width: 1
                    radius: 8
                    visible: vizPanel.selectedWord !== "" && vizPanel.currentTab !== 2
                    z: 5

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 8

                        // Header
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 1
                                Text {
                                    text: vizPanel.wordContext
                                        ? vizPanel.wordContext.word
                                        : ""
                                    color: "#fff"
                                    font.pixelSize: 18
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: vizPanel.wordContext
                                        ? "seen " + vizPanel.wordContext.count
                                          + (vizPanel.wordContext.userCount > 0
                                             ? " — " + vizPanel.wordContext.userCount + " by you"
                                             : "")
                                        : ""
                                    color: "#9bb"
                                    font.pixelSize: 11
                                }
                            }

                            Rectangle {
                                width: 22; height: 22; radius: 11
                                color: closeDrillMa.containsMouse ? "#334" : "transparent"
                                Text {
                                    anchors.centerIn: parent
                                    text: "✕"
                                    color: "#aaa"
                                    font.pixelSize: 12
                                }
                                MouseArea {
                                    id: closeDrillMa
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: vizPanel.closeDrillDown()
                                }
                            }
                        }

                        Rectangle { Layout.fillWidth: true; height: 1; color: "#2a4a7a" }

                        Flickable {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            contentHeight: drillCol.implicitHeight
                            clip: true
                            boundsBehavior: Flickable.StopAtBounds

                            ColumnLayout {
                                id: drillCol
                                width: parent.width
                                spacing: 12

                                // Successors: word → next
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 4
                                    Text {
                                        text: "Often followed by"
                                        color: "#9bb"
                                        font.pixelSize: 11
                                        font.weight: Font.DemiBold
                                    }
                                    Repeater {
                                        model: vizPanel.wordContext
                                            ? vizPanel.wordContext.successors
                                            : []
                                        delegate: Rectangle {
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 22
                                            color: succMa.containsMouse ? "#1e2e52" : "transparent"
                                            radius: 4

                                            RowLayout {
                                                anchors.fill: parent
                                                anchors.leftMargin: 6
                                                anchors.rightMargin: 6
                                                Text {
                                                    text: modelData.word
                                                    color: "#dde"
                                                    font.pixelSize: 12
                                                    Layout.fillWidth: true
                                                    elide: Text.ElideRight
                                                }
                                                Text {
                                                    text: modelData.count
                                                    color: "#7ec8ff"
                                                    font.pixelSize: 11
                                                }
                                            }
                                            MouseArea {
                                                id: succMa
                                                anchors.fill: parent
                                                hoverEnabled: true
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: vizPanel.openDrillDown(modelData.word)
                                            }
                                        }
                                    }
                                    Text {
                                        visible: vizPanel.wordContext
                                            && vizPanel.wordContext.successors.length === 0
                                        text: "(no successors yet)"
                                        color: "#666"
                                        font.pixelSize: 11
                                    }
                                }

                                // Predecessors: prev → word
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 4
                                    Text {
                                        text: "Often preceded by"
                                        color: "#9bb"
                                        font.pixelSize: 11
                                        font.weight: Font.DemiBold
                                    }
                                    Repeater {
                                        model: vizPanel.wordContext
                                            ? vizPanel.wordContext.predecessors
                                            : []
                                        delegate: Rectangle {
                                            Layout.fillWidth: true
                                            Layout.preferredHeight: 22
                                            color: predMa.containsMouse ? "#1e2e52" : "transparent"
                                            radius: 4

                                            RowLayout {
                                                anchors.fill: parent
                                                anchors.leftMargin: 6
                                                anchors.rightMargin: 6
                                                Text {
                                                    text: modelData.word
                                                    color: "#dde"
                                                    font.pixelSize: 12
                                                    Layout.fillWidth: true
                                                    elide: Text.ElideRight
                                                }
                                                Text {
                                                    text: modelData.count
                                                    color: "#7ec8ff"
                                                    font.pixelSize: 11
                                                }
                                            }
                                            MouseArea {
                                                id: predMa
                                                anchors.fill: parent
                                                hoverEnabled: true
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: vizPanel.openDrillDown(modelData.word)
                                            }
                                        }
                                    }
                                    Text {
                                        visible: vizPanel.wordContext
                                            && vizPanel.wordContext.predecessors.length === 0
                                        text: "(no predecessors yet)"
                                        color: "#666"
                                        font.pixelSize: 11
                                    }
                                }

                                // Trigram windows: X word Y / X Y word
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 4
                                    visible: vizPanel.wordContext
                                        && vizPanel.wordContext.trigrams.length > 0
                                    Text {
                                        text: "Phrase contexts"
                                        color: "#9bb"
                                        font.pixelSize: 11
                                        font.weight: Font.DemiBold
                                    }
                                    Repeater {
                                        model: vizPanel.wordContext
                                            ? vizPanel.wordContext.trigrams
                                            : []
                                        delegate: RowLayout {
                                            Layout.fillWidth: true
                                            spacing: 4
                                            Text {
                                                text: modelData.phrase
                                                color: "#dde"
                                                font.pixelSize: 12
                                                Layout.fillWidth: true
                                                elide: Text.ElideRight
                                            }
                                            Text {
                                                text: modelData.count
                                                color: "#7ec8ff"
                                                font.pixelSize: 11
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // Rebuild visualizations when data changes
    onVizDataChanged: {
        if (vizData) {
            if (currentTab === 0) cloudCanvas.buildCloud()
            else if (currentTab === 1) flowCanvas.buildFlow()
        }
    }

    onCurrentTabChanged: {
        if (vizData) {
            if (currentTab === 0) cloudCanvas.buildCloud()
            else if (currentTab === 1) flowCanvas.buildFlow()
        }
    }
}
