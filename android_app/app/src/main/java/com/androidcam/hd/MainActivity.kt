package com.androidcam.hd

import android.Manifest
import android.content.Context
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Matrix
import android.os.Build
import android.os.Bundle
import android.util.Size
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.*
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import okhttp3.*
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class MainActivity : AppCompatActivity() {

    private lateinit var viewFinder: PreviewView
    private lateinit var statusText: TextView
    private lateinit var fpsText: TextView
    private lateinit var ipAddressInput: EditText
    private lateinit var switchCameraBtn: Button
    private lateinit var startStopBtn: Button
    private lateinit var prefs: SharedPreferences

    private var cameraProvider: ProcessCameraProvider? = null
    private var lensFacing = CameraSelector.LENS_FACING_BACK
    private var cameraExecutor: ExecutorService = Executors.newSingleThreadExecutor()

    private var client: OkHttpClient = OkHttpClient.Builder().retryOnConnectionFailure(true).build()
    private var webSocket: WebSocket? = null
    private var isStreaming = false
    private var isSendingFrame = false

    private val compressStream = ByteArrayOutputStream(256 * 1024)
    private var currentLatencyMs = 0L

    private var frameCount = 0
    private var lastFpsTimestamp = System.currentTimeMillis()

    companion object {
        private const val REQUEST_CODE_PERMISSIONS = 10
        private val REQUIRED_PERMISSIONS = arrayOf(Manifest.permission.CAMERA)
        private const val PREFS_NAME = "AndroidCamPrefs"
        private const val KEY_LAST_IP = "last_ip"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(R.layout.activity_main)

        prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

        viewFinder = findViewById(R.id.viewFinder)
        statusText = findViewById(R.id.statusText)
        fpsText = findViewById(R.id.fpsText)
        ipAddressInput = findViewById(R.id.ipAddressInput)
        switchCameraBtn = findViewById(R.id.switchCameraBtn)
        startStopBtn = findViewById(R.id.startStopBtn)

        // Restore last entered IP or default
        val savedIp = prefs.getString(KEY_LAST_IP, "")
        if (!savedIp.isNullOrEmpty()) {
            ipAddressInput.setText(savedIp)
        }

        // Preset buttons if available
        findViewById<Button?>(R.id.btnPresetWifi)?.setOnClickListener {
            ipAddressInput.setText("192.168.31.214:8080")
        }
        findViewById<Button?>(R.id.btnPresetTailscale)?.setOnClickListener {
            ipAddressInput.setText("100.112.202.122:8080")
        }

        if (allPermissionsGranted()) {
            startCamera()
        } else {
            ActivityCompat.requestPermissions(this, REQUIRED_PERMISSIONS, REQUEST_CODE_PERMISSIONS)
        }

        switchCameraBtn.setOnClickListener {
            lensFacing = if (lensFacing == CameraSelector.LENS_FACING_BACK) {
                CameraSelector.LENS_FACING_FRONT
            } else {
                CameraSelector.LENS_FACING_BACK
            }
            startCamera()
        }

        startStopBtn.setOnClickListener {
            if (isStreaming) {
                stopStreaming()
            } else {
                val ip = ipAddressInput.text.toString().trim()
                if (ip.isEmpty()) {
                    Toast.makeText(this, "Please enter your laptop IP", Toast.LENGTH_SHORT).show()
                    return@setOnClickListener
                }
                // Save IP for next time
                prefs.edit().putString(KEY_LAST_IP, ip).apply()
                startStreaming(ip)
            }
        }
    }

    private fun startCamera() {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(this)
        cameraProviderFuture.addListener({
            cameraProvider = cameraProviderFuture.get()

            val preview = Preview.Builder().build().also {
                it.setSurfaceProvider(viewFinder.surfaceProvider)
            }

            // High Resolution 1080p Image Analysis Pipeline (Full HD, 16:9, Zero-Lag)
            val imageAnalysis = ImageAnalysis.Builder()
                .setTargetResolution(Size(1920, 1080))
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .setImageQueueDepth(1)
                .build()

            imageAnalysis.setAnalyzer(cameraExecutor) { imageProxy ->
                processImageProxy(imageProxy)
            }

            val cameraSelector = CameraSelector.Builder().requireLensFacing(lensFacing).build()

            try {
                cameraProvider?.unbindAll()
                cameraProvider?.bindToLifecycle(this, cameraSelector, preview, imageAnalysis)
            } catch (exc: Exception) {
                Toast.makeText(this, "Failed to start camera: ${exc.message}", Toast.LENGTH_SHORT).show()
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun processImageProxy(imageProxy: ImageProxy) {
        if (!isStreaming || webSocket == null || isSendingFrame) {
            imageProxy.close()
            return
        }

        // ZERO-LAG BUFFER CHECK:
        // If OkHttp has any queued bytes in the network socket buffer, skip this frame immediately!
        // Never allow a backlog of frames to accumulate over Wi-Fi or Tailscale.
        val queueSize = webSocket?.queueSize() ?: 0L
        if (queueSize > 0L) {
            imageProxy.close()
            return
        }

        isSendingFrame = true
        try {
            // CameraX built-in toBitmap() automatically resolves all YUV row strides and padding
            val rawBitmap = imageProxy.toBitmap()
            val rotationDegrees = imageProxy.imageInfo.rotationDegrees

            // Orient image correctly based on device sensor orientation
            val bitmapToSend = if (rotationDegrees != 0) {
                val matrix = Matrix().apply { postRotate(rotationDegrees.toFloat()) }
                val rotated = Bitmap.createBitmap(rawBitmap, 0, 0, rawBitmap.width, rawBitmap.height, matrix, true)
                rawBitmap.recycle()
                rotated
            } else {
                rawBitmap
            }

            // High-speed low-latency compression (75% quality is crystal clear 1080p, but 1/5th the payload of 88%)
            synchronized(compressStream) {
                compressStream.reset()
                bitmapToSend.compress(Bitmap.CompressFormat.JPEG, 75, compressStream)
                val jpegBytes = compressStream.toByteArray()

                if (webSocket != null) {
                    webSocket?.send(jpegBytes.toByteString())
                    frameCount++

                    val now = System.currentTimeMillis()
                    if (now - lastFpsTimestamp >= 1000) {
                        val currentFps = frameCount
                        frameCount = 0
                        lastFpsTimestamp = now
                        val w = bitmapToSend.width
                        val h = bitmapToSend.height
                        runOnUiThread {
                            fpsText.text = "${w}x${h} @ $currentFps FPS | Lag: ${currentLatencyMs}ms"
                        }

                        // Send lightweight ping to measure exact round-trip latency
                        val ping = JSONObject().apply {
                            put("type", "ping")
                            put("time", now)
                        }
                        webSocket?.send(ping.toString())
                    }
                }
            }

            if (bitmapToSend != rawBitmap) {
                bitmapToSend.recycle()
            }
        } catch (e: Exception) {
            e.printStackTrace()
        } finally {
            imageProxy.close()
            isSendingFrame = false
        }
    }

    private fun startStreaming(host: String) {
        val url = if (host.startsWith("ws://") || host.startsWith("wss://")) host else "ws://$host/ws"
        val request = Request.Builder().url(url).build()

        statusText.text = "Connecting..."
        statusText.setTextColor(0xFFFBBF24.toInt())

        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                runOnUiThread {
                    isStreaming = true
                    startStopBtn.text = "Stop Streaming"
                    startStopBtn.setBackgroundColor(0xFFDC2626.toInt())
                    statusText.text = "Streaming Live (1080p)"
                    statusText.setTextColor(0xFF10B981.toInt())
                }

                val hello = JSONObject().apply {
                    put("type", "client_hello")
                    put("device", "${Build.MANUFACTURER} ${Build.MODEL}")
                    put("resolution", "1920x1080")
                }
                webSocket.send(hello.toString())
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val json = JSONObject(text)
                    if (json.optString("type") == "pong") {
                        val sentTime = json.optLong("time", 0L)
                        if (sentTime > 0) {
                            val rtt = System.currentTimeMillis() - sentTime
                            currentLatencyMs = rtt
                        }
                    }
                } catch (e: Exception) {}
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                runOnUiThread {
                    stopStreaming()
                    Toast.makeText(this@MainActivity, "Connection failed: ${t.message}", Toast.LENGTH_SHORT).show()
                }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                runOnUiThread {
                    stopStreaming()
                }
            }
        })
    }

    private fun stopStreaming() {
        isStreaming = false
        webSocket?.close(1000, "User stopped")
        webSocket = null
        startStopBtn.text = "Start 1080p Streaming"
        startStopBtn.setBackgroundColor(0xFF0EA5E9.toInt())
        statusText.text = "Disconnected"
        statusText.setTextColor(0xFFEF4444.toInt())
    }

    private fun allPermissionsGranted() = REQUIRED_PERMISSIONS.all {
        ContextCompat.checkSelfPermission(baseContext, it) == PackageManager.PERMISSION_GRANTED
    }

    override fun onDestroy() {
        super.onDestroy()
        stopStreaming()
        cameraExecutor.shutdown()
    }
}
