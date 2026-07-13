"""Stage 5 — QA & accuracy gates (blocking).

Anti-hallucination is the core requirement: every atomic claim is verified
against its cited source passages by an independent judge. Unsupported /
contradicted claims are surfaced and must be rewritten or cut — a book cannot
pass until groundedness clears the configured threshold (default 100%).
"""
from __future__ import annotations

import hashlib
import threading

import structlog
from pydantic import BaseModel

import re

from bookforge import safety as safety_mod
from bookforge.clients import llm
from bookforge.config import settings
from bookforge.pipeline.pools import ctx_pool
from bookforge.schemas import (
    BlockType, BookBible, Chapter, Claim, QAResult, SafetyFinding, SourceDoc,
    profile_of,
)

log = structlog.get_logger(__name__)


class _Verdict(BaseModel):
    verdict: str = "unsupported"  # supported | unsupported | contradicted
    notes: str = ""


def _passage_lookup(source: SourceDoc) -> dict[str, str]:
    return {p.id: p.text for p in source.passages}


def verify_claim(claim: Claim, passages: dict[str, str]) -> Claim:
    cited = "\n\n".join(f"[{pid}] {passages.get(pid, '(missing)')}"
                        for pid in claim.source_passage_ids) or "(no sources cited)"
    v = llm.structured(
        f"Claim: \"{claim.text}\"\n\nCited sources:\n{cited}\n\n"
        "Is the claim fully SUPPORTED by the cited sources, UNSUPPORTED "
        "(not stated), or CONTRADICTED? Be strict — if the sources don't clearly "
        "state it, it is unsupported.",
        _Verdict, model=settings.vision_qa_model, max_tokens=500,
    )
    claim.verdict = v.verdict if v.verdict in {"supported", "unsupported", "contradicted"} else "unsupported"
    claim.verifier_notes = v.notes
    return claim


class _Prose(BaseModel):
    score: float = 0.5          # 0-1 overall craft
    ai_tell_density: float = 0.0
    notes: str = ""


class GateCache:
    """Memoizes per-chapter judge results across gate re-runs within one book
    run. The gate runs up to 5x per book (initial + fix-loop revisions +
    post-sanitize) but most chapters don't change between runs — re-scoring
    them re-spends Opus tokens for identical verdicts. Keys are content hashes,
    so any edited chapter self-invalidates."""

    def __init__(self) -> None:
        self.prose: dict[str, _Prose] = {}
        self.judged: dict[str, list[Claim]] = {}
        self.lock = threading.Lock()


def _ch_key(chapter: Chapter) -> str:
    return hashlib.sha256(chapter.model_dump_json().encode()).hexdigest()


def _cached_prose(chapter: Chapter, banned: list[str],
                  cache: GateCache | None) -> _Prose:
    if cache is not None:
        key = _ch_key(chapter)
        with cache.lock:
            hit = cache.prose.get(key)
        if hit is not None:
            return hit
    ps = score_prose(chapter, banned)
    if cache is not None:
        with cache.lock:
            cache.prose[key] = ps
    return ps


def score_prose(chapter: Chapter, bible_banned: list[str]) -> _Prose:
    # Include the authored CARD content (titles/intros/steps), not just prose
    # blocks — the cards are where vague filler hides.
    parts: list[str] = []
    for b in chapter.blocks:
        if b.text:
            parts.append(b.text)
        for e in b.entries or []:
            parts.append(e.title)
            if e.intro:
                parts.append(e.intro)
            parts.extend(s.text for s in e.steps)
    text = "\n\n".join(p for p in parts if p)[:9000]
    return llm.structured(
        "Score this chapter as a tough acquisitions editor at a top imprint "
        "(0-1). Reward specific, non-obvious, publishable material. Penalize "
        "HARD: vagueness and generic filler (advice that could appear in any "
        "book), restating the premise, flat exposition, repetition, and these "
        f"AI-tells: {bible_banned}. In notes, name the vaguest passages.\n\n{text}",
        _Prose, model=settings.vision_qa_model, max_tokens=700,
    )


