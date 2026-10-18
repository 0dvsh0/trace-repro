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
    time.sleep(1.5)

def capture(name):
    raw = rec.grab_frame()
    Image.frombuffer('RGBA', (rec.w, rec.h), raw, 'raw', 'BGRA', 0, 1).save(f'demo/{name}.png')
    print(f"Captured demo/{name}.png")

print("1. Switching to Tab 1 (Shop)...")
send_ctrl_num(1)

print("2. Resetting shop database...")
nav_to("http://127.0.0.1:8000/shop-reset/")

print("3. Navigating to demo basket...")
nav_to("http://127.0.0.1:8000/demo/basket/")
time.sleep(1.0)
capture("flow_step1_basket")

print("4. Navigating to guest checkout...")
nav_to("http://127.0.0.1:8000/checkout/")
time.sleep(1.0)

print("5. Clicking email input at (600, 470)...")
rec.click_at(600, 470)
time.sleep(0.2)
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
time.sleep(0.2)
capture("flow_step2_email_entered")

print("6. Clicking Continue button at (578, 732)...")
rec.click_at(578, 732)
time.sleep(2.0)
capture("flow_step3_payment_details")

print("7. Finding Pay button on payment details...")
# On payment details page, click "Pay and place order"
rec.click_at(967, 612)
time.sleep(3.0)
capture("flow_step4_failure_result")

print("8. Scrolling down on failure page...")
rec.scroll_down()
time.sleep(1.0)
capture("flow_step5_failure_explanation")

rec.close()
print("All steps executed successfully!")
