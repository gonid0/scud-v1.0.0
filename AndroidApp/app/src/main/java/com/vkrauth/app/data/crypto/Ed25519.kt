package com.vkrauth.app.data.crypto

import org.bouncycastle.crypto.params.Ed25519PrivateKeyParameters
import org.bouncycastle.crypto.params.Ed25519PublicKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer
import java.security.SecureRandom

object Ed25519 {

    fun generateKeyPair(): Pair<ByteArray, ByteArray> {
        val rnd = SecureRandom()
        val priv = Ed25519PrivateKeyParameters(rnd)
        val pub = priv.generatePublicKey()
        return priv.encoded to pub.encoded
    }

    fun publicKeyFromPrivate(privKeyBytes: ByteArray): ByteArray {
        val priv = Ed25519PrivateKeyParameters(privKeyBytes, 0)
        return priv.generatePublicKey().encoded
    }

    fun sign(privKeyBytes: ByteArray, message: ByteArray): ByteArray {
        val priv = Ed25519PrivateKeyParameters(privKeyBytes, 0)
        val signer = Ed25519Signer()
        signer.init(true, priv)
        signer.update(message, 0, message.size)
        return signer.generateSignature()
    }

    fun verify(pubKeyBytes: ByteArray, message: ByteArray, signature: ByteArray): Boolean {
        if (pubKeyBytes.size != 32 || signature.size != 64) return false
        return try {
            val pub = Ed25519PublicKeyParameters(pubKeyBytes, 0)
            val verifier = Ed25519Signer()
            verifier.init(false, pub)
            verifier.update(message, 0, message.size)
            verifier.verifySignature(signature)
        } catch (_: Exception) {
            false
        }
    }
}
