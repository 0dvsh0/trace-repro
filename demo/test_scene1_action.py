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

print("1. Resetting catalogue...")
rec.navigate_url("http://127.0.0.1:8000/shop-reset/")
time.sleep(1.0)

print("2. Navigating to demo basket...")
rec.navigate_url("http://127.0.0.1:8000/demo/basket/")
time.sleep(1.5)

raw_basket = rec.grab_frame()
Image.frombuffer('RGBA', (rec.w, rec.h), raw_basket, 'raw', 'BGRA', 0, 1).save('demo/test_1_basket.png')

print("3. Navigating to guest checkout...")
rec.navigate_url("http://127.0.0.1:8000/checkout/")
time.sleep(1.2)

print("4. Entering email...")
rec.click_at(600, 470)
time.sleep(0.2)
rec.select_all()
rec.paste_text("devansh@example.com")
time.sleep(0.3)

print("5. Submitting guest checkout...")
rec.click_at(578, 732)
time.sleep(2.0)

raw_pay = rec.grab_frame()
Image.frombuffer('RGBA', (rec.w, rec.h), raw_pay, 'raw', 'BGRA', 0, 1).save('demo/test_2_payment.png')

print("6. Clicking 'Pay and place order'...")
# Let's inspect the button center on test_2_payment.png
img = Image.open('demo/test_2_payment.png')
# Find blue button on this exact frame
btn_pixels = []
for y in range(400, 800):
    for x in range(400, 1600):
        p = img.getpixel((x, y))
        if p[2] > 200 and p[0] < 50:
            btn_pixels.append((x, y))
if btn_pixels:
    xs = [p[0] for p in btn_pixels]
    ys = [p[1] for p in btn_pixels]
    cx = (min(xs) + max(xs)) // 2
    cy = (min(ys) + max(ys)) // 2
    print(f"Calculated button center: ({cx}, {cy})")
    rec.click_at(cx, cy)
else:
    print("Fallback click at (967, 580)")
    rec.click_at(967, 580)

print("7. Waiting for race condition & failure page...")
time.sleep(3.5)

raw_fail = rec.grab_frame()
Image.frombuffer('RGBA', (rec.w, rec.h), raw_fail, 'raw', 'BGRA', 0, 1).save('demo/test_3_failure.png')
print("Saved demo/test_3_failure.png")

rec.close()
