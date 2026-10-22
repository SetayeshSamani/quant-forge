"""Single-host per-account OS lock, held across I/O without a DB transaction.

Kernel locks release on process death. Durable SUBMITTING/UNKNOWN states still
require reconciliation after that release. Canonical DB paths are mandatory;
network filesystems, hard-link aliases and distributed execution are unsupported.
"""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path

from wallex.execution.models import identifier


class WriterBusy(RuntimeError):
    pass


@contextmanager
def account_writer(ledger_path: Path, account_id: str):
    name = hashlib.sha256(identifier(account_id).encode("utf-8")).hexdigest() + ".lock"
    directory = Path(str(Path(ledger_path).resolve()) + ".writers")
    handle = None
    locked = False
    try:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            handle = (directory / name).open("a+b")
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            raise WriterBusy("Account writer is busy or its local lock is unavailable.") from None
        yield
    finally:
        if handle is not None:
            try:
                if locked:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
