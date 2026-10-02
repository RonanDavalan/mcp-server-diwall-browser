from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from mcp_server_diwall_browser import gate
from mcp_server_diwall_browser.gate import NOT_ARMED, GateDecision, Pacer, classify, normalize
from mcp_server_diwall_browser.operator_profile import Limits
from mcp_server_diwall_browser.output import ToolError


@dataclass
class Ref:
    """The fields of a marked element the gate reads."""

    name: str = ""
    form_has_card_field: bool = False
    is_submit_like: bool = False
    form_backend_id: int | None = None
    others: tuple[str, ...] = ()

    @property
    def gate_texts(self) -> tuple[str, ...]:
        return (self.name, *self.others)


@dataclass
class Snap:
    page_has_card_field: bool = False


PLAIN_PAGE = Snap()


def label_categories(label: str) -> tuple[str, ...]:
    return classify(Ref(label), action_kind="click", intent_category=None, snapshot=PLAIN_PAGE).categories


# --- the table of cases, replayed on the real module ---------------------------------------------

P, D, S = ("payment",), ("deletion",), ("secret_change",)
NONE: tuple[str, ...] = ()

LABEL_TABLE: list[tuple[str, tuple[str, ...]]] = [
    # Payment, in French and English, with case, accents and punctuation.
    ("Payer maintenant", P),
    ("PAYER", P),
    ("Payer 49,90 €", P),
    ("Valider la commande →", P),
    ("Confirmer la commande", P),
    ("Confirmer le paiement", P),
    ("Paiement sécurisé", P),
    ("Régler ma facture", P),
    ("RÈGLEMENT", P),
    ("Commander", P),
    ("Acheter maintenant", P),
    ("Faire un don", P),
    ("Pay now", P),
    ("PAY", P),
    ("Payment", P),
    ("Checkout", P),
    ("Proceed to checkout", P),
    ("Place order", P),
    ("Confirm order", P),
    ("Buy now", P),
    ("Purchase", P),
    ("Donate", P),
    ("pay-now", P),
    ("Confirmer et payer", P),
    # Irreversible deletion.
    ("Supprimer", D),
    ("SUPPRIMER", D),
    ("Supprimer définitivement", D),
    ("Supprimer mon compte", D),
    ("Supprimer le compte", D),
    ("Supprimer ce fichier ?", D),
    ("Effacer", D),
    ("Effacer l'historique", D),
    ("Effacer définitivement", D),
    ("Vider la corbeille", D),
    ("supprimer-definitivement", D),
    ("Delete", D),
    ("DELETE", D),
    ("Delete account", D),
    ("Delete permanently", D),
    ("Delete this item", D),
    ("Delete my data", D),
    ("Erase", D),
    ("Erase all data", D),
    ("Empty trash", D),
    ("Remove permanently", D),
    # Change of a secret.
    ("Changer le mot de passe", S),
    ("CHANGER LE MOT DE PASSE", S),
    ("Changer le mot de passe →", S),
    ("Modifier le mot de passe", S),
    ("Réinitialiser le mot de passe", S),
    ("Régénérer la clé", S),
    ("Régénérer le jeton", S),
    ("Révoquer", S),
    ("Révoquer l'accès", S),
    ("Révoquer la session", S),
    ("Change password", S),
    ("Change Password", S),
    ("change-password", S),
    ("Reset password", S),
    ("Reset password now", S),
    ("Regenerate key", S),
    ("Regenerate token", S),
    ("Rotate key", S),
    ("Revoke", S),
    ("Revoke token", S),
    # Not armed.
    ("Annuler", NONE),
    ("Retour", NONE),
    ("Rechercher", NONE),
    ("Enregistrer", NONE),
    ("Suivant", NONE),
    ("Continuer", NONE),
    ("Se connecter", NONE),
    ("Ajouter au panier", NONE),
    ("Add to cart", NONE),
    ("Recommander", NONE),
    ("Imprimer le reçu", NONE),
    ("Télécharger la facture", NONE),
    ("Valider", NONE),
    ("Envoyer", NONE),
    ("Modifier", NONE),
    ("Mes commandes", NONE),
    ("Payé", NONE),
    ("Repay later", NONE),
    ("Paypal", NONE),
    ("Undelete", NONE),
    # Known false positives, accepted: a false positive adds a confirmation.
    ("Supprimer le filtre", D),
    ("Pay attention", P),
    ("Supprimer du panier", D),
]

