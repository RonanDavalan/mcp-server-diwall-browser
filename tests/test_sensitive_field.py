from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from mcp_server_diwall_browser.sensitive_field import SensitiveVerdict, is_sensitive

# (attributes, sensitive?, card?, reason or None)
FIELD_TABLE: list[tuple[dict[str, str], bool, bool, str | None]] = [
    # Sensitive: passwords, one-time codes, tokens, identity numbers.
    ({"input_type": "password"}, True, False, "type_password"),
    ({"input_type": "PASSWORD", "name": "anything"}, True, False, "type_password"),
    ({"name": "user_pwd"}, True, False, "name_token"),
    ({"element_id": "mdp"}, True, False, "name_token"),
    ({"name": "login-pass"}, True, False, "name_token"),
    ({"name": "pin"}, True, False, "name_token"),
    ({"name": "otp_input"}, True, False, "name_token"),
    ({"element_id": "totp"}, True, False, "name_token"),
    ({"name": "ssn"}, True, False, "name_token"),
    ({"name": "nir"}, True, False, "name_token"),
    ({"autocomplete": "one-time-code"}, True, False, "autocomplete"),
    ({"autocomplete": "current-password"}, True, False, "autocomplete"),
    ({"autocomplete": "section-login new-password"}, True, False, "autocomplete"),
    ({"name": "api_key"}, True, False, "compact_substring"),
    ({"name": "apiKey"}, True, False, "compact_substring"),
    ({"name": "newPassword"}, True, False, "compact_substring"),
    ({"name": "passwd"}, True, False, "compact_substring"),
    ({"placeholder": "Mot de passe"}, True, False, "compact_substring"),
    ({"aria_label": "Votre mot-de-passe"}, True, False, "compact_substring"),
    ({"name": "client_secret"}, True, False, "compact_substring"),
    ({"name": "access_token"}, True, False, "compact_substring"),
    ({"placeholder": "Jeton d'accès"}, True, False, "compact_substring"),
    ({"aria_label": "Verification code"}, True, False, "compact_substring"),
    ({"name": "codeVerification"}, True, False, "compact_substring"),
    ({"aria_label": "Numéro de sécurité sociale"}, True, False, "compact_substring"),
    ({"name": "one_time_code"}, True, False, "compact_substring"),
    # Sensitive and card or bank identifiers.
    ({"autocomplete": "cc-number"}, True, True, "autocomplete"),
    ({"autocomplete": "cc-csc"}, True, True, "autocomplete"),
    ({"autocomplete": "billing cc-exp-month"}, True, True, "autocomplete"),
    ({"aria_label": "IBAN"}, True, True, "name_token"),
    ({"name": "bic"}, True, True, "name_token"),
    ({"name": "card_cvv"}, True, True, "name_token"),
    ({"element_id": "cvc"}, True, True, "name_token"),
    ({"placeholder": "Numéro de carte"}, True, True, "compact_substring"),
    ({"name": "cardNumber"}, True, True, "compact_substring"),
    ({"name": "credit-card"}, True, True, "compact_substring"),
    ({"aria_label": "Cryptogramme visuel"}, True, True, "compact_substring"),
    ({"name": "security_code"}, True, True, "compact_substring"),
    ({"input_type": "password", "name": "cvv"}, True, True, "type_password"),
    # Not sensitive.
    ({"name": "username"}, False, False, None),
    ({"name": "email", "input_type": "email", "autocomplete": "email"}, False, False, None),
    ({"name": "search", "input_type": "search"}, False, False, None),
    ({"name": "passport"}, False, False, None),
    ({"name": "compass"}, False, False, None),
    ({"name": "bypass"}, False, False, None),
    ({"name": "first_name"}, False, False, None),
    ({"name": "city"}, False, False, None),
    ({"name": "phone", "input_type": "tel"}, False, False, None),
    ({"name": "shipping_address"}, False, False, None),
    ({"autocomplete": "username"}, False, False, None),
    ({"name": "spinner"}, False, False, None),
    ({}, False, False, None),
]


def test_field_table_has_at_least_forty_rows() -> None:
    assert len(FIELD_TABLE) >= 40


Predicate = Callable[..., SensitiveVerdict]


