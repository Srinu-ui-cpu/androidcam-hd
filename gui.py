"""
AndroidCam HD - Modern Desktop GUI Control Center
"""

import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from typing import List, Dict

import cv2
import numpy as np
from PIL import Image, ImageTk, ImageDraw
import qrcode
import pystray
from pystray import MenuItem as item

from server import (
    AppState,
    VideoProcessor,
    get_network_interfaces,
    get_base_path,
    check_virtual_camera_status,
    install_driver_auto,
    is_autostart_enabled,
    set_autostart
)


def make_tray_icon_image():
    """Generates an embedded crisp icon for the Windows system tray."""
    img = Image.new("RGBA", (64, 64), color=(0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, 60, 60], radius=14, fill=(2, 132, 199))
    d.ellipse([16, 16, 48, 48], fill=(15, 23, 42), outline=(56, 189, 248), width=3)
    d.ellipse([24, 24, 40, 40], fill=(56, 189, 248))
    d.ellipse([46, 14, 52, 20], fill=(239, 68, 68))
    return img


class AndroidCamGUI:
    def __init__(self, root: tk.Tk, state: AppState, processor: VideoProcessor, port: int = 8080, start_minimized: bool = False):
        self.root = root
        self.state = state
        self.processor = processor
        self.port = port
        self.run_in_background = True

        self.root.title("AndroidCam HD - Virtual Camera Receiver")
        self.root.geometry("1120x760")
        self.root.minsize(980, 680)
        self.root.configure(bg="#0b0f19")

        # Network Interfaces
        self.interfaces: List[Dict[str, str]] = get_network_interfaces()
        self.selected_ip_idx = 0
        self.custom_ip = ""

        # UI references
        self.qr_img_tk = None
        self.preview_img_tk = None
        self.tray_icon = None

        self._apply_dark_theme()
        self._build_ui()
        self._generate_qr_code()
        self._setup_tray()

        # Intercept window close button to minimize to tray instead of quitting
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close_clicked)

        # Start UI update loop
        self.root.after(30, self._update_ui_loop)

        # Check driver registration on startup
        self.root.after(800, self._check_startup_driver)

        if start_minimized:
            self.root.withdraw()

    def _apply_dark_theme(self):
        style = ttk.Style()
        style.theme_use("clam")

        bg_color = "#0b0f19"
        card_bg = "#131b2e"
        accent_blue = "#0284c7"
        text_color = "#f8fafc"

        style.configure(".", background=bg_color, foreground=text_color, font=("Segoe UI", 9))
        style.configure("TFrame", background=bg_color)
        style.configure("Card.TFrame", background=card_bg, relief="flat")
        style.configure("TLabel", background=bg_color, foreground=text_color)
        style.configure("Card.TLabel", background=card_bg, foreground=text_color)
        style.configure("Accent.TButton", background=accent_blue, foreground="#ffffff", font=("Segoe UI", 9, "bold"))
        style.map("Accent.TButton", background=[("active", "#0369a1")])
        style.configure("Card.TCheckbutton", background=card_bg, foreground=text_color, font=("Segoe UI", 9))

    def _build_ui(self):
        # 1. Top Header Bar
        header = tk.Frame(self.root, bg="#0f172a", height=50)
        header.pack(fill=tk.X, side=tk.TOP)

        title_label = tk.Label(
            header,
            text="AndroidCam HD",
            font=("Segoe UI", 13, "bold"),
            fg="#38bdf8",
            bg="#0f172a",
            padx=16,
            pady=10
        )
        title_label.pack(side=tk.LEFT)

        tag_label = tk.Label(
            header,
            text="1080P/4K VIRTUAL WEBCAM",
            font=("Segoe UI", 8, "bold"),
            fg="#ffffff",
            bg="#ef4444",
            padx=6,
            pady=2
        )
        tag_label.pack(side=tk.LEFT)

        btn_bg = tk.Button(
            header,
            text="🔽 Hide to Background Tray",
            font=("Segoe UI", 8, "bold"),
            bg="#1e293b",
            fg="#94a3b8",
            relief=tk.FLAT,
            padx=8,
            pady=3,
            cursor="hand2",
            command=self._minimize_to_tray
        )
        btn_bg.pack(side=tk.RIGHT, padx=14)

        self.status_header_label = tk.Label(
            header,
            text="● WAITING FOR TABLET",
            font=("Segoe UI", 9, "bold"),
            fg="#ef4444",
            bg="#0f172a",
            padx=10
        )
        self.status_header_label.pack(side=tk.RIGHT)

        # 2. Main Content Frame (Left: Video, Right: Controls & QR)
        content = tk.Frame(self.root, bg="#0b0f19")
        content.pack(fill=tk.BOTH, expand=True, padx=14, pady=12)

        # LEFT: Video Viewport
        left_frame = tk.Frame(content, bg="#020617", bd=1, relief=tk.SOLID)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        # Canvas for Video Frame
        self.canvas = tk.Canvas(left_frame, bg="#020617", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Overlay Stats Bar below canvas
        stats_bar = tk.Frame(left_frame, bg="#0f172a", height=32)
        stats_bar.pack(fill=tk.X, side=tk.BOTTOM)

        self.lbl_fps = tk.Label(stats_bar, text="FPS: 0.0", font=("Segoe UI", 9, "bold"), fg="#38bdf8", bg="#0f172a")
        self.lbl_fps.pack(side=tk.LEFT, padx=14, pady=6)

        self.lbl_res = tk.Label(stats_bar, text="Resolution: 1920x1080", font=("Segoe UI", 9), fg="#94a3b8", bg="#0f172a")
        self.lbl_res.pack(side=tk.LEFT, padx=10, pady=6)

        self.lbl_bitrate = tk.Label(stats_bar, text="Bitrate: 0 KB/s", font=("Segoe UI", 9), fg="#94a3b8", bg="#0f172a")
        self.lbl_bitrate.pack(side=tk.LEFT, padx=10, pady=6)

        # RIGHT: Controls & QR Code Panel (Scrollable if needed)
        right_panel = tk.Frame(content, bg="#0b0f19", width=360)
        right_panel.pack(side=tk.RIGHT, fill=tk.Y)
        right_panel.pack_propagate(False)

        # Card 1: Network & QR Code
        net_card = tk.LabelFrame(
            right_panel,
            text=" 📱 Connect Tablet (Tailscale / Wi-Fi) ",
            font=("Segoe UI", 9, "bold"),
            bg="#131b2e",
            fg="#38bdf8",
            bd=1,
            padx=10,
            pady=8
        )
        net_card.pack(fill=tk.X, pady=(0, 8))

        tk.Label(net_card, text="Select network address to connect:", font=("Segoe UI", 8), fg="#94a3b8", bg="#131b2e").pack(anchor=tk.W)

        # Interface Selector dropdown (Tailscale and Wi-Fi)
        if self.interfaces:
            options = [f"{item['type']}: {item['ip']}" for item in self.interfaces]
            self.ip_combo_var = tk.StringVar(value=options[0])
            self.ip_combo = ttk.Combobox(net_card, textvariable=self.ip_combo_var, values=options, state="readonly")
            self.ip_combo.pack(fill=tk.X, pady=4)
            self.ip_combo.bind("<<ComboboxSelected>>", self._on_ip_selected)
        else:
            self.ip_combo_var = tk.StringVar(value="127.0.0.1")

        # Custom IP entry row for entering any custom Tailscale or Hotspot IP
        custom_ip_frame = tk.Frame(net_card, bg="#131b2e")
        custom_ip_frame.pack(fill=tk.X, pady=2)

        self.custom_ip_entry = tk.Entry(
            custom_ip_frame,
            bg="#0f172a",
            fg="#38bdf8",
            insertbackground="#ffffff",
            relief=tk.FLAT,
            font=("Segoe UI", 9)
        )
        self.custom_ip_entry.insert(0, self.interfaces[0]["ip"] if self.interfaces else "127.0.0.1")
        self.custom_ip_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        btn_set_ip = tk.Button(
            custom_ip_frame,
            text="Set IP",
            font=("Segoe UI", 8, "bold"),
            bg="#0284c7",
            fg="#ffffff",
            relief=tk.FLAT,
            padx=6,
            command=self._on_custom_ip_set
        )
        btn_set_ip.pack(side=tk.RIGHT)

        # URL Badge
        self.url_label = tk.Label(
            net_card,
            text=f"http://{self.interfaces[0]['ip']}:{self.port}" if self.interfaces else f"http://127.0.0.1:{self.port}",
            font=("Segoe UI", 10, "bold"),
            fg="#67e8f9",
            bg="#1e293b",
            padx=6,
            pady=4,
            cursor="hand2"
        )
        self.url_label.pack(fill=tk.X, pady=4)
        self.url_label.bind("<Button-1>", lambda e: self._copy_url())

        # QR Code Display
        self.qr_label = tk.Label(net_card, bg="#131b2e")
        self.qr_label.pack(pady=4)

        # Card 2: Virtual Webcam Status & Actions
        vcam_card = tk.LabelFrame(
            right_panel,
            text=" 🎥 Windows Virtual Camera ",
            font=("Segoe UI", 9, "bold"),
            bg="#131b2e",
            fg="#38bdf8",
            bd=1,
            padx=10,
            pady=8
        )
        vcam_card.pack(fill=tk.X, pady=(0, 8))

        self.vcam_status_lbl = tk.Label(
            vcam_card,
            text="Status: Checking...",
            font=("Segoe UI", 8, "bold"),
            fg="#fbbf24",
            bg="#131b2e",
            wraplength=320,
            justify=tk.LEFT
        )
        self.vcam_status_lbl.pack(anchor=tk.W, pady=(0, 4))

        self.btn_install_driver = tk.Button(
            vcam_card,
            text="⚙️ Enable / Re-register Virtual Camera",
            font=("Segoe UI", 8, "bold"),
            bg="#2563eb",
            fg="#ffffff",
            activebackground="#1d4ed8",
            relief=tk.FLAT,
            padx=8,
            pady=4,
            cursor="hand2",
            command=self._install_driver_prompt
        )
        self.btn_install_driver.pack(fill=tk.X, pady=2)

        # Card 3: Windows Startup & Background Settings
        sys_card = tk.LabelFrame(
            right_panel,
            text=" ⚙️ Startup & Background Mode ",
            font=("Segoe UI", 9, "bold"),
            bg="#131b2e",
            fg="#38bdf8",
            bd=1,
            padx=10,
            pady=6
        )
        sys_card.pack(fill=tk.X, pady=(0, 8))

        self.var_autostart = tk.BooleanVar(value=is_autostart_enabled())
        chk_autostart = tk.Checkbutton(
            sys_card,
            text="Start with Windows Boot (Auto-start)",
            variable=self.var_autostart,
            command=self._on_toggle_autostart,
            bg="#131b2e",
            fg="#f8fafc",
            selectcolor="#0f172a",
            activebackground="#131b2e",
            activeforeground="#f8fafc"
        )
        chk_autostart.pack(anchor=tk.W, pady=1)

        self.var_bg_close = tk.BooleanVar(value=True)
        chk_bg = tk.Checkbutton(
            sys_card,
            text="Run in Background when Closed (Keep WebCam Live)",
            variable=self.var_bg_close,
            bg="#131b2e",
            fg="#f8fafc",
            selectcolor="#0f172a",
            activebackground="#131b2e",
            activeforeground="#f8fafc"
        )
        chk_bg.pack(anchor=tk.W, pady=1)

        # Card 4: Video Adjustments
        adj_card = tk.LabelFrame(
            right_panel,
            text=" 🔄 Video Adjustments ",
            font=("Segoe UI", 9, "bold"),
            bg="#131b2e",
            fg="#38bdf8",
            bd=1,
            padx=10,
            pady=6
        )
        adj_card.pack(fill=tk.X, pady=(0, 8))

        self.var_flip_h = tk.BooleanVar(value=self.state.flip_horizontal)
        chk_flip_h = tk.Checkbutton(
            adj_card,
            text="Mirror Video (Flip Horizontal)",
            variable=self.var_flip_h,
            command=self._on_transform_change,
            bg="#131b2e",
            fg="#f8fafc",
            selectcolor="#0f172a",
            activebackground="#131b2e",
            activeforeground="#f8fafc"
        )
        chk_flip_h.pack(anchor=tk.W, pady=1)

        self.var_flip_v = tk.BooleanVar(value=self.state.flip_vertical)
        chk_flip_v = tk.Checkbutton(
            adj_card,
            text="Flip Vertical",
            variable=self.var_flip_v,
            command=self._on_transform_change,
            bg="#131b2e",
            fg="#f8fafc",
            selectcolor="#0f172a",
            activebackground="#131b2e",
            activeforeground="#f8fafc"
        )
        chk_flip_v.pack(anchor=tk.W, pady=1)

        rot_frame = tk.Frame(adj_card, bg="#131b2e")
        rot_frame.pack(fill=tk.X, pady=2)

        tk.Label(rot_frame, text="Rotation:", font=("Segoe UI", 8), fg="#94a3b8", bg="#131b2e").pack(side=tk.LEFT)
        btn_rot = tk.Button(
            rot_frame,
            text="Rotate 90°",
            font=("Segoe UI", 8),
            bg="#334155",
            fg="#ffffff",
            relief=tk.FLAT,
            padx=8,
            command=self._rotate_90
        )
        btn_rot.pack(side=tk.LEFT, padx=8)

        # Card 5: Tools (Snapshot & Record)
        tools_card = tk.LabelFrame(
            right_panel,
            text=" 📸 Capture & Recording ",
            font=("Segoe UI", 9, "bold"),
            bg="#131b2e",
            fg="#38bdf8",
            bd=1,
            padx=10,
            pady=6
        )
        tools_card.pack(fill=tk.X)

        btn_snap = tk.Button(
            tools_card,
            text="📸 Save 1080p Snapshot",
            font=("Segoe UI", 9),
            bg="#0284c7",
            fg="#ffffff",
            relief=tk.FLAT,
            pady=4,
            command=self._take_snapshot
        )
        btn_snap.pack(fill=tk.X, pady=2)

        self.btn_rec = tk.Button(
            tools_card,
            text="🔴 Start Recording (MP4)",
            font=("Segoe UI", 9),
            bg="#334155",
            fg="#ffffff",
            relief=tk.FLAT,
            pady=4,
            command=self._toggle_recording
        )
        self.btn_rec.pack(fill=tk.X, pady=2)

        btn_folder = tk.Button(
            tools_card,
            text="📂 Open Recordings Folder",
            font=("Segoe UI", 8),
            bg="#1e293b",
            fg="#94a3b8",
            relief=tk.FLAT,
            pady=2,
            command=self._open_recordings_folder
        )
        btn_folder.pack(fill=tk.X, pady=2)

    def _setup_tray(self):
        """Sets up Windows system tray icon."""
        try:
            icon_img = make_tray_icon_image()
            menu = pystray.Menu(
                item('Open AndroidCam HD', self._restore_from_tray, default=True),
                item('Start with Windows', self._toggle_autostart_tray, checked=lambda item: is_autostart_enabled()),
                pystray.Menu.SEPARATOR,
                item('Exit AndroidCam HD', self._quit_app)
            )
            self.tray_icon = pystray.Icon("AndroidCamHD", icon_img, "AndroidCam HD Receiver", menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception as e:
            print("System tray setup skipped:", e)

    def _minimize_to_tray(self):
        self.root.withdraw()

    def _restore_from_tray(self, icon=None, item=None):
        self.root.after(0, self.root.deiconify)

    def _toggle_autostart_tray(self, icon=None, item=None):
        current = is_autostart_enabled()
        set_autostart(not current)
        self.root.after(0, lambda: self.var_autostart.set(not current))

    def _on_toggle_autostart(self):
        enabled = self.var_autostart.get()
        set_autostart(enabled)

    def _on_window_close_clicked(self):
        if self.var_bg_close.get():
            self._minimize_to_tray()
        else:
            self._quit_app()

    def _quit_app(self, icon=None, item=None):
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.state.is_running = False
        self.processor.close()
        self.root.after(0, self.root.destroy)
        os._exit(0)

    def _generate_qr_code(self):
        active_ip = self.custom_ip if self.custom_ip else (self.interfaces[self.selected_ip_idx]["ip"] if self.interfaces else "127.0.0.1")
        url = f"http://{active_ip}:{self.port}"
        self.url_label.config(text=url)

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=4,
            border=2,
        )
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#000000", back_color="#ffffff")

        self.qr_img_tk = ImageTk.PhotoImage(img)
        self.qr_label.config(image=self.qr_img_tk)

    def _on_ip_selected(self, event):
        idx = self.ip_combo.current()
        if idx >= 0:
            self.selected_ip_idx = idx
            self.custom_ip = ""
            self.custom_ip_entry.delete(0, tk.END)
            self.custom_ip_entry.insert(0, self.interfaces[idx]["ip"])
            self._generate_qr_code()

    def _on_custom_ip_set(self):
        val = self.custom_ip_entry.get().strip()
        if val:
            self.custom_ip = val
            self._generate_qr_code()

    def _copy_url(self):
        url = self.url_label.cget("text")
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        messagebox.showinfo("Copied", f"Copied URL to clipboard:\n{url}")

    def _on_transform_change(self):
        with self.state.lock:
            self.state.flip_horizontal = self.var_flip_h.get()
            self.state.flip_vertical = self.var_flip_v.get()

    def _rotate_90(self):
        with self.state.lock:
            self.state.rotation = (self.state.rotation + 90) % 360

    def _take_snapshot(self):
        with self.state.lock:
            self.state.take_snapshot_req = True
        messagebox.showinfo("Snapshot", "1080p snapshot will be captured on next frame!")

    def _toggle_recording(self):
        with self.state.lock:
            self.state.is_recording = not self.state.is_recording
            recording = self.state.is_recording

        if recording:
            self.btn_rec.config(text="⏹ Stop Recording", bg="#dc2626")
        else:
            self.btn_rec.config(text="🔴 Start Recording (MP4)", bg="#334155")
            messagebox.showinfo("Recording Saved", f"Recording saved to:\n{self.processor.output_dir.resolve()}")

    def _open_recordings_folder(self):
        folder = self.processor.output_dir.resolve()
        os.startfile(folder)

    def _check_startup_driver(self):
        """Checks driver and offers automatic 1-click registration on first run."""
        is_ok, dev = check_virtual_camera_status()
        if not is_ok:
            msg = (
                "Welcome to AndroidCam HD!\n\n"
                "Enable the Virtual Webcam driver now?\n\n"
                "This allows Zoom, Microsoft Teams, Google Meet, and Discord\n"
                "to instantly see your tablet as 'AndroidCam HD' webcam."
            )
            if messagebox.askyesno("Enable Virtual Webcam", msg, parent=self.root):
                self._install_driver_prompt()
        else:
            with self.state.lock:
                self.state.virtual_cam_active = True
                self.state.virtual_cam_device = dev
                self.state.virtual_cam_error = ""

    def _install_driver_prompt(self):
        """Installs and registers virtual camera driver internally without needing any .bat scripts."""
        try:
            success = install_driver_auto(None)
            if success:
                self.root.after(1000, self._verify_driver_after_install)
            else:
                messagebox.showerror(
                    "Driver Setup",
                    "Administrator permission was declined or registration failed.\n"
                    "You can still preview, record, and snapshot without the virtual camera.",
                    parent=self.root
                )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to execute driver installer: {e}", parent=self.root)

    def _verify_driver_after_install(self):
        is_ok, dev = check_virtual_camera_status()
        if is_ok:
            messagebox.showinfo(
                "Driver Registered",
                f"Virtual Camera is now ACTIVE!\n\nDevice: {dev}\n\n"
                "You can now select 'AndroidCam HD' in Zoom, Teams, Meet, Discord, and Windows Camera.",
                parent=self.root
            )
            with self.state.lock:
                self.state.virtual_cam_active = True
                self.state.virtual_cam_device = dev
                self.state.virtual_cam_error = ""
        else:
            time.sleep(1)
            is_ok, dev = check_virtual_camera_status()
            if is_ok:
                with self.state.lock:
                    self.state.virtual_cam_active = True
                    self.state.virtual_cam_device = dev
                    self.state.virtual_cam_error = ""

    def _update_ui_loop(self):
        # 1. Update Connection Status
        with self.state.lock:
            connected = self.state.client_connected
            device_info = self.state.client_device_info
            fps = self.state.fps
            res_w = self.state.width
            res_h = self.state.height
            bitrate = self.state.bitrate_kbps
            vcam_active = self.state.virtual_cam_active
            vcam_dev = self.state.virtual_cam_device
            vcam_err = self.state.virtual_cam_error
            frame = self.state.latest_frame.copy() if self.state.latest_frame is not None else None

        if connected:
            self.status_header_label.config(text="● STREAMING LIVE", fg="#10b981")
        else:
            self.status_header_label.config(text="● WAITING FOR TABLET", fg="#ef4444")

        self.lbl_fps.config(text=f"FPS: {fps}")
        self.lbl_res.config(text=f"Resolution: {res_w}x{res_h}")
        self.lbl_bitrate.config(text=f"Bitrate: {bitrate} KB/s")

        # Virtual Cam status label
        if vcam_active:
            self.vcam_status_lbl.config(
                text=f"● Active in Windows: {vcam_dev}\n(Available in Zoom, Teams, Meet, Discord)",
                fg="#10b981"
            )
        else:
            if vcam_err:
                self.vcam_status_lbl.config(
                    text="● Driver not registered.\nClick button below to register driver.",
                    fg="#f87171"
                )
            else:
                self.vcam_status_lbl.config(
                    text="● Ready. Waiting for stream frames...",
                    fg="#fbbf24"
                )

        # 2. Render Frame on Canvas with correct widescreen aspect ratio
        if frame is not None:
            cw = self.canvas.winfo_width()
            ch = self.canvas.winfo_height()

            if cw > 50 and ch > 50:
                fh, fw = frame.shape[:2]
                scale = min(cw / fw, ch / fh)
                nw = max(1, int(fw * scale))
                nh = max(1, int(fh * scale))

                resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
                rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(rgb)
                self.preview_img_tk = ImageTk.PhotoImage(pil_img)

                x = (cw - nw) // 2
                y = (ch - nh) // 2

                self.canvas.delete("all")
                self.canvas.create_image(x, y, anchor=tk.NW, image=self.preview_img_tk)
        else:
            cw = self.canvas.winfo_width()
            ch = self.canvas.winfo_height()
            if cw > 50 and ch > 50:
                self.canvas.delete("all")
                self.canvas.create_text(
                    cw // 2,
                    ch // 2 - 20,
                    text="📷 AndroidCam HD",
                    font=("Segoe UI", 18, "bold"),
                    fill="#38bdf8"
                )
                self.canvas.create_text(
                    cw // 2,
                    ch // 2 + 20,
                    text="Scan the QR Code with your tablet or enter the IP in Android App\nSupports 1080p & 4K over Tailscale VPN or Local Wi-Fi",
                    font=("Segoe UI", 10),
                    fill="#64748b",
                    justify=tk.CENTER
                )

        self.root.after(33, self._update_ui_loop)
