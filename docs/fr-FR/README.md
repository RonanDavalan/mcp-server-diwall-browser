# mcp-server-diwall-browser

[English](../../README.md) | **Français** | [Deutsch](../de-DE/README.md) | [Español](../es-ES/README.md)

Un serveur MCP qui donne à un modèle de langage les yeux et les mains d'un opérateur dans le navigateur
même de l'opérateur, par le protocole Chrome DevTools : le modèle voit la même capture d'écran et le
même arbre d'accessibilité que l'opérateur, et agit dans la même session authentifiée. Il lui faut un
navigateur de la famille Chromium que l'opérateur démarre avec un profil dédié et un port de débogage lié
à l'interface de bouclage, ainsi qu'un répertoire chiffré (gocryptfs) pour les identifiants, les captures
et les téléchargements.

## Installer

Sous Linux, le serveur est livré en paquets natifs. Téléchargez celui de votre système sur la page
[Releases](https://github.com/RonanDavalan/mcp-server-diwall-browser/releases) du dépôt ou sur la page des téléchargements du site, puis installez-le depuis le dossier
qui le contient :

| Système | Commande |
|---|---|
| Debian 13, Ubuntu 24.04 et 26.04 | `sudo apt install ./mcp-server-diwall-browser_0.0.1-1_all.deb` |
| Fedora 44 | `sudo dnf install ./mcp-server-diwall-browser-0.0.1-1.fc44.noarch.rpm` |
| openSUSE Leap 16.0 | `sudo zypper install --allow-unsigned-rpm ./mcp-server-diwall-browser-0.0.1-1.leap160.noarch.rpm` |
| Arch Linux | `sudo pacman -U ./mcp-server-diwall-browser-0.0.1-1-any.pkg.tar.zst` |

L'installation, le démarrage du serveur par un compte ordinaire et le retrait sont validés en conteneur sur
ces systèmes. Un conteneur ne montre ni la fenêtre de confirmation, ni le montage du répertoire chiffré, ni
un navigateur. Debian 12 et Ubuntu 22.04 ne sont pas pris en charge : leur Python est plus ancien que 3.12.
L'installation demande une connexion au réseau : le paquet crée un environnement virtuel Python et y installe
les dépendances, aux versions que le projet a testées. La page de manuel est `man mcp-server-diwall-browser`.
Pour installer depuis un clone du dépôt, suivez [docs/OPERATOR_GUIDE.md](../OPERATOR_GUIDE.md).

## Le brancher à un client MCP

Le serveur parle MCP sur l'entrée et la sortie standard. Avec un paquet installé, la commande est
`mcp-server-diwall-browser` et le profil d'opérateur est lu à sa place habituelle :

```
claude mcp add --scope user diwall-browser -- mcp-server-diwall-browser
```

Depuis un clone du dépôt, avec Claude Code :

```
claude mcp add --scope user diwall-browser \
  -e MCP_DIWALL_BROWSER_PROFILE=~/.config/mcp-server-diwall-browser/profile.json \
  -- ~/mcp-server-diwall-browser/.venv/bin/python -m mcp_server_diwall_browser
```

Pour un autre client MCP, le bloc générique `mcpServers` :

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

Adaptez `~/mcp-server-diwall-browser` à l'endroit où vous avez cloné le dépôt, et le chemin du
profil à l'endroit où vous avez écrit votre profil d'opérateur. La variable peut être omise quand le profil
d'opérateur est à sa place habituelle, `~/.config/mcp-server-diwall-browser/profile.json`. La forme de la
commande `claude mcp add` a été vérifiée avec `claude mcp add --help`, puis lancée par l'opérateur sous
Debian : `claude mcp list` montrait le serveur connecté. Les tests démarrent le serveur exactement comme ces
deux blocs : `python -m mcp_server_diwall_browser` avec `MCP_DIWALL_BROWSER_PROFILE` définie.

Tout ce qui précède (installer, démarrer le navigateur, ouvrir le répertoire chiffré, écrire le profil
d'opérateur) est dans [docs/OPERATOR_GUIDE.md](../OPERATOR_GUIDE.md). Le fonctionnement et ses raisons sont
dans [docs/ARCHITECTURE.md](../ARCHITECTURE.md).

## Ce que le modèle voit

![La page de démonstration telle que le modèle la reçoit](../images/observe-demo.png)

L'image ci-dessus a été produite par `scripts/smoke_test.py` à partir de la page de démonstration locale
(`docs/demo/index.html`). Les repères numérotés sont dessinés par le serveur : une action désigne un
élément par son repère. Le champ de mot de passe, que la page a rempli elle-même, est masqué par un
rectangle plein, et sa valeur est retenue dans le texte que reçoit le modèle (`redacted_fields` vaut 1).

## Ce qui est testé, ce qui ne l'est pas

Testé :

- La suite complète, sous Debian 13 avec Python 3.12.11 et les dépendances de `requirements.lock` : sans
  navigateur, 1810 tests passent, 1 est ignoré (un test qui ne s'exécute qu'en root) et les 571 qui exigent un
  navigateur sont désélectionnés ; avec Google Chrome 155.0.8059.84 (sans interface, profil temporaire vierge),
  2381 tests passent et 1 est ignoré, sans échec attendu.
- Tests de téléchargement : deux échouaient au hasard, 7 et 8 fois sur 30 exécutions de chacun seul, parce
  que le serveur refermait le réglage de téléchargement avant que le navigateur ait choisi quoi faire du
  téléchargement. Après la modification, ils ont échoué 3 et 1 fois sur 130 exécutions de chacun seul ; les 3
  étaient une lecture, dans le test lui-même, d'un fichier que le navigateur renomme, qui est désormais un
  seul assistant ignorant le renommage. Depuis, chacun des deux seul a échoué 0 fois sur 200 exécutions, deux
  fois de suite. Un troisième test, qui refermait aussitôt le réglage et attendait qu'un téléchargement
  tardif soit refusé, a échoué 4 fois sur 200 seul : le vrai navigateur peut choisir avant que le serveur
  referme le réglage, si bien que ce test est remplacé par des tests sur un navigateur simulé. Les mesures et
  ce qui reste sont dans `docs/JOURNAL.md`.
- L'opérateur a rejoué une révision antérieure sous Debian (Python 3.13.5) avec Chrome 154.0.8037.97 : sans
  navigateur 1450 ont passé et 1 a été ignoré (un test qui ne s'exécute qu'en root) ; avec lui 119 ont passé
  et 6 ont échoué comme prévu, les six du zoom de page, clos depuis ; les deux tests de téléchargement seuls,
  0 échec sur 40 exécutions chacun. L'opérateur a ensuite rejoué `919ed8c` (la révision avant les tests de
  clic) sur le même navigateur : sans navigateur 1546 ont passé, 1 a été ignoré et 188 désélectionnés ; les
  trois fichiers de zoom et d'échelle, 125 passés ; les 188 tests de navigateur ont tous passé ; les deux
  tests de téléchargement et les deux tests qui remplacent l'instable, chacun seul, 0 échec sur 40
  exécutions. Les tests de clic n'y ont pas été rejoués.
- `scripts/smoke_test.py` rend 0 contre Chromium 141 et 3 quand aucun navigateur n'est trouvé.
- Les repères et les masques à une échelle d'écran de 1, 1,5 et 2, sur un Chromium 141 sans interface : le
  cadre de chaque repère visible est à moins de deux pixels du cadre que donne le navigateur lui-même, chaque
  pixel du champ de mot de passe de la page de démonstration est sous son masque, et le masque ne dépasse pas
  de plus de deux pixels (`tests/test_scale_integration.py`). Avant la correction, un masque ne couvrait pas
  son champ à une échelle supérieure à 1. Un vrai écran à 1,5 n'a pas été mesuré.
- Le zoom de page, à 100, 125, 150 et 200 % et à des échelles d'écran de 1 et 1,5, sur un Chromium 141 sans
  interface dont le profil vierge garde le zoom comme valeur par défaut : l'image que reçoit le modèle est la
  vue entière, le champ de mot de passe de la page de démonstration est sous son masque, comme celui d'une
  page défilée pour qu'il se trouve près du bas ou au milieu de la vue ; le masque ne dépasse pas de plus de
  deux pixels du champ, et dans les 16 placements mesurés aucun bord de masque ne se trouvait à l'intérieur
  de son champ (`tests/test_zoom_integration.py`, `tests/test_zoom_scrolled_integration.py`). Avant la
  correction, à 125, 150 et 200 %, 0 % du champ était couvert et `capture` lisait `ok`. Aucune image n'est
  publiée (`capture` lit `unavailable`, l'arbre est toujours servi) quand le navigateur ne donne aucun zoom
  utilisable, quand le zoom change pendant la capture, ou quand l'image s'écarte de plus de deux pixels de la
  taille de la vue sur l'un des axes (`tests/test_perception_zoom.py`, qui n'exige aucun navigateur). Sous
  Chrome 154, l'opérateur a mesuré la taille de l'image découpée telle que le navigateur la lit (la vue à
  chaque zoom, un pixel de moins en hauteur à 125 %) ; les tests de zoom y ont été rejoués plus tard
  (ci-dessous).
- Un clic du serveur atteint sa cible à des zooms de page de 100, 125, 150 et 200 % et à des échelles
  d'écran de 1 et 1,5, sur un Chromium 141 sans interface dont le profil vierge garde le zoom comme valeur
  par défaut. Le clic est celui du serveur (`Actions.click`, avec son garde-fou et ses limites) ; la page de
  test compte ce que reçoit chacun de ses boutons et une session séparée lit les comptes. À chacune des huit
  combinaisons, un bouton de 120 sur 32 pixels CSS, un bouton de 16 sur 16 entre deux leurres à 4 pixels, un
  bouton à deux vues et demie plus bas, et le grand bouton après que la page a été défilée pour le mettre au
  milieu de la vue, ont reçu exactement un clic, aucun leurre n'en a reçu et aucun n'est tombé hors d'un
  bouton. Sur une page défilée dans les deux sens, le clic atteint le bouton mis au milieu de la vue, et un
  bouton couvert par une bannière fixée à la vue est refusé (`element_obscured`) sans rien cliquer
  (`tests/test_click_integration.py`, 264 tests ; `tests/test_click_targets_page.py`, 51, et
  `tests/test_click_lookup_point.py`, 20, n'exigent aucun navigateur). Le même contrôle refuse un clic envoyé
  au point du cadre multiplié par le zoom, divisé par lui, ou multiplié par l'échelle d'écran (64 clics de ce
  genre sur 64 n'ont atteint aucun bouton), ainsi qu'un serveur qui interroge le navigateur sur le point de
  la vue. Avant la correction, 20 des 32 essais sur la première page échouaient, ceux qui exigeaient un
  défilement, et un bouton couvert par la bannière était accepté et la bannière cliquée, aux huit
  combinaisons : le navigateur répond à la recherche de nœud pour un point du document, et le clic lui
  donnait un point de la vue. Les mesures sont dans `docs/JOURNAL.md`.
- Un clic sur une page qui bouge au premier mouvement du pointeur (un menu qui s'ouvre au survol, une
  bannière qui glisse). La page de test décale chaque cible de 60 pixels CSS vers le bas au premier
  mouvement de la souris et place un bouton intrus où se trouvait la cible. À chacune des huit combinaisons
  de zoom de page et d'échelle d'écran, pour chacun de trois boutons (un grand, un de 16 sur 16 entre deux
  leurres, un à deux vues et demie plus bas), un clic du serveur atteint la cible une fois ou est refusé
  comme couvert (`element_obscured`) sans rien cliquer, et dans les 24 essais il est refusé à chaque fois
  (`tests/test_click_shifting_integration.py`, 80 tests ; `tests/test_click_shifting_page.py`, 30, et
  `tests/test_click_moving_page.py`, 10, n'exigent aucun navigateur). Avant la modification, les mêmes 24
  essais ne rendaient aucune erreur et le clic atteignait l'intrus. Après un refus, un clic sur une nouvelle
  observation atteint sa cible. Le serveur redemande désormais au navigateur ce qui se trouve sous le point
  après le mouvement et avant l'appui, à chaque fois, de sorte qu'une cible couverte là où elle se trouve,
  par un bouton affiché au premier mouvement, est refusée elle aussi
  (`tests/test_click_covering_integration.py`, 3 tests, sur Chrome 154.0.8037.97). Les 24 essais
  ci-dessus ont été mesurés sur Chromium 141.0.7390.37. Les mesures, et celles d'une page qui défile sans
  s'arrêter, sont dans `docs/JOURNAL.md`.
- Une page dense avec une bannière de consentement fixée sur sa partie basse (643 éléments à cliquer) : ses
  trois boutons sont repérés et listés en premier dans l'arbre, les éléments situés dessous disent
  `covered`, et un clic sur « Refuser » par son repère retire la bannière
  (`tests/test_ranking_integration.py`).
- La commande qui reprend un ancien fichier d'identifiants (voir le guide de l'opérateur) : sur des fichiers
  de la suite de tests dans un volume simulé, avec un refus testé pour chaque issue, et aucune valeur dans ce
  qu'elle affiche. Elle n'a pas été essayée sur un vrai volume gocryptfs.
- Un vrai volume gocryptfs 2.5.1, jetable, fait pour la vérification (pas celui de l'opérateur), monté sous
  Debian : `status` lisait `mounted` et `profile_encrypted: true`, et le serveur y a écrit son journal et créé
  `downloads/`. Opera 136 (Chromium 152), démarré avec son profil dans ce volume et un port de débogage sur
  l'interface de bouclage, a été attaché ; le serveur a ouvert une page d'un site marchand public, rempli son
  champ de recherche, appuyé sur Entrée et observé les résultats. Le texte saisi n'apparaît pas dans le
  journal.
- Claude Code, démarré depuis un terminal de bureau : `status` par Claude Code lisait
  `confirmation_channel: native`, donc l'affichage atteignait le serveur.
- Le processus du serveur sur l'entrée et la sortie standard : il n'écrit que le protocole sur sa sortie
  standard, ne tient aucune socket d'écoute et s'exécute avec une limite de fichier core à zéro.

Non testé :

- Une vraie fenêtre `kdialog` ou `zenity` : les tests emploient de faux programmes à leur place.
- Le volume gocryptfs de l'opérateur : les tests simulent la table des montages et le répertoire, et la
  vérification ci-dessus a employé un volume jetable.
- Brave, et les versions de Google Chrome autres que celle que l'opérateur a employée.
- La confirmation demandée par un client MCP de bureau (sollicitation) : testée avec le seul client du SDK
  MCP.
- Chrome 154 avec son profil gardé dans le propre répertoire chiffré de l'opérateur.
- Le zoom de page fait par la touche Ctrl + d'un navigateur qui n'est pas sans interface. Le protocole n'a
  aucune commande de zoom, si bien que les tests écrivent le zoom par défaut d'un profil vierge ; que le
  navigateur garde les deux sortes de la même façon n'est pas confirmé. Un écran dont l'échelle est
  réellement de 1,5 n'a pas été mesuré non plus ; les tests de clic ont les mêmes deux limites.
- Les tests de clic sur Chrome 154. La correction lit une propriété de la recherche de nœud du navigateur,
  mesurée sur Chromium 141 (il répond pour un point du document, pas de la vue) : si elle diffère sur une
  autre version, les tests d'un clic sur une page défilée y échouent. Un clic sur un élément à l'intérieur
  d'un cadre d'une page défilée n'a pas été mesuré.
- Une page qui bouge après la seconde recherche d'un clic et avant l'appui. Sur une page qui défile de 1000
  pixels CSS par seconde, 6 clics sur 144 n'ont donné aucune erreur et ne sont tombés nulle part ; aucun
  n'est tombé sur un autre bouton (Chrome 154.0.8037.97, `docs/JOURNAL.md`).
- Un refus dans la fenêtre de confirmation, donné par une personne.

Limites connues :

- Tant que le serveur est attaché au navigateur, les téléchargements sont refusés dans tous les onglets, y
  compris ceux de l'opérateur, sauf dans la courte fenêtre autour d'un clic ou d'une touche Entrée du
  serveur. Pour télécharger un fichier vous-même, arrêtez le serveur ou le client MCP ; les téléchargements
  fonctionnent de nouveau dès que la connexion du serveur prend fin.
- Un téléchargement qu'une page lance plus d'une demi-seconde après la fin d'un chargement est refusé.
- Accepter une boîte de dialogue dont le message demande un paiement, une suppression ou un changement de
  secret est toujours refusé : aucune capture ne peut être prise tant qu'une boîte de dialogue est ouverte.
  L'opérateur répond à une telle boîte de dialogue.
- La visibilité est approximative : le serveur ne calcule ni le contraste ni le rognage par `overflow`, si
  bien qu'un texte qu'un humain ne peut pas lire peut tout de même parvenir au modèle.
- Une observation tient au plus 150 repères et 400 lignes d'arbre (`marks_truncated`, `tree_truncated`). Au
  delà, le serveur garde d'abord ce qui se trouve dans une couche au-dessus de la page (une boîte de
  dialogue, un nœud modal, un nœud fixe ou collant peint sur du contenu visible, comme une bannière de
  cookies), listé sous une ligne `overlay` dans l'arbre, puis ce qui est visible et dégagé, puis ce qui est
  visible sous une couche (`covered: true`, et un clic dessus est refusé), puis le reste. Une bannière qui ne
  recouvre rien de visible n'est pas une couche et peut encore être coupée.
- Le contenu des cadres n'est jamais lu ; les cadres sont masqués dans l'image.
- Ce que le fournisseur du modèle conserve de ce qu'il reçoit n'est pas confirmé par ce projet.

## Crédits

mcp-server-diwall-browser est construit dans une collaboration asymétrique humain–LLM. Les rôles sont écrits
tels qu'ils ont été réellement tenus ; la liste complète est dans [CONTRIBUTORS.md](../../CONTRIBUTORS.md).

- **Ronan Davalan** : architecte et arbitre. Vision, exigences de sécurité, décisions, validation et essais.
- **Claude Code (Anthropic)** : ingénieur systèmes et développeur principal. Réalisation du serveur, tests, documentation et cadre de travail.
- **Claude Cloud (Anthropic)** : a écrit une partie du code sur des branches, à partir de consignes, relue avant chaque fusion.
- **Gemini (Google)** : conseiller d'architecture et synthétiseur ; a donné son nom au projet, **Diwall**, « digue » en breton.
- **Space Bunny Free (opencode)** : nettoyage et élagage du cadre de travail du projet au début du projet.
- **Vision locale** : Qwen3-VL (Alibaba), exécuté par Ollama, pour la localisation et la comparaison visuelles facultatives.

Licence : MIT, voir [LICENSE](../../LICENSE).
