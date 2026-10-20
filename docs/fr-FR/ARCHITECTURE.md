# Architecture

Pourquoi le serveur est construit comme il l'est. Comment l'installer et le lancer est dans
[OPERATOR_GUIDE.md](OPERATOR_GUIDE.md).

## Deux couches

```
modèle de langage, par son client MCP        lit, décide, explique à l'opérateur
        │  MCP sur l'entrée et la sortie standard : 14 outils
mcp-server-diwall-browser                    yeux et mains : observe, agit, refuse
        │  Chrome DevTools Protocol, 127.0.0.1 seulement
le navigateur de l'opérateur                 profil dédié, sessions authentifiées

à côté du serveur :  répertoire chiffré      identifiants en entrée ; journal, captures, téléchargements en sortie
                    fenêtre de confirmation  kdialog ou zenity, à laquelle l'opérateur répond
```

Le modèle apporte le jugement ; le serveur apporte ce que le modèle ne peut pas avoir seul : la même image et le même arbre
d'accessibilité que voit l'opérateur, des gestes dans la propre session de l'opérateur, et les refus qui ne dépendent pas
du jugement du modèle.

## Un pas

1. `open_tab` ou `navigate` charge une page dans un onglet que le serveur possède.
2. `observe` lit la page : le serveur prend un instantané, numérote avec des repères les éléments sur lesquels il peut
   agir, dessine les repères et les masques sur l'image, et rend l'image, l'arbre, les éléments et un `snapshot_id`.
3. Le modèle nomme un repère de cet instantané dans `click`, `fill`, `select_option` ou `press_key`.
4. Le serveur compte l'action dans les plafonds, trouve le nœud du navigateur derrière le repère, vérifie que c'est le même
   nœud, toujours dans la page, au point qu'il touchera, et que la page est sur une origine autorisée. Le garde-fou décide
   si l'action est verrouillée (paiement, suppression, changement de secret). Une action verrouillée est d'abord consignée
   au journal et capturée, puis la fenêtre de confirmation interroge l'opérateur ; l'identité est vérifiée de nouveau, le
   geste est fait, le serveur attend le chargement qu'il a lancé, et capture de nouveau.
5. Le résultat sort par le point de sortie unique, qui l'expurge et sépare ce que le serveur affirme (`server`) de ce que la
   page contient (`page_content`). Le journal reçoit une ligne.

## Modules

| Module | Rôle |
|---|---|
| `__main__` | point d'entrée : durcissement, journalisation vers la sortie d'erreur à travers l'expurgation, le serveur sur les flux standard |
| `hardening` | fichiers core désactivés, umask 077 |
| `origin` | analyse, normalisation et comparaison des adresses et des origines ; le joker de sous-domaine, un type à part |
| `output` | le point de sortie unique : expurgation, enveloppe, `server` et `page_content` tenus séparés |
| `sensitive_field` | le prédicat qui dit qu'un champ est sensible |
| `mount` | si le répertoire chiffré est monté comme attendu, et chaque chemin à l'intérieur |
| `credentials` | lecture des fichiers d'identifiants, résolution de l'un d'eux pour une origine exacte, et le schéma que le reste du code peut interroger |
| `credential_import` | une commande de l'opérateur, hors du serveur : convertit un ancien fichier d'identifiants, ou un répertoire de tels fichiers, vers le schéma, sans montrer aucune valeur |
| `setup` | un programme de l'opérateur, hors du serveur et importé par aucun de ses modules : lit la machine, crée ou ouvre le volume gocryptfs, écrit le profil de l'opérateur, lance le navigateur sur son profil et ferme les deux ; le terminal va à gocryptfs, qui seul lit le mot de passe |
| `journal` | le journal d'audit, une ligne JSON par appel |
| `operator_profile` | lecture du profil de l'opérateur ; avec `confirm`, les seuls modules du serveur qui lisent l'environnement |
| `profile_watch` | la trace d'un changement des listes d'origines du profil : la comparaison, les origines vues en dernier, l'avis |
| `cdp` | le client DevTools, sa liste de méthodes autorisées et ses scripts constants |
| `browser` | contrôles d'attachement, onglets que le serveur possède, navigation, boîtes de dialogue |
| `perception` | instantané, repères, arbre, image, masques, ce qui est gardé sous les plafonds, attente d'un texte ou d'un élément |
| `observation_filter` | les paramètres facultatifs `region` et `contains` de `observe` : ils réduisent l'arbre et les éléments, en ne lisant que des lignes déjà expurgées |
| `gate` | les catégories verrouillées, leur détection, les plafonds |
| `confirm` | la confirmation : texte de la fenêtre, fenêtre native ou demande du client |
| `actions` | clic, remplissage, choix d'une option, touches, défilement, boîtes de dialogue, par une seule chaîne de traitement |
| `tab_state` | le fichier des onglets que le serveur tient, pour que l'exécution suivante puisse fermer ceux qu'une exécution arrêtée a laissés ouverts |
| `evidence` | captures et téléchargements écrits dans le répertoire chiffré |
| `guide` | le texte que lit le modèle à l'ouverture de la session |
| `server` | la fabrique du serveur, ses dépendances, la session de navigateur, le journal de chaque appel |
| `tools/` | un fichier par outil, chacun par le point de sortie |

