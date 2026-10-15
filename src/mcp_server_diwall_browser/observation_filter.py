"""Le filtre de lecture de l'observation : un repère de page (`region`) et un texte (`contains`).

Pourquoi ce module existe :
    Une page longue remplit les plafonds de l'observation avec ce que la question ne demande pas. Le filtre
    réduit l'arbre et les éléments rendus, sans toucher à la page ni à l'image.

    Il ne lit que des textes déjà publiables : la ligne de l'arbre que le module de perception a écrite et
    rédigée. La valeur d'un champ sensible n'y est pas, donc `contains` ne peut pas la sonder. Le texte
    comparé est fourni par l'appelant (`text_of`) pour qu'aucun chemin du filtre ne lise une valeur brute.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .gate import normalize

REGIONS = ("banner", "navigation", "main", "contentinfo", "complementary", "form", "search", "dialog")
CONTEXT_LINES = 2


@dataclass(frozen=True)
class Selection:
    """Les positions gardées parmi les nœuds montrés, dans l'ordre du document, et les comptes à rendre."""

    positions: tuple[int, ...]
    lines_before: int
    lines_after: int
    region_found: bool | None


def active(region: str | None, contains: str | None) -> bool:
    return region is not None or bool(normalize(contains))


def select(
    ax: dict[str, dict[str, Any]],
    ax_ids: Sequence[str | None],
    has_line: Sequence[bool],
    depths: Sequence[int],
    *,
    region: str | None,
    contains: str | None,
    text_of: Callable[[int], str],
) -> Selection:
    """Quels nœuds montrés garder.

    ``ax_ids`` donne l'identifiant d'arbre d'accessibilité de chaque nœud montré, ``has_line`` dit s'il a une
    ligne dans l'arbre en texte, ``depths`` sa profondeur ; ``text_of(position)`` rend le texte publiable de sa
    ligne. Les repères d'abord, puis le texte à l'intérieur de ce qui reste.
    """
    total = len(ax_ids)
    lines_before = sum(1 for lined in has_line if lined)
    positions = list(range(total))
    region_found: bool | None = None
    if region is not None:
        members, region_found = _region_members(ax, region)
        positions = [p for p in positions if ax_ids[p] in members]
    needle = normalize(contains)
    if needle:
        positions = _around_matches(positions, has_line, depths, needle, text_of)
    lines_after = sum(1 for p in positions if has_line[p])
    return Selection(tuple(positions), lines_before, lines_after, region_found)


def _region_members(ax: dict[str, dict[str, Any]], region: str) -> tuple[set[str], bool]:
    """Les nœuds du sous-arbre de chaque repère de ce nom, et si la page en a au moins un."""
    roots = [
        node_id
        for node_id, node in ax.items()
        if not node.get("ignored") and _role_of(node) == region
    ]
    members: set[str] = set()
    stack = list(roots)
    while stack:
        node_id = stack.pop()
        if node_id in members:
            continue
        members.add(node_id)
        stack.extend(str(child) for child in ax.get(node_id, {}).get("childIds", ()))
    return members, bool(roots)


def _role_of(node: dict[str, Any]) -> str:
    role = node.get("role")
    value = role.get("value") if isinstance(role, dict) else None
    return value if isinstance(value, str) else ""


def _around_matches(
    positions: list[int],
    has_line: Sequence[bool],
    depths: Sequence[int],
    needle: str,
    text_of: Callable[[int], str],
) -> list[int]:
    lined = [p for p in positions if has_line[p]]
    keep: set[int] = set()
    for index, position in enumerate(lined):
        if needle not in normalize(text_of(position)):
            continue
        keep.add(position)
        wanted = depths[position]
        for earlier in reversed(lined[:index]):
            if depths[earlier] < wanted:
                keep.add(earlier)
                wanted = depths[earlier]
        keep.update(lined[max(0, index - CONTEXT_LINES) : index + CONTEXT_LINES + 1])
    # Un nœud sans ligne (un composant cliquable sans nom) suit la ligne qui le précède dans le document.
    result: list[int] = []
    previous_kept = False
    for position in positions:
        if has_line[position]:
            previous_kept = position in keep
            if previous_kept:
                result.append(position)
        elif previous_kept:
            result.append(position)
    return result
