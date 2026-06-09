package com.example.wallpapersync

/**
 * Telegram Wallpaper Sync - Android Companion App (MVP)
 *
 * HOW TO USE:
 * 1. In Android Studio: New Project → Empty Activity (Compose)
 * 2. Replace this file (and the package name throughout)
 * 3. Add the dependencies listed in android/README_ANDROID.md
 * 4. Update BOT_USERNAME below to your actual @BotFather username (without the @)
 * 5. Update BASE_URL to your running server (ngrok https url is perfect for testing)
 *
 * This single file contains:
 * - Device ID generation & persistence (SharedPreferences for zero extra setup)
 * - UI to generate the t.me deep link
 * - "Sync" button that calls your FastAPI /pending endpoint
 * - Preview + Set as Wallpaper buttons (home / lock / both)
 * - Calls POST /apply after successfully setting so the bot sends confirmation
 * - Basic error handling + refresh
 */

import android.app.WallpaperManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.google.gson.Gson
import com.google.gson.annotations.SerializedName
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.*
import java.io.IOException
import java.util.concurrent.TimeUnit
import java.util.*

// ==================== CONFIG - CHANGE THESE ====================

/** Your bot's username from @BotFather (without the @ sign) */
const val BOT_USERNAME = "tgpaperbot"   // <--- CHANGE ME

/**
 * Base URL of your Python server.
 * - Local dev with ngrok: "https://abc123.ngrok.io"
 * - Same WiFi: "http://192.168.1.42:8000"
 * - Emulator talking to host: "http://10.0.2.2:8000"
 */
const val BASE_URL = "https://telegram-wallpaper-sync-production.up.railway.app"   // <--- CHANGE ME

// ============================================================

// Data models matching the Python server
data class PendingItem(
    val id: Int,
    @SerializedName("image_url") val imageUrl: String,
    @SerializedName("received_at") val receivedAt: String? = null
)

data class PendingResponse(
    @SerializedName("device_id") val deviceId: String,
    val pending: List<PendingItem>
)

data class ApplyRequest(
    @SerializedName("device_id") val deviceId: String,
    @SerializedName("pending_id") val pendingId: Int,
    val screen: String   // "home", "lock", "both"
)

data class ApplyResponse(
    val ok: Boolean,
    @SerializedName("already_applied") val alreadyApplied: Boolean? = null
)

const val PREFS_NAME = "wallpaper_sync"
const val PREF_DEVICE_ID = "device_id"
const val AUTO_SYNC_WORK_NAME = "auto_wallpaper_sync"
const val AUTO_SYNC_NOW_WORK_NAME = "auto_wallpaper_sync_now"
const val FOREGROUND_SYNC_CHANNEL_ID = "wallpaper_sync_foreground"
const val FOREGROUND_SYNC_NOTIFICATION_ID = 1001
const val FOREGROUND_SYNC_INTERVAL_MS = 15_000L

// Minimal Retrofit service
interface WallpaperApi {
    @GET("pending")
    suspend fun getPending(@Query("device_id") deviceId: String): PendingResponse

    @POST("apply")
    suspend fun apply(@Body request: ApplyRequest): ApplyResponse

    @GET("history")
    suspend fun getHistory(@Query("device_id") deviceId: String, @Query("limit") limit: Int = 10): Map<String, Any>
}

class AutoWallpaperWorker(
    appContext: Context,
    params: WorkerParameters
) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val prefs = applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val deviceId = prefs.getString(PREF_DEVICE_ID, null) ?: return Result.success()

        return try {
            applyPendingWallpapers(applicationContext, deviceId)
            Result.success()
        } catch (_: Exception) {
            Result.retry()
        }
    }
}

class WallpaperSyncService : Service() {
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        startForeground(FOREGROUND_SYNC_NOTIFICATION_ID, buildNotification())
        serviceScope.launch {
            while (isActive) {
                val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                val deviceId = prefs.getString(PREF_DEVICE_ID, null)
                if (!deviceId.isNullOrBlank()) {
                    try {
                        applyPendingWallpapers(applicationContext, deviceId)
                    } catch (_: Exception) {
                        // Keep the service alive; the next poll can recover from network/API errors.
                    }
                }
                delay(FOREGROUND_SYNC_INTERVAL_MS)
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        serviceScope.cancel()
        super.onDestroy()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                FOREGROUND_SYNC_CHANNEL_ID,
                "Wallpaper Sync",
                NotificationManager.IMPORTANCE_LOW
            )
            val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            manager.createNotificationChannel(channel)
        }
    }

    private fun buildNotification(): Notification {
        val intent = Intent(this, MainActivity::class.java)
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, FOREGROUND_SYNC_CHANNEL_ID)
        } else {
            Notification.Builder(this)
        }

        return builder
            .setContentTitle("Wallpaper Sync active")
            .setContentText("Watching for Telegram wallpapers")
            .setSmallIcon(android.R.drawable.ic_menu_gallery)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build()
    }
}

