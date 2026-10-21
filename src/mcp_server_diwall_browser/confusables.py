"""Plier un texte de la page avant de le comparer à un mot : minuscules, sans accents, en lettres latines.

Pourquoi ce module existe :
    Une page écrit ses libellés et ses noms de champ ; la lettre cyrillique U+0440 y passe pour un « p » latin
    et échappe à toute comparaison avec un mot du vocabulaire de la porte ou de la liste des champs
    sensibles. Le pliage est écrit ici, une seule fois, et chaque comparaison de texte venu de la page
    le traverse. La table ne porte que des lettres que l'œil ne distingue pas dans une police courante.
"""

from __future__ import annotations

import unicodedata

# Lettres minuscules, par point de code : un caractère confondable écrit tel quel serait lui-même un piège dans ce
# fichier (`tests/test_layout.py` refuse tout caractère de cette sorte dans le code).
_LOOKALIKES = (
    # Cyrillique.
    (0x0430, "a"),
    (0x0435, "e"),
    (0x043e, "o"),
    (0x0440, "p"),
    (0x0441, "c"),
    (0x0443, "y"),
    (0x0445, "x"),
    (0x0456, "i"),
    (0x0458, "j"),
    (0x0455, "s"),
    (0x04bb, "h"),
    (0x0501, "d"),
    (0x051b, "q"),
    (0x051d, "w"),
    (0x043a, "k"),
    (0x04cf, "l"),
    # Grec.
    (0x03b1, "a"),
    (0x03bf, "o"),
    (0x03c1, "p"),
    (0x03bd, "v"),
    (0x03b9, "i"),
    (0x03ba, "k"),
    (0x03c5, "u"),
    (0x03c7, "x"),
    (0x03f2, "c"),
    # Latin étendu : alpha, g à crochet, i sans point.
    (0x0251, "a"),
    (0x0261, "g"),
    (0x0131, "i"),
)
_TO_LATIN = str.maketrans({chr(code): latin for code, latin in _LOOKALIKES})


def fold(text: str) -> str:
    """Le texte en minuscules, sans accents, où chaque lettre confondable d'un autre alphabet est sa lettre latine.

    La table passe avant et après la décomposition : avant, parce que certaines lettres (le sigma lunaire) se
    décomposent en une autre lettre que celle qu'elles imitent ; après, parce que d'autres ne tombent dans la
    table qu'une fois leur accent retiré (la lettre cyrillique yi, U+0457).
    """
    decomposed = unicodedata.normalize("NFKD", text.lower().translate(_TO_LATIN))
    return "".join(char for char in decomposed if not unicodedata.combining(char)).translate(_TO_LATIN)
