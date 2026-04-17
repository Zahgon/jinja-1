import inspect
import typing as t
from functools import WRAPPER_ASSIGNMENTS
from functools import wraps

from .utils import _PassArg
from .utils import pass_eval_context

if t.TYPE_CHECKING:
    import typing_extensions as te

V = t.TypeVar("V")


def async_variant(normal_func):  # type: ignore
    def decorator(async_func):  # type: ignore
        pass

    return decorator


_common_primitives = {int, float, bool, str, list, dict, tuple, type(None)}


async def auto_await(value: t.Union[t.Awaitable["V"], "V"]) -> "V":
    # Avoid a costly call to isawaitable
    if type(value) in _common_primitives:
        return t.cast("V", value)

    if inspect.isawaitable(value):
        return await t.cast("t.Awaitable[V]", value)

    return value


class _IteratorToAsyncIterator(t.Generic[V]):
    def __init__(self, iterator: "t.Iterator[V]"):
        self._iterator = iterator

    def __aiter__(self) -> "te.Self":
        return self

    async def __anext__(self) -> V:
        try:
            return next(self._iterator)
        except StopIteration as e:
            raise StopAsyncIteration(e.value) from e


def auto_aiter(
    iterable: "t.AsyncIterable[V] | t.Iterable[V]",
) -> "t.AsyncIterator[V]":
    if hasattr(iterable, "__aiter__"):
        return iterable.__aiter__()
    else:
        return _IteratorToAsyncIterator(iter(iterable))


async def auto_to_list(
    value: "t.AsyncIterable[V] | t.Iterable[V]",
) -> list["V"]:
    return [x async for x in auto_aiter(value)]
