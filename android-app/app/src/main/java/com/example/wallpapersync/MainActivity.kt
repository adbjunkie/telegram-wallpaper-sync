package com.example.wallpapersync

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.app.WallpaperManager
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Photo
import androidx.compose.material.icons.outlined.Person
import androidx.compose.material.icons.outlined.Photo
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import coil.compose.AsyncImage
import coil.request.ImageRequest
import com.google.android.gms.common.ConnectionResult
import com.google.android.gms.common.GoogleApiAvailability
import com.google.android.gms.tasks.Tasks
import com.google.firebase.messaging.FirebaseMessaging
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.google.gson.Gson
import com.google.gson.annotations.SerializedName
import com.google.zxing.BarcodeFormat
import com.google.zxing.qrcode.QRCodeWriter
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.TimeoutCancellationException
import okhttp3.OkHttpClient
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Query
import java.io.IOException
import java.util.UUID
import java.util.concurrent.TimeUnit

// ==================== CONFIG ====================

const val BOT_USERNAME = "tgpaperbot"
const val BASE_URL = "https://telegram-wallpaper-sync-production.up.railway.app"

// ================================================

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
    val screen: String
)

data class ApplyResponse(
    val ok: Boolean,
    @SerializedName("already_applied") val alreadyApplied: Boolean? = null
)

data class RegisterPushRequest(
    @SerializedName("device_id") val deviceId: String,
    @SerializedName("fcm_token") val fcmToken: String
)

data class ConnectedUser(
    val chat_id: Long,
    val username: String?,
    val first_name: String?,
    val linked_at: String?,
    val avatar_url: String,
    val talk_link: String
)

data class ConnectedResponse(
    @SerializedName("device_id") val deviceId: String,
    val connected: List<ConnectedUser>,
    @SerializedName("share_link") val shareLink: String?
)

const val PREFS_NAME = "wallpaper_sync"
const val PREF_DEVICE_ID = "device_id"
const val AUTO_SYNC_WORK_NAME = "auto_wallpaper_sync"
const val AUTO_SYNC_NOW_WORK_NAME = "auto_wallpaper_sync_now"
const val FOREGROUND_SYNC_CHANNEL_ID = "wallpaper_sync_foreground"
const val FOREGROUND_SYNC_NOTIFICATION_ID = 1001
const val FOREGROUND_SYNC_INTERVAL_MS = 15_000L
const val FCM_TOKEN_TIMEOUT_MS = 60_000L
const val PUSH_REGISTER_TIMEOUT_MS = 30_000L

interface WallpaperApi {
    @GET("pending")
    suspend fun getPending(@Query("device_id") deviceId: String): PendingResponse

    @POST("apply")
    suspend fun apply(@Body request: ApplyRequest): ApplyResponse

    @POST("register_push")
    suspend fun registerPush(@Body request: RegisterPushRequest): Map<String, Any>

    @GET("connected")
    suspend fun getConnected(@Query("device_id") deviceId: String): ConnectedResponse

    @POST("unlink")
    suspend fun unlinkUser(@Query("device_id") deviceId: String,
                           @Query("chat_id") chatId: Long): Map<String, Any>
}

class WallpaperFirebaseMessagingService : FirebaseMessagingService() {
    override fun onNewToken(token: String) {
        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val deviceId = prefs.getString(PREF_DEVICE_ID, null) ?: return
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            registerPushToken(this@WallpaperFirebaseMessagingService, deviceId, token)
        }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        val prefs = getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val deviceId = message.data["device_id"] ?: prefs.getString(PREF_DEVICE_ID, null) ?: return
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            try {
                applyPendingWallpapers(applicationContext, deviceId)
            } catch (_: Exception) {
                scheduleAutoWallpaperSync(applicationContext)
                startWallpaperSyncService(applicationContext)
            }
        }
    }
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
                        registerPushToken(applicationContext, deviceId)
                    } catch (_: Exception) { }
                    try {
                        applyPendingWallpapers(applicationContext, deviceId)
                    } catch (_: Exception) { }
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
            this, 0, intent,
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

