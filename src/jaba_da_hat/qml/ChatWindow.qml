import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Dialogs
import QtQuick.Layouts

ApplicationWindow {
    id: win
    required property var app
    required property var session
    property bool confirmedClose: false

    width: 480
    height: 640
    minimumWidth: 340
    minimumHeight: 380
    title: session.name + " — " + app.appName
    Material.theme: app.darkTheme ? Material.Dark : Material.Light
    Material.accent: Material.DeepOrange
    Material.primary: "#c4451c"

    readonly property color inBubble: app.darkTheme ? "#3a3a3d" : "#ececec"
    readonly property color outBubble: app.darkTheme ? "#6e3420" : "#ffd8c7"
    readonly property string linkColor: app.darkTheme ? "#8cc4ff" : "#1a5fb4"
    // the HTML comes from textutil.linkify, so only our own <a> tags exist
    function styled(html) { return html.replace(/<a href=/g, '<a style="color:' + linkColor + '" href=') }

    onActiveChanged: session.setWindowActive(active)
    onClosing: (close) => {
        if (!confirmedClose && session.activeTransferCount() > 0) {
            close.accepted = false
            closeDialog.open()
            return
        }
        session.windowClosed()
    }

    header: ToolBar {
        Material.foreground: "white"
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 12
            anchors.rightMargin: 4
            spacing: 8
            PresenceDot { presence: session.presence }
            ColumnLayout {
                spacing: 0
                Layout.fillWidth: true
                Label { text: session.name; font.bold: true; font.pixelSize: 16; elide: Text.ElideRight; Layout.fillWidth: true }
                Label {
                    text: session.jid + (session.statusText ? " — " + session.statusText : "")
                    font.pixelSize: 12; opacity: 0.85; elide: Text.ElideRight; Layout.fillWidth: true
                }
            }
            ToolButton {
                id: lockBtn
                text: session.encrypted ? "🔒" : "🔓"
                font.pixelSize: 18
                checkable: true
                checked: session.encrypted
                enabled: session.canEncrypt
                onToggled: session.encrypted = checked
                ToolTip.visible: hovered
                ToolTip.text: !session.canEncrypt ? qsTr("This contact has no OMEMO devices — encryption unavailable")
                             : session.encrypted ? qsTr("OMEMO encryption on — click to turn off")
                                                 : qsTr("Not end-to-end encrypted — click to turn on OMEMO")
            }
            ToolButton {
                text: "📎"
                font.pixelSize: 18
                enabled: app.connectionState === "online"
                onClicked: fileDialog.open()
                ToolTip.visible: hovered
                ToolTip.text: qsTr("Send a file (or drop it onto this window)")
            }
        }
    }

    FileDialog {
        id: fileDialog
        title: qsTr("Send file to %1").arg(session.name)
        fileMode: FileDialog.OpenFile
        onAccepted: session.sendFile(selectedFile)
    }

    Dialog {
        id: closeDialog
        anchors.centerIn: parent
        modal: true
        title: qsTr("Transfers in progress")
        standardButtons: Dialog.Yes | Dialog.No
        Label { text: qsTr("Closing this chat cancels its running file transfers. Close anyway?"); wrapMode: Text.Wrap; width: 280 }
        onAccepted: { win.confirmedClose = true; win.close() }
    }

    DropArea {
        anchors.fill: parent
        keys: ["text/uri-list"]
        onDropped: (drop) => {
            if (app.connectionState === "online")
                for (let i = 0; i < drop.urls.length; ++i) session.sendFile(drop.urls[i])
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 6
            topMargin: 10
            bottomMargin: 10
            model: session.messages
            ScrollBar.vertical: ScrollBar {}
            onCountChanged: Qt.callLater(positionViewAtEnd)

            delegate: Item {
                id: row
                required property string direction
                required property string html
                required property string time
                required property string kind
                required property var transfer
                required property bool encrypted
                readonly property real maxW: list.width * 0.78
                width: list.width
                height: content.height + 4

                Loader {
                    id: content
                    x: row.direction === "out" ? row.width - width - 12
                       : row.direction === "in" ? 12 : (row.width - width) / 2
                    sourceComponent: row.kind === "file" ? fileComp
                                   : row.direction === "system" ? systemComp : bubbleComp
                }

                Component {
                    id: bubbleComp
                    Rectangle {
                        radius: 12
                        color: row.direction === "out" ? win.outBubble : win.inBubble
                        width: Math.min(row.maxW, Math.max(body.implicitWidth, meta.implicitWidth) + 24)
                        height: body.height + meta.height + 16
                        TextEdit {
                            id: body
                            x: 12; y: 8
                            width: parent.width - 24
                            text: win.styled(row.html)
                            textFormat: TextEdit.RichText
                            wrapMode: TextEdit.Wrap
                            readOnly: true
                            selectByMouse: true
                            color: win.Material.foreground
                            selectionColor: win.Material.accent
                            font.pixelSize: 14
                            onLinkActivated: (link) => session.openLink(link)
                            HoverHandler { cursorShape: body.hoveredLink ? Qt.PointingHandCursor : Qt.IBeamCursor }
                        }
                        Label {
                            id: meta
                            anchors.right: parent.right
                            anchors.rightMargin: 10
                            anchors.top: body.bottom
                            text: (row.encrypted ? "🔒 " : "") + row.time
                            font.pixelSize: 10
                            opacity: 0.6
                        }
                    }
                }
                Component {
                    id: systemComp
                    Label {
                        width: Math.min(implicitWidth, row.maxW)
                        text: row.html
                        textFormat: Text.StyledText
                        wrapMode: Text.Wrap
                        horizontalAlignment: Text.AlignHCenter
                        font.italic: true
                        font.pixelSize: 12
                        opacity: 0.65
                    }
                }
                Component {
                    id: fileComp
                    TransferCard { transfer: row.transfer; session: win.session; width: Math.min(row.maxW, 340) }
                }
            }
        }

        Label {
            Layout.fillWidth: true
            Layout.leftMargin: 14
            text: qsTr("%1 is typing…").arg(session.name)
            visible: session.remoteTyping
            font.italic: true
            font.pixelSize: 12
            opacity: 0.7
        }

        Label {
            Layout.fillWidth: true
            Layout.margins: 6
            visible: app.connectionState !== "online"
            horizontalAlignment: Text.AlignHCenter
            text: qsTr("You are offline — messages can't be sent.")
            color: Material.color(Material.Red)
        }

        Pane {
            Layout.fillWidth: true
            padding: 8
            Material.elevation: 2
            RowLayout {
                anchors.fill: parent
                spacing: 6
                ScrollView {
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(120, Math.max(44, input.implicitHeight))
                    TextArea {
                        id: input
                        placeholderText: qsTr("Message (Shift+Enter: new line)")
                        wrapMode: TextArea.Wrap
                        focus: true
                        onTextChanged: session.textEdited(text)
                        Keys.onReturnPressed: (ev) => {
                            if (ev.modifiers & Qt.ShiftModifier) { ev.accepted = false; return }
                            send()
                        }
                        Keys.onEnterPressed: (ev) => send()
                        function send() {
                            if (app.connectionState !== "online" || !text.trim()) return
                            session.send(text)
                            text = ""
                        }
                    }
                }
                Button {
                    text: qsTr("Send")
                    highlighted: true
                    enabled: app.connectionState === "online" && input.text.trim().length > 0
                    onClicked: input.send()
                }
            }
        }
    }
}
