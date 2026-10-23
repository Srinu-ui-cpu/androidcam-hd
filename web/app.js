// AndroidCam HD - Client Streamer Engine

let mediaStream = null;
let videoTrack = null;
let currentFacingMode = 'environment'; // Default to Back Camera for best sensor quality
let ws = null;
let isStreaming = false;
let frameCount = 0;
let bytesSent = 0;
let lastFpsUpdateTime = performance.now();
let wakeLock = null;
let isAudioActive = false;
let audioMediaRecorder = null;
let isSendingFrame = false;

// DOM Elements
const video = document.getElementById('cameraPreview');
const canvas = document.getElementById('captureCanvas');
const ctx = canvas.getContext('2d', { willReadFrequently: true });
const toggleStreamBtn = document.getElementById('toggleStreamBtn');
const btnText = document.getElementById('btnText');
const switchCamBtn = document.getElementById('switchCamBtn');
const torchBtn = document.getElementById('torchBtn');
const batterySaverBtn = document.getElementById('batterySaverBtn');
const batterySaverOverlay = document.getElementById('batterySaverOverlay');
const audioToggleBtn = document.getElementById('audioToggleBtn');
const resolutionSelect = document.getElementById('resolutionSelect');
const fpsSelect = document.getElementById('fpsSelect');
const qualityRange = document.getElementById('qualityRange');
const qualityValText = document.getElementById('qualityValText');
const resDetailText = document.getElementById('resDetailText');
const fpsValText = document.getElementById('fpsValText');
const zoomRow = document.getElementById('zoomRow');
const zoomRange = document.getElementById('zoomRange');
const zoomValText = document.getElementById('zoomValText');
const statusBadge = document.getElementById('statusBadge');
const statusText = document.getElementById('statusText');
const fpsStat = document.getElementById('fpsStat');
const resStat = document.getElementById('resStat');
const bitrateStat = document.getElementById('bitrateStat');
const activeResBadge = document.getElementById('activeResBadge');
const serverHostInput = document.getElementById('serverHostInput');

// Initialize Default Host Input
serverHostInput.value = window.location.host;

// Quality slider change
qualityRange.addEventListener('input', (e) => {
  qualityValText.textContent = `${e.target.value}%`;
});

// Resolution select change
resolutionSelect.addEventListener('change', async (e) => {
  const [w, h] = e.target.value.split('x');
  resDetailText.textContent = `${w} × ${h}`;
  activeResBadge.textContent = h === '1080' ? '1080P' : (h === '2160' ? '4K' : `${h}P`);
  if (isStreaming) {
    await restartCamera();
  }
});

// FPS select change
fpsSelect.addEventListener('change', async (e) => {
  fpsValText.textContent = `${e.target.value} FPS`;
  if (isStreaming) {
    await restartCamera();
  }
});

// Switch Camera (Front / Back)
switchCamBtn.addEventListener('click', async () => {
  currentFacingMode = currentFacingMode === 'environment' ? 'user' : 'environment';
  if (isStreaming) {
    await restartCamera();
  }
});

// Battery Saver Mode
batterySaverBtn.addEventListener('click', () => {
  batterySaverOverlay.classList.add('active');
});

batterySaverOverlay.addEventListener('click', () => {
  batterySaverOverlay.classList.remove('active');
});

// Torch / Flashlight Toggle
let torchOn = false;
torchBtn.addEventListener('click', async () => {
  if (!videoTrack) return;
  const capabilities = videoTrack.getCapabilities ? videoTrack.getCapabilities() : {};
  if (capabilities.torch) {
    torchOn = !torchOn;
    try {
      await videoTrack.applyConstraints({
        advanced: [{ torch: torchOn }]
      });
      torchBtn.classList.toggle('active', torchOn);
    } catch (err) {
      console.warn("Could not toggle torch:", err);
    }
  } else {
    alert("Flashlight/Torch is not supported by this camera sensor.");
  }
});

