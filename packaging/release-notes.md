## Install

**Windows 10 or 11:** download `Ensemble-@VERSION@-windows-x64-setup.exe` and run it. It installs for you only, without administrator rights.

**macOS 11 or newer (Apple silicon and Intel):** paste this in Terminal:

```sh
curl -fsSL https://github.com/fab-ioc/ensemble/releases/latest/download/install-mac.sh | sh
```

It downloads `Ensemble-@VERSION@-macos-universal.zip`, checks it against `SHA256SUMS.txt` and the app's own signature, puts Ensemble in `/Applications` (or `~/Applications`), opens it and waits until the dashboard answers, or prints why not and the end of the log. macOS does not block an app installed this way. Run it again to update in place. If an older Ensemble hub (one run from a clone) holds port 8765, the install asks before stopping it (`| ENSEMBLE_REPLACE_OLD_HUB=1 sh` stops it without asking); it never stops another program.

**The disk image** (`Ensemble-@VERSION@-macos-universal.dmg`) works too, but the app has no Apple developer signature, so macOS blocks the first open and right-click › Open is not enough on macOS 15 or later:

- **macOS 14:** open Ensemble once and close the message; then System Settings › **Privacy & Security** › **Open Anyway**, enter your password, and click **Open**.
- **macOS 15 and 26:** open Ensemble once and click **Done** on "Apple could not verify Ensemble is free of malware"; then System Settings › **Privacy & Security** › **Open Anyway** (there for about an hour after the try), enter your password, and click **Open Anyway**.
- **Any version, in Terminal:** `xattr -dr com.apple.quarantine /Applications/Ensemble.app`, then open it.

Already installed? Ensemble offers **Update now** in the dashboard.

`SHA256SUMS.txt` lists the SHA-256 of each download.