def run_gate(chapters: list[Chapter], source: SourceDoc, banned: list[str]) -> QAResult:
    passages = _passage_lookup(source)
    claims_to_check = [c for ch in chapters for b in ch.blocks for c in b.claims]

    def _safe_verify(claim: Claim) -> Claim:
        try:
            return verify_claim(claim, passages)
        except Exception as exc:  # never let one bad call sink the book
            log.warning("verify_failed", claim=claim.id, err=str(exc)[:160])
            claim.verdict, claim.verifier_notes = "unsupported", "verify error"
            return claim

    # Claims are independent — verify them concurrently instead of one-at-a-time.
    # (ctx_pool propagates the per-book budget + job context into workers.)
    all_claims: list[Claim] = []
    if claims_to_check:
        with ctx_pool(8, "verify") as pool:
            all_claims = list(pool.map(_safe_verify, claims_to_check))

    unsupported = [c for c in all_claims if c.verdict != "supported"]
    grounded = (len(all_claims) - len(unsupported)) / len(all_claims) if all_claims else 1.0

    def _safe_prose(ch: Chapter) -> float:
        try:
            return score_prose(ch, banned).score
        except Exception as exc:
            if llm.never_swallow(exc):
                raise
            log.warning("prose_score_failed", chapter=ch.index, err=str(exc)[:160])
            return 0.5

    with ctx_pool(settings.prose_score_concurrency, "prose") as pool:
        prose_scores = list(pool.map(_safe_prose, chapters))
    prose = sum(prose_scores) / len(prose_scores) if prose_scores else 0.0

    passed = grounded >= settings.groundedness_threshold and prose >= 0.7
    result = QAResult(
        groundedness=grounded, unsupported_claims=unsupported,
        prose_quality_score=prose, passed=passed,
    )
    log.info("qa_gate", grounded=grounded, prose=prose, unsupported=len(unsupported),
             passed=passed)
    return result


# --------------------------------------------------------------------------- #
# Practical-guide gate — deterministic schema audit (free) + sampled judges.
# Runs BEFORE the image stage so a defective book never spends image budget.
# --------------------------------------------------------------------------- #
_QTY_RE = re.compile(r"\d|[½¼¾⅓⅔⅛]")
_GLYPH_O_RE = re.compile(r"\d[OØ]|[OØ]\d")  # "$1O", "2ØØ" — letter-O-for-zero


def _entry_texts(entry) -> str:
    parts = [entry.title, entry.intro, entry.caution or "", entry.savings or "",
             entry.variation, entry.note]
    parts.extend(s.text for s in entry.steps)
    return " ".join(p for p in parts if p)


def _instruction_units(entry) -> list[str]:
    """The imperative fields, each kept SEPARATE so a danger pattern must
    co-occur within one instruction (not across the whole card). Cautions and
    headnotes are excluded — their job is to NAME the danger."""
    units = [s.text for s in entry.steps]
    if entry.variation:
        units.append(entry.variation)
    return units


# Conventional quantity-less list forms every publisher allows: seasonings
# "to taste", "oil for frying", "flour, as needed", "pinch of salt", and
# non-food EQUIPMENT/MATERIALS that carry no amount (foil, bags, thermometer…).
_NO_QTY_OK = re.compile(
    r"to taste|as needed|optional|to finish|\bfor\b|"
    r"^\s*(?:a\s+)?(?:pinch|dash|splash|handful)\b|"
    r"\b(?:foil|wrap|parchment|bag|bags|container|containers|jar|jars|"
    r"thermometer|skewer|skewers|toothpick|twine|string|spray|paper|"
    r"rack|pan|sheet|tray|thawed|for greasing)\b",
    re.IGNORECASE)

# The check exists to catch WHOLESALE non-compliance (the shipped book had 0 of
# 1,176 cards quantified); a stray equipment line among real quantities is fine.
_MISSING_QTY_FAIL_FRACTION = 0.4

# Words that, present in the same step, mean the writer is WARNING against the
# danger, not instructing it ("thaw in the fridge, never on the counter").
_SAFETY_NEGATION = (
    "never", "not ", "n't", "avoid", "instead of", "rather than", "do not",
    "don't", "unsafe", "danger", "warning", "must not", "should not",
)


