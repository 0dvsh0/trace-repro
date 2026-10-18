import win32service
import win32con
import ctypes
import win32gui

hdesk = win32service.OpenDesktop('Default', 0, False, win32con.GENERIC_ALL)
ctypes.windll.user32.SetThreadDesktop(int(hdesk))

def cb(hwnd, extra):
    cls = win32gui.GetClassName(hwnd)
    title = win32gui.GetWindowText(hwnd)
    if 'Chrome' in cls or 'Chrome' in title:
        rect = win32gui.GetWindowRect(hwnd)
        print(f"HWND: {hwnd}, Cls: '{cls}', Title: '{title}', Rect: {rect}, Vis: {win32gui.IsWindowVisible(hwnd)}")

win32gui.EnumWindows(cb, None)
