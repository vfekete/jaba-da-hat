import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material as Mat  // namespaced: a plain import would shadow the Jdh style's controls
import QtQuick.Layouts

ApplicationWindow {
    id: win
    required property var app

    width: 460
    height: 480
    minimumWidth: 360
    minimumHeight: 300
    title: qsTr("Notifications — %1").arg(app.appName)
    Mat.Material.theme: app.darkTheme ? Mat.Material.Dark : Mat.Material.Light
    font: app.uiFont

    Zoom { app: win.app }
    Mat.Material.accent: Mat.Material.DeepOrange
    Mat.Material.primary: "#c4451c"

    header: ToolBar {
        Mat.Material.foreground: "white"
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 12
            Label { text: qsTr("Notifications"); font.bold: true; font.pixelSize: Math.round(16 * app.uiScale); Layout.fillWidth: true }
            ToolButton { text: qsTr("Clear messages"); onClicked: app.clearNotifications() }
        }
    }

    Label {
        anchors.centerIn: parent
        visible: list.count === 0
        text: qsTr("Nothing new")
        opacity: 0.6
    }

    ListView {
        id: list
        anchors.fill: parent
        anchors.margins: 8
        spacing: 8
        clip: true
        model: app.notificationModel
        ScrollBar.vertical: ScrollBar {}
        delegate: Pane {
            id: item
            required property string nid
            required property string kind
            required property string jid
            required property string text
            required property string time
            width: ListView.view.width
            Mat.Material.elevation: 1
            ColumnLayout {
                anchors.fill: parent
                RowLayout {
                    Label {
                        text: item.kind === "subscription" ? qsTr("Authorization request")
                            : item.kind === "error" ? qsTr("Problem") : qsTr("Information")
                        font.bold: true
                        color: item.kind === "error" ? Mat.Material.color(Mat.Material.Red) : Mat.Material.foreground
                        Layout.fillWidth: true
                    }
                    Label { text: item.time; opacity: 0.6; font.pixelSize: Math.round(12 * app.uiScale) }
                }
                Label { text: item.text; wrapMode: Text.Wrap; Layout.fillWidth: true }
                RowLayout {
                    Item { Layout.fillWidth: true }
                    Button { visible: item.kind === "subscription"; flat: true; highlighted: true; text: qsTr("Accept"); onClicked: app.acceptSubscription(item.nid) }
                    Button { visible: item.kind === "subscription"; flat: true; text: qsTr("Decline"); onClicked: app.declineSubscription(item.nid, false) }
                    Button { visible: item.kind === "subscription"; flat: true; text: qsTr("Decline && block"); onClicked: app.declineSubscription(item.nid, true) }
                    Button { visible: item.kind !== "subscription"; flat: true; text: qsTr("Dismiss"); onClicked: app.dismissNotification(item.nid) }
                }
            }
        }
    }
}