object ApiClient {
    private val okHttp = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
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
        FirebaseMessaging.getInstance().isAutoInitEnabled = true
        requestNotificationPermissionIfNeeded()
        setContent {
            MaterialTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    WallpaperSyncApp()
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
fun WallpaperSyncApp() {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var deviceId by remember { mutableStateOf<String?>(null) }
    var isLoading by remember { mutableStateOf(false) }
    var pendingItems by remember { mutableStateOf<List<PendingItem>>(emptyList()) }
    var connectedUsers by remember { mutableStateOf<List<ConnectedUser>>(emptyList()) }
    var statusMessage by remember { mutableStateOf<String?>(null) }
    var selectedUsers = remember { mutableStateListOf<Long>() }
    var selectedTabIndex by remember { mutableIntStateOf(0) }
    var manualSendImagePath by remember { mutableStateOf<String?>(null) }
    var showUserDetail by remember { mutableStateOf<ConnectedUser?>(null) }
    var showTips by remember { mutableStateOf(false) }

    val tabTitles = listOf("Share", "People", "Wallpapers")

    // Init
    LaunchedEffect(Unit) {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val existing = prefs.getString(PREF_DEVICE_ID, null)
        val activeId = if (existing.isNullOrBlank()) {
            val newId = UUID.randomUUID().toString()
            prefs.edit().putString(PREF_DEVICE_ID, newId).apply()
            newId
        } else {
            existing
        }
        deviceId = activeId
        try {
            registerPushToken(context, activeId)
        } catch (_: Exception) { }
        try {
            scheduleAutoWallpaperSync(context)
        } catch (_: Exception) { }
        try {
            startWallpaperSyncService(context)
        } catch (_: Exception) { }
        try {
            loadConnectedUsers(context, activeId) { connectedUsers = it }
        } catch (_: Exception) { }
    }

    val connectLink = remember(deviceId) {
        if (deviceId != null && BOT_USERNAME != "YOUR_BOT_USERNAME_HERE") {
            "https://t.me/$BOT_USERNAME?start=$deviceId"
        } else null
    }

    val landingUrl = remember(deviceId) {
        "https://telegram-wallpaper-sync-production.up.railway.app/landing/$deviceId"
    }

    fun showToast(msg: String) {
        Toast.makeText(context, msg, Toast.LENGTH_SHORT).show()
    }

    fun refreshPending() {
        val id = deviceId ?: return
        scope.launch {
            try {
                pendingItems = withContext(Dispatchers.IO) {
                    ApiClient.api.getPending(id).pending
                }
            } catch (e: Exception) {
                statusMessage = "Could not check wallpapers"
            }
        }
    }

    fun syncNow() {
        val id = deviceId ?: return
        scope.launch {
            isLoading = true
            try {
                val count = applyPendingWallpapers(context, id)
                pendingItems = withContext(Dispatchers.IO) {
                    ApiClient.api.getPending(id).pending
                }
                statusMessage = if (count == 0) "No new wallpapers" else "Applied $count wallpaper(s)"
            } catch (e: Exception) {
                statusMessage = "Sync failed"
            } finally {
                isLoading = false
            }
        }
    }

    fun setWallpaperAndNotify(item: PendingItem, screen: String) {
        val id = deviceId ?: return
        scope.launch {
            isLoading = true
            try {
                val bitmap = downloadBitmap(item.imageUrl)
                    ?: throw IOException("Failed to download")
                applyWallpaper(context, bitmap, screen)
                withContext(Dispatchers.IO) {
                    ApiClient.api.apply(
                        ApplyRequest(deviceId = id, pendingId = item.id, screen = screen)
                    )
                }
                pendingItems = pendingItems.filter { it.id != item.id }
                showToast("Wallpaper set as $screen!")
                statusMessage = "Applied to $screen"
            } catch (e: Exception) {
                statusMessage = "Error: ${e.message}"
                showToast("Error setting wallpaper")
            } finally {
                isLoading = false
            }
        }
    }

    fun removeUser(user: ConnectedUser) {
        val id = deviceId ?: return
        scope.launch {
            try {
                withContext(Dispatchers.IO) {
                    ApiClient.api.unlinkUser(id, user.chat_id)
                }
                connectedUsers = connectedUsers.filter { it.chat_id != user.chat_id }
                selectedUsers.remove(user.chat_id)
                showToast("Removed ${user.first_name ?: user.username ?: "user"}")
            } catch (e: Exception) {
                showToast("Failed to remove user")
            }
        }
    }

    Column(modifier = Modifier.fillMaxSize()) {
        // Header
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .background(MaterialTheme.colorScheme.surface)
                .padding(top = 12.dp, start = 16.dp, end = 16.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    "Wallpaper Sync",
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.Bold
                )
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    FilledTonalButton(onClick = { syncNow() }, enabled = !isLoading) {
                        if (isLoading) {
                            CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp)
                            Spacer(Modifier.width(6.dp))
                        }
                        Text("Sync")
                    }
                }
            }
            Spacer(Modifier.height(4.dp))

