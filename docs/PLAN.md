# jaba-da-hat — plan and XMPP feasibility

Status: **0.1.0 — UI skeleton on mock data.** No real XMPP traffic yet.

Decisions made so far (2026-11-15):

| Topic | Decision |
| --- | --- |
| End-to-end encryption | **OMEMO** (XEP-0384) instead of OTR |
| File transfer | **Jingle FT** (XEP-0234) preferred, **HTTP Upload** (XEP-0363) as fallback |
| Password storage | `~/.config/jaba-da-hat/config.json`, mode `0600` |
| XMPP library (phase 2) | `slixmpp` (asyncio) + `qasync` to share the Qt event loop |
| UI | PySide6 + QtQuick/QML (Material style), tray menu with Qt Widgets `QMenu` |
| Packaging | Nuitka `--mode=onefile` → `build/jdh-<SemVer>` |

## 1. Feasibility: requirement → protocol

Legend: ✅ fully covered by a standard · 🟡 possible with caveats · 🖥 client-side only (no protocol needed)

| Requirement | Basis | Verdict | Notes |
| --- | --- | --- | --- |
| Status available / away / do-not-disturb / offline | RFC 6121 §4.7.2.1 `<show/>` (`away`, `chat`, `dnd`, `xa`); offline = `unavailable` presence | ✅ | |
| Log on / log off, automatic re-login | RFC 6120 (stream), XEP-0198 (Stream Management, resume), XEP-0199 (ping, detects dead links) | ✅ | Re-login is client logic; SM lets the server keep the session for a short outage. |
| Auto-login from profile (JID, server, password …) | RFC 6120 §6 SASL, §5 STARTTLS, RFC 7590 (TLS), RFC 7622 (JID format) | ✅ | Server override optional; default is DNS SRV for the JID domain. |
| Contacts submenu, filter online / all | RFC 6121 §2 (roster), §4 (presence) | ✅ | |
| Single click opens chat | — | 🖥 | |
| File transfer with cancel / progress / pause / continue | XEP-0234 Jingle FT (+ XEP-0166, XEP-0260 SOCKS5, XEP-0261 IBB); XEP-0363 HTTP Upload | 🟡 | XEP-0234 is **Deferred** (0.19.1) but implemented by Gajim, Dino, Psi, Conversations. It has *cancel* (session-terminate) and *resume* (`<range offset=…/>`), but **no pause**: pause = terminate + later re-request with a range offset, which only works if the peer still offers the file. HTTP Upload has **cancel only**; downloads of HTTP-uploaded files can resume if the web server honours `Range`. |
| Encrypted communication | XEP-0384 OMEMO (Experimental 0.9.1) | 🟡 | OTR is not an XMPP standard (decided: OMEMO). Many clients still speak the legacy OMEMO 0.3 namespace (`eu.siacs.conversations.axolotl`), so we need both namespaces (`python-omemo` supports both). Needs a trust policy — see open question Q2. Contacts with no OMEMO devices cannot be encrypted to; the UI already shows this. |
| Chat kept only in memory while window open; remote left, me right | — | 🖥 | We will **not** request server archives (XEP-0313 MAM). The server may still store messages that arrive while we are offline (RFC 6121 §8.5.3) and deliver them at login — they appear as unread. |
| Links clickable, nothing auto-loaded | — | 🖥 | Message text is HTML-escaped and only `<a>` tags are generated (`textutil.linkify`). Images and files (incl. XEP-0066 OOB URLs) are shown as a card with a **Download** button. |
| Add / remove contact, multi-select remove | RFC 6121 §2.4–2.5 (roster set / remove), §3 (subscribe) | ✅ | Roster remove makes the server cancel subscriptions both ways. |
| Add / remove user-defined groups (multi-select) | RFC 6121 §2.1.2.2 `<group/>` | 🟡 | XMPP groups exist only as labels on contacts, so an **empty group cannot live on the server**. Empty groups are kept locally in the config (could later be synced via XEP-0049 private storage). Removing a group keeps its contacts. |
| Remove & block | XEP-0191 Blocking Command | ✅ | Needs server support (Prosody, ejabberd, Openfire have it). |
| Drag & drop: drop on contact → new group with both; insertion line | RFC 6121 groups | 🟡 | Group membership is stored; **position inside a group is not** (the protocol has no ordering). The line shows which group the contact lands in; groups are sorted alphabetically — see Q1. |
| Notifications for XMPP events (subscription requests, errors) | RFC 6121 §3.1.3–3.1.4 | ✅ | |
| Declining authorization must stop re-flooding | RFC 6121 §3.1.4, §3.2 — reply with `presence type="unsubscribed"` | ✅ | The server then deletes the pending request and stops redelivering it at each login. A peer can still send a *new* request later, so we also offer **Decline & block** (XEP-0191). |
| New message → GNOME notification + tray icon change | freedesktop Notifications (D-Bus), StatusNotifierItem | ✅ | **Vanilla GNOME has no tray**; Ubuntu ships the AppIndicator extension (present on this machine). Without it the app opens the Contacts window instead. |
| Presence change changes the app (icon) | — | 🖥 | Tray icon shows a presence dot and greys out when offline. |
| Typing notifications (send / show) | XEP-0085 Chat States | ✅ | Two separate settings. |
| Settings: theme, typing, account, download folder | — | 🖥 | |
| Start at login, quit at logout | XDG Autostart spec; SIGTERM at session end | ✅ | On logout we send `unavailable` presence before exiting. |
| Single binary with version and icon (.desktop) | — | 🖥 | `jdh-<ver> --install-desktop` installs the launcher and icon for the user. |

