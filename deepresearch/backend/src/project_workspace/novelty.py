"""Novelty-check helpers for idea candidates."""

from __future__ import annotations

from collections.abc import Callable
from logging import getLogger

from project_workspace.models import IdeaCandidate


NoveltyChecker = Callable[[list[IdeaCandidate], str], list[IdeaCandidate]]
logger = getLogger(__name__)


class NoveltyCheckService:
    """Annotate idea candidates with initial novelty-check fields."""

    def __init__(self, *, novelty_checker: NoveltyChecker | None = None) -> None:
        self._novelty_checker = novelty_checker

    def check(
        self,
        candidates: list[IdeaCandidate],
        *,
        topic: str,
    ) -> list[IdeaCandidate]:
        """Run configured novelty checker or safe fallback annotations."""

        if not candidates:
            return []

        if self._novelty_checker is not None:
            try:
                checked = self._novelty_checker(candidates, topic)
            except Exception:
                logger.exception("Novelty checker failed; using unclear fallback")
            else:
                normalized = self._normalize_checked(candidates, checked)
                if normalized:
                    return normalized

        return [self._fallback_candidate(candidate, topic=topic) for candidate in candidates]

    def _normalize_checked(
        self,
        original: list[IdeaCandidate],
        checked: list[IdeaCandidate],
    ) -> list[IdeaCandidate]:
        checked_by_title = {item.title: item for item in checked if item.title}
        normalized: list[IdeaCandidate] = []
        for candidate in original:
            item = checked_by_title.get(candidate.title)
            if item is None:
                normalized.append(self._fallback_candidate(candidate, topic=""))
                continue
            normalized.append(
                candidate.model_copy(
                    update={
                        "closest_related_work": list(item.closest_related_work),
                        "overlap_analysis": item.overlap_analysis,
                        "novelty_claim": item.novelty_claim,
                        "novelty_verdict": item.novelty_verdict,
                        "novelty_confidence": max(
                            0.0,
                            min(1.0, float(item.novelty_confidence)),
                        ),
                    }
                )
            )
        return normalized

    @staticmethod
    def _fallback_candidate(candidate: IdeaCandidate, *, topic: str) -> IdeaCandidate:
        query = build_novelty_query(candidate, topic=topic)
        return candidate.model_copy(
            update={
                "overlap_analysis": candidate.overlap_analysis
                or "Novelty has not been verified yet; run academic related-work search before treating this idea as novel.",
                "novelty_claim": candidate.novelty_claim
                or "Unverified candidate; novelty claim pending closest-work comparison.",
                "novelty_verdict": candidate.novelty_verdict or "unclear",
                "novelty_confidence": candidate.novelty_confidence or 0.0,
                "closest_related_work": candidate.closest_related_work
                or [f"Pending search query: {query}"],
            }
        )


def build_novelty_query(candidate: IdeaCandidate, *, topic: str) -> str:
    """Build a concise related-work query for a candidate."""

    parts = [
        candidate.title,
        candidate.problem,
        candidate.hypothesis,
        topic,
    ]
    text = " ".join(part for part in parts if part).strip()
    return " ".join(text.split())[:240]
