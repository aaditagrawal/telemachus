package dev.telemachus.display

import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.widget.Button
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.NotFoundException
import com.google.zxing.PlanarYUVLuminanceSource
import com.google.zxing.common.HybridBinarizer
import com.google.zxing.qrcode.QRCodeReader
import java.util.concurrent.Executors

class QRScannerActivity : AppCompatActivity() {
    private val reader = QRCodeReader()
    private val analyzerExecutor = Executors.newSingleThreadExecutor()
    private val decodeHints =
        mapOf(
            DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE),
            DecodeHintType.TRY_HARDER to true,
        )
    private var alreadyDelivered = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_qr_scanner)
        findViewById<Button>(R.id.cancelButton).setOnClickListener { finishCanceled() }
        startCamera()
    }

    private fun startCamera() {
        val previewView = findViewById<PreviewView>(R.id.preview)
        val providerFuture = ProcessCameraProvider.getInstance(this)
        providerFuture.addListener({
            try {
                val provider = providerFuture.get()
                val preview =
                    Preview.Builder().build().also {
                        it.setSurfaceProvider(previewView.surfaceProvider)
                    }
                val analyzer =
                    ImageAnalysis
                        .Builder()
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build()
                analyzer.setAnalyzer(analyzerExecutor, this::analyze)
                provider.unbindAll()
                provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, preview, analyzer)
            } catch (e: Exception) {
                Log.e(TAG, "Camera bind failed", e)
                Toast
                    .makeText(this, R.string.qr_scanner_unavailable, Toast.LENGTH_LONG)
                    .show()
                finishCanceled()
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun analyze(proxy: ImageProxy) {
        if (alreadyDelivered) {
            proxy.close()
            return
        }
        try {
            val plane = proxy.planes.firstOrNull() ?: return
            val packed =
                packAndRotateLuma(
                    plane.buffer,
                    proxy.width,
                    proxy.height,
                    plane.rowStride,
                    plane.pixelStride,
                    proxy.imageInfo.rotationDegrees,
                )
            val source =
                PlanarYUVLuminanceSource(
                    packed.bytes,
                    packed.width,
                    packed.height,
                    0,
                    0,
                    packed.width,
                    packed.height,
                    false,
                )
            val raw = reader.decode(BinaryBitmap(HybridBinarizer(source)), decodeHints).text
            if (raw.startsWith("telemachus://")) {
                deliverResult(raw)
            }
        } catch (_: NotFoundException) {
            // Most camera frames do not contain a QR code.
        } catch (e: Exception) {
            Log.e(TAG, "QR scan error", e)
        } finally {
            reader.reset()
            proxy.close()
        }
    }

    private fun deliverResult(raw: String) {
        if (alreadyDelivered) return
        alreadyDelivered = true
        runOnUiThread {
            if (PairingURL.parse(raw) == null) {
                Toast.makeText(this, R.string.invalid_pairing_qr, Toast.LENGTH_SHORT).show()
                alreadyDelivered = false
            } else {
                setResult(RESULT_OK, Intent().putExtra(EXTRA_URL, raw))
                finish()
            }
        }
    }

    override fun onDestroy() {
        analyzerExecutor.shutdownNow()
        super.onDestroy()
    }

    private fun finishCanceled() {
        setResult(RESULT_CANCELED)
        finish()
    }

    companion object {
        private const val TAG = "QRScanner"
        const val EXTRA_URL = "qr_url"

        internal data class LumaImage(
            val bytes: ByteArray,
            val width: Int,
            val height: Int,
        )

        internal fun packAndRotateLuma(
            source: java.nio.ByteBuffer,
            width: Int,
            height: Int,
            rowStride: Int,
            pixelStride: Int,
            rotationDegrees: Int,
        ): LumaImage {
            require(width > 0 && height > 0)
            require(pixelStride > 0 && rowStride >= (width - 1) * pixelStride + 1)
            val packed = ByteArray(width * height)
            val buffer = source.duplicate()
            for (y in 0 until height) {
                for (x in 0 until width) {
                    packed[y * width + x] = buffer.get(y * rowStride + x * pixelStride)
                }
            }
            return rotateLuma(packed, width, height, rotationDegrees)
        }

        internal fun rotateLuma(
            source: ByteArray,
            width: Int,
            height: Int,
            rotationDegrees: Int,
        ): LumaImage {
            require(source.size == width * height)
            val normalized = ((rotationDegrees % 360) + 360) % 360
            if (normalized == 0) return LumaImage(source, width, height)
            require(normalized == 90 || normalized == 180 || normalized == 270)

            val targetWidth = if (normalized == 180) width else height
            val targetHeight = if (normalized == 180) height else width
            val target = ByteArray(source.size)
            for (y in 0 until height) {
                for (x in 0 until width) {
                    val sourceIndex = y * width + x
                    val targetIndex =
                        when (normalized) {
                            90 -> x * height + (height - 1 - y)
                            180 -> (height - 1 - y) * width + (width - 1 - x)
                            else -> (width - 1 - x) * height + y
                        }
                    target[targetIndex] = source[sourceIndex]
                }
            }
            return LumaImage(target, targetWidth, targetHeight)
        }
    }
}
