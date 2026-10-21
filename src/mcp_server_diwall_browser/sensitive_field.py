"""Décider si un champ de formulaire porte un secret ou des données de paiement.

Pourquoi ce module existe :
    Masquer un champ dans la perception, refuser d'y taper et armer la porte de paiement dépendent de
    la même question. Deux listes divergeraient et l'une d'elles manquerait un champ : il y a donc un
    seul prédicat et chaque appelant l'interroge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .confusables import fold

SENSITIVE_AUTOCOMPLETE = frozenset(
    {
        "current-password",
        "new-password",
        "one-time-code",
        "cc-number",
        "cc-csc",
        "cc-exp",
        "cc-exp-month",
        "cc-exp-year",
    }
)
CARD_AUTOCOMPLETE = frozenset({"cc-number", "cc-csc", "cc-exp", "cc-exp-month", "cc-exp-year"})

SENSITIVE_WORDS = frozenset(
    {"pass", "pwd", "mdp", "pin", "otp", "totp", "cvv", "cvc", "iban", "bic", "ssn", "nir"}
)
CARD_WORDS = frozenset({"cvv", "cvc", "iban", "bic"})

SENSITIVE_SUBSTRINGS = (
    "password",
    "passwd",
    "passcode",
    "passphrase",
    "passkey",
    "motdepasse",
    "secret",
    "token",
    "jeton",
    "apikey",
    "cardnumber",
    "creditcard",
    "numerodecarte",
    "numerocarte",
    "numcarte",
    "cardno",
    "cryptogramme",
    "securitycode",
    "onetimecode",
    "verificationcode",
    "codepin",
    "codeverification",
    "codedeverification",
    "socialsecurity",
    "securitesociale",
)
CARD_SUBSTRINGS = (
    "cardnumber",
    "creditcard",
    "numerodecarte",
    "numerocarte",
    "numcarte",
    "cardno",
    "cryptogramme",
    "securitycode",
)

_WORD_SPLIT_RE = re.compile(r"[^a-z0-9]+")

# Attribut HTML -> mot-clé de is_sensitive. Un appelant qui n'a que les attributs d'un élément les passe
# par cette table, pour que les noms que le prédicat regarde soient écrits ici et nulle part ailleurs.
ATTRIBUTE_ARGUMENTS = {
    "type": "input_type",
    "name": "name",
    "id": "element_id",
    "autocomplete": "autocomplete",
    "aria-label": "aria_label",
    "placeholder": "placeholder",
}


@dataclass(frozen=True)
class SensitiveVerdict:
    sensitive: bool
    reason: str
    card: bool


def is_sensitive(
    *,
    tag: str | None = None,
    input_type: str | None = None,
    name: str | None = None,
    element_id: str | None = None,
    autocomplete: str | None = None,
    aria_label: str | None = None,
    placeholder: str | None = None,
    role: str | None = None,
) -> SensitiveVerdict:
    """Rendre si le champ est sensible, pourquoi, et s'il s'agit d'un champ de carte.

    ``tag`` et ``role`` sont acceptés pour que chaque appelant passe la même description d'un élément ;
    aucune règle n'en dépend aujourd'hui.
    """
    del tag, role
    reasons: list[str] = []
    card = False

    if (input_type or "").strip().lower() == "password":
        reasons.append("type_password")

    tokens = set((autocomplete or "").lower().split())
    if tokens & SENSITIVE_AUTOCOMPLETE:
        reasons.append("autocomplete")
    if tokens & CARD_AUTOCOMPLETE:
        card = True

    word_hit = False
    substring_hit = False
    for text in (name, element_id, aria_label, placeholder):
        if not text:
            continue
        words, compact = _normalize(text)
        matched_words = words & SENSITIVE_WORDS
        matched_substrings = [s for s in SENSITIVE_SUBSTRINGS if s in compact]
        word_hit = word_hit or bool(matched_words)
        substring_hit = substring_hit or bool(matched_substrings)
        if matched_words & CARD_WORDS or any(s in compact for s in CARD_SUBSTRINGS):
            card = True
    if word_hit:
        reasons.append("name_token")
    if substring_hit:
        reasons.append("compact_substring")

    if not reasons:
        return SensitiveVerdict(sensitive=False, reason="none", card=False)
    return SensitiveVerdict(sensitive=True, reason=reasons[0], card=card)


def _split_camel_case(text: str) -> str:
    """Couper ``userPin`` en ``user Pin`` et ``APIKey`` en ``API Key``.

    Sans cela, un mot comme ``pin`` caché dans un nom en camelCase n'est jamais un mot à part, et le
    champ reste sans masque.
    """
    pieces = []
    for index, char in enumerate(text):
        if index and char.isupper():
            previous = text[index - 1]
            following = text[index + 1] if index + 1 < len(text) else ""
            if previous.islower() or previous.isdigit() or (previous.isupper() and following.islower()):
                pieces.append(" ")
        pieces.append(char)
    return "".join(pieces)


def _normalize(text: str) -> tuple[set[str], str]:
    words = [w for w in _WORD_SPLIT_RE.split(fold(_split_camel_case(text))) if w]
    return set(words), "".join(words)