# Known misses of the starting vocabulary: listed so the gap is visible.
KNOWN_MISSES = ["Reset your password", "Regenerate API key", "Finaliser l'achat", "Valider mon panier"]


def test_the_table_has_enough_rows_of_each_kind() -> None:
    counts = {category: sum(1 for _, expected in LABEL_TABLE if expected == (category,)) for category in gate.CATEGORIES}
    assert len(LABEL_TABLE) >= 60
    assert all(count >= 20 for count in counts.values()), counts
    assert sum(1 for _, expected in LABEL_TABLE if expected == NONE) >= 12


@pytest.mark.parametrize(("label", "expected"), LABEL_TABLE)
def test_label_table_row(label: str, expected: tuple[str, ...]) -> None:
    assert label_categories(label) == expected


@pytest.mark.parametrize("label", KNOWN_MISSES)
def test_known_misses_of_the_starting_vocabulary(label: str) -> None:
    assert label_categories(label) == NONE


def test_words_are_matched_whole() -> None:
    assert label_categories("Recommander") == NONE
    assert label_categories("Commander") == P
    assert gate.phrase_in(("valider", "la", "commande"), "valider la commande maintenant".split())
    assert not gate.phrase_in(("valider", "la", "commande"), "valider la".split())


def test_normalization() -> None:
    assert normalize("  Réglement—TOTAL : 12,50 €!  ") == "reglement total 12 50"
    assert normalize(None) == ""


# --- one hundred common button labels: what the gate arms --------------------------------------

COMMON_BUTTONS = [
    # Navigation.
    "Accueil", "Retour", "Suivant", "Précédent", "Continuer", "Fermer", "Menu", "Plus", "Voir plus", "Afficher",
    "Home", "Back", "Next", "Previous", "Close", "More", "Show all", "Open", "Expand", "Collapse",
    # Forms.
    "Envoyer", "Valider", "Enregistrer", "Annuler", "Effacer le formulaire", "Rechercher", "Filtrer", "Trier", "Réinitialiser", "Appliquer",
    "Submit", "Save", "Cancel", "Search", "Clear", "Apply", "Reset", "Upload", "Attach", "Send",
    # Account.
    "Se connecter", "Se déconnecter", "Créer un compte", "Mon compte", "Mot de passe oublié ?", "Changer le mot de passe", "Modifier mon profil", "Supprimer mon compte", "Paramètres", "Notifications",
    "Sign in", "Sign out", "Sign up", "My account", "Forgot password?", "Change password", "Edit profile", "Delete account", "Settings", "Revoke access",
    # Commerce.
    "Ajouter au panier", "Voir le panier", "Commander", "Payer maintenant", "Valider la commande", "Appliquer le code promo", "Choisir la livraison", "Suivre ma commande", "Télécharger la facture", "Retourner un article",
    "Add to cart", "View cart", "Checkout", "Pay now", "Place order", "Apply coupon", "Track order", "Download invoice", "Return item", "Buy now",
    # Documents and other.
    "Télécharger", "Imprimer", "Partager", "Copier le lien", "Supprimer le filtre", "Renouveler", "Déclarer", "Résilier", "Signer", "Accepter",
    "Download", "Print", "Share", "Copy link", "Remove filter", "Renew", "Archive", "Unsubscribe", "Accept cookies", "Decline",
]