## 2. Phases

| Version | Content | Manual test by you |
| --- | --- | --- |
| **0.1.0** (this) | Project scaffold, tray + menu, contacts manager (multi-select, groups, drag & drop), chat window, file-transfer UI, notifications, settings, theme, autostart, `run.sh`, `build.sh`, tests — **all on a mock backend** | ✅ now |
| 0.2.0 | Real XMPP via slixmpp: TLS login, SRV, roster, presence, subscriptions (accept / decline / block), messages, chat states, carbons (XEP-0280), stream management, ping, auto-reconnect | needs a test server (see Q5) |
| 0.3.0 | File transfer: HTTP Upload send + OOB receive, Jingle FT send/receive (IBB + SOCKS5), ranged resume, "pause" emulation | |
| 0.4.0 | OMEMO (both namespaces), device list, fingerprint view, trust policy | |
| 1.0.0 | Polish, packaging check on a clean user account | |

Every phase is developed on the `dev` branch; merging to `main` only after tests pass and your manual test.

## 3. Open questions

- **Q1 — order inside groups.** The protocol cannot store a contact's position inside a group. Is it fine that the insertion line only decides the target group (contacts stay sorted by name), or should the app remember a manual order locally?
- **Q2 — OMEMO trust.** Blind trust before verification (Conversations/Dino default: new devices are trusted until you verify one, then only verified ones) or manual approval of every new device?
- **Q3 — offline messages / archives.** OK to ignore server archives (MAM) completely, as the "memory only" rule implies?
- **Q4 — chat closed when a message arrives.** Currently the message is kept in memory (unread badge + notification) until you open the chat; closing the chat forgets everything. OK?
- **Q5 — test server for phase 2.** Run a throw-away Prosody in a container (podman/docker, nothing installed system-wide), or do you have test accounts on an existing server?
- **Q6 — tray left-click.** Currently: opens the oldest unread chat, otherwise the Contacts window. OK?

## 4. Architecture (0.1.0)

```
main.py          argument parsing, QApplication, single instance (QLocalServer), SIGTERM → clean quit
core.py          AppController: settings, backend wiring, models, window management (exposed to QML as `app`)
backend/base.py  Backend interface (Qt signals + methods) — the only thing the UI talks to
backend/mock.py  MockBackend with fake example.org contacts; phase 2 adds backend/xmpp.py
models.py        RosterModel, MessageModel, NotificationModel, Transfer
chat.py          ChatSession: one open conversation (memory only), typing states, file transfer actions
tray.py          QSystemTrayIcon, dynamic right-click menu, icon with presence dot + unread badge
autostart.py     XDG autostart entry, .desktop + icon installation
config.py        JSON settings, written atomically with mode 0600
qml/             ChatWindow, ContactsWindow, SettingsWindow, NotificationsWindow (+ components)
```
