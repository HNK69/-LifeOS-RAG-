from typing import Any, Literal

from pydantic import BaseModel, Field


ExecutionStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
]


class ToolCall(BaseModel):
    """Record of one tool invocation."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str | None = None


class AgentState(BaseModel):
    """Runtime state for a single LifeOS agent execution."""

    query: str
    status: ExecutionStatus = "pending"

    tool_calls: list[ToolCall] = Field(default_factory=list)

    evidence: dict[str, Any] | None = None
    final_answer: str | None = None
    error: str | None = None

    step_count: int = 0
    max_steps: int = 5

    def mark_running(self) -> None:
        """Transition from pending to running."""
        if self.status in {"completed", "failed"}:
            raise ValueError(
                f"Cannot transition terminal state {self.status} to running."
            )
        self.status = "running"

    def mark_completed(self, final_answer: str | None = None) -> None:
        """Transition to completed state."""
        if self.status == "failed":
            raise ValueError("Cannot transition failed state to completed.")
        self.status = "completed"
        if final_answer is not None:
            self.final_answer = final_answer

    def mark_failed(self, error: str) -> None:
        """Transition to failed state."""
        self.status = "failed"
        self.error = error