# What a careful reader would require a confirmation for in this list.
TRULY_LOCKED = {
    "Changer le mot de passe", "Supprimer mon compte", "Change password", "Delete account", "Revoke access",
    "Commander", "Payer maintenant", "Valider la commande", "Checkout", "Pay now", "Place order", "Buy now",
}

ARMED_ON_COMMON_BUTTONS = sorted(TRULY_LOCKED | {"Effacer le formulaire", "Supprimer le filtre"})


def test_measure_on_one_hundred_common_buttons() -> None:
    assert len(COMMON_BUTTONS) == 100 and len(set(COMMON_BUTTONS)) == 100
    armed = sorted(label for label in COMMON_BUTTONS if label_categories(label))
    # Frozen measurement: 14 armed, 2 false positives (2 % of the labels), no miss.
    assert armed == ARMED_ON_COMMON_BUTTONS
    assert len(armed) == 14
    assert sorted(set(armed) - TRULY_LOCKED) == ["Effacer le formulaire", "Supprimer le filtre"]
    assert TRULY_LOCKED <= set(armed)


# --- other signals and the rule that the model's intent only adds ----------------------------------

Classifier = Callable[..., GateDecision]


def payment_click_is_armed(classifier: Classifier) -> bool:
    decision = classifier(Ref("Payer maintenant", is_submit_like=True, form_backend_id=7), action_kind="click", intent_category="read", snapshot=PLAIN_PAGE)
    return decision.armed and "payment" in decision.categories


def never_armed(*args: Any, **kwargs: Any) -> GateDecision:
    # Defective: detection switched off.
    return NOT_ARMED


def test_a_declared_read_never_disarms_a_payment() -> None:
    assert payment_click_is_armed(classify)


def test_arming_check_catches_a_detection_switched_off() -> None:
    assert not payment_click_is_armed(never_armed)


def test_a_card_field_on_the_page_arms_every_evaluated_action() -> None:
    page = Snap(page_has_card_field=True)
    decision = classify(Ref("Annuler"), action_kind="click", intent_category="read", snapshot=page)
    assert decision == GateDecision(True, ("payment",), ("card_field_on_page",))
    assert classify(Ref("Annuler"), action_kind="click", intent_category=None, snapshot=PLAIN_PAGE) == NOT_ARMED


def test_a_card_field_in_the_form_arms_its_submission() -> None:
    submit = Ref("Continuer", form_has_card_field=True, is_submit_like=True, form_backend_id=4)
    assert classify(submit, action_kind="click", intent_category=None, snapshot=PLAIN_PAGE).signals == ("card_field_in_form",)
    plain_button = Ref("Continuer", form_has_card_field=True, is_submit_like=False, form_backend_id=4)
    assert not classify(plain_button, action_kind="click", intent_category=None, snapshot=PLAIN_PAGE).armed
    field = Ref("Nom sur la carte", form_has_card_field=True, is_submit_like=False, form_backend_id=4)
    assert classify(field, action_kind="enter_key", intent_category=None, snapshot=PLAIN_PAGE).signals == ("card_field_in_form",)


@pytest.mark.parametrize("category", gate.CATEGORIES)
def test_a_declared_category_arms(category: str) -> None:
    decision = classify(Ref("Continuer"), action_kind="click", intent_category=category, snapshot=PLAIN_PAGE)
    assert decision == GateDecision(True, (category,), ("declared_category",))


def test_signals_add_up() -> None:
    ref = Ref("Supprimer", form_has_card_field=True, is_submit_like=True, form_backend_id=3)
    decision = classify(ref, action_kind="click", intent_category="secret_change", snapshot=Snap(page_has_card_field=True))
    assert decision.categories == ("payment", "deletion", "secret_change")
    assert set(decision.signals) == {"vocabulary_deletion", "card_field_on_page", "card_field_in_form", "declared_category"}


def test_other_texts_of_the_element_arm_too() -> None:
    ref = Ref("Continuer", others=("", "Valider la commande", "Étape 3 : paiement"))
    assert classify(ref, action_kind="click", intent_category=None, snapshot=PLAIN_PAGE).categories == ("payment",)


