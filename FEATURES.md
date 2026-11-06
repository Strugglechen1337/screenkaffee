# Funktionsvergleich und Abnahme

Stand: 28.10.2026. Eigenständige App **screenkaffee**.
**Vollständige Parität und Hardware-Abnahme sind weiterhin nicht erreicht.**
Maßgeblich ist `screenkaffee/parity.py` (`FEATURES`). Implementiert bedeutet: Code und Test vorhanden; zusätzliche Abnahme bleibt im jeweiligen Hinweis sichtbar.

## Neu umgesetzt

- **1.1.1 (06.11.2026)**: Papierkorb als eigene Ansicht mit Symbol in der Sammlungsleiste; 14 Fehler aus zwei Code-Reviews behoben (sicheres Löschen, Ordnerabgleich inkl. Favoriten, Handy-Freigabe, Zeitraffer, Zuschnitt-Eingabe und kurze Reststücke im Video-Editor, Miniaturen beim Arbeitsbereichswechsel, CLI-Fehlermeldungen, Menü-Speicherleck, Mehrfachziehen mit Undo). Per Test geprüft, nicht von Hand bedient.
- **1.1.0 (06.11.2026)**, alles per Test geprüft, nicht von Hand am Bildschirm bedient:
  - Editor: Markierungen nach vorne/hinten (Strg+Bild auf/ab), sperren, ausrichten und gleichmäßig verteilen, mit Pfeiltasten um 1 px bzw. 10 px verschieben; Ränder automatisch zuschneiden (auch als Aufnahmeoption); Lupe mit Rahmen um den Quellbereich und Verbindungslinien.
  - Video-Editor: Abschnitte aus der Mitte herausschneiden; Zuschnittsrahmen mit der Maus und festen Seitenverhältnissen.
  - Arbeitsbereich: Suche auch nach Fenster, App, URL und Untertiteln; Favoriten (werden nie aufgeräumt); ein gemeinsamer F2-Dialog, Tags für mehrere Aufnahmen; Miniaturen werden zwischengespeichert und nachgeladen.
  - Kommandozeile: Richtlinien gelten auch hier; Videoaufnahme stoppt den Recorder bei Fehlern sicher.

- Vier Editor-Eckgriffe, proportionale Stempelskalierung, Gruppen-Skalierung, separat verschiebbarer Sprechblasenzeiger.
- Textglühen/-schatten, sperrbare Lupenquelle, lokale Stempelbibliothek und persistente Standardstile je Werkzeug.
- Video-Ausgabe-Zoom: Strg+Alt+Pfeil auf/ab, Strg+Alt+Pos1 zum Rücksetzen. Zoomfokus wird an der Mausposition festgehalten. Die Aufnahme bleibt unverändert als Rohfassung erhalten; Zoom wird beim Speichern eingerechnet. Eine vergrößerte Live-Ansicht ist noch offen.
- Webcam-Größe, Form, Position und Spiegelung. Kein Zugriff auf Kamera oder Mikrofon in den automatisierten Tests.
- Manuelle Live-Scrollserie: Bereich auswählen, selbst scrollen, Strg+Alt+F7 für ein weiteres Bild, Strg+Alt+F8 zum Zusammensetzen. Rohbilder und Wiederherstellungsmanifest bleiben erhalten.
- Vertikale/horizontale Scrollbilder mit konfigurierbaren festen Kopf-/Fuß- bzw. Seitenleisten. Native Zielprogramme noch prüfen.
- Pixelgenaue Auswahl: beim Loslassen Umschalt halten, Pfeile ändern Größe, Strg+Pfeile verschieben, Enter übernimmt.
- Lokaler Austauschordner mit Hashprüfung, Versionssicherung und Konfliktkopien. Keine Netzwerkzugriffe und kein automatischer Löschabgleich.
- Lokale ungesendete EML-Entwürfe, mehrere Farbpipettenformate, Medieninformationen und erweiterte Tastenkürzel/CLI-Aktionen.
- Schrittnummern wahlweise als 1/2/3, A/B/C oder I/II/III.
- Umgesetzt (alles per Test geprüft, nicht an echter Hardware):
  - Video speichern als MP4, MKV (AV1), WebM, MOV, AVI, WMV, OGV, GIF, animiertes PNG/WebP oder HTML5-Seite, mit Größenvorgaben (Fit Max/Max/Exact), Qualität, Bildern pro Sekunde und Wiederholungen.
  - Arbeitsbereich: Schnellansicht (Leertaste), Stapelexport mit Größe/Effekten, verlustfreies Neu-Komprimieren, Auto-Umbenennen, Sammlungen umbenennen/sortieren/löschen/mit Symbol, Aufnahmen per Ziehen verschieben, „Speichern unter“ auch als PDF und Word.
  - Editor: beliebige Bilder öffnen (Strg+O, Kommandozeile), Vollbild F11, Magnet-Ausrichtung, Strg+Plus/Minus, Alt = Farbe unter der Maus, Strg = Quadrat, Auto-Speichern, Strg+Alt bei „Kopieren & Schließen“ für alle Editoren.
  - Aufnahme: Zoom per Mausrad sowie H im Aufnahmemodus, Titelmuster, Standardformat, „nur Zwischenablage“, automatische Größe/Rahmen/Schatten/Wasserzeichen und Druck, Druckränder, Löschbestätigung.
  - Video: feste Aufnahmebereiche, Klickring, Höchstdauer. Kommandozeile: `capture --screenshotregion --rect X;Y;W;H --filepath Datei`, `videos --merge A B`.
  - Außerdem: Aufnahmebereich vor dem Start mit Griffen verschieben/skalieren und Standardgrößen wählen, grüner Live-Rahmen beim Video-Zoom, Bildschirm unter dem Mauszeiger, JPEG XL, Rauschfilter/Zeitversatz/Balance für den Ton, Polygon offen/Alt geschlossen, Alt-Ziehen bewegt nur den Sprechblasentext, Alt+Umschalt schließt alle Editoren, Drehen rechts/links, Kommandozeile `capture --videoregion --delay`, `--newinstance`, `--multiinstances`, `--policies` (ADMX/ADML/REG) und Registry-Richtlinien.
  - Noch offen: Browser-Erweiterung, Aufnahmekontext (URL), M im Aufnahmemodus wirkt erst ab der nächsten Aufnahme; alles Hardware-Abhängige (HDR, mehrere Monitore, Webcam, Android, Omarchy, echte Tonaufnahme) ist weiter ungeprüft.
