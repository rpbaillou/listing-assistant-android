package com.rpbdesign.listingassistant

import android.content.Context
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CaptureRequest
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.camera2.interop.Camera2Interop
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import java.io.File

/**
 * v44: the in-app camera (CameraX). The Take photo button used to hand
 * off to the Pixel camera app, whose review screen makes him tap a
 * check after every shot — slow, and one photo per round trip. This
 * camera stays open: each shutter tap saves a photo immediately, no
 * review screen, no check. Photos hand to MainActivity one by one
 * through onPhoto, which downscales and forwards them to the WebView
 * exactly like the old camera-app result.
 *
 * Kept separate from MainActivity so the CameraX binding is one small
 * unit: MainActivity owns the overlay UI, the permission, and the
 * handoff; this class only binds the preview + capture and takes
 * pictures. No AndroidX Activity base class is needed: MainActivity
 * is a plain LifecycleOwner via a LifecycleRegistry.
 *
 * v52: optical stabilization is requested explicitly (Camera2 interop,
 * LENS_OPTICAL_STABILIZATION_MODE ON) when the back camera reports it
 * supports it. That is the lens hardware steadying the shot — it is NOT
 * video stabilization (EIS is a video feature and does not apply to a
 * still capture), and on this Pixel the lens may already steady by
 * default; the request makes it explicit and visible in code instead of
 * assumed. When the camera does not report OIS, or the bind rejects the
 * request, the camera binds exactly as before — no crash, no blocked
 * capture. Capture mode stays MINIMIZE_LATENCY: the fast shutter for
 * rapid multi-shot is unchanged.
 */
class InAppCamera(private val context: Context) {

    private var imageCapture: ImageCapture? = null
    private var cameraProvider: ProcessCameraProvider? = null
    private var capturing = false

    /** True while a capture is in flight (the shutter is ignored then). */
    fun isCapturing(): Boolean = capturing

    /**
     * Binds the preview to the given view and prepares capture. Calls
     * onReady on the main thread when the camera is live, onError with
     * a plain message when it cannot start (MainActivity then falls
     * back to the camera app). Safe to call again after stop().
     *
     * v52: optical stabilization is requested when the back camera
     * reports it (see opticalStabilizationAvailable). If the bind
     * rejects the OIS request, it is retried once without OIS before
     * giving up — the camera must never fail to open over a
     * stabilization option.
     */
    fun start(
        lifecycleOwner: LifecycleOwner,
        previewView: PreviewView,
        onReady: () -> Unit,
        onError: (String) -> Unit
    ) {
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            try {
                val provider = future.get()
                cameraProvider = provider
                val oisOn = opticalStabilizationAvailable(provider)
                try {
                    bindUseCases(provider, lifecycleOwner, previewView, oisOn)
                } catch (e: Exception) {
                    if (oisOn) {
                        // The OIS request itself was rejected at bind time.
                        // Retry plain; a stabilization option must never
                        // cost him the camera.
                        bindUseCases(provider, lifecycleOwner, previewView, false)
                    } else {
                        throw e
                    }
                }
                onReady()
            } catch (_: Exception) {
                onError("Couldn't start the camera.")
            }
        }, ContextCompat.getMainExecutor(context))
    }

    /**
     * v52: true when the back camera reports optical stabilization ON
     * as an available mode. Never throws — any failure to read the
     * characteristics means "no OIS", not a crash.
     */
    private fun opticalStabilizationAvailable(provider: ProcessCameraProvider): Boolean {
        return try {
            val info = Camera2CameraInfo.from(
                provider.getCameraInfo(CameraSelector.DEFAULT_BACK_CAMERA)
            )
            val modes = info.getCameraCharacteristic(
                CameraCharacteristics.LENS_INFO_AVAILABLE_OPTICAL_STABILIZATION
            )
            modes?.contains(CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE_ON) == true
        } catch (_: Exception) {
            false
        }
    }

    /**
     * v52: builds the preview + capture use cases and binds them.
     * When [oisOn] is true, both use cases carry an explicit request
     * for optical stabilization (Camera2 interop). Capture mode is
     * always MINIMIZE_LATENCY — the fast shutter stays.
     */
    private fun bindUseCases(
        provider: ProcessCameraProvider,
        lifecycleOwner: LifecycleOwner,
        previewView: PreviewView,
        oisOn: Boolean
    ) {
        val previewBuilder = Preview.Builder()
        val captureBuilder = ImageCapture.Builder()
            .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
        if (oisOn) {
            Camera2Interop.Extender(previewBuilder).setCaptureRequestOption(
                CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE,
                CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE_ON
            )
            Camera2Interop.Extender(captureBuilder).setCaptureRequestOption(
                CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE,
                CaptureRequest.LENS_OPTICAL_STABILIZATION_MODE_ON
            )
        }
        val preview = previewBuilder.build().also {
            it.setSurfaceProvider(previewView.surfaceProvider)
        }
        imageCapture = captureBuilder.build()
        provider.unbindAll()
        provider.bindToLifecycle(
            lifecycleOwner,
            CameraSelector.DEFAULT_BACK_CAMERA,
            preview,
            imageCapture
        )
    }

    /**
     * Saves one photo to [file] and calls onPhoto with it, or onError
     * with a plain message. While a capture is in flight the shutter
     * is ignored, so two taps can never write the same file.
     */
    fun takePhoto(file: File, onPhoto: (File) -> Unit, onError: (String) -> Unit) {
        val capture = imageCapture
        if (capture == null || capturing) return
        capturing = true
        val options = ImageCapture.OutputFileOptions.Builder(file).build()
        capture.takePicture(
            options,
            ContextCompat.getMainExecutor(context),
            object : ImageCapture.OnImageSavedCallback {
                override fun onImageSaved(output: ImageCapture.OutputFileResults) {
                    capturing = false
                    onPhoto(file)
                }

                override fun onError(exc: ImageCaptureException) {
                    capturing = false
                    onError("Couldn't save that photo. Try again.")
                }
            }
        )
    }

    /** Unbinds everything. Safe to call when nothing is bound. */
    fun stop() {
        capturing = false
        try {
            cameraProvider?.unbindAll()
        } catch (_: Exception) {
            // Already unbound or never started; nothing to do.
        }
        cameraProvider = null
        imageCapture = null
    }
}
