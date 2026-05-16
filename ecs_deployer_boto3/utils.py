from collections.abc import Generator, Iterable
from typing import TypeVar


T = TypeVar('T')


def chunker(seq: Iterable[T], size: int) -> Generator[list[T], None, None]:
    """Yield chunks of ``size`` items from ``seq``.

    >>> list(chunker(range(10), 3))
    [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]]
    """
    chunk: list[T] = []
    for item in seq:
        chunk.append(item)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def echo(message: str, prefix: str = '->> ') -> None:
    """Print a message with a visible prefix.

    The prefix makes deployment output easy to spot in CI logs.
    """
    print(f'{prefix}{message}', flush=True)  # noqa: T201
