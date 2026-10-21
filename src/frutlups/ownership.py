"""Native writer exclusion shared with the pinned manual template."""

import os
import threading
from contextlib import contextmanager
from pathlib import Path


_owners = {}
_roots = {}
_guard = threading.RLock()


@contextmanager
def writer_lock(root, roadmap=None, *, executable="git"):
    from . import gitws

    if roadmap is not None and roadmap.schema != "frutlups.roadmap/2":
        raise ValueError("unsupported roadmap schema; use native /2 or pinned v4")
    root = Path(root).resolve()
    owner = (os.getpid(), threading.get_ident())
    with _guard:
        nested = _roots.get(root) == owner
    if nested:
        yield
        return
    result = gitws._git(root, "rev-parse", "--git-common-dir",
                        executable=executable, check=False)
    if result.returncode:
        raise ValueError("initialize the project Git repository before mutating state")
    folder = (root / os.fsdecode(result.stdout.strip())).resolve()
    path = folder / "frutlups-writer.lock"
    message = "another writer owns the repository; status remains available"
    with _guard:
        if folder in _owners:
            if _owners[folder] != owner:
                raise ValueError(message)
            nested = True
        else:
            nested = False
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError("writer lock must be a regular file")
            stream = path.open("a+b")
            try:
                stream.seek(0, 2)
                if not stream.tell():
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                stream.close()
                raise ValueError(message) from exc
            except BaseException:
                stream.close()
                raise
            _owners[folder] = owner
        _roots[root] = owner
    try:
        yield
    finally:
        with _guard:
            _roots.pop(root, None)
            if not nested:
                _owners.pop(folder, None)
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                finally:
                    stream.close()
