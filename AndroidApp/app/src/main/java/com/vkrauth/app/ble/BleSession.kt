package com.vkrauth.app.ble

import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.content.Context
import android.os.Build
import android.util.Log
import com.vkrauth.app.data.crypto.toHex
import com.vkrauth.app.hce.TapLog
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.atomic.AtomicReference

private const val TAG = "BleSession"

/**
 * Один BLE-сеанс с конкретным ридером (shared §16.6).
 *
 * Зеркало TapSession: тот же протокол операций, только wire = GATT, а не
 * APDU. Реализация — корутинная: подключение, exchangeMtu, enable notify
 * на двух характеристиках, push первого INFO от ридера, потом цикл
 * "write op → await result".
 *
 * Жизненный цикл:
 *   ```
 *   val session = BleSession(context, device)
 *   session.connect()              // suspends until INFO received
 *   val info = session.info        // 146 B
 *   val result = session.runOperation(opBytes)
 *   session.close()
 *   ```
 */
class BleSession(
    private val context: Context,
    private val device: BluetoothDevice,
    private val tapLog: TapLog? = null
) {
    enum class State { IDLE, CONNECTING, CONNECTED, EXCHANGING_MTU, READY, FAILED, CLOSED }

    private val _state = MutableStateFlow(State.IDLE)
    val state: StateFlow<State> = _state.asStateFlow()

    private var gatt: BluetoothGatt? = null
    private var opChr: BluetoothGattCharacteristic? = null
    private var resultChr: BluetoothGattCharacteristic? = null
    private var infoChr: BluetoothGattCharacteristic? = null
    private var ctlChr: BluetoothGattCharacteristic? = null
    private var mtu: Int = 23

    private val connectedDef = CompletableDeferred<Unit>()
    private val servicesDef  = CompletableDeferred<Unit>()
    private val mtuDef       = CompletableDeferred<Int>()

    /** 146 B INFO from reader (shared §5.2). null until READY. */
    @Volatile
    var info: ByteArray? = null
        private set

    // Inbound reassembly buffer per characteristic.
    private val infoReassembly = AtomicReference(ReassemblyBuf())
    private val resultReassembly = AtomicReference(ReassemblyBuf())

    // Channel of completed inbound INFO messages (unsolicited push, §16.6).
    private val infoChannel = Channel<ByteArray>(Channel.UNLIMITED)

    // B4: op↔result корреляция. Раньше result сопоставлялся позиционно
    // (resultChannel.receive() сразу после write op) — reorder/drop кадра
    // рассинхронизировал op↔result. Теперь каждое OP_WRITE-сообщение несёт
    // прикладной префикс [op_seq 1B][op_bytes...]; ридер эхо'ит op_seq первым
    // байтом RESULT_NOTIFY ([op_seq 1B][result_bytes...]). runOperation кладёт
    // Deferred в pending[op_seq], handleNotify(RESULT) стрипает op_seq и
    // комплитит именно его. op_seq — Byte, инкрементируется и оборачивается.
    private var nextOpSeq: Byte = 0
    private val pendingLock = Any()
    private val pending = HashMap<Byte, CompletableDeferred<ByteArray>>()

    // B1: ack одного исходящего PDU (onCharacteristicWrite). Сериализует chunked-write —
    // следующий кадр шлём только после колбэка предыдущего, иначе WRITE_NO_RESPONSE-пачка
    // переполняет очередь GATT и Android молча теряет кадры.
    private val pendingWrite = AtomicReference<CompletableDeferred<Int>?>(null)

    // Сериализация записи CCCD (onDescriptorWrite). Включаем notify на двух
    // характеристиках строго по очереди — второй write только после колбэка первого.
    private val pendingDescWrite = AtomicReference<CompletableDeferred<Int>?>(null)

    @SuppressLint("MissingPermission")
    suspend fun connect(timeoutMs: Long = 10_000): ByteArray = withTimeout(timeoutMs) {
        _state.value = State.CONNECTING
        gatt = device.connectGatt(context, /*autoConnect=*/false, gattCallback, BluetoothDevice.TRANSPORT_LE)
            ?: throw IllegalStateException("connectGatt returned null")

        Log.i(TAG, "connect: connectGatt issued, awaiting connection…")
        tapLog?.info("BLE connectGatt issued → awaiting connection")
        connectedDef.await()
        Log.i(TAG, "connect: GATT connected")
        tapLog?.info("BLE GATT connected")

        // MTU exchange ДО discoverServices — канонический порядок на Android: requestMtu
        // ПОСЛЕ discovery на многих стэках не вызывает onMtuChanged (MTU уже установлен),
        // и сессия висла на mtuDef.await() до внешнего таймаута 12 c. Плюс ридер (NimBLE)
        // мог сам инициировать обмен — тогда наш requestMtu() это no-op без колбэка.
        // Поэтому best-effort: ждём колбэк с КОРОТКИМ таймаутом и идём дальше с тем MTU,
        // что есть. §16.5-фрейминг режет под любой MTU (golden-вектор MTU=20), маленький
        // MTU корректен — просто чуть медленнее. НА MTU НЕ БЛОКИРУЕМСЯ.
        _state.value = State.EXCHANGING_MTU
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            val asked = gatt!!.requestMtu(BleConstants.MTU_REQUEST)
            Log.i(TAG, "connect: requestMtu()=$asked, awaiting onMtuChanged (best-effort 3s)…")
            if (asked) {
                mtu = withTimeoutOrNull(3_000L) { mtuDef.await() } ?: mtu
            }
            Log.i(TAG, "connect: MTU=$mtu")
            tapLog?.info("BLE MTU=$mtu")
        }

        Log.i(TAG, "connect: discoverServices…")
        tapLog?.info("BLE discoverServices…")
        gatt!!.discoverServices()
        servicesDef.await()
        Log.i(TAG, "connect: services discovered, enabling notifications…")
        tapLog?.info("BLE services discovered → enabling notifications")

        enableNotifications(infoChr!!)
        enableNotifications(resultChr!!)
        Log.i(TAG, "connect: notifications enabled, awaiting INFO push…")
        tapLog?.info("BLE notifications enabled → awaiting INFO push")

        // INFO push приходит после enable-notify (ридер дефёрит INFO на ble_info_defer мс).
        val firstInfo = infoChannel.receive()
        Log.i(TAG, "connect: INFO received (${firstInfo.size} B) → READY")
        tapLog?.recv("BLE INFO received (${firstInfo.size} B) → READY")
        info = firstInfo
        _state.value = State.READY
        return@withTimeout firstInfo
    }

    /** Отправить операцию (например ACCESS 256 B). Возвращает ответ ридера. */
    @SuppressLint("MissingPermission")
    suspend fun runOperation(opBytes: ByteArray, timeoutMs: Long = 5_000): ByteArray {
        val target = opChr ?: error("opChr not initialised")
        // B4: назначаем op_seq и регистрируем Deferred ДО write — иначе быстрый
        // ридер мог бы прислать RESULT_NOTIFY раньше, чем мы успели положить в map.
        val deferred = CompletableDeferred<ByteArray>()
        val opSeq: Byte
        synchronized(pendingLock) {
            opSeq = nextOpSeq
            nextOpSeq = (nextOpSeq + 1).toByte()   // Byte, оборачивается на 256
            pending[opSeq] = deferred
        }
        // B4: прикладной префикс [op_seq][op_bytes]. PDU-фрейминг (writeChunked →
        // BleFraming) разрежет это как единое сообщение — golden ble_framing неизменён.
        val framed = ByteArray(1 + opBytes.size)
        framed[0] = opSeq
        System.arraycopy(opBytes, 0, framed, 1, opBytes.size)
        try {
            return withTimeout(timeoutMs) {
                // B1: per-frame WRITE_ACK flow control сохранён внутри writeChunked.
                tapLog?.send("BLE op seq=$opSeq (${opBytes.size} B)")
                writeChunked(target, framed)
                val result = deferred.await()
                tapLog?.recv("BLE result seq=$opSeq (${result.size} B)")
                result
            }
        } finally {
            synchronized(pendingLock) { pending.remove(opSeq) }
        }
    }

    @SuppressLint("MissingPermission")
    fun sendControl(code: Byte) {
        val g = gatt ?: return
        val chr = ctlChr ?: return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            g.writeCharacteristic(chr, byteArrayOf(code), BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT)
        } else {
            @Suppress("DEPRECATION")
            run {
                chr.value = byteArrayOf(code)
                chr.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                g.writeCharacteristic(chr)
            }
        }
    }

    @SuppressLint("MissingPermission")
    fun close() {
        if (_state.value == State.CLOSED) return
        sendControl(BleConstants.CTL_END)
        try { gatt?.disconnect() } catch (_: Exception) {}
        try { gatt?.close() } catch (_: Exception) {}
        _state.value = State.CLOSED
    }

    // -----------------------------------------------------------------------
    // GATT internals
    // -----------------------------------------------------------------------

    private val gattCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            Log.i(TAG, "onConnectionStateChange status=$status newState=$newState")
            // status != GATT_SUCCESS (часто 133/8/19) на connect — это ОШИБКА. Раньше мы
            // комплитили connectedDef только на STATE_CONNECTED, а при ошибке приходил
            // DISCONNECTED и connectedDef.await() висел до внешнего таймаута 12 c (UI всё
            // это показывал как «connecting/negotiating MTU»). Теперь fail-fast: на любой
            // не-успешный статус или disconnect-до-готовности валим сессию с исключением.
            if (status != BluetoothGatt.GATT_SUCCESS) {
                _state.value = State.FAILED
                tapLog?.error("BLE GATT connect failed: status=$status")
                if (!connectedDef.isCompleted) {
                    connectedDef.completeExceptionally(
                        IllegalStateException("GATT connect failed: status=$status")
                    )
                }
                try { g.close() } catch (_: Exception) {}
                return
            }
            when (newState) {
                BluetoothProfile.STATE_CONNECTED -> {
                    _state.value = State.CONNECTED
                    connectedDef.complete(Unit)
                }
                BluetoothProfile.STATE_DISCONNECTED -> {
                    _state.value = State.CLOSED
                    if (!connectedDef.isCompleted) {
                        connectedDef.completeExceptionally(
                            IllegalStateException("disconnected before ready")
                        )
                    }
                }
            }
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            Log.i(TAG, "onServicesDiscovered status=$status")
            val svc = g.getService(BleConstants.SERVICE_UUID)
            if (svc == null) {
                _state.value = State.FAILED
                servicesDef.completeExceptionally(IllegalStateException("SCUD service not found"))
                return
            }
            infoChr   = svc.getCharacteristic(BleConstants.CHR_INFO_NOTIFY)
            opChr     = svc.getCharacteristic(BleConstants.CHR_OP_WRITE)
            resultChr = svc.getCharacteristic(BleConstants.CHR_RESULT_NOTIFY)
            ctlChr    = svc.getCharacteristic(BleConstants.CHR_CONTROL)
            if (infoChr == null || opChr == null || resultChr == null) {
                _state.value = State.FAILED
                servicesDef.completeExceptionally(IllegalStateException("missing characteristic"))
                return
            }
            servicesDef.complete(Unit)
        }

        override fun onMtuChanged(g: BluetoothGatt, mtu: Int, status: Int) {
            Log.i(TAG, "onMtuChanged mtu=$mtu status=$status")
            if (!mtuDef.isCompleted) mtuDef.complete(mtu)
        }

        override fun onCharacteristicWrite(
            g: BluetoothGatt,
            chr: BluetoothGattCharacteristic,
            status: Int
        ) {
            // B1: разблокируем отправку следующего кадра в writeChunked.
            pendingWrite.getAndSet(null)?.complete(status)
        }

        @Deprecated("API < 33")
        override fun onCharacteristicChanged(g: BluetoothGatt, chr: BluetoothGattCharacteristic) {
            handleNotify(chr, chr.value)
        }

        override fun onCharacteristicChanged(
            g: BluetoothGatt,
            chr: BluetoothGattCharacteristic,
            value: ByteArray
        ) { handleNotify(chr, value) }

        override fun onDescriptorWrite(g: BluetoothGatt, desc: BluetoothGattDescriptor, status: Int) {
            Log.i(TAG, "onDescriptorWrite uuid=${desc.uuid} status=$status")
            // Разблокируем enableNotifications, ожидающий ИМЕННО этот CCCD-write.
            pendingDescWrite.getAndSet(null)?.complete(status)
        }
    }

    private fun handleNotify(chr: BluetoothGattCharacteristic, value: ByteArray) {
        val reassembly = when (chr.uuid) {
            BleConstants.CHR_INFO_NOTIFY   -> infoReassembly
            BleConstants.CHR_RESULT_NOTIFY -> resultReassembly
            else -> return
        }

        // Реассемблинг PDU-фрейминга [seq][flags][total_len] — общий ReassemblyBuf
        // (host-тестится против golden ble_framing). Префикс op_seq разбираем уже
        // НАД фреймингом, на готовом сообщении.
        val buf = reassembly.get()
        val completed = buf.feed(value) ?: return
        reassembly.set(ReassemblyBuf())

        if (chr.uuid == BleConstants.CHR_INFO_NOTIFY) {
            // INFO — unsolicited push без op_seq (§16.6) → свой канал, путь не тронут.
            infoChannel.trySend(completed)
            return
        }

        // B4: RESULT_NOTIFY-сообщение = [op_seq 1B][result_bytes...]. Стрипаем
        // op_seq и комплитим именно тот pending-op, что его прислал. Reorder/drop
        // больше не рассинхронизирует — нет позиционного receive().
        if (completed.isEmpty()) {
            Log.w("BleSession", "RESULT notify with empty message — drop")
            return
        }
        val opSeq = completed[0]
        val result = completed.copyOfRange(1, completed.size)
        val deferred = synchronized(pendingLock) { pending.remove(opSeq) }
        if (deferred == null) {
            Log.w("BleSession", "RESULT for unknown op_seq=$opSeq — drop (reorder/late?)")
            return
        }
        deferred.complete(result)
    }

    @SuppressLint("MissingPermission")
    private suspend fun enableNotifications(chr: BluetoothGattCharacteristic) {
        val g = gatt ?: return
        g.setCharacteristicNotification(chr, true)
        val cccd = chr.getDescriptor(BleConstants.CCC_DESCRIPTOR) ?: return
        val ack = CompletableDeferred<Int>()
        pendingDescWrite.set(ack)
        val value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
        // Android 13+ (API 33): старый `cccd.value=…; writeDescriptor(cccd)` — no-op,
        // onDescriptorWrite не приходит и notify НИКОГДА не включается (сессия висла на
        // этом шаге до таймаута 12 c). Используем новый writeDescriptor(desc, value).
        // Плюс сериализация: ждём колбэк ЭТОГО write перед следующим enable — два
        // writeDescriptor подряд Android роняет как "GATT busy".
        val ok = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            g.writeDescriptor(cccd, value) == BluetoothStatusCodes.SUCCESS
        } else {
            @Suppress("DEPRECATION")
            run { cccd.value = value; g.writeDescriptor(cccd) }
        }
        if (!ok) { pendingDescWrite.compareAndSet(ack, null); return }
        withTimeoutOrNull(WRITE_ACK_TIMEOUT_MS) { ack.await() }
    }

    /**
     * Чанкует исходящее сообщение по схеме §16.5 и отправляет write_no_response.
     * Первый PDU содержит total_len; последний — flag LAST.
     */
    @SuppressLint("MissingPermission")
    private suspend fun writeChunked(chr: BluetoothGattCharacteristic, data: ByteArray) {
        val g = gatt ?: error("not connected")
        // safe MTU: реальная полезная нагрузка = mtu - 3 (ATT header). Берём 20 если MTU не согласован.
        val maxPdu = if (mtu > 23) mtu - 3 else 20
        // Нарезка — в чистом BleFraming (юнит-тестится против golden ble_framing).
        val frames = BleFraming.frame(data, maxPdu)
        for ((i, pdu) in frames.withIndex()) {
            // B1: сериализуем кадры — следующий шлём только после onCharacteristicWrite
            // предыдущего (flow control). Иначе пачка WRITE_NO_RESPONSE переполняет очередь
            // GATT и Android молча выкидывает кадры → ридер видит seq-gap и рвёт операцию.
            val ack = CompletableDeferred<Int>()
            pendingWrite.set(ack)
            // Android 13+ (API 33): новый writeCharacteristic(chr, value, type). Старый
            // `chr.value=…; writeCharacteristic(chr)` deprecated и ненадёжен на Android 13+.
            val sent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                g.writeCharacteristic(chr, pdu, BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE) ==
                    BluetoothStatusCodes.SUCCESS
            } else {
                @Suppress("DEPRECATION")
                run {
                    chr.value = pdu
                    chr.writeType = BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
                    g.writeCharacteristic(chr)
                }
            }
            if (!sent) {
                pendingWrite.compareAndSet(ack, null)
                error("writeCharacteristic rejected at frame=$i")
            }
            withTimeout(WRITE_ACK_TIMEOUT_MS) { ack.await() }
        }
    }

    private companion object {
        // B1: бюджет ожидания onCharacteristicWrite на один кадр.
        const val WRITE_ACK_TIMEOUT_MS = 3_000L
    }
}