def test_a_dialog_message_arms_its_acceptance() -> None:
    decision = classify(None, action_kind="dialog_accept", intent_category=None, snapshot=PLAIN_PAGE, dialog_message="Supprimer définitivement ?")
    assert decision == GateDecision(True, ("deletion",), ("vocabulary_deletion",))
    assert not classify(None, action_kind="dialog_accept", intent_category=None, snapshot=PLAIN_PAGE, dialog_message="Continuer ?").armed


@pytest.mark.parametrize("gesture", ["fill", "select_option", "navigate", "scroll", "wait", "press_key", "dialog_dismiss"])
def test_filling_and_going_to_see_are_never_evaluated(gesture: str) -> None:
    ref = Ref("Payer maintenant", form_has_card_field=True, is_submit_like=True, form_backend_id=1)
    assert classify(ref, action_kind=gesture, intent_category="payment", snapshot=Snap(page_has_card_field=True)) == NOT_ARMED


def test_the_vocabulary_is_fixed_in_code() -> None:
    with pytest.raises(TypeError):
        gate.VOCABULARY["payment"] = ()  # type: ignore[index]
    assert set(gate.VOCABULARY) == set(gate.CATEGORIES)


# --- pace and ceilings -----------------------------------------------------------------------------


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def pacer(limits: Limits | None = None, time: FakeTime | None = None) -> tuple[Pacer, FakeTime]:
    fake = time or FakeTime()
    chosen = limits or Limits()
    return Pacer(lambda: chosen, clock=fake.clock, sleep=fake.sleep), fake


async def test_the_201st_action_is_refused_with_the_counters() -> None:
    pace, _time = pacer()
    for _ in range(200):
        await pace.before_action("click")
    with pytest.raises(ToolError) as caught:
        await pace.before_action("click")
    assert caught.value.code == "limit_reached"
    assert caught.value.extra_server["counters"] == {
        "actions": 200,
        "pages": 0,
        "max_actions": 200,
        "max_pages": 50,
        "min_action_delay_ms": 250,
    }


async def test_the_51st_page_is_refused() -> None:
    pace, _time = pacer()
    for _ in range(50):
        await pace.before_action("navigate")
    with pytest.raises(ToolError) as caught:
        await pace.before_action("open_tab")
    assert caught.value.code == "limit_reached"
    await pace.before_action("click")


async def test_two_actions_are_at_least_250_ms_apart() -> None:
    pace, time = pacer()
    await pace.before_action("click")
    time.now += 0.1
    await pace.before_action("fill")
    assert time.slept == [pytest.approx(0.15)]
    time.now += 1.0
    await pace.before_action("scroll")
    assert len(time.slept) == 1


async def test_the_profile_can_lower_the_ceilings_and_is_read_on_every_call() -> None:
    fake = FakeTime()
    limits = {"value": Limits(max_actions_per_session=2, min_action_delay_ms=0)}
    pace = Pacer(lambda: limits["value"], clock=fake.clock, sleep=fake.sleep)
    await pace.before_action("click")
    await pace.before_action("click")
    with pytest.raises(ToolError):
        await pace.before_action("click")
    limits["value"] = Limits(max_actions_per_session=5, min_action_delay_ms=0)
    await pace.before_action("click")
    assert pace.counters()["actions"] == 3


@pytest.mark.parametrize("kind", ["observe", "wait", "status", "list_tabs", "close_tab", "save_evidence"])
async def test_reads_and_bookkeeping_never_count(kind: str) -> None:
    pace, _time = pacer()
    with pytest.raises(ValueError):
        await pace.before_action(kind)
    assert pace.counters()["actions"] == 0


def test_defaults_hold_without_any_profile() -> None:
    assert Pacer().counters() == {"actions": 0, "pages": 0, "max_actions": 200, "max_pages": 50, "min_action_delay_ms": 250}
