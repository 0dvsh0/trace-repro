import sys
import os
import time
import ctypes
import win32clipboard
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
from recorder_engine import ChromeLiveRecorder

rec = ChromeLiveRecorder(hwnd=328650, fps=20)

def send_ctrl_num(num):
    rec.force_foreground()
    time.sleep(0.05)
    vk_num = 0x30 + num
    ctypes.windll.user32.keybd_event(0x11, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk_num, 0, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(vk_num, 0, 2, 0)
    ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
    time.sleep(0.3)

def nav_to(url):
    rec.force_foreground()
    win32clipboard.OpenClipboard()
    win32clipboard.EmptyClipboard()
    win32clipboard.SetClipboardText(url)
    win32clipboard.CloseClipboard()

    ctypes.windll.user32.keybd_event(0x11, 0, 0, 0) # Ctrl+L
    ctypes.windll.user32.keybd_event(0x4C, 0, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(0x4C, 0, 2, 0)
    ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
    time.sleep(0.1)

    ctypes.windll.user32.keybd_event(0x11, 0, 0, 0) # Ctrl+V
    ctypes.windll.user32.keybd_event(0x56, 0, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(0x56, 0, 2, 0)
    ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
    time.sleep(0.1)

    ctypes.windll.user32.keybd_event(0x0D, 0, 0, 0) # Enter
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(0x0D, 0, 2, 0)
    time.sleep(1.2)

print("Switching to Tab 1...")
send_ctrl_num(1)

print("Resetting shop catalogue...")
nav_to("http://127.0.0.1:8000/shop-reset/")

print("Loading demo basket...")
nav_to("http://127.0.0.1:8000/demo/basket/")
time.sleep(1.0)

raw1 = rec.grab_frame()
Image.frombuffer('RGBA', (rec.w, rec.h), raw1, 'raw', 'BGRA', 0, 1).save('demo/oscar_1_basket.png')
print("Captured basket page")

print("Clicking 'Proceed to checkout' at (1320, 945)...")
rec.click_at(1320, 945)
time.sleep(2.0)

raw2 = rec.grab_frame()
Image.frombuffer('RGBA', (rec.w, rec.h), raw2, 'raw', 'BGRA', 0, 1).save('demo/oscar_2_guest.png')
print("Captured guest checkout page")

print("Clicking email input (600, 470)...")
rec.click_at(600, 470)
time.sleep(0.2)
# Select all and paste email
ctypes.windll.user32.keybd_event(0x11, 0, 0, 0) # Ctrl+A
ctypes.windll.user32.keybd_event(0x41, 0, 0, 0)
time.sleep(0.05)
ctypes.windll.user32.keybd_event(0x41, 0, 2, 0)
ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
time.sleep(0.1)

win32clipboard.OpenClipboard()
win32clipboard.EmptyClipboard()
win32clipboard.SetClipboardText("devansh@example.com")
win32clipboard.CloseClipboard()

ctypes.windll.user32.keybd_event(0x11, 0, 0, 0) # Ctrl+V
ctypes.windll.user32.keybd_event(0x56, 0, 0, 0)
time.sleep(0.05)
ctypes.windll.user32.keybd_event(0x56, 0, 2, 0)
ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
time.sleep(0.3)

print("Clicking Continue button at (298, 700)...")
rec.click_at(298, 700)
time.sleep(2.0)

raw3 = rec.grab_frame()
Image.frombuffer('RGBA', (rec.w, rec.h), raw3, 'raw', 'BGRA', 0, 1).save('demo/oscar_3_payment.png')
print("Captured payment page")

rec.close()