# Abstractions that must never appear as a "What You Need" material — a card
# listing these is filler, not a real supply list (fat-loss "Patience", "A trend
# view", "Good lighting"). Matched as a whole-item test on each need entry.
_ABSTRACT_NEED = frozenset({
    "patience", "mindset", "consistency", "willingness", "motivation",
    "awareness", "discipline", "commitment", "dedication", "focus", "attitude",
    "a plan", "a goal", "goals", "good lighting", "time", "a trend view",
    "an open mind", "positivity", "a positive attitude", "self-control",
    "determination", "effort", "willpower", "realistic expectation",
    "realistic expectations", "an honest look", "honesty", "confidence",
})


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()


def _is_abstract_need(item: str) -> bool:
    n = _norm(item)
    if n in _ABSTRACT_NEED:
        return True
    # strip a leading article and retest ("a plan", "the mindset")
    stripped = re.sub(r"^(a|an|the)\s+", "", n)
    return stripped in _ABSTRACT_NEED


def _title_tokens(title: str) -> frozenset[str]:
    stop = {"the", "a", "an", "your", "of", "for", "to", "and", "it", "with"}
    return frozenset(_norm(title).split()) - stop


def _near_duplicate(a: frozenset[str], b: frozenset[str]) -> bool:
    """Jaccard overlap of content tokens ≥ 0.6 → the two titles say the same thing."""
    if not a or not b:
        return False
    inter = len(a & b)
    union = len(a | b)
    return union > 0 and inter / union >= 0.6


def audit_guide_chapters(chapters: list[Chapter], bible: BookBible) -> tuple[list[str], list[SafetyFinding]]:
    """Deterministic, zero-cost audit of every entry against the book's
    entry schema and its generated safety patterns."""
    profile = profile_of(bible)
    es = profile.entry_schema
    issues: list[str] = []
    findings: list[SafetyFinding] = []
    seen_titles: dict[str, str] = {}
    seen_openings: dict[str, str] = {}
    seen_tokens: list[tuple[frozenset[str], str]] = []  # for fuzzy near-dup
    danger = [tuple(str(x).lower() for x in p)
              for p in safety_mod.danger_patterns_for_bible(bible)]

    for ch in chapters:
        for bi, block in enumerate(ch.blocks):
            is_card = block.type == BlockType.use_card
            for e in block.entries:
                loc = f"ch{ch.index}.{bi} '{e.title[:40]}'"
                text_all = _entry_texts(e)

                if is_card:
                    if es.require_quantities and e.what_you_need:
                        missing = [x for x in e.what_you_need
                                   if not _QTY_RE.search(x)
                                   and not _NO_QTY_OK.search(x)]
                        # Fail only on wholesale non-compliance — the writer
                        # ignored quantities entirely (the shipped-book bug),
                        # not on a lone equipment/seasoning line.
                        if (len(missing) >= 2
                                and len(missing) >= _MISSING_QTY_FAIL_FRACTION
                                * len(e.what_you_need)):
                            issues.append(
                                f"{loc}: {len(missing)}/{len(e.what_you_need)} "
                                f"{es.need_label} items without a quantity: "
                                f"{missing[:3]}")
                    # Anti-vagueness: no abstract "materials"; and none at all
                    # when this book's entries need nothing physical.
                    abstract = [x for x in e.what_you_need if _is_abstract_need(x)]
                    if abstract:
                        issues.append(
                            f"{loc}: {es.need_label} lists abstractions, not real "
                            f"materials — remove or replace: {abstract[:3]}")
                    if not es.requires_materials and e.what_you_need:
                        issues.append(
                            f"{loc}: this book's entries need nothing physical — "
                            f"{es.need_label} must be empty (got {len(e.what_you_need)})")
                    if e.steps and not (
                            es.min_steps <= len(e.steps) <= es.max_steps):
                        issues.append(
                            f"{loc}: {len(e.steps)} steps (schema wants "
                            f"{es.min_steps}-{es.max_steps})")
                    if not e.steps:
                        issues.append(f"{loc}: full card has no steps")
                    if es.show_meta and not (e.serves and e.time):
                        issues.append(f"{loc}: missing serves/time meta")
                    nt = _norm(e.title)
                    if nt:
                        if nt in seen_titles:
                            issues.append(
                                f"{loc}: duplicate entry title (also at "
                                f"{seen_titles[nt]})")
                        seen_titles[nt] = loc
                        toks = _title_tokens(e.title)
                        for prev_toks, prev_loc in seen_tokens:
                            if _near_duplicate(toks, prev_toks):
                                issues.append(
                                    f"{loc}: near-duplicate of {prev_loc} — same "
                                    "idea reworded; make it genuinely distinct")
                                break
                        seen_tokens.append((toks, loc))
                    opening = " ".join(_norm(e.intro).split()[:4])
                    if opening and len(opening.split()) >= 3:
                        if opening in seen_openings:
                            issues.append(
                                f"{loc}: intro opens like {seen_openings[opening]}"
                                f" ('{opening}…')")
                        seen_openings[opening] = loc

                for field_name in ("title", "intro", "savings", "caution",
                                   "variation", "note", "source_line"):
                    val = getattr(e, field_name, None) or ""
                    if val.endswith("…"):
                        issues.append(
                            f"{loc}: {field_name} was truncated ('…') — "
                            "corrupted content must not ship")
                    if "[]" in val or re.search(r"(?i)adapted from\s*$", val.strip()):
                        issues.append(
                            f"{loc}: {field_name} has a broken/empty citation — "
                            "fill the source credit or leave it empty")
                for item in e.what_you_need:
                    if item.endswith("…"):
                        issues.append(f"{loc}: truncated {es.need_label} item")

                if _GLYPH_O_RE.search(text_all):
                    issues.append(f"{loc}: letter O where a zero belongs")

                # Scan each step separately so a match must co-occur in ONE
                # instruction; skip steps whose wording is a safety WARNING
                # (a step that says "never thaw on the counter" is safe advice,
                # not an unsafe instruction).
                for step_text in _instruction_units(e):
                    low = step_text.lower()
                    if any(neg in low for neg in _SAFETY_NEGATION):
                        continue
                    for pat in danger:
                        if pat and all(tok in low for tok in pat):
                            findings.append(SafetyFinding(
                                severity="block",
                                rule=" + ".join(pat),
                                location=loc,
                                excerpt=step_text[:120],
                                fix="rewrite this step safely",
                            ))
    for ch in chapters:
        for bi, block in enumerate(ch.blocks):
            if block.type == BlockType.chart and block.table:
                planned = None
                for sp in bible.specials_for(ch.index):
                    if sp.kind == "chart" and (
                        sp.heading == block.heading or not block.heading
                    ):
                        planned = sp.rows
                        break
                if planned and len(block.table.rows) < planned:
                    issues.append(
                        f"ch{ch.index}.{bi}: chart '{block.heading}' has "
                        f"{len(block.table.rows)}/{planned} rows — regenerate "
                        f"with all {planned} rows")
            if block.type == BlockType.checklist and block.checklist:
                planned_items = None
                for sp in bible.specials_for(ch.index):
                    if sp.kind == "checklist":
                        planned_items = sp.items
                        break
                if planned_items and len(block.checklist.items) < planned_items:
                    issues.append(
                        f"ch{ch.index}.{bi}: checklist has "
                        f"{len(block.checklist.items)}/{planned_items} items")
    return issues, findings


