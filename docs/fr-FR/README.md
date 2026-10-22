# mcp-server-diwall-browser

[English](../../README.md) | **Français** | [Deutsch](../de-DE/README.md) | [Español](../es-ES/README.md)

Un serveur MCP qui donne à un modèle de langage les yeux et les mains d'un opérateur dans le navigateur
même de l'opérateur, par le protocole Chrome DevTools : le modèle voit la même capture d'écran et le
même arbre d'accessibilité que l'opérateur, et agit dans la même session authentifiée. Il lui faut un
navigateur de la famille Chromium que l'opérateur démarre avec un profil dédié et un port de débogage lié
à l'interface de bouclage, ainsi qu'un répertoire chiffré (gocryptfs) pour les identifiants, les captures
et les téléchargements.

## Avant le premier usage

Le serveur ne travaille pas sans répertoire chiffré : le profil du navigateur auquel il s'attache garde vos sessions
ouvertes, et elles ne doivent pas reposer en clair sur le disque. Il n'existe aucun mode sans chiffrement. Après
l'installation, la première commande, que vous lancez vous-même dans un terminal, est
`mcp-server-diwall-browser-setup` : elle lit votre machine et se termine sur la commande suivante à taper.

- `mcp-server-diwall-browser-setup create --temporary` fait un volume en mémoire, pour un essai. Le fermer, arrêter la
  machine ou terminer votre session le détruit, et avec lui les sessions des sites que vous avez ouvertes dans le
  navigateur, le journal d'audit et les captures.
- `mcp-server-diwall-browser-setup create` fait un volume durable.
- `mcp-server-diwall-browser-setup open`, `browser` et `close` ouvrent le volume, lancent le navigateur sur son profil
  et arrêtent les deux.

Vous tapez vous-même le mot de passe, à l'invite de `gocryptfs` ; l'assistant ne le lit jamais. Vous ouvrez vous-même
vos sessions sur vos sites, dans le navigateur qu'il lance. Après avoir ajouté le serveur à votre client MCP,
redémarrez le client : une session déjà ouverte ne le voit pas. Les étapes, les refus et la voie manuelle sont dans la
section « Premier lancement » de [docs/fr-FR/OPERATOR_GUIDE.md](OPERATOR_GUIDE.md).

## Les sites que le modèle lit

La liste des sites du profil de l'opérateur borne ce que le modèle lit et sur quoi il agit, jamais où il navigue. La clé `origin_policy`
choisit ce que fait le serveur d'une origine que la liste ne contient pas :

- `strict` (le défaut, aussi quand la clé manque) : l'origine est refusée, et seule l'origine est rendue.
- `discovery` : le serveur vous interroge dans une fenêtre, une fois par origine : `Refuser` (la réponse par défaut), `Cette séance` ou
  `Ajouter à la liste`, qui écrit cette origine exacte dans votre profil.
- `open` : tout site public est lu et agi, et son contenu part chez le fournisseur du modèle ; les adresses et les noms locaux restent sur la
  liste. C'est la politique la plus libre et la plus risquée, et le choix vous appartient.

Les paiements, les suppressions irréversibles et les changements de secret gardent leur confirmation, et un identifiant n'est saisi que sur
l'origine exacte à laquelle il est lié, dans les trois politiques. Le détail est dans la section 5 de
[docs/OPERATOR_GUIDE.md](OPERATOR_GUIDE.md).

## Installer

