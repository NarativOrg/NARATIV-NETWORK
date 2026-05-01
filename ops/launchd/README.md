# macOS launchd setup for the Mac mini

There are five long-running services. Each has its own launchd plist so
they can be restarted independently and logged separately.

| Service   | Plist                                | Job              |
| --------- | ------------------------------------ | ---------------- |
| Admin UI  | `org.narativ.nn.admin.plist`         | FastAPI server   |
| Ingest    | `org.narativ.nn.ingest.plist`        | source poller    |
| Schedule  | `org.narativ.nn.schedule.plist`      | playlist regen   |
| Playout   | `org.narativ.nn.playout.plist`       | ffmpeg → RTMP    |
| Watchdog  | `org.narativ.nn.watchdog.plist`      | health checks    |

These run as a **logged-in user** (LaunchAgents in `~/Library/LaunchAgents/`),
which is what we want — the Mac mini stays auto-logged-in and the user's
ffmpeg has full GPU/codec access.

## Install

```sh
cd narativ-network
./ops/scripts/install_launchd.sh
```

The script edits each plist to point at your `narativ-network` checkout
and your venv's Python, then symlinks them into `~/Library/LaunchAgents/`
and `launchctl load`s them.

## Manage

```sh
launchctl list | grep narativ                 # see job state
launchctl kickstart -k gui/$(id -u)/org.narativ.nn.playout
launchctl print gui/$(id -u)/org.narativ.nn.playout
tail -f ~/Library/Logs/narativ-network/*.log
```

## Uninstall

```sh
./ops/scripts/uninstall_launchd.sh
```
