"""Which face Qt really uses for the families the client asks for, and their metrics (run in the container)."""

import glob
import sys

from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QFontMetrics
from PySide6.QtWidgets import QApplication

app = QApplication([])
fonts_dir = (
    sys.argv[1]
    if len(sys.argv) > 1
    else "/home/tester/pilcrow/qt/pilcrow_qt/assets/fonts"
)
for f in sorted(glob.glob(fonts_dir + "/*.ttf")):
    fid = QFontDatabase.addApplicationFont(f)
    fams = QFontDatabase.applicationFontFamilies(fid)
    print(
        f.split("/")[-1], "->", fams, "styles:", [QFontDatabase.styles(x) for x in fams]
    )
print(
    "all families containing Inter/Jet:",
    [x for x in QFontDatabase.families() if "Inter" in x or "Jet" in x],
)
for fam in [
    "Inter",
    "Inter Variable",
    "JetBrains Mono",
    "DejaVu Sans",
    "Noto Sans CJK TC",
]:
    for weight in (QFont.Weight.Normal, QFont.Weight.DemiBold):
        f = QFont(fam)
        f.setPixelSize(13)
        f.setWeight(weight)
        fi = QFontInfo(f)
        m = QFontMetrics(f)
        print(
            f"{fam:16} w={int(weight):3} -> {fi.family()!r} style={fi.styleName()!r} exact={fi.exactMatch()} h={m.height()} asc={m.ascent()} desc={m.descent()} ls={m.lineSpacing()} adv={m.horizontalAdvance('Fix the login redirect bug')}"
        )