/**
 * Reassembly буфер: накапливает framed PDU от ридера в один логический blob.
 * Возвращает blob только когда пришёл PDU с FLAG_LAST. `internal` — чтобы
 * conformance-тест прогонял РЕАЛЬНУЮ реассемблирующую логику против golden-векторов.
 */
internal class ReassemblyBuf {
    private var totalLen: Int = -1
    private val received = mutableListOf<ByteArray>()
    private var expectedSeq: Byte = 0

    fun feed(pdu: ByteArray): ByteArray? {
        if (pdu.size < 2) return null
        val seq = pdu[0]
        val flags = pdu[1]
        var off = 2
        if (flags.toInt() and BleConstants.FLAG_HAS_TOTAL.toInt() != 0) {
            if (pdu.size < 6) return null
            totalLen = ByteBuffer.wrap(pdu, 2, 4).order(ByteOrder.LITTLE_ENDIAN).int
            off = 6
            received.clear()
            expectedSeq = 0
        }
        if (seq != expectedSeq) {
            Log.w("BleSession", "frame seq mismatch: got=$seq expect=$expectedSeq — drop msg")
            totalLen = -1
            received.clear()
            return null
        }
        received.add(pdu.copyOfRange(off, pdu.size))
        expectedSeq = ((expectedSeq.toInt() + 1) and 0xFF).toByte()
        if (flags.toInt() and BleConstants.FLAG_LAST.toInt() != 0) {
            val joined = ByteArray(received.sumOf { it.size })
            var pos = 0
            for (c in received) { System.arraycopy(c, 0, joined, pos, c.size); pos += c.size }
            if (totalLen > 0 && joined.size != totalLen) {
                Log.w("BleSession", "total_len mismatch: got=${joined.size} expected=$totalLen")
            }
            return joined
        }
        return null
    }
}
