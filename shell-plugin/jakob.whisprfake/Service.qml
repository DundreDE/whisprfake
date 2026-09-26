import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons

// whisprfake Flow Bar: a Wispr-Flow-style pill at the bottom of the screen that only exists
// while you dictate. Live waveform while recording, a travelling wave while processing.
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

  // command-mode answer popup
  property bool answerOpen: false
  property bool answerPending: false
  property string answerQuestion: ""
  property string answerText: ""

  readonly property bool shown: phase !== "idle" || errorText !== ""
  readonly property color ink: mode === "command" ? Color.accent : Color.popups.text
  readonly property color bg: Util.alpha(Color.background, 0.94)

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
      }
      if (root.phase === "idle") { root.targetLevel = 0; root.mode = "dictate" }
    } else if (ev.event === "level") {
      root.targetLevel = ev.v
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
  }

  Timer { id: errorTimer; interval: 3500; onTriggered: root.errorText = "" }

  // 60 fps animation clock
  property real t: 0
  FrameAnimation {
    running: panel.visible || answerPanel.visible
    onTriggered: {
      root.t += frameTime
      root.level += (root.targetLevel - root.level) * Math.min(1, frameTime * 18)
      root.targetLevel *= Math.pow(0.02, frameTime)   // decay if no new level arrives
      if (root.phase === "recording") root.elapsed = (Date.now() - root.recordStart) / 1000
    }
  }

  PanelWindow {
    id: panel
    visible: root.shown || pill.opacity > 0.01
    anchors { bottom: true; left: true; right: true }
    implicitHeight: 120
    color: "transparent"
    WlrLayershell.namespace: "whisprfake-flowbar"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    exclusionMode: ExclusionMode.Ignore
    mask: Region { item: pill }

    Rectangle {
      id: pill
      readonly property int h: 38
      readonly property int w: root.errorText !== "" ? errLabel.implicitWidth + 36
                              : root.phase === "processing" ? 96
                              : root.locked ? 262 : 150
      width: w
      height: h
      radius: h / 2
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.bottom: parent.bottom
      anchors.bottomMargin: 28
      color: root.bg
      border.width: 1
      border.color: Util.alpha(root.ink, root.mode === "command" ? 0.7 : 0.18)

      opacity: root.shown ? 1 : 0
      scale: root.shown ? 1 : 0.6
      transformOrigin: Item.Bottom
      Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
      Behavior on scale { NumberAnimation { duration: 260; easing.type: Easing.OutBack; easing.overshoot: 1.6 } }
      Behavior on width { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
      Behavior on border.color { ColorAnimation { duration: 200 } }

      // soft glow that breathes with the voice
      Rectangle {
        anchors.centerIn: parent
        width: parent.width + 10 + root.level * 14
        height: parent.height + 10 + root.level * 14
        radius: height / 2
        z: -1
        color: "transparent"
        border.width: 2
        border.color: Util.alpha(root.ink, 0.10 + root.level * 0.25)
        visible: root.phase === "recording"
      }

      // ---- waveform (recording) ---------------------------------------------
      Row {
        id: wave
        anchors.centerIn: parent
        anchors.horizontalCenterOffset: root.locked ? -46 : 0
        spacing: 3
        visible: opacity > 0
        opacity: root.phase === "recording" && root.errorText === "" ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 140 } }
        Repeater {
          model: 17
          Rectangle {
            required property int index
            readonly property real center: 1 - Math.abs(index - 8) / 9          // taller in the middle
            readonly property real wobble: 0.55 + 0.45 * Math.sin(root.t * 9 + index * 0.9)
            readonly property real amp: Math.max(0.10, Math.min(1, root.level * 1.35)) * center * wobble
            width: 3
            height: Math.max(3, amp * (pill.h - 14))
            radius: 1.5
            anchors.verticalCenter: parent.verticalCenter
            color: root.ink
            opacity: 0.55 + 0.45 * center
          }
        }
      }

      // ---- processing: travelling dots -------------------------------------
      Row {
        anchors.centerIn: parent
        spacing: 7
        opacity: root.phase === "processing" && root.errorText === "" ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: 140 } }
        Repeater {
          model: 5
          Rectangle {
            required property int index
            readonly property real s: 0.5 + 0.5 * Math.sin(root.t * 7 - index * 0.8)
            width: 5; height: 5; radius: 2.5
            anchors.verticalCenter: parent.verticalCenter
            anchors.verticalCenterOffset: -s * 5
            color: root.ink
            opacity: 0.35 + 0.65 * s
          }
        }
      }

      // ---- hands-free controls ----------------------------------------------
      Row {
        anchors.right: parent.right
        anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        spacing: 6
        visible: root.locked && root.phase === "recording" && root.errorText === ""

        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: Math.floor(root.elapsed / 60) + ":" + ("0" + Math.floor(root.elapsed % 60)).slice(-2)
          color: Util.alpha(root.ink, 0.8)
          font.family: Style.fontFamily
          font.pixelSize: 12
        }
        Rectangle { // stop
          width: 24; height: 24; radius: 12
          color: stopMa.containsMouse ? Util.alpha(root.ink, 0.25) : Util.alpha(root.ink, 0.12)
          Rectangle { anchors.centerIn: parent; width: 8; height: 8; radius: 2; color: Color.urgent }
          MouseArea { id: stopMa; anchors.fill: parent; hoverEnabled: true; onClicked: root.send("stop") }
        }
        Rectangle { // cancel
          width: 24; height: 24; radius: 12
          color: cancelMa.containsMouse ? Util.alpha(root.ink, 0.25) : Util.alpha(root.ink, 0.12)
          Text { anchors.centerIn: parent; text: "✕"; color: root.ink; font.pixelSize: 11 }
          MouseArea { id: cancelMa; anchors.fill: parent; hoverEnabled: true; onClicked: root.send("cancel") }
        }
      }

      // ---- command-mode tag -------------------------------------------------
      Text {
        visible: root.mode === "command" && root.phase === "recording" && !root.locked
        anchors.left: parent.left
        anchors.leftMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        text: "⌘"
        color: Color.accent
        font.pixelSize: 13
      }

      // ---- error ----------------------------------------------------------------
      Text {
        id: errLabel
        anchors.centerIn: parent
        visible: root.errorText !== ""
        text: root.errorText
        color: Color.urgent
        font.family: Style.fontFamily
        font.pixelSize: 12
      }
    }
  }

  Timer { id: insertLater; interval: 180; onTriggered: root.send("answer.insert") }

  PanelWindow {
    id: answerPanel
    visible: root.answerOpen
    anchors { bottom: true; left: true; right: true }
    implicitHeight: 560
    color: "transparent"
    WlrLayershell.namespace: "whisprfake-answer"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.OnDemand
    exclusionMode: ExclusionMode.Ignore
    mask: Region { item: card }

    Rectangle {
      id: card
      width: 600
      height: Math.min(480, col.implicitHeight + 32)
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.bottom: parent.bottom
      anchors.bottomMargin: 84
      radius: 14
      color: Util.alpha(Color.background, 0.97)
      border.width: 1
      border.color: Util.alpha(Color.accent, 0.6)
      focus: true
      Keys.onEscapePressed: root.answerOpen = false

      Column {
        id: col
        x: 18; y: 16
        width: parent.width - 36
        spacing: 10

        Text {
          width: parent.width
          text: root.answerQuestion
          color: Util.alpha(Color.popups.text, 0.6)
          font.family: Style.fontFamily
          font.pixelSize: 12
          wrapMode: Text.Wrap
          maximumLineCount: 2
          elide: Text.ElideRight
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
            color: Color.popups.text
            font.pixelSize: 14
            wrapMode: Text.Wrap
            opacity: root.answerPending ? 0.5 + 0.5 * Math.sin(root.t * 5) : 1
          }
        }

        Row {
          spacing: 8
          anchors.right: parent.right
          visible: !root.answerPending
          Repeater {
            model: [{ label: "Einfügen", act: "insert" }, { label: "Kopieren", act: "copy" }, { label: "Schließen", act: "close" }]
            Rectangle {
              required property var modelData
              width: lbl.implicitWidth + 22; height: 28; radius: 8
              color: ma.containsMouse ? Util.alpha(Color.accent, 0.35) : Util.alpha(Color.popups.text, 0.08)
              Text { id: lbl; anchors.centerIn: parent; text: modelData.label; color: Color.popups.text; font.pixelSize: 12 }
              MouseArea {
                id: ma; anchors.fill: parent; hoverEnabled: true
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
