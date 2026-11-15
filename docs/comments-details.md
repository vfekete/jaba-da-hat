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

### [4] Desktop notifications via QSystemTrayIcon.showMessage

GNOME's notification service (`org.freedesktop.Notifications.Notify`) expects
the signature `(susssasa{sv}i)`. PySide6's QtDBus bindings cannot produce
a `uint32` (`u`) argument from Python — `QDBusMessage`, `QDBusInterface`
with introspection and `callWithArgumentList` all send `i` and the call fails
with "does not match expected type" (tested against GNOME Shell 50.1).
Qt's own StatusNotifierItem tray implementation calls the same D-Bus method
from C++ with the right types, so `showMessage` produces a normal GNOME
notification and `messageClicked` reports the default action. The drawback
is that the click signal does not say which notification was clicked, so the
tray remembers the JID of the latest one.

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
