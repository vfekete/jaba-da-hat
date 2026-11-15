# Comment details

Source files keep comments short. When a comment needs a longer explanation
(why something is done this way, what was tried, which spec section forces
it), the code carries a marker such as `see docs/comments-details.md [3]` or
just `[3]`, and the full explanation lives here under the same number.
Numbers are global, never reused and never renumbered.

---

## src/jaba_da_hat/config.py

### [1] Why empty groups are stored locally

In XMPP a group is not an object of its own: RFC 6121 §2.1.2.2 defines
`<group/>` only as a child of a roster `<item/>`. A group with no members
therefore cannot exist on the server — the moment the last contact leaves it,
the group is gone. The spec asks for "add user-defined group(s)" before any
contact is in them, so newly created groups are kept in `Settings.local_groups`
and shown as an empty group with a drop target until a contact is moved in.
Once a contact is in the group it also exists on the server. Syncing empty
groups across devices could later use XEP-0049 private XML storage.

## src/jaba_da_hat/core.py

### [2] Declining a subscription with `unsubscribed`

RFC 6121 §3.1.4: "A client denies a subscription request by sending a
presence stanza of type `unsubscribed`". Per §3.1.3 the user's server keeps
pending inbound requests and redelivers them at every login until the user
approves or denies them, which is the "flooding" the spec complains about.
Sending `unsubscribed` makes the server delete the pending request (§3.2), so
it is not redelivered. The requester can still send a fresh request, which is
why "Decline and block" additionally issues an XEP-0191 block.

## src/jaba_da_hat/autostart.py

### [3] Which command goes into the .desktop files

The autostart and launcher entries must start *this* copy of the app.
When running from source that is `<venv python> -m jaba_da_hat`. In a Nuitka
onefile build the code runs from an extraction directory, so `sys.executable`
points inside that cache, not at the `jdh-<version>` file the user owns.
Nuitka exports the original binary path in `NUITKA_ONEFILE_BINARY` (verified
against the built binary), with `sys.argv[0]` as a fallback for
non-onefile compiled builds.

## src/jaba_da_hat/tray.py

### [4] Desktop notifications sent with jeepney, not QtDBus

GNOME's notification service (`org.freedesktop.Notifications.Notify`) expects
the signature `(susssasa{sv}i)`. PySide6's QtDBus bindings cannot produce
a `uint32` (`u`) argument from Python — `QDBusMessage`, `QDBusInterface`
with introspection and `callWithArgumentList` all send `i` and the call fails
with "does not match expected type" (tested against GNOME Shell 50.1).
0.1.0 first used `QSystemTrayIcon.showMessage` (Qt sends it from C++), and
since the tray became our own D-Bus implementation ([10]) notifications are sent
with jeepney, which marshals by explicit signature. That also gives
the notification id back, so a click (`ActionInvoked`) opens exactly the chats
that notification was about.

## src/jaba_da_hat/main.py

### [5] Graceful exit on session logout

On Wayland GNOME ends a session by sending SIGTERM to the session's
processes (XSMP `commitDataRequest` only exists on X11). A plain
`signal.signal` handler is not enough: CPython runs Python-level handlers only
when the interpreter gets control, which never happens while Qt's C++
event loop is blocking. A periodic no-op `QTimer` works around that when
running from source, but **not in the Nuitka build**: compiled code does not
check pending signals, so the handler never ran. Measured: the child ignored
SIGTERM until the onefile wrapper SIGKILLed it after its 5 s grace time
(exit 137, settings not saved).

The fix is the self-pipe pattern: `signal.set_wakeup_fd()` makes the
C-level handler write the signal number into a pipe, and a `QSocketNotifier`
on the read end wakes the Qt loop directly, with no Python bytecode involved.
The slot calls `quit()`, `aboutToQuit` runs `AppController.shutdown()`,
which sends `unavailable` presence, drops all in-memory conversations and
saves settings. Verified on both the source run and the onefile binary
(SIGTERM to the wrapper is forwarded to the child; exit code 0).

## build.sh

### [6] Explicitly bundling QML and icons; Nuitka instead of pyside6-deploy