# --------------------------------------------------------------------------- #
# Fix-loop support — map faults back to sections, and a last-resort sanitizer.
# The pipeline no longer FAILS a book on deterministic defects; instead it feeds
# these faults back into the writer (feedback-driven regen) and, for any unsafe
# step that survives every revision, neutralizes it so the book still ships.
# --------------------------------------------------------------------------- #
_CH_RE = re.compile(r"ch(\d+)")
_LOC_RE = re.compile(r"ch(\d+)\.\d+\s+'(.*?)'")


def deterministic_defects(result: QAResult) -> bool:
    """The gate found a defect we can act on: an unsafe step, or a schema/layout
    issue. (A pure groundedness/prose shortfall is NOT deterministic — it only
    lowers the score, so the book ships without a fix pass.)"""
    return (not result.safety_passed) or bool(result.layout_issues)


def faults_by_chapter(result: QAResult) -> dict[int, list[str]]:
    """Group actionable faults by chapter index so only offending sections get
    regenerated. Loci already carry `ch{index}`: layout issues read
    "content: ch3.2 '…': …" and each SafetyFinding.location reads "ch3.2 '…'"."""
    faults: dict[int, list[str]] = {}
    for issue in result.layout_issues:
        # Stored as "content: <audit issue>"; the audit issue starts "chN.b '…'".
        text = issue.split("content: ", 1)[-1]
        m = _CH_RE.search(text)
        if m:
            faults.setdefault(int(m.group(1)), []).append(text)
    for f in result.safety_findings:
        m = _CH_RE.search(f.location)
        if m:
            detail = f"UNSAFE step ({f.rule}): \"{f.excerpt}\" — {f.fix}"
            faults.setdefault(int(m.group(1)), []).append(detail)
    return faults


