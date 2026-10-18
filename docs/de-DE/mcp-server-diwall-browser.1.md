% MCP-SERVER-DIWALL-BROWSER(1) mcp-server-diwall-browser | Benutzerbefehle
% Ronan Davalan
% 2026-10-18

# NAME

mcp-server-diwall-browser - MCP-Server, der einem Sprachmodell die Augen und Hände eines Bedieners im eigenen Browser gibt

# SYNOPSE

**mcp-server-diwall-browser**

**mcp-server-diwall-browser-credential-import** **\--source** *DATEI* **\--name** *NAME* [**\--origin** *URSPRUNG*...]

**mcp-server-diwall-browser-credential-import** **\--source-dir** *VERZEICHNIS*

# BESCHREIBUNG

**mcp-server-diwall-browser** ist ein Model-Context-Protocol-Server (MCP). Er verbindet sich über
das Chrome DevTools Protocol mit einem Browser der Chromium-Familie (Google Chrome, Chromium oder
Brave), den der Bediener gestartet hat. Er gibt dem Sprachmodell, das der MCP-Client betreibt,
dieselbe Bildschirmaufnahme und denselben Barrierefreiheitsbaum wie dem Bediener und die Mittel,
in derselben bereits angemeldeten Sitzung zu handeln.

Das Programm hat keine Optionen. Ein MCP-Client startet es als Kindprozess und spricht mit ihm
über Standardeingabe und Standardausgabe. Es öffnet keinen lauschenden Port und endet, wenn der
Client seine Eingabe schließt. Es verbindet sich nur mit dem Debugging-Port des Browsers auf der
Loopback-Schnittstelle. Es startet oder schließt den Browser nie und hängt das verschlüsselte
Verzeichnis weder ein noch aus.

Der Browser muss auf einem eigenen Profil laufen, nie auf dem gewohnten Profil des Bedieners:

    google-chrome --user-data-dir=VERZEICHNIS --remote-debugging-port=9222

Der Server weist ein Standardprofil (`profile_dir_is_default`) und einen Debugging-Port zurück, der
über die Loopback-Schnittstelle hinaus erreichbar ist (`debug_endpoint_not_loopback`). Zugangsdaten,
Aufnahmen, Downloads und das Prüfprotokoll liegen in einem verschlüsselten Verzeichnis
(gocryptfs), das der Bediener öffnet; solange es geschlossen ist, schreibt der Server nichts.

Die Installation des Programms konfiguriert es nicht. Schreiben Sie zuerst das Bedienerprofil
(siehe **BEDIENERPROFIL**) und verbinden Sie dann den Server mit einem MCP-Client; mit Claude Code:

    claude mcp add --scope user diwall-browser -- mcp-server-diwall-browser

Installation, Browser, verschlüsseltes Verzeichnis und jeder Ablehnungscode sind im
Bedienerhandbuch beschrieben (siehe **DATEIEN**).

# WERKZEUGE

Der Server bietet dem Modell vierzehn Werkzeuge.

**status**
:   Was konfiguriert, geöffnet, verbunden und erlaubt ist und was blockiert. Es funktioniert auch ohne jede Konfiguration.

**list_tabs**, **open_tab**, **close_tab**
:   Die Tabs dieses Servers. Der Server arbeitet nur in Tabs, die er selbst geöffnet hat, nie in einem Tab des Bedieners.

**navigate**
:   Eine http- oder https-Adresse in einem seiner Tabs laden.

**observe**
:   Das Bild mit nummerierten Markierungen, den Barrierefreiheitsbaum und die Liste der Elemente liefern. Die Markierungen gelten nur für diese Beobachtung.

**click**, **fill**, **select_option**, **press_key**, **scroll**, **handle_dialog**
:   Auf ein Element handeln, das durch seine Markierung bezeichnet ist. Ein sensibles Feld wird nur aus einer Zugangsdatendatei gefüllt (**fill** mit *credential_ref*), nie aus einem Text.

**wait**
:   Auf das Laden der Seite, einen Text oder ein Element warten.

