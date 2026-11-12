package com.screenbridge.app

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.math.BigInteger
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.net.Socket
import java.security.SecureRandom
import java.security.Principal
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.Date
import javax.net.ssl.X509KeyManager
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager
import javax.security.auth.x500.X500Principal

object TlsIdentity {
    // A new alias ensures existing restrictive key parameters are not reused.
    private const val ALIAS = "screenbridge-host-tls-v2"

    @Synchronized
    fun host(): SSLContext {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (!store.containsAlias(ALIAS)) {
            val gen = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_RSA, "AndroidKeyStore")
            gen.initialize(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_DECRYPT)
                .setKeySize(2048)
                .setDigests(KeyProperties.DIGEST_NONE, KeyProperties.DIGEST_SHA256, KeyProperties.DIGEST_SHA384, KeyProperties.DIGEST_SHA512)
                .setSignaturePaddings(KeyProperties.SIGNATURE_PADDING_RSA_PKCS1)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_RSA_PKCS1, KeyProperties.ENCRYPTION_PADDING_NONE)
                .setCertificateSubject(X500Principal("CN=ScreenBridge local host"))
                .setCertificateSerialNumber(BigInteger(128, SecureRandom()).abs().add(BigInteger.ONE))
                .setCertificateNotBefore(Date(System.currentTimeMillis() - 86400000L))
                .setCertificateNotAfter(Date(System.currentTimeMillis() + 315360000000L))
                .build())
            gen.generateKeyPair()
        }
        val key = store.getKey(ALIAS, null) as PrivateKey
        val certificate = store.getCertificate(ALIAS) as X509Certificate
        val manager = object : X509KeyManager {
            override fun getClientAliases(keyType: String?, issuers: Array<out Principal>?): Array<String>? = null
            override fun chooseClientAlias(keyTypes: Array<out String>?, issuers: Array<out Principal>?, socket: Socket?): String? = null
            override fun getServerAliases(keyType: String?, issuers: Array<out Principal>?): Array<String>? =
                if (keyType == "RSA") arrayOf(ALIAS) else null
            override fun chooseServerAlias(keyType: String?, issuers: Array<out Principal>?, socket: Socket?): String? =
                if (keyType == "RSA") ALIAS else null
            override fun getCertificateChain(alias: String?): Array<X509Certificate>? =
                if (alias == ALIAS) arrayOf(certificate) else null
            override fun getPrivateKey(alias: String?): PrivateKey? = if (alias == ALIAS) key else null
        }
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(arrayOf(manager), null, SecureRandom())
        return ctx
    }

    // Simplified LAN pairing deliberately does not authenticate the host.
    // An active attacker can impersonate it and intercept the PIN and screen.
    fun lanClient(): SSLContext {
        val trust = object : X509TrustManager {
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
            override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {
                throw CertificateException("Client certificates are not used")
            }
            override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
                if (chain.isNullOrEmpty()) throw CertificateException("Missing server certificate")
            }
        }
        return SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), SecureRandom()) }
    }
}
