import tkinter as tk
from tkinter import ttk, messagebox
import tkinter.font as tkFont
import sys
import threading
import json
from pathlib import Path
import winsound

import sv_ttk
import pywinstyles

# Optional native Windows toast notifications
try:
    from win10toast import ToastNotifier
    _toaster = ToastNotifier()
except Exception:
    _toaster = None

APP_DIR = Path.home() / ".workrest_timer"
APP_DIR.mkdir(exist_ok=True)
CONFIG_PATH = APP_DIR / "config.json"

DEFAULT_CONFIG = {
    "mode": "eye",
    "eye": {"work_min": 20, "break_sec": 20},
    "hand": {"work_min": 50, "break_min": 3},
    "toast": True,
    "sound": True
}

def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                merged = DEFAULT_CONFIG.copy()
                for k, v in cfg.items():
                    if isinstance(v, dict) and k in merged:
                        merged[k] = {**merged[k], **v} if isinstance(merged[k], dict) else v
                    else:
                        merged[k] = v
                return merged
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

CONFIG = load_config()

# ----------------- Notifications & Sound -----------------

def notify(title: str, msg: str):
    if not CONFIG.get("toast", True):
        return
    if _toaster:
        _toaster.show_toast(title, msg, duration=5, threaded=True)
    else:
        threading.Thread(target=lambda: messagebox.showinfo(title, msg), daemon=True).start()

def play_beep():
    if CONFIG.get("sound"):
        try:
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass

# ----------------- Titlebar -----------------

def apply_titlebar_theme(win):
    try:
        version = sys.getwindowsversion()
        if version.major == 10 and version.build >= 22000:
            pywinstyles.change_header_color(win, "#1c1c1c")
        elif version.major == 10:
            pywinstyles.apply_style(win, "dark")
            win.wm_attributes("-alpha", 0.99)
            win.wm_attributes("-alpha", 1)
    except Exception:
        pass

# ----------------- Timer State -----------------

current_after = None        # id returned by root.after
phase = "idle"              # "idle" | "work" | "eye_break" | "hand_break" | "paused"
remaining = 0               # seconds left in current phase
phase_total = 0             # total seconds for current phase (for progress)
combined_eye_due = 0        # seconds until next eye micro-break during combined work

# ----------------- Core Countdown -----------------

def schedule_tick():
    global current_after
    current_after = root.after(1000, tick)


def start_countdown(seconds, label_fmt, next_callback, *, progress_on=True):
    global remaining, phase_total
    remaining = int(seconds)
    phase_total = int(seconds)
    status_label.config(text=label_fmt.format(format_time(remaining)))
    if progress_on:
        progress["maximum"] = phase_total
        progress["value"] = 0
    schedule_tick()


def tick():
    global remaining, current_after, phase, combined_eye_due
    if phase == "paused":
        return  # do nothing while paused

    if remaining > 0:
        remaining -= 1
        # update UI
        status_label.config(text=current_label_text())
        progress["value"] = phase_total - remaining

        # Combined mode: inject eye micro-breaks during hand work
        if phase == "work" and CONFIG.get("mode") == "combined":
            if combined_eye_due > 0:
                combined_eye_due -= 1
            if combined_eye_due == 0 and remaining > 0:
                # trigger an eye break inside work
                start_eye_break_inline()
                return  # don't schedule next tick now; eye break handles it

        schedule_tick()
    else:
        # phase finished
        next_phase()


def cancel_tick():
    global current_after
    if current_after is not None:
        try:
            root.after_cancel(current_after)
        except Exception:
            pass
        current_after = None


def format_time(sec):
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"

# ----------------- Phase Management -----------------

def current_label_text():
    if phase == "work":
        mode = CONFIG.get("mode", "eye")
        return ("Eye work: {}" if mode == "eye" else ("Hand work: {}" if mode == "hand" else "Combined work: {}"))\
            .format(format_time(remaining))
    elif phase == "eye_break":
        return f"Eye break: {format_time(remaining)}"
    elif phase == "hand_break":
        return f"Hand break: {format_time(remaining)}"
    else:
        return "Ready to start timer"


def next_phase():
    # Decide what comes after the current phase
    global phase
    mode = CONFIG.get("mode", "eye")

    if phase == "work":
        play_beep(); notify("Work finished", "Break started")
        if mode == "eye":
            begin_eye_break(CONFIG["eye"]["break_sec"])
        elif mode == "hand":
            begin_hand_break(CONFIG["hand"]["break_min"] * 60)
        else:  # combined → hand break at end of the big work block
            begin_hand_break(CONFIG["hand"]["break_min"] * 60)

    elif phase in ("eye_break", "hand_break"):
        play_beep(); notify("Break finished", "You’re ready to start again!")
        finish_cycle()