            // Connected badge
            if (connectedUsers.isNotEmpty()) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.padding(bottom = 4.dp)
                ) {
                    Text(
                        "${connectedUsers.size} connected",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.primary
                    )
                    if (selectedUsers.isNotEmpty()) {
                        Spacer(Modifier.width(8.dp))
                        Text(
                            "${selectedUsers.size} selected",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.secondary
                        )
                    }
                }
            }
        }

        // Tab row
        TabRow(
            selectedTabIndex = selectedTabIndex,
            modifier = Modifier.fillMaxWidth()
        ) {
            tabTitles.forEachIndexed { index, title ->
                Tab(
                    selected = selectedTabIndex == index,
                    onClick = { selectedTabIndex = index },
                    text = { Text(title) },
                    icon = {
                        when (index) {
                            0 -> Icon(Icons.Outlined.Share, contentDescription = null)
                            1 -> Icon(Icons.Outlined.Person, contentDescription = null)
                            2 -> Icon(Icons.Outlined.Photo, contentDescription = null)
                        }
                    }
                )
            }
        }

        // Content area
        when (selectedTabIndex) {
            0 -> ShareTab(deviceId, connectLink, landingUrl, context)
            1 -> PeopleTab(
                connectedUsers, selectedUsers, isLoading,
                onToggleSelect = { user ->
                    if (selectedUsers.contains(user.chat_id)) {
                        selectedUsers.remove(user.chat_id)
                    } else {
                        selectedUsers.add(user.chat_id)
                    }
                },
                onRemove = { removeUser(it) },
                onRefresh = {
                    val id = deviceId!!
                    scope.launch { loadConnectedUsers(context, id) { connectedUsers = it } }
                },
                statusMessage = statusMessage
            )
            2 -> WallpapersTab(
                pendingItems, isLoading,
                onApply = { item, screen -> setWallpaperAndNotify(item, screen) },
                onRefresh = { refreshPending() },
                statusMessage = statusMessage
            )
        }

        // Bottom status
        statusMessage?.let {
            Text(
                it,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}

@Composable
fun ShareTab(deviceId: String?, connectLink: String?, landingUrl: String, context: Context) {
    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        // QR Code card
        item {
            Card(shape = RoundedCornerShape(16.dp)) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(24.dp),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    Text(
                        "Share your wallpaper link",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "Anyone with this link can send photos to your phone's wallpaper.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )

                    Spacer(Modifier.height(20.dp))

                    // QR code
                    if (connectLink != null) {
                        val qrBitmap = remember(connectLink) { generateQrCode(connectLink, 512) }
                        qrBitmap?.let {
                            Image(
                                bitmap = it.asImageBitmap(),
                                contentDescription = "QR code",
                                modifier = Modifier
                                    .size(220.dp)
                                    .clip(RoundedCornerShape(12.dp))
                                    .border(1.dp, MaterialTheme.colorScheme.outlineVariant, RoundedCornerShape(12.dp))
                            )
                        }
                    }

                    Spacer(Modifier.height(16.dp))

                    Text(
                        "Scan with any camera or Telegram app",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        fontSize = 12.sp
                    )

                    Spacer(Modifier.height(20.dp))

                    // Deep link displayed
                    if (connectLink != null) {
                        Text(
                            connectLink,
                            style = MaterialTheme.typography.bodySmall,
                            fontFamily = FontFamily.Monospace,
                            color = MaterialTheme.colorScheme.primary,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis
                        )
                        Spacer(Modifier.height(12.dp))
                    }

                    Row(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Button(
                            onClick = {
                                val link = connectLink ?: return@Button
                                val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                                clipboard.setPrimaryClip(ClipData.newPlainText("Wallpaper share link", link))
                                Toast.makeText(context, "Link copied!", Toast.LENGTH_SHORT).show()
                            },
                            modifier = Modifier.weight(1f)
                        ) {
                            Text("Copy Link")
                        }

                        OutlinedButton(
                            onClick = {
                                val link = connectLink ?: return@OutlinedButton
                                val sendIntent = Intent(Intent.ACTION_SEND).apply {
                                    type = "text/plain"
                                    putExtra(Intent.EXTRA_TEXT, link)
                                    putExtra(Intent.EXTRA_TITLE, "Set my wallpaper")
                                }
                                context.startActivity(Intent.createChooser(sendIntent, "Share link"))
                            },
                            modifier = Modifier.weight(1f)
                        ) {
                            Text("Share")
                        }
                    }

                    Spacer(Modifier.height(8.dp))

                    OutlinedButton(
                        onClick = {
                            val link = connectLink ?: return@OutlinedButton
                            try {
                                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(link)))
                            } catch (e: Exception) {
                                Toast.makeText(context, "Telegram not installed?", Toast.LENGTH_SHORT).show()
                            }
                        },
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Text("Open in Telegram")
                    }
                }
            }
        }

        // Web landing page card
        item {
            Card(
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.secondaryContainer
                )
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(20.dp)
                ) {
                    Text(
                        "No Telegram? No problem.",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.SemiBold
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "Share the web link below. They can view instructions in a browser and connect to Telegram from there.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSecondaryContainer
                    )
                    Spacer(Modifier.height(12.dp))
                    Text(
                        landingUrl,
                        style = MaterialTheme.typography.bodySmall,
                        fontFamily = FontFamily.Monospace,
                        color = MaterialTheme.colorScheme.primary,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis
                    )
                    Spacer(Modifier.height(8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(onClick = {
                            val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                            clipboard.setPrimaryClip(ClipData.newPlainText("Web link", landingUrl))
                            Toast.makeText(context, "Web link copied!", Toast.LENGTH_SHORT).show()
                        }) {
                            Text("Copy", fontSize = 13.sp)
                        }
                        TextButton(onClick = {
                            try {
                                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(landingUrl)))
                            } catch (_: Exception) { }
                        }) {
                            Text("Open", fontSize = 13.sp)
                        }
                    }
                }
            }
        }

        // Tips card
        item {
            Card(
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.surfaceVariant
                )
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Text("How it works", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(8.dp))
                    TipRow("1", "Share your QR code or link with anyone")
                    TipRow("2", "They open it in Telegram and send a photo to the bot")
                    TipRow("3", "Your phone auto-applies it as wallpaper")
                    TipRow("4", "You can manage who has access in the People tab")
                }
            }
        }
    }
}

