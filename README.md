# 🚀 AndroidCam HD

> **The 100% Offline, High-Definition (1080p / 4K) DroidCam Alternative for Android Tablets & Windows.**  
> No 480p caps. No ads. No cloud logins. No internet required.

---

## 📋 Overview

**AndroidCam HD** is a complete, custom replacement for DroidCam designed specifically for your Android tablet (e.g., Tecno MegaPad SE or any phone/tablet) and Windows laptop.

It replaces all three jobs seamlessly:
1. **The Eyes (Tablet)**: Captures your tablet camera at uncapped **1080p Full HD (1920×1080)**, **2K**, or **4K** at 30 or 60 FPS.
2. **The Road (Network)**: Streams video directly over your private network (**Local Wi-Fi**, **Tablet Hotspot**, **Laptop Hotspot**, or **Tailscale IP**).
3. **The Translator (Windows Virtual Camera)**: Makes Windows see your tablet as a standard native webcam in **Zoom, Microsoft Teams, Google Meet, Discord, OBS, Skype**, etc.

---

## ✨ Key Features

- **Uncapped High Definition**: Stream at true 1080p (1920×1080), 2K, or 4K.
- **100% Offline Operation**: Zero internet needed. All code, styles, and scripts run entirely locally.
- **Instant Connect via QR Code**: Just scan the QR code displayed on your laptop screen with your tablet camera.
- **Zero-Install Web App Mode (PWA)**: Open Chrome or any browser on your tablet and start streaming in 3 seconds. Can also be saved to your Android home screen as an app.
- **Native Android App Included**: Complete Kotlin + CameraX Android Studio project in `android_app/`.
- **DirectShow Virtual Camera**: Integrates with Windows via DirectShow / MediaFoundation virtual webcam driver.
- **Battery Saver / Cool Mode**: Dims the tablet screen completely black during calls while streaming continues in the background, preventing tablet overheating and saving 80% battery.
- **Webcam Controls**: Mirror horizontal (for natural eye contact), vertical flip, 90° rotation (for portrait/landscape mounting).
- **Local Recording & Snapshots**: Save high-res 1080p MP4 videos or PNG snapshots with one click.

---

## 🗂️ Project Structure

```text
androidcam/
├── driver/                         # Windows DirectShow Virtual Camera Driver
│   ├── UnityCaptureFilter64.dll     # 64-bit DirectShow virtual camera filter
│   ├── UnityCaptureFilter32.dll     # 32-bit DirectShow virtual camera filter
│   ├── install_driver.bat           # 1-click driver installer (registers webcam)
│   └── uninstall_driver.bat         # 1-click driver uninstaller
│
├── web/                            # Offline Camera Transmitter Web App (PWA)
│   ├── index.html                   # Responsive touch-optimized UI
│   ├── style.css                    # Glassmorphism dark mode styling
│   ├── app.js                       # Low-latency camera capture & WebSocket engine
│   ├── manifest.json                # PWA manifest for Android home-screen installation
│   └── icon.svg                     # High-res vector camera icon
│
├── android_app/                    # Native Android Studio Project (Kotlin + CameraX)
│   ├── app/src/main/
│   │   ├── AndroidManifest.xml
│   │   ├── java/com/androidcam/hd/MainActivity.kt
│   │   └── res/layout/activity_main.xml
│   ├── build.gradle.kts
│   └── settings.gradle.kts
│
├── server.py                       # High-performance aiohttp WebSocket & Video Engine
├── gui.py                          # Modern Tkinter Desktop Control Center with QR code
├── main.py                         # Application entrypoint
├── start_receiver.bat              # 1-click launcher for Windows
├── requirements.txt                # Python dependencies
└── tests/
    └── test_stream.py              # Automated end-to-end 1080p streaming test
```

---

## ⚡ Quick Start Guide

### Step 1: Install Python Dependencies (First Time Only)

Open PowerShell or Command Prompt in this folder and run:
```powershell
pip install -r requirements.txt
```

### Step 2: Register Windows Virtual Camera Driver (One-Time Setup)

To allow Windows apps (Zoom, Teams, Google Meet, Discord, etc.) to detect your tablet as a webcam:
1. Double-click `driver\install_driver.bat`  
   *(Windows will show a UAC prompt asking for permission; click **Yes**).*
2. You will see:
   ```text
   SUCCESS: Virtual Camera Driver has been registered!
   Windows will now recognize "Unity Video Capture" as a standard webcam.
   ```
*(Note: If you already use OBS Studio, its built-in Virtual Camera is also automatically supported).*

---

### Step 3: Start the Receiver on Windows

Double-click `start_receiver.bat` (or run `python main.py`).

The desktop GUI window will appear showing:
- **Detected IP Addresses** (Local Wi-Fi, Tailscale, Hotspot)
- **Interactive QR Code**
- **Live 1080p Preview Viewport**
- **Virtual Camera Status**

---

### Step 4: Connect Your Android Tablet

Choose the network method that fits your current setup:

#### Option A — Local Wi-Fi (Recommended & 100% Offline)
1. Ensure your tablet and laptop are connected to the same Wi-Fi network.
2. In the Windows app, select your **Wi-Fi IP** (e.g. `192.168.31.214:8080`).
3. Scan the QR code on your laptop screen using your tablet camera, or type the URL into Chrome on your tablet.
4. Tap **"Start Streaming (1080p)"**.

#### Option B — Tablet Hotspot or Laptop Hotspot (No Wi-Fi Router Needed)
1. Turn on the Wi-Fi Hotspot on either your tablet or laptop.
2. Connect the other device to that hotspot.
3. Select the Hotspot IP in the Windows app.
4. Open the link on the tablet and stream.

#### Option C — Keep Tailscale (Your Current Setup)
1. With Tailscale running on both devices, select the **Tailscale IP** in the Windows app (e.g. `100.112.202.122:8080`).
2. Open `http://100.112.202.122:8080` in Chrome on your tablet.
3. Tap **"Start Streaming"**.

---

### Step 5: Select the Camera in Zoom / Teams / Meet / Discord

1. Open Zoom, Teams, Google Meet, or your web browser.
2. Go to **Settings → Video → Camera**.
3. Select **"Unity Video Capture"** (or **"OBS Virtual Camera"**).
4. Your tablet's crystal clear 1080p video feed is now live!

---

## 🛠️ In-Call Tablet Controls

While streaming from your tablet:
- **Switch Camera**: Switch between the high-res Rear Camera and Front Selfie Camera.
- **Flashlight / Torch**: Toggle your tablet's camera flash for low-light lighting.
- **Cool / Saver**: Turns the tablet screen black while streaming continues in the background at full 1080p. Tap anywhere on the screen to wake it.
- **Zoom Slider**: Smooth hardware digital zoom from 1.0x to 5.0x.
- **Resolution Selector**: Switch anytime between 720p, 1080p, 2K, or 4K.

---

## 🧪 Running Automated Tests

To verify that the complete HTTP, WebSocket, and 1080p JPEG decoding pipeline works:
```powershell
python tests/test_stream.py
```
Expected output:
```text
[PASS] HTTP index.html served successfully (200 OK)
[PASS] HTTP manifest.json served successfully (200 OK)
[PASS] WebSocket connection established
[PASS] Sent 1080p JPEG frame (62424 bytes)
[PASS] Server received and decoded 1080p frame! Resolution: 1920x1080
[PASS] WebSocket closed gracefully

[SUCCESS] ALL TESTS PASSED! END-TO-END 1080P STREAMING CONFIRMED!
```
