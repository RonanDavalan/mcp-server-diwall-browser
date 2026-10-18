# mcp-server-diwall-browser

[English](../../README.md) | [Français](../fr-FR/README.md) | **Deutsch** | [Español](../es-ES/README.md)

Ein MCP-Server, der einem Sprachmodell die Augen und Hände eines Bedieners im eigenen Browser des Bedieners
gibt, über das Chrome DevTools Protocol: Das Modell sieht denselben Screenshot und denselben
Barrierefreiheitsbaum wie der Bediener und handelt in derselben angemeldeten Sitzung. Es braucht einen Browser
der Chromium-Familie, den der Bediener mit einem eigenen Profil und einem an die Loopback-Schnittstelle
gebundenen Debugging-Port startet, sowie ein verschlüsseltes Verzeichnis (gocryptfs) für Zugangsdaten, Aufnahmen
und Downloads.

## An einen MCP-Client anbinden

Der Server spricht MCP über Standardeingabe und Standardausgabe. Mit Claude Code:

```
claude mcp add --scope user diwall-browser \
  -e MCP_DIWALL_BROWSER_PROFILE=~/.config/mcp-server-diwall-browser/profile.json \
  -- ~/mcp-server-diwall-browser/.venv/bin/python -m mcp_server_diwall_browser
```

Für einen anderen MCP-Client der allgemeine Block `mcpServers`:

```json
{
  "mcpServers": {
    "diwall-browser": {
      "command": "~/mcp-server-diwall-browser/.venv/bin/python",
      "args": ["-m", "mcp_server_diwall_browser"],
      "env": {
        "MCP_DIWALL_BROWSER_PROFILE": "~/.config/mcp-server-diwall-browser/profile.json"
      }
    }
  }
}
```

Passen Sie `~/mcp-server-diwall-browser` an den Ort an, an dem Sie das Repository geklont haben, und den
Profilpfad an den Ort, an dem Sie Ihr Bedienerprofil geschrieben haben. Die Variable kann entfallen, wenn das
Bedienerprofil an seinem üblichen Platz liegt, `~/.config/mcp-server-diwall-browser/profile.json`. Die Form des
Befehls `claude mcp add` wurde mit `claude mcp add --help` geprüft und dann vom Bediener unter Debian ausgeführt:
`claude mcp list` zeigte den Server als verbunden. Die Tests starten den Server genau wie diese beiden Blöcke:
`python -m mcp_server_diwall_browser` mit gesetzter `MCP_DIWALL_BROWSER_PROFILE`.

Alles, was davor liegt (Installieren, den Browser starten, das verschlüsselte Verzeichnis öffnen, das
Bedienerprofil schreiben), steht in [docs/OPERATOR_GUIDE.md](../OPERATOR_GUIDE.md). Wie es funktioniert und
warum, steht in [docs/ARCHITECTURE.md](../ARCHITECTURE.md).

## Was das Modell sieht

![Die Demoseite, wie das Modell sie erhält](../images/observe-demo.png)

Das Bild oben wurde von `scripts/smoke_test.py` aus der lokalen Demoseite (`docs/demo/index.html`) erzeugt. Die
nummerierten Marken zeichnet der Server: Eine Aktion benennt ein Element über seine Marke. Das Passwortfeld, das
die Seite selbst ausgefüllt hat, wird durch ein ausgefülltes Rechteck verdeckt, und sein Wert wird dem Text
vorenthalten, den das Modell erhält (`redacted_fields` ist 1).

## Was getestet ist, was nicht

Getestet:

- Die vollständige Suite läuft in der Testumgebung dieses Repositorys (Ubuntu 24.04), mit Python 3.13.14 und den
  Abhängigkeiten aus `requirements.lock`: ohne Browser bestehen 1654 Tests, und die 532, die einen Browser
  brauchen, sind abgewählt (als anderer Benutzer als root wird einer der 1654 übersprungen: ein Test, der nur als
  root läuft); mit Chromium 141.0.7390.37 (ohne Oberfläche, leeres temporäres Profil) bestehen die 532
  Browser-Tests, ohne erwarteten Fehlschlag.
