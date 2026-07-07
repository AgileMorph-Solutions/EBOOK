"""LEGACY safety canon — pre-profile books only.

New books carry an LLM-generated, topic-specific safety brief on their
EditorialProfile (safety_rules / safety_cards / danger_patterns), planned by
the strongest model per book — no static per-domain canon can anticipate the
next topic. This module remains ONLY so old bibles/manifests (whose safety_key
is "hp"/"pest"/"general") keep rendering and QA-scanning exactly as they did.
Do not add new domains here; extend the profile planning prompt instead.
"""
from __future__ import annotations

# --- Hydrogen peroxide -------------------------------------------------------- #
HP_RULES = """SAFETY RULES — HYDROGEN PEROXIDE (obey exactly):
- Only ever use ordinary 3% household hydrogen peroxide. Never tell the reader to
  use stronger concentrations (like 35% "food grade") for home cleaning.
- NEVER instruct mixing hydrogen peroxide with vinegar in the same closed or
  sealed container — it forms peracetic acid, which is irritating and corrosive.
  If both are used on a surface, say to use one, rinse, then the other.
- NEVER instruct mixing hydrogen peroxide with bleach or with ammonia — this
  releases toxic, harmful fumes.
- Never tell the reader to swallow it, gargle it, use it as a mouthwash, or put
  it in their eyes. Do not give wound-care or medical/first-aid uses.
- Store it in its original dark brown bottle, away from light and heat, out of
  reach of children and pets.
- Tell the reader to spot-test on a hidden area first, because peroxide can
  lighten or bleach some fabrics, surfaces, and colors.
- Keep the room ventilated and avoid getting it in the eyes; rinse if contact."""

# Paired "never mix" cards for the safety page (label, why).
HP_NEVER_MIX = [
    ("Peroxide + Vinegar",
     "In a closed bottle this makes an irritating acid (peracetic acid). Use one, "
     "rinse well, then the other — never seal them together."),
    ("Peroxide + Bleach",
     "Mixing the two releases harmful fumes. Keep them far apart and never combine."),
    ("Bleach + Ammonia",
     "This makes a toxic gas. Never mix cleaning products together to be safe."),
    ("Peroxide + Eyes or Mouth",
     "Never swallow it, gargle with it, or get it in your eyes. It is for cleaning, "
     "not for the body."),
]

# --- Pest control ------------------------------------------------------------- #
PEST_RULES = """SAFETY RULES — PEST CONTROL (obey exactly):
- Borax and boric acid are useful but TOXIC if swallowed. Every time you use them,
  tell the reader to keep baits away from children and pets, wear gloves, wash
  hands after, and never place them on food-prep surfaces.
- Treat bed bugs and fleas with caution. Do not over-promise that a home remedy
  will fully clear an infestation. For real infestations, advise calling a
  professional.
- Use only FOOD-GRADE diatomaceous earth, apply a thin layer, and wear a dust
  mask while applying it so it is not inhaled.
- The hydrogen-peroxide rules above also apply: never mix peroxide with vinegar
  in a closed container, or with bleach or ammonia.
- Note that some essential oils and "natural" remedies can be harmful to pets
  (especially cats and dogs); keep treated areas away from animals until dry."""

PEST_NEVER_MIX = [
    ("Borax + Children or Pets",
     "Borax baits are poisonous if eaten. Place them out of reach, wear gloves, "
     "and never on food surfaces."),
    ("Peroxide + Bleach",
     "Mixing these releases harmful fumes. Use one product at a time."),
    ("Strong Oils + Pets",
     "Some essential oils harm cats and dogs. Keep pets away from treated areas "
     "until they are dry."),
    ("Bed Bugs + Wishful Thinking",
     "Home steps help, but a true infestation needs a professional. Do not delay "
     "calling for help."),
]

# Deterministic danger checks the QA gate scans for on LEGACY books
# (lowercased substrings that, co-occurring within one entry/block, indicate an
# unsafe instruction). New books carry LLM-generated profile.danger_patterns.
DANGER_PATTERNS = [
    ("hydrogen peroxide", "vinegar", "closed"),
    ("hydrogen peroxide", "vinegar", "sealed"),
    ("peroxide", "bleach", "mix"),
    ("peroxide", "ammonia", "mix"),
    ("peroxide", "drink"),
    ("peroxide", "swallow"),
    ("peroxide", "gargle"),
    ("peroxide", "mouthwash"),
    ("35%",),  # food-grade / high concentration for home use
]


def rules_for(book_kind_key: str) -> str:
    """LEGACY: safety rule block for pre-profile books (safety_key hp/pest/
    general). New books inject profile.safety_rules instead."""
    if book_kind_key == "pest":
        return HP_RULES + "\n\n" + PEST_RULES
    if book_kind_key == "hp":
        return HP_RULES
    return GENERAL_RULES + "\n\n" + HP_RULES


GENERAL_RULES = """SAFETY RULES — HOME GUIDES (obey exactly):
- Never instruct mixing cleaning products together unless the source explicitly
  says it is safe. When in doubt, use one product, rinse, then another.
- Keep all chemicals and baits away from children and pets.
- Tell the reader to spot-test on a hidden area first.
- Keep the room ventilated when using sprays or powders.
- Do not give medical advice; for bites, burns, or reactions, say to call a doctor."""

GENERAL_NEVER_MIX = [
    ("Bleach + Ammonia",
     "This makes a toxic gas. Never mix cleaning products blindly."),
    ("Bleach + Vinegar",
     "Mixing these releases harmful fumes. Use one product at a time."),
    ("Different Drain Cleaners",
     "Never pour two different drain products down the same drain."),
    ("Chemicals + Children or Pets",
     "Store cleaners locked up and keep treated areas off-limits until dry."),
]


def never_mix_for(safety_key: str) -> list[tuple[str, str]]:
    """LEGACY: safety-page card pairs for pre-profile books. New books render
    profile.safety_cards instead."""
    if safety_key == "pest":
        return PEST_NEVER_MIX
    if safety_key == "hp":
        return HP_NEVER_MIX
    return GENERAL_NEVER_MIX


# --------------------------------------------------------------------------- #
# Bible-aware accessors — the ONLY entry points the pipeline should use.
# Profile-carrying books get their LLM-generated, topic-specific safety brief;
# legacy books (profile=None) fall back to the static canon above.
# --------------------------------------------------------------------------- #
def rules_for_bible(bible) -> str:
    if bible.profile is not None:
        return bible.profile.safety_rules or ""
    return rules_for(bible.safety_key)


def cards_for_bible(bible) -> list[tuple[str, str]]:
    if bible.profile is not None:
        return [(c.pair, c.why) for c in bible.profile.safety_cards]
    return never_mix_for(bible.safety_key)


def danger_patterns_for_bible(bible) -> list[tuple]:
    if bible.profile is not None:
        return [tuple(p) for p in bible.profile.danger_patterns if p]
    return [tuple(p) for p in DANGER_PATTERNS]
