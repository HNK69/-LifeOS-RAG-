import atexit
import concurrent.futures
import threading
import time
from typing import Any

from app.agent.decision import AgentDecision, AgentPlanner
from app.agent.executor import AgentExecutor
from app.agent.state import AgentState
from intelligence.models import IntentPlan
from query.router import QueryResult

_agent_executor_lock = threading.Lock()
_shared_agent_executor: concurrent.futures.ThreadPoolExecutor | None = None


def get_agent_executor() -> concurrent.futures.ThreadPoolExecutor:
    """
    Retrieve or lazily initialize the shared bounded agent thread pool.

    The pool size is strictly bounded by AGENT_MAX_WORKERS to prevent unbounded
    thread accumulation when synchronous operations block or stall.
    """
    global _shared_agent_executor
    with _agent_executor_lock:
        if _shared_agent_executor is None:
            try:
                from config import AGENT_MAX_WORKERS
            except ImportError:
                from app.config import AGENT_MAX_WORKERS
            _shared_agent_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=AGENT_MAX_WORKERS,
                thread_name_prefix="lifeos_agent_worker",
            )
        return _shared_agent_executor


def shutdown_agent_executor(wait: bool = False, cancel_futures: bool = True) -> None:
    """Cleanly shut down the shared agent executor and cancel pending futures."""
    global _shared_agent_executor
    with _agent_executor_lock:
        if _shared_agent_executor is not None:
            try:
                _shared_agent_executor.shutdown(wait=wait, cancel_futures=cancel_futures)
            except TypeError:
                _shared_agent_executor.shutdown(wait=wait)
            _shared_agent_executor = None


atexit.register(shutdown_agent_executor, wait=False, cancel_futures=True)


class AgentRunner:
    """Run a bounded LifeOS agent execution loop."""

    def __init__(
        self,
        planner: AgentPlanner,
        executor: AgentExecutor,
        max_steps: int = 5,
        timeout: float | None = None,
        executor_pool: concurrent.futures.ThreadPoolExecutor | None = None,
    ):
        self.planner = planner
        self.executor = executor
        self.max_steps = max_steps
        self.executor_pool = executor_pool
        if timeout is None:
            try:
                from config import AGENT_TIMEOUT_SECONDS
                self.timeout = float(AGENT_TIMEOUT_SECONDS)
            except ImportError:
                from app.config import AGENT_TIMEOUT_SECONDS
                self.timeout = float(AGENT_TIMEOUT_SECONDS)
        else:
            self.timeout = float(timeout)

    def _execute_with_timeout(self, func, timeout: float) -> Any:
        """
        Execute func with a wall-clock timeout using a bounded worker pool.

        Python threads executing synchronous blocking operations cannot be
        forcibly killed from outside. Instead of spawning unbounded threads per
        timed-out operation, execution is dispatched to a strictly bounded
        thread pool. If func blocks longer than timeout, TimeoutError is raised,
        any pending/queued future is cancelled, and the control loop regains
        execution immediately without leaking unmanaged worker threads.
        """
        if timeout <= 0:
            raise TimeoutError("Agent execution timed out.")

        pool = self.executor_pool or get_agent_executor()
        future = pool.submit(func)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("Agent execution timed out.")

    def run(
        self,
        query: str,
        plan: IntentPlan | None = None,
        timeout: float | None = None,
    ) -> AgentState:
        """Plan and execute a bounded agent loop."""

        state = AgentState(query=query, max_steps=self.max_steps)

        if not query or not query.strip():
            state.mark_failed("Query must not be empty.")
            return state

        effective_timeout = (
            float(timeout) if timeout is not None else self.timeout
        )

        if effective_timeout <= 0:
            state.mark_failed("Agent execution timed out.")
            return state

        deadline = time.monotonic() + effective_timeout

        state.mark_running()

        try:
            while state.step_count < state.max_steps:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    state.mark_failed("Agent execution timed out.")
                    return state

                # Step 0: query only; step > 0: pass query and state
                step_plan = plan if state.step_count == 0 else None
                if state.step_count == 0:
                    decision = self._execute_with_timeout(
                        lambda: self._call_planner(query, plan=step_plan),
                        timeout=remaining,
                    )
                else:
                    decision = self._execute_with_timeout(
                        lambda: self._call_planner(query, state=state, plan=step_plan),
                        timeout=remaining,
                    )

                if not isinstance(decision, AgentDecision):
                    state.mark_failed("Planner returned invalid decision type.")
                    return state

                # 1. Terminal finish decision
                if decision.action == "finish":
                    final_ans = (
                        decision.final_answer
                        or self._derive_final_answer(state)
                    )
                    state.mark_completed(final_ans)
                    return state

                # 2. Terminal fail decision
                if decision.action == "fail":
                    state.mark_failed(
                        decision.error or "Agent planning failed."
                    )
                    return state

                # 3. Tool execution decision
                if decision.action == "tool":
                    if not decision.tool_name:
                        state.mark_failed("Tool name required for tool decision.")
                        return state

                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        state.mark_failed("Agent execution timed out.")
                        return state

                    state = self._execute_with_timeout(
                        lambda: self.executor.execute_tool(
                            state=state,
                            tool_name=decision.tool_name,
                            arguments=decision.arguments,
                        ),
                        timeout=remaining,
                    )

                    if state.status == "failed":
                        return state

                    # Single-step or terminal tool execution
                    if not decision.has_more_steps:
                        final_ans = (
                            decision.final_answer
                            or self._derive_final_answer(state)
                        )
                        state.mark_completed(final_ans)
                        return state

                    # If has_more_steps is True but reached step limit
                    if state.step_count >= state.max_steps:
                        state.mark_failed("Maximum agent steps exceeded.")
                        return state

            # Exited loop without explicit completion
            if state.status == "running":
                state.mark_failed("Maximum agent steps exceeded.")
            return state

        except TimeoutError:
            state.mark_failed("Agent execution timed out.")
            return state
        except Exception as exc:
            state.mark_failed(str(exc))
            return state

    def _call_planner(
        self,
        query: str,
        state: AgentState | None = None,
        plan: IntentPlan | None = None,
    ) -> AgentDecision:
        """Invoke planner, passing state if accepted by the planner signature."""
        if state is not None:
            if plan is not None:
                try:
                    return self.planner.decide(query, state=state, plan=plan)
                except TypeError:
                    pass
            try:
                return self.planner.decide(query, state=state)
            except TypeError:
                return self.planner.decide(query)
        if plan is not None:
            try:
                return self.planner.decide(query, plan=plan)
            except TypeError:
                pass
        return self.planner.decide(query)

    def _derive_final_answer(self, state: AgentState) -> str | None:
        """Derive grounded summary answer from execution evidence."""
        if not state.tool_calls:
            return None

        last_call = state.tool_calls[-1]
        last_result = last_call.result

        if isinstance(last_result, QueryResult):
            if (
                last_result.answer_type == "time"
                and isinstance(last_result.data, dict)
            ):
                data = last_result.data
                return (
                    f"Today is {data.get('weekday')}, {data.get('date')}. "
                    f"Current time is {data.get('time')}."
                )

            if (
                last_result.answer_type == "files"
                and isinstance(last_result.data, list)
            ):
                count = len(last_result.data)
                return f"Found {count} file{'s' if count != 1 else ''}."

            if (
                last_result.answer_type == "documents"
                and isinstance(last_result.data, list)
            ):
                count = len(last_result.data)
                return (
                    f"Retrieved {count} relevant document chunk"
                    f"{'s' if count != 1 else ''}."
                )

        elif isinstance(last_result, dict):
            return str(last_result)

        return None
