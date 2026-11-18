import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Font family ("System default" or one installed family) + size in points ("Default" = 0), with a preview.
ColumnLayout {
    id: picker
    required property var app
    property string family: ""
    property real size: 0
    property string previewText: qsTr("The quick brown fox jumps over the lazy dog")
    readonly property var sizes: [0, 8, 9, 10, 11, 12, 13, 14, 16, 18, 20, 24, 28]

    spacing: 4
    Layout.fillWidth: true
    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        ComboBox {  // up to its preferred width, narrower when the window is
            id: familyBox
            Layout.fillWidth: true
            Layout.minimumWidth: 100
            Layout.maximumWidth: Math.round(260 * (app?.uiScale ?? 1))
            model: [qsTr("System default")].concat(app?.fontFamilies ?? [])
            currentIndex: picker.family ? Math.max(0, model.indexOf(picker.family)) : 0
            onActivated: (i) => picker.family = i === 0 ? "" : model[i]
        }
        ComboBox {
            id: sizeBox
            Layout.fillWidth: true
            Layout.minimumWidth: 80
            Layout.maximumWidth: Math.round(120 * (app?.uiScale ?? 1))
            model: picker.sizes.map(v => v === 0 ? qsTr("Default") : v + " pt")
            currentIndex: Math.max(0, picker.sizes.indexOf(Math.round(picker.size)))
            onActivated: (i) => picker.size = picker.sizes[i]
        }
        Item { Layout.fillWidth: true }
    }
    Label {
        Layout.fillWidth: true
        elide: Text.ElideRight
        text: picker.previewText
        font.family: picker.family || Qt.application.font.family
        font.pointSize: (picker.size || 10.5) * (app?.zoomPercent ?? 100) / 100
    }
}
