# Architektur

Warum der Server so gebaut ist, wie er gebaut ist. Wie man ihn installiert und betreibt, steht in
[OPERATOR_GUIDE.md](OPERATOR_GUIDE.md).

## Zwei Ebenen

```
das Sprachmodell, über seinen MCP-Client     liest, entscheidet, erklärt es dem Bediener
        │  MCP über Standardeingabe und Standardausgabe: 14 Werkzeuge
mcp-server-diwall-browser                    Augen und Hände: beobachtet, handelt, lehnt ab
        │  Chrome DevTools Protocol, nur auf 127.0.0.1
der Browser des Bedieners                    eigenes Profil, angemeldete Sitzungen

neben dem Server:   verschlüsseltes Verzeichnis   Zugangsdaten hinein; Journal, Aufnahmen, Downloads heraus
                    Bestätigungsfenster           kdialog oder zenity, vom Bediener beantwortet
```

Das Modell bringt das Urteil mit; der Server bringt, was das Modell allein nicht haben kann: dasselbe
Bild und denselben Barrierefreiheitsbaum, die der Bediener sieht, Gesten in der eigenen Sitzung des
Bedieners und die Ablehnungen, die nicht vom Urteil des Modells abhängen.

## Ein Schritt

1. `open_tab` oder `navigate` lädt eine Seite in einem Tab, der dem Server gehört.
2. `observe` liest die Seite: Der Server erstellt einen Snapshot, nummeriert die Elemente, auf denen er
   handeln kann, mit Markierungen, zeichnet die Markierungen und die Masken auf das Bild und gibt das
   Bild, den Baum, die Elemente und eine `snapshot_id` zurück.
3. Das Modell nennt eine Markierung dieses Snapshots in `click`, `fill`, `select_option` oder
   `press_key`.
4. Der Server rechnet die Aktion auf die Obergrenzen an, findet den Knoten des Browsers hinter der
   Markierung, prüft, dass es derselbe Knoten ist, noch in der Seite, an dem Punkt, den er berühren
   wird, und dass die Seite auf einem Ursprung liegt, den die Ursprungsrichtlinie zulässt. Die Sperre entscheidet, ob die Aktion
   gesperrt ist (Zahlung, Löschung, Änderung eines Geheimnisses). Eine gesperrte Aktion wird zuerst ins
   Journal geschrieben und aufgenommen, dann fragt das Bestätigungsfenster den Bediener; die Identität
   wird erneut geprüft, die Geste ausgeführt, der Server wartet auf den Ladevorgang, den er ausgelöst
   hat, und nimmt erneut auf.
5. Das Ergebnis verlässt den Server über die einzige Ausgabestelle, die es schwärzt und das, was der
   Server feststellt (`server`), von dem trennt, was die Seite enthält (`page_content`). Das Journal
   erhält eine Zeile.

## Module