Sous Linux, le serveur est livré en paquets natifs. Téléchargez celui de votre système sur la page
[Releases](https://github.com/RonanDavalan/mcp-server-diwall-browser/releases) du dépôt ou depuis le [guide d'installation du site](https://mcp-diwall.davalan.fr/fr/guides/installer/), puis installez-le depuis le dossier
qui le contient :

| Système | Commande |
|---|---|
| Debian 13, Ubuntu 24.04 et 26.04 | `sudo apt install ./mcp-server-diwall-browser_0.0.1-1_all.deb` |
| Fedora 44 | `sudo dnf install ./mcp-server-diwall-browser-0.0.1-1.fc44.noarch.rpm` |
| openSUSE Leap 16.0 | `sudo zypper install --allow-unsigned-rpm ./mcp-server-diwall-browser-0.0.1-1.leap160.noarch.rpm` |
| Arch Linux | `sudo pacman -U ./mcp-server-diwall-browser-0.0.1-1-any.pkg.tar.zst` |

L'installation, le démarrage du serveur par un compte ordinaire et le retrait sont validés en conteneur sur
ces systèmes. Un conteneur ne montre ni la fenêtre de confirmation, ni le montage du répertoire chiffré, ni
un navigateur. Debian 12 et Ubuntu 22.04 ne sont pas pris en charge : leur Python est plus ancien que 3.12.
L'installation demande une connexion au réseau : le paquet crée un environnement virtuel Python et y installe
les dépendances, aux versions que le projet a testées. La page de manuel est `man mcp-server-diwall-browser`.
Pour installer depuis un clone du dépôt, suivez [docs/fr-FR/OPERATOR_GUIDE.md](OPERATOR_GUIDE.md).

Une machine ARM 64 bits prend le même `.deb`, puisque le paquet ne dépend pas de l'architecture : un Raspberry Pi 4 sous Debian 13 a été essayé (voir « Ce qui est testé, ce qui ne l'est pas »). Le navigateur y est le Chromium de la distribution.

## Le brancher à un client MCP

Le serveur parle MCP sur l'entrée et la sortie standard. Avec un paquet installé, la commande est
`mcp-server-diwall-browser` et le profil d'opérateur est lu à sa place habituelle :

```
claude mcp add --scope user diwall-browser -- mcp-server-diwall-browser
```

Depuis un clone du dépôt, avec Claude Code :

```
claude mcp add --scope user diwall-browser \
  -e MCP_DIWALL_BROWSER_PROFILE="$HOME/.config/mcp-server-diwall-browser/profile.json" \
  -- "$HOME/mcp-server-diwall-browser/.venv/bin/python" -m mcp_server_diwall_browser
```

Pour un autre client MCP, le bloc générique `mcpServers`. Le client ne développe pas `~` : cette
commande affiche le bloc avec votre répertoire personnel dans les chemins, prêt à coller dans sa
configuration :

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

Adaptez `~/mcp-server-diwall-browser` à l'endroit où vous avez cloné le dépôt, et le chemin du
profil à l'endroit où vous avez écrit votre profil d'opérateur. La variable peut être omise quand le profil
d'opérateur est à sa place habituelle, `~/.config/mcp-server-diwall-browser/profile.json`. La forme de la
commande `claude mcp add` a été vérifiée avec `claude mcp add --help`, puis lancée par l'opérateur sous
Debian : `claude mcp list` montrait le serveur connecté. Les tests démarrent le serveur exactement comme ces
deux blocs : `python -m mcp_server_diwall_browser` avec `MCP_DIWALL_BROWSER_PROFILE` définie.

