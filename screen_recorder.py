#!/usr/bin/env python3
"""
🎥 Screen Recorder with Audio
- Records screen region or full screen
- Captures microphone + system audio (loopback)
- Exports 720p MP4 with audio
- Rickrolls on launch 🎵
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import threading
import time
import os
import sys
import datetime
import wave
import subprocess
import webbrowser
import tempfile
import shutil
import urllib.request
import zipfile
import io

# ─── Auto-install dependencies ───────────────────────────────────────────────
def _pip(*pkgs):
    subprocess.check_call(
        [sys.executable, '-m', 'pip', 'install', *pkgs, '-q'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def _ensure(import_name, pip_name=None):
    try:
        __import__(import_name)
        return True
    except ImportError:
        try:
            _pip(pip_name or import_name)
            __import__(import_name)
            return True
        except Exception as e:
            print(f'[WARN] Could not install {pip_name or import_name}: {e}')
            return False

_ensure('mss', 'mss')
_ensure('cv2', 'opencv-python')
_ensure('numpy', 'numpy')
_ensure('pyaudio', 'pyaudio')
_HAS_SOUNDCARD = _ensure('soundcard', 'soundcard')

import mss
import cv2
import numpy as np
import pyaudio

if _HAS_SOUNDCARD:
    try:
        import soundcard as sc
    except Exception:
        _HAS_SOUNDCARD = False

# ─── Config ──────────────────────────────────────────────────────────────────
AUDIO_RATE    = 44100
AUDIO_CHUNK   = 1024
AUDIO_CH      = 2
VIDEO_FPS     = 20


# ─── Region Selector ─────────────────────────────────────────────────────────
class RegionSelector:
    """Fullscreen semi-transparent overlay for drag-to-select."""

    def __init__(self, master, callback):
        self._callback = callback
        self._done = False

        self._win = tk.Toplevel(master)
        self._win.attributes('-fullscreen', True)
        self._win.attributes('-topmost', True)
        self._win.attributes('-alpha', 0.25)
        self._win.configure(bg='black')
        self._win.title('')

        self._cv = tk.Canvas(
            self._win, cursor='cross',
            bg='black', highlightthickness=0
        )
        self._cv.pack(fill='both', expand=True)

        self._hint = tk.Label(
            self._win,
            text='🎯  Drag to select region  |  ESC = cancel',
            bg='#0d0d0d', fg='#00ff99',
            font=('Consolas', 13, 'bold'),
            padx=18, pady=7
        )
        self._hint.place(relx=0.5, y=28, anchor='n')

        self._sx = self._sy = 0
        self._rect = None

        self._cv.bind('<ButtonPress-1>',   self._press)
        self._cv.bind('<B1-Motion>',       self._drag)
        self._cv.bind('<ButtonRelease-1>', self._release)
        self._win.bind('<Escape>',         self._cancel)
        self._win.focus_force()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _clear_rect(self):
        if self._rect:
            self._cv.delete(self._rect)
            self._rect = None

    def _press(self, e):
        self._sx, self._sy = e.x, e.y
        self._clear_rect()

    def _drag(self, e):
        self._clear_rect()
        self._rect = self._cv.create_rectangle(
            self._sx, self._sy, e.x, e.y,
            outline='#00ff99', width=2, dash=(6, 3),
            fill=''
        )
        w = abs(e.x - self._sx)
        h = abs(e.y - self._sy)
        self._hint.config(text=f'📐  {w} × {h} px  |  ESC = cancel')

    def _release(self, e):
        if self._done:
            return
        self._done = True
        x1, y1 = min(self._sx, e.x), min(self._sy, e.y)
        x2, y2 = max(self._sx, e.x), max(self._sy, e.y)
        w, h = x2 - x1, y2 - y1
        self._win.destroy()
        if w > 20 and h > 20:
            self._callback(x1, y1, w, h)
        else:
            self._callback(None, None, None, None)

    def _cancel(self, _=None):
        if self._done:
            return
        self._done = True
        self._win.destroy()
        self._callback(None, None, None, None)


# ─── Audio Recorder ──────────────────────────────────────────────────────────
class AudioRecorder:
    def __init__(self):
        self._pa = pyaudio.PyAudio()
        self._mic_frames: list[bytes] = []
        self._sys_frames: list[bytes] = []
        self._recording = False
        self._threads: list[threading.Thread] = []
        self.has_system_audio = _HAS_SOUNDCARD

        # Verify mic is accessible
        self.has_mic = False
        try:
            info = self._pa.get_default_input_device_info()
            self.has_mic = (info is not None)
        except Exception:
            pass

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        self._mic_frames.clear()
        self._sys_frames.clear()
        self._recording = True
        self._threads.clear()

        if self.has_mic:
            t = threading.Thread(target=self._record_mic, daemon=True)
            t.start()
            self._threads.append(t)

        if self.has_system_audio:
            t = threading.Thread(target=self._record_system, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._recording = False
        # Don't join — threads are daemon, they'll stop on their own
        self._threads.clear()

    def cleanup(self):
        try:
            self._pa.terminate()
        except Exception:
            pass

    # ── capture loops ─────────────────────────────────────────────────────────

    def _record_mic(self):
        try:
            stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=AUDIO_CH,
                rate=AUDIO_RATE,
                input=True,
                frames_per_buffer=AUDIO_CHUNK,
            )
            while self._recording:
                data = stream.read(AUDIO_CHUNK, exception_on_overflow=False)
                self._mic_frames.append(data)
            stream.stop_stream()
            stream.close()
        except Exception as e:
            print(f'[MIC] {e}')

    def _record_system(self):
        try:
            speaker = sc.default_speaker()
            loopback = sc.get_microphone(speaker.id, include_loopback=True)
            with loopback.recorder(samplerate=AUDIO_RATE, channels=AUDIO_CH) as rec:
                while self._recording:
                    chunk = rec.record(numframes=AUDIO_CHUNK)
                    # float32 → int16
                    pcm = (np.clip(chunk, -1.0, 1.0) * 32767).astype(np.int16)
                    self._sys_frames.append(pcm.tobytes())
        except Exception as e:
            print(f'[SYS AUDIO] {e}')

    # ── export ────────────────────────────────────────────────────────────────

    def save_wav(self, path: str) -> bool:
        """Mix mic + system audio and write WAV. Returns True on success."""
        has_mic = bool(self._mic_frames)
        has_sys = bool(self._sys_frames)
        if not has_mic and not has_sys:
            return False

        try:
            if has_mic and has_sys:
                mic_np = np.frombuffer(b''.join(self._mic_frames), dtype=np.int16).astype(np.float32)
                sys_np = np.frombuffer(b''.join(self._sys_frames), dtype=np.int16).astype(np.float32)
                n = min(len(mic_np), len(sys_np))
                mixed = np.clip(mic_np[:n] * 0.55 + sys_np[:n] * 0.55, -32768, 32767).astype(np.int16)
                raw = mixed.tobytes()
            elif has_mic:
                raw = b''.join(self._mic_frames)
            else:
                raw = b''.join(self._sys_frames)

            with wave.open(path, 'wb') as wf:
                wf.setnchannels(AUDIO_CH)
                wf.setsampwidth(2)
                wf.setframerate(AUDIO_RATE)
                wf.writeframes(raw)
            return True
        except Exception as e:
            print(f'[AUDIO SAVE] {e}')
            return False


# ─── Video Recorder ──────────────────────────────────────────────────────────
class VideoRecorder:
    def __init__(self):
        self._frames: list[np.ndarray] = []
        self._recording = False
        self._thread: threading.Thread | None = None
        self.region: tuple[int, int, int, int] | None = None  # x, y, w, h
        self.fps = VIDEO_FPS

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    def set_region(self, x, y, w, h):
        self.region = (int(x), int(y), int(w), int(h))

    def clear_region(self):
        self.region = None

    def start(self):
        self._frames.clear()
        self._recording = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._recording = False
        # Don't join here — let thread finish on its own (daemon=True)

    def _loop(self):
        interval = 1.0 / self.fps
        with mss.mss() as sct:
            if self.region:
                x, y, w, h = self.region
                monitor = {'top': y, 'left': x, 'width': w, 'height': h}
            else:
                monitor = sct.monitors[1]

            while self._recording:
                t0 = time.perf_counter()
                img = sct.grab(monitor)
                frame = cv2.cvtColor(np.array(img), cv2.COLOR_BGRA2BGR)
                self._frames.append(frame)
                elapsed = time.perf_counter() - t0
                remaining = interval - elapsed
                if remaining > 0:
                    time.sleep(remaining)

    def save(self, path: str, p720: bool = True) -> bool:
        """Write frames to MP4. Returns True on success."""
        if not self._frames:
            return False
        try:
            h0, w0 = self._frames[0].shape[:2]
            if p720:
                scale = min(1280 / w0, 720 / h0, 1.0)
                ow = int(w0 * scale) & ~1
                oh = int(h0 * scale) & ~1
            else:
                ow, oh = w0 & ~1, h0 & ~1

            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(path, fourcc, self.fps, (ow, oh))

            for frame in self._frames:
                if ow != w0 or oh != h0:
                    frame = cv2.resize(frame, (ow, oh),
                                       interpolation=cv2.INTER_LANCZOS4)
                writer.write(frame)
            writer.release()
            return True
        except Exception as e:
            print(f'[VIDEO SAVE] {e}')
            return False


# ─── FFmpeg auto-installer ────────────────────────────────────────────────────
# Download URL: ffmpeg essentials build from gyan.dev (Windows 64-bit, ~80 MB)
_FFMPEG_URL = (
    'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
)

def _app_dir() -> str:
    """Directory that contains the running .exe (or .py during dev)."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _local_ffmpeg() -> str:
    """Path where we want ffmpeg.exe to live (next to the exe)."""
    return os.path.join(_app_dir(), 'ffmpeg.exe')