| Modul | Rolle |
|---|---|
| `__main__` | Einstiegspunkt: Härtung, Logging auf die Standardfehlerausgabe durch die Schwärzung, der Server über die Standardströme |
| `hardening` | Core-Dateien aus, umask 077 |
| `origin` | Parsen, Normalisieren und Vergleichen von Adressen und Ursprüngen; der Subdomain-Platzhalter, ein eigener Typ |
| `output` | die einzige Ausgabestelle: Schwärzung, Umschlag, `server` und `page_content` getrennt gehalten |
| `sensitive_field` | das Prädikat, das sagt, dass ein Feld sensibel ist |
| `mount` | ob das verschlüsselte Verzeichnis wie erwartet eingehängt ist, und jeder Pfad darin |
| `credentials` | Lesen von Zugangsdatendateien, Auflösen einer für einen genauen Ursprung und das Schema, das anderer Code erfragen darf |
| `credential_import` | ein Bedienerbefehl, außerhalb des Servers: wandelt eine alte Zugangsdatendatei oder ein Verzeichnis davon in das Schema um, ohne einen Wert zu zeigen |
| `setup` | ein Bedienerprogramm, außerhalb des Servers und von keinem seiner Module importiert: liest den Rechner, legt das gocryptfs-Volume an oder öffnet es, schreibt das Bedienerprofil, startet den Browser auf seinem Profil und schließt beides; das Terminal geht an gocryptfs, das allein das Passwort liest |
| `journal` | das Prüfprotokoll, eine JSON-Zeile je Aufruf |
| `operator_profile` | Lesen des Bedienerprofils; zusammen mit `confirm` die einzigen Module des Servers, die die Umgebung lesen |
| `origin_policy` | die eine Stelle, die entscheidet, ob ein Ursprung gelesen wird und darauf gehandelt wird: die Liste, die Richtlinie `strict`, `discovery` oder `open`, das Fenster für einen Ursprung, den die Liste nicht enthält, was eine Sitzung zugelassen oder abgelehnt hat |
| `profile_watch` | die Spur einer Änderung der Ursprungslisten oder der Ursprungsrichtlinie des Profils: der Vergleich, die zuletzt gesehenen Ursprünge, der Hinweis |
| `cdp` | der DevTools-Client, seine Liste erlaubter Methoden und seine konstanten Skripte |
| `browser` | Prüfungen der Anbindung, Tabs, die dem Server gehören, Navigation, Dialoge |
| `perception` | Snapshot, Markierungen, Baum, Bild, Masken, was unter den Obergrenzen behalten wird, Warten auf einen Text oder ein Element |
| `observation_filter` | das optionale `region` und `contains` von `observe`: Sie verkleinern den Baum und die Elemente und lesen nur bereits geschwärzte Zeilen |
| `gate` | die gesperrten Kategorien, ihre Erkennung, die Obergrenzen |
| `confirm` | die Bestätigung: Text des Fensters, natives Fenster oder Anfrage des Clients |
| `actions` | Klick, Ausfüllen, Auswahl einer Option, Tasten, Scrollen, Dialoge, über eine einzige Pipeline |
| `tab_state` | die Datei der Tabs, die der Server hält, damit der nächste Lauf die schließen kann, die ein angehaltener Lauf offen ließ |
| `evidence` | Aufnahmen und Downloads, ins verschlüsselte Verzeichnis geschrieben |
| `guide` | der Text, den das Modell beim Öffnen der Sitzung liest |
| `server` | die Fabrik des Servers, seine Abhängigkeiten, die Browser-Sitzung, das Journal jedes Aufrufs |
| `tools/` | eine Datei je Werkzeug, jedes über die Ausgabestelle |

Jede Sicherheitsprüfung existiert nur in einem Modul; ein zweites Modul, das sie braucht, ruft das erste
auf. Tests prüfen das und dass kein Werkzeug die Ausgabestelle umgeht.

## Schutzmaßnahmen

- Loopback: Der Server verbindet sich nur, wenn jeder Socket am Debugging-Port auf Loopback lauscht
  (bei jeder Anbindung in `/proc/net` gelesen), wenn das deklarierte Profil nicht das Standardprofil des
  Browsers ist und wenn der Browser eine Debugging-Adresse auf demselben Loopback-Port ankündigt.
- Erlaubte Ursprünge und Ursprungsrichtlinie: Mit `strict` wird eine Seite außerhalb der Liste weder gelesen noch wird darauf gehandelt;
  nur ihr Ursprung wird zurückgegeben. Mit `discovery` beantwortet der Bediener für jeden neuen Ursprung ein Fenster, und mit `open` wird
  jeder öffentliche Ursprung zugelassen, lokale Adressen und Namen ausgenommen. Ein Eintrag ist ein Ursprung oder ein Subdomain-Platzhalter
  (`https://*.example.invalid`), vom Bediener geschrieben; ein Platzhalter weitet Lesen und Handeln aus,
  nie das Tippen. Zugangsdaten werden nur auf dem genauen Ursprung getippt, den ihre Datei nennt, und
  eine Datei, die einen Platzhalter aufführt, wird abgelehnt.
