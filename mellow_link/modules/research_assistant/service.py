from __future__ import annotations

import re
from typing import Iterable, List


class ResearchAssistantService:
    MAX_DOCUMENT_CHARS = 4000
    MAX_SUMMARY_CHARS = 3200
    REDUCED_DOCUMENT_CHARS = 1800

    def build_prompt(self, question: str, context_note: str = "", document_context: str = "") -> str:
        prompt = f"[research_assistant]\nQuestion: {question.strip()}"
        if context_note.strip():
            prompt += f"\nContext: {context_note.strip()}"
        if document_context.strip():
            clipped = document_context.strip()[: self.MAX_DOCUMENT_CHARS]
            if len(document_context.strip()) > self.MAX_DOCUMENT_CHARS:
                clipped += "\n...(문서 내용이 길어 일부만 사용됨)..."
            prompt += f"\n\nDocument Context:\n{clipped}"
        prompt += (
            "\n\nInstruction:"
            "\n- Answer from the uploaded documents first."
            "\n- Write in Korean."
            "\n- Keep it practical and readable for an end user."
            "\n- Organize the response under these headings:"
            "\n  한 줄 결론"
            "\n  핵심 요약"
            "\n  주요 쟁점"
            "\n  다음 액션"
            "\n- Avoid debug wording, placeholders, and generic completion phrases."
        )
        return prompt

    def build_reduced_prompt(self, question: str, context_note: str = "", document_context: str = "") -> str:
        clipped = (document_context or "").strip()[: self.REDUCED_DOCUMENT_CHARS]
        reduced_note = context_note.strip()
        if reduced_note:
            reduced_note = reduced_note[:240]
        prompt = f"[research_assistant_retry]\nQuestion: {question.strip()}"
        if reduced_note:
            prompt += f"\nContext: {reduced_note}"
        if clipped:
            prompt += f"\n\nDocument Context (Reduced):\n{clipped}"
        prompt += (
            "\n\nInstruction:"
            "\n- Answer only from the provided document context."
            "\n- Ignore workspace, tools, system bootstrap, and environment metadata."
            "\n- Write in Korean."
            "\n- Produce only end-user content under these headings:"
            "\n  한 줄 결론"
            "\n  핵심 요약"
            "\n  주요 쟁점"
            "\n  다음 액션"
        )
        return prompt

    def is_weak_summary(self, raw_summary: str) -> bool:
        text = self._normalize_text(raw_summary)
        if not text or len(text) < 80:
            return True
        lowered = text.lower()
        weak_markers = (
            '"status":"initialized"',
            '"workspace_directory"',
            '"docs_directory"',
            '"available_tools"',
            '"status":"ready"',
            "작업을 기다려",
            "시스템 지침 인식 완료",
            "시스템 지시사항 수신 완료",
        )
        if any(marker in lowered for marker in weak_markers):
            return True
        if text.startswith("{") and text.endswith("}"):
            return True
        return False

    def format_user_summary(self, raw_summary: str, question: str, has_document_context: bool) -> str:
        text = self._normalize_text(raw_summary)
        if not text:
            return self._fallback_summary(question, has_document_context)

        lines = [line.strip("-• \t") for line in text.splitlines() if line.strip()]
        sentences = self._extract_sentences(text)
        bullets = [line for line in lines if len(line) > 8]

        conclusion = self._pick_conclusion(lines, sentences, question)
        summary_items = self._pick_summary_items(lines, sentences, conclusion)
        issue_items = self._pick_issue_items(lines, sentences)
        action_items = self._pick_action_items(lines, sentences, question, has_document_context)

        return self._render_sections(
            conclusion=conclusion,
            summary_items=summary_items,
            issue_items=issue_items,
            action_items=action_items,
        )[: self.MAX_SUMMARY_CHARS].rstrip()

    def _normalize_text(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        text = re.sub(r"\r\n?", "\n", text)
        text = re.sub(r"```(?:[\w-]+)?", "", text)
        text = text.replace("```", "")
        text = text.replace("[REDACTED_PATH]", "")
        text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
        text = re.sub(r"__(.*?)__", r"\1", text)
        text = text.replace("`", "")
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(
            r"(?i)\b(research run completed|run completed|analysis completed|문서 기반 리서치 실행이 완료되었습니다\.?)\b",
            "",
            text,
        )
        text = re.sub(r"\s*->\s*", " -> ", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r" ?\n ?", "\n", text)
        return text.strip()

    def _extract_sentences(self, text: str) -> List[str]:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            return []
        parts = re.split(r"(?<=[.!?다요])\s+|\n+", cleaned)
        return [part.strip(" -•\t") for part in parts if len(part.strip(" -•\t")) >= 12]

    def _pick_conclusion(self, lines: List[str], sentences: List[str], question: str) -> str:
        heading_match = self._extract_heading_content(lines, ("한 줄 결론", "결론", "요약 결론"))
        if heading_match:
            return heading_match
        for candidate in lines + sentences:
            if len(candidate) >= 12:
                return candidate[:220]
        return f"질문 '{question[:80]}'에 대해 문서 기준으로 핵심 내용을 정리했습니다."

    def _pick_summary_items(self, lines: List[str], sentences: List[str], conclusion: str) -> List[str]:
        heading_items = self._extract_heading_block(lines, ("핵심 요약", "요약", "핵심 내용"))
        if heading_items:
            return heading_items[:3]
        items = self._dedupe_preserve_order(
            item for item in lines + sentences
            if item != conclusion and len(item) >= 12 and not self._looks_like_issue(item) and not self._looks_like_action(item)
        )
        return items[:3] or [conclusion]

    def _pick_issue_items(self, lines: List[str], sentences: List[str]) -> List[str]:
        heading_items = self._extract_heading_block(lines, ("주요 쟁점", "쟁점", "주의 사항", "리스크", "한계"))
        if heading_items:
            return heading_items[:3]
        items = self._dedupe_preserve_order(
            item for item in lines + sentences if self._looks_like_issue(item)
        )
        return items[:3] or ["문서 근거가 충분한 범위에서만 결론을 해석해야 합니다."]

    def _pick_action_items(self, lines: List[str], sentences: List[str], question: str, has_document_context: bool) -> List[str]:
        heading_items = self._extract_heading_block(lines, ("다음 액션", "권장 조치", "추천 액션", "후속 조치"))
        if heading_items:
            return heading_items[:3]
        items = self._dedupe_preserve_order(
            item for item in lines + sentences if self._looks_like_action(item)
        )
        if items:
            return items[:3]
        if has_document_context:
            return [
                "문서 원문에서 핵심 수치와 주장 근거를 다시 확인하세요.",
                "필요하면 질문 범위를 더 좁혀 다시 실행해 세부 비교를 받으세요.",
            ]
        return [
            "분석에 필요한 문서를 추가 업로드한 뒤 다시 실행하세요.",
            f"질문 '{question[:60]}'을 더 구체적으로 나눠 후속 실행을 진행하세요.",
        ]

    def _extract_heading_content(self, lines: List[str], headings: Iterable[str]) -> str:
        heading_set = tuple(headings)
        for index, line in enumerate(lines):
            normalized = line.replace(":", "").strip()
            if normalized in heading_set:
                if index + 1 < len(lines):
                    return lines[index + 1]
            for heading in heading_set:
                if normalized.startswith(heading):
                    content = normalized[len(heading):].strip(" :")
                    if content:
                        return content
        return ""

    def _extract_heading_block(self, lines: List[str], headings: Iterable[str]) -> List[str]:
        heading_set = tuple(headings)
        for index, line in enumerate(lines):
            normalized = line.replace(":", "").strip()
            if normalized in heading_set:
                items: List[str] = []
                for later in lines[index + 1:]:
                    later_norm = later.replace(":", "").strip()
                    if later_norm in ("한 줄 결론", "결론", "핵심 요약", "요약", "주요 쟁점", "쟁점", "다음 액션", "권장 조치", "추천 액션", "후속 조치"):
                        break
                    if later:
                        items.extend(self._expand_compound_items(later))
                return items
        return []

    def _looks_like_issue(self, text: str) -> bool:
        keywords = ("다만", "주의", "한계", "리스크", "불확실", "쟁점", "확인 필요", "부족", "추가 검토")
        return any(keyword in text for keyword in keywords)

    def _looks_like_action(self, text: str) -> bool:
        keywords = ("확인", "검토", "추가", "재실행", "업로드", "권장", "추천", "진행", "분석", "정리", "점검")
        return any(keyword in text for keyword in keywords)

    def _fallback_summary(self, question: str, has_document_context: bool) -> str:
        action = (
            "문서 원문을 다시 확인하고 질문 범위를 더 좁혀 재실행하세요."
            if has_document_context
            else "분석에 필요한 문서를 업로드한 뒤 질문을 더 구체적으로 입력하세요."
        )
        return (
            "한 줄 결론\n"
            f"- 질문 '{question[:80]}'에 대해 충분한 문서 기반 응답을 생성하지 못했습니다.\n\n"
            "핵심 요약\n"
            "- 실행은 완료되었지만 사용자에게 전달할 수준의 요약 텍스트가 충분히 생성되지 않았습니다.\n\n"
            "주요 쟁점\n"
            "- 문서 근거가 부족하거나 질문 범위가 넓어 응답이 빈약해질 수 있습니다.\n\n"
            "다음 액션\n"
            f"- {action}"
        )

    def _render_sections(
        self,
        *,
        conclusion: str,
        summary_items: List[str],
        issue_items: List[str],
        action_items: List[str],
    ) -> str:
        def section(title: str, items: List[str]) -> str:
            lines = [title]
            for item in items:
                cleaned = self._sanitize_display_text(item)
                if cleaned:
                    lines.append(f"- {cleaned}")
            return "\n".join(lines)

        return "\n\n".join([
            section("한 줄 결론", [self._sanitize_display_text(conclusion)]),
            section("핵심 요약", summary_items[:3]),
            section("주요 쟁점", issue_items[:3]),
            section("다음 액션", action_items[:3]),
        ])

    def _dedupe_preserve_order(self, items: Iterable[str]) -> List[str]:
        seen = set()
        results: List[str] = []
        for item in items:
            normalized = re.sub(r"\s+", " ", item).strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned = self._sanitize_display_text(normalized)
            if cleaned:
                results.append(cleaned[:260])
        return results

    def _expand_compound_items(self, text: str) -> List[str]:
        normalized = self._sanitize_display_text(text)
        if not normalized:
            return []
        parts = re.split(r"\s+-\s+(?=[^-\s])", normalized)
        expanded: List[str] = []
        for part in parts:
            candidate = part.strip(" -•\t")
            if not candidate:
                continue
            expanded.append(candidate)
        return expanded or [normalized]

    def _sanitize_display_text(self, text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return ""
        cleaned = cleaned.replace("[REDACTED_PATH]", "")
        cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
        cleaned = re.sub(r"__(.*?)__", r"\1", cleaned)
        cleaned = cleaned.replace("`", "")
        cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
        cleaned = re.sub(r"\s*\[[A-Z_]+]\s*", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
        cleaned = re.sub(r"\(\s+", "(", cleaned)
        cleaned = re.sub(r"\s+\)", ")", cleaned)
        cleaned = cleaned.strip(" -•\t")
        return cleaned
