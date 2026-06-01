package com.vkrauth.app.data.crypto

import android.content.Context
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import dagger.hilt.android.qualifiers.ApplicationContext
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class KeyManager @Inject constructor(
    @ApplicationContext private val context: Context
) {
    private val keystoreAlias = "scud_wrapping_key"

    private val prefs: SharedPreferences by lazy {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            "scud_secure_prefs",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    }

    fun generateAndStore(): ByteArray {
        val (priv, pub) = Ed25519.generateKeyPair()
        val aesKey = getOrCreateWrappingKey()
        val (iv, wrapped) = aesGcmEncrypt(aesKey, priv)
        priv.fill(0)

        prefs.edit()
            .putString("ed_priv_wrapped_b64", Base64.encodeToString(wrapped, Base64.NO_WRAP))
            .putString("ed_priv_iv_b64", Base64.encodeToString(iv, Base64.NO_WRAP))
            .putString("ed_pub_b64", Base64.encodeToString(pub, Base64.NO_WRAP))
            .apply()

        return pub
    }

    fun hasKeyPair(): Boolean = prefs.contains("ed_priv_wrapped_b64")

    fun getPublicKey(): ByteArray {
        val s = prefs.getString("ed_pub_b64", null) ?: error("no keypair")
        return Base64.decode(s, Base64.NO_WRAP)
    }

    fun sign(message: ByteArray): ByteArray {
        val wrapped = Base64.decode(
            prefs.getString("ed_priv_wrapped_b64", null) ?: error("no keypair"),
            Base64.NO_WRAP
        )
        val iv = Base64.decode(
            prefs.getString("ed_priv_iv_b64", null) ?: error("no keypair"),
            Base64.NO_WRAP
        )
        val aesKey = getWrappingKey() ?: error("keystore key missing")

        val priv = aesGcmDecrypt(aesKey, iv, wrapped)
        try {
            return Ed25519.sign(priv, message)
        } finally {
            priv.fill(0)
        }
    }

    fun clear() {
        prefs.edit().clear().apply()
        try {
            val ks = KeyStore.getInstance("AndroidKeyStore")
            ks.load(null)
            ks.deleteEntry(keystoreAlias)
        } catch (_: Exception) {
        }
    }

    private fun getOrCreateWrappingKey(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore")
        ks.load(null)
        if (ks.containsAlias(keystoreAlias)) {
            return (ks.getEntry(keystoreAlias, null) as KeyStore.SecretKeyEntry).secretKey
        }

        // StrongBox есть лишь у части устройств (Pixel 3+, отд. флагманы) и НИКОГДА у эмулятора.
        // setIsStrongBoxBacked() не бросает исключение сам — StrongBoxUnavailableException летит
        // позже из generateKey(), поэтому раньше try/catch вокруг сеттера ничего не ловил, и на
        // Android 13 без StrongBox логин падал с "failed to generate key". Проверяем фичу заранее
        // и дополнительно деградируем при любом сбое генерации.
        val hasStrongBox = Build.VERSION.SDK_INT >= Build.VERSION_CODES.P &&
            context.packageManager.hasSystemFeature(PackageManager.FEATURE_STRONGBOX_KEYSTORE)

        return generateWrappingKey(strongBox = hasStrongBox, unlockedDeviceRequired = true)
    }

    private fun generateWrappingKey(strongBox: Boolean, unlockedDeviceRequired: Boolean): SecretKey {
        val keyGen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        val specBuilder = KeyGenParameterSpec.Builder(
            keystoreAlias,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .setUserAuthenticationRequired(false)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            if (unlockedDeviceRequired) specBuilder.setUnlockedDeviceRequired(true)
            if (strongBox) specBuilder.setIsStrongBoxBacked(true)
        }

        return try {
            keyGen.init(specBuilder.build())
            keyGen.generateKey()
        } catch (e: Exception) {
            // Прогрессивная деградация: сначала снимаем StrongBox, затем unlockedDeviceRequired.
            // Лучше менее «строгий» рабочий ключ, чем неработающая авторизация. generateKey()
            // перезаписывает alias, поэтому повторная попытка с тем же alias безопасна.
            when {
                strongBox -> generateWrappingKey(strongBox = false, unlockedDeviceRequired = unlockedDeviceRequired)
                unlockedDeviceRequired -> generateWrappingKey(strongBox = false, unlockedDeviceRequired = false)
                else -> throw e
            }
        }
    }

    private fun getWrappingKey(): SecretKey? {
        val ks = KeyStore.getInstance("AndroidKeyStore")
        ks.load(null)
        return (ks.getEntry(keystoreAlias, null) as? KeyStore.SecretKeyEntry)?.secretKey
    }

    private fun aesGcmEncrypt(key: SecretKey, plaintext: ByteArray): Pair<ByteArray, ByteArray> {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key)
        val iv = cipher.iv
        val ct = cipher.doFinal(plaintext)
        return iv to ct
    }

    private fun aesGcmDecrypt(key: SecretKey, iv: ByteArray, ct: ByteArray): ByteArray {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(128, iv))
        return cipher.doFinal(ct)
    }
}