Chaque contrôle de sécurité n'existe que dans un seul module ; un second module qui en a besoin appelle le premier. Les
tests vérifient cela, et qu'aucun outil ne contourne le point de sortie.

## Garde-fous

- Boucle locale : le serveur ne s'attache que si chaque socket du port de débogage écoute sur la boucle locale (lu dans
  `/proc/net` à chaque attachement), si le profil déclaré n'est pas celui par défaut du navigateur, et si le navigateur
  annonce une adresse de débogage sur ce même port de la boucle locale.
- Origines autorisées : une page hors d'elles n'est ni lue ni touchée par une action ; seule son origine est rendue. Une
  entrée est une origine ou un joker de sous-domaine (`https://*.example.invalid`), écrit par l'opérateur ; un joker élargit
  la lecture et l'action, jamais la saisie. Un identifiant n'est saisi que sur l'origine exacte que nomme son fichier, et un
  fichier qui liste un joker est refusé.
- Actions verrouillées : le paiement, la suppression irréversible et le changement de secret sont détectés à partir d'un
  vocabulaire écrit dans le code et à partir de champs de carte. Les heuristiques et l'`intent_category` du modèle ne
  peuvent qu'ajouter un verrou ; rien n'en retire, et le profil de l'opérateur ne peut pas les configurer.
- Confirmation : une action verrouillée attend la réponse de l'opérateur dans une fenêtre que le serveur ouvre.
- Échec fermé : une table des montages illisible, un masque qui ne peut pas être dessiné ou un journal qui ne peut pas être
  écrit aboutit à un refus, jamais à un contrôle plus faible.

## Pourquoi un client DevTools écrit à la main, avec une liste de méthodes autorisées

Avec une bibliothèque d'automatisation générale, ce que le serveur peut envoyer au navigateur est ce que la bibliothèque
peut envoyer. Avec son propre client, c'est une liste de 27 méthodes dans un seul module, et un test la compare à chaque nom
de méthode du code. Les scripts ne sont envoyés qu'à partir d'une table de textes constants ; les arguments d'un outil
voyagent dans le champ `arguments` du protocole et ne sont jamais formatés dans un script.

## Pourquoi un repère est le nœud du navigateur, pas un rang

Un repère est lié au nœud du navigateur pour cet élément dans cet instantané. Un rang (« le troisième bouton ») bouge quand
la page insère un bandeau ou une ligne, et le même numéro désigne alors un autre élément, peut-être un bouton de paiement.
Lié au nœud, un élément déplacé ou remplacé est refusé (`element_stale`) et le modèle observe de nouveau.

## Pourquoi le serveur dessine les masques et les repères sur l'image

Dessiné dans la page, un calque peut être déplacé, caché ou lu par les propres scripts de la page. Dessiné sur les pixels
capturés, un masque couvre ce que le serveur a mesuré, et une valeur dessous ne peut pas être retrouvée depuis l'image ; si
un masque ne peut pas être dessiné, aucune image n'est publiée. Les repères correspondent aux boîtes de l'instantané auquel
le serveur confronte l'action quand le modèle agit. Chaque boîte est en pixels CSS : les pixels de l'appareil de
l'instantané sont divisés par le rapport des deux zones d'affichage du navigateur, si bien qu'un masque se place sur son
champ à toute échelle d'écran ; sans un tel rapport la page est refusée, non lue à l'échelle 1.

