# Android App - Telegram Wallpaper Sync

Source files for the companion Android app. After your server is live on Railway, these are the only two values you need to change:

- `BOT_USERNAME` (your bot without the @)
- `BASE_URL` (your Railway https URL)

## How to use these files

1. Open **Android Studio** → New Project → **Empty Activity** (Compose).
2. Minimum SDK 26+ is fine.
3. Replace:
   - `AndroidManifest.xml`
   - `MainActivity.kt` (the full file we provide)
4. Add the dependencies shown in the snippet below.
5. Edit the two constants at the top of `MainActivity.kt` with your Railway values.
6. Build and install the APK.

## Required dependencies (app/build.gradle.kts)

Add inside `dependencies { ... }`:

```kotlin
implementation(platform("androidx.compose:compose-bom:2024.09.00"))
implementation("androidx.compose.ui:ui")
implementation("androidx.compose.material3:material3")
implementation("androidx.activity:activity-compose:1.9.2")

implementation("com.squareup.okhttp3:okhttp:4.12.0")
implementation("com.squareup.retrofit2:retrofit:2.11.0")
implementation("com.squareup.retrofit2:converter-gson:2.11.0")

implementation("io.coil-kt:coil-compose:2.7.0")

implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
```

(Compose BOM + activity-compose are usually already in the template — just add the networking + Coil ones.)

## Important files you must have

- `AndroidManifest.xml` — contains `INTERNET` + `SET_WALLPAPER` permissions.
- `MainActivity.kt` — the entire UI and logic for this MVP (one screen).

## Changing the package name

After pasting:
- Use Android Studio's **Refactor → Rename Package** (or manually replace `com.example.wallpapersync` in all files + manifest package + applicationId in build.gradle).

## The two constants to edit (after Railway deploy)

At the top of `MainActivity.kt`:

```kotlin
const val BOT_USERNAME = "mywallpapersyncbot"   // no @
const val BASE_URL = "https://your-app.up.railway.app"
```

Update these with your live Railway service, build the APK, and you're done.

## WallpaperManager notes

```kotlin
wm.setBitmap(bitmap, null, true, WallpaperManager.FLAG_SYSTEM)  // home
wm.setBitmap(bitmap, null, true, WallpaperManager.FLAG_LOCK)    // lock
```

Test on a real phone.

## Future improvements (easy to add)

- ViewModel + state management
- Downscale images before setting wallpaper
- Show the currently set system wallpaper
- Firebase Cloud Messaging (instant push instead of manual Sync)
- Per-image approval or history screen

The provided code is a complete, working end-to-end MVP.