def _download_ffmpeg_with_ui():
    """
    Show a progress window, download ffmpeg zip, extract ffmpeg.exe
    to the same folder as the running exe, then close the window.
    Blocks until finished (runs in main thread via a nested mainloop).
    """
    dest = _local_ffmpeg()
    if os.path.isfile(dest):
        return  # already there

    # ── Progress window ───────────────────────────────────────────────────────
    win = tk.Tk()
    win.title('First-time setup')
    win.resizable(False, False)
    win.configure(bg='#0d0d0d')
    win.attributes('-topmost', True)

    tk.Label(win, text='⬇  Downloading ffmpeg…',
             bg='#0d0d0d', fg='#00ff99',
             font=('Consolas', 12, 'bold'),
             padx=30, pady=14).pack()

    info_var = tk.StringVar(value='Connecting…')
    tk.Label(win, textvariable=info_var,
             bg='#0d0d0d', fg='#888888',
             font=('Consolas', 9),
             padx=30).pack()

    bar = ttk.Progressbar(win, length=340, mode='determinate')
    bar.pack(padx=30, pady=(8, 4))

    pct_var = tk.StringVar(value='0 %')
    tk.Label(win, textvariable=pct_var,
             bg='#0d0d0d', fg='#555555',
             font=('Consolas', 8),
             pady=4).pack()

    win.eval('tk::PlaceWindow . center')
    win.update()

    error_holder: list[str] = []

    def _worker():
        try:
            # ── Download ──────────────────────────────────────────────────────
            req = urllib.request.urlopen(_FFMPEG_URL, timeout=60)
            total = int(req.headers.get('Content-Length', 0))
            downloaded = 0
            chunks: list[bytes] = []

            while True:
                chunk = req.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    mb_done = downloaded / 1_048_576
                    mb_total = total / 1_048_576
                    win.after(0, lambda p=pct, d=mb_done, t=mb_total: (
                        bar.configure(value=p),
                        pct_var.set(f'{p:.0f} %  ({d:.1f} / {t:.1f} MB)'),
                    ))
                else:
                    mb_done = downloaded / 1_048_576
                    win.after(0, lambda d=mb_done: (
                        info_var.set(f'Downloaded {d:.1f} MB…'),
                    ))

            raw = b''.join(chunks)

            # ── Extract ffmpeg.exe from zip ───────────────────────────────────
            win.after(0, lambda: info_var.set('Extracting ffmpeg.exe…'))
            win.after(0, lambda: bar.configure(mode='indeterminate'))
            win.after(0, lambda: bar.start(12))

            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                # Find ffmpeg.exe inside the nested bin/ folder
                target = next(
                    n for n in z.namelist()
                    if n.endswith('/ffmpeg.exe') or n == 'ffmpeg.exe'
                )
                with z.open(target) as src, open(dest, 'wb') as dst:
                    shutil.copyfileobj(src, dst)

            win.after(0, win.destroy)

        except Exception as exc:
            error_holder.append(str(exc))
            win.after(0, win.destroy)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    win.mainloop()  # blocks until win.destroy()

    if error_holder:
        # Show error using a simple Tk window (main Tk not created yet)
        err_win = tk.Tk()
        err_win.withdraw()
        messagebox.showerror(
            'FFmpeg download failed',
            f'Could not download ffmpeg automatically:\n\n{error_holder[0]}\n\n'
            'Please install ffmpeg manually and add it to PATH.',
            parent=err_win
        )
        err_win.destroy()


