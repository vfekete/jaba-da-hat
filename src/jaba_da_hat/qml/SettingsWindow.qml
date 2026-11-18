import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material as Mat  // namespaced: a plain import would shadow the Jdh style's controls
import QtQuick.Dialogs
import QtQuick.Layouts

ApplicationWindow {
    id: win
    required property var app
    property var s: ({})

    width: 520
    height: 600
    minimumWidth: 420
    minimumHeight: 480
    title: qsTr("Settings — %1").arg(app.appName)
    Mat.Material.theme: app.darkTheme ? Mat.Material.Dark : Mat.Material.Light
    font: app.uiFont

    Zoom { app: win.app }
    Mat.Material.accent: Mat.Material.DeepOrange
    Mat.Material.primary: "#c4451c"

    function reload() { s = app.settingsMap() }
    onVisibleChanged: if (visible) reload()
    Component.onCompleted: reload()

    function collect() {
        return {
            jid: jid.text.trim(),
            password: password.text,
            server: server.text.trim(),
            port: port.value,
            resource: resource.text.trim() || "jaba-da-hat",
            require_tls: requireTls.checked,
            use_encryption: useEncryption.checked,
            auto_login: autoLogin.checked,
            auto_reconnect: autoReconnect.checked,
            start_at_login: startAtLogin.checked,
            theme: ["system", "light", "dark"][theme.currentIndex],
            notification_style: ["toast", "gnome", "both"][notifStyle.currentIndex],
            ui_font_family: uiFont.family,
            ui_font_size: uiFont.size,
            message_font_family: msgFont.family,
            message_font_size: msgFont.size,
            send_typing: sendTyping.checked,
            show_typing: showTyping.checked,
            download_dir: downloadDir.text.trim()
        }
    }

    header: TabBar {
        id: tabs
        TabButton { text: qsTr("Account") }
        TabButton { text: qsTr("Appearance") }
        TabButton { text: qsTr("Privacy") }
        TabButton { text: qsTr("General") }
    }

    footer: DialogButtonBox {
        standardButtons: DialogButtonBox.Save | DialogButtonBox.Cancel
        onAccepted: { app.saveSettings(win.collect()); win.close() }
        onRejected: win.close()
    }

    StackLayout {
        anchors.fill: parent
        anchors.margins: 16
        currentIndex: tabs.currentIndex

        // ---- Account
        ScrollView {
            contentWidth: availableWidth
            GridLayout {
                width: parent.width
                columns: 2
                columnSpacing: 12
                Label { text: qsTr("Jabber ID") }
                TextField { id: jid; Layout.fillWidth: true; text: win.s.jid || ""; placeholderText: "you@example.org" }
                Label { text: qsTr("Password") }
                RowLayout {
                    Layout.fillWidth: true
                    TextField { id: password; Layout.fillWidth: true; text: win.s.password || ""; echoMode: showPw.checked ? TextInput.Normal : TextInput.Password }
                    ToolButton { id: showPw; text: "👁"; checkable: true }
                }
                Label { text: qsTr("Server") }
                TextField { id: server; Layout.fillWidth: true; text: win.s.server || ""; placeholderText: qsTr("automatic (from Jabber ID)") }
                Label { text: qsTr("Port") }
                SpinBox {
                    id: port; from: 1; to: 65535; value: win.s.port || 5222; editable: true
                    textFromValue: (v) => String(v)
                    valueFromText: (t) => parseInt(t) || 5222
                }
                Label { text: qsTr("Resource") }
                TextField { id: resource; Layout.fillWidth: true; text: win.s.resource || "jaba-da-hat" }
                Item { width: 1; height: 1 }
                CheckBox { id: requireTls; Layout.fillWidth: true; text: qsTr("Require encrypted connection (TLS)"); checked: win.s.require_tls !== false }
                Item { width: 1; height: 1 }
                CheckBox { id: useEncryption; Layout.fillWidth: true; text: qsTr("Use end-to-end encryption (OMEMO) by default"); checked: win.s.use_encryption !== false }
                Item { width: 1; height: 1 }
                CheckBox { id: autoLogin; Layout.fillWidth: true; text: qsTr("Log in automatically on start"); checked: win.s.auto_login !== false }
                Item { width: 1; height: 1 }
                CheckBox { id: autoReconnect; Layout.fillWidth: true; text: qsTr("Reconnect automatically when the connection drops"); checked: win.s.auto_reconnect !== false }
                Label {
                    Layout.columnSpan: 2
                    Layout.fillWidth: true
                    Layout.topMargin: 8
                    wrapMode: Text.Wrap
                    font.pixelSize: Math.round(11 * app.uiScale)
                    opacity: 0.65
                    text: qsTr("The password is stored in ~/.config/jaba-da-hat/config.json, readable only by your user (mode 0600).")
                }
            }
        }

        // ---- Appearance
        ScrollView {
            contentWidth: availableWidth
            ColumnLayout {
                width: parent.width
                spacing: 12
                Label { text: qsTr("Theme") ; font.bold: true }
                ComboBox {
                    id: theme
                    Layout.fillWidth: true
                Layout.maximumWidth: Math.round(260 * app.uiScale)
                    model: [qsTr("Follow system"), qsTr("Light"), qsTr("Dark")]
                    currentIndex: Math.max(0, ["system", "light", "dark"].indexOf(win.s.theme || "system"))
                }
                Label {
                    text: qsTr("“Follow system” switches automatically when GNOME's dark style is toggled.")
                    wrapMode: Text.Wrap; Layout.fillWidth: true; opacity: 0.65; font.pixelSize: Math.round(12 * app.uiScale)
                }
                Label { text: qsTr("Notifications"); font.bold: true; Layout.topMargin: 12 }
                ComboBox {
                    id: notifStyle
                    Layout.fillWidth: true
                Layout.maximumWidth: Math.round(260 * app.uiScale)
                    model: [qsTr("Own toast (top right)"), qsTr("GNOME notification"), qsTr("Both")]
                    currentIndex: Math.max(0, ["toast", "gnome", "both"].indexOf(win.s.notification_style || "toast"))
                }
                Label {
                    text: qsTr("GNOME hides its notification banners while “Do Not Disturb” is on; the own toast is shown regardless. The tray icon always shakes on new activity.")
                    wrapMode: Text.Wrap; Layout.fillWidth: true; opacity: 0.65; font.pixelSize: Math.round(12 * app.uiScale)
                }
                Label { text: qsTr("Interface font"); font.bold: true; Layout.topMargin: 12 }
                FontPicker {
                    id: uiFont
                    app: win.app
                    family: win.s.ui_font_family || ""
                    size: win.s.ui_font_size || 0
                    previewText: qsTr("Contacts · Settings · Notifications")
                }
                Label { text: qsTr("Message font"); font.bold: true; Layout.topMargin: 8 }
                FontPicker {
                    id: msgFont
                    app: win.app
                    family: win.s.message_font_family || ""
                    size: win.s.message_font_size || 0
                }
                RowLayout {
                    Layout.topMargin: 8
                    spacing: 8
                    Label { text: qsTr("Zoom: %1 %").arg(app.zoomPercent) }
                    Button { text: qsTr("Reset"); flat: true; enabled: app.zoomPercent !== 100; onClicked: app.zoom(0) }
                }
                Label {
                    text: qsTr("Ctrl + / Ctrl − / Ctrl 0 in any window make all text bigger, smaller or back to normal. The tray menu is drawn by GNOME and keeps the system font.")
                    wrapMode: Text.Wrap; Layout.fillWidth: true; opacity: 0.65; font.pixelSize: Math.round(12 * app.uiScale)
                }
            }
        }

        // ---- Privacy
        ScrollView {
            contentWidth: availableWidth
            ColumnLayout {
                width: parent.width
                spacing: 4
                CheckBox { id: sendTyping; Layout.fillWidth: true; text: qsTr("Let contacts see when I am typing"); checked: win.s.send_typing !== false }
                CheckBox { id: showTyping; Layout.fillWidth: true; text: qsTr("Show when contacts are typing"); checked: win.s.show_typing !== false }
                Label {
                    text: qsTr("Typing notifications use XEP-0085 chat states. Conversations are kept in memory only while their window is open and are never written to disk. Pictures and files are never downloaded automatically.")
                    wrapMode: Text.Wrap; Layout.fillWidth: true; opacity: 0.65; font.pixelSize: Math.round(12 * app.uiScale); Layout.topMargin: 8
                }
            }
        }

        // ---- General
        ScrollView {
            contentWidth: availableWidth
            ColumnLayout {
                width: parent.width
                spacing: 8
                CheckBox { id: startAtLogin; Layout.fillWidth: true; text: qsTr("Start when I log in to GNOME"); checked: win.s.start_at_login === true }
                Label { text: qsTr("Download folder"); font.bold: true; Layout.topMargin: 8 }
                RowLayout {
                    Layout.fillWidth: true
                    TextField { id: downloadDir; Layout.fillWidth: true; text: win.s.download_dir || "" }
                    Button { text: qsTr("Choose…"); onClicked: folderDialog.open() }
                }
                Label { text: qsTr("Version %1").arg(app.version); opacity: 0.6; Layout.topMargin: 16 }
            }
        }
    }

    FolderDialog {
        id: folderDialog
        currentFolder: "file://" + downloadDir.text
        onAccepted: downloadDir.text = decodeURIComponent(selectedFolder.toString().replace(/^file:\/\//, ""))
    }
}
