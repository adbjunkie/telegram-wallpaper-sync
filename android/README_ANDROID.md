# Android App - Telegram Wallpaper Sync

Source files for the companion Android app. After your server is live on Railway, these are the only two values you need to change:

- `BOT_USERNAME` (your bot without the @)
- `BASE_URL` (your Railway https URL)

## How to use these files (exact steps)

1. In Android Studio: **New Project → Empty Activity** (make sure Compose is enabled / it's the Compose template). Use minimum SDK 26+.
2. After the project is created:
   - Delete the generated `MainActivity.kt`
   - Copy the `MainActivity.kt` from `android/app/src/main/java/com/example/wallpapersync/MainActivity.kt` into your project (same package or refactor later).
   - Replace your `AndroidManifest.xml` with the one from `android/app/src/main/AndroidManifest.xml`.
3. **Follow the big section below** ("Fix: Unresolved reference...") to replace your `app/build.gradle.kts` with the complete working version.
4. Edit the two constants at the very top of `MainActivity.kt` (`BOT_USERNAME` and `BASE_URL`) with your live Railway values.
5. Sync Gradle, then build and run on a device.

## Fix: "Unresolved reference 'android'", 'androidx', 'coil', 'retrofit2', etc.

**99% of the time this exact wall of errors means your `app/build.gradle.kts` is missing the dependencies.**

### Step-by-step (do exactly this)

1. In Android Studio, **open** the file:
   `app/build.gradle.kts`   ← the one inside the `app` folder

2. **Delete all the current content** of that file.

3. Copy the **entire contents** of the file we provided:
   `android/app/build.gradle.kts.example`

4. Paste it into `app/build.gradle.kts`.

5. **Important**: At the top of the pasted file, change these two lines if your project uses a different package name:

   ```kotlin
   namespace = "com.example.wallpapersync"
   applicationId = "com.example.wallpapersync"
   ```

   (Make them match the package you chose when you created the new project, and the `package` line in your `MainActivity.kt`.)

6. Click the **"Sync Now"** button (or **File → Sync Project with Gradle Files**).

7. Wait for the Gradle sync to finish completely.

8. If errors are still there after sync:
   - **Build → Clean Project**
   - **Build → Rebuild Project**
   - Sync again

This full file includes the Compose plugin, the `buildFeatures { compose = true }` block, the correct dependencies for your code (Retrofit + Gson + Coil + Coroutines + explicit kotlin-stdlib), and everything needed to make `android.*`, `androidx.*`, `coil`, `retrofit2`, `okhttp3`, `SerializedName`, `Composable`, `Modifier`, `WallpaperManager`, etc. resolve.

The previous partial "just add these lines" approach often fails if the base Compose setup from the template was incomplete or if the Kotlin plugin version was off. The `.example` file is the complete, working version.

After this, the huge list of unresolved references should disappear.

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