- Download-Tests: Zwei scheiterten zufällig, 7- und 8-mal in je 30 Einzelläufen, weil der Server die
  Download-Einstellung schloss, bevor der Browser entschieden hatte, was er mit dem Download tut. Nach der
  Änderung scheiterten sie 3- und 1-mal in je 130 Einzelläufen; die 3 waren ein Lesen, im Test selbst, einer
  Datei, die der Browser umbenennt, was nun ein einziger Helfer ist, der die Umbenennung ignoriert. Seither
  scheiterte jeder der beiden einzeln 0-mal in 200 Läufen, zweimal hintereinander. Ein dritter Test, der die
  Einstellung sofort schloss und erwartete, dass ein später Download abgelehnt wird, scheiterte einzeln 4-mal in
  200 Läufen: Der echte Browser kann entscheiden, bevor der Server die Einstellung schließt, daher wird der Test
  durch Tests an einem simulierten Browser ersetzt. Die Messungen und was bleibt, stehen in `docs/JOURNAL.md`.
- Der Bediener hat eine frühere Revision unter Debian (Python 3.13.5) mit Chrome 154.0.8037.97 wiederholt: ohne
  Browser bestanden 1450 und 1 wurde übersprungen (ein Test, der nur als root läuft); mit ihm bestanden 119, und
  6 scheiterten wie erwartet, die sechs des Seitenzooms, seither geschlossen; die beiden Download-Tests einzeln,
  0 Fehlschläge in je 40 Läufen. Der Bediener wiederholte dann `919ed8c` (die Revision vor den Klick-Tests) mit
  demselben Browser: ohne Browser bestanden 1546, 1 wurde übersprungen und 188 abgewählt; die drei Zoom- und
  Skalierungsdateien, 125 bestanden; alle 188 Browser-Tests bestanden; die beiden Download-Tests und die beiden
  Tests, die den instabilen ersetzen, einzeln, 0 Fehlschläge in je 40 Läufen. Die Klick-Tests wurden damit nicht
  wiederholt.
- `scripts/smoke_test.py` liefert 0 gegen Chromium 141 und 3, wenn kein Browser gefunden wird.
- Die Marken und die Masken bei einer Bildschirmskalierung von 1, 1,5 und 2, auf einem Chromium 141 ohne
  Oberfläche: Der Rahmen jeder sichtbaren Marke liegt innerhalb von zwei Pixeln des Rahmens, den der Browser
  selbst angibt, jedes Pixel des Passwortfelds der Demoseite liegt unter seiner Maske, und die Maske reicht nicht
  mehr als zwei Pixel darüber hinaus (`tests/test_scale_integration.py`). Vor der Korrektur deckte eine Maske ihr
  Feld bei einer Skalierung über 1 nicht ab. Ein echter Bildschirm bei 1,5 wurde nicht gemessen.
- Der Seitenzoom, bei 100, 125, 150 und 200 % und bei Bildschirmskalierungen von 1 und 1,5, auf einem Chromium
  141 ohne Oberfläche, dessen leeres Profil den Zoom als Standard hält: Das Bild, das das Modell erhält, ist die
  ganze Ansicht, das Passwortfeld der Demoseite liegt unter seiner Maske, ebenso das Feld einer Seite, die so
  gescrollt ist, dass es nahe am unteren Rand oder in der Mitte der Ansicht liegt; die Maske reicht nicht mehr als
  zwei Pixel über das Feld hinaus, und in den 16 gemessenen Platzierungen lag keine Kante einer Maske innerhalb
  ihres Felds (`tests/test_zoom_integration.py`, `tests/test_zoom_scrolled_integration.py`). Vor der Korrektur
  waren bei 125, 150 und 200 % 0 % des Felds bedeckt, und `capture` meldete `ok`. Kein Bild wird veröffentlicht
  (`capture` meldet `unavailable`, der Baum wird weiterhin geliefert), wenn der Browser keinen brauchbaren Zoom
  liefert, wenn sich der Zoom während der Aufnahme ändert oder wenn das Bild auf einer der Achsen mehr als zwei
  Pixel von der Größe der Ansicht abweicht (`tests/test_perception_zoom.py`, das keinen Browser braucht). Unter
  Chrome 154 maß der Bediener die Größe des zugeschnittenen Bilds, wie der Browser sie liest (die Ansicht bei
  jedem Zoom, bei 125 % ein Pixel zu wenig in der Höhe); die Zoom-Tests wurden später damit wiederholt (unten).
