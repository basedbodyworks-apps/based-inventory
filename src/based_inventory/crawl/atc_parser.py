"""Parse ATC state from rendered HTML (static analysis).

Used in tests with saved fixtures, and as a fallback in the live crawler
when Playwright's dynamic queries fail. For the full crawler (variant-aware,
dynamic rendering), see `atc.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

from based_inventory.crawl.selectors import ATC_SELECTORS, ATC_TEXT_PATTERN


@dataclass(frozen=True)
class AtcState:
    present: bool
    enabled: bool
    text: str

    @classmethod
    def missing(cls) -> AtcState:
        return cls(present=False, enabled=False, text="")


_SOLD_OUT_PATTERNS = ("sold-out", "sold_out", "unavailable", "soldout")


def _is_disabled(tag: Tag) -> bool:
    if tag.has_attr("disabled"):
        return True
    aria = (tag.get("aria-disabled") or "").lower()
    if aria == "true":
        return True
    class_attr = " ".join(tag.get("class") or []).lower()
    return any(p in class_attr for p in _SOLD_OUT_PATTERNS)


def parse_atc_state(html: str) -> AtcState:
    soup = BeautifulSoup(html, "html.parser")

    for selector in ATC_SELECTORS:
        element = soup.select_one(selector)
        if element:
            return AtcState(
                present=True,
                enabled=not _is_disabled(element),
                text=element.get_text(strip=True) or "",
            )

    # Text-based fallback
    pattern = re.compile(ATC_TEXT_PATTERN)
    candidate = soup.find(["button", "a"], string=pattern)
    if candidate:
        return AtcState(
            present=True,
            enabled=not _is_disabled(candidate),
            text=candidate.get_text(strip=True) or "",
        )

    return AtcState.missing()
