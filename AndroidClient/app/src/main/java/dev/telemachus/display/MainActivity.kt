package dev.telemachus.display

import android.annotation.SuppressLint
import android.app.Dialog
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ActivityInfo
import android.graphics.Color
import android.graphics.drawable.ColorDrawable
import android.media.MediaFormat
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.MotionEvent
import android.view.SurfaceHolder
import android.view.View
import android.view.Window
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.constraintlayout.widget.ConstraintSet
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.android.material.slider.Slider
import com.google.android.material.switchmaterial.SwitchMaterial
import dev.telemachus.display.databinding.ActivityMainBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.net.InetSocketAddress
import java.net.Socket
import java.util.Locale

private fun mainDiag(msg: String) = DiagLog.log("MA", msg)

class MainActivity : AppCompatActivity() {
    private lateinit var wirelessController: WirelessTabController
    private val pairedHostStorage by lazy { PairedHostStorage(this) }
    private val cameraPerm by lazy { CameraPermissionManager(this) }
    private lateinit var binding: ActivityMainBinding
    private lateinit var prefs: PreferencesManager
    private var videoDecoder: VideoDecoder? = null
    private var streamClient: StreamClient? = null
    private var currentSurfaceHolder: SurfaceHolder? = null
    private var displayWidth = 0 // 0 = no config received yet
    private var displayHeight = 0 // 0 = no config received yet
    private var displayRotation = 0 // 0, 90, 180, 270 degrees
    private var pingJob: kotlinx.coroutines.Job? = null

    // For dragging stats overlay
    private var isDraggingOverlay = false
    private var overlayDx = 0f
    private var overlayDy = 0f

    // Input prediction for low-latency gaming
    private val inputPredictor = InputPredictor()

