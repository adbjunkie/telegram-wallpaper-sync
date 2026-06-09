# Telegram Wallpaper Sync - Ready-to-Open Android Project

This is a **complete Android Studio project folder**. You can open it directly.

## How to Open (Important - Follow Exactly to Avoid Crashes)

1. Open **Android Studio** (use a recent version, Hedgehog or newer recommended).
2. **File → Open**
3. Select the folder named `android-app` (the one containing this README and the `app` subfolder).
4. **Do NOT click anything yet.**
5. Wait for the bottom status bar to finish:
   - It will say things like "Gradle sync in progress...", "Downloading Gradle...", "Configuring project...".
   - This can take **2 to 5 minutes** the first time (it downloads Gradle 8.7 and dependencies).
   - Wait until you see no more progress indicators and the project is indexed.
6. Once Gradle sync completes (you may see a "Sync Now" or errors — click **Sync Now** if prompted).
7. Only then try to build (Build → Make Project or the green Run ▶ button).

**Do not click on compiler settings, error links in the Build tool window, or "Configure" buttons until the first Gradle sync has fully succeeded.** Doing so can trigger the "Cannot find configurable for specified predicate" crash you saw.

## After First Successful Open

- Edit the two constants at the top of `MainActivity.kt`:
  ```kotlin
  const val BOT_USERNAME = "yourbotname"   // from @BotFather, no @
  const val BASE_URL = "https://your-service.up.railway.app"
  ```
- Run on a device (physical device preferred for wallpaper testing).

## If You Still Get Errors or the Same Crash

1. After opening, go to **File → Invalidate Caches / Restart → Invalidate and Restart**.
2. Let it re-open and re-sync.
3. If the project doesn't recognize as Android:
   - Close the project.
   - Delete the `.idea` folder inside `android-app`.
   - Re-open the `android-app` folder.
   - Wait for full sync.

## Project Details

- Package: com.example.wallpapersync (change if you want via Refactor)
- Min SDK 26, Compile SDK 34
- Uses Jetpack Compose + Retrofit + Coil
- Ready for your Railway-deployed server

The server code is in the parent folder (`../server`).

This should now "just open and work" once the initial sync finishes. Let it cook on first launch.