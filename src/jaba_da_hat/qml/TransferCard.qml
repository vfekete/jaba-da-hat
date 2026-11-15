import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

// One file transfer inside a chat. Incoming files are never fetched until "Download" is pressed.
Pane {
    id: card
    required property var transfer
    required property var session
    padding: 10
    Material.elevation: 1

    readonly property string st: transfer ? transfer.state : ""
    // teardown can briefly null the transfer before the delegate goes away
    readonly property var t: transfer || ({ fileName: "", sizeText: "", detail: "", incoming: false, done: 0, progress: 0, canPause: false })
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
            Label { text: card.t.incoming ? "⬇" : "⬆"; font.pixelSize: 18 }
            ColumnLayout {
                spacing: 0
                Label {
                    text: card.t.fileName
                    font.bold: true
                    elide: Text.ElideMiddle
                    Layout.maximumWidth: 260
                }
                Label {
                    text: card.t.sizeText + " · " + (card.labels[card.st] || card.st)
                          + (card.t.detail ? " · " + card.t.detail : "")
                    opacity: 0.7
                    font.pixelSize: 12
                    elide: Text.ElideRight
                    Layout.maximumWidth: 260
                }
            }
        }
        ProgressBar {
            Layout.fillWidth: true
            visible: card.st === "active" || card.st === "paused" || (card.st === "failed" && card.t.done > 0)
            value: card.t.progress
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
                visible: card.st === "active" && card.t.canPause
                onClicked: session.pauseTransfer(transfer)
            }
            Button {
                text: card.st === "failed" ? qsTr("Retry") : qsTr("Resume"); flat: true; highlighted: true
                visible: (card.st === "paused" || card.st === "failed") && card.t.canPause
                onClicked: session.resumeTransfer(transfer)
            }
            Button {
                text: qsTr("Cancel"); flat: true
                visible: card.st === "pending" || card.st === "active" || card.st === "paused"
                onClicked: session.cancelTransfer(transfer)
            }
            Button {
                text: qsTr("Open folder"); flat: true
                visible: card.st === "completed" && card.t.incoming
                onClicked: session.openTransferFolder(transfer)
            }
        }
    }
}
