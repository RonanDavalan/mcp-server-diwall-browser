"""Mettre le processus dans un état sûr avant qu'aucun secret soit chargé.

Pourquoi ce module existe :
    Effacer une chaîne Python après usage n'enlève pas ses octets de la mémoire, si bien qu'un
    vidage mémoire écrit sur le disque après un plantage contiendrait chaque secret résolu. La seule
    défense fiable est d'interdire les vidages mémoire avant que le premier secret soit lu, et de
    refuser d'en lire un si cela ne peut pas être fait.
"""

from __future__ import annotations

import os
import resource


class HardeningError(RuntimeError):
    """Le processus n'a pas pu être mis dans l'état exigé pour détenir des secrets."""


def disable_core_dumps() -> None:
    """Mettre à zéro les deux limites de vidage mémoire ; lever si le noyau refuse.

    L'appeler de nouveau est sans danger : abaisser à zéro une limite déjà nulle est accepté, et la
    limite est relue pour qu'un échec silencieux ne passe pas.
    """
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
    except (ValueError, OSError) as exc:
        raise HardeningError("core dumps could not be disabled") from exc
    if soft != 0 or hard != 0:
        raise HardeningError("core dump limit is not zero after setting it")


def restrict_umask() -> int:
    """Rendre privé par défaut chaque fichier que le processus crée.

    Rend le masque précédent pour que l'appelant puisse le journaliser si besoin.
    """
    return os.umask(0o077)
