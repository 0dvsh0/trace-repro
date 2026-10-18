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
rec.switch_tab(1)
time.sleep(0.5)

print("--- PREPARATION BEFORE RECORDING ---")
print("1. Resetting shop database...")
rec.navigate_url("http://127.0.0.1:8000/shop-reset/")
time.sleep(1.0)

print("2. Pre-loading basket page so Tab 1 starts cleanly on Basket...")
rec.navigate_url("http://127.0.0.1:8000/demo/basket/")
time.sleep(1.5)

# Verify Tab 1 is cleanly on Basket
raw0 = rec.grab_frame()
Image.frombuffer('RGBA', (rec.w, rec.h), raw0, 'raw', 'BGRA', 0, 1).save('demo/perfect_00s_basket.png')
print("Captured demo/perfect_00s_basket.png")

print("\n--- SIMULATING SCENE 1 ACTIONS ---")
print("t=6s: Scrolling basket...")
rec.scroll_down_arrow()
rec.scroll_down_arrow()
time.sleep(0.5)

print("t=12s: Navigating to checkout...")
rec.navigate_url("http://127.0.0.1:8000/checkout/")
time.sleep(1.5)

print("t=16s: Entering email...")
rec.click_at(600, 470)
time.sleep(0.2)
rec.select_all()
rec.paste_text("devansh@example.com")
time.sleep(0.3)

raw_email = rec.grab_frame()
Image.frombuffer('RGBA', (rec.w, rec.h), raw_email, 'raw', 'BGRA', 0, 1).save('demo/perfect_18s_email.png')
print("Captured demo/perfect_18s_email.png")

print("t=22s: Submitting guest checkout via Enter key...")
rec.press_enter()
time.sleep(2.5)

raw_pay = rec.grab_frame()
Image.frombuffer('RGBA', (rec.w, rec.h), raw_pay, 'raw', 'BGRA', 0, 1).save('demo/perfect_26s_payment.png')
print("Captured demo/perfect_26s_payment.png")

print("t=32s: Clicking 'Pay and place order'...")
# Button is at (967, 553)
rec.click_at(967, 553)
time.sleep(3.5)

raw_fail = rec.grab_frame()
Image.frombuffer('RGBA', (rec.w, rec.h), raw_fail, 'raw', 'BGRA', 0, 1).save('demo/perfect_38s_failure.png')
print("Captured demo/perfect_38s_failure.png")

print("t=42s: Scrolling down to failure explanation...")
rec.scroll_down()
time.sleep(1.0)

raw_exp = rec.grab_frame()
Image.frombuffer('RGBA', (rec.w, rec.h), raw_exp, 'raw', 'BGRA', 0, 1).save('demo/perfect_48s_explanation.png')
print("Captured demo/perfect_48s_explanation.png")

rec.close()
print("All steps completed successfully!")
