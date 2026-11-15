import QtQuick

Rectangle {
    property string presence: "offline"
    implicitWidth: 12
    implicitHeight: 12
    radius: width / 2
    border.width: 1
    border.color: Qt.rgba(1, 1, 1, 0.7)
    color: ({ available: "#2ec27e", away: "#f5c211", xa: "#e66100", dnd: "#e01b24" })[presence] || "#9a9996"
}
