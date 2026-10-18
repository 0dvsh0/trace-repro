import win32service, win32gui, win32con, ctypes, time, urllib.parse, win32clipboard
from ctypes import wintypes
from PIL import Image

hdesk = win32service.OpenDesktop('Default', 0, False, win32con.GENERIC_ALL)
ctypes.windll.user32.SetThreadDesktop(int(hdesk))

chrome_hwnd = None
def enum(hwnd, ctx):
    global chrome_hwnd
    if win32gui.IsWindowVisible(hwnd) and 'chrome' in win32gui.GetWindowText(hwnd).lower():
        chrome_hwnd = hwnd
win32gui.EnumWindows(enum, None)

ctypes.windll.user32.SetForegroundWindow(chrome_hwnd)
time.sleep(0.1)

# Tab 1
ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
ctypes.windll.user32.keybd_event(0x31, 0, 0, 0)
ctypes.windll.user32.keybd_event(0x31, 0, 2, 0)
ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
time.sleep(0.3)

cypher = "MATCH (s:Span)-[r:WROTE_ENTITY]->(e:StateEntity {key: 'orders:8812'}) RETURN s, r, e"
url = 'http://localhost:3000/graph?graph=demo&query=' + urllib.parse.quote(cypher) + '&view=Graph&layout=force'

# Ctrl+L
ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
ctypes.windll.user32.keybd_event(0x4C, 0, 0, 0)
ctypes.windll.user32.keybd_event(0x4C, 0, 2, 0)
ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
time.sleep(0.2)

win32clipboard.OpenClipboard()
win32clipboard.EmptyClipboard()
win32clipboard.SetClipboardText(url)
win32clipboard.CloseClipboard()

# Ctrl+V
ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
ctypes.windll.user32.keybd_event(0x56, 0, 0, 0)
ctypes.windll.user32.keybd_event(0x56, 0, 2, 0)
ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
time.sleep(0.1)

# Enter
ctypes.windll.user32.keybd_event(0x0D, 0, 0, 0)
ctypes.windll.user32.keybd_event(0x0D, 0, 2, 0)
time.sleep(2.5)

rect = wintypes.RECT()
ctypes.windll.user32.GetWindowRect(chrome_hwnd, ctypes.byref(rect))
w, h = rect.right - rect.left, rect.bottom - rect.top

hdc = ctypes.windll.user32.GetWindowDC(chrome_hwnd)
mem = ctypes.windll.gdi32.CreateCompatibleDC(hdc)
bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc, w, h)
ctypes.windll.gdi32.SelectObject(mem, bmp)
ctypes.windll.user32.PrintWindow(chrome_hwnd, mem, 2)

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
img.save('demo/fresh_recording/check_falkordb_query.png')
print('Captured FalkorDB query result!')

ctypes.windll.gdi32.DeleteObject(bmp)
ctypes.windll.gdi32.DeleteDC(mem)
ctypes.windll.user32.ReleaseDC(chrome_hwnd, hdc)
