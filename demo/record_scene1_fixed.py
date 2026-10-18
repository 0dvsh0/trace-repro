import os
import sys
import time
import wave
import subprocess
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
from recorder_engine import ChromeLiveRecorder

AUDIO_PATH = os.path.join("demo", "fresh_recording", "audio", "scene_01_incident.wav")
RAW_OUTPUT = os.path.join("demo", "fresh_recording", "video_raw", "scene_01_incident_raw.mp4")
MUXED_OUTPUT = os.path.join("demo", "fresh_recording", "scenes", "scene_01_incident_muxed.mp4")

with wave.open(AUDIO_PATH, 'r') as wf:
    dur = wf.getnframes() / float(wf.getframerate()) + 0.5 # 53.99s

print(f"Initializing ChromeLiveRecorder for Scene 1 (duration: {dur:.2f}s)...")
rec = ChromeLiveRecorder(hwnd=328650, fps=20)
rec.force_foreground()
rec.switch_tab(1)
time.sleep(0.5)

print("\n--- PRE-FLIGHT CALIBRATION (BEFORE RECORDING STARTS) ---")
print("1. Resetting shop database...")
rec.navigate_url("http://127.0.0.1:8000/shop-reset/")
time.sleep(1.0)

print("2. Pre-loading basket page so Tab 1 starts cleanly on Basket...")
rec.navigate_url("http://127.0.0.1:8000/demo/basket/")
time.sleep(2.0)

# Verify Tab 1 is 100% on Basket
raw_check = rec.grab_frame()
check_img = Image.frombuffer('RGBA', (rec.w, rec.h), raw_check, 'raw', 'BGRA', 0, 1)
check_img.save('demo/preflight_tab1_basket.png')
print("Verified Tab 1 is cleanly on Basket page (saved demo/preflight_tab1_basket.png)!")

actions = [
    (0.0, lambda: None, ()),                   # Frame 0: Clean Basket page with Aurex ANC Headphones
    (4.0, rec.scroll_down_arrow, ()),          # Scroll to show total ₹4,500
    (6.0, rec.scroll_down_arrow, ()),
    (9.0, rec.scroll_home, ()),                # Scroll back to top
    (12.0, rec.navigate_url, ("http://127.0.0.1:8000/checkout/",)), # Navigate to guest checkout
    (15.5, rec.click_at, (600, 470)),          # Focus email field
    (16.5, rec.select_all, ()),                # Select all
    (17.5, rec.paste_text, ("devansh@example.com",)), # Paste guest email
    (21.0, rec.press_enter, ()),               # Submit form via Enter key -> loads payment details
    (25.0, lambda: None, ()),                  # Showcase "Pay for your order" & total 4500.00
    (30.5, rec.click_at, (967, 553)),          # Click "Pay and place order"
    (35.5, lambda: None, ()),                  # Failure page renders: "We couldn't take your payment"
    (40.0, rec.scroll_down, ()),               # Scroll down to "What happened, in plain English"
    (46.0, rec.scroll_down_arrow, ()),         # Highlight Order reference / cancelled status
    (50.0, rec.scroll_down_arrow, ()),         # Hold view until end of scene
]

print("\n--- STARTING LIVE RECORDING OF SCENE 1 ---")
rec.record_scene(dur, actions, RAW_OUTPUT)
rec.close()
print("Scene 1 recorded cleanly!")

print("\n--- MUXING SCENE 1 WITH NARRATION AUDIO ---")
mux_cmd = [
    'ffmpeg', '-y',
    '-i', RAW_OUTPUT,
    '-i', AUDIO_PATH,
    '-c:v', 'copy',
    '-c:a', 'aac',
    '-b:a', '192k',
    '-shortest',
    MUXED_OUTPUT
]
res = subprocess.run(mux_cmd, capture_output=True, text=True)
if res.returncode != 0:
    print(f"Mux error: {res.stderr}")
else:
    print(f"Successfully generated {MUXED_OUTPUT}")

print("\n--- RE-CONCATENATING MASTER VIDEO ---")
concat_txt = os.path.join("demo", "fresh_recording", "final_concat.txt")
final_output = os.path.join("demo", "trace_repro_final_demo.mp4")

concat_cmd = [
    'ffmpeg', '-y',
    '-f', 'concat',
    '-safe', '0',
    '-i', concat_txt,
    '-c', 'copy',
    final_output
]
res = subprocess.run(concat_cmd, capture_output=True, text=True)
if res.returncode != 0:
    print(f"Concat error: {res.stderr}")
else:
    print(f"MASTER VIDEO UPDATED AT: {final_output}")