def finish_cycle():
    global phase
    phase = "idle"
    cancel_tick()
    button.config(state=tk.NORMAL)
    pause_btn.config(state=tk.DISABLED)
    stop_btn.config(state=tk.DISABLED)
    progress_label.config(text="")
    status_label.config(text="Ready to start timer")
    progress["value"] = 0

# ----- Begin phases -----

def begin_work():
    """Start a work phase based on selected mode."""
    global phase, combined_eye_due
    mode = CONFIG.get("mode", "eye")
    button.config(state=tk.DISABLED)
    pause_btn.config(state=tk.NORMAL)
    stop_btn.config(state=tk.NORMAL)

    progress_label.config(text="Working…")

    if mode == "eye":
        phase = "work"
        start_countdown(CONFIG["eye"]["work_min"] * 60, "Eye work: {}", next_phase)

    elif mode == "hand":
        phase = "work"
        start_countdown(CONFIG["hand"]["work_min"] * 60, "Hand work: {}", next_phase)

    else:  # combined
        phase = "work"
        combined_eye_due = CONFIG["eye"]["work_min"] * 60  # micro-break cadence
        start_countdown(CONFIG["hand"]["work_min"] * 60, "Combined work: {}", next_phase)


def start_eye_break_inline():
    """Pause the combined work and run a short eye break, then resume work."""
    global phase, remaining, phase_total, combined_eye_due
    cancel_tick()
    paused_remaining = remaining
    phase = "eye_break"
    progress_label.config(text="Eye break…")
    eye_len = CONFIG["eye"]["break_sec"]
    remaining = eye_len
    phase_total = eye_len
    progress["maximum"] = eye_len
    progress["value"] = 0

    def back_to_work():
        # restore work phase after micro-break
        global phase, remaining, phase_total, combined_eye_due
        phase = "work"
        remaining = paused_remaining
        phase_total = max(phase_total, paused_remaining)  # keep previous total
        progress_label.config(text="Working…")
        combined_eye_due = CONFIG["eye"]["work_min"] * 60  # schedule next eye break
        schedule_tick()

    # temporarily override next_phase for this micro-break
    def micro_next():
        play_beep(); notify("Eye break", "Back to work")
        back_to_work()

    # run the eye break countdown
    status_label.config(text=current_label_text())
    schedule_tick()

    # Replace next_phase behavior only for this inline break
    global next_phase_backup
    next_phase_backup = next_phase
    def tmp_next_phase():
        # restore handler then call back_to_work
        global next_phase
        next_phase = next_phase_backup
        micro_next()
    # Monkey-patch next_phase to our temporary handler just for this micro-break
    globals()['next_phase'] = tmp_next_phase


def begin_eye_break(seconds):
    global phase
    phase = "eye_break"
    progress_label.config(text="On break…")
    start_countdown(seconds, "Eye break: {}", next_phase)


def begin_hand_break(seconds):
    global phase
    phase = "hand_break"
    progress_label.config(text="On break…")
    start_countdown(seconds, "Hand break: {}", next_phase)

# ----------------- Controls: Start / Pause / Resume / Stop -----------------

def on_start():
    if phase in ("idle",):
        begin_work()


def on_pause_resume():
    global phase
    if phase == "paused":
        # resume
        phase = prev_phase_var.get() or "work"
        pause_btn.config(text="Pause")
        schedule_tick()
    elif phase in ("work", "eye_break", "hand_break"):
        # pause
        prev_phase_var.set(phase)
        phase = "paused"
        cancel_tick()
        pause_btn.config(text="Resume")


def on_stop():
    cancel_tick()
    finish_cycle()

# ----------------- UI Setup -----------------

root = tk.Tk()
root.geometry("460x330")
root.title("Rest Timer")
root.resizable(False, False)
root['padx'] = 20
root['pady'] = 20

# Force dark theme
sv_ttk.set_theme("dark")
apply_titlebar_theme(root)

# ---------- Gear Button (top-right) ----------

