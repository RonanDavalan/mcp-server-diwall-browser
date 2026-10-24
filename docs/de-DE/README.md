# mcp-server-diwall-browser

[English](../../README.md) | [Français](../fr-FR/README.md) | **Deutsch** | [Español](../es-ES/README.md)

![Das Auge von Diwall MCP beleuchtet das Web: Alles, worauf das Modell einwirken kann, erhält einen nummerierten Rahmen, und das Passwortfeld bleibt schwarz](../images/scene-diwall-mcp.png)

Ein MCP-Server, der einem Sprachmodell die Augen und Hände eines Bedieners im eigenen Browser des Bedieners
gibt, über das Chrome DevTools Protocol: Das Modell sieht denselben Screenshot und denselben
Barrierefreiheitsbaum wie der Bediener und handelt in derselben angemeldeten Sitzung. Es braucht einen Browser
der Chromium-Familie, den der Bediener mit einem eigenen Profil und einem an die Loopback-Schnittstelle
gebundenen Debugging-Port startet, sowie ein verschlüsseltes Verzeichnis (gocryptfs) für Zugangsdaten, Aufnahmen
und Downloads.

## Vor dem ersten Gebrauch

Der Server arbeitet nicht ohne verschlüsseltes Verzeichnis: Das Profil des Browsers, an den er sich anbindet, hält Ihre
angemeldeten Sitzungen, und sie dürfen nicht unverschlüsselt auf der Platte liegen. Einen Modus ohne Verschlüsselung gibt
es nicht. Nach der Installation lautet der erste Befehl, den Sie selbst in einem Terminal ausführen,
`mcp-server-diwall-browser-setup`: Er liest Ihren Rechner und endet mit dem nächsten Befehl, den Sie eingeben.

- `mcp-server-diwall-browser-setup create --temporary` legt für einen Versuch ein Volume im Arbeitsspeicher an. Es zu
  schließen, den Rechner auszuschalten oder Ihre Sitzung zu beenden zerstört es, und mit ihm die Sitzungen der Websites, die
  Sie im Browser geöffnet haben, das Audit-Journal und die Aufnahmen.
- `mcp-server-diwall-browser-setup create` legt ein dauerhaftes Volume an.
- `mcp-server-diwall-browser-setup open`, `browser` und `close` öffnen das Volume, starten den Browser auf seinem Profil
  und beenden beides.

Das Passwort geben Sie selbst an der Eingabeaufforderung von `gocryptfs` ein; der Assistent liest es nie. Bei Ihren Websites
melden Sie sich selbst an, in dem Browser, den er startet. Haben Sie den Server zu Ihrem MCP-Client hinzugefügt, starten Sie
den Client neu: Eine bereits offene Sitzung sieht ihn nicht. Die Schritte, die Ablehnungen und der manuelle Weg stehen im
Abschnitt „Erster Start“ von [docs/de-DE/OPERATOR_GUIDE.md](OPERATOR_GUIDE.md).

## Welche Websites das Modell liest

Die Liste der Websites im Profil des Bedieners begrenzt, was das Modell liest und worauf es handelt, nie, wohin es navigiert. Der Schlüssel
`origin_policy` wählt, was der Server mit einem Ursprung tut, den die Liste nicht enthält:

- `strict` (der Standard, auch wenn der Schlüssel fehlt): Der Ursprung wird abgelehnt, und nur der Ursprung wird zurückgegeben.
- `discovery`: Der Server fragt Sie in einem Fenster, einmal je Ursprung: `Refuser` (die Standardantwort), `Cette séance` oder
  `Ajouter à la liste`, was genau diesen Ursprung in Ihr Profil schreibt.
- `open`: Jede öffentliche Website wird gelesen und darauf gehandelt, und ihr Inhalt geht an den Anbieter des Modells; lokale Adressen und Namen
  bleiben auf der Liste. Es ist die freieste Richtlinie und die riskanteste, und die Wahl liegt bei Ihnen.

Zahlungen, unwiderrufliche Löschungen und Änderungen eines Geheimnisses behalten ihre Bestätigung, und Zugangsdaten werden in allen drei
Richtlinien nur auf dem genauen Ursprung getippt, an den sie gebunden sind. Die Einzelheiten stehen in Abschnitt 5 von
[docs/OPERATOR_GUIDE.md](OPERATOR_GUIDE.md).