**save_evidence**
:   Eine Aufnahme im verschlüsselten Verzeichnis aufbewahren.

# BEDIENERPROFIL

Der Server liest das Bedienerprofil bei jedem Aufruf neu; eine Änderung wirkt ohne Neustart. Es ist
eine JSON-Datei unter `$XDG_CONFIG_HOME/mcp-server-diwall-browser/profile.json`, oder unter
`~/.config/mcp-server-diwall-browser/profile.json`, wenn `XDG_CONFIG_HOME` nicht gesetzt ist, oder
unter dem absoluten Pfad, den die Variable `MCP_DIWALL_BROWSER_PROFILE` nennt. Die Datei hat den
Modus 600 und ihr Verzeichnis den Modus 700; sonst weist der Server sie zurück (`profile_invalid`).
Die Datei enthält kein Geheimnis.

`schema_version`
:   Immer `1`.

`secrets_dir`
:   Wo das verschlüsselte Verzeichnis geöffnet ist (absoluter Pfad).

`secrets_crypt_dir`
:   Das verschlüsselte Verzeichnis auf der Platte (optional).

`allowed_origins`
:   Die Seiten, die der Server liest und auf denen er handelt: Ursprünge oder Subdomain-Platzhalter wie `https://*.example.invalid`. Von jeder anderen Seite wird nur der Ursprung zurückgegeben.

`confirm_each_change`
:   Ursprünge aus `allowed_origins`, auf denen jede Änderung eine Bestätigung verlangt.

`local_model_origins`, `declarations`
:   Erklärungen des Bedieners, die **status** anzeigt und die sonst nichts bewirken.

`browser`
:   `family` (`chrome`, `chromium` oder `brave`), `profile_dir` (das `--user-data-dir` des eigenen Profils) und `debug_port` (von 1024 bis 65535, standardmäßig 9222).

`limits`
:   `min_action_delay_ms` (standardmäßig 250), `max_actions_per_session` (200) und `max_pages_per_session` (50).

Ein Schlüssel, der die gesperrten Aktionen (Zahlung, Löschung) konfigurieren würde, wird
zurückgewiesen: Nur der Code entscheidet, was gesperrt ist. Ein vollständiges Beispiel steht im
Bedienerhandbuch.

# BESTÄTIGUNG

Vor einer Zahlung, einer unwiderruflichen Löschung oder einer Änderung eines Geheimnisses und vor
jeder Änderung auf einem Ursprung aus `confirm_each_change` öffnet der Server mit **kdialog** oder
**zenity** ein Fenster mit dem Titel „Confirmation du serveur MCP“ und den Schaltflächen
„Autoriser“ und „Refuser“. Das Fenster wartet 90 Sekunden und zählt dann als nicht beantwortet;
eine Antwort gilt für genau eine Aktion. Es braucht eine Anzeige in der Umgebung des Servers
(`DISPLAY` oder `WAYLAND_DISPLAY`); **status** nennt den Kanal in `confirmation_channel`.

# IMPORT VON ZUGANGSDATEN

**mcp-server-diwall-browser-credential-import** wandelt eine Zugangsdatendatei in einem anderen
Format, die im geöffneten verschlüsselten Verzeichnis liegt, in eine Datei um, die der Server liest,
im eigenen Prozess und ohne einen Wert anzuzeigen. Starten Sie den Befehl selbst in Ihrer Shell,
bei geöffnetem verschlüsseltem Verzeichnis.

**\--source** *DATEI*
:   Absoluter Pfad der alten Datei unter dem verschlüsselten Verzeichnis. Erforderlich zusammen mit **\--name**, außer wenn **\--source-dir** angegeben ist.

**\--name** *NAME*
:   Bezeichnung der neuen Datei, die im Verzeichnis `credentials/` zu `NAME.json` wird.

**\--origin** *URSPRUNG*
:   Ein vollständiger Ursprung (`https://host`, mit Port, wenn es nicht der Standardport ist). Kann wiederholt werden. Ohne diese Option wird jeder Host der alten Datei zu `https://host`.

