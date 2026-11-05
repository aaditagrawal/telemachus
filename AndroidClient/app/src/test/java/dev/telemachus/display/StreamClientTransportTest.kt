package dev.telemachus.display

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.DataOutputStream
import java.net.ServerSocket
import java.net.Socket
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.CountDownLatch
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class StreamClientTransportTest {
    @Test
    fun controlsRemainOrderedAndFramesKeepTheirPayloadAndMetadata() {
        ServerSocket(0).use { server ->
            val client = StreamClient("127.0.0.1", server.localPort)
            val worker = Executors.newSingleThreadExecutor()
            val connected = CountDownLatch(1)
            val received = CountDownLatch(1)
            val payload = byteArrayOf(0, 0, 0, 1, 0x26, 1, 2, 3)
            client.onConnectionStatus = { if (it) connected.countDown() }
            client.onFrameReceived = { bytes, size, timestamp, keyframe ->
                assertArrayEquals(payload, bytes.copyOf(size))
                assertTrue(timestamp > 0)
                assertTrue(keyframe)
                client.releaseBuffer(bytes)
                received.countDown()
            }
            try {
                val connection = worker.submit { client.connect() }
                server.accept().use { peer ->
                    peer.soTimeout = 2_000
                    assertTrue(connected.await(2, TimeUnit.SECONDS))
                    drainCapabilities(peer)
                    client.sendTouch(0.25f, 0.75f, 1)
                    client.requestKeyframe(force = true)
                    client.sendPing()
                    val controls = ByteArray(14 + 2 + 9)
                    java.io.DataInputStream(peer.getInputStream()).readFully(controls)
                    val data = ByteBuffer.wrap(controls).order(ByteOrder.LITTLE_ENDIAN)
                    assertEquals(2, data.get().toInt())
                    assertEquals(1, data.get().toInt())
                    assertEquals(0.25f, data.float, 0f)
                    assertEquals(0.75f, data.float, 0f)
                    assertEquals(1, data.int)
                    assertEquals(7, data.get().toInt())
                    assertEquals(1, data.get().toInt())
                    assertEquals(4, data.get().toInt())
                    assertTrue(data.long > 0)

                    DataOutputStream(peer.getOutputStream()).apply {
                        writeByte(6)
                        writeInt(payload.size)
                        writeByte(1)
                        writeLong(123)
                        write(payload)
                        flush()
                    }
                    assertTrue(received.await(2, TimeUnit.SECONDS))
                    client.disconnect()
                    connection.get(2, TimeUnit.SECONDS)
                }
            } finally {
                client.disconnect()
                worker.shutdownNow()
            }
        }
    }

    @Test
    fun closingSocketStopsBackpressuredControlWriterAndReceiveWorker() {
        ServerSocket(0).use { server ->
            val client = StreamClient("127.0.0.1", server.localPort)
            val worker = Executors.newSingleThreadExecutor()
            val connected = CountDownLatch(1)
            client.onConnectionStatus = { if (it) connected.countDown() }
            try {
                val connection = worker.submit { client.connect() }
                server.accept().use { peer ->
                    peer.receiveBufferSize = 1024
                    peer.soTimeout = 2_000
                    assertTrue(connected.await(2, TimeUnit.SECONDS))
                    drainCapabilities(peer)
                    val socket =
                        StreamClient::class.java
                            .getDeclaredField("socket")
                            .apply { isAccessible = true }
                            .get(client) as Socket
                    socket.sendBufferSize = 1024
                    repeat(10_000) { client.sendTouch(0.5f, 0.5f, 1) }
                    client.disconnect()
                    connection.get(2, TimeUnit.SECONDS)
                    val executor =
                        StreamClient::class.java
                            .getDeclaredField("touchExecutor")
                            .apply {
                                isAccessible =
                                    true
                            }.get(client) as ExecutorService
                    assertTrue(executor.awaitTermination(2, TimeUnit.SECONDS))
                }
            } finally {
                client.disconnect()
                worker.shutdownNow()
            }
        }
    }

    private fun drainCapabilities(peer: Socket) {
        val input = peer.getInputStream()
        var type = input.read()
        if (type == 9) type = input.read()
        assertEquals(8, type)
        assertEquals(12, input.read())
    }
}
