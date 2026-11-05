package dev.telemachus.display

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.DataInputStream
import java.io.DataOutputStream
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Runs the two real stream clients against identical loopback wire fixtures. */
class StreamDependencyPerformanceTest {
    private interface Client : AutoCloseable {
        fun start(
            port: Int,
            connected: CountDownLatch,
            frame: (ByteArray, Int, Boolean) -> Unit,
            wireless: Boolean,
        )

        fun touch(index: Int)
    }

    private class OldClient(
        private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO),
        private val ownsScope: Boolean = true,
    ) : Client {
        private lateinit var client: BaselineStreamClient

        override fun start(
            port: Int,
            connected: CountDownLatch,
            frame: (ByteArray, Int, Boolean) -> Unit,
            wireless: Boolean,
        ) {
            client = BaselineStreamClient("127.0.0.1", port)
            client.onConnectionStatus = { if (it) connected.countDown() }
            client.onFrameReceived = { bytes, size, _, sync ->
                frame(bytes, size, sync)
                client.releaseBuffer(bytes)
            }
            scope.launch {
                if (wireless) client.connectWireless(ByteArray(32), "benchmark-device") else client.connect()
            }
        }

        override fun touch(index: Int) = client.sendTouch(index.toFloat(), 0.25f, 2, 2, 0.5f, 0.75f)

        override fun close() {
            client.disconnect()
            if (ownsScope) scope.cancel()
        }
    }

    private class NewClient(
        private val worker: ConnectionWorker<StreamClient> = ConnectionWorker { it.disconnect() },
        private val ownsWorker: Boolean = true,
    ) : Client {
        private lateinit var client: StreamClient

        override fun start(
            port: Int,
            connected: CountDownLatch,
            frame: (ByteArray, Int, Boolean) -> Unit,
            wireless: Boolean,
        ) {
            client = StreamClient("127.0.0.1", port)
            client.onConnectionStatus = { if (it) connected.countDown() }
            client.onFrameReceived = { bytes, size, _, sync ->
                frame(bytes, size, sync)
                client.releaseBuffer(bytes)
            }
            worker.start(client) {
                if (wireless) client.connectWireless(ByteArray(32), "benchmark-device") else client.connect()
            }
        }

        override fun touch(index: Int) = client.sendTouch(index.toFloat(), 0.25f, 2, 2, 0.5f, 0.75f)

        override fun close() {
            if (ownsWorker) worker.close() else worker.cancel()
        }
    }

    private class Session(
        candidate: Boolean,
        wireless: Boolean = false,
        sharedClient: Client? = null,
        frame: (ByteArray, Int, Boolean) -> Unit = { _, _, _ -> },
    ) : AutoCloseable {
        val client: Client = sharedClient ?: if (candidate) NewClient() else OldClient()
        private val server = ServerSocket(0)
        val peer: Socket
        val input: DataInputStream
        val output: DataOutputStream

        init {
            val connected = CountDownLatch(1)
            client.start(server.localPort, connected, frame, wireless)
            server.soTimeout = 5_000
            peer =
                server.accept().apply {
                    tcpNoDelay = true
                    soTimeout = 5_000
                }
            input = DataInputStream(peer.getInputStream())
            output = DataOutputStream(peer.getOutputStream())
            if (wireless) {
                val expected = AuthHandshake.encodeRequest(ByteArray(32), "benchmark-device")
                val request = ByteArray(expected.size)
                input.readFully(request)
                assertTrue(expected.contentEquals(request))
                output.write(byteArrayOf(0x53, 0x53, 0x57, 0x52, 0))
                output.flush()
            }
            assertTrue(connected.await(5, TimeUnit.SECONDS))
            // Optional AVC-only, then metadata and device-info offers.
            var offer = input.readUnsignedByte()
            if (offer == 9) offer = input.readUnsignedByte()
            assertEquals(8, offer)
            assertEquals(12, input.readUnsignedByte())
        }

        override fun close() {
            client.close()
            peer.close()
            server.close()
        }
    }

    @Test
    fun compareControlFrameAndTeardownCosts() {
        val runtime = Runtime.getRuntime()
        println(
            "ENV java=${System.getProperty("java.version")} processors=${runtime.availableProcessors()} " +
                "maxHeapBytes=${runtime.maxMemory()}",
        )
        compare("connection-ready", "us/connect") { candidate ->
            val started = System.nanoTime()
            Session(candidate).use { (System.nanoTime() - started) / 1000.0 }
        }
        compare("wireless-auth-ready", "us/connect") { candidate ->
            val started = System.nanoTime()
            Session(candidate, wireless = true).use { (System.nanoTime() - started) / 1000.0 }
        }
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
        val worker = ConnectionWorker<StreamClient> { it.disconnect() }
        try {
            compare("reconnect-ready", "us/connect") { candidate ->
                val started = System.nanoTime()
                val client = if (candidate) NewClient(worker, false) else OldClient(scope, false)
                Session(candidate, sharedClient = client).use { (System.nanoTime() - started) / 1000.0 }
            }
        } finally {
            scope.cancel()
            worker.close()
        }
        for (count in listOf(1, 64, 1024)) {
            compare("control-$count", "us/event") { candidate ->
                Session(candidate).use { session ->
                    val received = CountDownLatch(1)
                    var failure: Throwable? = null
                    val reader =
                        Thread {
                            try {
                                repeat(count) { index ->
                                    val packet = ByteArray(22)
                                    session.input.readFully(packet)
                                    val decoded =
                                        java.nio.ByteBuffer
                                            .wrap(packet)
                                            .order(java.nio.ByteOrder.LITTLE_ENDIAN)
                                    assertEquals(2, decoded.get().toInt())
                                    assertEquals(2, decoded.get().toInt())
                                    assertEquals(index.toFloat(), decoded.float, 0f)
                                    assertEquals(0.25f, decoded.float, 0f)
                                    assertEquals(0.5f, decoded.float, 0f)
                                    assertEquals(0.75f, decoded.float, 0f)
                                    assertEquals(2, decoded.int)
                                }
                            } catch (error: Throwable) {
                                failure = error
                            } finally {
                                received.countDown()
                            }
                        }
                    reader.start()
                    val started = System.nanoTime()
                    repeat(count) { session.client.touch(it) }
                    assertTrue(received.await(5, TimeUnit.SECONDS))
                    val elapsed = System.nanoTime() - started
                    reader.join()
                    failure?.let { throw it }
                    elapsed / 1000.0 / count
                }
            }
        }
        for (size in listOf(1024, 100 * 1024, 1024 * 1024)) {
            compare("frame-$size", "us/frame") { candidate ->
                val received = CountDownLatch(8)
                val payload = ByteArray(size) { (it % 251).toByte() }
                var failure: Throwable? = null
                Session(candidate) { bytes, length, sync ->
                    try {
                        assertEquals(size, length)
                        assertTrue(sync)
                        // Full payload validation is identical in both measured paths.
                        assertEquals(-1, java.util.Arrays.mismatch(payload, 0, size, bytes, 0, length))
                    } catch (error: Throwable) {
                        failure = error
                    } finally {
                        received.countDown()
                    }
                }.use { session ->
                    val started = System.nanoTime()
                    repeat(8) {
                        session.output.writeByte(6)
                        session.output.writeInt(size)
                        session.output.writeByte(1)
                        session.output.writeLong(0)
                        session.output.write(payload)
                        session.output.flush()
                    }
                    assertTrue(received.await(10, TimeUnit.SECONDS))
                    val elapsed = System.nanoTime() - started
                    failure?.let { throw it }
                    elapsed / 1000.0 / 8
                }
            }
        }
        compare("blocked-read-teardown", "us/close") { candidate ->
            Session(candidate).use { session ->
                val started = System.nanoTime()
                session.client.close()
                // EOF at the peer confirms that a blocking read was interrupted.
                assertEquals(-1, session.input.read())
                (System.nanoTime() - started) / 1000.0
            }
        }
    }

    private fun compare(
        name: String,
        unit: String,
        run: (Boolean) -> Double,
    ) {
        repeat(6) {
            run(false)
            run(true)
        }
        val baseline = ArrayList<Double>()
        val candidate = ArrayList<Double>()
        repeat(24) { index ->
            if (index % 2 == 0) {
                baseline.add(run(false))
                candidate.add(run(true))
            } else {
                candidate.add(run(true))
                baseline.add(run(false))
            }
        }

        fun summary(samples: List<Double>): String {
            val sorted = samples.sorted()
            return "median=${(sorted[11] + sorted[12]) / 2},p90=${sorted[21]},min=${sorted.first()},max=${sorted.last()}"
        }
        println("PERF $name $unit samples=24 baseline=${summary(baseline)} candidate=${summary(candidate)}")
    }
}
