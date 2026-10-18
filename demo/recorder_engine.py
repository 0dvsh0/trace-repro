import os
import time
import subprocess
import ctypes
from ctypes import wintypes
import win32service
import win32gui
import win32con
import win32process
import win32clipboard

class ChromeLiveRecorder:
    def __init__(self, hwnd=328650, fps=20):
        self.hwnd = hwnd
        self.fps = fps
        self.frame_delay = 1.0 / fps

        # Attach to interactive desktop
        self.hdesk = win32service.OpenDesktop('Default', 0, False, win32con.GENERIC_ALL)
        ctypes.windll.user32.SetThreadDesktop(int(self.hdesk))

        self.force_foreground()

        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        self.w = rect.right - rect.left
        self.h = rect.bottom - rect.top
        self.w = self.w - (self.w % 2)
        self.h = self.h - (self.h % 2)

        # Prepare GDI DCs
        self.hdc = ctypes.windll.user32.GetWindowDC(self.hwnd)
        self.mem = ctypes.windll.gdi32.CreateCompatibleDC(self.hdc)
        self.bmp = ctypes.windll.gdi32.CreateCompatibleBitmap(self.hdc, self.w, self.h)
        ctypes.windll.gdi32.SelectObject(self.mem, self.bmp)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
                        ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                        ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                        ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                        ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                        ('biClrImportant', wintypes.DWORD)]

        self.bi = BITMAPINFOHEADER()
        self.bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        self.bi.biWidth = self.w
        self.bi.biHeight = -self.h
        self.bi.biPlanes = 1
        self.bi.biBitCount = 32
        self.bi.biCompression = 0
        self.buf = ctypes.create_string_buffer(self.w * self.h * 4)

    def force_foreground(self):
        try:
            fg = ctypes.windll.user32.GetForegroundWindow()
            fg_tid, _ = win32process.GetWindowThreadProcessId(fg)
            cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
            target_tid, _ = win32process.GetWindowThreadProcessId(self.hwnd)

            ctypes.windll.user32.AttachThreadInput(cur_tid, fg_tid, True)
            ctypes.windll.user32.AttachThreadInput(cur_tid, target_tid, True)
            ctypes.windll.user32.ShowWindow(self.hwnd, win32con.SW_MAXIMIZE)
            ctypes.windll.user32.SetForegroundWindow(self.hwnd)
            ctypes.windll.user32.SetFocus(self.hwnd)
            ctypes.windll.user32.AttachThreadInput(cur_tid, target_tid, False)
            ctypes.windll.user32.AttachThreadInput(cur_tid, fg_tid, False)
        except Exception:
            pass

    def grab_frame(self):
        ctypes.windll.user32.PrintWindow(self.hwnd, self.mem, 2)
        ctypes.windll.gdi32.GetDIBits(self.mem, self.bmp, 0, self.h, self.buf, ctypes.byref(self.bi), 0)
        return self.buf.raw

    def switch_tab(self, tab_num):
        self.force_foreground()
        time.sleep(0.05)
        vk_num = 0x30 + tab_num
        ctypes.windll.user32.keybd_event(0x11, 0, 0, 0) # Ctrl down
        ctypes.windll.user32.keybd_event(vk_num, 0, 0, 0)
        time.sleep(0.05)
        ctypes.windll.user32.keybd_event(vk_num, 0, 2, 0)
        ctypes.windll.user32.keybd_event(0x11, 0, 2, 0) # Ctrl up
        time.sleep(0.3)

    def navigate_url(self, url):
        self.force_foreground()
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
        time.sleep(1.0)

    def paste_text(self, text):
        self.force_foreground()
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text)
        win32clipboard.CloseClipboard()

        ctypes.windll.user32.keybd_event(0x11, 0, 0, 0) # Ctrl+V
        ctypes.windll.user32.keybd_event(0x56, 0, 0, 0)
        time.sleep(0.05)
        ctypes.windll.user32.keybd_event(0x56, 0, 2, 0)
        ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
        time.sleep(0.1)

    def select_all(self):
        self.force_foreground()
        ctypes.windll.user32.keybd_event(0x11, 0, 0, 0) # Ctrl+A
        ctypes.windll.user32.keybd_event(0x41, 0, 0, 0)
        time.sleep(0.05)
        ctypes.windll.user32.keybd_event(0x41, 0, 2, 0)
        ctypes.windll.user32.keybd_event(0x11, 0, 2, 0)
        time.sleep(0.1)

    def press_enter(self):
        self.force_foreground()
        ctypes.windll.user32.keybd_event(0x0D, 0, 0, 0)
        time.sleep(0.05)
        ctypes.windll.user32.keybd_event(0x0D, 0, 2, 0)
        time.sleep(0.1)

    def scroll_down(self):
        self.force_foreground()
        ctypes.windll.user32.keybd_event(0x22, 0, 0, 0) # VK_NEXT
        ctypes.windll.user32.keybd_event(0x22, 0, 2, 0)

    def scroll_up(self):
        self.force_foreground()
        ctypes.windll.user32.keybd_event(0x21, 0, 0, 0) # VK_PRIOR
        ctypes.windll.user32.keybd_event(0x21, 0, 2, 0)

    def scroll_home(self):
        self.force_foreground()
        # Also send ESC to close any open modal
        ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x1B, 0, 2, 0)
        time.sleep(0.1)
        ctypes.windll.user32.keybd_event(0x24, 0, 0, 0) # VK_HOME
        ctypes.windll.user32.keybd_event(0x24, 0, 2, 0)

    def scroll_down_arrow(self):
        self.force_foreground()
        ctypes.windll.user32.keybd_event(0x28, 0, 0, 0) # VK_DOWN
        ctypes.windll.user32.keybd_event(0x28, 0, 2, 0)

    def click_at(self, x, y):
        self.force_foreground()
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        screen_x = rect.left + x
        screen_y = rect.top + y
        ctypes.windll.user32.SetCursorPos(screen_x, screen_y)
        ctypes.windll.user32.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.05)
        ctypes.windll.user32.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def record_scene(self, duration, actions, output_mp4):
        total_frames = int(duration * self.fps)
        cmd = [
            'ffmpeg', '-y',
            '-f', 'rawvideo',
            '-vcodec', 'rawvideo',
            '-s', f'{self.w}x{self.h}',
            '-pix_fmt', 'bgra',
            '-r', str(self.fps),
            '-i', '-',
            '-c:v', 'libx264',
            '-pix_fmt', 'yuv420p',
            '-preset', 'veryfast',
            '-b:v', '4500k',
            output_mp4
        ]

        pipe = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        start_time = time.time()

        action_idx = 0
        sorted_actions = sorted(actions, key=lambda a: a[0])

        for frame_idx in range(total_frames):
            frame_t = frame_idx / float(self.fps)

            while action_idx < len(sorted_actions) and sorted_actions[action_idx][0] <= frame_t:
                _, func, args = sorted_actions[action_idx]
                try:
                    func(*args)
                except Exception as e:
                    print(f"Action error at t={frame_t:.2f}s: {e}")
                action_idx += 1

            raw_bytes = self.grab_frame()
            pipe.stdin.write(raw_bytes)

            elapsed = time.time() - start_time
            target = (frame_idx + 1) * self.frame_delay
            sleep_time = target - elapsed
            if sleep_time > 0.001:
                time.sleep(sleep_time)

        pipe.stdin.close()
        pipe.wait()
        print(f"Recorded {total_frames} frames ({duration:.2f}s) to {output_mp4}")

    def close(self):
        ctypes.windll.gdi32.DeleteObject(self.bmp)
        ctypes.windll.gdi32.DeleteDC(self.mem)
        ctypes.windll.user32.ReleaseDC(self.hwnd, self.hdc)
