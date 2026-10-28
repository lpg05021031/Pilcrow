"""How a QLabel is really drawn under each way the client sets its font (run in the container).

For each case: the font QFontInfo reports, and the painted width of a sample string (measured from
a rendered image), which is what tells Inter (narrow) from DejaVu (wide).
"""

import sys

sys.path.insert(0, "/repo/qt")
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontInfo, QImage
from PySide6.QtWidgets import QApplication, QLabel

app = QApplication([])
app.setStyle("Fusion")
from pilcrow_qt import fonts, theme

print("fonts.load ->", fonts.load())
theme.theme.apply(True)
SAMPLE = "New session — Write tests for the parser"


def painted_width(w: QLabel) -> int:
    w.adjustSize()
    img = w.grab().toImage().convertToFormat(QImage.Format.Format_Grayscale8)
    cols = []
    for x in range(img.width()):
        for y in range(img.height()):
            if abs(img.pixelColor(x, y).value() - img.pixelColor(0, 0).value()) > 40:
                cols.append(x)
                break
    return (max(cols) - min(cols)) if cols else -1


cases = {
    "plain QLabel (app font)": lambda lb: None,
    "QSS font-weight: 400": lambda lb: lb.setStyleSheet("font-weight: 400;"),
    "QSS font-weight: 500": lambda lb: lb.setStyleSheet("font-weight: 500;"),
    "QSS font-size: 13px": lambda lb: lb.setStyleSheet("font-size: 13px;"),
    "QSS color only": lambda lb: lb.setStyleSheet("color: #ededed;"),
    "setFont(theme.font_ui())": lambda lb: lb.setFont(theme.font_ui()),
    "setFont(font_ui(13, 500))": lambda lb: lb.setFont(theme.font_ui(13, 500)),
}
for name, fn in cases.items():
    lb = QLabel(SAMPLE)
    lb.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    fn(lb)
    lb.show()
    fi = QFontInfo(lb.font())
    print(
        f"{name:28} -> QFontInfo {fi.family()!r}/{fi.styleName()!r} w={fi.weight()} px={fi.pixelSize()} painted width={painted_width(lb)}"
    )
