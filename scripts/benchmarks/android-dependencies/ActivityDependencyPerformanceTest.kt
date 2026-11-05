package dev.telemachus.display

import android.app.Activity
import android.os.Looper
import android.widget.EditText
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import java.io.DataInputStream
import java.net.ServerSocket
import java.net.SocketTimeoutException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [28])
class ActivityDependencyPerformanceTest {
    @Test
    fun compareActualActivityPingAndChecklistScheduling() {
        val oldController = Robolectric.buildActivity(BaselineMainActivity::class.java).setup()
        val newController = Robolectric.buildActivity(MainActivity::class.java).setup()
        val activities = listOf(oldController.get(), newController.get())
        try {
            activities.forEach { call(it, "stopChecklistUpdates") }
            val pingSamples = activities.map { ArrayList<Double>() }
            val probeSamples = activities.map { ArrayList<Double>() }
            // Both real activity timer implementations use the same sender here.
            // StreamDependencyPerformanceTest measures the sender replacement separately.
            ServerSocket(0).use { server ->
                val client = StreamClient("127.0.0.1", server.localPort)
                val worker = ConnectionWorker<StreamClient> { it.disconnect() }
                val ready = CountDownLatch(1)
                client.onConnectionStatus = { if (it) ready.countDown() }
                worker.start(client) { client.connect() }
                server.accept().use { peer ->
                    peer.soTimeout = 5_000
                    val input = DataInputStream(peer.getInputStream())
                    assertTrue(ready.await(5, TimeUnit.SECONDS))
                    var offer = input.readUnsignedByte()
                    if (offer == 9) offer = input.readUnsignedByte()
                    assertEquals(8, offer)
                    assertEquals(12, input.readUnsignedByte())
                    activities.forEach { field(it, "streamClient", client) }
                    repeat(14) { round ->
                        val order = if (round % 2 == 0) listOf(0, 1) else listOf(1, 0)
                        for (index in order) {
                            val started = System.nanoTime()
                            call(activities[index], "startPingTimer")
                            assertEquals(4, input.readUnsignedByte())
                            input.readLong()
                            val delayUs = (System.nanoTime() - started - 1_000_000_000L) / 1000.0
                            call(activities[index], "stopPingTimer")
                            if (round >= 2) pingSamples[index].add(delayUs)
                        }
                    }
                    // Cancellation must leave no delayed ping on the socket.
                    peer.soTimeout = 1_100
                    try {
                        input.readByte()
                        throw AssertionError("Ping remained scheduled after stop")
                    } catch (_: SocketTimeoutException) {
                    }
                    activities.forEach { field(it, "streamClient", null) }
                }
                worker.close()
            }
            ServerSocket(0).use { server ->
                server.soTimeout = 5_000
                activities.forEach {
                    field(it, "connectionDetailsVisible", true)
                    it.findViewById<EditText>(R.id.portInput).setText(server.localPort.toString())
                }
                repeat(30) { round ->
                    val order = if (round % 2 == 0) listOf(0, 1) else listOf(1, 0)
                    for (index in order) {
                        val started = System.nanoTime()
                        call(activities[index], "updateChecklist")
                        server.accept().use { peer ->
                            val elapsedUs = (System.nanoTime() - started) / 1000.0
                            peer.getOutputStream().write(1)
                            peer.soTimeout = 5_000
                            assertEquals(-1, peer.getInputStream().read())
                            shadowOf(Looper.getMainLooper()).idle()
                            if (round >= 6) probeSamples[index].add(elapsedUs)
                        }
                    }
                }
            }
            printResult("scheduled-ping-over-1s", pingSamples)
            printResult("checklist-to-probe", probeSamples)
        } finally {
            newController.pause().stop().destroy()
            oldController.pause().stop().destroy()
        }
    }

    private fun call(
        activity: Activity,
        name: String,
    ) {
        activity.javaClass
            .getDeclaredMethod(name)
            .apply { isAccessible = true }
            .invoke(activity)
    }

    private fun field(
        activity: Activity,
        name: String,
        value: Any?,
    ) {
        activity.javaClass
            .getDeclaredField(name)
            .apply { isAccessible = true }
            .set(activity, value)
    }

    private fun printResult(
        name: String,
        samples: List<List<Double>>,
    ) {
        fun summary(values: List<Double>): String {
            val sorted = values.sorted()
            val half = sorted.size / 2
            return "median=${(sorted[half - 1] + sorted[half]) / 2}," +
                "p90=${sorted[(sorted.size * .9).toInt() - 1]},min=${sorted.first()},max=${sorted.last()}"
        }
        println("PERF $name us/op samples=${samples[0].size} baseline=${summary(samples[0])} candidate=${summary(samples[1])}")
    }
}