Tout ce qui précède (installer, démarrer le navigateur, ouvrir le répertoire chiffré, écrire le profil
d'opérateur) est dans [docs/fr-FR/OPERATOR_GUIDE.md](OPERATOR_GUIDE.md). Le fonctionnement et ses raisons sont
dans [docs/fr-FR/ARCHITECTURE.md](ARCHITECTURE.md).

## Ce que le modèle voit

![La page de démonstration telle que le modèle la reçoit](../images/observe-demo.png)

L'image ci-dessus a été produite par `scripts/smoke_test.py` à partir de la page de démonstration locale
(`docs/demo/index.html`). Les repères numérotés sont dessinés par le serveur : une action désigne un
élément par son repère. Le champ de mot de passe, que la page a rempli elle-même, est masqué par un
rectangle plein, et sa valeur est retenue dans le texte que reçoit le modèle (`redacted_fields` vaut 1).

## Ce qui est testé, ce qui ne l'est pas

Testé :

- La suite complète, sous Debian 13 avec Python 3.12.11 et les dépendances de `requirements.lock` : sans
  navigateur, 2100 tests passent, 1 est ignoré (un test qui ne s'exécute qu'en root) et les 589 qui exigent un
  navigateur sont désélectionnés ; avec Google Chrome 156.0.8078.47 (sans interface, profil temporaire vierge),
  2 602 tests passent et 1 est ignoré, sans échec attendu.
- Tests de téléchargement : deux échouaient au hasard, 7 et 8 fois sur 30 exécutions de chacun seul, parce
  que le serveur refermait le réglage de téléchargement avant que le navigateur ait choisi quoi faire du
  téléchargement. Après la modification, ils ont échoué 3 et 1 fois sur 130 exécutions de chacun seul ; les 3
  étaient une lecture, dans le test lui-même, d'un fichier que le navigateur renomme, qui est désormais un
  seul assistant ignorant le renommage. Depuis, chacun des deux seul a échoué 0 fois sur 200 exécutions, deux
  fois de suite. Un troisième test, qui refermait aussitôt le réglage et attendait qu'un téléchargement
  tardif soit refusé, a échoué 4 fois sur 200 seul : le vrai navigateur peut choisir avant que le serveur
  referme le réglage, si bien que ce test est remplacé par des tests sur un navigateur simulé. Les mesures et
  ce qui reste sont dans `docs/JOURNAL.md`.
- L'opérateur a rejoué une révision antérieure sous Debian (Python 3.13.5) avec Chrome 154.0.8037.97 : sans
  navigateur 1 450 ont passé et 1 a été ignoré (un test qui ne s'exécute qu'en root) ; avec lui 119 ont passé
  et 6 ont échoué comme prévu, les six du zoom de page, clos depuis ; les deux tests de téléchargement seuls,
  0 échec sur 40 exécutions chacun. L'opérateur a ensuite rejoué `ff8c433` (la révision avant les tests de
  clic) sur le même navigateur : sans navigateur 1 546 ont passé, 1 a été ignoré et 188 désélectionnés ; les
  trois fichiers de zoom et d'échelle, 125 passés ; les 188 tests de navigateur ont tous passé ; les deux
  tests de téléchargement et les deux tests qui remplacent l'instable, chacun seul, 0 échec sur 40
  exécutions. Les tests de clic n'y ont pas été rejoués.
- `scripts/smoke_test.py` rend 0 contre Google Chrome 155.0.8059.84 et 3 quand aucun navigateur n'est trouvé.
- Un Raspberry Pi 4 Model B de 8 Go, Raspberry Pi OS sur Debian 13 (aarch64), Python 3.13.5 et Chromium 155.0.8059.79, le 22 octobre 2026 : le `.deb` final installé par `apt` ; le premier lancement de l'assistant d'installation de bout en bout avec le vrai `gocryptfs` (volumes temporaire et durable, navigateur, fermeture) ; `scripts/smoke_test.py` qui rend 0 ; le serveur piloté par un vrai client MCP sur un Chromium visible, avec fenêtre et non sans interface, sur un écran X11 virtuel, avec la fenêtre de confirmation native de `zenity` ouverte puis laissée expirer ; puis le paquet retiré et purgé, sans fichier restant. La suite complète y a donné 2 694 tests réussis et 1 ignoré en 15 minutes (cinq sont des mesures de ce poste que le dépôt ne contient pas). Sur ce réseau, la première page chargée par un Chromium qui vient de démarrer a pris 15 à 26 secondes, ce qui a fait échouer trois tests lors d'un premier passage ; avec le navigateur coupé du réseau, ils réussissent tous (guide, section 12).
- Les repères et les masques à une échelle d'écran de 1, 1,5 et 2, sur un Chromium 141 sans interface : le
  cadre de chaque repère visible est à moins de deux pixels du cadre que donne le navigateur lui-même, chaque
  pixel du champ de mot de passe de la page de démonstration est sous son masque, et le masque ne dépasse pas
  de plus de deux pixels (`tests/test_scale_integration.py`). Avant la correction, un masque ne couvrait pas
  son champ à une échelle supérieure à 1. Un vrai écran à 1,5 n'a pas été mesuré.
