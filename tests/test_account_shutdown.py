"""Process-level coverage for closing the note while an account read is active."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

from tests.test_account import AUTH_CLAIM, fake_jwt


_SERVER = r'''
import json, os, sys, time
from pathlib import Path
home = Path(os.environ["CODEX_HOME"])
reads = 0
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if method == "initialize":
        (home / "server-started").write_text("ready")
    if os.environ["NOTE_SHUTDOWN_CASE"] == "timeout":
        time.sleep(30)
    if method == "initialized":
        continue
    time.sleep(.05)
    if method == "initialize":
        result = {}
    elif method == "account/read":
        reads += 1
        result = {"account": {"type": "chatgpt", "email": "test@example.test", "planType": "pro"}}
        if reads == 2:
            (home / "read-completed").write_text("complete")
    elif method == "account/rateLimits/read":
        result = {"accountId": "test-account", "rateLimits": {
            "limitId": "codex", "primary": {"usedPercent": 10, "windowDurationMins": 10080}}}
    else:
        sys.exit(8)
    print(json.dumps({"id": request["id"], "result": result}), flush=True)
'''


_PARENT = r'''
import queue, subprocess, sys, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from usage_note.account_limits import fetch_account_limits
from usage_note.ui import UsageNote

home = Path(sys.argv[1])
original_popen = subprocess.Popen
def launch(args, **kwargs):
    process = original_popen([sys.executable, "-u", str(home / "fake_server.py")], **kwargs)
    (home / "child.pid").write_text(str(process.pid))
    return process

# Fake only the widgets and scan input. refresh() creates the production worker.
note = UsageNote.__new__(UsageNote)
note._busy = note._closed = False
note._results = queue.Queue()
note.refresh_button = note.status_label = SimpleNamespace(configure=lambda **kwargs: None)
note.reader = SimpleNamespace(scan=lambda: {"days": {}, "warnings": []})
note.account_store = SimpleNamespace(warnings=[])
note._read_account_data = lambda: fetch_account_limits(home, timeout=.7)
patch("usage_note.account_limits._find_codex", return_value=Path("fake.exe")).start()
patch("usage_note.account_limits.subprocess.Popen", side_effect=launch).start()
note.refresh()
deadline = time.monotonic() + 3
while not (home / "server-started").exists() and time.monotonic() < deadline:
    time.sleep(.01)
if not (home / "server-started").exists():
    raise RuntimeError("The synthetic server did not start")
# This matches main() returning after the window closes. The interpreter must
# keep the request owner alive until its finally block has cleaned up the child.
'''


class AccountShutdownTests(unittest.TestCase):
    def _matching_process_alive(self, pid, server, *, terminate=False):
        if os.name == "nt":
            # Verify both PID and this test's unique script path before cleanup;
            # never target another Python/Codex process by executable name.
            quoted_path = str(server).replace("'", "''")
            action = f"Stop-Process -Id {pid} -ErrorAction SilentlyContinue;" if terminate else ""
            command = (
                f"$noteTestProcess = Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; "
                f"if ($noteTestProcess -and $noteTestProcess.CommandLine.Contains('{quoted_path}')) "
                f"{{ {action} Write-Output 'alive' }} else {{ Write-Output 'exited' }}"
            )
            result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                                    capture_output=True, text=True, timeout=5,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, "Could not inspect the synthetic child process")
            return result.stdout.strip() == "alive"
        try:
            os.kill(pid, signal.SIGTERM if terminate else 0)
        except ProcessLookupError:
            return False
        return True

    def _run_shutdown(self, case):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            server = home / "fake_server.py"
            server.write_text(textwrap.dedent(_SERVER), encoding="utf-8")
            runner = home / "parent.py"
            runner.write_text(textwrap.dedent(_PARENT), encoding="utf-8")
            auth = {"tokens": {"account_id": "test-account", "id_token": fake_jwt({
                "email": "test@example.test", AUTH_CLAIM: {"chatgpt_plan_type": "pro"}})}}
            (home / "auth.json").write_text(json.dumps(auth), encoding="utf-8")
            environment = dict(os.environ, NOTE_SHUTDOWN_CASE=case,
                               PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            started = time.monotonic()
            parent = subprocess.Popen([sys.executable, str(runner), str(home)],
                                      env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            child_pid = None
            try:
                stdout, stderr = parent.communicate(timeout=5)
                elapsed = time.monotonic() - started
                self.assertEqual(parent.returncode, 0,
                                 "The test parent failed before orderly shutdown: "
                                 + stderr.decode("utf-8", errors="replace"))
                self.assertEqual(stderr, b"")
                child_pid = int((home / "child.pid").read_text())
                self.assertFalse(self._matching_process_alive(child_pid, server),
                                 "Closing the note abandoned the account query child process")
                self.assertLess(elapsed, 4, "Shutdown must finish within the bounded account read")
                if case == "success":
                    self.assertTrue((home / "read-completed").exists(),
                                    "The account read was abandoned before its final identity check")
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.communicate(timeout=3)
                if child_pid is None and (home / "child.pid").exists():
                    child_pid = int((home / "child.pid").read_text())
                if child_pid is not None:
                    self._matching_process_alive(child_pid, server, terminate=True)

    def test_exit_waits_for_successful_account_read_and_closes_its_server(self):
        self._run_shutdown("success")

    def test_exit_waits_for_deadline_and_closes_unresponsive_server(self):
        self._run_shutdown("timeout")


if __name__ == "__main__":
    unittest.main()