- Ein Klick des Servers trifft sein Ziel bei Seitenzooms von 100, 125, 150 und 200 % und Bildschirmskalierungen
  von 1 und 1,5, auf einem Chromium 141 ohne Oberfläche, dessen leeres Profil den Zoom als Standard hält. Der
  Klick ist der des Servers (`Actions.click`, mit seiner Sperre und seinen Grenzen); die Testseite zählt, was
  jeder ihrer Buttons empfängt, und eine getrennte Sitzung liest die Zähler. Bei jeder der acht Kombinationen
  erhielten ein Button von 120 mal 32 CSS-Pixeln, ein Button von 16 mal 16 zwischen zwei Attrappen im Abstand von
  4 Pixeln, ein Button zweieinhalb Ansichten weiter unten und der große Button, nachdem die Seite so gescrollt
  wurde, dass er in der Mitte der Ansicht liegt, genau einen Klick, keine Attrappe erhielt einen und keiner fiel
  außerhalb eines Buttons. Auf einer in beide Richtungen gescrollten Seite trifft der Klick den in die Mitte der
  Ansicht gebrachten Button, und ein Button, der von einem an der Ansicht festen Banner verdeckt ist, wird
  abgelehnt (`element_obscured`), ohne dass etwas geklickt wird (`tests/test_click_integration.py`, 264 Tests;
  `tests/test_click_targets_page.py`, 51, und `tests/test_click_lookup_point.py`, 20, brauchen keinen Browser).
  Dieselbe Prüfung lehnt einen Klick ab, der an den mit dem Zoom multiplizierten, durch ihn geteilten oder mit
  der Bildschirmskalierung multiplizierten Punkt des Rahmens gesendet wird (64 von 64 solchen Klicks erreichten
  keinen Button), und einen Server, der den Browser nach dem Punkt der Ansicht fragt. Vor der Korrektur
  scheiterten 20 der 32 Versuche auf der ersten Seite, die mit Scrollen, und ein vom Banner verdeckter Button
  wurde angenommen und das Banner geklickt, bei den acht Kombinationen: Der Browser beantwortet die
  Knotensuche für einen Punkt des Dokuments, und der Klick gab ihm einen Punkt der Ansicht. Die Messungen stehen
  in `docs/JOURNAL.md`.
- Ein Klick auf eine Seite, die sich bewegt, sobald sich der Zeiger zum ersten Mal bewegt (ein Menü, das sich beim
  Überfahren öffnet, ein Banner, das hereingleitet). Die Testseite verschiebt jedes Ziel bei der ersten
  Mausbewegung um 60 CSS-Pixel nach unten und setzt einen Eindringling-Button dorthin, wo das Ziel war. Bei jeder
  der acht Kombinationen aus Seitenzoom und Bildschirmskalierung trifft für jeden von drei Buttons (einen großen,
  einen von 16 mal 16 zwischen zwei Attrappen, einen zweieinhalb Ansichten weiter unten) ein Klick des Servers
  entweder einmal das Ziel oder wird als verdeckt abgelehnt (`element_obscured`), ohne dass etwas geklickt wird,
  und in den 24 Versuchen wird er jedes Mal abgelehnt (`tests/test_click_shifting_integration.py`, 80 Tests;
  `tests/test_click_shifting_page.py`, 30, und `tests/test_click_moving_page.py`, 10, brauchen keinen Browser).
  Vor der Änderung lieferten dieselben 24 Versuche keinen Fehler, und der Klick traf den Eindringling. Nach einer
  Ablehnung trifft ein Klick auf eine neue Beobachtung. Der Server fragt den Browser nun nach der Bewegung und vor
  dem Drücken jedes Mal erneut, was unter dem Punkt liegt, sodass auch ein Ziel abgelehnt wird, das an seiner
  Stelle von einem bei der ersten Bewegung angezeigten Button verdeckt ist
  (`tests/test_click_covering_integration.py`, 3 Tests, auf Chrome 154.0.8037.97). Die obigen 24 Versuche wurden
  auf Chromium 141.0.7390.37 gemessen. Die Messungen, und die einer Seite, die ohne Halt scrollt, stehen in
  `docs/JOURNAL.md`.
