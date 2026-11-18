import QtQuick

// Ctrl + / Ctrl − / Ctrl 0 in every window: bigger, smaller, back to 100 % (fonts only).
// StandardKey.ZoomIn is Ctrl++ (shifted =); Ctrl+= also works without Shift. No sequence twice: a duplicate is "ambiguous".
Item {
    required property var app
    Shortcut {
        sequences: [StandardKey.ZoomIn, "Ctrl+="]
        onActivated: app.zoom(1)
        onActivatedAmbiguously: app.zoom(1)
    }
    Shortcut {
        sequences: [StandardKey.ZoomOut]
        onActivated: app.zoom(-1)
        onActivatedAmbiguously: app.zoom(-1)
    }
    Shortcut {
        sequence: "Ctrl+0"
        onActivated: app.zoom(0)
        onActivatedAmbiguously: app.zoom(0)
    }
}
