% MCP-SERVER-DIWALL-BROWSER(1) mcp-server-diwall-browser | Commandes utilisateur
% Ronan Davalan
% 2026-10-18

# NOM

mcp-server-diwall-browser - serveur MCP qui donne à un modèle de langage les yeux et les mains d'un opérateur dans son propre navigateur

# SYNOPSIS

**mcp-server-diwall-browser**

**mcp-server-diwall-browser-credential-import** **\--source** *FICHIER* **\--name** *NOM* [**\--origin** *ORIGINE*...]

**mcp-server-diwall-browser-credential-import** **\--source-dir** *RÉPERTOIRE*

# DESCRIPTION

**mcp-server-diwall-browser** est un serveur Model Context Protocol (MCP). Il s'attache, par le
Chrome DevTools Protocol, à un navigateur de la famille Chromium (Google Chrome, Chromium ou
Brave) que l'opérateur a lancé. Il donne au modèle de langage que le client MCP fait travailler
la même capture d'écran et le même arbre d'accessibilité qu'à l'opérateur, et le moyen d'agir
dans la même session déjà ouverte.

Le programme n'a aucune option. Un client MCP le lance comme processus enfant et lui parle sur
son entrée et sa sortie standard. Il n'ouvre aucun port en écoute et s'arrête quand le client
ferme son entrée. Il se connecte seulement au port de débogage du navigateur, sur l'interface de
boucle locale. Il ne lance ni ne ferme jamais le navigateur, et ne monte ni ne démonte jamais le
répertoire chiffré.

Le navigateur doit tourner sur un profil à lui, jamais sur le profil habituel de l'opérateur :

    google-chrome --user-data-dir=RÉPERTOIRE --remote-debugging-port=9222

Le serveur refuse un profil par défaut (`profile_dir_is_default`) et un port de débogage joignable
au-delà de la boucle locale (`debug_endpoint_not_loopback`). Les identifiants, les captures, les
téléchargements et le journal d'audit sont gardés dans un répertoire chiffré (gocryptfs) que
l'opérateur ouvre ; tant qu'il est fermé, le serveur n'écrit rien.

Installer le programme ne le configure pas. Écrivez d'abord le profil de l'opérateur (voir
**PROFIL DE L'OPÉRATEUR**), puis branchez le serveur à un client MCP ; avec Claude Code :

    claude mcp add --scope user diwall-browser -- mcp-server-diwall-browser

L'installation, le navigateur, le répertoire chiffré et chaque code de refus sont décrits dans le
guide de l'opérateur (voir **FICHIERS**).

# OUTILS

Le serveur offre quatorze outils au modèle.

**status**
:   Ce qui est configuré, ouvert, rattaché et autorisé, et ce qui bloque. Il fonctionne sans rien de configuré.

**list_tabs**, **open_tab**, **close_tab**
:   Les onglets de ce serveur. Le serveur ne travaille que dans les onglets qu'il a ouverts, jamais dans un onglet de l'opérateur.

**navigate**
:   Charger une adresse http ou https dans l'un de ses onglets.

**observe**
:   Rendre l'image aux repères numérotés, l'arbre d'accessibilité et la liste des éléments. Les repères ne valent que pour cette observation.

**click**, **fill**, **select_option**, **press_key**, **scroll**, **handle_dialog**
:   Agir sur un élément désigné par son repère. Un champ sensible ne se remplit que depuis un fichier d'identifiants (**fill** avec *credential_ref*), jamais depuis un texte.

**wait**
:   Attendre le chargement de la page, un texte ou un élément.

**save_evidence**
:   Garder une capture dans le répertoire chiffré.

# PROFIL DE L'OPÉRATEUR

Le serveur relit le profil de l'opérateur à chaque appel : une modification prend effet sans
redémarrage. C'est un fichier JSON, cherché dans
`$XDG_CONFIG_HOME/mcp-server-diwall-browser/profile.json`, ou dans
`~/.config/mcp-server-diwall-browser/profile.json` quand `XDG_CONFIG_HOME` n'est pas défini, ou au
chemin absolu que donne la variable `MCP_DIWALL_BROWSER_PROFILE`. Le fichier est en mode 600 et
son répertoire en mode 700 ; sinon le serveur le refuse (`profile_invalid`). Le fichier ne contient
aucun secret.

`schema_version`
:   Toujours `1`.

`secrets_dir`
:   L'endroit où le répertoire chiffré est ouvert (chemin absolu).

`secrets_crypt_dir`
:   Le répertoire chiffré sur le disque (facultatif).

`allowed_origins`
:   Les sites que le serveur lit et sur lesquels il agit : des origines, ou des jokers de sous-domaine comme `https://*.example.invalid`. D'une autre page, seule l'origine est rendue.

`confirm_each_change`
:   Origines de `allowed_origins` où chaque changement demande une confirmation.

`local_model_origins`, `declarations`
:   Déclarations de l'opérateur, montrées par **status** et sans autre usage.

`browser`
:   `family` (`chrome`, `chromium` ou `brave`), `profile_dir` (le `--user-data-dir` du profil dédié) et `debug_port` (de 1024 à 65535, 9222 par défaut).

`limits`
:   `min_action_delay_ms` (250 par défaut), `max_actions_per_session` (200) et `max_pages_per_session` (50).

Une clé qui configurerait les actions verrouillées (paiement, suppression) est refusée : seul le
code décide de ce qui est verrouillé. Le guide de l'opérateur donne un exemple complet.

# CONFIRMATION

Avant un paiement, une suppression irréversible ou un changement de secret, et avant chaque
changement sur une origine de `confirm_each_change`, le serveur ouvre, par **kdialog** ou
**zenity**, une fenêtre intitulée « Confirmation du serveur MCP » avec les boutons « Autoriser » et
« Refuser ». La fenêtre attend 90 secondes, puis compte comme une absence de réponse ; une réponse
couvre une seule action. Elle exige un affichage dans l'environnement du serveur (`DISPLAY` ou
`WAYLAND_DISPLAY`) ; **status** indique le canal dans `confirmation_channel`.

