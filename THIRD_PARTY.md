# Abhängigkeiten und Herkunft

screenkaffee ist eine eigenständige Implementierung. Es enthält keinen fremden
Programmcode und keine fremden Marken- oder Bildassets.

| Komponente | Lizenz / Quelle |
| --- | --- |
| Python | PSF, weitere Lizenzen mitgelieferter Standardbibliotheken |
| PySide6 Essentials / Shiboken / Qt | LGPLv3; zusätzliche Komponenten entsprechend ihren Lizenzhinweisen |
| NumPy | BSD-3-Clause, weitere gebündelte Bibliotheken laut Lizenzdateien |
| Pillow | HPND, weitere Bildbibliotheken laut Lizenzdateien |
| PyWinRT | MIT |
| ZXing-C++ | Apache-2.0 |

Windows: Lizenztexte der installierten Wheels werden unter `licenses/` mitgeliefert.
Qt bleibt als dynamische Bibliothek unter `_internal/PySide6/` austauschbar.
Linux: Originale Lizenzdateien bleiben im Runtime-Archiv und in den
`*.dist-info/licenses/`-Verzeichnissen erhalten. Die Python-Laufzeit stammt aus
[python-build-standalone](https://github.com/astral-sh/python-build-standalone),
Release `20261026`, Python `3.12.16`; SHA256 wird im Build-Skript geprüft.

Die zugehörigen Bibliotheksquellen und Änderungs-/Austauschhinweise sind erhältlich unter:

- [Qt for Python](https://code.qt.io/cgit/pyside/pyside-setup.git/), Version 6.12.0
- [Qt](https://code.qt.io/cgit/qt/), Version 6.12.0
- [NumPy](https://github.com/numpy/numpy)
- [Pillow](https://github.com/python-pillow/Pillow)
- [PyWinRT](https://github.com/pywinrt/pywinrt)
- [ZXing-C++](https://github.com/zxing-cpp/zxing-cpp)

Die App untersagt keine Änderungen oder das Debuggen der Bibliotheken. Für einen
eigenen Build können die Wheels im Quellverzeichnis ersetzt und die Build-Skripte
erneut ausgeführt werden. Die Drittanbieter-Lizenzen gelten unabhängig von der
MIT-Lizenz des eigenen App-Codes.

Tesseract, adb, grim, slurp, hyprctl, ffmpeg und wf-recorder sind optionale
lokale Integrationen; diese ausführbaren Programme werden nicht mit diesem Paket
verteilt. Die App führt sie nur für vom Nutzer gestartete Funktionen aus.

Technische Referenzen:

- [Windows Graphics Capture](https://learn.microsoft.com/en-us/windows/apps/develop/media-authoring-processing/screen-capture)
- [SDR-Weißpegel auf HDR-Anzeigen](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/ns-wingdi-displayconfig_sdr_white_level)
- [PyWinRT Capture-Interop](https://pywinrt.readthedocs.io/en/stable/api/windows/graphics/capture/interop.html)
- [XDG Screenshot Portal](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Screenshot.html)
