#!/usr/bin/env python3
"""Phase L #56 — RAG/Agent 用例评分：keyword | llm_judge。"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any


class CaseGrader:
    """对单条 eval 用例打分；无 Key 或 Judge 失败时降级 keyword。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        judge_url: str | None = None,
        judge_model: str | None = None,
        tenant_id: str = "admin",
    ) -> None:
        if api_key is not None:
            self.api_key = api_key
        else:
            self.api_key = os.environ.get("EVAL_API_KEY") or os.environ.get("LLM_API_KEY") or ""
        base = (
            judge_url
            or os.environ.get("EVAL_JUDGE_URL")
            or os.environ.get("LLM_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        self.judge_url = f"{base}/chat/completions"
        self.judge_model = (
            judge_model
            or os.environ.get("EVAL_JUDGE_MODEL")
            or os.environ.get("DEFAULT_MODEL")
            or "gpt-4o-mini"
        )
        self.tenant_id = tenant_id

    def grade_rag(self, case: dict[str, Any], result: dict[str, Any]) -> tuple[bool, str, str]:
        mode = (case.get("grading") or "keyword").strip().lower()
        if mode == "llm_judge" and self.api_key:
            try:
                passed, reason = self._llm_judge_rag(case, result)
                return passed, reason, "llm_judge"
            except Exception as exc:
                passed, reason = self._grade_rag_keyword(case, result)
                return passed, f"llm_judge fallback ({exc}): {reason}", "keyword_fallback"
        passed, reason = self._grade_rag_keyword(case, result)
        return passed, reason, "keyword"

    def _grade_rag_keyword(self, case: dict[str, Any], result: dict[str, Any]) -> tuple[bool, str]:
        answer = result.get("answer", "") or ""
        keywords = case.get("expected_keywords", [])
        expect_no_answer = case.get("expect_no_answer", False)

        if expect_no_answer:
            idk_indicators = ["don't know", "cannot", "not available", "not in", "outside", "refuse", "无法", "不知道"]
            answer_lower = answer.lower()
            if any(k.lower() in answer_lower for k in idk_indicators):
                return True, "Correctly indicated no answer"
            return False, f"Expected refusal/IDK but got: {answer[:100]}"

        if not keywords:
            return True, "No keywords to check"

        answer_lower = answer.lower()
        matched = [k for k in keywords if k.lower() in answer_lower]
        if matched:
            return True, f"Keywords matched: {matched}"
        return False, f"No keywords matched in answer. Expected any of: {keywords}"

    def _llm_judge_rag(self, case: dict[str, Any], result: dict[str, Any]) -> tuple[bool, str]:
        answer = result.get("answer", "") or ""
        query = case.get("query", "")
        rubric = case.get("rubric") or (
            "Judge if the answer is grounded, relevant, and correct for the query. "
            "If expect_no_answer is true, pass only when the model appropriately refuses."
        )
        expect_no_answer = bool(case.get("expect_no_answer", False))
        keywords = case.get("expected_keywords", [])

        system = (
            "You are an evaluation judge. Reply with JSON only: "
            '{"pass": bool, "score": float 0-1, "reason": string}'
        )
        user = (
            f"Query: {query}\n"
            f"Answer: {answer}\n"
            f"expect_no_answer: {expect_no_answer}\n"
            f"expected_keywords (optional): {keywords}\n"
            f"Rubric: {rubric}\n"
            "Score pass=true if score>=0.7 or answer clearly satisfies rubric."
        )
        raw = self._call_judge(system, user)
        parsed = _parse_judge_json(raw)
        passed = bool(parsed.get("pass", parsed.get("score", 0) >= 0.7))
        reason = str(parsed.get("reason") or raw[:200])
        score = parsed.get("score")
        if score is not None:
            reason = f"score={score}: {reason}"
        return passed, reason

    def _call_judge(self, system: str, user: str) -> str:
        payload = {
            "model": self.judge_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.0,
            "max_tokens": 256,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        req = urllib.request.Request(
            self.judge_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc

        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError("judge response missing choices")
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if not content:
            raise RuntimeError("judge response empty content")
        return content


def _parse_judge_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise
