"""Real Tk regressions for title-bar callback lifetime."""

import os
import tkinter as tk
import unittest
from unittest.mock import patch

from usage_note.branding import style_window


@unittest.skipUnless(os.environ.get("CODEX_NOTE_GUI_TESTS") == "1",
                     "Set CODEX_NOTE_GUI_TESTS=1 to allow visible Tk tests")
class BrandingTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.update()
        self.interpreter = self.root.tk
        self.interpreter.eval("set ::branding_errors {}; "
                              "proc bgerror {message} {lappend ::branding_errors $message}")
        native = patch("usage_note.branding.native_titlebar")
        self.native_titlebar = native.start()
        self.addCleanup(native.stop)

    def tearDown(self):
        if self.root is not None:
            self.root.destroy()
        # Tcl survives Tk destruction: drain queued callbacks so their errors
        # fail this test instead of leaking into a later window's event loop.
        self.interpreter.call("update")
        errors = self.interpreter.splitlist(
            self.interpreter.call("set", "::branding_errors"))
        self.assertEqual(errors, (), "Tk callbacks outlived their window")

    def style(self, window):
        style_window(window, "#F7F4EC", "#292D2A", "#E2DED3")

    def test_dialog_destroyed_before_idle_does_not_leave_callback(self):
        dialog = tk.Toplevel(self.root)
        self.style(dialog)
        dialog.destroy()
        self.root.update()
        self.native_titlebar.assert_not_called()

    def test_root_destroyed_before_idle_does_not_leave_callback(self):
        self.style(self.root)
        self.root.destroy()
        self.root = None
        self.native_titlebar.assert_not_called()

    def test_destroying_child_preserves_parent_idle_callback(self):
        child = tk.Frame(self.root)
        self.style(self.root)
        child.destroy()
        self.root.update_idletasks()
        self.native_titlebar.assert_called_once_with(
            self.root, "#F7F4EC", "#292D2A", "#E2DED3")

    def test_style_still_applies_after_idle_and_remapping(self):
        self.style(self.root)
        self.root.update_idletasks()
        self.native_titlebar.assert_called_once()
        self.root.deiconify()
        self.root.update()
        self.root.withdraw()
        self.root.update()
        self.native_titlebar.reset_mock()
        self.root.deiconify()
        self.root.update()
        self.native_titlebar.assert_called_once_with(
            self.root, "#F7F4EC", "#292D2A", "#E2DED3")


if __name__ == "__main__":
    unittest.main()