// Simple client
object ApiClient {
    private val okHttp = OkHttpClient.Builder()
        .connectTimeout(30, java.util.concurrent.TimeUnit.SECONDS)
        .readTimeout(60, java.util.concurrent.TimeUnit.SECONDS)
        .build()

    val api: WallpaperApi by lazy {
        Retrofit.Builder()
            .baseUrl(normalizedBaseUrl(BASE_URL))
            .client(okHttp)
            .addConverterFactory(GsonConverterFactory.create(Gson()))
            .build()
            .create(WallpaperApi::class.java)
    }

    private fun normalizedBaseUrl(rawUrl: String): String {
        val trimmed = rawUrl.trim().trimEnd('/')
        val withScheme = if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) {
            trimmed
        } else {
            "https://$trimmed"
        }
        return "$withScheme/"
    }
}

// ==================== Activity ====================

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestNotificationPermissionIfNeeded()

        setContent {
            MaterialTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    WallpaperSyncScreen()
                }
            }
        }
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 1002)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WallpaperSyncScreen() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    // Persistent device ID (generated once)
    var deviceId by remember { mutableStateOf<String?>(null) }
    var isLoading by remember { mutableStateOf(false) }
    var pendingItems by remember { mutableStateOf<List<PendingItem>>(emptyList()) }
    var lastMessage by remember { mutableStateOf<String?>(null) }
    var showHistory by remember { mutableStateOf(false) }

    // Load or create device ID on first composition
    LaunchedEffect(Unit) {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val existing = prefs.getString(PREF_DEVICE_ID, null)
        val activeDeviceId = if (existing.isNullOrBlank()) {
            val newId = UUID.randomUUID().toString()
            prefs.edit().putString(PREF_DEVICE_ID, newId).apply()
            newId
        } else {
            existing
        }
        deviceId = activeDeviceId
        scheduleAutoWallpaperSync(context)
        startWallpaperSyncService(context)
    }

    val connectLink = remember(deviceId) {
        if (deviceId != null && BOT_USERNAME != "YOUR_BOT_USERNAME_HERE") {
            "https://t.me/$BOT_USERNAME?start=$deviceId"
        } else {
            null
        }
    }

    fun showToast(msg: String) {
        Toast.makeText(context, msg, Toast.LENGTH_SHORT).show()
    }

    // Fetch pending wallpapers from backend
    fun syncNow() {
        val id = deviceId ?: return
        scope.launch {
            isLoading = true
            try {
                val appliedCount = applyPendingWallpapers(context, id)
                pendingItems = withContext(Dispatchers.IO) {
                    ApiClient.api.getPending(id).pending
                }
                lastMessage = if (appliedCount == 0) {
                    "No new wallpapers yet."
                } else {
                    "Applied $appliedCount wallpaper${if (appliedCount == 1) "" else "s"}."
                }
            } catch (e: Exception) {
                lastMessage = "Sync failed: ${e.message}"
                showToast("Could not reach server. Is it running and reachable?")
            } finally {
                isLoading = false
            }
        }
    }

    // Set the wallpaper + notify backend
    fun setWallpaper(item: PendingItem, screen: String) {
        val id = deviceId ?: return
        scope.launch {
            isLoading = true
            try {
                val bitmap = withContext(Dispatchers.IO) {
                    downloadBitmap(item.imageUrl)
                } ?: throw IOException("Failed to download image")

                applyWallpaper(context, bitmap, screen)

                // Tell the server we applied it → bot will send confirmation in Telegram
                withContext(Dispatchers.IO) {
                    ApiClient.api.apply(
                        ApplyRequest(
                            deviceId = id,
                            pendingId = item.id,
                            screen = screen
                        )
                    )
                }

                // Remove from local list immediately for nice UX
                pendingItems = pendingItems.filter { it.id != item.id }

                showToast("Wallpaper set as $screen!")
                lastMessage = "✅ Applied! Check Telegram for confirmation from the bot."

                // Optional: refresh history (not shown in detail here)
            } catch (e: Exception) {
                lastMessage = "Failed to set wallpaper: ${e.message}"
                showToast("Error setting wallpaper: ${e.localizedMessage}")
            } finally {
                isLoading = false
            }
        }
    }

    fun copyLink() {
        val link = connectLink ?: return
        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("Telegram connect link", link))
        showToast("Link copied! Send it to the bot in Telegram.")
    }

    fun openInTelegram() {
        val link = connectLink ?: return
        try {
            val intent = Intent(Intent.ACTION_VIEW, Uri.parse(link))
            context.startActivity(intent)
        } catch (e: Exception) {
            showToast("Could not open Telegram. Is it installed?")
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
    ) {
        Text(
            "Telegram Wallpaper Sync",
            style = MaterialTheme.typography.headlineMedium,
            modifier = Modifier.padding(bottom = 8.dp)
        )

        Text(
            "Share your link. People send photos to the bot. This phone applies them automatically.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )

        Spacer(Modifier.height(20.dp))

        // Connection section
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp)
        ) {
            Column(Modifier.padding(16.dp)) {
                Text("1. Share your wallpaper link", style = MaterialTheme.typography.titleMedium)

                Spacer(Modifier.height(8.dp))

                if (BOT_USERNAME == "YOUR_BOT_USERNAME_HERE" || BASE_URL.contains("YOUR-SERVER")) {
                    Text(
                        "⚠️ Edit BOT_USERNAME and BASE_URL at the top of MainActivity.kt first!",
                        color = MaterialTheme.colorScheme.error,
                        fontSize = 13.sp
                    )
                    Spacer(Modifier.height(8.dp))
                }

                if (deviceId != null) {
                    Text(
                        "Your device ID:",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Text(
                        deviceId!!,
                        fontFamily = FontFamily.Monospace,
                        fontSize = 12.sp,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                }

                Spacer(Modifier.height(12.dp))

                if (connectLink != null) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { copyLink() }) {
                            Text("Copy Share Link")
                        }
                        Button(onClick = { openInTelegram() }) {
                            Text("Open Link")
                        }
                    }
                    Spacer(Modifier.height(6.dp))
                    Text(
                        connectLink,
                        fontSize = 12.sp,
                        fontFamily = FontFamily.Monospace,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                        color = MaterialTheme.colorScheme.primary
                    )
                } else {
                    Text("Set BOT_USERNAME in code to generate your personal link.")
                }
            }
        }

        Spacer(Modifier.height(16.dp))

        // Sync section
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp)
        ) {
            Column(Modifier.padding(16.dp)) {
                Text("2. Auto sync is active", style = MaterialTheme.typography.titleMedium)

                Spacer(Modifier.height(8.dp))

                Text(
                    "This phone keeps a small notification active, checks for bot photos, and applies them to home and lock screen.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )

                Spacer(Modifier.height(8.dp))

                Button(
                    onClick = { syncNow() },
                    enabled = !isLoading && deviceId != null,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    if (isLoading) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(18.dp),
                            strokeWidth = 2.dp,
                            color = MaterialTheme.colorScheme.onPrimary
                        )
                        Spacer(Modifier.width(8.dp))
                    }
                    Text(if (isLoading) "Checking..." else "Check now")
                }

                if (pendingItems.isNotEmpty()) {
                    Spacer(Modifier.height(12.dp))
                    Text("${pendingItems.size} pending", style = MaterialTheme.typography.labelMedium)
                }
            }
        }

        Spacer(Modifier.height(16.dp))

        // Pending list
        if (pendingItems.isNotEmpty()) {
            Text("Waiting wallpapers", style = MaterialTheme.typography.titleSmall)
            Spacer(Modifier.height(8.dp))

            LazyColumn(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                items(pendingItems, key = { it.id }) { item ->
                    PendingWallpaperCard(
                        item = item,
                        onSet = { screen -> setWallpaper(item, screen) },
                        isLoading = isLoading
                    )
                }
            }
        } else {
            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth(),
                contentAlignment = Alignment.Center
            ) {
                Text(
                    lastMessage ?: "Share your link. New bot photos will be applied automatically.",
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }

        // Status / messages
        lastMessage?.let {
            Spacer(Modifier.height(8.dp))
            Text(
                it,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.secondary
            )
        }

        Spacer(Modifier.height(8.dp))

        // Tiny helper
        TextButton(onClick = { showHistory = !showHistory }) {
            Text(if (showHistory) "Hide tips" else "Show tips")
        }
        if (showHistory) {
            Text(
                "Tips:\n• Anyone with your share link can connect their Telegram chat\n• Connected people can send or forward photos to the bot\n• Keep the Wallpaper Sync notification enabled\n• Disable battery optimization if your phone pauses syncing",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}

@Composable
fun PendingWallpaperCard(
    item: PendingItem,
    onSet: (String) -> Unit,
    isLoading: Boolean
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Column(Modifier.padding(12.dp)) {
            AsyncImage(
                model = ImageRequest.Builder(LocalContext.current)
                    .data(item.imageUrl)
                    .crossfade(true)
                    .build(),
                contentDescription = "Wallpaper preview",
                modifier = Modifier
                    .fillMaxWidth()
                    .height(180.dp)
                    .clip(RoundedCornerShape(8.dp)),
                contentScale = ContentScale.Crop
            )

            Spacer(Modifier.height(10.dp))

            Text(
                "Received: ${item.receivedAt?.take(19) ?: "just now"}",
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            Spacer(Modifier.height(10.dp))

            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                OutlinedButton(
                    onClick = { onSet("home") },
                    modifier = Modifier.weight(1f),
                    enabled = !isLoading
                ) { Text("Home") }

                OutlinedButton(
                    onClick = { onSet("lock") },
                    modifier = Modifier.weight(1f),
                    enabled = !isLoading
                ) { Text("Lock") }

                Button(
                    onClick = { onSet("both") },
                    modifier = Modifier.weight(1f),
                    enabled = !isLoading
                ) { Text("Both") }
            }
        }
    }
}

// Helper: download image bytes and decode to Bitmap (runs on IO)
suspend fun downloadBitmap(url: String): Bitmap? = withContext(Dispatchers.IO) {
    try {
        val client = OkHttpClient()
        val request = okhttp3.Request.Builder().url(url).build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) return@withContext null
            val bytes = response.body?.bytes() ?: return@withContext null
            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
        }
    } catch (e: Exception) {
        e.printStackTrace()
        null
    }
}