    // Checklist status handler
    private val checklistHandler = Handler(Looper.getMainLooper())
    private var checklistRunnable: Runnable? = null
    private var isConnected = false // Track connection state to prevent checklist conflicts
    private var connectionAttemptInProgress = false
    private var automaticUsbConnect = false
    private var connectionDetailsVisible = false
    private val autoConnectHandler = Handler(Looper.getMainLooper())
    private val autoConnectRunnable =
        Runnable {
            if (automaticUsbConnect && !isConnected && !connectionAttemptInProgress) {
                connect("127.0.0.1", currentUsbPort(), automatic = true)
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        DiagLog.init(applicationContext)
        prefs = PreferencesManager(this)

        // Allow rotation based on device sensor when not connected
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_FULL_SENSOR

        // Enable edge-to-edge display (draw behind system bars and cutout)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            window.attributes.layoutInDisplayCutoutMode =
                WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }

        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // Apply fullscreen mode immediately
        enableFullscreenMode()

        setupSurface()
        setupUI()
        setupDraggableOverlay()
        setupSettingsButton()
        restoreOverlayPosition()
        restoreSettingsButtonPosition()
        startChecklistUpdates()
        setupModeToggle()
        setupWirelessController()
        if (savedInstanceState?.getBoolean(STATE_AUTOMATIC_USB_CONNECT) == true) {
            enableAutomaticUsbConnect()
        } else {
            handleLaunchIntent(intent)
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleLaunchIntent(intent)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean(STATE_AUTOMATIC_USB_CONNECT, automaticUsbConnect)
        super.onSaveInstanceState(outState)
    }

    private fun handleLaunchIntent(intent: Intent?) {
        if (intent?.getBooleanExtra(EXTRA_AUTO_CONNECT, false) != true) return
        // Treat the launch extra as an event. Persisting it on the Activity's
        // Intent would make a deliberate Disconnect resume after recreation.
        intent.removeExtra(EXTRA_AUTO_CONNECT)
        enableAutomaticUsbConnect()
    }

    private fun enableAutomaticUsbConnect() {
        automaticUsbConnect = true
        prefs.connectionMode = ConnectionMode.USB
        binding.modeToggleGroup.check(R.id.modeUSB)
        applyModeVisibility(ConnectionMode.USB)
        scheduleAutomaticUsbConnect(150)
    }

    private fun currentUsbPort(): Int =
        binding.portInput.text
            .toString()
            .toIntOrNull() ?: 54321

    private fun scheduleAutomaticUsbConnect(delayMs: Long = 1500) {
        if (!automaticUsbConnect || isConnected) return
        autoConnectHandler.removeCallbacks(autoConnectRunnable)
        autoConnectHandler.postDelayed(autoConnectRunnable, delayMs)
    }

    private fun setupModeToggle() {
        // Restore previous mode and reflect in toggle.
        val saved = prefs.connectionMode
        binding.modeToggleGroup.check(if (saved == ConnectionMode.WIRELESS) R.id.modeWireless else R.id.modeUSB)
        applyModeVisibility(saved)

        binding.modeToggleGroup.addOnButtonCheckedListener { _, checkedId, isChecked ->
            if (!isChecked) return@addOnButtonCheckedListener
            val mode = if (checkedId == R.id.modeWireless) ConnectionMode.WIRELESS else ConnectionMode.USB
            prefs.connectionMode = mode
            applyModeVisibility(mode)
            if (mode == ConnectionMode.WIRELESS) {
                automaticUsbConnect = false
                autoConnectHandler.removeCallbacks(autoConnectRunnable)
                wirelessController.show()
            } else if (!isConnected) {
                automaticUsbConnect = true
                scheduleAutomaticUsbConnect(150)
            }
        }
    }

    private fun applyModeVisibility(mode: ConnectionMode) {
        binding.usbModeContent.visibility = if (mode == ConnectionMode.USB) View.VISIBLE else View.GONE
        binding.wirelessModeContent.visibility = if (mode == ConnectionMode.WIRELESS) View.VISIBLE else View.GONE
        // USB checklist polls 127.0.0.1:port every 2s via adb-reverse to verify Mac
        // server reachability. While in Wireless mode that probe creates loopback
        // connections that fight the wireless session for the Mac's single client
        // slot — kicking the wireless client off seconds after it auths. Pause
        // checklist updates whenever Wireless is the active tab.
        if (mode == ConnectionMode.WIRELESS) {
            stopChecklistUpdates()
        } else {
            startChecklistUpdates()
        }
        updateDisconnectedHeader(mode)
    }

    private fun setupWirelessController() {
        wirelessController =
            WirelessTabController(
                activity = this,
                views =
                    WirelessTabController.Views(
                        connecting = binding.wirelessConnecting,
                        firstTime = binding.wirelessFirstTime,
                        connected = binding.wirelessConnected,
                        pairedIdle = binding.wirelessPairedIdle,
                        repair = binding.wirelessTokenMismatch,
                        permDenied = binding.wirelessPermDenied,
                        scanButton = binding.wirelessScanButton,
                        rescanButton = binding.wirelessRescanButton,
                        disconnectButton = binding.wirelessDisconnectButton,
                        forgetButton = binding.wirelessForgetButton,
                        reconnectButton = binding.wirelessReconnectButton,
                        idleForgetButton = binding.wirelessIdleForgetButton,
                        openSettingsButton = binding.wirelessOpenSettingsButton,
                        connectedMacName = binding.connectedMacName,
                        connectedMacIp = binding.connectedMacIp,
                        connectingLabel = binding.connectingLabel,
                        connectingSubtitle = binding.connectingSubtitle,
                        idleMacName = binding.idleMacName,
                        idleMacIp = binding.idleMacIp,
                        repairTitle = binding.repairTitle,
                        repairMessage = binding.repairMessage,
                    ),
                storage = pairedHostStorage,
                cameraPerm = cameraPerm,
                isTrustedLanAcknowledged = { prefs.trustedLanAcknowledged },
                acknowledgeTrustedLan = { prefs.trustedLanAcknowledged = true },
                onConnectRequested = { host, port, token, deviceName, macName ->
                    connectWireless(host, port, token, deviceName, macName)
                },
            )
        wirelessController.bind()
        binding.wirelessDisconnectButton.setOnClickListener { disconnect() }
        if (prefs.connectionMode == ConnectionMode.WIRELESS) {
            wirelessController.show()
        }
    }

    override fun onActivityResult(
        requestCode: Int,
        resultCode: Int,
        data: android.content.Intent?,
    ) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == WirelessTabController.REQ_SCAN && resultCode == RESULT_OK) {
            val url = data?.getStringExtra(QRScannerActivity.EXTRA_URL) ?: return
            wirelessController.onScanResult(url)
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == WirelessTabController.REQ_CAMERA) {
            val granted = grantResults.firstOrNull() == android.content.pm.PackageManager.PERMISSION_GRANTED
            wirelessController.onCameraPermissionResult(granted)
        }
    }

    /** Keep the tablet awake and its streamed Mac pixels out of screenshots only while connected. */
    private fun setStreamingWindowState(enabled: Boolean) {
        val streamingFlags =
            WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON or
                WindowManager.LayoutParams.FLAG_SECURE
        if (enabled) {
            window.addFlags(streamingFlags)
        } else {
            window.clearFlags(streamingFlags)
        }
    }

    /**
     * Enable fullscreen immersive mode
     * Uses modern WindowInsets API on Android R+ for better system compatibility
     * Also handles display cutout (notch) to use full screen area
     */
    private fun enableFullscreenMode() {
        // Ensure we draw behind the cutout
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            window.attributes.layoutInDisplayCutoutMode =
                WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.setDecorFitsSystemWindows(false)
            window.insetsController?.let { controller ->
                controller.hide(WindowInsets.Type.statusBars() or WindowInsets.Type.navigationBars())
                controller.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        } else {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = (
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                    or View.SYSTEM_UI_FLAG_FULLSCREEN
                    or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                    or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                    or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                    or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
            )
        }
    }

    /**
     * Disable fullscreen mode (when disconnected)
     */
    private fun disableFullscreenMode() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.insetsController?.show(WindowInsets.Type.statusBars() or WindowInsets.Type.navigationBars())
        } else {
            @Suppress("DEPRECATION")
            window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_VISIBLE
        }
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun setupSurface() {
        binding.surfaceView.holder.addCallback(
            object : SurfaceHolder.Callback {
                override fun surfaceCreated(holder: SurfaceHolder) {
                    mainDiag("surfaceCreated")
                    log("Surface created")
                }

                override fun surfaceChanged(
                    holder: SurfaceHolder,
                    format: Int,
                    width: Int,
                    height: Int,
                ) {
                    mainDiag("surfaceChanged: ${width}x$height")
                    log("Surface changed: ${width}x$height")
                    // Don't initialize decoder here — wait for display config
                    // from the server so we use the correct resolution.
                    // Store the holder so we can initialize later.
                    currentSurfaceHolder = holder
                    // If we already have a display config (reconnect case), init now
                    if (displayWidth > 0 && displayHeight > 0 && videoDecoder == null) {
                        initializeDecoder(holder)
                    }
                }

                override fun surfaceDestroyed(holder: SurfaceHolder) {
                    mainDiag("surfaceDestroyed")
                    log("Surface destroyed")
                    // Only release decoder, NOT the connection.
                    videoDecoder?.release()
                    videoDecoder = null
                }
            },
        )

        binding.surfaceView.setOnTouchListener { view, event ->
            handleTouch(view, event)
            true
        }
    }

    private fun setupUI() {
        binding.connectButton.setOnClickListener {
            var host =
                binding.hostInput.text
                    .toString()
                    .ifEmpty { "127.0.0.1" }
            val port =
                binding.portInput.text
                    .toString()
                    .toIntOrNull() ?: 54321

            // Convert localhost to 127.0.0.1 for better Android compatibility
            if (host.equals("localhost", ignoreCase = true)) {
                host = "127.0.0.1"
            }

            // Validate input
            if (host.isBlank()) {
                showError("Please enter a host address")
                return@setOnClickListener
            }

            updateStatus("Checking for your Mac…")
            automaticUsbConnect = true
            connect(host, port, automatic = true)
        }

        binding.disconnectButton.setOnClickListener {
            disconnect()
        }

        // Advanced settings toggle
        binding.showAdvanced.setOnClickListener {
            connectionDetailsVisible = !connectionDetailsVisible
            val visibility = if (connectionDetailsVisible) View.VISIBLE else View.GONE
            binding.checklistContainer.visibility = visibility
            binding.advancedSettings.visibility = visibility
            binding.showAdvanced.setText(
                if (connectionDetailsVisible) {
                    R.string.hide_connection_details
                } else {
                    R.string.connection_details
                },
            )
            if (connectionDetailsVisible) {
                updateChecklist()
            }
        }

        showDisconnectedStreamUi()
    }

    private fun showError(message: String) {
        runOnUiThread {
            android.app.AlertDialog
                .Builder(this)
                .setTitle("Connection Error")
                .setMessage(message)
                .setPositiveButton("OK", null)
                .show()
        }
    }

    private fun updateStatus(status: String) {
        runOnUiThread {
            binding.statusText.text = status
        }
    }

    private fun updateDisconnectedHeader(mode: ConnectionMode) {
        if (!::binding.isInitialized || isConnected) return
        if (mode == ConnectionMode.USB) {
            binding.connectionTitle.setText(R.string.waiting_for_mac)
            binding.connectionSubtitle.setText(R.string.usb_waiting_description)
            binding.connectionProgress.visibility = View.VISIBLE
            binding.connectButton.setText(R.string.try_again)
            updateStatus(getString(R.string.looking_for_mac))
        } else {
            binding.connectionTitle.setText(R.string.connect_wirelessly)
            binding.connectionSubtitle.setText(R.string.wireless_pair_once)
            binding.connectionProgress.visibility = View.GONE
        }
    }

    private fun showConnectedStreamUi() {
        connectionDetailsVisible = false
        binding.checklistContainer.visibility = View.GONE
        binding.advancedSettings.visibility = View.GONE
        binding.showAdvanced.setText(R.string.connection_details)
        binding.surfaceView.visibility = View.VISIBLE
        binding.disconnectedBackdrop.visibility = View.GONE
        binding.settingsPanel.visibility = View.GONE
        binding.settingsButton.visibility = View.VISIBLE
        restoreSettingsButtonPosition()
        updateOverlayVisibility(prefs.showStatsOverlay)
    }

    private fun showDisconnectedStreamUi() {
        // Keep one stable layout while the USB retry loop runs. Showing system
        // bars or changing orientation here resized/recreated the Activity and
        // made the waiting state visibly flash.
        enableFullscreenMode()
        binding.surfaceView.visibility = View.GONE
        binding.disconnectedBackdrop.visibility = View.VISIBLE
        binding.settingsPanel.visibility = View.VISIBLE
        binding.settingsButton.visibility = View.GONE
        binding.statusBar.visibility = View.GONE
        binding.connectButton.isEnabled = true
        binding.statusIndicator.setBackgroundResource(R.drawable.status_indicator_waiting)
        updateDisconnectedHeader(prefs.connectionMode)
    }

    @SuppressLint("ClickableViewAccessibility", "InflateParams")
    private fun setupDraggableOverlay() {
        binding.statusBar.setOnTouchListener { view, event ->
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    isDraggingOverlay = true
                    overlayDx = view.x - event.rawX
                    overlayDy = view.y - event.rawY
                    true
                }

                MotionEvent.ACTION_MOVE -> {
                    if (isDraggingOverlay) {
                        // Calculate new position
                        var newX = event.rawX + overlayDx
                        var newY = event.rawY + overlayDy

                        // Get screen bounds
                        val parent = view.parent as View
                        val maxX = parent.width - view.width.toFloat()
                        val maxY = parent.height - view.height.toFloat()

                        // Constrain to screen bounds
                        newX = newX.coerceIn(0f, maxX)
                        newY = newY.coerceIn(0f, maxY)

                        view
                            .animate()
                            .x(newX)
                            .y(newY)
                            .setDuration(0)
                            .start()
                    }
                    true
                }

                MotionEvent.ACTION_UP -> {
                    if (isDraggingOverlay) {
                        // Save position
                        prefs.overlayX = view.x
                        prefs.overlayY = view.y
                        isDraggingOverlay = false
                    }
                    true
                }

                else -> {
                    false
                }
            }
        }
    }

    private fun restoreOverlayPosition() {
        val x = prefs.overlayX
        val y = prefs.overlayY

        if (x >= 0 && y >= 0) {
            binding.statusBar.post {
                binding.statusBar.x = x
                binding.statusBar.y = y
            }
        }

        // Apply opacity to both overlay and settings button
        val opacity = prefs.overlayOpacity
        updateOverlayOpacity(opacity)
        updateSettingsButtonOpacity(opacity)

        // Apply visibility
        updateOverlayVisibility(prefs.showStatsOverlay)
    }

    private fun updateOverlayOpacity(opacity: Float) {
        binding.statusBar.alpha = opacity
    }

    private fun updateOverlayVisibility(show: Boolean) {
        if (streamClient != null && show) {
            binding.statusBar.visibility = View.VISIBLE
            // Restore position when showing
            val x = prefs.overlayX
            val y = prefs.overlayY
            if (x >= 0 && y >= 0) {
                binding.statusBar.post {
                    binding.statusBar.x = x
                    binding.statusBar.y = y
                }
            }
        } else {
            binding.statusBar.visibility = View.GONE
        }
    }

    @SuppressLint("InflateParams", "SetTextI18n")
    private fun showSettingsDialog() {
        val dialog = Dialog(this)
        dialog.requestWindowFeature(Window.FEATURE_NO_TITLE)
        dialog.setContentView(R.layout.dialog_settings)
        dialog.window?.setBackgroundDrawable(ColorDrawable(Color.TRANSPARENT))

        val view = dialog.findViewById<View>(android.R.id.content)
        val showStatsSwitch = view.findViewById<SwitchMaterial>(R.id.showStatsSwitch)
        val opacitySlider = view.findViewById<Slider>(R.id.opacitySlider)
        val opacityValue = view.findViewById<TextView>(R.id.opacityValue)
        val resetButton = view.findViewById<View>(R.id.resetPositionButton)
        val resetSettingsBtn = view.findViewById<View>(R.id.resetSettingsButton)
        val disconnectButton = view.findViewById<View>(R.id.disconnectSettingsButton)
        val closeButton = view.findViewById<View>(R.id.closeButton)

        // Only show Disconnect when actually streaming. Otherwise the button is
        // a no-op and confuses users into clicking it twice.
        disconnectButton.visibility = if (isConnected) View.VISIBLE else View.GONE

        // Position buttons (8 directions)
        val cornerTopLeft = view.findViewById<MaterialButton>(R.id.cornerTopLeft)
        val cornerTopRight = view.findViewById<MaterialButton>(R.id.cornerTopRight)
        val cornerBottomLeft = view.findViewById<MaterialButton>(R.id.cornerBottomLeft)
        val cornerBottomRight = view.findViewById<MaterialButton>(R.id.cornerBottomRight)
        val positionTopCenter = view.findViewById<MaterialButton>(R.id.positionTopCenter)
        val positionBottomCenter = view.findViewById<MaterialButton>(R.id.positionBottomCenter)
        val positionCenterLeft = view.findViewById<MaterialButton>(R.id.positionCenterLeft)
        val positionCenterRight = view.findViewById<MaterialButton>(R.id.positionCenterRight)

        // Load current settings
        showStatsSwitch.isChecked = prefs.showStatsOverlay
        opacitySlider.value = prefs.overlayOpacity
        opacityValue.text = "${(prefs.overlayOpacity * 100).toInt()}%"

        // Highlight current position selection (8 positions)
        // 0=BottomRight, 1=BottomLeft, 2=TopRight, 3=TopLeft
        // 4=TopCenter, 5=BottomCenter, 6=CenterLeft, 7=CenterRight
        fun updatePositionSelection(selectedPosition: Int) {
            val buttons =
                listOf(
                    cornerBottomRight,
                    cornerBottomLeft,
                    cornerTopRight,
                    cornerTopLeft,
                    positionTopCenter,
                    positionBottomCenter,
                    positionCenterLeft,
                    positionCenterRight,
                )
            buttons.forEachIndexed { index, button ->
                if (index == selectedPosition) {
                    button.backgroundTintList =
                        android.content.res.ColorStateList
                            .valueOf(0x334CAF50)
                } else {
                    button.backgroundTintList = null
                }
            }
        }
        updatePositionSelection(prefs.settingsButtonCorner)

        // Setup listeners
        showStatsSwitch.setOnCheckedChangeListener { _, isChecked ->
            prefs.showStatsOverlay = isChecked
            updateOverlayVisibility(isChecked)
        }

        opacitySlider.addOnChangeListener { _, value, _ ->
            prefs.overlayOpacity = value
            updateOverlayOpacity(value)
            updateSettingsButtonOpacity(value)
            opacityValue.text = "${(value * 100).toInt()}%"
        }

        resetButton.setOnClickListener {
            prefs.overlayX = -1f
            prefs.overlayY = -1f
            // Use displayMetrics for reliable positioning
            val dm = resources.displayMetrics
            binding.statusBar
                .animate()
                .x(dm.widthPixels - binding.statusBar.width - 48f)
                .y(48f)
                .setDuration(300)
                .start()
        }

        // Position button listeners (8 directions)
        cornerBottomRight.setOnClickListener {
            prefs.settingsButtonCorner = 0
            updatePositionSelection(0)
            updateSettingsButtonPosition(0)
        }

        cornerBottomLeft.setOnClickListener {
            prefs.settingsButtonCorner = 1
            updatePositionSelection(1)
            updateSettingsButtonPosition(1)
        }

        cornerTopRight.setOnClickListener {
            prefs.settingsButtonCorner = 2
            updatePositionSelection(2)
            updateSettingsButtonPosition(2)
        }

        cornerTopLeft.setOnClickListener {
            prefs.settingsButtonCorner = 3
            updatePositionSelection(3)
            updateSettingsButtonPosition(3)
        }

        positionTopCenter.setOnClickListener {
            prefs.settingsButtonCorner = 4
            updatePositionSelection(4)
            updateSettingsButtonPosition(4)
        }

        positionBottomCenter.setOnClickListener {
            prefs.settingsButtonCorner = 5
            updatePositionSelection(5)
            updateSettingsButtonPosition(5)
        }

        positionCenterLeft.setOnClickListener {
            prefs.settingsButtonCorner = 6
            updatePositionSelection(6)
            updateSettingsButtonPosition(6)
        }

        positionCenterRight.setOnClickListener {
            prefs.settingsButtonCorner = 7
            updatePositionSelection(7)
            updateSettingsButtonPosition(7)
        }

        resetSettingsBtn.setOnClickListener {
            prefs.settingsButtonCorner = 0
            updatePositionSelection(0)
            updateSettingsButtonPosition(0)
        }

        disconnectButton.setOnClickListener {
            dialog.dismiss()
            disconnect()
        }

        closeButton.setOnClickListener {
            dialog.dismiss()
        }

        dialog.show()

        // Cap dialog height to 85% of screen so content scrolls on smaller screens / landscape
        dialog.window?.let { win ->
            val maxH = (resources.displayMetrics.heightPixels * 0.85).toInt()
            win.setLayout(WindowManager.LayoutParams.MATCH_PARENT, maxH)
        }
    }

    private fun updateSettingsButtonOpacity(opacity: Float) {
        binding.settingsButton.alpha = opacity
    }

    private fun setupSettingsButton() {
        // Simple click to show settings dialog
        // Position can be changed via corner buttons in settings
        binding.settingsButton.setOnClickListener {
            showSettingsDialog()
        }
    }

    private fun restoreSettingsButtonPosition() {
        updateSettingsButtonPosition(prefs.settingsButtonCorner)
    }

    /**
     * Use ConstraintSet to position settings button - most reliable method
     * Works correctly with orientation changes
     * Supports 8 positions: 4 corners + 4 edges
     */
    private fun updateSettingsButtonPosition(position: Int) {
        val constraintLayout = binding.root
        val constraintSet = ConstraintSet()
        constraintSet.clone(constraintLayout)

        val buttonId = binding.settingsButton.id
        val marginDp = (24 * resources.displayMetrics.density).toInt()

        // Clear all constraints first
        constraintSet.clear(buttonId, ConstraintSet.TOP)
        constraintSet.clear(buttonId, ConstraintSet.BOTTOM)
        constraintSet.clear(buttonId, ConstraintSet.START)
        constraintSet.clear(buttonId, ConstraintSet.END)

        when (position) {
            0 -> { // Bottom Right (default)
                constraintSet.connect(
                    buttonId,
                    ConstraintSet.BOTTOM,
                    ConstraintSet.PARENT_ID,
                    ConstraintSet.BOTTOM,
                    marginDp,
                )
                constraintSet.connect(buttonId, ConstraintSet.END, ConstraintSet.PARENT_ID, ConstraintSet.END, marginDp)
            }

            1 -> { // Bottom Left
                constraintSet.connect(
                    buttonId,
                    ConstraintSet.BOTTOM,
                    ConstraintSet.PARENT_ID,
                    ConstraintSet.BOTTOM,
                    marginDp,
                )
                constraintSet.connect(
                    buttonId,
                    ConstraintSet.START,
                    ConstraintSet.PARENT_ID,
                    ConstraintSet.START,
                    marginDp,
                )
            }

            2 -> { // Top Right
                constraintSet.connect(buttonId, ConstraintSet.TOP, ConstraintSet.PARENT_ID, ConstraintSet.TOP, marginDp)
                constraintSet.connect(buttonId, ConstraintSet.END, ConstraintSet.PARENT_ID, ConstraintSet.END, marginDp)
            }

            3 -> { // Top Left
                constraintSet.connect(buttonId, ConstraintSet.TOP, ConstraintSet.PARENT_ID, ConstraintSet.TOP, marginDp)
                constraintSet.connect(
                    buttonId,
                    ConstraintSet.START,
                    ConstraintSet.PARENT_ID,
                    ConstraintSet.START,
                    marginDp,
                )
            }

            4 -> { // Top Center
                constraintSet.connect(buttonId, ConstraintSet.TOP, ConstraintSet.PARENT_ID, ConstraintSet.TOP, marginDp)
                constraintSet.connect(buttonId, ConstraintSet.START, ConstraintSet.PARENT_ID, ConstraintSet.START, 0)
                constraintSet.connect(buttonId, ConstraintSet.END, ConstraintSet.PARENT_ID, ConstraintSet.END, 0)
            }

            5 -> { // Bottom Center
                constraintSet.connect(
                    buttonId,
                    ConstraintSet.BOTTOM,
                    ConstraintSet.PARENT_ID,
                    ConstraintSet.BOTTOM,
                    marginDp,
                )
                constraintSet.connect(buttonId, ConstraintSet.START, ConstraintSet.PARENT_ID, ConstraintSet.START, 0)
                constraintSet.connect(buttonId, ConstraintSet.END, ConstraintSet.PARENT_ID, ConstraintSet.END, 0)
            }

            6 -> { // Center Left
                constraintSet.connect(buttonId, ConstraintSet.TOP, ConstraintSet.PARENT_ID, ConstraintSet.TOP, 0)
                constraintSet.connect(buttonId, ConstraintSet.BOTTOM, ConstraintSet.PARENT_ID, ConstraintSet.BOTTOM, 0)
                constraintSet.connect(
                    buttonId,
                    ConstraintSet.START,
                    ConstraintSet.PARENT_ID,
                    ConstraintSet.START,
                    marginDp,
                )
            }

            7 -> { // Center Right
                constraintSet.connect(buttonId, ConstraintSet.TOP, ConstraintSet.PARENT_ID, ConstraintSet.TOP, 0)
                constraintSet.connect(buttonId, ConstraintSet.BOTTOM, ConstraintSet.PARENT_ID, ConstraintSet.BOTTOM, 0)
                constraintSet.connect(buttonId, ConstraintSet.END, ConstraintSet.PARENT_ID, ConstraintSet.END, marginDp)
            }

            else -> { // Default to bottom right
                constraintSet.connect(
                    buttonId,
                    ConstraintSet.BOTTOM,
                    ConstraintSet.PARENT_ID,
                    ConstraintSet.BOTTOM,
                    marginDp,
                )
                constraintSet.connect(buttonId, ConstraintSet.END, ConstraintSet.PARENT_ID, ConstraintSet.END, marginDp)
            }
        }

        // Reset any absolute positioning that might have been set
        binding.settingsButton.translationX = 0f
        binding.settingsButton.translationY = 0f

        constraintSet.applyTo(constraintLayout)
    }

    /**
     * Display config from a new Mac always arrives AFTER codecSelected, so a
     * missing negotiation at this point proves the Mac app predates H.264
     * support — surface that instead of a silent black screen.
     */
    private fun warnIfAvcOnlyWithoutNegotiation() {
        if (!CodecCapabilities.hasHevcDecoder && streamClient?.codecNegotiated != true) {
            mainDiag("AVC-only device but Mac did not negotiate codec — Mac app too old")
            runOnUiThread {
                updateStatus("This device has no HEVC decoder. Update the Telemachus Mac app to enable H.264 support.")
            }
        }
    }

    private fun initializeDecoder(holder: SurfaceHolder) {
        mainDiag(
            "initializeDecoder called, surface=${holder.surface}, " +
                "valid=${holder.surface.isValid}, res=${displayWidth}x$displayHeight",
        )
        if (displayWidth <= 0 || displayHeight <= 0) {
            mainDiag("initializeDecoder skipped — no display config yet")
            return
        }
        try {
            // Pass display for vsync-aligned frame presentation
            // Use modern API on Android R+, fallback to deprecated for older versions
            val displayObj =
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    display // Activity.getDisplay() - modern API
                } else {
                    @Suppress("DEPRECATION")
                    windowManager.defaultDisplay
                }
            val mime =
                if (streamClient?.streamCodecIsHevc == false) {
                    MediaFormat.MIMETYPE_VIDEO_AVC
                } else {
                    MediaFormat.MIMETYPE_VIDEO_HEVC
                }
            videoDecoder = VideoDecoder(holder.surface, displayObj, displayWidth, displayHeight, mime)
            // Wire up buffer release callback
            videoDecoder?.onFrameDecoded = { buffer ->
                streamClient?.releaseBuffer(buffer)
            }
            videoDecoder?.onKeyframeRequired = { force, reason ->
                streamClient?.requestKeyframe(force = force, reason = reason)
            }
            streamClient?.requestKeyframe(force = true, reason = "decoder initialized")
            mainDiag("Decoder initialized OK ${displayWidth}x$displayHeight mime=$mime, videoDecoder=$videoDecoder")
            log("✅ Decoder initialized ${displayWidth}x$displayHeight $mime (${displayObj?.refreshRate ?: 60f}Hz)")
        } catch (e: Exception) {
            mainDiag("Decoder init FAILED: ${e.message}")
            log("❌ Failed to initialize decoder: ${e.message}")
            runOnUiThread {
                updateStatus("Video decoder failed: ${e.message}")
            }
        }
    }

    /**
     * Wire up all StreamClient callbacks. Used by both USB connect() and wireless connectWireless().
     */
    private fun setupStreamClientCallbacks() {
        streamClient?.onFrameReceived = { frameData, frameSize, timestamp, isKeyframe ->
            val dec = videoDecoder
            if (dec != null) {
                dec.decode(frameData, frameSize, timestamp, isKeyframe)
            } else {
                mainDiag("FRAME DROPPED: videoDecoder is null!")
                streamClient?.releaseBuffer(frameData)
            }
        }

        videoDecoder?.onFrameDecoded = { buffer ->
            streamClient?.releaseBuffer(buffer)
        }

        streamClient?.onLatencyMeasured = { rttMs ->
            runOnUiThread {
                binding.latencyText.text = String.format(Locale.US, "%.1f ms", rttMs)
            }
        }

        streamClient?.onConnectionStatus = { connected ->
            runOnUiThread {
                isConnected = connected
                setStreamingWindowState(connected)
                if (connected) {
                    startPingTimer()
                    stopChecklistUpdates()
                    enableFullscreenMode()
                    // For wireless mode, transition controller to CONNECTED here —
                    // not in MainActivity.connectWireless's coroutine after the
                    // receive loop returns (that runs AFTER disconnect, causing
                    // a stale CONNECTED transition that hides the PAIRED_IDLE UI).
                    if (prefs.connectionMode == ConnectionMode.WIRELESS) {
                        val entry = pairedHostStorage.load()
                        wirelessController.onConnectSuccess(
                            entry?.macName ?: "Mac",
                            entry?.host ?: "—",
                        )
                    }
                } else {
                    stopPingTimer()
                    showDisconnectedStreamUi()
                    val mode = prefs.connectionMode
                    val willTransition = mode == ConnectionMode.WIRELESS
                    android.util.Log.i(
                        "MainActivity",
                        "onConnectionStatus(false) — mode=$mode, willTransition=$willTransition",
                    )
                    if (mode == ConnectionMode.WIRELESS) {
                        // Don't restart checklist (it conflicts with wireless on Mac).
                        // Tell wireless controller to show the idle/reconnect UI.
                        wirelessController.onStreamDisconnected()
                    } else {
                        log("📋 Restarting checklist updates")
                        startChecklistUpdates()
                    }
                }
            }
        }

        streamClient?.onDisplaySize = { width, height, rotation ->
            mainDiag("onDisplaySize: ${width}x$height @ $rotation°")
            warnIfAvcOnlyWithoutNegotiation()
            displayWidth = width
            displayHeight = height
            displayRotation = rotation
            if (videoDecoder != null) {
                videoDecoder?.updateResolution(width, height)
            } else {
                val holder = currentSurfaceHolder
                if (holder != null && holder.surface.isValid) {
                    mainDiag("Display config arrived, initializing decoder ${width}x$height")
                    runOnUiThread {
                        if (videoDecoder == null) {
                            initializeDecoder(holder)
                        }
                    }
                } else {
                    mainDiag("Display config arrived but no valid surface yet")
                }
            }
            runOnUiThread {
                binding.resolutionText.text = getString(R.string.resolution_format, width, height)
                binding.connectButton.isEnabled = false
                binding.disconnectButton.isEnabled = true
                binding.statusIndicator.setBackgroundResource(R.drawable.status_indicator_green)
                updateStatus(getString(R.string.connected_streaming))
                showConnectedStreamUi()
                applyRotation(rotation)
            }
            log("Display: ${width}x$height @ $rotation°")
        }

        streamClient?.onStats = { fps, mbps ->
            runOnUiThread {
                binding.fpsText.text = String.format(Locale.US, "%.1f", fps)
                binding.bitrateText.text = String.format(Locale.US, "%.1f Mbps", mbps)
            }
        }
    }

    private fun connectWireless(
        host: String,
        port: Int,
        token: ByteArray,
        deviceName: String,
        macName: String,
    ) {
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                log("Connecting wirelessly to $macName at $host:$port...")
                streamClient = StreamClient(host, port, applicationContext)
                setupStreamClientCallbacks()
                streamClient?.connectWireless(token, deviceName)
                // NOTE: onConnectSuccess is fired from the onConnectionStatus(true)
                // listener (above) right after handshake OK — not here. This line
                // would otherwise run AFTER the receive loop exits, i.e. AFTER
                // disconnect, incorrectly transitioning back to CONNECTED.
            } catch (e: StreamClient.WirelessConnectError) {
                runOnUiThread {
                    wirelessController.onConnectError(e)
                }
            } catch (e: Exception) {
                log("Wireless connect failed: ${e.message}")
                runOnUiThread {
                    wirelessController.onConnectError(StreamClient.WirelessConnectError.NetworkUnreachable)
                }
            }
        }
    }

    private fun connect(
        host: String,
        port: Int,
        automatic: Boolean = false,
    ) {
        if (isConnected || connectionAttemptInProgress) return
        connectionAttemptInProgress = true
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                log("Connecting to $host:$port...")

                streamClient = StreamClient(host, port, applicationContext)
                setupStreamClientCallbacks()
                streamClient?.connect()
            } catch (e: Exception) {
                val errorMessage =
                    when {
                        e.message?.contains("ECONNREFUSED") == true -> {
                            "Mac server is not running.\n\nPlease start Telemachus.app on your Mac first."
                        }

                        e.message?.contains("Network is unreachable") == true -> {
                            "Cannot reach Mac.\n\n" +
                                "Make sure both devices are connected via USB cable and ADB reverse is configured."
                        }

                        e.message?.contains("timeout") == true -> {
                            "Connection timeout.\n\nCheck if Mac firewall is blocking port $port."
                        }

                        else -> {
                            "Connection failed: ${e.message}\n\n" +
                                "Try:\n• Start Telemachus.app on Mac\n" +
                                "• Check USB connection\n• Run: adb reverse tcp:$port tcp:$port"
                        }
                    }
                if (!automatic) {
                    updateStatus("Couldn’t reach your Mac")
                }
                if (!automatic) {
                    showError(errorMessage)
                }
            } finally {
                connectionAttemptInProgress = false
                if (automatic && automaticUsbConnect) {
                    // onConnectionStatus(false) is posted to the main thread. Checking
                    // isConnected on this IO thread can race that callback and suppress
                    // the retry after a server closes an otherwise successful socket.
                    runOnUiThread {
                        if (!isConnected) {
                            showDisconnectedStreamUi()
                            scheduleAutomaticUsbConnect()
                        }
                    }
                }
            }
        }
    }

    private fun disconnect() {
        automaticUsbConnect = false
        autoConnectHandler.removeCallbacks(autoConnectRunnable)
        stopPingTimer()
        streamClient?.disconnect()
        // Reset display config so next connect defers decoder init until config arrives
        displayWidth = 0
        displayHeight = 0
        log("Disconnected")
    }

    private fun startPingTimer() {
        stopPingTimer()
        pingJob =
            lifecycleScope.launch(Dispatchers.IO) {
                while (true) {
                    kotlinx.coroutines.delay(1000) // Ping every 1 second
                    streamClient?.sendPing()
                }
            }
    }

    private fun stopPingTimer() {
        pingJob?.cancel()
        pingJob = null
    }

    private fun cleanup() {
        try {
            disconnect()
            videoDecoder?.release()
            videoDecoder = null
            setStreamingWindowState(false)
        } catch (e: Exception) {
            log("⚠️ Cleanup error: ${e.message}")
        }
    }

    private fun handleTouch(
        view: View,
        event: MotionEvent,
    ) {
        val first =
            TouchMapper.map(
                x = event.x,
                y = event.y,
                viewWidth = view.width,
                viewHeight = view.height,
                videoWidth = displayWidth,
                videoHeight = displayHeight,
            )
        val x = first.x
        val y = first.y
        val pointerCount = event.pointerCount.coerceAtMost(2)

        var x2 = 0f
        var y2 = 0f
        if (pointerCount >= 2) {
            val second =
                TouchMapper.map(
                    x = event.getX(1),
                    y = event.getY(1),
                    viewWidth = view.width,
                    viewHeight = view.height,
                    videoWidth = displayWidth,
                    videoHeight = displayHeight,
                )
            x2 = second.x
            y2 = second.y
        }

        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                inputPredictor.reset()
                inputPredictor.addSample(x, y)
                streamClient?.sendTouch(x, y, 0, pointerCount, x2, y2)
            }

            MotionEvent.ACTION_POINTER_DOWN -> {
                streamClient?.sendTouch(x, y, 0, pointerCount, x2, y2)
            }

            MotionEvent.ACTION_MOVE -> {
                if (pointerCount == 1) {
                    inputPredictor.addSample(x, y)
                    val (px, py) = inputPredictor.predictPosition(12f)
                    streamClient?.sendTouch(px.coerceIn(0f, 1f), py.coerceIn(0f, 1f), 1, 1)
                } else {
                    streamClient?.sendTouch(x, y, 1, pointerCount, x2, y2)
                }
            }

            MotionEvent.ACTION_UP -> {
                inputPredictor.reset()
                streamClient?.sendTouch(x, y, 2, 1)
            }

            MotionEvent.ACTION_POINTER_UP -> {
                streamClient?.sendTouch(x, y, 2, pointerCount, x2, y2)
            }

            MotionEvent.ACTION_CANCEL -> {
                inputPredictor.reset()
                streamClient?.sendTouch(x, y, 2, 1)
            }
        }
    }

    /**
     * Apply rotation by changing the Activity's screen orientation
     * This provides proper fullscreen portrait/landscape support
     */
    private fun applyRotation(rotation: Int) {
        requestedOrientation =
            when (rotation) {
                90 -> ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
                180 -> ActivityInfo.SCREEN_ORIENTATION_REVERSE_LANDSCAPE
                270 -> ActivityInfo.SCREEN_ORIENTATION_REVERSE_PORTRAIT
                else -> ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE // 0°
            }

        // Reset SurfaceView transform (orientation change handles rotation)
        binding.surfaceView.apply {
            this.rotation = 0f
            scaleX = 1f
            scaleY = 1f
        }

        // ConstraintSet handles orientation changes automatically
        // No need for postDelayed positioning

        log(
            "🔄 Orientation: ${when (rotation) {
                90 -> "Portrait"
                180 -> "Landscape (flipped)"
                270 -> "Portrait (flipped)"
                else -> "Landscape"
            }}",
        )
    }

    /**
     * Reset orientation to follow device sensor (when disconnected)
     */
    private fun resetOrientationToSensor() {
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_FULL_SENSOR
    }

    @SuppressLint("SetTextI18n") // Developer-only rolling diagnostic output is not user-facing copy.
    private fun log(message: String) {
        runOnUiThread {
            val current = binding.logText.text.toString()
            val lines = current.split("\n").takeLast(5)
            binding.logText.text = (lines + message).joinToString("\n")
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        autoConnectHandler.removeCallbacks(autoConnectRunnable)
        stopChecklistUpdates()
        cleanup()
    }

    companion object {
        private const val EXTRA_AUTO_CONNECT = "auto_connect"
        private const val STATE_AUTOMATIC_USB_CONNECT = "automatic_usb_connect"
        private const val ACTION_USB_STATE = "android.hardware.usb.action.USB_STATE"
        private const val EXTRA_USB_CONNECTED = "connected"
        private const val EXTRA_USB_CONFIGURED = "configured"
        private const val EXTRA_USB_FUNCTION_ADB = "adb"
    }

    // ==================== Connection Checklist ====================

    private fun startChecklistUpdates() {
        // Stop any existing runnable first to prevent duplicates
        checklistRunnable?.let {
            checklistHandler.removeCallbacks(it)
        }

        checklistRunnable =
            object : Runnable {
                override fun run() {
                    updateChecklist()
                    checklistHandler.postDelayed(this, 2000) // Update every 2 seconds
                }
            }
        checklistHandler.post(checklistRunnable!!)
    }

    private fun stopChecklistUpdates() {
        checklistRunnable?.let {
            checklistHandler.removeCallbacks(it)
            checklistRunnable = null
        }
    }

    private fun updateChecklist() {
        // Diagnostics are deliberately lazy. The normal disconnected screen
        // should be calm, and a second socket probe used to compete with the
        // real automatic connection loop.
        if (isConnected || !connectionDetailsVisible) return

        // These values live in Settings.Global. Querying Settings.Secure with
        // Global keys returns false negatives on several Android versions.
        val isDeveloperModeEnabled =
            Settings.Global.getInt(
                contentResolver,
                Settings.Global.DEVELOPMENT_SETTINGS_ENABLED,
                0,
            ) == 1
        updateChecklistItem(binding.checkDeveloperMode, isDeveloperModeEnabled)

        // Check USB Debugging (ADB enabled)
        val isAdbEnabled =
            Settings.Global.getInt(
                contentResolver,
                Settings.Global.ADB_ENABLED,
                0,
            ) == 1
        updateChecklistItem(binding.checkUsbDebugging, isAdbEnabled)

        // Charging alone also succeeds with charge-only cables. The sticky USB
        // state broadcast tells us whether Android configured a real data link
        // and exposed the ADB USB function.
        val isUsbConnected = isUsbDataConnectionActive()
        updateChecklistItem(binding.checkUsbConnected, isUsbConnected)

        if (automaticUsbConnect || connectionAttemptInProgress) {
            updateChecklistItem(binding.checkMacServer, false)
            updateMainStatus(false)
            return
        }

        // Check Mac Server (try to connect to port)
        lifecycleScope.launch(Dispatchers.IO) {
            // Double-check connection state before socket test
            if (isConnected) return@launch

            val port =
                binding.portInput.text
                    .toString()
                    .toIntOrNull() ?: 54321
            val isServerRunning = checkServerRunning("127.0.0.1", port)
            runOnUiThread {
                // Final check before updating UI
                if (isConnected) return@runOnUiThread

                updateChecklistItem(binding.checkMacServer, isServerRunning)

                // Update main status indicator based on all checklist items
                val allReady = isDeveloperModeEnabled && isAdbEnabled && isUsbConnected && isServerRunning
                updateMainStatus(allReady)
            }
        }
    }

    private fun updateMainStatus(allReady: Boolean) {
        binding.statusIndicator.setBackgroundResource(
            if (allReady) {
                R.drawable.status_indicator_green
            } else {
                R.drawable.status_indicator_waiting
            },
        )
        if (!automaticUsbConnect && !connectionAttemptInProgress) {
            binding.statusText.text =
                if (allReady) "Ready to connect" else "Check the connection details below"
        }
    }

    private fun updateChecklistItem(
        indicator: View,
        isOk: Boolean,
    ) {
        indicator.setBackgroundResource(
            if (isOk) {
                R.drawable.status_indicator_green
            } else {
                R.drawable.status_indicator_red
            },
        )
    }

    private fun isUsbDataConnectionActive(): Boolean {
        val usbState = registerReceiver(null, IntentFilter(ACTION_USB_STATE)) ?: return false
        return usbState.getBooleanExtra(EXTRA_USB_CONNECTED, false) &&
            usbState.getBooleanExtra(EXTRA_USB_CONFIGURED, false) &&
            usbState.getBooleanExtra(EXTRA_USB_FUNCTION_ADB, false)
    }

    /**
     * Check if Mac server is actually running (not just ADB reverse)
     *
     * Problem: When `adb reverse tcp:54321 tcp:54321` is active, ADB daemon listens on port 54321.
     * A simple socket connect will succeed to ADB daemon, not the actual Mac server.
     *
     * Solution: After connecting, try to read data with a short timeout.
     * Mac server sends display config (type=1) immediately upon connection.
     * ADB daemon doesn't send anything, so read will timeout → false.
     */
    private fun checkServerRunning(
        host: String,
        port: Int,
    ): Boolean {
        var socket: Socket? = null
        return try {
            socket = Socket()
            socket.connect(InetSocketAddress(host, port), 300) // 300ms connect timeout
            socket.soTimeout = 200 // 200ms read timeout

            // Try to read - Mac server sends display config immediately
            // ADB daemon doesn't send anything, so read will timeout
            val input = socket.getInputStream()
            val firstByte = input.read() // Blocks up to soTimeout

            // If we got data (>= 0), it's the real Mac server
            // -1 means EOF (connection closed), anything else is data
            firstByte >= 0
        } catch (e: Exception) {
            // Timeout, connection refused, or other error = server not running
            false
        } finally {
            try {
                socket?.close()
            } catch (e: Exception) {
                // ignore
            }
        }
    }
}
