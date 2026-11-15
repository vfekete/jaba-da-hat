# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- File transfer over HTTP: sending uploads the file to the server's HTTP Upload service
  (XEP-0363) and sends the link; a received file link (XEP-0066, body = link) shows as a file
  card. Nothing is fetched before **Download** is pressed, so the size shows as unknown
  until then.
- Downloads can be paused, resumed (HTTP `Range`; also works when the server ignores `Range`)
  and cancelled; uploads can be cancelled (HTTP Upload has no pause, so the button is hidden).
- Clear errors when the server has no upload service or the file exceeds its size limit.
- Test server: HTTP Upload component on `https://localhost:15281`.

## [0.2.0] — 2026-11-15

First version that talks to real XMPP servers. Tested against Prosody and by hand.

### Added
- **Real XMPP backend** (`backend/xmpp.py`, slixmpp), now the default; `--mock` starts the old
  mock backend. Covered: TLS login with DNS SRV or a manual server, roster, presence (best
  resource wins), subscriptions (accept / decline / decline & block), messages, chat states,
  carbons (XEP-0280), server history (MAM, XEP-0313), stream management (XEP-0198), ping
  keepalive (XEP-0199), blocking (XEP-0191), XEP-0066 links shown as clickable text.
- Server history newer than the last time we were connected is fetched at login and shown as
  incoming messages; only that timestamp (`history_since`) is stored, never message content.
- Messages I write on another device appear in an open chat on my side (carbons).
- `--ca-file` to trust an extra CA (used for the test server).
- `tools/test-server.sh`: throw-away Prosody in a container with test accounts, and
  `tests/test_xmpp_integration.py` against it (`JDH_XMPP_TEST=1`).
- `docs/rfc/xep/xep-0313.html` (Message Archive Management), needed for fetching server history.
- `--register` / `--unregister` command-line options (old `--install-desktop` / `--uninstall-desktop` still work).
- At startup the binary re-registers itself when an older version is registered, so the
  launcher and autostart entry always point to the newest binary; it never registers on its own.

- Own toast notification in the top-right corner (XWayland helper process), because GNOME hides
  banners under Do Not Disturb; Settings → Appearance chooses toast / GNOME / both.
- Several events at once (messages from different people, file offers, notifications) are
  combined into one toast listing them all; clicking it opens every mentioned chat at once.

- `jeepney` dependency (pure-Python D-Bus) for the own tray implementation.

### Fixed
- The server field accepts `host:port` (e.g. `127.0.0.1:15222`); before, the whole text was
  looked up as a host name and failed with a misleading "no DNS record for <JID domain>".
- Connection errors name the address actually tried and say "host name not found" /
  "connection refused" instead of raw Python errors.
- Repeated tray wiggle was invisible after the first round: frames changed faster than the panel
  samples the icon; frames are now held 150 ms and every one is tilted.
- Tray menu kept showing stale entries (e.g. "Status: Offline / Connecting…" after a successful
  login) because GNOME's AppIndicator extension caches labels per menu-item id; ids are no
  longer reused between menu rebuilds.
- Busy (wait) cursor after double click or menu actions that open no window: the tray is now
  our own StatusNotifierItem/dbusmenu implementation that does not accept activation tokens.
- `Gdk-CRITICAL … GDK_IS_WAYLAND_WINDOW` when opening file dialogs: dialogs now go through the
  desktop portal (`QT_QPA_PLATFORMTHEME=xdgdesktopportal`).
- Qt Wayland `textinput` warnings flooding the terminal are filtered out.
- Misleading "re-registered … from 0.1.0 to 0.1.0" log line.

### Changed
- A dropped connection is retried after 3, 10, 30, 60, 120 s, then every 5 min, with one
  "reconnecting" notice per outage instead of one per attempt.
- Permanent login failures (wrong password, untrusted certificate, same resource logged in
  elsewhere) stop automatic retries through a dedicated `loginRejected` signal instead of
  matching the error text.
- With encryption switched on but unavailable (OMEMO arrives in 0.4.0) a message is refused,
  never sent as plain text.
- slixmpp is never logged at DEBUG, because that log contains the SASL exchange.
- `build.sh` bundles the slixmpp plugins, which are imported by name at runtime.
- Tray icon wiggles 1 s on / 1 s off for as long as something is waiting.
- GNOME notifications are sent directly over D-Bus; clicking one opens the chats it was about.
- Tray icon no longer uses the app logo: coloured status circles, or an envelope (unread message),
  file (incoming file) or note (pending notification), drawn edge to edge to be as big as possible.
- The binary also registers itself when it is not registered at all (before: only re-registered
  older versions).
- The .desktop files use an absolute icon path, so GNOME shows the icon without a shell restart;
  a registration of the same version whose entry is out of date (old icon reference, moved
  binary) is rewritten too.
- Double click on the tray icon opens a window for every contact with unread messages or a
  waiting file, all at once (single left click always opens the menu —
  that is the AppIndicator extension's behaviour). Menu's last item is now **Close** (was "Quit").
- Plan updated with the answered questions: OMEMO devices are never trusted automatically
  (approval lasts until the chat closes), server history (MAM) is fetched and shown as
  incoming messages, Prosody is the test server.

## [0.1.0] — 2026-11-15

First UI skeleton, running entirely on a **mock backend** (no real XMPP traffic yet), so the
look and workflow can be tested by hand before the protocol work starts.

### Added
- `docs/PLAN.md`: requirement-by-requirement feasibility check against the XMPP RFCs/XEPs,
  phase plan and open questions — so we know up front what XMPP can and cannot do.
- `docs/rfc/xep/`: the XEPs the features rely on (disco, chat states, blocking, stream management,
  ping, Jingle + Jingle FT + transports, HTTP Upload, carbons, receipts, OOB, OMEMO).
- Tray icon with presence dot and unread badge; right-click menu with status
  (available / away / do not disturb / offline), log on / log off, contacts submenu with
  "only online / all" filter (single click opens the chat), notifications submenu with
  accept / decline / decline & block for authorization requests, settings, quit.
- Chat window: remote messages left, own messages right; clickable links; nothing is
  auto-loaded (text is escaped and only links are generated); typing indicator; OMEMO
  toggle (UI only); file send via button or drag & drop; transfer cards with
  download / decline / pause / resume / cancel / progress. Conversation lives in memory
  only and is forgotten when the window closes.
- Contacts manager: add contact, remove / remove & block (multi-selection), add groups,
  remove groups (multi-selection, members are kept), rename, drag & drop between rows
  (insertion line shows the target group), onto a contact (creates a new group with both)
  or onto a group header.
- Desktop notifications for new messages, file offers, authorization requests and errors.
- Settings: account (JID, password, server, port, resource, TLS, OMEMO default, auto-login,
  auto-reconnect), theme (follow system / light / dark), typing notifications (send / show
  separately), start at login, download folder (default `~/Downloads`).
- Settings stored in `~/.config/jaba-da-hat/config.json` with mode 0600 (it contains the password).
- XDG autostart entry (toggle in settings); clean shutdown on SIGTERM at session logout.
- Single-instance guard: starting it again shows the running instance.
- `run.sh` (run from source in the venv) and `build.sh` (Nuitka single binary
  `build/jdh-<version>`, icon and version embedded, `--install-desktop` installs launcher + icon).
- Test suite (pytest, offscreen Qt) and `tools/screenshots.py` for README screenshots.