def sanitize_unsafe_steps(
    chapters: list[Chapter], findings: list[SafetyFinding],
) -> int:
    """Last resort: neutralize every step still flagged unsafe after the fix loop
    so a dangerous instruction never ships. The offending step is removed and a
    caution is set on its entry; the book ships without it. Returns the count."""
    sanitized = 0
    for f in findings:
        if f.severity != "block":
            continue
        m = _LOC_RE.search(f.location)
        if not m:
            continue
        ch_idx, title_prefix = int(m.group(1)), m.group(2)
        excerpt = (f.excerpt or "").strip()
        for ch in chapters:
            if ch.index != ch_idx:
                continue
            for block in ch.blocks:
                for e in block.entries:
                    if title_prefix and not e.title.startswith(title_prefix[:40]):
                        continue
                    if not excerpt:
                        continue
                    before = len(e.steps)
                    e.steps = [s for s in e.steps
                               if not s.text.strip().startswith(excerpt[:120])]
                    removed = len(e.steps) != before
                    if e.variation and e.variation.strip().startswith(excerpt[:120]):
                        e.variation = ""
                        removed = True
                    if removed:
                        note = "For safety, this step was removed — follow a safe method."
                        e.caution = (e.caution + " " if e.caution else "") + note
                        sanitized += 1
    return sanitized


