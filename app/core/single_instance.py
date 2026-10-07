"""Process-wide guard that prevents multiple NEXTBUY gateway sessions."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import IO

_LOCK_HANDLE: IO[bytes] | None = None


class AlreadyRunningError(RuntimeError):
    pass


def acquire_single_instance_lock(name: str = "nextbuy-bot") -> None:
    global _LOCK_HANDLE
    if _LOCK_HANDLE is not None:
        return

    path = Path(tempfile.gettempdir()) / f"{name}.lock"
    handle = path.open("a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()

    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError) as exc:
        handle.close()
        raise AlreadyRunningError(
            "O NEXTBUY já está em execução. Encerre a instância atual antes de iniciar outra."
        ) from exc

    _LOCK_HANDLE = handle


def release_single_instance_lock() -> None:
    global _LOCK_HANDLE
    handle = _LOCK_HANDLE
    if handle is None:
        return

    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
        _LOCK_HANDLE = None
