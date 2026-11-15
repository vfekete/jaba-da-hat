import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts

// Contact management: add / remove / block, user groups, multi-selection, drag & drop grouping.
ApplicationWindow {
    id: win
    required property var app

    width: 440
    height: 680
    minimumWidth: 360
    minimumHeight: 420
    title: qsTr("Contacts — %1").arg(app.appName)
    Material.theme: app.darkTheme ? Material.Dark : Material.Light
    Material.accent: Material.DeepOrange
    Material.primary: "#c4451c"

    property var selected: ({})          // jid -> true
    readonly property int selectedCount: Object.keys(selected).length
    property string filterText: ""

    function isSelected(jid) { return selected[jid] === true }
    function toggle(jid, additive) {
        let s = additive ? Object.assign({}, selected) : ({})
        if (additive && s[jid]) delete s[jid]; else s[jid] = true
        selected = s
    }
    function selectedJids() { return Object.keys(selected) }
    function clearSelection() { selected = ({}) }

    Connections {
        target: app.rosterModel
        // forget selections of contacts that disappeared
        function onChanged() {
            let s = {}
            for (const jid in win.selected)
                if (app.hasContact(jid)) s[jid] = true
            if (Object.keys(s).length !== win.selectedCount) win.selected = s
        }
    }

    header: ToolBar {
        Material.foreground: "white"
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 4
            spacing: 0
            ToolButton { text: qsTr("＋ Contact"); onClicked: addContactDialog.open(); enabled: app.connectionState === "online" }
            ToolButton { text: qsTr("＋ Group"); onClicked: addGroupDialog.open() }
            Item { Layout.fillWidth: true }
            ToolButton {
                text: "⋮"
                font.pixelSize: 18
                onClicked: moreMenu.popup()
                Menu {
                    id: moreMenu
                    MenuItem { text: qsTr("Remove selected contacts (%1)").arg(win.selectedCount); enabled: win.selectedCount > 0 && app.connectionState === "online"; onTriggered: removeDialog.openFor(false) }
                    MenuItem { text: qsTr("Remove && block selected (%1)").arg(win.selectedCount); enabled: win.selectedCount > 0 && app.connectionState === "online"; onTriggered: removeDialog.openFor(true) }
                    MenuSeparator {}
                    MenuItem { text: qsTr("Remove groups…"); enabled: app.groups.length > 0; onTriggered: removeGroupsDialog.open() }
                    MenuItem { text: qsTr("Blocked contacts (%1)…").arg(app.blockedJids.length); onTriggered: blockedDialog.open() }
                    MenuSeparator {}
                    MenuItem { text: qsTr("Clear selection"); enabled: win.selectedCount > 0; onTriggered: win.clearSelection() }
                }
            }
        }
    }

    footer: ToolBar {
        Material.background: win.Material.dialogColor
        height: 32
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 10
            anchors.rightMargin: 10
            PresenceDot { presence: app.presence }
            Label {
                Layout.fillWidth: true
                text: app.presenceLabel + (app.connectionState === "connecting" ? qsTr(" (connecting…)") : "")
                      + (app.isMock ? qsTr(" · mock data") : "")
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            Label { text: win.selectedCount ? qsTr("%1 selected").arg(win.selectedCount) : ""; font.pixelSize: 12 }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        TextField {
            id: filter
            Layout.fillWidth: true
            Layout.margins: 8
            placeholderText: qsTr("Filter contacts…")
            onTextChanged: win.filterText = text.toLowerCase()
        }

        Label {
            Layout.fillWidth: true
            Layout.leftMargin: 12
            Layout.rightMargin: 12
            wrapMode: Text.Wrap
            font.pixelSize: 11
            opacity: 0.6
            text: qsTr("Click selects (Ctrl+click adds), double-click opens chat. Drag a contact between rows to move it to that group, onto a contact to create a new group with both, or onto a group header.")
        }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: app.rosterModel
            ScrollBar.vertical: ScrollBar {}
            section.property: "group"
            section.criteria: ViewSection.FullString
            section.delegate: groupHeader

            delegate: contactRow
        }
    }

    // ---- drag proxy (follows the pointer while a contact is dragged) ----
    Rectangle {
        id: dragProxy
        property string jid: ""
        property string fromGroup: ""
        property string label: ""
        visible: Drag.active
        z: 100
        width: proxyLabel.implicitWidth + 24
        height: 30
        radius: 15
        color: Material.accent
        opacity: 0.9
        Drag.keys: ["jdh-contact"]
        Drag.hotSpot.x: 10
        Drag.hotSpot.y: 15
        Label { id: proxyLabel; anchors.centerIn: parent; text: dragProxy.label; color: "white" }
    }

    Component {
        id: groupHeader
        Rectangle {
            id: hdr
            required property string section
            width: ListView.view.width
            height: 30
            color: drop.containsDrag ? Qt.alpha(win.Material.accent, 0.25) : win.Material.dialogColor
            Label {
                anchors.verticalCenter: parent.verticalCenter
                x: 12
                text: (hdr.section || qsTr("Ungrouped")).toUpperCase()
                font.bold: true
                font.pixelSize: 11
                opacity: 0.75
            }
            DropArea {
                id: drop
                anchors.fill: parent
                keys: ["jdh-contact"]
                onDropped: (ev) => app.moveContact(dragProxy.jid, dragProxy.fromGroup, hdr.section)
            }
        }
    }

    Component {
        id: contactRow
        Item {
            id: row
            required property int index
            required property string jid
            required property string name
            required property string group
            required property string presence
            required property string statusText
            required property int unread
            required property bool placeholder

            readonly property bool matches: placeholder ? win.filterText === ""
                : win.filterText === "" || name.toLowerCase().includes(win.filterText) || jid.includes(win.filterText)
            width: ListView.view.width
            height: matches ? (placeholder ? 34 : 52) : 0
            visible: matches

            // drop zone: "above" | "below" | "onto" | ""
            property string zone: ""

            Rectangle {
                anchors.fill: parent
                visible: !row.placeholder
                color: row.zone === "onto" ? Qt.alpha(win.Material.accent, 0.30)
                     : win.isSelected(row.jid) ? Qt.alpha(win.Material.accent, 0.16)
                     : mouse.containsMouse ? Qt.alpha(win.Material.foreground, 0.05) : "transparent"
            }

            Label {
                visible: row.placeholder
                anchors.verticalCenter: parent.verticalCenter
                x: 40
                text: qsTr("empty group — drop contacts here")
                font.italic: true
                opacity: 0.5
            }

            RowLayout {
                visible: !row.placeholder
                anchors.fill: parent
                anchors.leftMargin: 8
                anchors.rightMargin: 8
                spacing: 8
                CheckBox {
                    checked: win.isSelected(row.jid)
                    onClicked: win.toggle(row.jid, true)
                    padding: 0
                }
                PresenceDot { presence: row.presence }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    Label { text: row.name; font.bold: row.unread > 0; elide: Text.ElideRight; Layout.fillWidth: true }
                    Label {
                        text: row.jid + (row.statusText ? " — " + row.statusText : "")
                        font.pixelSize: 11; opacity: 0.6; elide: Text.ElideRight; Layout.fillWidth: true
                    }
                }
                Rectangle {
                    visible: row.unread > 0
                    width: 22; height: 22; radius: 11
                    color: "#e01b24"
                    Label { anchors.centerIn: parent; text: row.unread; color: "white"; font.pixelSize: 11; font.bold: true }
                }
                ToolButton {
                    text: "💬"
                    onClicked: app.openChat(row.jid)
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("Open chat")
                }
            }

            MouseArea {
                id: mouse
                anchors.fill: parent
                anchors.rightMargin: 48
                anchors.leftMargin: 40
                enabled: !row.placeholder
                hoverEnabled: true
                acceptedButtons: Qt.LeftButton | Qt.RightButton
                onClicked: (ev) => {
                    if (ev.button === Qt.RightButton) {
                        if (!win.isSelected(row.jid)) win.toggle(row.jid, false)
                        ctxMenu.popup()
                    } else {
                        win.toggle(row.jid, ev.modifiers & Qt.ControlModifier)
                    }
                }
                onDoubleClicked: app.openChat(row.jid)
            }

            DragHandler {
                id: dragger
                enabled: !row.placeholder
                target: null
                dragThreshold: 8
                onActiveChanged: {
                    if (active) {
                        dragProxy.jid = row.jid
                        dragProxy.fromGroup = row.group
                        dragProxy.label = row.name
                    }
                    dragProxy.Drag.active = active
                    if (!active) dragProxy.Drag.drop()
                }
                onCentroidChanged: {
                    if (!active) return
                    const p = row.mapToItem(win.contentItem, centroid.position.x, centroid.position.y)
                    dragProxy.x = p.x - dragProxy.Drag.hotSpot.x
                    dragProxy.y = p.y - dragProxy.Drag.hotSpot.y
                }
            }

            DropArea {
                anchors.fill: parent
                keys: ["jdh-contact"]
                function zoneFor(y) {
                    if (row.placeholder) return "above"
                    if (y < height * 0.28) return "above"
                    if (y > height * 0.72) return "below"
                    return "onto"
                }
                onPositionChanged: (ev) => row.zone = (dragProxy.jid === row.jid) ? "" : zoneFor(ev.y)
                onEntered: (ev) => row.zone = (dragProxy.jid === row.jid) ? "" : zoneFor(ev.y)
                onExited: row.zone = ""
                onDropped: (ev) => {
                    const z = row.zone
                    row.zone = ""
                    if (!z) return
                    if (z === "onto") {
                        newGroupDialog.dragged = dragProxy.jid
                        newGroupDialog.draggedFrom = dragProxy.fromGroup
                        newGroupDialog.target = row.jid
                        newGroupDialog.targetName = row.name
                        newGroupDialog.draggedName = dragProxy.label
                        newGroupDialog.open()
                    } else {
                        app.moveContact(dragProxy.jid, dragProxy.fromGroup, row.group)
                    }
                }
            }

            // insertion line showing where the dragged contact will land
            Rectangle {
                visible: row.zone === "above" || row.zone === "below"
                width: parent.width - 16
                x: 8
                height: 3
                radius: 1.5
                y: row.zone === "above" ? 0 : parent.height - height
                color: win.Material.accent
                Rectangle { width: 9; height: 9; radius: 4.5; y: -3; color: win.Material.accent }
            }

            Menu {
                id: ctxMenu
                MenuItem { text: qsTr("Open chat"); onTriggered: app.openChat(row.jid) }
                MenuItem { text: qsTr("Rename…"); onTriggered: { renameDialog.jid = row.jid; renameField.text = row.name; renameDialog.open() } }
                MenuItem { text: qsTr("Copy Jabber ID"); onTriggered: app.copyText(row.jid) }
                MenuSeparator {}
                MenuItem { text: qsTr("Remove (%1)").arg(win.selectedCount); enabled: app.connectionState === "online"; onTriggered: removeDialog.openFor(false) }
                MenuItem { text: qsTr("Remove && block (%1)").arg(win.selectedCount); enabled: app.connectionState === "online"; onTriggered: removeDialog.openFor(true) }
            }
        }
    }

    // ---------------------------------------------------------------- dialogs
    Dialog {
        id: addContactDialog
        anchors.centerIn: parent
        width: Math.min(360, win.width - 32)
        modal: true
        title: qsTr("Add contact")
        standardButtons: Dialog.Ok | Dialog.Cancel
        onOpened: { jidField.text = ""; nickField.text = ""; groupBox.editText = ""; jidField.forceActiveFocus() }
        onAccepted: app.addContact(jidField.text, nickField.text, groupBox.editText)
        ColumnLayout {
            anchors.fill: parent
            TextField { id: jidField; Layout.fillWidth: true; placeholderText: qsTr("Jabber ID, e.g. someone@example.org") }
            TextField { id: nickField; Layout.fillWidth: true; placeholderText: qsTr("Nickname (optional)") }
            ComboBox { id: groupBox; Layout.fillWidth: true; editable: true; model: app.groups; displayText: editText || qsTr("Group (optional)") }
            Label {
                Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: 11; opacity: 0.6
                text: qsTr("A subscription request is sent; the contact appears online once they accept.")
            }
        }
        Component.onCompleted: standardButton(Dialog.Ok).enabled = Qt.binding(() => jidField.text.indexOf("@") > 0)
    }

    Dialog {
        id: addGroupDialog
        anchors.centerIn: parent
        width: Math.min(320, win.width - 32)
        modal: true
        title: qsTr("Add group")
        standardButtons: Dialog.Ok | Dialog.Cancel
        onOpened: { groupField.text = ""; groupField.forceActiveFocus() }
        onAccepted: app.addGroup(groupField.text)
        TextField { id: groupField; width: parent.width; placeholderText: qsTr("Group name"); onAccepted: addGroupDialog.accept() }
    }

    Dialog {
        id: newGroupDialog
        property string dragged
        property string draggedFrom
        property string draggedName
        property string target
        property string targetName
        anchors.centerIn: parent
        width: Math.min(340, win.width - 32)
        modal: true
        title: qsTr("Create group")
        standardButtons: Dialog.Ok | Dialog.Cancel
        onOpened: { newGroupField.text = ""; newGroupField.forceActiveFocus() }
        onAccepted: app.groupTogether(dragged, draggedFrom, target, newGroupField.text)
        ColumnLayout {
            anchors.fill: parent
            Label { text: qsTr("New group for %1 and %2:").arg(newGroupDialog.draggedName).arg(newGroupDialog.targetName); wrapMode: Text.Wrap; Layout.fillWidth: true }
            TextField { id: newGroupField; Layout.fillWidth: true; placeholderText: qsTr("Group name"); onAccepted: newGroupDialog.accept() }
        }
    }

    Dialog {
        id: renameDialog
        property string jid
        anchors.centerIn: parent
        width: Math.min(320, win.width - 32)
        modal: true
        title: qsTr("Rename contact")
        standardButtons: Dialog.Ok | Dialog.Cancel
        onAccepted: app.renameContact(jid, renameField.text)
        TextField { id: renameField; width: parent.width; onAccepted: renameDialog.accept() }
    }

    Dialog {
        id: removeDialog
        property bool block: false
        property var jids: []
        function openFor(b) { block = b; jids = win.selectedJids(); open() }
        anchors.centerIn: parent
        width: Math.min(360, win.width - 32)
        modal: true
        title: block ? qsTr("Remove and block") : qsTr("Remove contacts")
        standardButtons: Dialog.Yes | Dialog.No
        onAccepted: {
            if (block) app.removeAndBlock(jids); else app.removeContacts(jids)
            win.clearSelection()
        }
        Label {
            width: parent.width
            wrapMode: Text.Wrap
            text: (removeDialog.block
                   ? qsTr("Remove and block %n contact(s)? They will no longer be able to message you or see your presence.", "", removeDialog.jids.length)
                   : qsTr("Remove %n contact(s) from your roster?", "", removeDialog.jids.length))
                  + "\n\n" + removeDialog.jids.join("\n")
        }
    }

    Dialog {
        id: blockedDialog
        property var chosen: ({})
        anchors.centerIn: parent
        width: Math.min(380, win.width - 32)
        height: Math.min(460, win.height - 40)
        modal: true
        title: qsTr("Blocked contacts")
        onOpened: chosen = ({})
        footer: DialogButtonBox {
            Button {
                text: qsTr("Unblock selected")
                enabled: Object.keys(blockedDialog.chosen).length > 0 && app.connectionState === "online"
                DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
            }
            Button { text: qsTr("Close"); flat: true; DialogButtonBox.buttonRole: DialogButtonBox.RejectRole }
        }
        onAccepted: app.unblock(Object.keys(chosen))
        ColumnLayout {
            anchors.fill: parent
            Label {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                opacity: 0.7
                font.pixelSize: 12
                text: app.blockedJids.length === 0 ? qsTr("Nobody is blocked.")
                      : qsTr("Blocked people can't message you or see your presence. Unblocking doesn't add them back to your contacts.")
            }
            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: app.blockedJids
                delegate: CheckBox {
                    required property string modelData
                    text: modelData
                    checked: blockedDialog.chosen[modelData] === true
                    onToggled: {
                        let c = Object.assign({}, blockedDialog.chosen)
                        if (checked) c[modelData] = true; else delete c[modelData]
                        blockedDialog.chosen = c
                    }
                }
            }
        }
    }

    Dialog {
        id: removeGroupsDialog
        property var chosen: ({})
        anchors.centerIn: parent
        width: Math.min(340, win.width - 32)
        height: Math.min(460, win.height - 40)
        modal: true
        title: qsTr("Remove groups")
        standardButtons: Dialog.Ok | Dialog.Cancel
        onOpened: chosen = ({})
        onAccepted: app.removeGroups(Object.keys(chosen))
        ColumnLayout {
            anchors.fill: parent
            Label { text: qsTr("Contacts in removed groups are kept."); opacity: 0.7; font.pixelSize: 12 }
            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: app.groups
                delegate: CheckBox {
                    required property string modelData
                    text: modelData
                    checked: removeGroupsDialog.chosen[modelData] === true
                    onToggled: {
                        let c = Object.assign({}, removeGroupsDialog.chosen)
                        if (checked) c[modelData] = true; else delete c[modelData]
                        removeGroupsDialog.chosen = c
                    }
                }
            }
        }
    }
}
