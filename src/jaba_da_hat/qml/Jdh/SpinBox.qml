import QtQuick
import QtQuick.Controls.Material as Mat

// Height from the font's line height — see [26].
Mat.SpinBox {
    id: control
    FontMetrics { id: fm; font: control.font }
    implicitHeight: Math.ceil(fm.height * 2.3) + topInset + bottomInset
}
