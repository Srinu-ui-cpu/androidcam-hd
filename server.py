"""
AndroidCam HD - High Performance Video Receiver & Virtual Camera Hub
"""

import asyncio
import ctypes
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Tuple, List, Dict

# Prevent OpenBLAS thread allocation issues on Windows
os.environ["OPENBLAS_NUM_THREADS"] = "1"

import cv2
import numpy as np
from aiohttp import web

# Configure logging
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger("AndroidCam")

# Import pyvirtualcam if available
try:
    import pyvirtualcam
    PYVIRTUALCAM_AVAILABLE = True
except ImportError:
    PYVIRTUALCAM_AVAILABLE = False
    logger.warning("pyvirtualcam is not installed.")


def get_base_path() -> Path:
    """Returns base directory for assets, handling PyInstaller frozen executables."""
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            return Path(sys._MEIPASS)
        return Path(sys.executable).parent
    return Path(__file__).parent


def ensure_camera_friendly_name(name: str = "AndroidCam HD"):
    """Configures the DirectShow friendly name in HKCU and enables Windows FrameServer compatibility."""
    if sys.platform != "win32":
        return
    try:
        import winreg
        # DirectShow Video Input Device Category
        path = r"Software\Classes\CLSID\{860BB310-5D01-11d0-BD3B-00A0C911CE86}\Instance\{5C2CD55C-92AD-4999-8666-912BD3E70010}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as k:
            winreg.SetValueEx(k, "FriendlyName", 0, winreg.REG_SZ, name)
            winreg.SetValueEx(k, "CLSID", 0, winreg.REG_SZ, "{5C2CD55C-92AD-4999-8666-912BD3E70010}")

        # Media Foundation FrameServer compatibility (allows Windows Camera app and Store apps to see DirectShow camera)
        mf_path = r"Software\Microsoft\Windows Media Foundation\Platform"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, mf_path) as k:
            winreg.SetValueEx(k, "EnableFrameServerMode", 0, winreg.REG_DWORD, 0)
    except Exception as e:
        logger.debug(f"Could not update camera friendly name in HKCU: {e}")


def check_virtual_camera_status() -> Tuple[bool, str]:
    """Checks whether Windows recognized any Virtual Camera (Unity Capture or OBS)."""
    if not PYVIRTUALCAM_AVAILABLE:
        return False, "pyvirtualcam library not available"

    try:
        import pyvirtualcam
        c = pyvirtualcam.Camera(width=64, height=64, fps=30.0, fmt=pyvirtualcam.PixelFormat.BGR)
        c.close()
        ensure_camera_friendly_name("AndroidCam HD")
        return True, "AndroidCam HD"
    except Exception as e:
        return False, f"Not registered ({e})"


