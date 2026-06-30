"""Display labels for practical-guide sections (strip tier suffixes)."""

_SUFFIXES = (" — Essentials", " — More Uses", " — More Tips", " — Extra Tips")


def section_eyebrow(title: str) -> str:
    """'Kitchen Cleaning — Essentials' -> 'Kitchen Cleaning'."""
    for suffix in _SUFFIXES:
        if suffix in title:
            return title.rsplit(suffix, 1)[0]
    return title


def divider_title(title: str) -> str:
    """Section opener shows the base topic, not the tier suffix."""
    return section_eyebrow(title)