def field_table_holds(predicate: Predicate) -> bool:
    for attributes, sensitive, card, reason in FIELD_TABLE:
        verdict = predicate(**attributes)
        if verdict.sensitive is not sensitive or verdict.card is not card:
            return False
        if reason is not None and verdict.reason != reason:
            return False
    return True


def password_type_only(**attributes: Any) -> SensitiveVerdict:
    # Defective: looks at the input type and nothing else.
    sensitive = (attributes.get("input_type") or "").lower() == "password"
    return SensitiveVerdict(sensitive, "type_password" if sensitive else "none", False)


@pytest.mark.parametrize(("attributes", "sensitive", "card", "reason"), FIELD_TABLE)
def test_field_table_row(attributes: dict[str, str], sensitive: bool, card: bool, reason: str | None) -> None:
    verdict = is_sensitive(**attributes)
    assert verdict.sensitive is sensitive
    assert verdict.card is card
    if reason is not None:
        assert verdict.reason == reason


def test_field_table_holds_for_the_real_predicate() -> None:
    assert field_table_holds(is_sensitive)


def test_field_table_fails_on_a_password_type_only_predicate() -> None:
    assert not field_table_holds(password_type_only)


def test_tag_and_role_never_decide() -> None:
    plain = is_sensitive(name="city")
    assert is_sensitive(name="city", tag="input", role="textbox") == plain
    assert is_sensitive(name="pwd", tag="div", role="button").sensitive


def test_all_parameters_accept_none() -> None:
    verdict = is_sensitive(
        tag=None,
        input_type=None,
        name=None,
        element_id=None,
        autocomplete=None,
        aria_label=None,
        placeholder=None,
        role=None,
    )
    assert verdict == SensitiveVerdict(False, "none", False)


# Substring matching flags these on purpose; a false positive only masks a
# field, a false negative would expose one.
ACCEPTED_FALSE_POSITIVES = ["tokenizer", "secretary", "token_count", "passwordless_hint"]


@pytest.mark.parametrize("name", ACCEPTED_FALSE_POSITIVES)
def test_known_substring_false_positives_stay_flagged(name: str) -> None:
    assert is_sensitive(name=name).sensitive


# Fields the fixed word lists do not catch. They are listed so that the gap is
# visible and any change to it is deliberate.
KNOWN_MISSES = [
    {"placeholder": "Code de vérification"},
    {"name": "userPin"},
    {"name": "pinCode"},
    {"name": "otpCode"},
]


@pytest.mark.parametrize("attributes", KNOWN_MISSES)
def test_known_misses_of_the_fixed_word_lists(attributes: dict[str, str]) -> None:
    assert not is_sensitive(**attributes).sensitive


COMMON_FIELD_NAMES = [
    "address", "address1", "address2", "street", "street_number", "city", "town", "zip", "zipcode",
    "postal_code", "postcode", "country", "region", "state", "province", "county", "first_name",
    "firstname", "last_name", "lastname", "surname", "full_name", "name", "middle_name", "nickname",
    "title", "civility", "gender", "birth_date", "birthdate", "date_of_birth", "age", "company",
    "organization", "company_name", "job_title", "department", "vat_number", "siret", "siren",
    "email", "email_confirm", "phone", "mobile", "fax", "website", "url", "comment", "comments",
    "message", "subject", "description", "notes", "quantity", "qty", "amount", "price", "currency",
    "coupon", "promo_code", "discount", "order_id", "order_number", "invoice_number", "reference",
    "customer_id", "account_name", "username", "login", "user", "language", "locale", "timezone",
    "newsletter", "subscribe", "terms", "accept_terms", "consent", "search", "query", "q", "filter",
    "sort", "page", "category", "tags", "color", "size", "weight", "height", "width", "file_name",
    "house_number", "delivery_date", "delivery_time", "start_date", "end_date", "duration", "rating", "feedback",
]


def false_positives_on_common_fields() -> list[str]:
    return [name for name in COMMON_FIELD_NAMES if is_sensitive(name=name, element_id=name).sensitive]


def test_false_positive_rate_on_one_hundred_common_field_names() -> None:
    assert len(COMMON_FIELD_NAMES) == 100
    assert len(set(COMMON_FIELD_NAMES)) == 100
    # Frozen measurement: any change in this list is a deliberate decision.
    assert false_positives_on_common_fields() == []