- Gesperrte Aktionen: Zahlung, unwiderrufliche Löschung und Änderung eines Geheimnisses werden anhand
  eines im Code geschriebenen Vokabulars und anhand von Kartenfeldern erkannt. Heuristiken und die
  `intent_category` des Modells können nur eine Sperre hinzufügen; nichts hebt eine auf, und das
  Bedienerprofil kann sie nicht konfigurieren.
- Bestätigung: Eine gesperrte Aktion wartet auf die Antwort des Bedieners in einem Fenster, das der
  Server öffnet.
- Im Zweifel abgelehnt: Eine nicht lesbare Mount-Tabelle, eine Maske, die sich nicht zeichnen lässt,
  oder ein Journal, das sich nicht schreiben lässt, endet in einer Ablehnung, nie in einer schwächeren
  Prüfung.

## Warum ein von Hand geschriebener DevTools-Client mit einer Liste erlaubter Methoden

Mit einer allgemeinen Automatisierungsbibliothek ist das, was der Server an den Browser senden kann, das,
was die Bibliothek senden kann. Mit einem eigenen Client ist es eine Liste von 27 Methoden in einem
Modul, und ein Test vergleicht sie mit jedem Methodennamen im Code. Skripte werden nur aus einer Tabelle
konstanter Texte gesendet; die Argumente eines Werkzeugs reisen im Feld `arguments` des Protokolls und
werden nie in ein Skript formatiert.

## Warum eine Markierung der Knoten des Browsers ist und kein Rang

Eine Markierung ist an den Knoten des Browsers für dieses Element in diesem Snapshot gebunden. Ein Rang
(„der dritte Button“) verschiebt sich, wenn die Seite ein Banner oder eine Zeile einfügt, und dieselbe
Nummer bezeichnet dann ein anderes Element, möglicherweise einen Zahlungsbutton. An den Knoten
gebunden, wird ein verschobenes oder ersetztes Element abgelehnt (`element_stale`), und das Modell
beobachtet erneut.

## Warum Masken und Markierungen vom Server auf das Bild gezeichnet werden

In die Seite gezeichnet, kann ein Overlay verschoben, verborgen oder von den eigenen Skripten der Seite
gelesen werden. Auf die aufgenommenen Pixel gezeichnet, deckt eine Maske ab, was der Server gemessen
hat, und ein Wert darunter lässt sich aus dem Bild nicht wiederherstellen; lässt sich eine Maske nicht
zeichnen, wird kein Bild veröffentlicht. Die Markierungen entsprechen den Rahmen des Snapshots, gegen den
der Server prüft, wenn das Modell handelt. Jeder Rahmen ist in CSS-Pixeln: Die Gerätepixel des
Snapshots werden durch das Verhältnis der beiden Viewports des Browsers geteilt, sodass eine Maske bei
jeder Bildschirmskalierung auf ihrem Feld sitzt; ohne ein solches Verhältnis wird die Seite abgelehnt,
nicht bei Skalierung 1 gelesen.

## Warum eine Ebene über der Seite zuerst behalten wird

Eine Beobachtung enthält höchstens 150 Markierungen und 400 Baumzeilen. Ein Einwilligungsbanner, das
über der Ansicht fixiert ist, steht spät im Dokument: In Dokumentreihenfolge wurde es abgeschnitten, und
die Elemente darunter wurden angeboten, als könne ein Klick sie erreichen. Unter den Obergrenzen behält
der Server der Reihe nach: was zu einer Ebene über der Seite gehört (ein Dialog, ein modaler Knoten, ein
fester oder klebender Knoten, der über sichtbarem Inhalt liegt); was in der Ansicht und frei ist; was in
der Ansicht unter einer Ebene liegt (`covered`); den Rest. Die Baumzeilen der Ebene stehen zuerst, unter
einer Overlay-Zeile. Ebenen werden allein am Snapshot erkannt, an Zeichenreihenfolge und
Zeigerereignissen, ohne einen Aufruf je Element; ein fester Container um die ganze Seite ist keine.

