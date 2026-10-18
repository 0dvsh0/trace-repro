import sys
import os
import time
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
from recorder_engine import ChromeLiveRecorder

rec = ChromeLiveRecorder(hwnd=328650, fps=20)
rec.force_foreground()
rec.switch_tab(1)
time.sleep(0.5)

print("0. Resetting shop state...")
rec.navigate_url("http://127.0.0.1:8000/shop-reset/")
time.sleep(1.5)

print("1. Loading basket on healed server...")
rec.navigate_url("http://127.0.0.1:8000/demo/basket/")
time.sleep(2.0)

raw0 = rec.grab_frame()
Image.frombuffer('RGBA', (rec.w, rec.h), raw0, 'raw', 'BGRA', 0, 1).save('demo/healed_01_basket.png')
print("Captured demo/healed_01_basket.png")

print("2. Navigating to checkout...")
rec.navigate_url("http://127.0.0.1:8000/checkout/")
time.sleep(2.0)

print("3. Entering email...")
rec.click_at(600, 470)
time.sleep(0.2)
rec.select_all()
rec.paste_text("devansh@example.com")
time.sleep(0.5)

print("4. Submitting guest email...")
rec.press_enter()
time.sleep(2.5)

raw_pay = rec.grab_frame()
Image.frombuffer('RGBA', (rec.w, rec.h), raw_pay, 'raw', 'BGRA', 0, 1).save('demo/healed_02_payment.png')
print("Captured demo/healed_02_payment.png")

print("5. Clicking 'Pay and place order'...")
rec.click_at(968, 566)
time.sleep(4.0)

raw_thankyou = rec.grab_frame()
Image.frombuffer('RGBA', (rec.w, rec.h), raw_thankyou, 'raw', 'BGRA', 0, 1).save('demo/healed_03_thankyou.png')
print("Captured demo/healed_03_thankyou.png")

rec.close()
print("Test completed!")
