package com.geoloc.wifiscanner

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.net.wifi.ScanResult
import android.net.wifi.WifiManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import org.eclipse.paho.client.mqttv3.*
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.*

class MainActivity : AppCompatActivity() {

    private lateinit var wifiManager: WifiManager
    private lateinit var mqttClient: MqttClient
    private lateinit var handler: Handler

    private lateinit var statusText: TextView
    private lateinit var networkList: TextView
    private lateinit var topicInput: EditText
    private lateinit var intervalInput: EditText
    private lateinit var startButton: Button
    private lateinit var scanButton: Button

    private var isScanning = false
    private val PERMISSION_REQUEST_CODE = 123

    private val scanRunnable = object : Runnable {
        override fun run() {
            if (isScanning) {
                scanAndPublish()
                val interval = intervalInput.text.toString().toIntOrNull() ?: 5
                handler.postDelayed(this, interval * 1000L)
            }
        }
    }

    private val wifiScanReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val success = intent.getBooleanExtra(WifiManager.EXTRA_RESULTS_UPDATED, false)
            if (success) {
                processScanResults()
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Initialize views
        statusText = findViewById(R.id.statusText)
        networkList = findViewById(R.id.networkList)
        topicInput = findViewById(R.id.topicInput)
        intervalInput = findViewById(R.id.intervalInput)
        startButton = findViewById(R.id.startButton)
        scanButton = findViewById(R.id.scanButton)

        // Initialize WiFi manager
        wifiManager = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        handler = Handler(Looper.getMainLooper())

        // Set default values
        topicInput.setText("geoloc/wifi/bssids")
        intervalInput.setText("5")

        // Button listeners
        scanButton.setOnClickListener { scanOnce() }
        startButton.setOnClickListener { toggleScanning() }

        // Check permissions
        checkPermissions()
    }

    override fun onResume() {
        super.onResume()
        registerReceiver(wifiScanReceiver, IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION))
    }

    override fun onPause() {
        super.onPause()
        unregisterReceiver(wifiScanReceiver)
    }

    private fun checkPermissions() {
        val permissions = arrayOf(
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_WIFI_STATE,
            Manifest.permission.CHANGE_WIFI_STATE
        )

        val needed = permissions.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }

        if (needed.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, needed.toTypedArray(), PERMISSION_REQUEST_CODE)
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == PERMISSION_REQUEST_CODE) {
            if (grantResults.all { it == PackageManager.PERMISSION_GRANTED }) {
                updateStatus("Permissions granted")
            } else {
                updateStatus("Location permission required for WiFi scanning")
            }
        }
    }

    private fun scanOnce() {
        updateStatus("Scanning...")
        wifiManager.startScan()
    }

    private fun toggleScanning() {
        if (isScanning) {
            stopScanning()
        } else {
            startScanning()
        }
    }

    private fun startScanning() {
        val broker = "tcp://test.mosquitto.org:1883"
        val clientId = "android_${System.currentTimeMillis()}"

        try {
            mqttClient = MqttClient(broker, clientId, null)
            val options = MqttConnectOptions()
            options.isCleanSession = true
            options.connectionTimeout = 10

            updateStatus("Connecting to MQTT...")
            mqttClient.connect(options)

            isScanning = true
            startButton.text = "Stop"
            updateStatus("Connected - Publishing...")
            handler.post(scanRunnable)

        } catch (e: Exception) {
            updateStatus("MQTT Error: ${e.message}")
        }
    }

    private fun stopScanning() {
        isScanning = false
        startButton.text = "Start Publishing"
        handler.removeCallbacks(scanRunnable)

        try {
            if (::mqttClient.isInitialized && mqttClient.isConnected) {
                mqttClient.disconnect()
            }
        } catch (e: Exception) {
            // Ignore disconnect errors
        }

        updateStatus("Stopped")
    }

    private fun scanAndPublish() {
        wifiManager.startScan()
    }

    private fun processScanResults() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
            != PackageManager.PERMISSION_GRANTED) {
            updateStatus("No location permission")
            return
        }

        val results = wifiManager.scanResults
        displayResults(results)

        if (isScanning && ::mqttClient.isInitialized && mqttClient.isConnected) {
            publishResults(results)
        }
    }

    private fun displayResults(results: List<ScanResult>) {
        val sb = StringBuilder()
        val sorted = results.sortedByDescending { it.level }

        for (result in sorted.take(10)) {
            val signal = calculateSignalPercent(result.level)
            val ssid = if (result.SSID.isNullOrEmpty()) "<Hidden>" else result.SSID
            sb.append("$ssid\n")
            sb.append("  ${result.BSSID} | ${signal}% | Ch ${getChannel(result.frequency)}\n\n")
        }

        if (results.size > 10) {
            sb.append("... and ${results.size - 10} more\n")
        }

        networkList.text = sb.toString()
        updateStatus("Found ${results.size} networks")
    }

    private fun publishResults(results: List<ScanResult>) {
        try {
            val payload = JSONObject()
            payload.put("timestamp", SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).format(Date()))
            payload.put("device", "android")
            payload.put("count", results.size)

            val networks = JSONArray()
            for (result in results) {
                val net = JSONObject()
                net.put("ssid", if (result.SSID.isNullOrEmpty()) "<Hidden>" else result.SSID)
                net.put("bssid", result.BSSID.lowercase())
                net.put("signal", calculateSignalPercent(result.level))
                net.put("channel", getChannel(result.frequency))
                net.put("rssi_dbm", result.level)
                networks.put(net)
            }
            payload.put("networks", networks)

            val topic = topicInput.text.toString()
            val message = MqttMessage(payload.toString().toByteArray())
            message.qos = 1
            mqttClient.publish(topic, message)

            updateStatus("Published ${results.size} networks")

        } catch (e: Exception) {
            updateStatus("Publish error: ${e.message}")
        }
    }

    private fun calculateSignalPercent(rssi: Int): Int {
        return when {
            rssi >= -30 -> 100
            rssi <= -90 -> 0
            else -> ((rssi + 90) * 100 / 60)
        }
    }

    private fun getChannel(frequency: Int): Int {
        return when {
            frequency in 2412..2484 -> (frequency - 2412) / 5 + 1
            frequency in 5170..5825 -> (frequency - 5170) / 5 + 34
            else -> 0
        }
    }

    private fun updateStatus(message: String) {
        runOnUiThread {
            statusText.text = message
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        stopScanning()
    }
}
