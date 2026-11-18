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
    font: app.uiFont

    Zoom { app: win.app }
    Material.accent: Material.DeepOrange
    Material.primary: "#c4451c"

    readonly property color inBubble: app.darkTheme ? "#3a3a3d" : "#ececec"
    readonly property color outBubble: app.darkTheme ? "#6e3420" : "#ffd8c7"
    readonly property string linkColor: app.darkTheme ? "#8cc4ff" : "#1a5fb4"
    // the HTML comes from textutil.linkify, so only our own <a> tags exist
    function styled(html) { return html.replace(/<a href=/g, '<a style="color:' + linkColor + '" href=') }

    onActiveChanged: {
        session.setWindowActive(active)
        if (active && !trustDialog.visible && !closeDialog.visible) input.forceActiveFocus()
    }
    Component.onCompleted: input.forceActiveFocus()
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
                Label { text: session.name; font.bold: true; font.pixelSize: Math.round(16 * app.uiScale); elide: Text.ElideRight; Layout.fillWidth: true }
                Label {
                    text: session.jid + (session.statusText ? " — " + session.statusText : "")
                    font.pixelSize: Math.round(12 * app.uiScale); opacity: 0.85; elide: Text.ElideRight; Layout.fillWidth: true
                }
            }
            ToolButton {
                id: lockBtn
                focusPolicy: Qt.NoFocus
                text: session.encrypted ? "🔒" : "🔓"
                font.pixelSize: Math.round(18 * app.uiScale)
                checkable: true
                checked: session.encrypted
                onToggled: session.encrypted = checked
                ToolTip.visible: hovered
                ToolTip.text: session.encrypted
                    ? (session.canEncrypt ? qsTr("OMEMO encryption on — click to turn off")
                                          : qsTr("Encryption on, but no OMEMO devices known for this contact: nothing is sent until they appear or you turn encryption off"))
                    : qsTr("Not end-to-end encrypted — click to turn on OMEMO")
            }
            ToolButton {
                text: "📎"
                focusPolicy: Qt.NoFocus
                font.pixelSize: Math.round(18 * app.uiScale)
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
        width: Math.min(win.width - 32, Math.round(340 * app.uiScale))  // explicit: content width depends on it
        standardButtons: Dialog.Yes | Dialog.No
        contentItem: Label { text: qsTr("Closing this chat cancels its running file transfers. Close anyway?"); wrapMode: Text.Wrap }
        onAccepted: { win.confirmedClose = true; win.close() }
    }

    // OMEMO: every device must be approved in this window; nothing is pre-selected — see docs/comments-details.md [22].
    Dialog {
        id: trustDialog
        property var req: session.trustRequest
        property var picked: ({})
        onReqChanged: picked = ({})
        Connections {
            target: session
            function onTrustChanged() {
                if ((session.trustRequest.devices ?? []).length > 0) trustDialog.open()
                else trustDialog.close()
            }
        }
        anchors.centerIn: parent
        width: Math.min(win.width - 24, 460)
        height: Math.min(win.height - 24, implicitHeight)
        modal: true
        closePolicy: Popup.NoAutoClose
        title: qsTr("Approve encryption devices")
        ColumnLayout {
            anchors.fill: parent
            spacing: 8
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("Compare each fingerprint with the one shown on that device (in person or over another channel). Only approved devices can read your messages in this chat; the approval ends when this window closes.")
            }
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredHeight: Math.min(devCol.implicitHeight, 300)
                clip: true
                ColumnLayout {
                    id: devCol
                    width: trustDialog.availableWidth
                    Repeater {
                        model: trustDialog.req.devices ?? []
                        delegate: CheckBox {
                            required property var modelData
                            Layout.fillWidth: true
                            checked: false
                            onToggled: { trustDialog.picked[modelData.id] = checked; trustDialog.picked = trustDialog.picked }
                            contentItem: ColumnLayout {
                                spacing: 0
                                anchors.left: parent.indicator.right
                                anchors.leftMargin: 8
                                anchors.right: parent.right
                                Label {
                                    text: (modelData.own ? qsTr("Your other device") : modelData.jid)
                                          + (modelData.label ? " · " + modelData.label : "") + " · #" + modelData.device
                                    font.bold: true
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Label {
                                    text: modelData.fingerprint
                                    font.family: "monospace"
                                    font.pixelSize: Math.round(12 * app.uiScale)
                                    wrapMode: Text.WrapAnywhere
                                    Layout.fillWidth: true
                                }
                            }
                        }
                    }
                }
            }
            Label {
                Layout.fillWidth: true
                wrapMode: Text.WrapAnywhere
                font.pixelSize: Math.round(12 * app.uiScale)
                opacity: 0.8
                text: qsTr("This device: ") + (trustDialog.req.own ?? "")
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                Button { text: qsTr("Don't send"); flat: true; onClicked: session.cancelTrust() }
                Button {
                    text: qsTr("Approve selected and send")
                    highlighted: true
                    enabled: Object.keys(trustDialog.picked).some(k => trustDialog.picked[k])
                    onClicked: session.approveDevices(Object.keys(trustDialog.picked).filter(k => trustDialog.picked[k]))
                }
            }
        }
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
        // Typing while something else has focus (e.g. after selecting text in a bubble) goes to the input.
        Keys.onPressed: (ev) => {
            const plain = !(ev.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
            if (plain && ev.text.length > 0 && ev.text.charCodeAt(0) >= 32 && !input.activeFocus) {
                input.forceActiveFocus()
                input.insert(input.cursorPosition, ev.text)
                ev.accepted = true
            }
        }

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
                            font: app.messageFont
                            onLinkActivated: (link) => session.openLink(link)
                            HoverHandler { cursorShape: body.hoveredLink ? Qt.PointingHandCursor : Qt.IBeamCursor }
                        }
                        Label {
                            id: meta
                            anchors.right: parent.right
                            anchors.rightMargin: 10
                            anchors.top: body.bottom
                            text: (row.encrypted ? "🔒 " : "") + row.time
                            font.pixelSize: Math.round(10 * app.uiScale)
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
                        font.pixelSize: Math.round(12 * app.uiScale)
                        opacity: 0.65
                    }
                }
                Component {
                    id: fileComp
                    TransferCard { transfer: row.transfer; session: win.session; uiScale: app.uiScale; width: Math.min(row.maxW, 340 * app.uiScale) }
                }
            }
        }

        Label {
            Layout.fillWidth: true
            Layout.leftMargin: 14
            text: qsTr("%1 is typing…").arg(session.name)
            visible: session.remoteTyping
            font.italic: true
            font.pixelSize: Math.round(12 * app.uiScale)
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
                // Own Flickable: scroll to the cursor only after the new height is laid out — see docs/comments-details.md [25].
                Flickable {
                    id: inputFlick
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(Math.round(120 * app.uiScale) + input.topPadding + input.bottomPadding,
                                                     input.implicitHeight)
                    contentWidth: width
                    contentHeight: input.implicitHeight
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar { policy: inputFlick.contentHeight > inputFlick.height ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff }
                    onContentHeightChanged: Qt.callLater(showCursor)
                    onHeightChanged: Qt.callLater(showCursor)
                    function showCursor() {
                        const r = input.cursorRectangle
                        const top = r.y - input.topPadding
                        const bottom = r.y + r.height + input.bottomPadding
                        const maxY = Math.max(0, contentHeight - height)
                        if (top < contentY) contentY = Math.max(0, top)
                        else if (bottom > contentY + height) contentY = Math.min(maxY, bottom - height)
                    }
                    TextArea {
                        id: input
                        width: inputFlick.width
                        placeholderText: qsTr("Message (Shift+Enter: new line)")
                        wrapMode: TextArea.Wrap
                        font: app.messageFont
                        focus: true
                        onCursorRectangleChanged: Qt.callLater(inputFlick.showCursor)
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
                    focusPolicy: Qt.NoFocus
                    highlighted: true
                    enabled: app.connectionState === "online" && input.text.trim().length > 0
                    onClicked: input.send()
                }
            }
        }
    }
}