- Fehler behoben (28.10.2026): Nach dem Zuschneiden zeigte der Editor weiter das ganze Bild, nur die Ausgabe war richtig. Jetzt wird der Bereich außerhalb ausgeblendet, die Ansicht passt sich dem Ausschnitt an und die Größenanzeige stimmt. Rückgängig bringt alles zurück.
- Fehler behoben (30.10.2026): Das Polygon-Werkzeug zeigte beim Klicken nichts an und lieferte eine offene Linie. Jetzt gibt es eine Live-Vorschau, Doppelklick, Rechtsklick oder Enter beendet und schließt die Figur, Alt lässt sie offen, Esc bricht ab.
- Autostart: Einstellungen → „Beim Anmelden automatisch starten“. Die App startet dann ohne Fenster im Tray (`--minimized`). Es wird nur ein Eintrag für den aktuellen Benutzer angelegt (Windows: Run-Schlüssel, Linux: ~/.config/autostart). Wird der Ordner verschoben, passt sich der Eintrag beim nächsten Start an. Echter Neustart noch nicht geprüft.
- Fehler behoben (28.10.2026): Videoaufnahme brach unter Windows mit „Failed to set value '.1' for option 'stats_period'“ ab; ffmpeg bekommt jetzt `0.1`. Echte Bildschirmaufnahme am Gerät noch nicht von mir geprüft.
- Entfernt auf Wunsch (28.10.2026): „Text erkennen“ im Arbeitsbereich, „Sensible Daten erkennen“ im Editor und die OCR-Spracheinstellung, weil die Texterkennung im Test nicht funktionierte. Manuelles Schwärzen bleibt.
- Gemischte Textformatierung in einem Textfeld: Doppelklick öffnet einen Editor mit Fett, Kursiv, Unterstrichen, Farbe und Größe für die Auswahl. Die Formatierung bleibt im Projekt erhalten. Im Test ohne Systemschriften sind Fett/Kursiv nur über das Textdokument geprüft.
- Design überarbeitet: Editor-Befehlsleiste neu geordnet mit neuem „Kopieren & Schließen“ (Markierungen sichern, Bild in die Zwischenablage, Editor schließen); kleinere Miniaturen im Arbeitsbereich, damit mehrere pro Zeile passen. Vergleich anhand gerenderter Fenster mit den Handbuchabbildungen; kein Pixelvergleich.
- Editor: eigene Schaltflächen „Leinwand“ (Außenabstand, Hintergrund) und „Rahmen“ (Breite, Farbe, Eckenradius) sowie „Vorheriges/Nächstes Bild“ mit Bild auf/ab im Arbeitsbereich.
- Neue Projektendung .screenkaffee.json; alte .prism.json-Projekte bleiben kompatibel.
- Update (29.10.2026):
  - **Kompaktes Design**: Kompakte Kopfleiste im Arbeitsbereich mit F10-Umschalter für die Sammlungsleiste, volle Breite für das Miniatur-Raster (`#4c4c4c`) mit weiß gerahmten Vorschaubildern, Schlagschatten und Play-/Dokument-Overlays, blauem Auswahlrahmen (`#1e70bf`), Direktfilter durch Tippen/Esc und Rechtsklick-Kontextmenü; untere Aktionsleiste mit linksbündigen farbigen Rundschaltflächen und rechtsbündigem „Schließen“. Im Editor grüne Schaltfläche „Kopieren & Schließen“, eigene Symbole für Leinwand/Rahmen/Drehen/Vor/Zurück/100 %, runde Werkzeugknöpfe links und dauerhafte Größenanzeige links in der Statusleiste.
  - **Editor & Effekte**: Schlagschatten für alle Zeichenobjekte (`shadow_radius`, `shadow_offset`, `shadow_color`), `Strg+Ziehen` zum Duplizieren von Markierungen, `Alt+Ziehen` auf Lupe (verschiebt Glas bei fester Quelle), `Strg+Alt` beim Rechteck (deckt Hintergrund mit Hintergrundfarbe ab), „Originalgröße wiederherstellen“ für Stempel, Automatische Leinwandgröße (`auto_canvas`), Ausschneiden mit optionaler Lücke und Risskante (`gap`, `torn`), `Alt+Umschalt` bei Vorheriges/Nächstes Bild löscht das aktuelle Bild.
  - **Aufnahme, Video, Dokumente & Austausch**: `M` im Aufnahmemodus schaltet den Mauszeiger live im Standbild ein/aus, Pfeiltasten verschieben den Zeiger vor Auswahlbeginn pixelweise; Aufnahmekontext (`capture_context`: Fenstertitel, Programm, URL sowie `{window}`/`{app}`/`{url}` im Titelmuster); `LiveZoomPreview`-Vorschaufenster beim Video-Zoom; `Shift+Ziehen` exportiert und importiert `.screenkaffee.json`-Projektbündel; Dokument-Generator unterstützt Seitenumbruch, Bild-vor-Text, Dateiname als Titel, `Template.docx`, GIF-Anzeigedauer und `Entf`-Taste; lokaler Ordnerabgleich unterstützt optionalen Löschabgleich (`propagate_deletions`) in den lokalen Papierkorb; `--newinstance`/`--multiinstances`-Steuerkanal automatisch getestet.