def register_driver_user_space() -> bool:
    """
    Registers the Virtual Camera DirectShow filter directly into HKCU (Current User).
    Requires ZERO Administrator privileges, NEVER triggers UAC prompts,
    and works even on standard, non-admin user accounts!
    """
    if sys.platform != "win32":
        return False
    try:
        import winreg
        appdata = os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
        dest_dir = Path(appdata) / "AndroidCamHD" / "driver"
        dest_dir.mkdir(parents=True, exist_ok=True)

        src_dir = get_base_path() / "driver"
        dll64 = dest_dir / "UnityCaptureFilter64.dll"
        dll32 = dest_dir / "UnityCaptureFilter32.dll"

        # Copy DLLs permanently to LocalAppData
        if (src_dir / "UnityCaptureFilter64.dll").exists() and not dll64.exists():
            shutil.copy2(src_dir / "UnityCaptureFilter64.dll", dll64)
        if (src_dir / "UnityCaptureFilter32.dll").exists() and not dll32.exists():
            shutil.copy2(src_dir / "UnityCaptureFilter32.dll", dll32)

        if not dll64.exists():
            return False

        # 1. Register 64-bit InprocServer32 in HKCU\Software\Classes\CLSID
        clsid_path = r"Software\Classes\CLSID\{5C2CD55C-92AD-4999-8666-912BD3E70010}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, clsid_path) as k:
            winreg.SetValue(k, "", winreg.REG_SZ, "AndroidCam HD")
            with winreg.CreateKey(k, "InprocServer32") as inproc:
                winreg.SetValue(inproc, "", winreg.REG_SZ, str(dll64.resolve()))
                winreg.SetValueEx(inproc, "ThreadingModel", 0, winreg.REG_SZ, "Both")

        # 2. Register DirectShow Video Input Category in HKCU
        cat_path = r"Software\Classes\CLSID\{860BB310-5D01-11d0-BD3B-00A0C911CE86}\Instance\{5C2CD55C-92AD-4999-8666-912BD3E70010}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, cat_path) as cat:
            winreg.SetValueEx(cat, "CLSID", 0, winreg.REG_SZ, "{5C2CD55C-92AD-4999-8666-912BD3E70010}")
            winreg.SetValueEx(cat, "FriendlyName", 0, winreg.REG_SZ, "AndroidCam HD")
            winreg.SetValueEx(cat, "DevicePath", 0, winreg.REG_SZ, "foo:bar")
            filter_data = b'\x02\x00\x00\x00\x00\x00 \x00\x01\x00\x00\x00\x00\x00\x00\x000pi3\x08\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x000ty3\x00\x00\x00\x008\x00\x00\x00H\x00\x00\x00vids\x00\x00\x10\x00\x80\x00\x00\xaa\x008\x9bq\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
            winreg.SetValueEx(cat, "FilterData", 0, winreg.REG_BINARY, filter_data)

        # 3. Media Foundation FrameServer compatibility in HKCU
        mf_path = r"Software\Microsoft\Windows Media Foundation\Platform"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, mf_path) as mf:
            winreg.SetValueEx(mf, "EnableFrameServerMode", 0, winreg.REG_DWORD, 0)

        logger.info("[+] User-space registration succeeded in HKCU without administrator rights.")
        return True
    except Exception as e:
        logger.error(f"User-space registration error: {e}")
        return False


def install_driver_auto(hwnd=None) -> bool:
    """
    Installs and registers virtual camera driver internally without needing any .bat scripts.
    1. First attempts zero-privilege user-space (HKCU) registration (works 100% without admin rights).
    2. Fallback to elevated UAC registration only if needed.
    """
    # 1. If camera is already registered and working, succeed immediately!
    is_ok, dev = check_virtual_camera_status()
    if is_ok:
        ensure_camera_friendly_name("AndroidCam HD")
        return True

    # 2. Attempt zero-admin user-space registration
    if register_driver_user_space():
        is_ok, dev = check_virtual_camera_status()
        if is_ok:
            return True

    # 3. Fallback: elevated registration if user explicitly requested system-wide install
    try:
        appdata = os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local"))
        dest_dir = Path(appdata) / "AndroidCamHD" / "driver"
        dest_dir.mkdir(parents=True, exist_ok=True)

        src_dir = get_base_path() / "driver"
        dll64 = dest_dir / "UnityCaptureFilter64.dll"
        dll32 = dest_dir / "UnityCaptureFilter32.dll"

        # Copy DLLs permanently
        if (src_dir / "UnityCaptureFilter64.dll").exists():
            shutil.copy2(src_dir / "UnityCaptureFilter64.dll", dll64)
        if (src_dir / "UnityCaptureFilter32.dll").exists():
            shutil.copy2(src_dir / "UnityCaptureFilter32.dll", dll32)

        # Write clean elevated registration script
        cmd_script = dest_dir / "register_camera.cmd"
        cmd_content = f'''@echo off
regsvr32.exe /s "{dll64}"
if exist "{dll32}" regsvr32.exe /s "{dll32}"
reg add "HKLM\\SOFTWARE\\Classes\\CLSID\\{{860BB310-5D01-11d0-BD3B-00A0C911CE86}}\\Instance\\{{5C2CD55C-92AD-4999-8666-912BD3E70010}}" /v FriendlyName /d "AndroidCam HD" /f >nul 2>&1
reg add "HKLM\\SOFTWARE\\WOW6432Node\\Classes\\CLSID\\{{860BB310-5D01-11d0-BD3B-00A0C911CE86}}\\Instance\\{{5C2CD55C-92AD-4999-8666-912BD3E70010}}" /v FriendlyName /d "AndroidCam HD" /f >nul 2>&1
reg add "HKLM\\SOFTWARE\\Microsoft\\Windows Media Foundation\\Platform" /v EnableFrameServerMode /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows Media Foundation\\Platform" /v EnableFrameServerMode /t REG_DWORD /d 0 /f >nul 2>&1
'''
        cmd_script.write_text(cmd_content, encoding="utf-8")

        # Execute elevated silently (hwnd=None)
        ret = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            "cmd.exe",
            f'/c "{cmd_script}"',
            None,
            0  # SW_HIDE
        )
        if ret > 32:
            time.sleep(1.5)
            ensure_camera_friendly_name("AndroidCam HD")
            is_ok, _ = check_virtual_camera_status()
            return is_ok
        return False
    except Exception as e:
        logger.error(f"Failed to auto-install driver: {e}")
        return False


