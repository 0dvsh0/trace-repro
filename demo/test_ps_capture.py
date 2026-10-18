import win32service, win32gui, win32con, ctypes, time, subprocess
from ctypes import wintypes
from PIL import Image

hdesk = win32service.OpenDesktop('Default', 0, False, win32con.GENERIC_ALL)
ctypes.windll.user32.SetThreadDesktop(int(hdesk))

# Launch a powershell window
proc = subprocess.Popen(['powershell.exe', '-NoExit', '-Command', 'python demo/showcase.py'])
time.sleep(2.0)

ps_hwnd = None
def enum(hwnd, ctx):
    global ps_hwnd
    if win32gui.IsWindowVisible(hwnd):
        title = win32gui.GetWindowText(hwnd).lower()
        if 'powershell' in title or 'showcase' in title or 'trace-repro' in title:
            ps_hwnd = hwnd
win32gui.EnumWindows(enum, None)

print('Found PS HWND:', ps_hwnd)
if ps_hwnd:
    ctypes.windll.user32.ShowWindow(ps_hwnd, 3) # Maximize
    time.sleep(0.5)
    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(ps_hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    print(f'PS size: {w}x{h}')

    hdc = ctypes.windll.user32.GetWindowDC(ps_hwnd)
    mem = ctypes.windll.gdi32.CreateCompatibleDC(hdc)
    bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc, w, h)
    ctypes.windll.gdi32.SelectObject(mem, bmp)
    ctypes.windll.user32.PrintWindow(ps_hwnd, mem, 2)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
                    ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                    ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                    ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                    ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                    ('biClrImportant', wintypes.DWORD)]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth, bi.biHeight = w, -h
    bi.biPlanes, bi.biBitCount, bi.biCompression = 1, 32, 0
    buf = ctypes.create_string_buffer(w * h * 4)
    ctypes.windll.gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)

    img = Image.frombuffer('RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1).convert('RGB')
    img.save('demo/fresh_recording/test_ps_live.png')
    print('Saved test_ps_live.png!')

    ctypes.windll.gdi32.DeleteObject(bmp)
    ctypes.windll.gdi32.DeleteDC(mem)
    ctypes.windll.user32.ReleaseDC(ps_hwnd, hdc)

# Terminate test process
proc.terminate()
