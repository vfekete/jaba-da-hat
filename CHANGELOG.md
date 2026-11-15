# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `docs/rfc/xep/xep-0313.html` (Message Archive Management), needed for fetching server history.
- `--register` / `--unregister` command-line options (old `--install-desktop` / `--uninstall-desktop` still work).
- At startup the binary re-registers itself when an older version is registered, so the
  launcher and autostart entry always point to the newest binary; it never registers on its own.

- Own toast notification in the top-right corner (XWayland helper process), because GNOME hides
  banners under Do Not Disturb; Settings → Appearance chooses toast / GNOME / both.
- Several events at once (messages from different people, file offers, notifications) are
  combined into one toast listing them all; clicking it opens every mentioned chat at once.
- Tray icon wiggles on a new message, file or notification, and again every 15 s while it waits.

### Changed
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