## Installieren

Unter Linux wird der Server als natives Paket geliefert. Laden Sie das Paket Ihres Systems von der Seite
[Releases](https://github.com/RonanDavalan/mcp-server-diwall-browser/releases) des Repositorys oder aus der [Installationsanleitung der Website](https://mcp-diwall.davalan.fr/de/anleitungen/installieren/) herunter und installieren Sie es
aus dem Ordner, der es enthält:

| System | Befehl |
|---|---|
| Debian 13, Ubuntu 24.04 und 26.04 | `sudo apt install ./mcp-server-diwall-browser_0.0.1-1_all.deb` |
| Fedora 44 | `sudo dnf install ./mcp-server-diwall-browser-0.0.1-1.fc44.noarch.rpm` |
| openSUSE Leap 16.0 | `sudo zypper install --allow-unsigned-rpm ./mcp-server-diwall-browser-0.0.1-1.leap160.noarch.rpm` |
| Arch Linux | `sudo pacman -U ./mcp-server-diwall-browser-0.0.1-1-any.pkg.tar.zst` |

Installation, Start des Servers durch ein gewöhnliches Konto und Entfernen sind auf diesen Systemen in
Containern validiert. Ein Container zeigt weder das Bestätigungsfenster noch das Einhängen des
verschlüsselten Verzeichnisses noch einen Browser. Debian 12 und Ubuntu 22.04 werden nicht unterstützt: Ihr
Python ist älter als 3.12. Die Installation braucht eine Netzwerkverbindung: Das Paket legt eine virtuelle
Python-Umgebung an und installiert darin die Abhängigkeiten in den Versionen, die das Projekt getestet hat.
Die Handbuchseite ist `man mcp-server-diwall-browser`. Zur Installation aus einem Klon des Repositorys folgen
Sie [docs/de-DE/OPERATOR_GUIDE.md](OPERATOR_GUIDE.md).

Eine 64-Bit-ARM-Maschine nimmt dasselbe `.deb`, da das Paket nicht von der Architektur abhängt: Ein Raspberry Pi 4 unter Debian 13 wurde ausprobiert (siehe „Was getestet ist, was nicht“). Der Browser ist dort das Chromium der Distribution.

## An einen MCP-Client anbinden

Der Server spricht MCP über Standardeingabe und Standardausgabe. Mit installiertem Paket lautet der Befehl
`mcp-server-diwall-browser`, und das Bedienerprofil wird an seinem üblichen Platz gelesen:

```
claude mcp add --scope user diwall-browser -- mcp-server-diwall-browser
```

Aus einem Klon des Repositorys, mit Claude Code:

```
claude mcp add --scope user diwall-browser \
  -e MCP_DIWALL_BROWSER_PROFILE="$HOME/.config/mcp-server-diwall-browser/profile.json" \
  -- "$HOME/mcp-server-diwall-browser/.venv/bin/python" -m mcp_server_diwall_browser
```

Für einen anderen MCP-Client der allgemeine Block `mcpServers`. Der Client löst `~` nicht auf:
Dieser Befehl gibt den Block mit Ihrem Home-Verzeichnis in den Pfaden aus, bereit zum Einfügen in
dessen Konfiguration:

```
cat <<EOF
{
  "mcpServers": {
    "diwall-browser": {
      "command": "$HOME/mcp-server-diwall-browser/.venv/bin/python",
      "args": ["-m", "mcp_server_diwall_browser"],
      "env": {
        "MCP_DIWALL_BROWSER_PROFILE": "$HOME/.config/mcp-server-diwall-browser/profile.json"
      }
    }
  }
}
EOF
```

Passen Sie `~/mcp-server-diwall-browser` an den Ort an, an dem Sie das Repository geklont haben, und den
Profilpfad an den Ort, an dem Sie Ihr Bedienerprofil geschrieben haben. Die Variable kann entfallen, wenn das
Bedienerprofil an seinem üblichen Platz liegt, `~/.config/mcp-server-diwall-browser/profile.json`. Die Form des
Befehls `claude mcp add` wurde mit `claude mcp add --help` geprüft und dann vom Bediener unter Debian ausgeführt:
`claude mcp list` zeigte den Server als verbunden. Die Tests starten den Server genau wie diese beiden Blöcke:
`python -m mcp_server_diwall_browser` mit gesetzter `MCP_DIWALL_BROWSER_PROFILE`.

Alles, was davor liegt (Installieren, den Browser starten, das verschlüsselte Verzeichnis öffnen, das
Bedienerprofil schreiben), steht in [docs/de-DE/OPERATOR_GUIDE.md](OPERATOR_GUIDE.md). Wie es funktioniert und
warum, steht in [docs/de-DE/ARCHITECTURE.md](ARCHITECTURE.md).

## Was das Modell sieht

![Die Demoseite, wie das Modell sie erhält](../images/observe-demo.png)

Das Bild oben wurde von `scripts/smoke_test.py` aus der lokalen Demoseite (`docs/demo/index.html`) erzeugt. Die
nummerierten Markierungen zeichnet der Server: Eine Aktion benennt ein Element über seine Markierung. Das Passwortfeld, das
die Seite selbst ausgefüllt hat, wird durch ein ausgefülltes Rechteck verdeckt, und sein Wert wird dem Text
vorenthalten, den das Modell erhält (`redacted_fields` ist 1).

## Was getestet ist, was nicht

Getestet:

- Die vollständige Suite unter Debian 13 mit Python 3.12.11 und den Abhängigkeiten aus `requirements.lock`: ohne
  Browser bestehen 2100 Tests, 1 wird übersprungen (ein Test, der nur als root läuft) und die 589, die einen
  Browser brauchen, sind abgewählt; mit Google Chrome 156.0.8078.47 (ohne Oberfläche, leeres temporäres Profil)
  bestehen 2689 Tests und 1 wird übersprungen, ohne erwarteten Fehlschlag.
- Download-Tests: Zwei scheiterten zufällig, 7- und 8-mal in je 30 Einzelläufen, weil der Server die
  Download-Einstellung schloss, bevor der Browser entschieden hatte, was er mit dem Download tut. Nach der
  Änderung scheiterten sie 3- und 1-mal in je 130 Einzelläufen; die 3 waren ein Lesen, im Test selbst, einer
  Datei, die der Browser umbenennt, was nun ein einziger Helfer ist, der die Umbenennung ignoriert. Seither
  scheiterte jeder der beiden einzeln 0-mal in 200 Läufen, zweimal hintereinander. Ein dritter Test, der die
  Einstellung sofort schloss und erwartete, dass ein später Download abgelehnt wird, scheiterte einzeln 4-mal in
  200 Läufen: Der echte Browser kann entscheiden, bevor der Server die Einstellung schließt, daher wird der Test
  durch Tests an einem simulierten Browser ersetzt. Die Messungen und was bleibt, stehen in `docs/JOURNAL.md`.
- `scripts/smoke_test.py` liefert 0 gegen Google Chrome 155.0.8059.84 und 3, wenn kein Browser gefunden wird.
- Ein Raspberry Pi 4 Model B mit 8 GB, Raspberry Pi OS auf Debian 13 (aarch64), Python 3.13.5 und Chromium 155.0.8059.79: das `.deb`, von `apt` installiert; der erste Start des Einrichtungsassistenten von Anfang bis Ende mit dem echten `gocryptfs` (temporäres und dauerhaftes Volume, Browser, Schließen); `scripts/smoke_test.py`, das 0 liefert; der Server, von einem echten MCP-Client gesteuert, auf einem sichtbaren Chromium mit Fenster, nicht headless, auf einem virtuellen X11-Bildschirm, wobei das native Bestätigungsfenster von `zenity` geöffnet und ablaufen gelassen wurde; danach das Paket entfernt und vollständig gelöscht, ohne verbliebene Datei. Die vollständige Suite ergab dort 2694 bestandene Tests und 1 übersprungenen in 15 Minuten (fünf davon sind Messungen dieser Maschine, die das Repository nicht enthält). In diesem Netz brauchte die erste Seite, die ein frisch gestarteter Chromium lud, 15 bis 26 Sekunden, wodurch bei einem ersten Durchlauf drei Tests scheiterten; mit vom Netz getrenntem Browser bestehen alle (Bedienerhandbuch, Abschnitt 12).
- Die Markierungen und die Masken bei einer Bildschirmskalierung von 1, 1,5 und 2, auf einem Chromium 141 ohne
  Oberfläche: Der Rahmen jeder sichtbaren Markierung liegt innerhalb von zwei Pixeln des Rahmens, den der Browser
  selbst angibt, jedes Pixel des Passwortfelds der Demoseite liegt unter seiner Maske, und die Maske reicht nicht
  mehr als zwei Pixel darüber hinaus (`tests/test_scale_integration.py`). Vor der Korrektur deckte eine Maske ihr
  Feld bei einer Skalierung über 1 nicht ab. Ein echter Bildschirm bei 1,5 wurde nicht gemessen.
- Der Seitenzoom, bei 100, 125, 150 und 200 % und bei Bildschirmskalierungen von 1 und 1,5, auf einem Chromium
  141 ohne Oberfläche, dessen leeres Profil den Zoom als Standard hält: Das Bild, das das Modell erhält, ist die
  ganze Ansicht, das Passwortfeld der Demoseite liegt unter seiner Maske, ebenso das Feld einer Seite, die so
  gescrollt ist, dass es nahe am unteren Rand oder in der Mitte der Ansicht liegt; die Maske reicht nicht mehr als
  zwei Pixel über das Feld hinaus, und in den 16 gemessenen Platzierungen lag keine Kante einer Maske innerhalb
  ihres Felds (`tests/test_zoom_integration.py`, `tests/test_zoom_scrolled_integration.py`). Vor der Korrektur
  waren bei 125, 150 und 200 % 0 % des Felds bedeckt, und `capture` meldete `ok`. Kein Bild wird veröffentlicht
  (`capture` meldet `unavailable`, der Baum wird weiterhin geliefert), wenn der Browser keinen brauchbaren Zoom
  liefert, wenn sich der Zoom während der Aufnahme ändert oder wenn das Bild auf einer der Achsen mehr als zwei
  Pixel von der Größe der Ansicht abweicht (`tests/test_perception_zoom.py`, das keinen Browser braucht). Unter
  Chrome 154 maß der Bediener die Größe des zugeschnittenen Bilds, wie der Browser sie liest (die Ansicht bei
  jedem Zoom, bei 125 % ein Pixel zu wenig in der Höhe); die Zoom-Tests wurden später damit wiederholt (unten).
- Ein Klick des Servers trifft sein Ziel bei Seitenzooms von 100, 125, 150 und 200 % und Bildschirmskalierungen
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
  Klick auf „Ablehnen“ über seine Markierung entfernt das Banner (`tests/test_ranking_integration.py`).
- Der Einrichtungsassistent `mcp-server-diwall-browser-setup`: 50 Tests auf einem simulierten Rechner (Einhängetabellen,
  Paketverwaltungen, kein `/dev/fuse`, kein Terminal, ein LUKS-Volume); dann mit dem echten gocryptfs
  2.5.1 in einem Pseudoterminal, auf Wegwerf-Volumes: `create --temporary`, der Server liest `mounted`, `browser`
  (Google Chrome 155.0.8059.84 ohne Bildschirm, angebunden, `profile_encrypted: true`), `close` (kein Volume mehr im
  Arbeitsspeicher, keine Datei des Browserprofils auf der Platte), dann `create`, `close` und `open` eines dauerhaften
  Volumes; und in einem sauberen Debian-13-Container, wo `check` `sudo apt install gocryptfs fuse3` gab, was gelang.
- Der Befehl, der eine alte Zugangsdatei übernimmt (siehe Bedienerhandbuch): an Dateien der Testsuite in einem
  simulierten Volume, mit einer getesteten Ablehnung für jeden Ausgang und ohne einen Wert in irgendetwas, das er
  ausgibt. Er wurde an keinem echten gocryptfs-Volume ausprobiert.
- Ein echtes gocryptfs-2.5.1-Volume, ein Wegwerf-Volume, für die Prüfung angelegt (nicht das des Bedieners),
  unter Debian eingehängt: `status` meldete `mounted` und `profile_encrypted: true`, und der Server schrieb sein
  Journal hinein und legte `downloads/` darin an. Opera 136 (Chromium 152), gestartet mit seinem Profil in diesem
  Volume und einem Debugging-Port auf der Loopback-Schnittstelle, wurde angebunden; der Server öffnete eine Seite
  eines öffentlichen Einkaufsportals, füllte dessen Suchfeld, drückte die Eingabetaste und beobachtete die
  Ergebnisse. Der getippte Text erscheint nicht im Journal.
- Die Ursprungsrichtlinie (`strict`, `discovery`, `open`): `tests/test_origin_policy.py` und `tests/test_origin_policy_server.py` auf einem simulierten Browser und `tests/test_origin_policy_integration.py`, 12 Tests des über seinen Einstiegspunkt gestarteten Servers gegen Google Chrome 155.0.8059.84, mit Attrappenprogrammen für das Fenster: ein Portal, das auf einen zweiten Origin verweist, eine Weiterleitung auf einen unbekannten Origin, die drei Antworten des Fensters (das Profil nach `Ajouter à la liste` neu gelesen), unter `open` abgelehnte lokale Namen, eine Zahlung, die weiter ihr Fenster verlangt, und die im Profil geänderte Richtlinie ohne Neustart. Die Fenster der echten `kdialog` und `zenity` wurden in einem virtuellen Bildschirm gezeigt und durch Tastendrücke beantwortet (Eingabe und Escape geben die Ablehnung); eine Person hat sie beantwortet (der nächste Eintrag).
- Das Bestätigungsfenster, auf dem Rechner des Bedieners gezeigt und von einer Person beantwortet, mit eingehängtem eigenem gocryptfs-Volume des Bedieners und einer lokalen Zahlungsseite, in Opera
  138.0.6044.24, einmal mit `kdialog` 25.04.0 und einmal mit `zenity` 4.1.90 (der Server fand nur dieses Werkzeug):
  Bei beiden lieferte eine Ablehnung `confirmation_denied`, und die Seite erhielt nichts; eine Zustimmung führte den Klick
  aus, und die Seite erhielt eine Anfrage; ohne Antwort verfiel die Anfrage nach 90,4 Sekunden (`confirmation_expired`),
  und die Seite erhielt nichts. Das Protokoll enthält für jede der sechs Anfragen eine Zeile `requested` und eine Zeile
  mit dem Ergebnis.
- Claude Code, aus einem Desktop-Terminal gestartet: `status` über Claude Code meldete
  `confirmation_channel: native`, die Anzeige erreichte also den Server.
- Der Serverprozess über Standardeingabe und Standardausgabe: Er schreibt nichts außer dem Protokoll auf seine
  Standardausgabe, hält keinen lauschenden Socket und läuft mit einer Core-Datei-Grenze von null.

Nicht getestet:

- Eine andere 64-Bit-ARM-Maschine als dieser Raspberry Pi 4 (ein Raspberry Pi 5, eine andere Distribution), eine Wayland-Desktopsitzung und eine Person, die dort das Bestätigungsfenster beantwortet.
- Der Einrichtungsassistent auf einem Rechner mit Auslagerungsspeicher (sein Bericht ist an einer simulierten Liste
  erprobt) und sein Befehl `browser` mit Brave, Chromium oder Opera.

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
- Ein Element, das auf das Drücken statt auf den Klick reagiert und im Augenblick des Drückens von einem Skript unter den
  Zeiger gesetzt wird: Das Drücken wird geprüft, nachdem es gemacht wurde, sodass die Handler der Seite fürs Drücken
  gelaufen sind. Die Wache einer Taste und eines Textes ist ein Skript der Seite: Eine Seite, die sie erwartet, umgeht
  sie, und sie sieht die Ereignisse eines Frames nicht (Google Chrome 156.0.8078.47, `docs/OPERATOR_GUIDE.md`, Abschnitt 7).

Bekannte Grenzen:

- Das verschlüsselte Verzeichnis schützt so viel wie sein Passwort: Wer das Passwort hat, liest seinen Inhalt, und ein schwaches oder mehrfach verwendetes Passwort schützt wenig.
- Die Schutzmaßnahmen sind die, die zum Datum dieser Version gemessen wurden, mit den Tests und Messungen, die dieses Dokument nennt. Ein Modell, das neuer ist als die erprobten, kann einen Weg um sie herum finden, den kein Versuch gesehen hat.
- Solange der Server am Browser hängt, werden Downloads in allen Tabs abgelehnt, auch in denen des Bedieners,
  außer im kurzen Fenster um einen Klick oder eine Eingabetaste des Servers. Um selbst eine Datei herunterzuladen,
  halten Sie den Server oder den MCP-Client an; Downloads funktionieren wieder, sobald die Verbindung des Servers
  endet.
- Ein Download, den eine Seite mehr als eine halbe Sekunde nach dem Ende eines Ladevorgangs startet, wird
  abgelehnt.
- Das Annehmen eines `confirm`- oder `prompt`-Dialogs verlangt vom Bediener eine Bestätigung in einem Fenster, was
  immer seine Meldung sagt.
  Das Annehmen eines Dialogs, dessen Meldung eine Zahlung, eine Löschung oder eine Änderung eines Geheimnisses
  verlangt, wird immer abgelehnt: Solange ein Dialog offen ist, kann keine Aufnahme gemacht werden. Der Bediener
    beantwortet einen solchen Dialog.
- Ein Geheimnis, das eine Website in den Pfad einer Adresse schreibt, wird in den Ergebnissen so gezeigt, wie es
  geschrieben ist; nur der Wert eines Abfrageparameters, dessen Name ein Wort wie `token` enthält, und das Fragment
  werden geschwärzt.

- Die Sichtbarkeit ist ungefähr: Der Server berechnet weder Kontrast noch das Abschneiden durch `overflow`, sodass
  Text, den ein Mensch nicht lesen kann, das Modell dennoch erreichen kann.
- Eine Beobachtung enthält höchstens 150 Markierungen und 400 Baumzeilen (`marks_truncated`, `tree_truncated`). Darüber
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
- **Claude Code (Anthropic; Opus 5.5, Sonnet 5.5 und Haiku 5.5)**: Systemingenieur und Hauptentwickler. Umsetzung des Servers, Tests, Dokumentation, Arbeitsrahmen, Text und Pflege der Website des Projekts, die Szene zur ersten Veröffentlichung und ihr kurzer Clip, in Blender am Arbeitsplatz gerendert, gesteuert über LibreModular-MCP, und ein Feldbericht zum ersten Start aus dem Debian-Paket.
- **Claude Cloud (Anthropic)**: schrieb einen Teil des Codes auf Branches, nach Vorgaben, vor jeder Zusammenführung geprüft.
- **Muse Spark 1.3 Contributor (Meta, über OpenCode)**: erste Nutzung des Servers durch ein anderes Modell als Claude, auf einer lokalen Instanz einer Webanwendung, deren Feldbericht die folgenden Korrekturen geprägt hat; das Stylesheet und das Layout der Website des Projekts, unter mehreren Versuchen ausgewählt, Text und Verhalten der Seiten blieben unberührt; zwei Durchsichten der Website, mit Formulierungsvorschlägen in vier Sprachen, als Entwürfe belassen, und gefundenen Mängeln, die behoben wurden.
- **DeepSeek V4 Pro (DeepSeek, über OpenCode)**: unabhängiges Sicherheitsaudit des Tags `v0.0.1` auf einer leeren Sitzung; vier seiner sieben Befunde wurden bei der Sichtung bestätigt und behoben.
- **GLM 5.3 (Zhipu, über OpenCode)**: unabhängiges Sicherheitsaudit desselben Tags auf einer leeren Sitzung, mit Reproduktionen auf einem Browser ohne Bildschirm; drei seiner sieben Befunde wurden bei der Sichtung bestätigt und behoben.
- **DeepSeek V4.1 Flash 1017 (DeepSeek, über OpenCode)**: das 3D-Emblem „das Glas-D“, in Blender am Arbeitsplatz gezeichnet, in zwei Pupillenvarianten (eine runde Pupille und ein Mauszeiger); es ist das Auge der Szene, die die erste Version illustriert.
- **Gemini (Google)**: Architekturberater und Synthetisierer; gab dem Projekt seinen Namen, **Diwall**, „Deich“ auf Bretonisch.
- **gemini-3.5-flash (Google, über OpenCode)**: hat die Anbindung über einen eigenen Agenten getestet: Die Werkzeuge erreichen nur diesen Agenten, und seine Shell fragt, bevor sie etwas ausführt.
- **Space Bunny Free (opencode)**: das Logo des Projekts, das D mit dem runden Auge, verwendet auf der Website, in ihren Icons und auf dem Titelblatt der Handbücher; Aufräumen und Beschneiden des Arbeitsrahmens des Projekts zu Beginn des Projekts.

Lizenz: MIT, siehe [LICENSE](../../LICENSE).