def _ensure_ffmpeg():
    """Download ffmpeg if not already present. Called once at startup."""
    if os.path.isfile(_local_ffmpeg()):
        return
    if shutil.which('ffmpeg'):
        return   # already on PATH, no need to download
    # Not found anywhere → download
    _download_ffmpeg_with_ui()


# ─── Utility: merge video + audio via ffmpeg ─────────────────────────────────
def _find_ffmpeg() -> str | None:
    # 1) Next to the exe / script (auto-downloaded or manually placed)
    local = _local_ffmpeg()
    if os.path.isfile(local):
        return local

    # 2) Bundled inside PyInstaller .exe (--add-binary ffmpeg.exe;.)
    if getattr(sys, 'frozen', False):
        bundled = os.path.join(sys._MEIPASS, 'ffmpeg.exe')
        if os.path.isfile(bundled):
            return bundled

    # 3) System PATH
    found = shutil.which('ffmpeg')
    if found:
        return found

    # 4) Common install locations
    candidates = [
        r'C:\ffmpeg\bin\ffmpeg.exe',
        r'C:\Program Files\ffmpeg\bin\ffmpeg.exe',
        r'C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe',
        '/usr/bin/ffmpeg',
        '/usr/local/bin/ffmpeg',
        '/opt/homebrew/bin/ffmpeg',
    ]
    return next((p for p in candidates if os.path.isfile(p)), None)


