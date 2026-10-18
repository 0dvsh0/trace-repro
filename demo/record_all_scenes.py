import os
import sys
import time
import wave
import subprocess

sys.path.insert(0, os.path.dirname(__file__))
from recorder_engine import ChromeLiveRecorder

AUDIO_DIR = os.path.join("demo", "fresh_recording", "audio")
RAW_VIDEO_DIR = os.path.join("demo", "fresh_recording", "video_raw")
SCENES_DIR = os.path.join("demo", "fresh_recording", "scenes")
os.makedirs(RAW_VIDEO_DIR, exist_ok=True)
os.makedirs(SCENES_DIR, exist_ok=True)

def get_audio_duration(wav_path):
    with wave.open(wav_path, 'r') as wf:
        return wf.getnframes() / float(wf.getframerate())

def build_scene_choreography(rec):
    return [
        {
            "id": "scene_01_incident",
            "wav": os.path.join(AUDIO_DIR, "scene_01_incident.wav"),
            "preflight": lambda: (
                rec.switch_tab(1),
                time.sleep(0.5),
                rec.navigate_url("http://127.0.0.1:8000/shop-reset/"),
                time.sleep(1.2),
                rec.navigate_url("http://127.0.0.1:8000/demo/basket/"),
                time.sleep(1.8)
            ),
            "actions": [
                (0.0, lambda: None, ()),                   # Clean Basket page with Aurex ANC Headphones
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
        },
        {
            "id": "scene_02_gitlab_ci",
            "wav": os.path.join(AUDIO_DIR, "scene_02_gitlab_ci.wav"),
            "actions": [
                (0.1, rec.switch_tab, (4,)), # Tab 4: GitLab Job Log #17230374712
                (0.5, rec.scroll_home, ()),
                (4.0, rec.scroll_down, ()),  # View 253 spans log line
                (10.0, rec.scroll_down, ()), # View stack trace and exit code 1
                (16.0, rec.scroll_up, ()),   # View runner metadata
                (22.0, rec.switch_tab, (3,)),# Tab 3: GitLab MR !1
                (24.0, rec.scroll_home, ()),
                (28.0, rec.scroll_down, ()), # Show reproduction pipeline #2954886147
                (35.0, rec.scroll_down_arrow, ()),
            ]
        },
        {
            "id": "scene_03_falkordb",
            "wav": os.path.join(AUDIO_DIR, "scene_03_falkordb.wav"),
            "actions": [
                (0.1, rec.switch_tab, (5,)), # Tab 5: FalkorDB Browser
                (0.5, rec.scroll_home, ()),
                (3.0, rec.click_at, (932, 196)),  # Click blue RUN button to execute Cypher query live
                (12.0, rec.click_at, (711, 385)), # Click expire_cart node
                (24.0, rec.click_at, (1256, 747)),# Click apply_webhook node
                (36.0, rec.click_at, (808, 494)), # Click orders:8812 entity node
                (46.0, rec.click_at, (960, 540)), # Click canvas background
                (52.0, rec.click_at, (932, 196)), # Re-run query to showcase graph view
            ]
        },
        {
            "id": "scene_04_terminal_healing",
            "wav": os.path.join(AUDIO_DIR, "scene_04_terminal_healing.wav"),
            "preflight": lambda: (
                rec.switch_tab(2),
                time.sleep(0.5),
                rec.navigate_url("file:///D:/coding/hackathon/trace-repro/demo/demo.html"),
                time.sleep(1.5)
            ),
            "actions": [
                (0.1, rec.switch_tab, (2,)), # Tab 2: demo.html
                (0.5, rec.scroll_home, ()),
                (2.0, rec.scroll_down, ()),
                (4.0, rec.scroll_down, ()),  # Focus on console viewer
                (7.0, rec.click_at, (1530, 310)), # Select Page 1 thumbnail (Stages 1-4)
                (9.0, rec.click_at, (900, 500)),  # Open Page 1 lightbox (Stages 1 to 4 with RED exit=1)
                (27.0, rec.click_at, (1860, 125)),# Close lightbox
                (29.0, rec.click_at, (1530, 395)),# Select Page 2 thumbnail (Stages 3 to 6)
                (32.0, rec.click_at, (900, 500)),  # Open Page 2 lightbox (Stages 4, 5 GREEN exit=0, 6 diff)
                (50.0, rec.click_at, (1860, 125)),# Close lightbox
                (53.0, rec.scroll_down_arrow, ()),
            ]
        },
        {
            "id": "scene_05_gitlab_mr",
            "wav": os.path.join(AUDIO_DIR, "scene_05_gitlab_mr.wav"),
            "actions": [
                (0.1, rec.switch_tab, (3,)), # Tab 3: GitLab MR !1
                (0.5, rec.scroll_home, ()),
                (3.0, rec.scroll_down, ()),  # View causal collision table
                (9.0, rec.scroll_down, ()),  # View JUnit widget & Code Quality
                (15.0, rec.scroll_down_arrow, ()), # View git diff / commit
                (20.0, rec.scroll_up, ()),   # Return to top
            ]
        },
        {
            "id": "scene_06_three_services",
            "wav": os.path.join(AUDIO_DIR, "scene_06_three_services.wav"),
            "actions": [
                (0.1, rec.switch_tab, (2,)), # Tab 2: demo.html
                (0.5, rec.scroll_home, ()),
                (3.0, rec.scroll_down_arrow, ()),
                (5.0, rec.scroll_down_arrow, ()),
                (8.0, rec.click_at, (300, 320)),  # Checkout: Lost Update
                (16.0, rec.click_at, (700, 320)), # Inventory: Oversell on stocks:4471
                (26.0, rec.click_at, (1100, 320)),# Payments: Double Charge on payments:8812
            ]
        },
        {
            "id": "scene_07_checklist_close",
            "wav": os.path.join(AUDIO_DIR, "scene_07_checklist_close.wav"),
            "preflight": lambda: (
                rec.switch_tab(1),
                time.sleep(0.5),
                rec.navigate_url("http://127.0.0.1:8000/shop-reset/"),
                time.sleep(1.2),
                rec.navigate_url("http://127.0.0.1:8000/demo/basket/"),
                time.sleep(1.8)
            ),
            "actions": [
                (0.1, rec.switch_tab, (1,)), # Tab 1: Oscar Shop
                (0.5, lambda: None, ()),     # Show basket with Aurex ANC Headphones (₹4,500.00)
                (3.0, rec.scroll_down_arrow, ()),
                (5.0, rec.navigate_url, ("http://127.0.0.1:8000/checkout/",)), # Navigate to checkout
                (8.0, rec.click_at, (600, 470)), # Focus email field
                (9.0, rec.select_all, ()),
                (10.0, rec.paste_text, ("devansh@example.com",)), # Enter guest email
                (12.5, rec.press_enter, ()),     # Submit guest email -> loads payment options
                (16.5, rec.click_at, (968, 566)),# Click "Pay and place order"
                (21.0, rec.scroll_down_arrow, ()), # Show Order confirmation page
                (26.0, rec.scroll_home, ()),     # Linger on confirmed order header
            ]
        }
    ]

def main():
    print("Initializing ChromeLiveRecorder...")
    rec = ChromeLiveRecorder(hwnd=328650, fps=20)

    scenes = build_scene_choreography(rec)
    final_clips = []

    for idx, sc in enumerate(scenes):
        scene_id = sc["id"]
        wav_file = sc["wav"]
        dur = get_audio_duration(wav_file) + 0.5 # Add 0.5s padding
        print(f"\n=======================================================")
        print(f"Recording {idx+1}/{len(scenes)}: {scene_id} ({dur:.2f}s)")
        print(f"=======================================================")

        raw_mp4 = os.path.join(RAW_VIDEO_DIR, f"{scene_id}_raw.mp4")
        muxed_mp4 = os.path.join(SCENES_DIR, f"{scene_id}_muxed.mp4")
        # Run preflight calibration if defined
        if "preflight" in sc:
            print(f"Running preflight for {scene_id}...")
            sc["preflight"]()

        # Record continuous raw video
        rec.record_scene(dur, sc["actions"], raw_mp4)

        # Mux with audio
        mux_cmd = [
            'ffmpeg', '-y',
            '-i', raw_mp4,
            '-i', wav_file,
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-b:a', '192k',
            '-shortest',
            muxed_mp4
        ]
        res = subprocess.run(mux_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"Mux error for {scene_id}: {res.stderr}")
        else:
            print(f"Successfully muxed {muxed_mp4}")
            final_clips.append(muxed_mp4)

    rec.close()
    print("\nChromeLiveRecorder closed.")

    # Assemble final video via concat
    concat_txt = os.path.join("demo", "fresh_recording", "final_concat.txt")
    with open(concat_txt, "w", encoding="utf-8") as f:
        for clip in final_clips:
            # Use forward slashes for ffmpeg concat
            f.write(f"file '{os.path.abspath(clip).replace(os.sep, '/')}'\n")

    final_output = os.path.join("demo", "trace_repro_final_demo.mp4")
    print(f"\nConcatenating all {len(final_clips)} scenes into {final_output}...")

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
        print(f"\nSUCCESS! Broadcast video generated at: {final_output}")

        # Probe final video
        probe_cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration,size,bit_rate:stream=codec_name,width,height,r_frame_rate',
            '-of', 'default=noprint_wrappers=1',
            final_output
        ]
        probe_res = subprocess.run(probe_cmd, capture_output=True, text=True)
        print("\nFINAL VIDEO METADATA:")
        print(probe_res.stdout)

if __name__ == '__main__':
    main()