def open_settings():
    win = tk.Toplevel(root)
    win.title("Settings")
    win.resizable(False, False)
    sv_ttk.set_theme("dark")
    apply_titlebar_theme(win)

    pad = {"padx":12, "pady":8}

    # Eye config
    eye_fr = ttk.LabelFrame(win, text="Eye Rest (20-20-20)")
    eye_fr.grid(row=0, column=0, sticky="ew", **pad)
    eye_work = tk.IntVar(value=CONFIG["eye"]["work_min"])
    eye_break = tk.IntVar(value=CONFIG["eye"]["break_sec"])
    ttk.Label(eye_fr, text="Work (min):").grid(row=0, column=0, sticky="w")
    ttk.Spinbox(eye_fr, from_=5, to=180, textvariable=eye_work, width=6).grid(row=0, column=1, sticky="w")
    ttk.Label(eye_fr, text="Break (sec):").grid(row=1, column=0, sticky="w")
    ttk.Spinbox(eye_fr, from_=5, to=300, textvariable=eye_break, width=6).grid(row=1, column=1, sticky="w")

    # Hand config
    hand_fr = ttk.LabelFrame(win, text="Hand Rest")
    hand_fr.grid(row=1, column=0, sticky="ew", **pad)
    hand_work = tk.IntVar(value=CONFIG["hand"]["work_min"])
    hand_break = tk.IntVar(value=CONFIG["hand"]["break_min"])
    ttk.Label(hand_fr, text="Work (min):").grid(row=0, column=0, sticky="w")
    ttk.Spinbox(hand_fr, from_=10, to=240, textvariable=hand_work, width=6).grid(row=0, column=1, sticky="w")
    ttk.Label(hand_fr, text="Break (min):").grid(row=1, column=0, sticky="w")
    ttk.Spinbox(hand_fr, from_=1, to=60, textvariable=hand_break, width=6).grid(row=1, column=1, sticky="w")

    # Options
    opt_fr = ttk.LabelFrame(win, text="Options")
    opt_fr.grid(row=2, column=0, sticky="ew", **pad)
    toast_var = tk.BooleanVar(value=CONFIG.get("toast", True))
    sound_var = tk.BooleanVar(value=CONFIG.get("sound", True))
    ttk.Checkbutton(opt_fr, text="Windows toast", variable=toast_var).grid(row=0, column=0, sticky="w")
    ttk.Checkbutton(opt_fr, text="Sound", variable=sound_var).grid(row=0, column=1, sticky="w")

    def save_and_close():
        CONFIG["eye"]["work_min"] = int(eye_work.get())
        CONFIG["eye"]["break_sec"] = int(eye_break.get())
        CONFIG["hand"]["work_min"] = int(hand_work.get())
        CONFIG["hand"]["break_min"] = int(hand_break.get())
        CONFIG["toast"] = bool(toast_var.get())
        CONFIG["sound"] = bool(sound_var.get())
        save_config(CONFIG)
        win.destroy()

    btns = ttk.Frame(win)
    btns.grid(row=3, column=0, sticky="e", **pad)
    ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right", padx=6)
    ttk.Button(btns, text="Save", style="Accent.TButton", command=save_and_close).pack(side="right")

# Gear button
gear_btn = ttk.Button(root, text="⚙️", width=3, command=open_settings)
gear_btn.place(relx=1.0, y=10, x=-10, anchor="ne")

# Status label
status_label = ttk.Label(root, text="Ready to start timer")
status_label.pack(pady=(40, 10))

# Mode radio buttons (Eye / Hand / Combined)
mode_var = tk.StringVar(value=CONFIG.get("mode", "eye"))
mode_frame = ttk.Frame(root)
mode_frame.pack(pady=(0, 6))
for text, val in (("Eye", "eye"), ("Hand", "hand"), ("Combined", "combined")):
    ttk.Radiobutton(mode_frame, text=text, variable=mode_var, value=val,
                    command=lambda: (CONFIG.update({"mode": mode_var.get()}), save_config(CONFIG)))\
        .pack(side="left", padx=6)

# Progress bar + label
progress_label = ttk.Label(root, text="", anchor="center")
progress_label.pack(fill="x", padx=4, pady=(2, 4))

progress = ttk.Progressbar(root, mode="determinate")
progress.pack(fill="x", padx=4, pady=(0, 10))

# Control buttons
controls = ttk.Frame(root)
controls.pack(pady=6)
button = ttk.Button(controls, text="Start", command=lambda: on_start(), style="Accent.TButton")
button.grid(row=0, column=0, padx=6)

prev_phase_var = tk.StringVar(value="")
pause_btn = ttk.Button(controls, text="Pause", command=on_pause_resume, state=tk.DISABLED)
pause_btn.grid(row=0, column=1, padx=6)

stop_btn = ttk.Button(controls, text="Stop", command=on_stop, state=tk.DISABLED)
stop_btn.grid(row=0, column=2, padx=6)

# Initialize
sv_ttk.set_theme("dark")
apply_titlebar_theme(root)

root.mainloop()
