import sys
import os
import time
import ctypes
import win32clipboard

sys.path.insert(0, os.path.dirname(__file__))
from recorder_engine import ChromeLiveRecorder
from PIL import Image

rec = ChromeLiveRecorder(hwnd=328650, fps=20)
rec.switch_tab(1)
time.sleep(0.5)

url = "http://localhost:3000/graph?graph=demo&query=MATCH+(s%3ASpan)-%5Br%3AWROTE_ENTITY%5D-%3E(e%3AStateEntity+%7Bkey%3A+'orders%3A8812'%7D)+RETURN+s%2C+r%2C+e&view=Graph&layout=force"

win32clipboard.OpenClipboard()
win32clipboard.EmptyClipboard()
win32clipboard.SetClipboardText(url)
win32clipboard.CloseClipboard()

rec.force_foreground()
time.sleep(0.05)
ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
ctypes.windll.user32.keybd_event(0x4C, 0, 0, 0) # Ctrl+L
time.sleep(0.05)
ctypes.windll.user32.keybd_event(0x4C, 0, 2, 0)
ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
time.sleep(0.1)
ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
ctypes.windll.user32.keybd_event(0x56, 0, 0, 0) # Ctrl+V
time.sleep(0.05)
ctypes.windll.user32.keybd_event(0x56, 0, 2, 0)
ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
time.sleep(0.1)
ctypes.windll.user32.keybd_event(0x0D, 0, 0, 0) # Enter
time.sleep(0.05)
ctypes.windll.user32.keybd_event(0x0D, 0, 2, 0)

time.sleep(2.0)

raw = rec.grab_frame()
img = Image.frombuffer('RGBA', (rec.w, rec.h), raw, 'raw', 'BGRA', 0, 1)
img.save('demo/falkordb_exact_url.png')
print('Navigated Tab 1 to exact user URL!')
rec.close()