# IMPORT D'IDENTIFIANTS

**mcp-server-diwall-browser-credential-import** convertit un fichier d'identifiants d'un autre
format, gardé dans le répertoire chiffré ouvert, en un fichier que le serveur lit, dans son propre
processus et sans montrer aucune valeur. Lancez-le vous-même, dans votre shell, avec le répertoire
chiffré ouvert.

**\--source** *FICHIER*
:   Chemin absolu de l'ancien fichier, sous le répertoire chiffré. Obligatoire avec **\--name**, sauf si **\--source-dir** est donné.

**\--name** *NOM*
:   Nom du nouveau fichier, qui devient `NOM.json` dans le répertoire `credentials/`.

**\--origin** *ORIGINE*
:   Une origine complète (`https://hôte`, avec un port s'il n'est pas celui par défaut). Peut être répétée. Sans elle, chaque hôte de l'ancien fichier devient `https://hôte`.

**\--source-dir** *RÉPERTOIRE*
:   Chemin absolu d'un répertoire d'anciens fichiers ; chaque fichier qui se termine par `.json` est converti à son tour. Exclut les trois options précédentes.

La commande écrit une ligne JSON par fichier sur sa sortie standard. Seuls le nom d'utilisateur et
le mot de passe sont repris ; un second facteur ne l'est pas. Elle n'écrase rien et ne supprime rien.

# ENVIRONNEMENT

`MCP_DIWALL_BROWSER_PROFILE`
:   Chemin absolu du profil de l'opérateur, à la place de l'emplacement habituel.

`XDG_CONFIG_HOME`
:   Base de l'emplacement habituel du profil de l'opérateur.

`DISPLAY`, `WAYLAND_DISPLAY`
:   L'affichage sur lequel s'ouvre la fenêtre de confirmation.

# PAQUETS

Le serveur est livré en paquets natifs, installés depuis le dossier qui contient le fichier téléchargé :

Debian 13, Ubuntu 24.04 et 26.04
:   `sudo apt install ./mcp-server-diwall-browser_0.0.1-1_all.deb`

Fedora 44
:   `sudo dnf install ./mcp-server-diwall-browser-0.0.1-1.fc44.noarch.rpm`

openSUSE Leap 16.0
:   `sudo zypper install --allow-unsigned-rpm ./mcp-server-diwall-browser-0.0.1-1.leap160.noarch.rpm`

Arch Linux
:   `sudo pacman -U ./mcp-server-diwall-browser-0.0.1-1-any.pkg.tar.zst`

L'installation, le démarrage du serveur par un compte ordinaire et le retrait sont validés en conteneur sur ces systèmes. Un conteneur ne montre ni la fenêtre de confirmation, ni le montage du répertoire chiffré, ni un navigateur. Debian 12 et Ubuntu 22.04 ne sont pas pris en charge : leur Python est plus ancien que 3.12.

# FICHIERS

`/usr/bin/mcp-server-diwall-browser`, `/usr/bin/mcp-server-diwall-browser-credential-import`
:   Les deux commandes.

`/usr/lib/mcp-server-diwall-browser`
:   Le programme et son environnement virtuel Python, créé par le paquet à l'installation, qui demande une connexion au réseau pour cette étape.

`/usr/share/doc/mcp-server-diwall-browser/fr/OPERATOR_GUIDE.md`
:   Le guide de l'opérateur : installation, navigateur, répertoire chiffré, profil, identifiants et codes de refus.

`/usr/share/doc/mcp-server-diwall-browser/OPERATOR_GUIDE.md`
:   Le même guide, en anglais, langue de la source.

`~/.config/mcp-server-diwall-browser/profile.json`
:   Le profil de l'opérateur.

*secrets_dir*`/mcp-server-diwall-browser/`
:   Écrit par le serveur dans le seul répertoire chiffré ouvert : `credentials/`, `browser-profile/`, `journal/operations.jsonl`, `evidence/`, `downloads/` et `run/tabs.json`.

# CODE DE SORTIE

**mcp-server-diwall-browser** se termine avec 0 quand le client MCP ferme son entrée. Le refus d'un
outil est un résultat rendu au client, avec un code, et non un code de sortie.

**mcp-server-diwall-browser-credential-import** se termine avec 0 quand chaque fichier est repris et
avec 1 sinon, après avoir écrit une ligne `error: CODE` sur sa sortie d'erreur.

# VOIR AUSSI

**gocryptfs**(1), **fusermount3**(1), **kdialog**(1), **zenity**(1)

Projet : <https://github.com/RonanDavalan/mcp-server-diwall-browser> et
<https://mcp.diwall.davalan.fr>

# AUTEUR

Ronan Davalan. Les autres contributeurs sont listés dans `CONTRIBUTORS.md`.
