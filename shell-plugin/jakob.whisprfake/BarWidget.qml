import QtQuick
import Quickshell
import qs.Ui
import qs.Commons

// Status icon in the Omarchy bar: left = whisprfake menu, middle = hands-free on/off, right = Hub.
BarWidget {
  id: root
  moduleName: "jakob.whisprfake"

  readonly property var svc: bar?.shell?.firstPartyServiceFor("jakob.whisprfake")
  readonly property string phase: svc ? svc.phase : "idle"
  readonly property bool online: svc ? svc.connected : false
  readonly property bool meeting: svc ? svc.meetingRecording : false

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: !root.online ? "󰍭" : root.meeting ? "󰑊" : (root.phase === "processing" ? "󰦖" : "󰍬")
    active: root.phase === "recording" || root.meeting
    dimmed: !root.online
    slotSize: Style.bar.statusSlot
    tooltipText: !root.online ? "whisprfake: Dienst nicht erreichbar"
                 : root.meeting ? "whisprfake – Meeting wird aufgenommen (Menü › Diktat › beenden)"
                 : "whisprfake – Ctrl+Super halten zum Diktieren\nLinks: Menü · Mitte: freihändig · Rechts: Hub"
    onPressed: function(b) {
      if (!root.bar) return
      if (b === Qt.RightButton) root.bar.run("whisprfake hub")
      else if (b === Qt.MiddleButton) root.bar.run("whisprfake ctl toggle")
      else root.bar.run("omarchy-menu summon whisprfake")
    }
  }
}
