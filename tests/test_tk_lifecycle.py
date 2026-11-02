"""Process-level regressions for Tk ownership during background reads."""

import os
from pathlib import Path
import subprocess
import sys
import unittest


_PROBE = r'''
import gc
from pathlib import Path
import sys
import tempfile
import threading
import time
import tkinter as tk

from usage_note.pricing import PriceBook
from usage_note.reader import UsageReader
from usage_note.timebase import BEIJING_TZ
from usage_note.ui import UsageNote


def collect_on_worker():
    thread = threading.Thread(target=gc.collect, name="lifecycle-gc-probe")
    thread.start()
    thread.join()


# Choose exactly when to collect so an unsafe interpreter release is a reliable
# failure, rather than depending on allocations and the default GC threshold.
gc.disable()
with tempfile.TemporaryDirectory() as directory:
    home = Path(directory)
    prices = PriceBook(home / "prices.json", home / "overrides.json")
    if sys.argv[1] == "closed":
        for _ in range(3):
            root = tk.Tk()
            root.withdraw()
            note = UsageNote(root, UsageReader(home), prices, home / "settings.json")
            deadline = time.monotonic() + 3
            while note._busy and time.monotonic() < deadline:
                root.update()
                time.sleep(.01)
            assert not note._busy, "Reader did not finish"
            note.close()
            del note, root
            collect_on_worker()
    else:
        started, release = threading.Event(), threading.Event()

        class BlockingReader:
            codex_home = home
            tz = BEIJING_TZ

            def scan(self):
                started.set()
                assert release.wait(3), "Window was not destroyed before waiting for the reader"
                time.sleep(.05)
                return {"days": {}, "warnings": []}

        root = tk.Tk()
        root.withdraw()
        note = UsageNote(root, BlockingReader(), prices, home / "settings.json")
        assert started.wait(3), "Reader did not start"
        root.bind("<Destroy>", lambda event: release.set() if event.widget is root else None, add="+")
        readers = [thread for thread in threading.enumerate() if thread.name == "codex-usage-reader"]
        note.close()
        finished_before_return = all(not thread.is_alive() for thread in readers)
        for thread in readers:
            thread.join(timeout=3)
        assert finished_before_return, "Closing returned while a worker still owned the Tk window"
        del note, root
        collect_on_worker()
'''


@unittest.skipUnless(os.environ.get("CODEX_NOTE_GUI_TESTS") == "1",
                     "Set CODEX_NOTE_GUI_TESTS=1 to allow Tk lifecycle tests")
class TkLifecycleTests(unittest.TestCase):
    def run_probe(self, case):
        result = subprocess.run(
            [sys.executable, "-X", "faulthandler", "-c", _PROBE, case],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(result.stderr, b"", "Tk cleanup emitted an error or warning")

    def test_closed_windows_are_safe_when_next_collection_runs_on_worker(self):
        self.run_probe("closed")

    def test_close_waits_for_reader_after_destroying_window(self):
        self.run_probe("active")


if __name__ == "__main__":
    unittest.main()
