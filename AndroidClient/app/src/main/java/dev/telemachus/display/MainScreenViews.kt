package dev.telemachus.display

import android.util.SparseArray
import android.view.SurfaceView
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.cardview.widget.CardView
import androidx.constraintlayout.widget.ConstraintLayout
import com.google.android.material.button.MaterialButton
import com.google.android.material.button.MaterialButtonToggleGroup
import com.google.android.material.textfield.TextInputEditText

/** Typed references to the views consumed by the main screen. */
class MainScreenViews private constructor(
    val root: ConstraintLayout,
    index: SparseArray<View>,
) {
    constructor(root: ConstraintLayout) : this(root, indexViews(root))

    val advancedSettings: LinearLayout = index.requireView(R.id.advancedSettings)
    val bitrateText: TextView = index.requireView(R.id.bitrateText)
    val checkDeveloperMode: View = index.requireView(R.id.checkDeveloperMode)
    val checkMacServer: View = index.requireView(R.id.checkMacServer)
    val checkUsbConnected: View = index.requireView(R.id.checkUsbConnected)
    val checkUsbDebugging: View = index.requireView(R.id.checkUsbDebugging)
    val checklistContainer: LinearLayout = index.requireView(R.id.checklistContainer)
    val connectButton: MaterialButton = index.requireView(R.id.connectButton)
    val connectedMacIp: TextView = index.requireView(R.id.connectedMacIp)
    val connectedMacName: TextView = index.requireView(R.id.connectedMacName)
    val connectingLabel: TextView = index.requireView(R.id.connectingLabel)
    val connectingSubtitle: TextView = index.requireView(R.id.connectingSubtitle)
    val connectionProgress: ProgressBar = index.requireView(R.id.connectionProgress)
    val connectionSubtitle: TextView = index.requireView(R.id.connectionSubtitle)
    val connectionTitle: TextView = index.requireView(R.id.connectionTitle)
    val disconnectButton: MaterialButton = index.requireView(R.id.disconnectButton)
    val disconnectedBackdrop: View = index.requireView(R.id.disconnectedBackdrop)
    val fpsText: TextView = index.requireView(R.id.fpsText)
    val hostInput: TextInputEditText = index.requireView(R.id.hostInput)
    val idleMacIp: TextView = index.requireView(R.id.idleMacIp)
    val idleMacName: TextView = index.requireView(R.id.idleMacName)
    val latencyText: TextView = index.requireView(R.id.latencyText)
    val logText: TextView = index.requireView(R.id.logText)
    val modeToggleGroup: MaterialButtonToggleGroup = index.requireView(R.id.modeToggleGroup)
    val portInput: TextInputEditText = index.requireView(R.id.portInput)
    val repairMessage: TextView = index.requireView(R.id.repairMessage)
    val repairTitle: TextView = index.requireView(R.id.repairTitle)
    val resolutionText: TextView = index.requireView(R.id.resolutionText)
    val settingsButton: CardView = index.requireView(R.id.settingsButton)
    val settingsPanel: CardView = index.requireView(R.id.settingsPanel)
    val showAdvanced: TextView = index.requireView(R.id.showAdvanced)
    val statusBar: CardView = index.requireView(R.id.statusBar)
    val statusIndicator: View = index.requireView(R.id.statusIndicator)
    val statusText: TextView = index.requireView(R.id.statusText)
    val surfaceView: SurfaceView = index.requireView(R.id.surfaceView)
    val usbModeContent: LinearLayout = index.requireView(R.id.usbModeContent)
    val wirelessConnected: LinearLayout = index.requireView(R.id.wirelessConnected)
    val wirelessConnecting: LinearLayout = index.requireView(R.id.wirelessConnecting)
    val wirelessDisconnectButton: MaterialButton = index.requireView(R.id.wirelessDisconnectButton)
    val wirelessFirstTime: LinearLayout = index.requireView(R.id.wirelessFirstTime)
    val wirelessForgetButton: MaterialButton = index.requireView(R.id.wirelessForgetButton)
    val wirelessIdleForgetButton: MaterialButton = index.requireView(R.id.wirelessIdleForgetButton)
    val wirelessModeContent: LinearLayout = index.requireView(R.id.wirelessModeContent)
    val wirelessOpenSettingsButton: MaterialButton = index.requireView(R.id.wirelessOpenSettingsButton)
    val wirelessPairedIdle: LinearLayout = index.requireView(R.id.wirelessPairedIdle)
    val wirelessPermDenied: LinearLayout = index.requireView(R.id.wirelessPermDenied)
    val wirelessReconnectButton: MaterialButton = index.requireView(R.id.wirelessReconnectButton)
    val wirelessRescanButton: MaterialButton = index.requireView(R.id.wirelessRescanButton)
    val wirelessScanButton: MaterialButton = index.requireView(R.id.wirelessScanButton)
    val wirelessTokenMismatch: LinearLayout = index.requireView(R.id.wirelessTokenMismatch)

    companion object {
        private fun indexViews(root: View): SparseArray<View> {
            val views = SparseArray<View>(64)

            fun visit(view: View) {
                val id = view.id
                if (id != View.NO_ID && views[id] == null) views.put(id, view)
                if (view is ViewGroup) {
                    for (child in 0 until view.childCount) visit(view.getChildAt(child))
                }
            }
            visit(root)
            return views
        }
    }
}

private inline fun <reified T : View> SparseArray<View>.requireView(id: Int): T =
    T::class.java.cast(requireNotNull(get(id)))
