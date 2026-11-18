import QtQuick
import QtQuick.Controls.Material as Mat

// Height from the font's line height; topInset/bottomInset keep Material's room for the shadow — see [26].
Mat.Button {
    id: control
    FontMetrics { id: fm; font: control.font }
    implicitHeight: Math.ceil(fm.height * 1.9) + topInset + bottomInset
}
