from __future__ import annotations


class AIWorkflowService:
    def build_summary(self, task_type: str, prompt: str) -> str:
        return f"{task_type} workflow queued: {prompt[:120]}"
