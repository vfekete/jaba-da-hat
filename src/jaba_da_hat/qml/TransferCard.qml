import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

// One file transfer inside a chat. Incoming files are never fetched until "Download" is pressed.
Pane {
    id: card
    required property var transfer
    required property var session
    property real uiScale: 1  // follows the interface font / zoom of the window
    padding: 10
    Material.elevation: 1

    readonly property string st: transfer?.state ?? ""
    // the Transfer can be destroyed just before this delegate during teardown, hence the ?. below
    readonly property var labels: ({
        offered: qsTr("Offered — not downloaded"),
        pending: qsTr("Waiting…"),
        active: qsTr("Transferring"),
        paused: qsTr("Paused"),
        completed: qsTr("Completed"),
        cancelled: qsTr("Cancelled"),
        failed: qsTr("Failed"),
        declined: qsTr("Declined")
    })

    ColumnLayout {
        anchors.fill: parent
        spacing: 4
        RowLayout {
            spacing: 8
            Label { text: (card.transfer?.incoming ?? false) ? "⬇" : "⬆"; font.pixelSize: Math.round(18 * card.uiScale) }
            ColumnLayout {
                spacing: 0
                Label {
                    text: (card.transfer?.fileName ?? "")
                    font.bold: true
                    elide: Text.ElideMiddle
                    Layout.maximumWidth: 260
                }
                Label {
                    text: (card.transfer?.sizeText ?? "") + " · " + (card.labels[card.st] || card.st)
                          + ((card.transfer?.detail ?? "") ? " · " + (card.transfer?.detail ?? "") : "")
                    opacity: 0.7
                    font.pixelSize: Math.round(12 * card.uiScale)
                    elide: Text.ElideRight
                    Layout.maximumWidth: 260
                }
            }
        }
        ProgressBar {
            Layout.fillWidth: true
            visible: card.st === "active" || card.st === "paused" || (card.st === "failed" && (card.transfer?.done ?? 0) > 0)
            value: (card.transfer?.progress ?? 0)
        }
        RowLayout {
            spacing: 4
            Button {
                text: qsTr("Download"); flat: true; highlighted: true
                visible: card.st === "offered"
                onClicked: session.acceptTransfer(transfer)
            }
            Button {
                text: qsTr("Decline"); flat: true
                visible: card.st === "offered"
                onClicked: session.declineTransfer(transfer)
            }
            Button {
                text: qsTr("Pause"); flat: true
                visible: card.st === "active" && (card.transfer?.canPause ?? false)
                onClicked: session.pauseTransfer(transfer)
            }
            Button {
                text: card.st === "failed" ? qsTr("Retry") : qsTr("Resume"); flat: true; highlighted: true
                visible: (card.st === "paused" || card.st === "failed") && (card.transfer?.canPause ?? false)
                onClicked: session.resumeTransfer(transfer)
            }
            Button {
                text: qsTr("Cancel"); flat: true
                visible: card.st === "pending" || card.st === "active" || card.st === "paused"
                onClicked: session.cancelTransfer(transfer)
            }
            Button {
                text: qsTr("Open folder"); flat: true
                visible: card.st === "completed" && (card.transfer?.incoming ?? false)
                onClicked: session.openTransferFolder(transfer)
            }
        }
    }
}
