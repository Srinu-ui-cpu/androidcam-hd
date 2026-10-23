"""
AndroidCam HD - Modern Desktop GUI Control Center
"""

import os
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from typing import List, Dict

import cv2
import numpy as np
from PIL import Image, ImageTk
import qrcode

from server import AppState, VideoProcessor, get_network_interfaces, get_base_path


class AndroidCamGUI:
    def __init__(self, root: tk.Tk, state: AppState, processor: VideoProcessor, port: int = 8080):
        self.root = root
        self.state = state
        self.processor = processor
        self.port = port

        self.root.title("AndroidCam HD - Virtual Camera Receiver")
        self.root.geometry("1100x720")
        self.root.minsize(960, 640)
        self.root.configure(bg="#0b0f19")

        # Network Interfaces
        self.interfaces: List[Dict[str, str]] = get_network_interfaces()
        self.selected_ip_idx = 0

        # UI references
        self.qr_img_tk = None
        self.preview_img_tk = None

        self._apply_dark_theme()
        self._build_ui()
        self._generate_qr_code()

        # Start UI update loop
        self.root.after(30, self._update_ui_loop)

    def _apply_dark_theme(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Configure dark colors
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

        self.status_header_label = tk.Label(
            header,
            text="● WAITING FOR TABLET",
            font=("Segoe UI", 9, "bold"),
            fg="#ef4444",
            bg="#0f172a",
            padx=16
        )
        self.status_header_label.pack(side=tk.RIGHT)

        # 2. Main Content Frame (Left: Video, Right: Controls & QR)
        content = tk.Frame(self.root, bg="#0b0f19")
        content.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

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

        # RIGHT: Controls & QR Code Panel
        right_panel = tk.Frame(content, bg="#0b0f19", width=340)
        right_panel.pack(side=tk.RIGHT, fill=tk.Y)
        right_panel.pack_propagate(False)

        # Card 1: Network & QR Code
        net_card = tk.LabelFrame(
            right_panel,
            text=" 📱 Connect Tablet ",
            font=("Segoe UI", 9, "bold"),
            bg="#131b2e",
            fg="#38bdf8",
            bd=1,
            padx=10,
            pady=10
        )
        net_card.pack(fill=tk.X, pady=(0, 10))

        tk.Label(net_card, text="Scan with tablet camera or open URL:", font=("Segoe UI", 8), fg="#94a3b8", bg="#131b2e").pack(anchor=tk.W)

        # Interface Selector dropdown
        if self.interfaces:
            options = [f"{item['type']}: {item['ip']}" for item in self.interfaces]
            self.ip_combo_var = tk.StringVar(value=options[0])
            self.ip_combo = ttk.Combobox(net_card, textvariable=self.ip_combo_var, values=options, state="readonly")
            self.ip_combo.pack(fill=tk.X, pady=6)
            self.ip_combo.bind("<<ComboboxSelected>>", self._on_ip_selected)
        else:
            self.ip_combo_var = tk.StringVar(value="127.0.0.1")

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
        self.qr_label.pack(pady=6)

        # Card 2: Virtual Webcam Status & Actions
        vcam_card = tk.LabelFrame(
            right_panel,
            text=" 🎥 Windows Virtual Camera ",
            font=("Segoe UI", 9, "bold"),
            bg="#131b2e",
            fg="#38bdf8",
            bd=1,
            padx=10,
            pady=10
        )
        vcam_card.pack(fill=tk.X, pady=(0, 10))

        self.vcam_status_lbl = tk.Label(
            vcam_card,
            text="Status: Checking...",
            font=("Segoe UI", 8, "bold"),
            fg="#fbbf24",
            bg="#131b2e",
            wraplength=300,
            justify=tk.LEFT
        )
        self.vcam_status_lbl.pack(anchor=tk.W, pady=(0, 6))

        self.btn_install_driver = tk.Button(
            vcam_card,
            text="⚙️ Install Virtual Camera Driver",
            font=("Segoe UI", 9, "bold"),
            bg="#2563eb",
            fg="#ffffff",
            activebackground="#1d4ed8",
            relief=tk.FLAT,
            padx=10,
            pady=6,
            cursor="hand2",
            command=self._install_driver_prompt
        )
        self.btn_install_driver.pack(fill=tk.X, pady=4)

        # Card 3: Video Adjustments
        adj_card = tk.LabelFrame(
            right_panel,
            text=" 🔄 Video Adjustments ",
            font=("Segoe UI", 9, "bold"),
            bg="#131b2e",
            fg="#38bdf8",
            bd=1,
            padx=10,
            pady=8
        )
        adj_card.pack(fill=tk.X, pady=(0, 10))

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
        chk_flip_h.pack(anchor=tk.W, pady=2)

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
        chk_flip_v.pack(anchor=tk.W, pady=2)

        rot_frame = tk.Frame(adj_card, bg="#131b2e")
        rot_frame.pack(fill=tk.X, pady=4)

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

        # Card 4: Tools (Snapshot & Record)
        tools_card = tk.LabelFrame(
            right_panel,
            text=" 📸 Capture & Recording ",
            font=("Segoe UI", 9, "bold"),
            bg="#131b2e",
            fg="#38bdf8",
            bd=1,
            padx=10,
            pady=8
        )
        tools_card.pack(fill=tk.X)

        btn_snap = tk.Button(
            tools_card,
            text="📸 Save 1080p Snapshot",
            font=("Segoe UI", 9),
            bg="#0284c7",
            fg="#ffffff",
            relief=tk.FLAT,
            pady=5,
            command=self._take_snapshot
        )
        btn_snap.pack(fill=tk.X, pady=3)

        self.btn_rec = tk.Button(
            tools_card,
            text="🔴 Start Recording (MP4)",
            font=("Segoe UI", 9),
            bg="#334155",
            fg="#ffffff",
            relief=tk.FLAT,
            pady=5,
            command=self._toggle_recording
        )
        self.btn_rec.pack(fill=tk.X, pady=3)

        btn_folder = tk.Button(
            tools_card,
            text="📂 Open Recordings Folder",
            font=("Segoe UI", 8),
            bg="#1e293b",
            fg="#94a3b8",
            relief=tk.FLAT,
            pady=3,
            command=self._open_recordings_folder
        )
        btn_folder.pack(fill=tk.X, pady=3)

    def _generate_qr_code(self):
        if not self.interfaces:
            return
        ip = self.interfaces[self.selected_ip_idx]["ip"]
        url = f"http://{ip}:{self.port}"
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

        # Convert to PhotoImage
        self.qr_img_tk = ImageTk.PhotoImage(img)
        self.qr_label.config(image=self.qr_img_tk)

    def _on_ip_selected(self, event):
        idx = self.ip_combo.current()
        if idx >= 0:
            self.selected_ip_idx = idx
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

    def _install_driver_prompt(self):
        bat_file = get_base_path() / "driver" / "install_driver.bat"
        if not bat_file.exists():
            bat_file = Path("driver/install_driver.bat").resolve()
        if not bat_file.exists():
            messagebox.showerror("Error", f"Could not find driver installer at:\n{bat_file}")
            return

        msg = (
            "Clicking Yes will launch the Virtual Camera Driver Installer with Administrator privileges.\n\n"
            "This will register 'Unity Video Capture' as a native DirectShow webcam in Windows.\n\n"
            "Proceed?"
        )
        if messagebox.askyesno("Install Virtual Camera Driver", msg):
            try:
                subprocess.Popen(["cmd.exe", "/c", str(bat_file)], shell=True)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to execute driver installer: {e}")

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

        # 2. Render Frame on Canvas
        if frame is not None:
            # Scale frame to fit canvas while preserving aspect ratio
            cw = self.canvas.winfo_width()
            ch = self.canvas.winfo_height()

            if cw > 50 and ch > 50:
                fh, fw = frame.shape[:2]
                scale = min(cw / fw, ch / fh)
                nw = int(fw * scale)
                nh = int(fh * scale)

                resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
                # Convert BGR to RGB for Tkinter PhotoImage
                rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
                pil_img = Image.fromarray(rgb)
                self.preview_img_tk = ImageTk.PhotoImage(pil_img)

                # Center image on canvas
                x = (cw - nw) // 2
                y = (ch - nh) // 2

                self.canvas.delete("all")
                self.canvas.create_image(x, y, anchor=tk.NW, image=self.preview_img_tk)
        else:
            # Draw placeholder when waiting for connection
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
                    text="Scan the QR Code with your tablet or open the URL in Chrome\nSupports 1080p & 4K over Local Wi-Fi, Hotspot, or Tailscale",
                    font=("Segoe UI", 10),
                    fill="#64748b",
                    justify=tk.CENTER
                )

        # Repeat update loop
        self.root.after(33, self._update_ui_loop)