## Warum die Bestätigung ein natives Fenster ist

Der MCP-Client handelt für das Modell und kann eine Anfrage von selbst beantworten. Ein Fenster von
`kdialog` oder `zenity`, vom Server ohne Shell gestartet, zeigt den in diesem Augenblick im Browser
gelesenen Zustand und wird von einer Person beantwortet, mit der Ablehnung als Standardbutton und einer
ausbleibenden Antwort nach 90 Sekunden, die als Ablehnung zählt. Die eigene Anfrage des Clients
(Elicitation) wird nur verwendet, wenn der Bediener erklärt, dass der Client sie nie von selbst
beantwortet.

## Verworfene Entscheidungen und was sie wieder öffnen würde

- Playwright oder eine ähnliche Bibliothek: Die Liste dessen, was der Server sendet, wäre die der
  Bibliothek. Würde sie wieder öffnen: ein Browser, dessen Debugging-Protokoll der handgeschriebene
  Client nicht mehr sprechen kann.
- Ein Werkzeug, das ein Skript ausführt: Es würde Markierungen, Identitätsprüfungen, die Sperre und die
  Masken umgehen und alles lesen, was eine Seite enthält. Würde es wieder öffnen: eine Aufgabe des
  Bedieners, die die Werkzeuge nicht erledigen können, und dann als neues konstantes Skript im Client,
  nicht als ein vom Modell gewähltes Skript.
- Lesen innerhalb von Frames: Ein Frame eines anderen Ursprungs ist eine andere Website, oft eine für
  Zahlungen oder Werbung; ihn zu lesen würde dem Modell eine Seite außerhalb der erlaubten Ursprünge
  schicken. Frames werden verdeckt. Würde es wieder öffnen: eine Website der erlaubten Ursprünge, deren
  benötigter Inhalt in einem Frame desselben Ursprungs liegt.
- Ein Netzwerktransport: Ein lauschender Port kann von jedem lokalen Prozess erreicht werden;
  Standardströme binden den Server an den Client, der ihn gestartet hat. Würde ihn wieder öffnen: ein
  MCP-Client, den der Bediener braucht und der Server nur über das Netzwerk erreicht.
- Die Obergrenzen anheben: Sie halten das, was das Modell erhält, begrenzt. Würde es wieder öffnen: eine
  Seite, deren einzige Ebene bei der jetzigen Rangfolge abgeschnitten wird.
- Das alte Format der Zugangsdaten, vom Server gelesen: Sein Schema ist streng, damit eine Datei nie als
  etwas gelesen wird, das sie nicht ist. Würde es wieder öffnen: ein Format, das der Bediener nicht
  einmal umwandeln kann.
- Ein Werkzeug, das Zugangsdaten umwandelt: Dem Modell würde der Pfad einer Datei mit Geheimnissen
  übergeben, und seine Antworten gehen an seinen Anbieter; der Befehl läuft in der Shell des Bedieners.
  Würde es wieder öffnen: ein Bediener ohne Shell und ein Weg, die Werte dort aus der Sitzung des Modells
  herauszuhalten.
- Ein zweiter Weg der Auflösung, für das alte Format: Zwei Regeln, die Zugangsdaten an einen Ursprung
  binden, sind zwei Stellen, die zu korrigieren sind. Würde ihn wieder öffnen: nichts; `resolve` bleibt
  der einzige Leser des einen Schemas.
- Ein eigener Browser-Kontext des Servers, damit die Ablehnung von Downloads auf die Tabs des Servers
  beschränkt bleibt: Seine Tabs würden die angemeldete Sitzung des Bedieners nicht teilen, was der Grund
  des Projekts ist. Solange der Server verbunden ist, werden daher auch die eigenen Downloads des
  Bedieners abgelehnt. Würde ihn wieder öffnen: ein Browser, der die Download-Einstellung für einen
  einzelnen Tab annimmt, was nicht bestätigt ist.
