import QtQuick
import QtQuick.Effects
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons

// whisprfake Flow Bar: a Wispr-Flow-style pill at the bottom of the screen that only exists while you
// dictate. It grows out of a dot, the waveform breathes with your voice, turns into a travelling wave
// while the text is being cleaned up, flashes a check mark when the text lands and shrinks away.
Item {
  id: root

  property var shell: null

  // ---- state from the daemon ------------------------------------------------
  property string phase: "idle"      // idle | recording | processing
  property string mode: "dictate"    // dictate | command
  property bool locked: false        // hands-free
  property real level: 0             // 0..1, smoothed
  property real targetLevel: 0
  property real recordStart: 0
  property real elapsed: 0
  property string errorText: ""
  property bool meetingRecording: false
  property bool doneFlash: false

  // command-mode answer popup
  property bool answerOpen: false
  property bool answerPending: false
  property string answerQuestion: ""
  property string answerText: ""

  readonly property bool shown: phase !== "idle" || errorText !== "" || doneFlash
  readonly property bool command: mode === "command"
  readonly property color accent: Color.accent
  readonly property color ink: Color.popups.text
  readonly property color surface: Util.alpha(Qt.darker(Color.background, 1.15), 0.96)

  function handle(line) {
    var ev
    try { ev = JSON.parse(line) } catch (e) { return }
    if (ev.event === "state") {
      var was = root.phase
      root.phase = ev.state === "recording" ? "recording" : (ev.state === "processing" ? "processing" : "idle")
      root.mode = ev.mode || root.mode
      root.locked = !!ev.locked
      if (root.phase === "recording" && was !== "recording") {
        root.recordStart = Date.now() - (ev.elapsed || 0) * 1000
        root.errorText = ""
        root.doneFlash = false
      }
      if (root.phase === "idle") { root.targetLevel = 0; Qt.callLater(function() { if (!root.doneFlash) root.mode = "dictate" }) }
    } else if (ev.event === "level") {
      root.targetLevel = ev.v
    } else if (ev.event === "inserted") {
      root.doneFlash = true
      doneTimer.restart()
    } else if (ev.event === "meeting_changed") {
      root.meetingRecording = !!ev.recording
    } else if (ev.event === "answer") {
      root.answerQuestion = ev.question || ""
      root.answerText = ev.text || ""
      root.answerPending = !!ev.pending
      root.answerOpen = true
    } else if (ev.event === "error") {
      root.errorText = ev.message || "Fehler"
      errorTimer.restart()
    }
  }

  property bool connected: sockLoader.item ? sockLoader.item.connected : false

  function send(method) {
    var s = sockLoader.item
    if (s && s.connected) { s.write(JSON.stringify({ id: 1, method: method }) + "\n"); s.flush() }
  }

  // The socket is recreated on every retry: a Quickshell Socket that failed once doesn't reconnect reliably.
  Loader {
    id: sockLoader
    active: true
    sourceComponent: Socket {
      path: Quickshell.env("XDG_RUNTIME_DIR") + "/whisprfake.sock"
      connected: true
      parser: SplitParser { onRead: data => root.handle(data) }
      onConnectedChanged: {
        if (connected) { write('{"method":"subscribe"}\n'); flush() }
        else root.phase = "idle"
      }
    }
  }

  Timer { // reconnect when the daemon (re)starts
    interval: 1500; repeat: true; running: !root.connected
    onTriggered: { sockLoader.active = false; sockLoader.active = true }
  }

  IpcHandler {
    target: "whisprfake"
    function closeAnswer(): string { root.answerOpen = false; return "ok" }
    function state(): string { return root.phase + " connected=" + root.connected }
    // visual testing without speaking: demo("recording" | "processing" | "done" | "handsfree" | "command" | "idle")
    function demo(what: string): string {
      root.errorText = ""
      root.doneFlash = what === "done"
      root.locked = what === "handsfree"
      root.mode = what === "command" ? "command" : "dictate"
      root.phase = (what === "processing") ? "processing" : (what === "idle" || what === "done") ? "idle" : "recording"
      root.recordStart = Date.now() - 83000
      root.demoLevel = what === "idle" ? 0 : 1
      return "ok"
    }
  }
  property real demoLevel: 0

  Timer { id: errorTimer; interval: 3500; onTriggered: root.errorText = "" }
  Timer { id: doneTimer; interval: 650; onTriggered: root.doneFlash = false }

  // ---- animation clock + per-bar spring physics ------------------------------
  readonly property int nBars: 24
  property var bars: []
  property real t: 0
  FrameAnimation {
    running: panel.visible || answerPanel.visible
    onTriggered: {
      var dt = Math.min(frameTime, 0.05)
      root.t += dt
      if (root.demoLevel > 0) root.targetLevel = 0.35 + 0.35 * Math.abs(Math.sin(root.t * 2.3)) * Math.abs(Math.sin(root.t * 0.9))
      root.level += (root.targetLevel - root.level) * Math.min(1, dt * 16)
      if (root.demoLevel === 0) root.targetLevel *= Math.pow(0.03, dt)
      if (root.phase === "recording") root.elapsed = (Date.now() - root.recordStart) / 1000
      var next = []
      var n = root.nBars
      for (var i = 0; i < n; i++) {
        var x = (i - (n - 1) / 2) / ((n - 1) / 2)             // -1 .. 1
        var env = Math.exp(-x * x * 2.2)                      // bell: taller in the middle
        var target
        if (root.phase === "processing") {
          target = 0.18 + 0.32 * Math.pow(0.5 + 0.5 * Math.sin(root.t * 6.5 - i * 0.55), 2)
        } else {
          var wobble = 0.6 + 0.4 * Math.sin(root.t * (7 + (i % 3)) + i * 1.7) * Math.sin(root.t * 3.1 + i * 0.4)
          target = 0.07 + Math.min(1, root.level * 1.5) * env * wobble
        }
        var prev = root.bars.length === n ? root.bars[i] : 0.07
        var k = target > prev ? 22 : 9                        // fast attack, slow release
        next.push(prev + (target - prev) * Math.min(1, dt * k))
      }
      root.bars = next
    }
  }

  // ======================================================================== Flow Bar
  PanelWindow {
    id: panel
    visible: root.shown || pill.opacity > 0.01
    anchors { bottom: true; left: true; right: true }
    implicitHeight: 140
    color: "transparent"
    WlrLayershell.namespace: "whisprfake-flowbar"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    exclusionMode: ExclusionMode.Ignore
    mask: Region { item: pill }

    // soft accent glow that breathes with the voice
    Rectangle {
      id: glow
      anchors.centerIn: pill
      width: pill.width + 18
      height: pill.height + 18
      radius: height / 2
      color: root.accent
      visible: false
    }
    MultiEffect {
      source: glow
      anchors.fill: glow
      blurEnabled: true
      blur: 1.0
      blurMax: 40
      opacity: root.phase === "recording" ? (root.command ? 0.35 : 0.08) + root.level * 0.45 : 0
      Behavior on opacity { NumberAnimation { duration: 180 } }
    }

    Item {
      id: pill
      readonly property int h: 40
      readonly property int w: root.errorText !== "" ? errRow.implicitWidth + 36
                              : root.doneFlash && root.phase === "idle" ? h
                              : root.phase === "processing" ? 116
                              : root.locked ? 286
                              : root.command ? 240 : 176
      width: root.shown ? w : 14
      height: root.shown ? h : 14
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.bottom: parent.bottom
      anchors.bottomMargin: 30 + (root.shown ? 0 : 13)
      opacity: root.shown ? 1 : 0
      scale: root.shown ? 1 : 0.6
      Behavior on width { NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 1.2 } }
      Behavior on height { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
      Behavior on opacity { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
      Behavior on scale { NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 1.8 } }
      Behavior on anchors.bottomMargin { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }

      // body + drop shadow
      Rectangle {
        id: body
        anchors.fill: parent
        radius: height / 2
        color: root.surface
        border.width: 1
        border.color: root.command ? Util.alpha(root.accent, 0.8) : Util.alpha(root.ink, 0.14)
        Behavior on border.color { ColorAnimation { duration: 200 } }
        visible: false
      }
      MultiEffect {
        source: body
        anchors.fill: body
        shadowEnabled: true
        shadowColor: "#000000"
        shadowOpacity: 0.55
        shadowBlur: 0.9
        shadowVerticalOffset: 4
      }
      // glassy top highlight
      Rectangle {
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 1 }
        height: parent.height / 2
        radius: height
        gradient: Gradient {
          GradientStop { position: 0.0; color: Util.alpha("#ffffff", 0.07) }
          GradientStop { position: 1.0; color: Util.alpha("#ffffff", 0.0) }
        }
      }

      // ---- waveform / processing wave -------------------------------------
      Item {
        id: waveBox
        anchors.verticalCenter: parent.verticalCenter
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.horizontalCenterOffset: root.locked ? -44 : (root.command ? 34 : 0)
        width: Math.min(root.nBars * 6, pill.width - (root.locked ? 130 : root.command ? 96 : 32))
        height: pill.height - 12
        clip: true
        opacity: (root.phase === "recording" || root.phase === "processing") && root.errorText === "" && !root.doneFlash ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 150 } }
        Behavior on anchors.horizontalCenterOffset { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }

        Row {
          anchors.centerIn: parent
          spacing: 3
          Repeater {
            model: root.nBars
            Rectangle {
              required property int index
              readonly property real x01: Math.abs(index - (root.nBars - 1) / 2) / ((root.nBars - 1) / 2)
              readonly property real v: root.bars.length > index ? root.bars[index] : 0.07
              width: 3
              height: Math.max(3, v * waveBox.height)
              radius: 1.5
              anchors.verticalCenter: parent.verticalCenter
              // accent in the middle fading to the text colour at the edges
              color: root.command ? root.accent : Qt.tint(root.ink, Util.alpha(root.accent, Math.max(0, 0.85 - x01 * 1.3)))
              opacity: root.phase === "processing" ? 0.55 + 0.45 * v * 2 : 0.45 + 0.55 * (1 - x01 * 0.6)
            }
          }
        }
      }

      // ---- command chip ---------------------------------------------------
      Rectangle {
        visible: root.command && root.phase === "recording" && !root.locked && root.errorText === ""
        anchors.left: parent.left
        anchors.leftMargin: 7
        anchors.verticalCenter: parent.verticalCenter
        height: 26
        width: cmdLabel.implicitWidth + 16
        radius: 13
        color: Util.alpha(root.accent, 0.18)
        Text {
          id: cmdLabel
          anchors.centerIn: parent
          text: "Befehl"
          color: root.accent
          font.family: Style.fontFamily
          font.pixelSize: 11
          font.bold: true
        }
      }

      // ---- hands-free controls ----------------------------------------------
      Row {
        anchors.right: parent.right
        anchors.rightMargin: 7
        anchors.verticalCenter: parent.verticalCenter
        spacing: 6
        visible: root.locked && root.phase === "recording" && root.errorText === ""

        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: Math.floor(root.elapsed / 60) + ":" + ("0" + Math.floor(root.elapsed % 60)).slice(-2)
          color: Util.alpha(root.ink, 0.75)
          font.family: Style.fontFamily
          font.pixelSize: 12
          font.features: { "tnum": 1 }
          width: 34
          horizontalAlignment: Text.AlignRight
        }
        Rectangle { // stop = insert
          width: 26; height: 26; radius: 13
          color: stopMa.containsMouse ? Util.alpha(root.accent, 0.35) : Util.alpha(root.accent, 0.18)
          Behavior on color { ColorAnimation { duration: 120 } }
          Rectangle { anchors.centerIn: parent; width: 9; height: 9; radius: 2.5; color: root.accent }
          MouseArea { id: stopMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.send("stop") }
        }
        Rectangle { // cancel
          width: 26; height: 26; radius: 13
          color: cancelMa.containsMouse ? Util.alpha(root.ink, 0.22) : Util.alpha(root.ink, 0.09)
          Behavior on color { ColorAnimation { duration: 120 } }
          Text { anchors.centerIn: parent; text: "✕"; color: root.ink; font.pixelSize: 11 }
          MouseArea { id: cancelMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.send("cancel") }
        }
      }

      // ---- done: check mark -------------------------------------------------
      Text {
        anchors.centerIn: parent
        text: "✓"
        color: root.accent
        font.pixelSize: 18
        font.bold: true
        opacity: root.doneFlash && root.phase === "idle" && root.errorText === "" ? 1 : 0
        scale: opacity > 0.5 ? 1 : 0.4
        Behavior on opacity { NumberAnimation { duration: 140 } }
        Behavior on scale { NumberAnimation { duration: 260; easing.type: Easing.OutBack; easing.overshoot: 2.5 } }
      }

      // ---- error --------------------------------------------------------------
      Row {
        id: errRow
        anchors.centerIn: parent
        spacing: 8
        visible: root.errorText !== ""
        Text { text: "⚠"; color: Color.urgent; font.pixelSize: 13; anchors.verticalCenter: parent.verticalCenter }
        Text {
          text: root.errorText
          color: root.ink
          font.family: Style.fontFamily
          font.pixelSize: 12
          anchors.verticalCenter: parent.verticalCenter
        }
      }
    }
  }

  // ======================================================================== Answer popup
  Timer { id: insertLater; interval: 180; onTriggered: root.send("answer.insert") }

  PanelWindow {
    id: answerPanel
    visible: root.answerOpen || card.opacity > 0.01
    anchors { bottom: true; left: true; right: true }
    implicitHeight: 600
    color: "transparent"
    WlrLayershell.namespace: "whisprfake-answer"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: root.answerOpen ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.None
    exclusionMode: ExclusionMode.Ignore
    mask: Region { item: card }

    Rectangle {
      id: cardBody
      anchors.fill: card
      radius: 16
      color: root.surface
      border.width: 1
      border.color: Util.alpha(root.ink, 0.12)
      visible: false
    }
    MultiEffect {
      source: cardBody
      anchors.fill: cardBody
      opacity: card.opacity
      shadowEnabled: true
      shadowColor: "#000000"
      shadowOpacity: 0.6
      shadowBlur: 1.0
      shadowVerticalOffset: 6
    }

    Item {
      id: card
      width: 620
      height: Math.min(500, col.implicitHeight + 36)
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.bottom: parent.bottom
      anchors.bottomMargin: root.answerOpen ? 90 : 70
      opacity: root.answerOpen ? 1 : 0
      focus: root.answerOpen
      Keys.onEscapePressed: root.answerOpen = false
      Behavior on opacity { NumberAnimation { duration: 180 } }
      Behavior on anchors.bottomMargin { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
      Behavior on height { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

      // accent line on top
      Rectangle {
        anchors { top: parent.top; horizontalCenter: parent.horizontalCenter; topMargin: 1 }
        width: parent.width - 40
        height: 2
        radius: 1
        color: root.accent
        opacity: root.answerPending ? 0.4 + 0.6 * Math.abs(Math.sin(root.t * 3)) : 0.9
      }

      Column {
        id: col
        x: 20; y: 18
        width: parent.width - 40
        spacing: 12

        Row {
          spacing: 8
          width: parent.width
          Text { text: "✦"; color: root.accent; font.pixelSize: 14; anchors.verticalCenter: parent.verticalCenter }
          Text {
            width: parent.width - 30
            text: root.answerQuestion
            color: Util.alpha(root.ink, 0.6)
            font.family: Style.fontFamily
            font.pixelSize: 12
            elide: Text.ElideRight
            anchors.verticalCenter: parent.verticalCenter
          }
        }

        Flickable {
          width: parent.width
          height: Math.min(340, answer.implicitHeight)
          contentHeight: answer.implicitHeight
          clip: true
          Text {
            id: answer
            width: parent.width
            text: root.answerPending ? "Denke nach …" : root.answerText
            textFormat: root.answerPending ? Text.PlainText : Text.MarkdownText
            color: root.ink
            font.pixelSize: 14
            lineHeight: 1.25
            wrapMode: Text.Wrap
            opacity: root.answerPending ? 0.45 + 0.4 * Math.abs(Math.sin(root.t * 3)) : 1
          }
        }

        Row {
          spacing: 8
          anchors.right: parent.right
          visible: !root.answerPending
          Repeater {
            model: [{ label: "Einfügen", act: "insert", primary: true }, { label: "Kopieren", act: "copy", primary: false },
                    { label: "Schließen", act: "close", primary: false }]
            Rectangle {
              required property var modelData
              width: lbl.implicitWidth + 26; height: 30; radius: 15
              color: modelData.primary ? (ma.containsMouse ? Qt.lighter(root.accent, 1.15) : root.accent)
                                       : (ma.containsMouse ? Util.alpha(root.ink, 0.16) : Util.alpha(root.ink, 0.08))
              Behavior on color { ColorAnimation { duration: 120 } }
              Text {
                id: lbl
                anchors.centerIn: parent
                text: modelData.label
                color: modelData.primary ? Color.background : root.ink
                font.pixelSize: 12
                font.bold: modelData.primary
              }
              MouseArea {
                id: ma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                onClicked: {
                  root.answerOpen = false
                  if (modelData.act === "insert") insertLater.restart()
                  else if (modelData.act === "copy") root.send("answer.copy")
                }
              }
            }
          }
        }
      }
    }
  }
}
