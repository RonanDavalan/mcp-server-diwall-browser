"""A simulated page shown at a page zoom, answered the way the browser was measured to answer it.

Why this module exists:
    At a page zoom the view in device pixels stays what it is while the CSS pixel grows, and the cut
    of a screenshot is read in CSS pixels times the zoom. The simulated page of page_model knows only
    a screen scale; this one adds the zoom, so that the capture is tested without a browser.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from fixtures.page_model import Page


@dataclass
class ZoomedPage(Page):
    """``device_scale`` is the zoom times the screen scale: device pixels per CSS pixel."""

    zoom: float = 1.0

    def layout_metrics(self) -> dict[str, Any]:
        metrics = super().layout_metrics()
        for name in ("cssVisualViewport", "visualViewport"):
            metrics[name]["zoom"] = self.zoom
        return metrics

    def screenshot(self, clip: dict[str, Any] | None = None) -> dict[str, Any]:
        if clip is not None:
            # The cut is in CSS pixels times the zoom; the page is drawn in CSS pixels.
            clip = {**clip, **{name: round(clip[name] / self.zoom, 6) for name in ("x", "y", "width", "height")}}
        return super().screenshot(clip)


def at_zoom(page: Page, zoom: float, screen_scale: float = 1.0) -> ZoomedPage:
    """The same page, shown by a browser whose page zoom is ``zoom`` on a screen of ``screen_scale``."""
    values = {item.name: getattr(page, item.name) for item in fields(Page) if item.init}
    values["device_scale"] = zoom * screen_scale
    return ZoomedPage(**values, zoom=zoom)
