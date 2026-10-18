import sys
import os
import time
import ctypes
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
from recorder_engine import ChromeLiveRecorder

rec = ChromeLiveRecorder(hwnd=328650, fps=20)
rec.force_foreground()

# Let's inspect each tab 1 to 7
def press_ctrl_w():
    ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
    ctypes.windll.user32.keybd_event(0x57, 0, 0, 0) # W
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(0x57, 0, 2, 0)
    ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
    time.sleep(0.3)

# If tab 7 is 'New tab', close it
rec.switch_tab(7)
time.sleep(0.5)
press_ctrl_w()
time.sleep(0.5)

# If tab 2 is 'All products', switch to it and close it
rec.switch_tab(2)
time.sleep(0.5)
press_ctrl_w()
time.sleep(0.5)

# Now capture tabs 1 to 5 to verify their titles
for t in range(1, 6):
    rec.switch_tab(t)
    time.sleep(0.5)
    raw = rec.grab_frame()
    Image.frombuffer('RGBA', (rec.w, rec.h), raw, 'raw', 'BGRA', 0, 1).save(f'demo/clean_tab_{t}.png')
    print(f"Captured clean_tab_{t}.png")

rec.close()