def merge_video_audio(video: str, audio: str, out: str) -> bool:
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        return False
    try:
        r = subprocess.run(
            [ffmpeg, '-y',
             '-i', video,
             '-i', audio,
             '-c:v', 'copy',
             '-c:a', 'aac',
             '-b:a', '192k',
             '-shortest',
             out],
            capture_output=True,
            timeout=300
        )
        return r.returncode == 0
    except Exception as e:
        print(f'[FFMPEG] {e}')
        return False


# ─── GUI ─────────────────────────────────────────────────────────────────────
class App(tk.Tk):
    # Palette
    BG     = '#0d0d0d'
    PANEL  = '#141414'
    CARD   = '#1a1a1a'
    BORDER = '#252525'
    ACCENT = '#00ff99'
    DANGER = '#ff3355'
    BLUE   = '#4488ff'
    TEXT   = '#e0e0e0'
    MUTED  = '#555555'
    LITE   = '#888888'

    def __init__(self):
        super().__init__()

        self.vid = VideoRecorder()
        self.aud = AudioRecorder()
        self._elapsed   = 0
        self._timer_id  = None
        self._region_sv = tk.StringVar(value='Full Screen')

        self.title('Screen Recorder')
        self.resizable(False, False)
        self.configure(bg=self.BG)

        self._build()
        self.eval('tk::PlaceWindow . center')
        self.protocol('WM_DELETE_WINDOW', self._quit)

    # ── build UI ──────────────────────────────────────────────────────────────

    def _build(self):
        # ── Header
        hdr = tk.Frame(self, bg=self.BG)
        hdr.pack(fill='x', padx=22, pady=(18, 10))

        self._rec_dot = tk.Label(hdr, text='●', bg=self.BG,
                                 fg=self.MUTED, font=('Consolas', 11))
        self._rec_dot.pack(side='left')

        tk.Label(hdr, text='  SCREEN RECORDER', bg=self.BG,
                 fg=self.TEXT, font=('Consolas', 17, 'bold')).pack(side='left')

        # ── Status card
        sc_frame = tk.Frame(self, bg=self.PANEL,
                            highlightbackground=self.BORDER,
                            highlightthickness=1)
        sc_frame.pack(fill='x', padx=22, pady=(0, 10))
        sc_inner = tk.Frame(sc_frame, bg=self.PANEL, padx=16, pady=10)
        sc_inner.pack(fill='x')

        self._status_lbl = tk.Label(
            sc_inner, text='READY',
            bg=self.PANEL, fg=self.MUTED,
            font=('Consolas', 11, 'bold')
        )
        self._status_lbl.pack(side='left')

        self._timer_lbl = tk.Label(
            sc_inner, text='00:00:00',
            bg=self.PANEL, fg=self.MUTED,
            font=('Consolas', 16, 'bold')
        )
        self._timer_lbl.pack(side='right')

        # ── Region selector row
        rr = tk.Frame(self, bg=self.BG)
        rr.pack(fill='x', padx=22, pady=(0, 4))

        tk.Label(rr, text='REGION:', bg=self.BG,
                 fg=self.MUTED, font=('Consolas', 8)).pack(side='left')

        tk.Label(rr, textvariable=self._region_sv,
                 bg=self.BG, fg=self.ACCENT,
                 font=('Consolas', 8, 'bold')).pack(side='left', padx=(5, 0))

        self._btn_sel = self._mini_btn(rr, '✎ Select Region',
                                       self.ACCENT, self._select_region)
        self._btn_sel.pack(side='right', padx=(4, 0))

        self._btn_rst = self._mini_btn(rr, '↺ Full Screen',
                                       self.LITE, self._reset_region)
        self._btn_rst.pack(side='right')

        # ── Audio info row
        ar = tk.Frame(self, bg=self.BG)
        ar.pack(fill='x', padx=22, pady=(2, 6))

        mic_c = self.ACCENT if self.aud.has_mic else self.DANGER
        mic_t = '🎤 Mic  ON' if self.aud.has_mic else '🎤 Mic  OFF'
        tk.Label(ar, text=mic_t, bg=self.BG, fg=mic_c,
                 font=('Consolas', 8)).pack(side='left', padx=(0, 14))

        sys_c = self.ACCENT if self.aud.has_system_audio else self.LITE
        sys_t = '🔊 System  ON' if self.aud.has_system_audio else '🔊 System  OFF'
        tk.Label(ar, text=sys_t, bg=self.BG, fg=sys_c,
                 font=('Consolas', 8)).pack(side='left')

        if not _find_ffmpeg():
            tk.Label(ar, text='  ⚠ ffmpeg not found (audio merge disabled)',
                     bg=self.BG, fg='#ff8800',
                     font=('Consolas', 7)).pack(side='right')

        # ── Divider
        tk.Frame(self, bg=self.BORDER, height=1).pack(fill='x', padx=22, pady=6)

        # ── Main buttons
        br = tk.Frame(self, bg=self.BG)
        br.pack(padx=22, pady=(4, 16))

        self._btn_start = self._big_btn(br, '▶  START', self.ACCENT, '#000',
                                        self._start)
        self._btn_start.grid(row=0, column=0, padx=(0, 8))

        self._btn_stop = self._big_btn(br, '■  STOP', self.DANGER, '#fff',
                                       self._stop, disabled=True)
        self._btn_stop.grid(row=0, column=1, padx=(0, 8))

        self._btn_save = self._big_btn(br, '💾  SAVE', self.BLUE, '#fff',
                                       self._save, disabled=True)
        self._btn_save.grid(row=0, column=2)

        # ── Footer
        ft = tk.Frame(self, bg=self.BG)
        ft.pack(fill='x', padx=22, pady=(0, 14))
        tk.Label(ft, text='720p  •  MP4  •  20 FPS',
                 bg=self.BG, fg=self.MUTED,
                 font=('Consolas', 7)).pack(side='left')
        tk.Label(ft, text='ESC cancels region select',
                 bg=self.BG, fg=self.MUTED,
                 font=('Consolas', 7)).pack(side='right')

    # ── widget factories ──────────────────────────────────────────────────────

    def _mini_btn(self, parent, text, fg, cmd):
        return tk.Button(
            parent, text=text, command=cmd,
            bg=self.CARD, fg=fg,
            activebackground=self.BORDER,
            activeforeground=fg,
            font=('Consolas', 8),
            relief='flat', bd=0,
            padx=8, pady=3,
            cursor='hand2'
        )

    def _big_btn(self, parent, text, bg, fg, cmd, disabled=False):
        st = 'disabled' if disabled else 'normal'
        return tk.Button(
            parent, text=text, command=cmd,
            bg=bg if not disabled else '#222',
            fg=fg if not disabled else self.MUTED,
            activebackground=bg, activeforeground=fg,
            disabledforeground=self.MUTED,
            font=('Consolas', 11, 'bold'),
            relief='flat', bd=0,
            padx=18, pady=10,
            cursor='hand2',
            state=st
        )

    # ── actions ───────────────────────────────────────────────────────────────

    def _select_region(self):
        if self.vid._recording:
            return
        self.withdraw()
        # Short delay so the main window fully disappears
        self.after(180, lambda: RegionSelector(self, self._on_region_done))

    def _on_region_done(self, x, y, w, h):
        self.deiconify()
        self.lift()
        self.focus_force()
        if x is not None:
            self.vid.set_region(x, y, w, h)
            self._region_sv.set(f'({x},{y})  {w}×{h}')
        # If cancelled → leave existing region unchanged

    def _reset_region(self):
        self.vid.clear_region()
        self._region_sv.set('Full Screen')

    def _start(self):
        self.vid.start()
        self.aud.start()
        self._elapsed = 0
        self._set_ui('recording')
        self._tick()

    def _stop(self):
        # Signal threads to stop (non-blocking)
        self.vid.stop()
        self.aud.stop()
        if self._timer_id:
            self.after_cancel(self._timer_id)
            self._timer_id = None
        # Update UI immediately — disable STOP, show "stopping…"
        self._rec_dot.config(fg=self.MUTED)
        self._status_lbl.config(text='STOPPING…', fg=self.LITE)
        self._btn_stop.config(state='disabled', bg='#222', fg=self.MUTED)
        # Poll until capture thread finishes, then unlock SAVE
        self._wait_for_stop()

    def _wait_for_stop(self):
        """Poll every 100ms until capture thread is done, then update UI."""
        thread = self.vid._thread
        if thread and thread.is_alive():
            self.after(100, self._wait_for_stop)
            return
        # Thread finished — safe to read frame count
        n = self.vid.frame_count
        self._status_lbl.config(text=f'STOPPED  ({n} frames)', fg=self.LITE)
        self._timer_lbl.config(fg=self.LITE)
        self._btn_start.config(state='normal', bg=self.ACCENT, fg='#000')
        self._btn_save.config(state='normal', bg=self.BLUE, fg='#fff')

    def _save(self):
        if not self.vid.frame_count:
            messagebox.showwarning('Nothing to save', 'No frames were recorded.')
            return

        save_dir = filedialog.askdirectory(title='Choose folder to save recording')
        if not save_dir:
            return

        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        out_path = os.path.join(save_dir, f'recording_{ts}.mp4')

        self._status_lbl.config(text='SAVING…', fg=self.ACCENT)
        self._btn_save.config(state='disabled', bg='#222', fg=self.MUTED)
        self.update_idletasks()

        threading.Thread(
            target=self._do_save,
            args=(out_path,),
            daemon=True
        ).start()

    def _do_save(self, final_path: str):
        tmp = tempfile.mkdtemp(prefix='screenrec_')
        try:
            v_tmp = os.path.join(tmp, 'video.mp4')
            a_tmp = os.path.join(tmp, 'audio.wav')

            # Save raw video
            ok_v = self.vid.save(v_tmp, p720=True)
            if not ok_v:
                self.after(0, lambda: self._save_done(False, ''))
                return

            # Save audio
            ok_a = self.aud.save_wav(a_tmp)

            if ok_a:
                # Try to merge with ffmpeg
                merged = merge_video_audio(v_tmp, a_tmp, final_path)
                if not merged:
                    # ffmpeg not found or failed → keep silent video
                    shutil.copy2(v_tmp, final_path)
            else:
                shutil.copy2(v_tmp, final_path)

            self.after(0, lambda: self._save_done(True, final_path))

        except Exception as e:
            print(f'[SAVE] {e}')
            self.after(0, lambda: self._save_done(False, ''))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _save_done(self, ok: bool, path: str):
        if ok:
            self._status_lbl.config(text='SAVED ✓', fg=self.ACCENT)
            self._toast('✅  Recording saved!',
                        f'{os.path.basename(path)}\n{path}')
        else:
            self._status_lbl.config(text='ERROR', fg=self.DANGER)
            messagebox.showerror('Error', 'Failed to save the recording.')

    # ── state helpers ─────────────────────────────────────────────────────────

    def _set_ui(self, state: str):
        if state == 'recording':
            self._rec_dot.config(fg=self.DANGER)
            self._status_lbl.config(text='● RECORDING', fg=self.DANGER)
            self._timer_lbl.config(fg=self.ACCENT)
            self._btn_start.config(state='disabled', bg='#222', fg=self.MUTED)
            self._btn_stop.config(state='normal', bg=self.DANGER, fg='#fff')
            self._btn_save.config(state='disabled', bg='#222', fg=self.MUTED)

    def _tick(self):
        self._elapsed += 1
        h =  self._elapsed // 3600
        m = (self._elapsed % 3600) // 60
        s =  self._elapsed % 60
        self._timer_lbl.config(text=f'{h:02d}:{m:02d}:{s:02d}')
        self._timer_id = self.after(1000, self._tick)

    # ── toast popup ──────────────────────────────────────────────────────────

    def _toast(self, title: str, body: str):
        w = tk.Toplevel(self)
        w.title('Done')
        w.configure(bg=self.PANEL)
        w.resizable(False, False)
        w.attributes('-topmost', True)

        tk.Label(w, text=title, bg=self.PANEL, fg=self.ACCENT,
                 font=('Consolas', 13, 'bold'),
                 pady=14, padx=24).pack()
        tk.Label(w, text=body, bg=self.PANEL, fg=self.TEXT,
                 font=('Consolas', 9),
                 pady=2, padx=24, justify='left').pack()
        tk.Button(w, text='OK', command=w.destroy,
                  bg=self.ACCENT, fg='#000',
                  font=('Consolas', 11, 'bold'),
                  relief='flat', padx=28, pady=7,
                  cursor='hand2').pack(pady=14)
        w.grab_set()
        w.eval('tk::PlaceWindow . center')

    # ── close ─────────────────────────────────────────────────────────────────

    def _quit(self):
        if self.vid._recording:
            self.vid.stop()
            self.aud.stop()
        self.aud.cleanup()
        self.destroy()


# ─── Entry point ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    _ensure_ffmpeg()   # download ffmpeg if not present (first run only)
    app = App()
    app.mainloop()