## Pourquoi une couche au-dessus de la page est gardée en premier

Une observation tient au plus 150 repères et 400 lignes d'arbre. Une bannière de consentement fixée sur la vue vient tard
dans le document : dans l'ordre du document, elle était coupée, et les éléments dessous étaient proposés comme si un clic
pouvait les atteindre. Sous les plafonds, le serveur garde, dans l'ordre : ce qui appartient à une couche au-dessus de la
page (une boîte de dialogue, un nœud modal, un nœud fixe ou collant qui se peint sur du contenu en vue) ; ce qui est en vue
et dégagé ; ce qui est en vue sous une couche (`covered`) ; le reste. Les lignes d'arbre de la couche viennent en premier
sous une ligne de couche. Les couches se reconnaissent à partir du seul instantané, par l'ordre de peinture et les
événements du pointeur, sans appel par élément ; un conteneur fixe autour de toute la page n'en est pas une.

## Pourquoi la confirmation est une fenêtre native

Le client MCP agit pour le modèle et peut répondre seul à une demande. Une fenêtre `kdialog` ou `zenity`, lancée par le
serveur sans shell, montre l'état lu dans le navigateur à cet instant et reçoit la réponse d'une personne, avec le refus
comme bouton par défaut et l'absence de réponse après 90 secondes comptée comme un refus. La demande propre au client
(élicitation) n'est employée que quand l'opérateur déclare que le client n'y répond jamais seul.

## Décisions écartées, et ce qui les rouvrirait

- Playwright ou une bibliothèque semblable : la liste de ce que le serveur envoie serait celle de la bibliothèque. Ce qui la
  rouvrirait : un navigateur dont le protocole de débogage n'est plus parlé par le client écrit à la main.
- Un outil qui exécute un script : il contournerait les repères, les contrôles d'identité, le garde-fou et les masques, et
  lirait tout ce qu'une page contient. Ce qui le rouvrirait : une tâche de l'opérateur que les outils ne peuvent pas faire,
  et alors sous la forme d'un nouveau script constant dans le client, non d'un script choisi par le modèle.
- Lire à l'intérieur des cadres : un cadre d'une autre origine est un autre site, souvent de paiement ou de publicité ; le
  lire enverrait au modèle une page hors des origines autorisées. Les cadres sont masqués. Ce qui le rouvrirait : un site des
  origines autorisées dont le contenu nécessaire vit dans un cadre de la même origine.
- Un transport par le réseau : un port en écoute peut être atteint par n'importe quel processus local ; les flux standard
  lient le serveur au client qui l'a lancé. Ce qui le rouvrirait : un client MCP dont l'opérateur a besoin et qui n'atteint
  les serveurs que par le réseau.
- Relever les plafonds : ils bornent ce que reçoit le modèle. Ce qui le rouvrirait : une page dont la seule couche est
  coupée par le classement actuel.
- L'ancien format d'identifiants lu par le serveur : son schéma est strict pour qu'un fichier ne soit jamais lu comme ce
  qu'il n'est pas. Ce qui le rouvrirait : un format que l'opérateur ne peut pas convertir une fois.
- Un outil qui convertit des identifiants : le modèle recevrait le chemin d'un fichier de secrets, et ses réponses vont à
  son fournisseur ; la commande s'exécute dans le shell de l'opérateur. Ce qui le rouvrirait : un opérateur sans shell, et
  un moyen de garder les valeurs hors de la session du modèle à cet endroit.
- Un second chemin de résolution, pour l'ancien format : deux règles qui lient un identifiant à une origine sont deux
  endroits à corriger. Ce qui le rouvrirait : rien ; `resolve` reste le seul lecteur du schéma unique.
- Un contexte de navigateur propre au serveur, pour que le refus des téléchargements reste dans les onglets du serveur : ses
  onglets ne partageraient pas la session authentifiée de l'opérateur, qui est la raison d'être du projet. Tant que le
  serveur est attaché, les téléchargements de l'opérateur sont donc refusés eux aussi. Ce qui le rouvrirait : un navigateur
  qui accepte le réglage de téléchargement pour un seul onglet, ce qui n'est pas confirmé.
