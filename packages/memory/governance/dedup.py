from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from packages.memory.config import MemoryGovernanceConfig

if TYPE_CHECKING:
    from packages.memory.store import MemoryRecord

logger = logging.getLogger("ai_platform.memory")


@dataclass
class DedupResult:
    """Result of a dedup check.

    Attributes:
        action: "skip" | "merge" | "insert"
        matched_id: memory_id of matched record if merge/skip
        reason: human-readable reason
    """

    action: str  # "skip" | "merge" | "insert"
    matched_id: str | None = None  # memory_id of matched record if merge/skip
    reason: str = ""  # human-readable reason


def _text_overlap_ratio(a: str, b: str) -> float:
    """Simple text overlap ratio for content-based fallback comparison."""
    if not a or not b:
        return 0.0
    set_a = set(a.lower().split())
    set_b = set(b.lower().split())
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def check_dedup(
    record: MemoryRecord,
    candidates: list[MemoryRecord],
    config: MemoryGovernanceConfig,
) -> DedupResult:
    """Check if a record is a duplicate or similar to existing candidates.

    Args:
        record: The incoming record to check.
        candidates: Existing records to compare against.
        config: Governance configuration with dedup thresholds.

    Returns:
        DedupResult with action "skip", "merge", or "insert".
    """
    # Lazy import to avoid circular dependency (store -> governance.dedup -> store)
    from packages.memory.store import _cosine_similarity

    if not config.dedup_enabled:
        return DedupResult(action="insert", reason="dedup disabled")

    if not candidates:
        return DedupResult(action="insert", reason="no candidates")

    best_similarity = 0.0
    best_candidate: MemoryRecord | None = None

    for candidate in candidates:
        if record.embedding is not None and candidate.embedding is not None:
            similarity = _cosine_similarity(record.embedding, candidate.embedding)
        else:
            similarity = _text_overlap_ratio(record.content, candidate.content)

        if similarity > best_similarity:
            best_similarity = similarity
            best_candidate = candidate

    if best_candidate is None:
        return DedupResult(action="insert", reason="no comparable candidates")

    matched_id = best_candidate.memory_id

    # Skip threshold (near-duplicate)
    if best_similarity >= config.dedup_skip_threshold:
        return DedupResult(
            action="skip",
            matched_id=matched_id,
            reason=f"cosine_sim={best_similarity:.4f} >= skip_threshold={config.dedup_skip_threshold}",
        )

    # Merge threshold (similar but not identical)
    if best_similarity >= config.dedup_merge_threshold:
        return DedupResult(
            action="merge",
            matched_id=matched_id,
            reason=f"cosine_sim={best_similarity:.4f} >= merge_threshold={config.dedup_merge_threshold}",
        )

    # Below both thresholds -> insert as new
    return DedupResult(
        action="insert",
        reason=f"cosine_sim={best_similarity:.4f} < merge_threshold={config.dedup_merge_threshold}",
    )


def _perform_merge(
    matched: MemoryRecord,
    incoming: MemoryRecord,
    *,
    use_llm: bool = False,
    llm_client: Any = None,
) -> MemoryRecord:
    """Merge incoming record content into matched record.

    When use_llm is True, an LLM call is used to intelligently merge content.
    Otherwise, simple content appending is used.
    """
    import time

    if use_llm and llm_client is not None:
        # LLM-based merge: ask LLM to merge the content
        merged_content = _llm_merge_content(matched.content, incoming.content, llm_client)
    else:
        # Simple concatenation merge
        merged_content = matched.content
        if incoming.content not in matched.content:
            merged_content = matched.content + "\n" + incoming.content

    # Track merge history
    if not hasattr(matched, "merged_from") or matched.merged_from is None:
        matched.merged_from = []
    if incoming.memory_id not in matched.merged_from:
        matched.merged_from.append(incoming.memory_id)

    matched.content = merged_content
    matched.last_accessed_at = time.time()
    matched.access_count = (matched.access_count or 0) + 1

    # Merge metadata
    if incoming.metadata:
        matched.metadata.update(incoming.metadata)

    return matched


def _llm_merge_content(
    existing_content: str,
    incoming_content: str,
    llm_client: Any,
) -> str:
    """Use LLM to merge two content pieces intelligently.

    Args:
        existing_content: Content of the existing record.
        incoming_content: Content of the incoming record.
        llm_client: LLM client with a chat/completion interface.

    Returns:
        Merged content string.
    """
    try:
        prompt = (
            "You are a memory consolidation assistant. Merge the following two pieces of related information "
            "into a single coherent entry. Remove redundancy while preserving all unique details.\n\n"
            f"EXISTING:\n{existing_content}\n\n"
            f"NEW:\n{incoming_content}\n\n"
            "MERGED:"
        )
        response = llm_client.chat(prompt)
        merged = response.strip()
        if merged:
            return merged
    except Exception as e:
        logger.warning("LLM merge failed, falling back to simple append: %s", e)

    # Fallback: simple append
    return existing_content + "\n" + incoming_content
