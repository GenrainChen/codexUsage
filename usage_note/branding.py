"""App identity and native Windows title-bar colors; no custom window chrome."""

import ctypes
import sys
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / 'assets'
APP_ID = 'Genrain.CodexUsageNote'


def set_app_identity():
    """Give this app its own taskbar identity instead of grouping under Python."""
    if sys.platform == 'win32':
        shell = ctypes.WinDLL('shell32')
        function = shell.SetCurrentProcessExplicitAppUserModelID
        function.argtypes = [wintypes.LPCWSTR]
        function.restype = ctypes.c_long
        function(APP_ID)


def _colorref(hex_color):
    red, green, blue = bytes.fromhex(hex_color.lstrip('#'))
    return red | (green << 8) | (blue << 16)


def native_titlebar(window, background, foreground, border):
    """Apply per-window DWM colors; unsupported Windows keeps its normal frame."""
    if sys.platform != 'win32':
        return {}
    user = ctypes.WinDLL('user32')
    user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetAncestor.restype = wintypes.HWND
    handle = user.GetAncestor(window.winfo_id(), 2)  # GA_ROOT: Tk's wrapper HWND.
    dwm = ctypes.WinDLL('dwmapi')
    dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                        ctypes.c_void_p, wintypes.DWORD]
    dwm.DwmSetWindowAttribute.restype = ctypes.c_long
    values = {20: 0, 34: _colorref(border), 35: _colorref(background),
              36: _colorref(foreground)}
    results = {}
    for attribute, value in values.items():
        data = wintypes.DWORD(value)
        results[attribute] = dwm.DwmSetWindowAttribute(handle, attribute,
                                                     ctypes.byref(data), ctypes.sizeof(data))
    return results


def style_window(window, background, foreground, border):
    """Brand root and dialogs, including restored/remapped native frames."""
    if sys.platform == 'win32':
        window.iconbitmap(default=str(ASSETS / 'codex-usage.ico'))
    else:
        window._brand_icon = tk.PhotoImage(master=window, file=str(ASSETS / 'codex-usage-128.png'))
        window.iconphoto(True, window._brand_icon)

    def apply(event=None):
        if event is not None and event.widget is not window:
            return
        native_titlebar(window, background, foreground, border)

    window.bind('<Map>', apply, add='+')
    window.after_idle(apply)