suspend fun applyWallpaper(context: Context, bitmap: Bitmap, screen: String) = withContext(Dispatchers.IO) {
    val wm = WallpaperManager.getInstance(context)

    when (screen) {
        "home" -> wm.setBitmap(bitmap, null, true, WallpaperManager.FLAG_SYSTEM)
        "lock" -> wm.setBitmap(bitmap, null, true, WallpaperManager.FLAG_LOCK)
        else -> {
            wm.setBitmap(bitmap, null, true, WallpaperManager.FLAG_SYSTEM)
            try {
                wm.setBitmap(bitmap, null, true, WallpaperManager.FLAG_LOCK)
            } catch (_: Exception) {
                // Some devices do not allow apps to set the lock screen separately.
            }
        }
    }
}

suspend fun applyPendingWallpapers(context: Context, deviceId: String): Int = withContext(Dispatchers.IO) {
    val pending = ApiClient.api.getPending(deviceId).pending
    var appliedCount = 0

    pending.forEach { item ->
        val bitmap = downloadBitmap(item.imageUrl) ?: return@forEach
        applyWallpaper(context, bitmap, "both")
        ApiClient.api.apply(
            ApplyRequest(
                deviceId = deviceId,
                pendingId = item.id,
                screen = "both"
            )
        )
        appliedCount += 1
    }

    appliedCount
}

fun scheduleAutoWallpaperSync(context: Context) {
    val constraints = Constraints.Builder()
        .setRequiredNetworkType(NetworkType.CONNECTED)
        .build()

    val periodicWork = PeriodicWorkRequestBuilder<AutoWallpaperWorker>(15, TimeUnit.MINUTES)
        .setConstraints(constraints)
        .build()

    WorkManager.getInstance(context).enqueueUniquePeriodicWork(
        AUTO_SYNC_WORK_NAME,
        ExistingPeriodicWorkPolicy.UPDATE,
        periodicWork
    )

    val runNowWork = OneTimeWorkRequestBuilder<AutoWallpaperWorker>()
        .setConstraints(constraints)
        .build()

    WorkManager.getInstance(context).enqueueUniqueWork(
        AUTO_SYNC_NOW_WORK_NAME,
        ExistingWorkPolicy.REPLACE,
        runNowWork
    )
}

fun startWallpaperSyncService(context: Context) {
    val intent = Intent(context, WallpaperSyncService::class.java)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        context.startForegroundService(intent)
    } else {
        context.startService(intent)
    }
}