**\--source-dir** *VERZEICHNIS*
:   Absoluter Pfad eines Verzeichnisses mit alten Dateien; jede Datei, die auf `.json` endet, wird der Reihe nach umgewandelt. Schließt die drei vorherigen Optionen aus.

Der Befehl schreibt je Datei eine JSON-Zeile auf seine Standardausgabe. Nur Benutzername und
Passwort werden übernommen, ein zweiter Faktor nicht. Er überschreibt nichts und löscht nichts.

# UMGEBUNG

`MCP_DIWALL_BROWSER_PROFILE`
:   Absoluter Pfad des Bedienerprofils anstelle des üblichen Ortes.

`XDG_CONFIG_HOME`
:   Basis des üblichen Ortes des Bedienerprofils.

`DISPLAY`, `WAYLAND_DISPLAY`
:   Die Anzeige, auf der sich das Bestätigungsfenster öffnet.

# PAKETE

Der Server wird als natives Paket geliefert und aus dem Ordner installiert, der die heruntergeladene Datei enthält:

Debian 13, Ubuntu 24.04 und 26.04
:   `sudo apt install ./mcp-server-diwall-browser_0.0.1-1_all.deb`

Fedora 44
:   `sudo dnf install ./mcp-server-diwall-browser-0.0.1-1.fc44.noarch.rpm`

openSUSE Leap 16.0
:   `sudo zypper install --allow-unsigned-rpm ./mcp-server-diwall-browser-0.0.1-1.leap160.noarch.rpm`

Arch Linux
:   `sudo pacman -U ./mcp-server-diwall-browser-0.0.1-1-any.pkg.tar.zst`

Installation, Start des Servers durch ein gewöhnliches Konto und Entfernen sind auf diesen Systemen in Containern validiert. Ein Container zeigt weder das Bestätigungsfenster noch das Einhängen des verschlüsselten Verzeichnisses noch einen Browser. Debian 12 und Ubuntu 22.04 werden nicht unterstützt: Ihr Python ist älter als 3.12.

# DATEIEN

`/usr/bin/mcp-server-diwall-browser`, `/usr/bin/mcp-server-diwall-browser-credential-import`
:   Die beiden Befehle.

`/usr/lib/mcp-server-diwall-browser`
:   Das Programm und seine virtuelle Python-Umgebung, die das Paket bei der Installation anlegt und für die es eine Netzwerkverbindung braucht.

`/usr/share/doc/mcp-server-diwall-browser/de/OPERATOR_GUIDE.md`
:   Das Bedienerhandbuch: Installation, Browser, verschlüsseltes Verzeichnis, Profil, Zugangsdaten und Ablehnungscodes.

`/usr/share/doc/mcp-server-diwall-browser/OPERATOR_GUIDE.md`
:   Dasselbe Handbuch auf Englisch, der Sprache der Quelle.

`~/.config/mcp-server-diwall-browser/profile.json`
:   Das Bedienerprofil.

*secrets_dir*`/mcp-server-diwall-browser/`
:   Vom Server nur im geöffneten verschlüsselten Verzeichnis geschrieben: `credentials/`, `browser-profile/`, `journal/operations.jsonl`, `evidence/`, `downloads/` und `run/tabs.json`.

# EXIT-STATUS

**mcp-server-diwall-browser** endet mit 0, wenn der MCP-Client seine Eingabe schließt. Die
Ablehnung eines Werkzeugs ist ein Ergebnis mit Code, das an den Client zurückgegeben wird, kein
Exit-Status.

**mcp-server-diwall-browser-credential-import** endet mit 0, wenn jede Datei übernommen ist, und
sonst mit 1, nachdem es eine Zeile `error: CODE` auf die Fehlerausgabe geschrieben hat.

# SIEHE AUCH

**gocryptfs**(1), **fusermount3**(1), **kdialog**(1), **zenity**(1)

Projekt: <https://github.com/RonanDavalan/mcp-server-diwall-browser> und
<https://mcp.diwall.davalan.fr>

# AUTOR

Ronan Davalan. Die weiteren Mitwirkenden stehen in `CONTRIBUTORS.md`.