## Bisherige Prüfungen

- 271 automatisierte Tests bestanden (mit ffprobe im PATH; ohne schlägt nur `test_subtitle_embedding_creates_a_real_mp4_track` fehl). Funktionsliste: 58 implementiert, 17 teilweise, 1 fehlend, 7 ausgeschlossen.
- Nativer Windows-Farbtest auf SDR-Anzeige exakt bestanden; Windows-HDR-Aufnahme am echten HDR-Display erfolgreich abgenommen (02.11.2026). Linux-/Omarchy-HDR bleibt offen.


- Automatisierte Tests: HDR-SDR-Weißnormierung, exakter Roundtrip aller 256
  SDR-Graustufen, Highlight-Kurve, NaN/Inf, ICC-PNG, Projekt-/Undo-/Crop-/Schwärzungs-
  Roundtrip, Datenbank, Stitching, Dokumentstruktur und Workspace-Oberfläche.
- Native Windows-Aufnahme eines synthetischen Farbtests: Schwarz, Weiß, Grau,
  Blau, Orange und Grün werden per WGC FP16 aufgenommen und gegen Sollwerte
  verglichen. Der Ergebnisbericht enthält keine Bildschirmbilder.
- Oberfläche und Editor werden als Testbilder gerendert und visuell geprüft.
- Fehlende Abnahme: echter HDR-Monitor, veränderter Windows-SDR-Weißpegel,
  mehrere GPUs/Monitore/DPI, Omarchy Wayland und seine HDR-Einstellungen.

## Omarchy-Abnahme

1. Archiv entpacken und `./screenkaffee` starten.
2. `./screenkaffee --diagnose` erzeugt `data/diagnostics.json` ohne Bildinhalt.
3. Bereich, aktives Fenster, Monitor, Zwischenablage und Projekt-Wiederöffnung prüfen.
4. Dieselbe SDR-Farbtafel einmal mit HDR aus und einmal mit HDR an aufnehmen.
   PNGs in einem farbverwalteten Viewer vergleichen, insbesondere Weiß und Hauttöne.
5. Bei Abweichungen die genaue Omarchy-/Hyprland-Version und Monitor-HDR-Konfiguration
   festhalten. Bis dahin ist Linux-HDR ausdrücklich nicht als abgenommen markiert.