@Composable
fun TipRow(number: String, text: String) {
    Row(
        modifier = Modifier.padding(vertical = 3.dp),
        verticalAlignment = Alignment.Top
    ) {
        Text(
            number,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.width(16.dp)
        )
        Text(
            text,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun PeopleTab(
    users: List<ConnectedUser>,
    selectedUsers: List<Long>,
    isLoading: Boolean,
    onToggleSelect: (ConnectedUser) -> Unit,
    onRemove: (ConnectedUser) -> Unit,
    onRefresh: () -> Unit,
    statusMessage: String?
) {
    if (users.isEmpty()) {
        Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Icon(
                    Icons.Outlined.Person,
                    contentDescription = null,
                    modifier = Modifier.size(64.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.4f)
                )
                Spacer(Modifier.height(12.dp))
                Text(
                    "No one connected yet",
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    "Share your link from the 'Share' tab",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f)
                )
                Spacer(Modifier.height(16.dp))
                OutlinedButton(onClick = onRefresh) { Text("Refresh") }
            }
        }
        return
    }

    Column(modifier = Modifier.fillMaxSize()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                "${users.size} connected",
                style = MaterialTheme.typography.titleSmall
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (selectedUsers.isNotEmpty()) {
                    TextButton(onClick = { /* TODO: batch upload wallpaper */ }) {
                        Text("Send wallpaper")
                    }
                }
                OutlinedButton(onClick = onRefresh) { Text("Refresh") }
            }
        }

        if (selectedUsers.isNotEmpty()) {
            FlowRow(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp)
                    .padding(bottom = 8.dp)
            ) {
                Text(
                    "${selectedUsers.size} selected — tap a wallpaper in the Wallpapers tab to send to them",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.secondary
                )
            }
        }

        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.spacedBy(8.dp),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp, vertical = 4.dp)
        ) {
            items(users, key = { it.chat_id }) { user ->
                val isSelected = selectedUsers.contains(user.chat_id)
                val displayName = user.first_name ?: user.username ?: "Unknown"

                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { onToggleSelect(user) },
                    shape = RoundedCornerShape(12.dp),
                    colors = CardDefaults.cardColors(
                        containerColor = if (isSelected)
                            MaterialTheme.colorScheme.primaryContainer
                        else
                            MaterialTheme.colorScheme.surface
                    ),
                    border = if (isSelected)
                        androidx.compose.foundation.BorderStroke(2.dp, MaterialTheme.colorScheme.primary)
                    else null
                ) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(12.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        // Avatar
                        Box(
                            modifier = Modifier
                                .size(44.dp)
                                .clip(CircleShape)
                                .background(MaterialTheme.colorScheme.primaryContainer),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                (displayName.firstOrNull()?.toString() ?: "?").uppercase(),
                                style = MaterialTheme.typography.titleMedium,
                                color = MaterialTheme.colorScheme.onPrimaryContainer,
                                fontWeight = FontWeight.Bold
                            )
                        }

                        Spacer(Modifier.width(12.dp))

                        Column(modifier = Modifier.weight(1f)) {
                            Text(
                                displayName,
                                style = MaterialTheme.typography.bodyLarge,
                                fontWeight = FontWeight.Medium
                            )
                            if (user.username != null) {
                                Text(
                                    "@${user.username}",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.primary
                                )
                            }
                            user.linked_at?.let {
                                Text(
                                    "Connected ${it.take(10)}",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant
                                )
                            }
                        }

                        // Selection indicator
                        if (isSelected) {
                            Box(
                                modifier = Modifier
                                    .size(28.dp)
                                    .clip(CircleShape)
                                    .background(MaterialTheme.colorScheme.primary),
                                contentAlignment = Alignment.Center
                            ) {
                                Text("✓", color = MaterialTheme.colorScheme.onPrimary, fontWeight = FontWeight.Bold)
                            }
                        }

                        Spacer(Modifier.width(4.dp))

                        // Remove button
                        IconButton(
                            onClick = { onRemove(user) },
                            modifier = Modifier.size(36.dp)
                        ) {
                            Icon(
                                Icons.Filled.Close,
                                contentDescription = "Remove",
                                modifier = Modifier.size(18.dp),
                                tint = MaterialTheme.colorScheme.error
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun WallpapersTab(
    items: List<PendingItem>,
    isLoading: Boolean,
    onApply: (PendingItem, String) -> Unit,
    onRefresh: () -> Unit,
    statusMessage: String?
) {
    Column(modifier = Modifier.fillMaxSize()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                "${items.size} pending",
                style = MaterialTheme.typography.titleSmall
            )
            OutlinedButton(onClick = onRefresh) { Text("Refresh") }
        }

        if (items.isEmpty()) {
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Icon(
                        Icons.Outlined.Photo,
                        contentDescription = null,
                        modifier = Modifier.size(64.dp),
                        tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.4f)
                    )
                    Spacer(Modifier.height(12.dp))
                    Text(
                        statusMessage ?: "No pending wallpapers",
                        style = MaterialTheme.typography.bodyLarge,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "Connected users can send photos to the bot",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f)
                    )
                }
            }
            return
        }

        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp, vertical = 4.dp)
        ) {
            items(items, key = { it.id }) { item ->
                Card(
                    shape = RoundedCornerShape(12.dp),
                    elevation = CardDefaults.cardElevation(defaultElevation = 1.dp)
                ) {
                    Column(modifier = Modifier.padding(10.dp)) {
                        AsyncImage(
                            model = ImageRequest.Builder(LocalContext.current)
                                .data(item.imageUrl)
                                .crossfade(true)
                                .build(),
                            contentDescription = "Wallpaper",
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(180.dp)
                                .clip(RoundedCornerShape(8.dp)),
                            contentScale = ContentScale.Crop
                        )

                        Spacer(Modifier.height(8.dp))

                        Text(
                            "Received: ${item.receivedAt?.take(19) ?: "just now"}",
                            fontSize = 11.sp,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )

                        Spacer(Modifier.height(8.dp))

                        Row(
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            OutlinedButton(
                                onClick = { onApply(item, "home") },
                                modifier = Modifier.weight(1f),
                                enabled = !isLoading
                            ) { Text("Home", fontSize = 13.sp) }

                            OutlinedButton(
                                onClick = { onApply(item, "lock") },
                                modifier = Modifier.weight(1f),
                                enabled = !isLoading
                            ) { Text("Lock", fontSize = 13.sp) }

                            Button(
                                onClick = { onApply(item, "both") },
                                modifier = Modifier.weight(1f),
                                enabled = !isLoading
                            ) { Text("Both", fontSize = 13.sp) }
                        }
                    }
                }
            }
        }
    }
}