- Le zoom de page, à 100, 125, 150 et 200 % et à des échelles d'écran de 1 et 1,5, sur un Chromium 141 sans
  interface dont le profil vierge garde le zoom comme valeur par défaut : l'image que reçoit le modèle est la
  vue entière, le champ de mot de passe de la page de démonstration est sous son masque, comme celui d'une
  page défilée pour qu'il se trouve près du bas ou au milieu de la vue ; le masque ne dépasse pas de plus de
  deux pixels du champ, et dans les 16 placements mesurés aucun bord de masque ne se trouvait à l'intérieur
  de son champ (`tests/test_zoom_integration.py`, `tests/test_zoom_scrolled_integration.py`). Avant la
  correction, à 125, 150 et 200 %, 0 % du champ était couvert et `capture` lisait `ok`. Aucune image n'est
  publiée (`capture` lit `unavailable`, l'arbre est toujours servi) quand le navigateur ne donne aucun zoom
  utilisable, quand le zoom change pendant la capture, ou quand l'image s'écarte de plus de deux pixels de la
  taille de la vue sur l'un des axes (`tests/test_perception_zoom.py`, qui n'exige aucun navigateur). Sous
  Chrome 154, l'opérateur a mesuré la taille de l'image découpée telle que le navigateur la lit (la vue à
  chaque zoom, un pixel de moins en hauteur à 125 %) ; les tests de zoom y ont été rejoués plus tard
  (ci-dessous).
