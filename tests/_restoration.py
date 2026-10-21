"""Exact observations and named-file undo for the two grouped drift tests."""
from frutlups import gitws


def snapshot(root, cfg, calls, label):
    """Git-listed bytes and known ignored outputs; never walk ignored stores."""
    witness = gitws.workspace_witness(root, executable=cfg.git)
    raw = {name: (root / name).read_bytes() if (root / name).is_file() else None
           for name in witness["files"]}
    named = [root / cfg.ledger, root / ".git/index", root / ".git/template-commit-inflight",
             root / ".git/index.lock", root / ".git/frutlups-writer.lock", root / "local_state/plan.json"]
    directories = [root / "local_state", root / "local_state/frutlups", root / "local_state/frutlups/jobs"]
    directories += [job.cwd / "local_state/frutlups/jobs" / job.id for job in calls]
    listing = {}
    for directory in directories:
        entries = list(directory.iterdir()) if directory.exists() else []
        listing[directory.relative_to(root).as_posix()] = sorted(p.name for p in entries)
        if directory in directories[3:]:
            assert all(p.is_file() and not p.is_symlink() for p in entries), label + ": unexpected job output type"
            named.extend(entries)
    for path in named:
        assert not path.is_symlink(), label + ": symlink in named state"
        raw[path.relative_to(root).as_posix()] = path.read_bytes() if path.exists() else None
    archives = {p.name: p.read_bytes() for p in (root / ".git").glob("template-commit-resolved-*")}
    return dict(witness=witness, raw=raw, directories=listing, archives=archives)


def restore_file(root, path, original):
    """Undo only this variant's named file; None means it was newly created."""
    root, path = root.resolve(), path.absolute()
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("restoration must remain at the owned root without symlinks")
    if original is None:
        path.unlink()
    else:
        path.write_bytes(original)