- Eine dichte Seite mit einem Einwilligungsbanner, das über ihrem unteren Teil festsitzt (643 Dinge zum Klicken):
  seine drei Buttons sind markiert und im Baum zuerst aufgelistet, die Elemente darunter sagen `covered`, und ein
  Klick auf „Ablehnen“ über seine Marke entfernt das Banner (`tests/test_ranking_integration.py`).
- Der Befehl, der eine alte Zugangsdatei übernimmt (siehe Bedienerhandbuch): an Dateien der Testsuite in einem
  simulierten Volume, mit einer getesteten Ablehnung für jeden Ausgang und ohne einen Wert in irgendetwas, das er
  ausgibt. Er wurde an keinem echten gocryptfs-Volume ausprobiert.
- Ein echtes gocryptfs-2.5.1-Volume, ein Wegwerf-Volume, für die Prüfung angelegt (nicht das des Bedieners),
  unter Debian eingehängt: `status` meldete `mounted` und `profile_encrypted: true`, und der Server schrieb sein
  Journal hinein und legte `downloads/` darin an. Opera 136 (Chromium 152), gestartet mit seinem Profil in diesem
  Volume und einem Debugging-Port auf der Loopback-Schnittstelle, wurde angebunden; der Server öffnete eine Seite
  eines öffentlichen Einkaufsportals, füllte dessen Suchfeld, drückte die Eingabetaste und beobachtete die
  Ergebnisse. Der getippte Text erscheint nicht im Journal.
- Claude Code, aus einem Desktop-Terminal gestartet: `status` über Claude Code meldete
  `confirmation_channel: native`, die Anzeige erreichte also den Server.
- Der Serverprozess über Standardeingabe und Standardausgabe: Er schreibt nichts außer dem Protokoll auf seine
  Standardausgabe, hält keinen lauschenden Socket und läuft mit einer Core-Datei-Grenze von null.

Nicht getestet:

- Ein echtes Fenster von `kdialog` oder `zenity`: Die Tests verwenden an dessen Stelle Attrappenprogramme.
- Das eigene gocryptfs-Volume des Bedieners: Die Tests simulieren die Mount-Tabelle und das Verzeichnis, und die
  obige Prüfung verwendete ein Wegwerf-Volume.
- Brave und Google-Chrome-Versionen außer der, die der Bediener verwendet hat.
- Die über einen Desktop-MCP-Client erfragte Bestätigung (Elicitation): nur mit dem Client des MCP-SDK getestet.
- Chrome 154 mit seinem Profil im eigenen verschlüsselten Verzeichnis des Bedieners.
- Der Seitenzoom mit Strg + Taste eines Browsers, der nicht ohne Oberfläche läuft. Das Protokoll hat keinen Befehl
  für einen Zoom, daher schreiben die Tests den Standardzoom eines leeren Profils; ob der Browser die beiden Arten
  gleich behandelt, ist nicht bestätigt. Ein Bildschirm, dessen Skalierung tatsächlich 1,5 beträgt, wurde ebenfalls
  nicht gemessen; die Klick-Tests haben dieselben beiden Grenzen.
- Die Klick-Tests auf Chrome 154. Die Korrektur liest eine Eigenschaft der Knotensuche des Browsers, auf Chromium
  141 gemessen (sie antwortet für einen Punkt des Dokuments, nicht der Ansicht): Weicht sie in einer anderen
  Version ab, scheitern dort die Tests eines Klicks auf eine gescrollte Seite. Ein Klick auf ein Element in einem
  Frame einer gescrollten Seite wurde nicht gemessen.