// ==================== Utility Functions ====================

fun generateQrCode(text: String, size: Int): Bitmap? {
    return try {
        val writer = QRCodeWriter()
        val bitMatrix = writer.encode(text, BarcodeFormat.QR_CODE, size, size)
        val bitmap = Bitmap.createBitmap(size, size, Bitmap.Config.RGB_565)
        val canvas = Canvas(bitmap)
        val paint = Paint().apply { color = Color.WHITE }
        canvas.drawRect(0f, 0f, size.toFloat(), size.toFloat(), paint)
        paint.color = Color.BLACK
        for (x in 0 until size) {
            for (y in 0 until size) {
                if (bitMatrix[x, y]) {
                    canvas.drawRect(x.toFloat(), y.toFloat(), x.toFloat() + 1f, y.toFloat() + 1f, paint)
                }
            }
        }
        bitmap
    } catch (e: Exception) {
        null
    }
}

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
            } catch (_: Exception) { }
        }
    }
}

suspend fun applyPendingWallpapers(context: Context, deviceId: String): Int = withContext(Dispatchers.IO) {
    val pending = ApiClient.api.getPending(deviceId).pending
    var count = 0
    pending.forEach { item ->
        val bitmap = downloadBitmap(item.imageUrl) ?: return@forEach
        applyWallpaper(context, bitmap, "both")
        ApiClient.api.apply(
            ApplyRequest(deviceId = deviceId, pendingId = item.id, screen = "both")
        )
        count++
    }
    count
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

suspend fun registerPushToken(context: Context, deviceId: String, knownToken: String? = null): String =
    withContext(Dispatchers.IO) {
        val playServicesStatus = GoogleApiAvailability.getInstance().isGooglePlayServicesAvailable(context)
        if (playServicesStatus != ConnectionResult.SUCCESS) {
            val statusText = GoogleApiAvailability.getInstance().getErrorString(playServicesStatus)
            throw IOException("Google Play Services unavailable: $statusText")
        }
        FirebaseMessaging.getInstance().isAutoInitEnabled = true
        val token = knownToken ?: try {
            withTimeout(FCM_TOKEN_TIMEOUT_MS) {
                Tasks.await(FirebaseMessaging.getInstance().token)
            }
        } catch (_: TimeoutCancellationException) {
            throw IOException("Timed out fetching Firebase token")
        }
        if (token.isNullOrBlank()) {
            throw IOException("Firebase returned an empty token")
        }
        try {
            withTimeout(PUSH_REGISTER_TIMEOUT_MS) {
                ApiClient.api.registerPush(
                    RegisterPushRequest(deviceId = deviceId, fcmToken = token)
                )
            }
        } catch (_: TimeoutCancellationException) {
            throw IOException("Timed out registering token")
        }
        "Push registered"
    }

suspend fun loadConnectedUsers(
    context: Context,
    deviceId: String,
    onResult: (List<ConnectedUser>) -> Unit
) {
    withContext(Dispatchers.IO) {
        try {
            val response = ApiClient.api.getConnected(deviceId)
            withContext(Dispatchers.Main) {
                onResult(response.connected)
            }
        } catch (_: Exception) { }
    }
}