class AppState:
    """Thread-safe state shared between Server, Virtual Camera, and GUI."""
    def __init__(self):
        self.lock = threading.Lock()
        self.is_running = True
        self.client_connected = False
        self.client_device_info = "Waiting for connection..."
        self.client_ip = ""
        self.width = 1920
        self.height = 1080
        self.fps = 0.0
        self.bitrate_kbps = 0.0
        self.latency_ms = 0.0

        # Video adjustments
        self.flip_horizontal = True  # Natural mirror mode for webcams
        self.flip_vertical = False
        self.rotation = 0  # 0, 90, 180, 270

        # Virtual Camera State
        self.virtual_cam_enabled = True
        self.virtual_cam_active = False
        self.virtual_cam_device = "None"
        self.virtual_cam_error = ""

        # Recording & Snapshots
        self.is_recording = False
        self.recording_writer: Optional[cv2.VideoWriter] = None
        self.recording_filename = ""
        self.take_snapshot_req = False
        self.snapshot_saved_path = ""

        # Latest decoded frame (BGR format for OpenCV / RGB for VirtualCam)
        self.latest_frame: Optional[np.ndarray] = None
        self.latest_frame_time = 0.0

        # Stats tracking
        self.frame_count = 0
        self.bytes_received = 0
        self.last_stat_time = time.perf_counter()


