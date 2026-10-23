import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2
import numpy as np
import aiohttp
from server import AppState, VideoProcessor, StreamingServer


async def run_client_test(port: int, state: AppState):
    # 1. HTTP Verification
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/") as resp:
            assert resp.status == 200, f"HTTP / returned {resp.status}"
            text = await resp.text()
            assert "AndroidCam HD" in text, "Index page missing expected title"
            print("[PASS] HTTP index.html served successfully (200 OK)")

        async with session.get(f"http://127.0.0.1:{port}/manifest.json") as resp:
            assert resp.status == 200, f"HTTP /manifest.json returned {resp.status}"
            print("[PASS] HTTP manifest.json served successfully (200 OK)")

        # 2. WebSocket Verification
        async with session.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
            print("[PASS] WebSocket connection established")

            # Send hello
            await ws.send_json({
                "type": "client_hello",
                "device": "Test Tecno MegaPad",
                "resolution": "1920x1080"
            })

            # Generate synthetic 1080p frame (1920x1080)
            synthetic_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
            cv2.rectangle(synthetic_frame, (100, 100), (1820, 980), (0, 165, 233), -1)
            cv2.putText(synthetic_frame, "1080P TEST STREAM", (500, 540), cv2.FONT_HERSHEY_SIMPLEX, 2.5, (255, 255, 255), 4)

            # Encode as JPEG
            success, enc_jpg = cv2.imencode(".jpg", synthetic_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            assert success, "JPEG encoding failed"

            # Send binary frame
            await ws.send_bytes(enc_jpg.tobytes())
            print(f"[PASS] Sent 1080p JPEG frame ({len(enc_jpg)} bytes)")

            # Give server 50ms to decode
            await asyncio.sleep(0.1)

            # Check server state
            with state.lock:
                assert state.client_connected, "State did not register client connection"
                assert state.width == 1920 and state.height == 1080, f"Expected 1920x1080, got {state.width}x{state.height}"
                assert state.latest_frame is not None, "Latest frame is None"
                print(f"[PASS] Server received and decoded 1080p frame! Resolution: {state.width}x{state.height}")

            await ws.close()
            print("[PASS] WebSocket closed gracefully")


def main():
    port = 8089
    state = AppState()
    state.virtual_cam_enabled = False # disable hardware virtual cam in test
    processor = VideoProcessor(state)
    server = StreamingServer(state, processor, host="127.0.0.1", port=port)

    loop = asyncio.new_event_loop()
    server_thread = threading.Thread(
        target=lambda: (asyncio.set_event_loop(loop), loop.run_until_complete(server.start()), loop.run_forever()),
        daemon=True
    )
    server_thread.start()
    time.sleep(0.5)

    try:
        client_loop = asyncio.new_event_loop()
        client_loop.run_until_complete(run_client_test(port, state))
        print("\n[SUCCESS] ALL TESTS PASSED! END-TO-END 1080P STREAMING CONFIRMED!")
    finally:
        processor.close()
        loop.call_soon_threadsafe(loop.stop)


if __name__ == "__main__":
    main()
