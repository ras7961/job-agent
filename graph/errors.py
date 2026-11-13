import asyncio
import functools
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx
from langchain_core.exceptions import OutputParserException
from langgraph.errors import GraphBubbleUp
from pydantic import ValidationError

from graph.state import AgentState


log = logging.getLogger("job_agent")
_TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


class GroundingError(Exception):
    """LLM output made claims not supported by the resume. Retryable (regenerate)."""

    retryable = True


def is_transient(exc: BaseException) -> bool:
    if getattr(exc, "retryable", False):
        return True
    if isinstance(
        exc,
        (asyncio.TimeoutError, TimeoutError, ConnectionError, httpx.TransportError,
         OutputParserException, ValidationError),
    ):
        return True
    if type(exc).__name__ == "TimeoutError":
        return True
    return getattr(exc, "status_code", None) in _TRANSIENT_STATUS


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_error(node: str, exc: BaseException, state: AgentState | None = None) -> dict:
    fingerprint = None
    try:
        fingerprint = state["jobs"][state["cursor"]]["fingerprint"]  # type: ignore[index]
    except Exception:
        pass
    return {
        "node": node,
        "fingerprint": fingerprint,
        "type": type(exc).__name__,
        "message": str(exc)[:500],
        "retryable": is_transient(exc),
        "ts": now_iso(),
    }


async def retry_async(
    fn: Callable[[], Awaitable[Any]],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 20.0,
    timeout: float | None = None,
    label: str = "",
) -> Any:
    """Retry transient errors with exponential backoff + jitter. Never swallows interrupts."""
    for attempt in range(1, attempts + 1):
        try:
            coro = fn()
            return await (asyncio.wait_for(coro, timeout) if timeout else coro)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            if attempt >= attempts or not is_transient(exc):
                raise
            delay = min(max_delay, base_delay * 2 ** (attempt - 1)) * (0.5 + random.random())
            log.warning(
                "%s attempt %d/%d failed (%s: %s); retrying in %.1fs",
                label, attempt, attempts, type(exc).__name__, exc, delay,
            )
            await asyncio.sleep(delay)


def resilient(
    name: str,
    *,
    timeout: float | None = None,
    retries: int = 2,
    fatal: bool = False,
):
    """Retry transient errors and convert other node errors into state-level failures."""

    def decorator(fn: Callable[[AgentState], Awaitable[dict]]):
        @functools.wraps(fn)
        async def wrapper(state: AgentState) -> dict:
            started = time.monotonic()
            try:
                update = await retry_async(
                    lambda: fn(state),
                    attempts=retries + 1,
                    timeout=timeout,
                    label=name,
                )
            except GraphBubbleUp:
                raise
            except Exception as exc:
                err = make_error(name, exc, state)
                log.exception("node=%s failed fatal=%s run=%s", name, fatal, state.get("run_id"))
                return {"failed": True, "fatal": fatal, "failure": err, "errors": [err]}
            log.info("node=%s ok %.2fs run=%s", name, time.monotonic() - started, state.get("run_id"))
            return update

        return wrapper

    return decorator
