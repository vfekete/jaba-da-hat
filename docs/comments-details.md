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
