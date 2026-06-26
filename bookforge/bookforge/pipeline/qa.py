"""Stage 5 — QA & accuracy gates (blocking).

Anti-hallucination is the core requirement: every atomic claim is verified
against its cited source passages by an independent judge. Unsupported /
contradicted claims are surfaced and must be rewritten or cut — a book cannot
pass until groundedness clears the configured threshold (default 100%).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import structlog
from pydantic import BaseModel

from bookforge.clients import llm
from bookforge.config import settings
from bookforge.schemas import Chapter, Claim, QAResult, SourceDoc

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


def score_prose(chapter: Chapter, bible_banned: list[str]) -> _Prose:
    text = "\n\n".join(b.text for b in chapter.blocks)[:8000]
    return llm.structured(
        "Score this chapter's prose as a tough literary editor (0-1). Penalize "
        f"flat exposition, repetition, and these AI-tells: {bible_banned}.\n\n{text}",
        _Prose, model=settings.vision_qa_model, max_tokens=600,
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
    # (Production runs this fan-out across Celery workers; here a thread pool.)
    all_claims: list[Claim] = []
    if claims_to_check:
        with ThreadPoolExecutor(max_workers=8) as pool:
            all_claims = list(pool.map(_safe_verify, claims_to_check))

    unsupported = [c for c in all_claims if c.verdict != "supported"]
    grounded = (len(all_claims) - len(unsupported)) / len(all_claims) if all_claims else 1.0

    prose_scores = []
    for ch in chapters:
        try:
            prose_scores.append(score_prose(ch, banned).score)
        except Exception as exc:
            log.warning("prose_score_failed", chapter=ch.index, err=str(exc)[:160])
            prose_scores.append(0.5)
    prose = sum(prose_scores) / len(prose_scores) if prose_scores else 0.0

    passed = grounded >= settings.groundedness_threshold and prose >= 0.7
    result = QAResult(
        groundedness=grounded, unsupported_claims=unsupported,
        prose_quality_score=prose, passed=passed,
    )
    log.info("qa_gate", grounded=grounded, prose=prose, unsupported=len(unsupported),
             passed=passed)
    return result