// Hardware Zoom Slider
zoomRange.addEventListener('input', async (e) => {
  const val = parseFloat(e.target.value);
  zoomValText.textContent = `${val.toFixed(1)}x`;
  if (videoTrack && videoTrack.getCapabilities && videoTrack.getCapabilities().zoom) {
    try {
      await videoTrack.applyConstraints({
        advanced: [{ zoom: val }]
      });
    } catch (err) {
      console.warn("Could not apply zoom:", err);
    }
  }
});

// Screen WakeLock to prevent tablet from sleeping
async function requestWakeLock() {
  if ('wakeLock' in navigator) {
    try {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => {
        wakeLock = null;
      });
    } catch (err) {
      console.log('WakeLock error:', err);
    }
  }
}

function releaseWakeLock() {
  if (wakeLock) {
    wakeLock.release().catch(() => {});
    wakeLock = null;
  }
}

// Start / Stop Streaming
toggleStreamBtn.addEventListener('click', async () => {
  if (isStreaming) {
    stopStreaming();
  } else {
    await startStreaming();
  }
});

async function startStreaming() {
  toggleStreamBtn.disabled = true;
  btnText.textContent = "Connecting...";

  try {
    // 1. Initialize Camera Stream
    await initCamera();

    // 2. Connect WebSocket to Windows Receiver
    connectWebSocket();

    // 3. Request WakeLock
    await requestWakeLock();

    isStreaming = true;
    toggleStreamBtn.disabled = false;
    toggleStreamBtn.classList.add('streaming');
    btnText.textContent = "Stop Streaming";
  } catch (err) {
    console.error("Failed to start stream:", err);
    alert(`Could not start camera: ${err.message || err}`);
    stopStreaming();
  }
}

function stopStreaming() {
  isStreaming = false;
  if (mediaStream) {
    mediaStream.getTracks().forEach(track => track.stop());
    mediaStream = null;
  }
  videoTrack = null;

  if (ws) {
    ws.close();
    ws = null;
  }

  releaseWakeLock();

  toggleStreamBtn.disabled = false;
  toggleStreamBtn.classList.remove('streaming');
  btnText.textContent = `Start Streaming (${resolutionSelect.value.split('x')[1]}p)`;
  statusBadge.className = 'status-badge';
  statusText.textContent = 'Disconnected';
  fpsStat.textContent = '0';
  bitrateStat.textContent = '0 KB/s';
}

async function restartCamera() {
  if (mediaStream) {
    mediaStream.getTracks().forEach(track => track.stop());
  }
  await initCamera();
}

async function initCamera() {
  const [targetWidth, targetHeight] = resolutionSelect.value.split('x').map(Number);
  const targetFps = parseInt(fpsSelect.value, 10);

  const constraints = {
    video: {
      facingMode: currentFacingMode,
      width: { ideal: targetWidth },
      height: { ideal: targetHeight },
      frameRate: { ideal: targetFps }
    },
    audio: isAudioActive
  };

  mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
  video.srcObject = mediaStream;
  await video.play();

  videoTrack = mediaStream.getVideoTracks()[0];
  const settings = videoTrack.getSettings ? videoTrack.getSettings() : {};
  const actualW = settings.width || video.videoWidth || targetWidth;
  const actualH = settings.height || video.videoHeight || targetHeight;

  canvas.width = actualW;
  canvas.height = actualH;
  resStat.textContent = `${actualW}x${actualH}`;

  // Check hardware capabilities (Torch & Zoom)
  if (videoTrack.getCapabilities) {
    const caps = videoTrack.getCapabilities();
    if (caps.zoom) {
      zoomRow.style.display = 'block';
      zoomRange.min = caps.zoom.min || 1;
      zoomRange.max = caps.zoom.max || 5;
      zoomRange.step = caps.zoom.step || 0.1;
      zoomRange.value = settings.zoom || 1;
      zoomValText.textContent = `${zoomRange.value}x`;
    } else {
      zoomRow.style.display = 'none';
    }
  }

  // Start sending loop
  scheduleNextFrame();
}

