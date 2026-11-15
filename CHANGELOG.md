# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
