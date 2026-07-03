"""H4 concurrency-safety contracts for the artifact filesystem.

These pin the invariants that let place/route run in parallel with BOM
finalization without clobbering:

* :func:`atomic_write_text` / ``atomic_write_bytes`` never expose a torn/partial
  file to a concurrent reader, and leave the original intact if the write fails.
* :func:`global_lock` gives true mutual exclusion *across processes* (it is a
  pid-file lock; it is intentionally not a thread lock).

All are mutation-checkable: reverting the atomic write to ``write_text`` or the
lock body to a no-op makes the corresponding test go red.
"""

import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from faebryk.libs.util import atomic_write_bytes, atomic_write_text, global_lock

# Two distinct, complete payloads of very different length. A torn write would
# splice them and be detectable as "not one of the two".
_SMALL = "a" * 64
_LARGE = "b" * (512 * 1024)


def test_atomic_write_text_never_torn(tmp_path: Path):
    target = tmp_path / "board.txt"
    atomic_write_text(target, _SMALL)

    stop = threading.Event()
    seen_bad: list[str] = []

    def reader():
        while not stop.is_set():
            try:
                content = target.read_text()
            except FileNotFoundError:
                # os.replace is atomic; the target always exists once seeded
                seen_bad.append("missing")
                continue
            if content not in (_SMALL, _LARGE):
                seen_bad.append(f"torn:{len(content)}")

    t = threading.Thread(target=reader)
    t.start()
    try:
        for i in range(400):
            atomic_write_text(target, _LARGE if i % 2 else _SMALL)
    finally:
        stop.set()
        t.join()

    assert not seen_bad, f"reader observed non-atomic writes: {seen_bad[:5]}"


def test_atomic_write_bytes_crash_leaves_original_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / "board.bin"
    atomic_write_bytes(target, b"ORIGINAL")

    def boom(src, dst):
        raise OSError("simulated crash between temp-write and replace")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        atomic_write_bytes(target, b"NEW-DATA-THAT-SHOULD-NOT-LAND")

    # original is untouched, and no stray temp file was left behind
    assert target.read_bytes() == b"ORIGINAL"
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp." in p.name]
    assert not leftovers, f"temp files leaked: {leftovers}"


# --- global_lock cross-process mutual exclusion --------------------------------

# module-level worker (must be picklable for the process pool)
def _locked_increment(args: tuple[str, str, int]) -> None:
    lock_path, counter_path, iters = args
    from faebryk.libs.util import global_lock  # re-import in child

    for _ in range(iters):
        with global_lock(Path(lock_path), timeout_s=30):
            # non-atomic read-modify-write of a shared counter: if the lock
            # fails to exclude, increments are lost.
            p = Path(counter_path)
            cur = int(p.read_text()) if p.exists() else 0
            time.sleep(0.0005)  # widen the race window
            p.write_text(str(cur + 1))


def test_global_lock_excludes_across_processes(tmp_path: Path):
    lock_path = tmp_path / "board.lock"
    counter_path = tmp_path / "counter"
    counter_path.write_text("0")

    n_procs, iters = 4, 25
    args = [(str(lock_path), str(counter_path), iters) for _ in range(n_procs)]
    with ProcessPoolExecutor(max_workers=n_procs) as ex:
        list(ex.map(_locked_increment, args))

    assert int(counter_path.read_text()) == n_procs * iters
    assert not lock_path.exists(), "lock file must be released on exit"
