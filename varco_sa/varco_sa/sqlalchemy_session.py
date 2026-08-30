from collections.abc import Callable, Coroutine
from contextlib import AbstractAsyncContextManager
from contextvars import ContextVar, Token
from functools import wraps
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Global session context
current_session: ContextVar[AsyncSession] = ContextVar("current_session")

_R = TypeVar("_R")


class SessionContext(AbstractAsyncContextManager[AsyncSession]):
    """Context manager for multi-operation transactions."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.token: Token[AsyncSession] | None = None

    async def __aenter__(self) -> AsyncSession:
        self.token = current_session.set(self.session)
        return self.session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        if self.token is not None:
            current_session.reset(self.token)
        if exc_type:
            await self.session.rollback()
        else:
            await self.session.commit()
        await self.session.close()


def with_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[
    [Callable[..., Coroutine[Any, Any, _R]]],
    Callable[..., Coroutine[Any, Any, _R]],
]:
    """
    Decorator to automatically provide a session to repository methods.
    """

    def decorator(
        func: Callable[..., Coroutine[Any, Any, _R]],
    ) -> Callable[..., Coroutine[Any, Any, _R]]:
        @wraps(func)
        async def wrapper(
            self: Any,
            *args: Any,
            session: AsyncSession | None = None,
            **kwargs: Any,
        ) -> _R:
            if session is None:
                try:
                    # Try to get session from contextvar
                    session = current_session.get()
                except LookupError:
                    # Create a new session if not in context
                    async with session_factory() as s:
                        return await func(self, *args, session=s, **kwargs)
            return await func(self, *args, session=session, **kwargs)

        return wrapper

    return decorator