def _sampled_guide_judges(
    chapters: list[Chapter], source: SourceDoc, bible: BookBible,
    cache: GateCache | None = None,
) -> tuple[float, list[Claim], float]:
    """Budget-aware LLM checks on a sample: scoped groundedness over prose
    blocks (essays + chapter intros — never authored steps/quantities) and
    prose scoring. Sample chapters run concurrently; unchanged chapters hit
    the gate cache on re-runs."""
    from bookforge.pipeline import content, grounding

    sample = chapters[:: max(1, len(chapters) // 4)][:4]
    passages_by_id = _passage_lookup(source)

    def _safe_verify(claim: Claim) -> Claim:
        try:
            return verify_claim(claim, passages_by_id)
        except Exception as exc:
            if llm.never_swallow(exc):
                raise
            log.warning("verify_failed", claim=claim.id, err=str(exc)[:160])
            claim.verdict, claim.verifier_notes = "unsupported", "verify error"
            return claim

    def _judge_chapter(ch: Chapter) -> list[Claim]:
        key = _ch_key(ch)
        if cache is not None:
            with cache.lock:
                hit = cache.judged.get(key)
            if hit is not None:
                return hit
        prose_blocks = [b for b in ch.blocks
                        if b.type in (BlockType.history, BlockType.section_intro)
                        and b.text]
        if not prose_blocks:
            verified: list[Claim] = []
        else:
            pseudo = Chapter(index=ch.index, title=ch.title, blocks=prose_blocks)
            passages = grounding.retrieve(ch.title, source.passages, k=8)
            try:
                content.attribute_claims(pseudo, passages)
            except Exception as exc:
                if llm.never_swallow(exc):
                    raise
                log.warning("guide_claims_skipped", chapter=ch.index,
                            err=str(exc)[:160])
                return []
            claims = [c for b in pseudo.blocks for c in b.claims][:40]
            with ctx_pool(8, "verify") as vpool:
                verified = list(vpool.map(_safe_verify, claims))
        if cache is not None:
            with cache.lock:
                cache.judged[key] = verified
        return verified

    with ctx_pool(min(4, max(1, len(sample))), "judge") as pool:
        judged_lists = list(pool.map(_judge_chapter, sample))
    all_claims = [c for lst in judged_lists for c in lst][:120]

    if all_claims:
        # Guides are AUTHORED content by policy (profile.authored_content):
        # essays/headnotes legitimately weave era color beyond the source, so
        # "unsupported" is reported for review but only a CONTRADICTED claim
        # (source says otherwise → invented/wrong history) gates the book.
        unsupported = [c for c in all_claims if c.verdict != "supported"]
        contradicted = [c for c in all_claims if c.verdict == "contradicted"]
        grounded = (len(all_claims) - len(contradicted)) / len(all_claims)
    else:
        unsupported = []
        grounded = 1.0

    def _safe_sample_prose(ch: Chapter) -> float | None:
        try:
            return _cached_prose(ch, bible.voice.banned_patterns, cache).score
        except Exception as exc:
            if llm.never_swallow(exc):
                raise
            log.warning("guide_prose_score_failed", chapter=ch.index,
                        err=str(exc)[:160])
            return None

    with ctx_pool(min(4, max(1, len(sample))), "prose") as pool:
        maybe_scores = list(pool.map(_safe_sample_prose, sample))
    scores = [s for s in maybe_scores if s is not None]
    prose = sum(scores) / len(scores) if scores else 0.8
    return grounded, unsupported, prose


def run_guide_gate(
    chapters: list[Chapter], source: SourceDoc, bible: BookBible,
    cache: GateCache | None = None,
) -> QAResult:
    """Content gate for practical guides (replaces the old hardcoded pass).

    1. Deterministic schema audit — free, blocking when >5% of cards violate
       or any generated danger pattern trips.
    2. Sampled LLM judges — scoped groundedness on prose + prose scoring.

    Pass one `cache` across a run's gate invocations so re-runs after the
    fix-loop only re-judge the chapters that actually changed."""
    from bookforge.pipeline import content_guardrails

    issues, findings = audit_guide_chapters(chapters, bible)
    # Count coherence: any prose total (digit or spelled-out) that disagrees with
    # the book's promised count is a fault the fix-loop rewrites.
    issues.extend(content_guardrails.find_count_contradictions(chapters, bible))
    total_cards = sum(
        1 for ch in chapters for b in ch.blocks
        if b.type == BlockType.use_card and b.entries) or 1
    hard_fail = (len(issues) > 0.05 * total_cards
                 or any(f.severity == "block" for f in findings))

    try:
        grounded, unsupported, prose = _sampled_guide_judges(
            chapters, source, bible, cache)
    except Exception as exc:
        if llm.never_swallow(exc):
            raise
        log.warning("guide_judges_skipped", err=str(exc)[:200])
        grounded, unsupported, prose = 1.0, [], 0.8

    # STRICT quality: score every chapter's prose and turn a below-bar chapter
    # into an ACTIONABLE fault the fix-loop must regenerate (with the editor's
    # notes as feedback), instead of a soft warning that ships as-is.
    if settings.strict_quality:
        floor = settings.prose_floor

        def _strict_one(ch: Chapter) -> _Prose | None:
            try:
                return _cached_prose(ch, bible.voice.banned_patterns, cache)
            except Exception as exc:
                if llm.never_swallow(exc):
                    raise
                log.warning("strict_prose_score_failed", chapter=ch.index,
                            err=str(exc)[:160])
                return None

        with ctx_pool(settings.prose_score_concurrency, "prose") as pool:
            strict_scores = list(pool.map(_strict_one, chapters))
        scores: list[float] = []
        for ch, ps in zip(chapters, strict_scores):
            if ps is None:
                continue
            scores.append(ps.score)
            if ps.score < floor:
                note = (ps.notes or "generic/vague passages").strip()[:160]
                issues.append(
                    f"ch{ch.index}.0 '{ch.title[:36]}': prose "
                    f"{ps.score:.2f} below the {floor:.2f} bar — sharpen the "
                    f"vaguest lines. Editor: {note}")
        if scores:
            prose = sum(scores) / len(scores)

    passed = (not hard_fail
              and grounded >= settings.groundedness_threshold
              and prose >= settings.prose_floor)
    result = QAResult(
        groundedness=grounded,
        unsupported_claims=unsupported,
        prose_quality_score=prose,
        layout_issues=[f"content: {i}" for i in issues[:60]],
        safety_findings=findings,
        safety_passed=not any(f.severity == "block" for f in findings),
        passed=passed,
    )
    log.info("guide_qa_gate", cards=total_cards, audit_issues=len(issues),
             safety_findings=len(findings), grounded=grounded, prose=prose,
             passed=passed)
    return result
