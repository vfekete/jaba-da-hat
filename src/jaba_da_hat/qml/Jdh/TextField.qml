import QtQuick
import QtQuick.Controls.Material as Mat

// Height from the font's line height (follows font and zoom), not Material's fixed 56 px — see docs/comments-details.md [26].
Mat.TextField {
    id: control
    FontMetrics { id: fm; font: control.font }
    implicitHeight: Math.ceil(fm.height * 2.3) + topInset + bottomInset
}
