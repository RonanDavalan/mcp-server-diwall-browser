"""Decide whether a form field carries a secret or payment data.

Why this module exists:
    Masking a field in the perception, refusing to type into it and arming
    the payment gate all depend on the same question. Two lists would drift
    apart and one of them would miss a field, so there is a single predicate
    and every caller asks it.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

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
    "motdepasse",
    "secret",
    "token",
    "jeton",
    "apikey",
    "cardnumber",
    "creditcard",
    "numerodecarte",
    "numerocarte",
    "cryptogramme",
    "securitycode",
    "onetimecode",
    "verificationcode",
    "codeverification",
    "socialsecurity",
    "securitesociale",
)
CARD_SUBSTRINGS = (
    "cardnumber",
    "creditcard",
    "numerodecarte",
    "numerocarte",
    "cryptogramme",
    "securitycode",
)

_WORD_SPLIT_RE = re.compile(r"[^a-z0-9]+")


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
    """Return whether the field is sensitive, why, and whether it is a card field.

    ``tag`` and ``role`` are accepted so that every caller passes the same
    description of an element; no rule depends on them today.
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


def _normalize(text: str) -> tuple[set[str], str]:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    words = [w for w in _WORD_SPLIT_RE.split(plain) if w]
    return set(words), "".join(words)