// WebSocket Connection
function connectWebSocket() {
  const host = serverHostInput.value.trim() || window.location.host;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${host}/ws`;

  statusText.textContent = "Connecting to Receiver...";

  ws = new WebSocket(wsUrl);
  ws.binaryType = 'arraybuffer';

  ws.onopen = () => {
    statusBadge.className = 'status-badge connected';
    statusText.textContent = 'Streaming Live';
    // Send client metadata
    const meta = {
      type: 'client_hello',
      device: navigator.userAgent,
      resolution: `${canvas.width}x${canvas.height}`,
      facing: currentFacingMode
    };
    ws.send(JSON.stringify(meta));
  };

  ws.onclose = () => {
    statusBadge.className = 'status-badge';
    statusText.textContent = 'Disconnected';
    if (isStreaming) {
      // Auto-reconnect after 2 seconds
      setTimeout(() => {
        if (isStreaming) connectWebSocket();
      }, 2000);
    }
  };

  ws.onerror = (err) => {
    console.warn("WebSocket error:", err);
  };
}

// Low-Latency Frame Capture & Send Loop
let lastFrameTime = 0;

function scheduleNextFrame() {
  if (!isStreaming) return;

  if ('requestVideoFrameCallback' in video) {
    video.requestVideoFrameCallback(processAndSendVideoFrame);
  } else {
    requestAnimationFrame(processAndSendVideoFrame);
  }
}

function processAndSendVideoFrame(now, metadata) {
  if (!isStreaming) return;

  const targetFps = parseInt(fpsSelect.value, 10);
  const minInterval = 1000 / (targetFps + 2); // Small threshold for jitter

  const currentTime = performance.now();
  if (currentTime - lastFrameTime >= minInterval) {
    lastFrameTime = currentTime;

    // Zero-latency flow control:
    // If the websocket connection is busy or previous frame is compressing,
    // skip this frame to prevent buffer bloat and lag!
    if (ws && ws.readyState === WebSocket.OPEN && ws.bufferedAmount < 65536 && !isSendingFrame) {
      isSendingFrame = true;

      // Draw active video frame to canvas
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

      const quality = parseInt(qualityRange.value, 10) / 100;
      canvas.toBlob((blob) => {
        if (!blob) {
          isSendingFrame = false;
          scheduleNextFrame();
          return;
        }

        blob.arrayBuffer().then((buffer) => {
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(buffer);
            frameCount++;
            bytesSent += buffer.byteLength;
          }
          isSendingFrame = false;
        }).catch(() => {
          isSendingFrame = false;
        });
      }, 'image/jpeg', quality);
    }
  }

  // Update FPS and Bitrate Stats every 1 second
  if (currentTime - lastFpsUpdateTime >= 1000) {
    const elapsedSec = (currentTime - lastFpsUpdateTime) / 1000;
    const currentFps = Math.round(frameCount / elapsedSec);
    const kbps = Math.round((bytesSent / 1024) / elapsedSec);

    fpsStat.textContent = currentFps;
    bitrateStat.textContent = `${kbps} KB/s`;

    frameCount = 0;
    bytesSent = 0;
    lastFpsUpdateTime = currentTime;
  }

  scheduleNextFrame();
}

// Audio Toggle Button
audioToggleBtn.addEventListener('click', async () => {
  isAudioActive = !isAudioActive;
  audioToggleBtn.classList.toggle('active', isAudioActive);
  audioToggleBtn.innerHTML = isAudioActive
    ? `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/></svg> Audio: On`
    : `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="1" y1="1" x2="23" y2="23"/><path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6"/></svg> Audio: Off`;

  if (isStreaming) {
    await restartCamera();
  }
});
