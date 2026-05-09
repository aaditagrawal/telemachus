package com.sidescreen.app

import android.content.Intent
import android.os.Bundle
import android.util.Log
import android.widget.Button
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExperimentalGetImage
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import com.google.mlkit.vision.barcode.BarcodeScannerOptions
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage

class QRScannerActivity : AppCompatActivity() {
    private val scanner by lazy {
        BarcodeScanning.getClient(
            BarcodeScannerOptions.Builder()
                .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
                .build(),
        )
    }
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
                    ImageAnalysis.Builder()
                        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                        .build()
                analyzer.setAnalyzer(ContextCompat.getMainExecutor(this), this::analyze)
                provider.unbindAll()
                provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, preview, analyzer)
            } catch (e: Exception) {
                Log.e(TAG, "Camera bind failed", e)
                Toast.makeText(this, "QR scanning unavailable on this device — use USB mode", Toast.LENGTH_LONG).show()
                finishCanceled()
            }
        }, ContextCompat.getMainExecutor(this))
    }

    @ExperimentalGetImage
    private fun analyze(proxy: ImageProxy) {
        val mediaImage = proxy.image
        if (mediaImage == null || alreadyDelivered) {
            proxy.close()
            return
        }
        val input = InputImage.fromMediaImage(mediaImage, proxy.imageInfo.rotationDegrees)
        scanner.process(input)
            .addOnSuccessListener { barcodes ->
                val raw = barcodes.firstOrNull { it.rawValue?.startsWith("sidescreen://") == true }?.rawValue
                if (raw != null && !alreadyDelivered) {
                    alreadyDelivered = true
                    val parsed = PairingURL.parse(raw)
                    if (parsed == null) {
                        Toast.makeText(this, "Invalid QR, expected SideScreen pairing code", Toast.LENGTH_SHORT).show()
                        alreadyDelivered = false
                    } else {
                        setResult(RESULT_OK, Intent().putExtra(EXTRA_URL, raw))
                        finish()
                    }
                }
            }
            .addOnFailureListener { e ->
                Log.e(TAG, "ML Kit scan error", e)
            }
            .addOnCompleteListener { proxy.close() }
    }

    private fun finishCanceled() {
        setResult(RESULT_CANCELED)
        finish()
    }

    companion object {
        private const val TAG = "QRScanner"
        const val EXTRA_URL = "qr_url"
    }
}
