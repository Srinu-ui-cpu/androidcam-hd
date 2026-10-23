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


def check_virtual_camera_status() -> Tuple[bool, str]:
    """Checks whether Windows recognized any Virtual Camera (Unity Capture or OBS)."""
    if not PYVIRTUALCAM_AVAILABLE:
        return False, "pyvirtualcam library not available"

    # 1. Check Unity Video Capture
    try:
        import pyvirtualcam._native_windows_unity_capture as uc
        c = uc.Camera(width=64, height=64, fps=30.0, fourcc=0, device=None)
        dev = str(c.device)
        c.close()
        return True, dev
    except Exception as e:
        uc_err = str(e)

    # 2. Check OBS Virtual Camera
    try:
        import pyvirtualcam._native_windows_obs as obs
        c = obs.Camera(width=64, height=64, fps=30.0, fourcc=0, device=None)
        dev = str(c.device)
        c.close()
        return True, dev
    except Exception as e:
        obs_err = str(e)

    return False, f"Not registered ({uc_err})"


def install_driver_auto(hwnd=None) -> bool:
    """
    Automatically extracts DLLs to permanent %LOCALAPPDATA%\\AndroidCamHD\\driver
    and registers them with Windows DirectShow via UAC elevation.
    No .bat files or command line needed!
    """
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

        # Trigger elevated silent regsvr32
        ret = ctypes.windll.shell32.ShellExecuteW(
            hwnd,
            "runas",
            "regsvr32.exe",
            f'/s "{dll64}"',
            None,
            0  # SW_HIDE
        )
        if ret > 32:
            if dll32.exists():
                ctypes.windll.shell32.ShellExecuteW(
                    hwnd,
                    "runas",
                    "regsvr32.exe",
                    f'/s "{dll32}"',
                    None,
                    0
                )
            return True
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
    """Handles frame decoding, transformations, virtual camera feeding, and recording."""
    def __init__(self, state: AppState):
        self.state = state
        self.vcam: Optional[pyvirtualcam.Camera] = None
        self.vcam_dims: Optional[Tuple[int, int]] = None
        self.output_dir = Path("recordings")
        self.output_dir.mkdir(exist_ok=True)

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
            # We use BGR pixel format directly matching OpenCV
            self.vcam = pyvirtualcam.Camera(
                width=width,
                height=height,
                fps=fps,
                fmt=pyvirtualcam.PixelFormat.BGR,
                print_fps=False
            )
            self.vcam_dims = (width, height)
            self.state.virtual_cam_active = True
            self.state.virtual_cam_device = str(self.vcam.device)
            self.state.virtual_cam_error = ""
            logger.info(f"[+] Virtual Camera active: {self.vcam.device}")
        except Exception as e:
            self.state.virtual_cam_active = False
            self.state.virtual_cam_error = str(e)
            logger.warning(f"Virtual camera not initialized: {e}")

    def process_raw_frame(self, raw_bytes: bytes):
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
    """Discovers all local network interfaces (Wi-Fi, Tailscale, Hotspot, Ethernet)."""
    interfaces = []
    try:
        cmd = "Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceAlias, IPAddress | ConvertTo-Json"
        res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=5)
        if res.returncode == 0 and res.stdout.strip():
            raw = json.loads(res.stdout)
            items = raw if isinstance(raw, list) else [raw]
            for item in items:
                alias = item.get("InterfaceAlias", "Unknown")
                ip = item.get("IPAddress", "")
                if ip and not ip.startswith("127.") and not ip.startswith("169.254."):
                    # Categorize interface type
                    kind = "Local Network"
                    lower_alias = alias.lower()
                    if "tailscale" in lower_alias:
                        kind = "Tailscale VPN"
                    elif "wi-fi" in lower_alias or "wlan" in lower_alias or "wireless" in lower_alias:
                        kind = "Wi-Fi"
                    elif "ethernet" in lower_alias or "lan" in lower_alias:
                        kind = "Ethernet"
                    elif "hotspot" in lower_alias or "virtual" in lower_alias:
                        kind = "Hotspot / Virtual"

                    interfaces.append({"alias": alias, "ip": ip, "type": kind})
    except Exception as e:
        logger.warning(f"PowerShell network query failed: {e}")

    # Fallback to standard socket if empty
    if not interfaces:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
            interfaces.append({"alias": "Local Network", "ip": local_ip, "type": "LAN"})
        except Exception:
            interfaces.append({"alias": "Localhost", "ip": "127.0.0.1", "type": "Loopback"})

    return interfaces


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

        client_ip = request.remote
        logger.info(f"[+] Client connected from {client_ip}")

        with self.state.lock:
            self.state.client_connected = True
            self.state.client_ip = client_ip

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.BINARY:
                    # Raw JPEG frame received from Android tablet
                    self.processor.process_raw_frame(msg.data)

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
                            await ws.send_json({"type": "pong", "time": time.time()})
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
