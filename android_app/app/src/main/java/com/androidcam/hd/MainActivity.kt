package com.androidcam.hd

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Rect
import android.graphics.YuvImage
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

    private var cameraProvider: ProcessCameraProvider? = null
    private var lensFacing = CameraSelector.LENS_FACING_BACK
    private var cameraExecutor: ExecutorService = Executors.newSingleThreadExecutor()

    private var client: OkHttpClient = OkHttpClient()
    private var webSocket: WebSocket? = null
    private var isStreaming = false
    private var isSendingFrame = false

    private var frameCount = 0
    private var lastFpsTimestamp = System.currentTimeMillis()

    companion object {
        private const val REQUEST_CODE_PERMISSIONS = 10
        private val REQUIRED_PERMISSIONS = arrayOf(Manifest.permission.CAMERA)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(R.layout.activity_main)

        viewFinder = findViewById(R.id.viewFinder)
        statusText = findViewById(R.id.statusText)
        fpsText = findViewById(R.id.fpsText)
        ipAddressInput = findViewById(R.id.ipAddressInput)
        switchCameraBtn = findViewById(R.id.switchCameraBtn)
        startStopBtn = findViewById(R.id.startStopBtn)

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

            // 1080p Image Analysis Pipeline (No 480p cap!)
            val imageAnalysis = ImageAnalysis.Builder()
                .setTargetResolution(Size(1920, 1080))
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_YUV_420_888)
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

        isSendingFrame = true
        try {
            val jpegBytes = yuv420ToJpeg(imageProxy, 85)
            if (jpegBytes != null && webSocket != null) {
                webSocket?.send(jpegBytes.toByteString())
                frameCount++

                val now = System.currentTimeMillis()
                if (now - lastFpsTimestamp >= 1000) {
                    val currentFps = frameCount
                    frameCount = 0
                    lastFpsTimestamp = now
                    runOnUiThread {
                        fpsText.text = "1080p @ $currentFps FPS"
                    }
                }
            }
        } catch (e: Exception) {
            e.printStackTrace()
        } finally {
            imageProxy.close()
            isSendingFrame = false
        }
    }

    private fun yuv420ToJpeg(image: ImageProxy, quality: Int): ByteArray? {
        val planes = image.planes
        val yBuffer = planes[0].buffer
        val uBuffer = planes[1].buffer
        val vBuffer = planes[2].buffer

        val ySize = yBuffer.remaining()
        val uSize = uBuffer.remaining()
        val vSize = vBuffer.remaining()

        val nv21 = ByteArray(ySize + uSize + vSize)
        yBuffer.get(nv21, 0, ySize)
        vBuffer.get(nv21, ySize, vSize)
        uBuffer.get(nv21, ySize + vSize, uSize)

        val yuvImage = YuvImage(nv21, android.graphics.ImageFormat.NV21, image.width, image.height, null)
        val out = ByteArrayOutputStream()
        yuvImage.compressToJpeg(Rect(0, 0, image.width, image.height), quality, out)
        return out.toByteArray()
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
