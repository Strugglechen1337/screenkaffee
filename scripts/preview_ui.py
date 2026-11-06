import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase, QFont
from screenkaffee.app import MainWindow, STYLE
from screenkaffee.workspace import Workspace
from screenkaffee.editor import EditorWindow

# Nur rendern: keine echten globalen Tastenkürzel registrieren (sie würden der laufenden App die Tasten wegnehmen)
MainWindow.apply_hotkeys = lambda self: {}
app = QApplication([])
for filename in ["segoeui.ttf", "segoeuib.ttf"]:
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + filename)
app.setFont(QFont("Segoe UI", 10))
app.setStyle("Fusion")
app.setStyleSheet(STYLE)
folder = Path(".tools/ui-preview")
workspace = Workspace(folder)
if not workspace.rows():
    for index, (title, background) in enumerate([("Projektübersicht", "#193447"), ("Farben & Kontraste", "#2e2546"), ("Einrichtung · Schritt 1", "#1c423d")]):
        image = Image.new("RGB", (1000, 620), background)
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 28)
        large = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 42)
        draw.text((55, 45), title, fill="#f1f5f9", font=large)
        for row in range(4):
            draw.rounded_rectangle((55, 150 + row * 90, 925, 220 + row * 90), 12, fill="#345568")
            draw.text((80, 165 + row * 90), ["Aufnahmen organisieren", "Farben sichtbar erhalten", "Mit Markierungen erklären", "Als Dokument weitergeben"][row], fill="white", font=font)
        workspace.add(image, title, {"backend": "Vorschaubild"})
window = MainWindow(workspace)
window.show()
app.processEvents()
window.select_id(workspace.rows()[0]["id"])
app.processEvents()
window.grab().save(".tools/workspace-preview.png")
editor = EditorWindow(workspace.image(workspace.rows()[0]["id"]), "Editor-Vorschau")
editor.view.add_annotation({"kind": "arrow", "color": "#ff6448", "width": 7, "points": [650, 115, 500, 178]})
editor.view.add_annotation({"kind": "step", "color": "#ff6448", "width": 4, "points": [20, 165, 58, 203], "text": "1", "text_width": 46, "text_height": 46})
editor.show()
app.processEvents()
editor.grab().save(".tools/editor-preview.png")
if window.hotkeys:
    window.hotkeys.close()
window.tray.hide()
workspace.close()
print("Rendered .tools/workspace-preview.png and .tools/editor-preview.png")