- Un clic du serveur atteint sa cible à des zooms de page de 100, 125, 150 et 200 % et à des échelles
  d'écran de 1 et 1,5, sur un Chromium 141 sans interface dont le profil vierge garde le zoom comme valeur
  par défaut. Le clic est celui du serveur (`Actions.click`, avec son garde-fou et ses limites) ; la page de
  test compte ce que reçoit chacun de ses boutons et une session séparée lit les comptes. À chacune des huit
  combinaisons, un bouton de 120 sur 32 pixels CSS, un bouton de 16 sur 16 entre deux leurres à 4 pixels, un
  bouton à deux vues et demie plus bas, et le grand bouton après que la page a été défilée pour le mettre au
  milieu de la vue, ont reçu exactement un clic, aucun leurre n'en a reçu et aucun n'est tombé hors d'un
  bouton. Sur une page défilée dans les deux sens, le clic atteint le bouton mis au milieu de la vue, et un
  bouton couvert par une bannière fixée à la vue est refusé (`element_obscured`) sans rien cliquer
  (`tests/test_click_integration.py`, 264 tests ; `tests/test_click_targets_page.py`, 51, et
  `tests/test_click_lookup_point.py`, 20, n'exigent aucun navigateur). Le même contrôle refuse un clic envoyé
  au point du cadre multiplié par le zoom, divisé par lui, ou multiplié par l'échelle d'écran (64 clics de ce
  genre sur 64 n'ont atteint aucun bouton), ainsi qu'un serveur qui interroge le navigateur sur le point de
  la vue. Avant la correction, 20 des 32 essais sur la première page échouaient, ceux qui exigeaient un
  défilement, et un bouton couvert par la bannière était accepté et la bannière cliquée, aux huit
  combinaisons : le navigateur répond à la recherche de nœud pour un point du document, et le clic lui
  donnait un point de la vue. Les mesures sont dans `docs/JOURNAL.md`.
- Un clic sur une page qui bouge au premier mouvement du pointeur (un menu qui s'ouvre au survol, une
  bannière qui glisse). La page de test décale chaque cible de 60 pixels CSS vers le bas au premier
  mouvement de la souris et place un bouton intrus où se trouvait la cible. À chacune des huit combinaisons
  de zoom de page et d'échelle d'écran, pour chacun de trois boutons (un grand, un de 16 sur 16 entre deux
  leurres, un à deux vues et demie plus bas), un clic du serveur atteint la cible une fois ou est refusé
  comme couvert (`element_obscured`) sans rien cliquer, et dans les 24 essais il est refusé à chaque fois
  (`tests/test_click_shifting_integration.py`, 80 tests ; `tests/test_click_shifting_page.py`, 30, et
  `tests/test_click_moving_page.py`, 10, n'exigent aucun navigateur). Avant la modification, les mêmes 24
  essais ne rendaient aucune erreur et le clic atteignait l'intrus. Après un refus, un clic sur une nouvelle
  observation atteint sa cible. Le serveur redemande désormais au navigateur ce qui se trouve sous le point
  après le mouvement et avant l'appui, à chaque fois, de sorte qu'une cible couverte là où elle se trouve,
  par un bouton affiché au premier mouvement, est refusée elle aussi
  (`tests/test_click_covering_integration.py`, 3 tests, sur Chrome 154.0.8037.97). Les 24 essais
  ci-dessus ont été mesurés sur Chromium 141.0.7390.37. Les mesures, et celles d'une page qui défile sans
  s'arrêter, sont dans `docs/JOURNAL.md`.
- Une page dense avec une bannière de consentement fixée sur sa partie basse (643 éléments à cliquer) : ses
  trois boutons sont repérés et listés en premier dans l'arbre, les éléments situés dessous disent
  `covered`, et un clic sur « Refuser » par son repère retire la bannière
  (`tests/test_ranking_integration.py`).
- L'assistant d'installation, `mcp-server-diwall-browser-setup` : 50 tests sur une machine simulée (tables de montage,
  gestionnaires de paquets, pas de `/dev/fuse`, pas de terminal, un volume LUKS) ; puis, le 20 octobre 2026, avec le vrai
  gocryptfs 2.5.1 dans un pseudo-terminal, sur des volumes jetables : `create --temporary`, le serveur lisant `mounted`,
  `browser` (Google Chrome 155.0.8059.84 sans écran, rattaché, `profile_encrypted: true`), `close` (plus de volume en
  mémoire, aucun fichier du profil du navigateur sur le disque), puis `create`, `close` et `open` d'un volume durable ; et
  dans un conteneur Debian 13 propre, où `check` a donné `sudo apt install gocryptfs fuse3`, qui a réussi.
- La commande qui reprend un ancien fichier d'identifiants (voir le guide de l'opérateur) : sur des fichiers
  de la suite de tests dans un volume simulé, avec un refus testé pour chaque issue, et aucune valeur dans ce
  qu'elle affiche. Elle n'a pas été essayée sur un vrai volume gocryptfs.
- Un vrai volume gocryptfs 2.5.1, jetable, fait pour la vérification (pas celui de l'opérateur), monté sous
  Debian : `status` lisait `mounted` et `profile_encrypted: true`, et le serveur y a écrit son journal et créé
  `downloads/`. Opera 136 (Chromium 152), démarré avec son profil dans ce volume et un port de débogage sur
  l'interface de bouclage, a été attaché ; le serveur a ouvert une page d'un site marchand public, rempli son
  champ de recherche, appuyé sur Entrée et observé les résultats. Le texte saisi n'apparaît pas dans le
  journal.
- La politique d'origines (`strict`, `discovery`, `open`) : `tests/test_origin_policy.py` et `tests/test_origin_policy_server.py` sur un navigateur simulé, et `tests/test_origin_policy_integration.py`, 12 tests du serveur lancé par son point d'entrée contre Google Chrome 155.0.8059.84, avec de faux programmes de fenêtre : un portail qui renvoie vers une seconde origine, une redirection vers une origine inconnue, les trois réponses de la fenêtre (le profil relu après `Ajouter à la liste`), les noms locaux refusés avec `open`, un paiement qui demande toujours sa fenêtre, et la politique changée dans le profil sans redémarrage. Les fenêtres des vrais `kdialog` et `zenity` ont été montrées dans un écran virtuel et répondues par des frappes (Entrée et Échap donnent le refus) ; une personne y a répondu le 22 octobre 2026 (l'entrée suivante).
- La fenêtre de confirmation, affichée sur le poste de l'opérateur et à laquelle une personne a répondu le 22 octobre 2026
  (révision `0f4f613`), avec le propre volume gocryptfs de l'opérateur monté et une page de paiement locale, dans Opera
  138.0.6044.24, une fois avec `kdialog` 25.04.0 et une fois avec `zenity` 4.1.90 (le serveur ne trouvait que cet outil) :
  avec chacun, un refus a rendu `confirmation_denied` et la page n'a rien reçu ; un accord a fait le clic et la page a reçu
  une requête ; sans réponse, la demande a expiré après 90,4 secondes (`confirmation_expired`) et la page n'a rien reçu. Le
  journal porte une ligne `requested` et une ligne de l'issue pour chacune des six demandes.
- Claude Code, démarré depuis un terminal de bureau : `status` par Claude Code lisait
  `confirmation_channel: native`, donc l'affichage atteignait le serveur.
- Le processus du serveur sur l'entrée et la sortie standard : il n'écrit que le protocole sur sa sortie
  standard, ne tient aucune socket d'écoute et s'exécute avec une limite de fichier core à zéro.

Non testé :

- Une machine ARM 64 bits autre que ce Raspberry Pi 4 (un Raspberry Pi 5, une autre distribution), une session de bureau Wayland, et une personne qui répond à la fenêtre de confirmation sur ce poste.
- L'assistant d'installation sur une machine qui a un espace d'échange (son rapport est essayé sur une liste simulée), et
  sa commande `browser` avec Brave, Chromium ou Opera.
- Brave, et les versions de Google Chrome autres que celle que l'opérateur a employée.
- La confirmation demandée par un client MCP de bureau (sollicitation) : testée avec le seul client du SDK
  MCP.
- Chrome 154 avec son profil gardé dans le propre répertoire chiffré de l'opérateur.
- Le zoom de page fait par la touche Ctrl + d'un navigateur qui n'est pas sans interface. Le protocole n'a
  aucune commande de zoom, si bien que les tests écrivent le zoom par défaut d'un profil vierge ; que le
  navigateur garde les deux sortes de la même façon n'est pas confirmé. Un écran dont l'échelle est
  réellement de 1,5 n'a pas été mesuré non plus ; les tests de clic ont les mêmes deux limites.
- Les tests de clic sur Chrome 154. La correction lit une propriété de la recherche de nœud du navigateur,
  mesurée sur Chromium 141 (il répond pour un point du document, pas de la vue) : si elle diffère sur une
  autre version, les tests d'un clic sur une page défilée y échouent. Un clic sur un élément à l'intérieur
  d'un cadre d'une page défilée n'a pas été mesuré.
- Un contrôle qui agit sur l'appui plutôt que sur le clic, placé sous le pointeur par un script à l'instant de
  l'appui : l'appui est contrôlé après avoir été fait, si bien que les gestionnaires d'appui de la page se sont
  exécutés. La garde d'une touche et d'un texte est un script de la page : une page qui l'attend la contourne, et
  elle ne voit pas les événements d'un cadre (Google Chrome 156.0.8078.47, `docs/OPERATOR_GUIDE.md`, section 7).

Limites connues :

- Le répertoire chiffré protège autant que son mot de passe : qui a le mot de passe en lit le contenu, et un mot de passe faible ou réutilisé protège peu.
- Les garde-fous sont ceux mesurés à la date de cette version, par les essais et les mesures que ce document nomme. Un modèle plus récent que ceux essayés peut trouver un contournement qu'aucun essai n'a vu.
- Qui ne veut pas de ces garde-fous trouvera d'autres serveurs MCP qui pilotent un navigateur sans eux.
- Tant que le serveur est attaché au navigateur, les téléchargements sont refusés dans tous les onglets, y
  compris ceux de l'opérateur, sauf dans la courte fenêtre autour d'un clic ou d'une touche Entrée du
  serveur. Pour télécharger un fichier vous-même, arrêtez le serveur ou le client MCP ; les téléchargements
  fonctionnent de nouveau dès que la connexion du serveur prend fin.
- Un téléchargement qu'une page lance plus d'une demi-seconde après la fin d'un chargement est refusé.
- Accepter une boîte de dialogue `confirm` ou `prompt` demande une confirmation à l'opérateur dans une fenêtre, quel que
  soit son message.
  Accepter une boîte de dialogue dont le message demande un paiement, une suppression ou un changement de
  secret est toujours refusé : aucune capture ne peut être prise tant qu'une boîte de dialogue est ouverte.
    L'opérateur répond à une telle boîte de dialogue.
- Un secret qu'un site écrit dans le chemin d'une adresse est montré tel quel dans les résultats ; seuls la valeur d'un
  paramètre de requête dont le nom contient un mot tel que `token`, et le fragment, sont rédigés.

- La visibilité est approximative : le serveur ne calcule ni le contraste ni le rognage par `overflow`, si
  bien qu'un texte qu'un humain ne peut pas lire peut tout de même parvenir au modèle.
- Une observation tient au plus 150 repères et 400 lignes d'arbre (`marks_truncated`, `tree_truncated`). Au
  delà, le serveur garde d'abord ce qui se trouve dans une couche au-dessus de la page (une boîte de
  dialogue, un nœud modal, un nœud fixe ou collant peint sur du contenu visible, comme une bannière de
  cookies), listé sous une ligne `overlay` dans l'arbre, puis ce qui est visible et dégagé, puis ce qui est
  visible sous une couche (`covered: true`, et un clic dessus est refusé), puis le reste. Une bannière qui ne
  recouvre rien de visible n'est pas une couche et peut encore être coupée.
- Le contenu des cadres n'est jamais lu ; les cadres sont masqués dans l'image.
- Ce que le fournisseur du modèle conserve de ce qu'il reçoit n'est pas confirmé par ce projet.

## Crédits

mcp-server-diwall-browser est construit dans une collaboration asymétrique humain–LLM. Les rôles sont écrits
tels qu'ils ont été réellement tenus ; la liste complète est dans [CONTRIBUTORS.md](../../CONTRIBUTORS.md).

- **Ronan Davalan** : architecte et arbitre. Vision, exigences de sécurité, décisions, validation et essais.
- **Claude Code (Anthropic ; Opus 5.5, Sonnet 5.5 et Haiku 5.5)** : ingénieur systèmes et développeur principal. Réalisation du serveur, tests, documentation, cadre de travail, texte et entretien du site du projet, et rapport de terrain du premier lancement depuis le paquet Debian.
- **Claude Cloud (Anthropic)** : a écrit une partie du code sur des branches, à partir de consignes, relue avant chaque fusion.
- **Muse Spark 1.3 Contributor (Meta, par OpenCode)** : premier usage du serveur par un modèle autre que Claude, sur une instance locale d'une application web, dont le rapport de terrain a orienté les corrections suivantes ; la feuille de style et la mise en page du site du projet, retenues parmi plusieurs essais, le texte et le comportement des pages restés intacts ; deux relectures du site, avec des propositions de formulation en quatre langues restées à l'état de brouillon et des défauts relevés, corrigés.
- **DeepSeek V4 Pro (DeepSeek, par OpenCode)** : audit de sécurité indépendant du tag `v0.0.1`, sur une session vierge ; quatre de ses sept constats ont été confirmés au tri et corrigés.
- **GLM 5.3 (Zhipu, par OpenCode)** : audit de sécurité indépendant du même tag, sur une session vierge, avec des reproductions sur un navigateur sans écran ; trois de ses sept constats ont été confirmés au tri et corrigés.
- **Gemini (Google)** : conseiller d'architecture et synthétiseur ; a donné son nom au projet, **Diwall**, « digue » en breton.
- **gemini-3.5-flash (Google, par OpenCode)** : a testé le branchement par un agent dédié : les outils n'atteignent que cet agent, et son shell demande avant d'exécuter.
- **Space Bunny Free (opencode)** : nettoyage et élagage du cadre de travail du projet au début du projet.

Licence : MIT, voir [LICENSE](../../LICENSE).
