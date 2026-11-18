import QtQuick
import QtQuick.Controls.Material as Mat

// Height from the font's line height; the label wraps, so a narrow window doesn't cut it (or push others) off — see [26].
Mat.CheckBox {
    id: control
    FontMetrics { id: fm; font: control.font }
    topPadding: Math.round(fm.height * 0.35)  // Material: 15 px touch padding
    bottomPadding: Math.round(fm.height * 0.35)
    implicitHeight: Math.max(Math.ceil(fm.height * 1.9),
                             (contentItem ? contentItem.implicitHeight : 0) + topPadding + bottomPadding)
    Binding { target: control.contentItem; property: "wrapMode"; value: Text.WordWrap; when: !!control.contentItem && control.contentItem.wrapMode !== undefined }
}
