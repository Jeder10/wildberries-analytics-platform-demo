"""Load exact-size Windows icons for the title bar and the taskbar."""

import ctypes
from ctypes import wintypes
from pathlib import Path


USER32 = ctypes.WinDLL("user32", use_last_error=True)
USER32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
USER32.GetAncestor.restype = wintypes.HWND
USER32.GetDpiForWindow.argtypes = (wintypes.HWND,)
USER32.GetDpiForWindow.restype = wintypes.UINT
USER32.GetSystemMetricsForDpi.argtypes = (ctypes.c_int, wintypes.UINT)
USER32.GetSystemMetricsForDpi.restype = ctypes.c_int
USER32.LoadImageW.argtypes = (
    wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT,
)
USER32.LoadImageW.restype = wintypes.HANDLE
USER32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
USER32.SendMessageW.restype = ctypes.c_ssize_t
USER32.DestroyIcon.argtypes = (wintypes.HICON,)
USER32.DestroyIcon.restype = wintypes.BOOL

GA_ROOT = 2
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
SM_CXICON, SM_CYICON = 11, 12
SM_CXSMICON, SM_CYSMICON = 49, 50


def apply_window_icons(window_id: int, path: Path) -> tuple[int, ...]:
    window = USER32.GetAncestor(window_id, GA_ROOT) or window_id
    dpi = USER32.GetDpiForWindow(window) or 96
    handles = []
    for kind, x_metric, y_metric in (
        (ICON_SMALL, SM_CXSMICON, SM_CYSMICON),
        (ICON_BIG, SM_CXICON, SM_CYICON),
    ):
        width = USER32.GetSystemMetricsForDpi(x_metric, dpi)
        height = USER32.GetSystemMetricsForDpi(y_metric, dpi)
        handle = USER32.LoadImageW(None, str(path), IMAGE_ICON, width, height, LR_LOADFROMFILE)
        if handle:
            USER32.SendMessageW(window, WM_SETICON, kind, handle)
            handles.append(handle)
    return tuple(handles)


def release_window_icons(handles: tuple[int, ...]) -> None:
    for handle in handles:
        USER32.DestroyIcon(handle)
