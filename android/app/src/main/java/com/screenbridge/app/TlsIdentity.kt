package com.screenbridge.app

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.math.BigInteger
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.Date
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager
import javax.security.auth.x500.X500Principal

object TlsIdentity {
    private const val ALIAS = "screenbridge-host"

    fun normalized(value: String): String {
        val v = value.replace(":", "").replace(" ", "").trim().lowercase()
        require(v.matches(Regex("[0-9a-f]{64}"))) { "A complete SHA-256 fingerprint is required" }
        return v
    }

    fun hex(bytes: ByteArray) = bytes.joinToString("") { "%02x".format(it.toInt() and 0xff) }
    fun fingerprint(cert: X509Certificate) = hex(MessageDigest.getInstance("SHA-256").digest(cert.encoded))
    fun pretty(value: String) = normalized(value).uppercase().chunked(2).joinToString(":")

    @Synchronized
    fun host(): Pair<SSLContext, String> {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (!store.containsAlias(ALIAS)) {
            val gen = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_RSA, "AndroidKeyStore")
            gen.initialize(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_DECRYPT)
                .setKeySize(2048)
                .setDigests(KeyProperties.DIGEST_SHA256, KeyProperties.DIGEST_SHA384, KeyProperties.DIGEST_SHA512)
                .setSignaturePaddings(KeyProperties.SIGNATURE_PADDING_RSA_PKCS1)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_RSA_PKCS1)
                .setCertificateSubject(X500Principal("CN=ScreenBridge local host"))
                .setCertificateSerialNumber(BigInteger(128, SecureRandom()).abs().add(BigInteger.ONE))
                .setCertificateNotBefore(Date(System.currentTimeMillis() - 86400000L))
                .setCertificateNotAfter(Date(System.currentTimeMillis() + 315360000000L))
                .build())
            gen.generateKeyPair()
        }
        val km = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
        km.init(store, null)
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(km.keyManagers, null, SecureRandom())
        return Pair(ctx, fingerprint(store.getCertificate(ALIAS) as X509Certificate))
    }

    fun pinnedClient(expected: String): SSLContext {
        val expectedBytes = normalized(expected).chunked(2).map { it.toInt(16).toByte() }.toByteArray()
        val trust = object : X509TrustManager {
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
            override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {
                throw CertificateException("Client certificates are not used")
            }
            override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
                if (chain.isNullOrEmpty()) throw CertificateException("Missing server certificate")
                val actual = MessageDigest.getInstance("SHA-256").digest(chain[0].encoded)
                if (!MessageDigest.isEqual(actual, expectedBytes)) throw CertificateException("Fingerprint mismatch")
            }
        }
        return SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), SecureRandom()) }
    }
}
