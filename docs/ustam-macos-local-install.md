# Mac local source installation

This route builds Ustam on your Mac without an Apple Developer account. It does not remove quarantine, disable Gatekeeper or make a downloaded app Apple-trusted. The Script Editor opening route is still awaiting an actual user Run acceptance test.

1. Download and extract the repository's source ZIP. Keep the `scripts`, `launchers` and `ustam` folders together.
2. Open `launchers/Install Ustam.applescript` as **source** in Apple's Script Editor. Read the source, then click **Run** yourself. No Terminal command is required for this guided route.
3. Choose the extracted source folder. If Python 3.11+ is missing, choose the official Python download link, install it, then run the source again. Internet access is needed to install the pinned build tool.
4. Wait while the installer creates its isolated build environment, builds Ustam, verifies each native binary/framework and checks the extracted ZIP. The progress message changes as it works. Each build stage is limited to one hour; Stop cancels the owned build. If the helper stops unexpectedly, the next status poll reports a failure and log path and stops the identified build group. It cannot continue showing stale progress indefinitely.
5. Once validated, the installer puts **Ustam.app** in `~/Applications`. An existing identified Ustam app is backed up before replacement; an unrelated app is not overwritten. Close a running Ustam copy before updating. Saved Ustam records and provider/project configuration are preserved. No provider job is started.
6. Choose **Open Ustam**. On Mac, a short “Preparing Ustam” notice appears until the hub is ready. The browser opens automatically. If that fails, a notice shows the loopback address and an **Open browser** button. A failed startup reports an error instead of silently disappearing.

Removing the application does not remove registered projects or their configuration. A local build working on one Mac does not prove a quarantined public download will open. If macOS denies an opening and does not offer an approval route, stop and report that result; do not bypass its protections.