- Eine Seite, die sich nach der zweiten Suche eines Klicks und vor dem Drücken bewegt. Auf einer Seite, die um 1000
  CSS-Pixel pro Sekunde scrollt, lieferten 6 von 144 Klicks keinen Fehler und landeten nirgends; keiner landete auf
  einem anderen Button (Chrome 154.0.8037.97, `docs/JOURNAL.md`).
- Eine Ablehnung im Bestätigungsfenster, von einer Person gegeben.

Bekannte Grenzen:

- Solange der Server am Browser hängt, werden Downloads in allen Tabs abgelehnt, auch in denen des Bedieners,
  außer im kurzen Fenster um einen Klick oder eine Eingabetaste des Servers. Um selbst eine Datei herunterzuladen,
  halten Sie den Server oder den MCP-Client an; Downloads funktionieren wieder, sobald die Verbindung des Servers
  endet.
- Ein Download, den eine Seite mehr als eine halbe Sekunde nach dem Ende eines Ladevorgangs startet, wird
  abgelehnt.
- Das Annehmen eines Dialogs, dessen Meldung eine Zahlung, eine Löschung oder eine Änderung eines Geheimnisses
  verlangt, wird immer abgelehnt: Solange ein Dialog offen ist, kann keine Aufnahme gemacht werden. Der Bediener
  beantwortet einen solchen Dialog.
- Die Sichtbarkeit ist ungefähr: Der Server berechnet weder Kontrast noch das Abschneiden durch `overflow`, sodass
  Text, den ein Mensch nicht lesen kann, das Modell dennoch erreichen kann.
- Eine Beobachtung enthält höchstens 150 Marken und 400 Baumzeilen (`marks_truncated`, `tree_truncated`). Darüber
  hinaus behält der Server zuerst, was in einer Ebene über der Seite liegt (ein Dialog, ein modaler Knoten, ein
  fester oder klebender Knoten, der über sichtbarem Inhalt liegt, wie ein Cookie-Banner), im Baum unter einer
  `overlay`-Zeile aufgeführt, dann was sichtbar und frei ist, dann was sichtbar unter einer Ebene liegt
  (`covered: true`, und ein Klick darauf wird abgelehnt), dann den Rest. Ein Banner, das nichts Sichtbares
  überlappt, ist keine Ebene und kann dennoch abgeschnitten werden.
- Der Inhalt von Frames wird nie gelesen; Frames werden im Bild verdeckt.
- Was der Anbieter des Modells von dem behält, was er erhält, ist von diesem Projekt nicht bestätigt.

## Danksagungen

mcp-server-diwall-browser entsteht in einer asymmetrischen Zusammenarbeit von Mensch und LLM. Die Rollen sind so
festgehalten, wie sie tatsächlich ausgeübt wurden; die vollständige Liste steht in
[CONTRIBUTORS.md](../../CONTRIBUTORS.md).

- **Ronan Davalan**: Architekt und Schiedsrichter. Vision, Sicherheitsanforderungen, Entscheidungen, Abnahme und Tests.
- **Claude Code (Anthropic)**: Systemingenieur und Hauptentwickler. Umsetzung des Servers, Tests, Dokumentation und Arbeitsrahmen.
- **Claude Cloud (Anthropic)**: schrieb einen Teil des Codes auf Branches, nach Vorgaben, vor jeder Zusammenführung geprüft.
- **Gemini (Google)**: Architekturberater und Synthetisierer; gab dem Projekt seinen Namen, **Diwall**, „Deich“ auf Bretonisch.
- **Space Bunny Free (opencode)**: Aufräumen und Beschneiden des Arbeitsrahmens des Projekts zu Beginn des Projekts.
- **Lokale Bildverarbeitung**: Qwen3-VL (Alibaba), über Ollama ausgeführt, für die optionale visuelle Ortung und den optionalen Vergleich.

Lizenz: noch nicht gewählt.
