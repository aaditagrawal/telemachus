package dev.telemachus.display

import java.util.concurrent.Executors
import java.util.concurrent.Future

/** Owns one blocking connection and closes it before a replacement can start. */
class ConnectionWorker<T>(
    private val disconnect: (T) -> Unit,
) : AutoCloseable {
    private var current: T? = null
    private var task: Future<*>? = null
    private var closed = false

    @Synchronized
    fun start(
        client: T,
        connect: () -> Unit,
    ) {
        if (closed) {
            disconnect(client)
            return
        }
        cancel()
        current = client
        task = executor.submit(connect)
    }

    @Synchronized
    fun cancel() {
        val previous = current
        current = null
        previous?.let(disconnect)
        task?.cancel(true)
        task = null
    }

    @Synchronized
    override fun close() {
        closed = true
        cancel()
    }

    companion object {
        // Share idle IO threads across Activity lifetimes, as the previous
        // dispatcher did. Each owner cancels its task and closes its socket.
        // DNS can ignore interruption, so canceled attempts must not queue new ones.
        private val executor =
            Executors.newCachedThreadPool { runnable ->
                Thread(runnable, "ConnectionIO").apply { isDaemon = true }
            }
    }
}
