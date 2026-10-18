import win32service, win32gui, win32con, ctypes, win32process
from ctypes import wintypes

hdesk = win32service.OpenDesktop('Default', 0, False, win32con.GENERIC_ALL)
ctypes.windll.user32.SetThreadDesktop(int(hdesk))

def enum(hwnd, ctx):
    if win32gui.IsWindowVisible(hwnd):
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w > 1000 and h > 600:
            tid, pid = win32process.GetWindowThreadProcessId(hwnd)
            title = win32gui.GetWindowText(hwnd).encode('ascii', 'replace').decode('ascii')
            cls = win32gui.GetClassName(hwnd)
            print(f'HWND {hwnd} | PID {pid} | cls: {cls} | size: {w}x{h} | title: {title[:70]}')

win32gui.EnumWindows(enum, None)
