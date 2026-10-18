import sys
import os
import time
import ctypes
import win32clipboard
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
from recorder_engine import ChromeLiveRecorder

rec = ChromeLiveRecorder(hwnd=328650, fps=20)
rec.force_foreground()

# Open new tab via Ctrl+T
ctypes.windll.user32.keybd_event(0x11, 0, 0, 0) # Ctrl
ctypes.windll.user32.keybd_event(0x54, 0, 0, 0) # T
time.sleep(0.05)
ctypes.windll.user32.keybd_event(0x54, 0, 2, 0)
ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
time.sleep(0.5)

# Navigate to FalkorDB URL
falkor_url = "http://localhost:3000/graph?graph=demo&query=MATCH+(s%3ASpan)-%5Br%3AWROTE_ENTITY%5D-%3E(e%3AStateEntity+%7Bkey%3A+'orders%3A8812'%7D)+RETURN+s%2C+r%2C+e&view=Graph&layout=force"
win32clipboard.OpenClipboard()
win32clipboard.EmptyClipboard()
win32clipboard.SetClipboardText(falkor_url)
win32clipboard.CloseClipboard()

ctypes.windll.user32.keybd_event(0x11, 0, 0, 0) # Ctrl
ctypes.windll.user32.keybd_event(0x56, 0, 0, 0) # V
time.sleep(0.05)
ctypes.windll.user32.keybd_event(0x56, 0, 2, 0)
ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
time.sleep(0.1)

ctypes.windll.user32.keybd_event(0x0D, 0, 0, 0) # Enter
time.sleep(0.05)
ctypes.windll.user32.keybd_event(0x0D, 0, 2, 0)
time.sleep(2.5)

raw = rec.grab_frame()
img = Image.frombuffer('RGBA', (rec.w, rec.h), raw, 'raw', 'BGRA', 0, 1)
img.save('demo/tab_5_falkor.png')
print('FalkorDB tab opened and captured to demo/tab_5_falkor.png!')
rec.close()
