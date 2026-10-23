"""
AndroidCam HD - Main Application Entry Point
"""

import os
os.environ["OPENBLAS_NUM_THREADS"] = "1"
import argparse
import asyncio
import logging
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

import cv2
import numpy as np
import qrcode

from server import AppState, VideoProcessor, StreamingServer, get_network_interfaces
from gui import AndroidCamGUI

# Set utf-8 encoding for Windows terminal
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger("AndroidCam")


def run_async_server(server: StreamingServer, loop: asyncio.AbstractEventLoop):
    asyncio.set_event_loop(loop)
    loop.run_until_complete(server.start())
    loop.run_forever()


def print_banner(interfaces, port):
    print("\n" + "=" * 60)
    print("   🚀 AndroidCam HD - 1080p/4K Offline Camera Receiver")
    print("   No 480p cap · No Internet Needed · Zero Cloud Logins")
    print("=" * 60)
    print("\nAvailable Connection Addresses for your Android Tablet:")
    for iface in interfaces:
        url = f"http://{iface['ip']}:{port}"
        print(f"  • [{iface['type']:<15}]  ->  {url}")

    print("\nScan the QR code in the GUI window or open the link above in Chrome on your tablet.\n" + "=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(description="AndroidCam HD - High-Definition Webcam Receiver")
    parser.add_argument("--port", type=int, default=8080, help="Port to host receiver server (default: 8080)")
    parser.add_argument("--no-gui", action="store_true", help="Run in headless / CLI mode without Tkinter GUI")
    parser.add_argument("--no-vcam", action="store_true", help="Disable virtual webcam output")
    parser.add_argument("--background", "--minimized", action="store_true", help="Start minimized in system tray on boot")
    args = parser.parse_args()

    state = AppState()
    if args.no_vcam:
        state.virtual_cam_enabled = False

    processor = VideoProcessor(state)
    server = StreamingServer(state, processor, host="0.0.0.0", port=args.port)

    interfaces = get_network_interfaces()
    print_banner(interfaces, args.port)

    # Start Asyncio Server in Background Thread
    server_loop = asyncio.new_event_loop()
    server_thread = threading.Thread(
        target=run_async_server,
        args=(server, server_loop),
        daemon=True,
        name="ServerThread"
    )
    server_thread.start()

    if not args.no_gui:
        # Launch Tkinter GUI
        root = tk.Tk()
        gui = AndroidCamGUI(root, state, processor, port=args.port, start_minimized=args.background)

        def on_close():
            logger.info("Shutting down AndroidCam...")
            state.is_running = False
            processor.close()
            server_loop.call_soon_threadsafe(server_loop.stop)
            root.destroy()
            sys.exit(0)

        root.protocol("WM_DELETE_WINDOW", on_close)
        root.mainloop()
    else:
        # CLI Mode with OpenCV window
        print("Running in CLI mode. Press 'q' in OpenCV preview to exit.")
        try:
            cv2.namedWindow("AndroidCam HD Preview", cv2.WINDOW_NORMAL)
            cv2.resizeWindow("AndroidCam HD Preview", 960, 540)
            while state.is_running:
                frame = None
                with state.lock:
                    if state.latest_frame is not None:
                        frame = state.latest_frame.copy()

                if frame is not None:
                    cv2.imshow("AndroidCam HD Preview", frame)
                else:
                    blank = np.zeros((540, 960, 3), dtype=np.uint8)
                    cv2.putText(
                        blank,
                        "AndroidCam HD: Waiting for Tablet...",
                        (200, 270),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.9,
                        (56, 189, 248),
                        2
                    )
                    cv2.imshow("AndroidCam HD Preview", blank)

                key = cv2.waitKey(30) & 0xFF
                if key == ord('q'):
                    break
        except KeyboardInterrupt:
            pass
        finally:
            logger.info("Exiting...")
            processor.close()
            server_loop.call_soon_threadsafe(server_loop.stop)
            cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