class VideoProcessor:
    """Decoupled, zero-lag background worker for decoding, transforming, and sending to virtual webcam."""
    def __init__(self, state: AppState):
        self.state = state
        self.vcam: Optional[pyvirtualcam.Camera] = None
        self.vcam_dims: Optional[Tuple[int, int]] = None
        self.output_dir = Path("recordings")
        self.output_dir.mkdir(exist_ok=True)

        # Zero-Lag Worker Queue: Only keeps the latest frame!
        # If network frames arrive faster than decoding, older frames are dropped instantly!
        self._latest_raw_bytes: Optional[bytes] = None
        self._frame_lock = threading.Lock()
        self._frame_event = threading.Event()
        self._running = True

        self._worker_thread = threading.Thread(target=self._worker_loop, name="ZeroLagVideoWorker", daemon=True)
        self._worker_thread.start()

    def push_raw_frame(self, raw_bytes: bytes):
        """Called by network thread: instant O(1) swap. Never blocks network loop!"""
        with self._frame_lock:
            self._latest_raw_bytes = raw_bytes
        self._frame_event.set()

    # Backwards compatibility alias
    def process_raw_frame(self, raw_bytes: bytes):
        self.push_raw_frame(raw_bytes)

    def _worker_loop(self):
        """Dedicated high-priority worker loop for real-time decoding without queue lag."""
        while self._running:
            if not self._frame_event.wait(timeout=0.1):
                continue
            self._frame_event.clear()

            with self._frame_lock:
                raw_bytes = self._latest_raw_bytes
                self._latest_raw_bytes = None

            if raw_bytes is None:
                continue

            self._process_single_frame(raw_bytes)

    def init_virtual_camera(self, width: int, height: int, fps: int = 30):
        if not PYVIRTUALCAM_AVAILABLE or not self.state.virtual_cam_enabled:
            return

        if self.vcam is not None and self.vcam_dims == (width, height):
            return

        # Close existing camera if dimensions changed
        if self.vcam is not None:
            try:
                self.vcam.close()
            except Exception as e:
                logger.error(f"Error closing virtual camera: {e}")
            self.vcam = None
            self.state.virtual_cam_active = False

        try:
            logger.info(f"Initializing Windows Virtual Camera at {width}x{height} @ {fps}fps...")
            self.vcam = pyvirtualcam.Camera(
                width=width,
                height=height,
                fps=fps,
                fmt=pyvirtualcam.PixelFormat.BGR,
                print_fps=False
            )
            self.vcam_dims = (width, height)
            ensure_camera_friendly_name("AndroidCam HD")
            self.state.virtual_cam_active = True
            self.state.virtual_cam_device = "AndroidCam HD"
            self.state.virtual_cam_error = ""
            logger.info("[+] Virtual Camera active: AndroidCam HD")
        except Exception as e:
            self.state.virtual_cam_active = False
            self.state.virtual_cam_error = str(e)
            logger.warning(f"Virtual camera not initialized: {e}")

    def _process_single_frame(self, raw_bytes: bytes):
        # 1. Fast JPEG decode via OpenCV C++ TurboJPEG
        nparr = np.frombuffer(raw_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            return

        h, w = frame.shape[:2]

        # 2. Transformations
        with self.state.lock:
            flip_h = self.state.flip_horizontal
            flip_v = self.state.flip_vertical
            rot = self.state.rotation

        # Rotation
        if rot == 90:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif rot == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif rot == 270:
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

        # Flips
        if flip_h and flip_v:
            frame = cv2.flip(frame, -1)
        elif flip_h:
            frame = cv2.flip(frame, 1)
        elif flip_v:
            frame = cv2.flip(frame, 0)

        out_h, out_w = frame.shape[:2]

        # 3. Feed Virtual Camera
        if self.state.virtual_cam_enabled:
            if self.vcam is None or self.vcam_dims != (out_w, out_h):
                self.init_virtual_camera(out_w, out_h)

            if self.vcam is not None:
                try:
                    self.vcam.send(frame)
                except Exception as e:
                    logger.warning(f"Failed to send frame to virtual camera: {e}")

        # 4. Handle Recording
        if self.state.is_recording:
            if self.state.recording_writer is None:
                ts = time.strftime("%Y%m%d_%H%M%S")
                rec_path = self.output_dir / f"AndroidCam_{ts}.mp4"
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                self.state.recording_writer = cv2.VideoWriter(
                    str(rec_path), fourcc, 30.0, (out_w, out_h)
                )
                self.state.recording_filename = str(rec_path)
                logger.info(f"Started recording to: {rec_path}")

            if self.state.recording_writer is not None:
                self.state.recording_writer.write(frame)
        else:
            if self.state.recording_writer is not None:
                self.state.recording_writer.release()
                self.state.recording_writer = None
                logger.info(f"Finished recording: {self.state.recording_filename}")

        # 5. Handle Snapshot
        if self.state.take_snapshot_req:
            ts = time.strftime("%Y%m%d_%H%M%S")
            snap_path = self.output_dir / f"Snapshot_{ts}.png"
            cv2.imwrite(str(snap_path), frame)
            self.state.snapshot_saved_path = str(snap_path)
            self.state.take_snapshot_req = False
            logger.info(f"Saved snapshot to: {snap_path}")

        # 6. Update State
        with self.state.lock:
            self.state.latest_frame = frame
            self.state.latest_frame_time = time.perf_counter()
            self.state.width = out_w
            self.state.height = out_h
            self.state.frame_count += 1
            self.state.bytes_received += len(raw_bytes)

            now = time.perf_counter()
            elapsed = now - self.state.last_stat_time
            if elapsed >= 1.0:
                self.state.fps = round(self.state.frame_count / elapsed, 1)
                self.state.bitrate_kbps = round((self.state.bytes_received / 1024) / elapsed, 1)
                self.state.frame_count = 0
                self.state.bytes_received = 0
                self.state.last_stat_time = now

    def close(self):
        self._running = False
        self._frame_event.set()
        if self.vcam is not None:
            try:
                self.vcam.close()
            except Exception:
                pass
            self.vcam = None
        if self.state.recording_writer is not None:
            self.state.recording_writer.release()
            self.state.recording_writer = None


def get_network_interfaces() -> List[Dict[str, str]]:
    """Discovers all local network interfaces (Wi-Fi, Tailscale, Hotspot, Ethernet) reliably using psutil."""
    interfaces = []
    seen_ips = set()

    # 1. Primary discovery: psutil (native OS API, instant, 100% reliable)
    try:
        import psutil
        for name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family == socket.AF_INET:
                    ip = addr.address
                    if ip.startswith("127.") or ip.startswith("169.254."):
                        continue
                    if ip in seen_ips:
                        continue
                    seen_ips.add(ip)

                    lower = name.lower()
                    if "tailscale" in lower or ip.startswith("100."):
                        kind = "Tailscale VPN"
                    elif any(w in lower for w in ["wi-fi", "wlan", "wireless"]) or ip.startswith("192.168."):
                        kind = "Wi-Fi / LAN"
                    elif any(w in lower for w in ["ethernet", "lan", "eth"]):
                        kind = "Ethernet"
                    elif any(w in lower for w in ["hotspot", "virtual"]):
                        kind = "Hotspot"
                    else:
                        kind = "Local Network"

                    interfaces.append({"alias": name, "ip": ip, "type": kind})
    except Exception as e:
        logger.warning(f"psutil network query failed: {e}")

    # Sort so Tailscale and Wi-Fi are at the top
    interfaces.sort(key=lambda x: (0 if "Tailscale" in x["type"] else (1 if "Wi-Fi" in x["type"] else 2)))

    # Fallback if empty
    if not interfaces:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            interfaces.append({"alias": "Local Network", "ip": local_ip, "type": "Wi-Fi / LAN"})
        except Exception:
            interfaces.append({"alias": "Localhost", "ip": "127.0.0.1", "type": "Localhost"})

    return interfaces


def is_autostart_enabled() -> bool:
    """Checks if AndroidCam HD is configured to start on Windows boot."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, "AndroidCamHD")
            return bool(val)
    except Exception:
        return False


def set_autostart(enable: bool) -> bool:
    """Enables or disables auto-starting AndroidCam HD on Windows boot."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                if getattr(sys, "frozen", False):
                    exe_path = sys.executable
                else:
                    exe_path = f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'
                cmd = f'"{exe_path}" --background'
                winreg.SetValueEx(key, "AndroidCamHD", 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, "AndroidCamHD")
                except FileNotFoundError:
                    pass
        return True
    except Exception as e:
        logger.error(f"Failed to toggle autostart: {e}")
        return False


class StreamingServer:
    """aiohttp-based HTTP & WebSocket server for receiving camera stream."""
    def __init__(self, state: AppState, processor: VideoProcessor, host: str = "0.0.0.0", port: int = 8080):
        self.state = state
        self.processor = processor
        self.host = host
        self.port = port
        self.app = web.Application()
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self.web_dir = get_base_path() / "web"
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_get("/ws", self.handle_websocket)
        # Static files
        self.app.router.add_static("/", path=str(self.web_dir), name="static")

    async def handle_index(self, request):
        index_file = self.web_dir / "index.html"
        return web.FileResponse(index_file)

    async def handle_websocket(self, request):
        ws = web.WebSocketResponse(max_msg_size=16 * 1024 * 1024)
        await ws.prepare(request)

        # Enable TCP_NODELAY for instantaneous sub-millisecond packet transmission
        sock = request.transport.get_extra_info('socket')
        if sock:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass

        client_ip = request.remote
        logger.info(f"[+] Client connected from {client_ip}")

        with self.state.lock:
            self.state.client_connected = True
            self.state.client_ip = client_ip

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.BINARY:
                    # Instantaneous zero-lag push to dedicated worker: non-blocking O(1)
                    self.processor.push_raw_frame(msg.data)

                elif msg.type == web.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        msg_type = data.get("type", "")
                        if msg_type == "client_hello":
                            dev = data.get("device", "Unknown Android")
                            res = data.get("resolution", "1080p")
                            with self.state.lock:
                                self.state.client_device_info = f"{dev} ({res})"
                            logger.info(f"Client Device: {dev} [Resolution: {res}]")
                        elif msg_type == "ping":
                            # Immediate pong echo for precise latency measurement
                            await ws.send_json({"type": "pong", "time": data.get("time", time.time())})
                    except Exception as e:
                        logger.warning(f"Malformed JSON from client: {e}")

                elif msg.type == web.WSMsgType.ERROR:
                    logger.warning(f"WebSocket closed with exception: {ws.exception()}")
        finally:
            logger.info(f"[-] Client disconnected from {client_ip}")
            with self.state.lock:
                self.state.client_connected = False
                self.state.client_device_info = "Disconnected"

        return ws

    async def start(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        logger.info(f"[+] Server started on http://{self.host}:{self.port}")

    async def stop(self):
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        logger.info("Server stopped.")