The QML files are loaded by path at runtime (`QQmlComponent` with a file
URL built from `Path(__file__)`), so no static import scan can see them; the
same applies to the PNG icons. Both directories are bundled with
`--include-data-dir`. `pyside6-deploy` is driven by a spec file next to a
single entry script, and it detects QML only through static imports. This
project is a package (`src/jaba_da_hat`) with runtime-loaded QML, so calling
Nuitka directly with the `pyside6` plugin is simpler and more predictable.
The Wayland and platform-theme Qt plugins are listed explicitly because
the "sensible" default set does not cover a GNOME Wayland session (dark-mode
detection and window decorations depend on them).

## src/jaba_da_hat/toast.py

### [7] Own toast through XWayland, in a helper process

The user's GNOME has notification banners off (`org.gnome.desktop.notifications
show-banners = false`, which is what the "Do Not Disturb" switch sets), so
freedesktop notifications only land silently in the message list. The app
therefore draws its own toast. On native Wayland a client cannot position its
windows. GNOME places a new toplevel itself (usually centred) and may give it
focus, and GNOME does not implement wlr-layer-shell. Under XWayland a window
with `Qt::ToolTip | X11BypassWindowManagerHint` is override-redirect, so
Mutter shows it exactly where asked (top-right, below the panel) and never
focuses it. Running the whole app on XWayland would make every window blurry
with fractional scaling, so only the toast runs on XWayland, as a
short-lived child process (`jdh --toast …`) started by the tray. Its exit code
says what happened (0 clicked → open the chat, 1 timeout, 2 dismissed); any
other code (no XWayland, crash) falls back to a GNOME notification.

## src/jaba_da_hat/autostart.py (cont.)

### [8] Absolute icon path in the .desktop files

With `Icon=jaba-da-hat` GNOME Shell has to find the icon in the
`hicolor` theme under `~/.local/share/icons`. That directory has no
`index.theme`/icon cache of its own, and GNOME Shell caches theme lookups,
so a freshly installed icon is often not found until the shell restarts
(on Wayland that means logging out). This is what the user saw after the first
registration. The Desktop Entry spec allows an absolute path in `Icon=`,
which GNOME loads directly without any cache. The themed copies are still
installed for other consumers, and caches are refreshed best-effort without
touching system directories.

## src/jaba_da_hat/main.py (cont.)

### [9] File dialogs through the desktop portal

With Qt's default GNOME platform theme (`gtk3`) the QML `FileDialog`/`FolderDialog`
are native GTK dialogs running inside our process. GTK then tries to attach
D-Bus window properties to a parent that is a Qt Wayland surface, not a GDK
one, and logs `Gdk-CRITICAL … gdk_wayland_window_set_dbus_properties_libgtk_only:
assertion 'GDK_IS_WAYLAND_WINDOW (window)' failed`, which the user saw while testing.
The `xdgdesktopportal` platform theme shows the same GNOME file chooser through
xdg-desktop-portal (out of process), so no GTK code runs in the app. Dark-mode
detection is unchanged (verified: both themes report `ColorScheme.Dark` on
the target machine). `setdefault` keeps a user-provided `QT_QPA_PLATFORMTHEME`.

## src/jaba_da_hat/sni.py

### [10] Own StatusNotifierItem instead of QSystemTrayIcon

Ubuntu's AppIndicator extension (`appIndicator.js`, `provideActivationToken`)
creates a GNOME startup-notification sequence on every double click and
menu-item click, and hands its XDG activation token to the item via
`ProvideXdgActivationToken`. Qt's tray implements that method and keeps the
token for the next window it shows. When the action opens no window (status
change, log on/off, the contacts filter, double click with nothing waiting),
the token is never used and GNOME shows the busy cursor until its startup
timeout. The user reported this. Qt offers no switch to drop the method.
If the method is missing, the extension calls `launch_failed()` (ending the
sequence at once) and stops sending tokens for this item.

So the tray is our own StatusNotifierItem + `com.canonical.dbusmenu` on a
separate session-bus connection (jeepney, pure Python), driven from Qt's event
loop by a `QSocketNotifier`, so no threads are needed. It deliberately answers
`ProvideXdgActivationToken` with `UnknownMethod`. We tested that windows opened
from the tray without a token still get focus on the target machine
(GNOME 50, Wayland). The menu is still built as a `QMenu`; `MenuExporter`
publishes its actions (submenus, sections, radio/check states, icons) and
triggers the `QAction` on `clicked`, so the menu code and its tests stayed
unchanged. Tested end-to-end against a private `dbus-daemon` with a fake
watcher (tests/test_sni.py) and live against GNOME's watcher.

### [11] Menu item ids are never reused

The AppIndicator extension (`dbusMenu.js`, `_doLayoutUpdate`) asks `GetLayout`
only for the `type` and `children-display` properties. For an id it
already knows it applies just those and keeps its cached label, enabled
and toggle state. It fetches full properties (`GetGroupProperties`) only for
ids it has not seen. The first exporter renumbered items from 1 on every rebuild,
so a rebuilt menu with the same shape reused every id and the panel kept
showing the old texts. The user saw "Status: Offline / Connecting…" long
after the (mock) login had succeeded, while the tray icon — drawn from live
state — was already green. A counter that never resets makes every rebuild
consist of new items, which the extension fetches in full.

## src/jaba_da_hat/tray.py

### [12] Wiggle frames are held 150 ms, with no upright frame in between

The AppIndicator extension does not show every icon we publish. After each
`NewIcon` signal it waits 30 ms (`MAX_UPDATE_FREQUENCY`), then fetches
`IconPixmap` asynchronously. Measured on the target machine, that is about one
fetch per 140 ms. The first wiggle alternated tilted and upright frames every
70 ms (`0, 16, 0, -16, …`), so the panel sampled every second frame. After
the first round it mostly hit the upright ones, and the user saw the icon wiggle only once,
although our timer kept running and the panel kept reading (verified with debug
logging of every wiggle round and every `IconPixmap` read). Frames now last
150 ms and alternate between two tilts, so every sample the panel takes is
visibly different from the previous one.

## src/jaba_da_hat/backend/xmpp.py

### [13] slixmpp runs in its own thread, not on the Qt loop through qasync

The plan first said `slixmpp` + `qasync` (one shared event loop). The backend
instead runs slixmpp on a private asyncio loop in a worker thread:

- DNS SRV lookups, TCP connect and the TLS handshake never stall the tray or a
  chat window, even on a slow or broken network.
- No extra dependency whose Python 3.14 / PySide6 support we'd have to track.
- The boundary is narrow and explicit: UI → loop through
  `loop.call_soon_threadsafe` (`_call`), loop → UI through `_emit`, which wraps
  each signal emission in a closure and sends it over a private signal with
  `Qt.QueuedConnection` to a slot of the backend (a main-thread object). So
  every public signal is emitted on the main thread, and connected Python
  lambdas never run on the worker thread, whatever PySide's rules are for
  connections without a receiver object.

All slixmpp objects (the client, its roster) are touched only on the loop
thread. `shutdown()` disconnects, cancels the remaining slixmpp tasks and
closes the loop; without the cancel, finalizers of dropped clients ran after
the loop was gone and printed "Event loop is closed" on exit.

### [14] Without "require TLS" only SCRAM may authenticate

slixmpp refuses every SASL mechanism on an unencrypted stream by default. When
the user switches off "require TLS" (a server without TLS, e.g. on a LAN), we
allow only SCRAM: it proves knowledge of the password without sending it, so a
listener learns nothing they could log in with. PLAIN would put the password
on the wire in base64. A server that offers only PLAIN without TLS therefore
fails to authenticate, which is deliberate.

### [15] Connection retries belong to the controller, not to slixmpp

slixmpp retries a failed connect forever on its own (back-off up to 300 s), and
nothing outside it would know. `reschedule_connection_attempt` is overridden
to stop after one pass over all addresses and report `offline`. The controller
then retries on its own schedule (`RECONNECT_DELAYS_S`), only while the user
wants to be online and auto-reconnect is on. So there is exactly one retry
loop, Log off really stops it, and the "Cannot connect" notice appears once per
outage instead of on every attempt.

Permanent failures (wrong password, untrusted certificate, the same resource
logged in elsewhere) emit `loginRejected` instead. The controller then stops
retrying: repeating a wrong password can get the account locked, and two
clients with the same resource would kick each other off forever.

### [16] A subscription request from someone we already asked is approved automatically

When we add a contact we send `subscribe`. Most clients answer by approving
it **and** sending their own `subscribe` back, so both sides see each other.
Asking the user "X wants to add you" right after they added X themselves is
noise, so a request from a JID we already subscribed to (or are waiting for,
`pending_out`) is approved without asking. Requests from anyone else still go
to Notifications. This concerns presence only. It has nothing to do with OMEMO
device trust, which is never automatic.

### [17] Server history: everything since we were last connected

Decided: archived messages (XEP-0313 MAM) are shown as incoming messages and
forgotten when the window closes. Fetching the whole archive at every login
would bring back conversations already read, so the controller stores one
timestamp, `history_since`, in the settings: the moment we were last
connected (written when the connection goes down and when the app quits). At
login, only messages archived after it are fetched (at most `MAM_LIMIT`). No
message content is stored on disk.

- First start (no timestamp yet): no history is fetched.
- After a crash the timestamp is older, so some already-seen messages can
  show up again. That is harmless and rare.
- A message can arrive twice: as a live offline delivery and in the archive.
  Both carry the archive id (XEP-0359 `stanza-id` from our own server), and
  each id is shown only once.
- Messages I sent from another device are skipped in history (the interface
  shows them only live, through carbons, in a chat that is open).

### [20] A message whose body is just its XEP-0066 link is a shared file

HTTP Upload (XEP-0363) only puts a file on a web server; the recipient learns
about it from an ordinary message. Conversations, Dino, Gajim and others send
the download URL both as the message body and as an XEP-0066 `<x
xmlns='jabber:x:oob'><url/>` element, and treat exactly that shape as "file"
rather than "text". We do the same in both directions:

- receiving: an http(s) OOB URL with an empty body or a body equal to the URL
  becomes a file card (`fileOffered`). Anything else (a link inside a
  sentence) stays a message with a clickable link.
- sending: after the upload finishes we send body = URL plus the OOB element,
  so other clients show a file and old clients still see a working link.

Nothing is requested from the file server before the user presses Download,
not even a `HEAD` for the size: any request tells the server (often run by a
third party) that we are online and from which address. The card therefore
says "size unknown" until the download starts and `Content-Length` arrives.

Downloads go to `<name>.part` and are renamed when complete. Pause aborts the
request and keeps the `.part`; resume asks for the rest with `Range`. A
server that ignores `Range` (answers 200 instead of 206) is handled by starting
the file over. Uploads cannot be paused: an XEP-0363 slot is a single PUT, so
the Pause button is hidden for them (`can_pause: False`) and only Cancel is
offered.

## src/jaba_da_hat/backend/jingle.py

### [21] Jingle file transfer: routing, transports, pause and resume

**Routing.** Sending asks each online resource of the contact (highest
priority first) for disco#info. If one advertises Jingle, Jingle FT 5 and the
IBB transport, the file is offered to that resource over Jingle, as decided
("Jingle preferred"). Otherwise (no such client, contact offline, or the offer
refused) it goes through HTTP Upload, which also reaches an offline contact.

**Only IBB.** In-Band Bytestreams run through the server, so they work behind
any NAT or firewall without extra setup. SOCKS5 (XEP-0260) would be faster but
needs reachable candidates or a proxy; it is not implemented, so we don't
advertise it. Many clients still offer SOCKS5 first. Then we accept with an
empty SOCKS5 transport and report `candidate-error`; per XEP-0260 the sender
falls back with `transport-replace` to IBB, which we accept. Up to `WINDOW`
IBB blocks are in flight at once, so throughput is not limited to one block per
round trip.

**Integrity.** The sender hashes the file (SHA-256) before offering it and puts
the hash into the offer. The receiver hashes while writing, also over an
existing `.part` when resuming, and deletes the file if the hash differs.
Nothing unverified is renamed from `.part` to its real name.

**Pause.** Jingle has no pause, so each side does what is possible:

- sender: stops sending blocks; the session stays open and Resume continues.
  The receiver just sees a stall.
- receiver: ends the session (`<cancel/>` with the text `paused`, so our own
  sender shows "Paused by the contact" instead of "cancelled") and keeps the
  `.part`. Resume sends a new session-initiate *requesting* the file
  (XEP-0234 §6.2) with `<range offset=…>` (§6.4). Our sender keeps every file
  it offered while the card exists and answers such a request from the same
  contact for the same hash. Other clients usually can't, so the card then says
  to ask for the file again; the `.part` stays until the card is cancelled.

The same request path resumes a download after the connection broke. A send
that failed is resumed by offering the file again.

## src/jaba_da_hat/backend/e2ee.py

### [22] OMEMO: per-window trust, what is stored, and "never silently plain"

**Library.** Encryption is not written here: python-omemo (Syndace) does the
X3DH / Double Ratchet work, slixmpp-omemo connects it to slixmpp. slixmpp-omemo
currently enables the legacy namespace (`eu.siacs.conversations.axolotl`),
which is what Conversations, Dino and Gajim speak; OMEMO 2 (`urn:xmpp:omemo:2`)
waits for slixmpp to support Stanza Content Encryption (XEP-0420).

**Trust (decided).** No device is ever trusted automatically (blind trust
before verification is off). When a message is about to be encrypted for
devices nobody approved *in this chat window*, the window shows their
fingerprints and nothing is pre-selected; only ticked devices can read the
message, and "Don't send" cancels it. The approvals belong to that window:
before every encryption we reset each device's trust to what the window
decided (approved / rejected / undecided), so another window, or the same
contact after the window was closed, asks again. Own other devices are
treated the same way. Trust decisions are kept in memory only: the storage
class routes every `/trust/…` key to a dict that is never written.

**Stored on disk** (`~/.local/share/jaba-da-hat/omemo/<hash>.json`, 0600, in
a 0700 directory, written atomically with fsync): the device's private
identity key, pre-keys and the ratchet sessions. They must survive restarts:
without them the device id changes at every start (contacts would see a new
device each time) and messages encrypted while we were offline could never be
decrypted. No message content is stored.

**Never silently plain.** The lock in the chat window is what the user
*wants*. It starts on (setting "encryption by default") before we know whether
the contact has OMEMO devices, so there's no window of time in which the first
message could slip out unencrypted. If encryption is on but impossible (no
devices, all rejected, offline), the message is *not sent* and the chat says
why; only switching the lock off sends plain text.

**Incoming.** Encrypted messages are decrypted whatever the trust state (the
library allows it). A message from a device not approved in this window is
shown with a one-time line naming the device and its fingerprint.

**Files.** In an encrypted chat a file must not reach the server readable.
Jingle IBB passes through the server and plain HTTP Upload stores the file
as is, so encrypted chats use XEP-0454 only: the file is encrypted with
AES-256-GCM (random key, 12-byte IV) into a private temp file, the ciphertext
is uploaded, and the link `aesgcm://host/path#<iv><key>` travels inside the
OMEMO message. The fragment is never sent to the web server. The upload slot
and the device approval come *before* the upload, so cancelling the approval
uploads nothing. Receiving downloads the ciphertext (resumable), then decrypts
and checks the GCM tag in a worker thread; an altered file is deleted, not
kept.

## src/jaba_da_hat/main.py

### [18] slixmpp is never logged at DEBUG

slixmpp's DEBUG output contains every stanza sent and received, including the
SASL exchange. With PLAIN authentication that is the password in base64, and
it would end up in a terminal or a bug report. `-v` therefore raises only our
own loggers to DEBUG; slixmpp stays at INFO (WARNING without `-v`).

## build.sh

### [19] slixmpp plugins are listed for Nuitka by asking slixmpp

`register_plugin("xep_0313")` imports `slixmpp.plugins.xep_0313` by name at
runtime, and each plugin pulls in its dependencies the same way. Nuitka only
follows static imports, so a binary built without help logs in and then fails
on the first plugin. build.sh creates a client exactly like the app does,
initialises its plugins, and passes every loaded `slixmpp.plugins.*` /
`slixmpp.features.*` package to `--include-package`. The list stays correct
when the backend starts using a new XEP.
