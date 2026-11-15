from jaba_da_hat import autostart


def test_enable_disable(isolated_home):
    assert not autostart.is_enabled()
    autostart.set_enabled(True)
    f = autostart.autostart_file()
    assert str(f).startswith(str(isolated_home))
    text = f.read_text()
    assert "X-GNOME-Autostart-enabled=true" in text and "--autostart" in text
    assert autostart.is_enabled()
    autostart.set_enabled(False)
    assert not f.exists()


def test_install_desktop_entry(isolated_home):
    written = autostart.install_desktop_entry()
    assert all(str(p).startswith(str(isolated_home)) for p in written)
    entry = autostart.applications_file().read_text()
    assert f"Icon={autostart.icon_file()}" in entry and autostart.icon_file().exists() and "Categories=Network;InstantMessaging;Chat;" in entry
    assert any(p.name == "jaba-da-hat.png" for p in written)
    removed = autostart.uninstall_desktop_entry()
    assert set(removed) >= set(written)


def test_parse_version():
    assert autostart.parse_version("0.10.2") > autostart.parse_version("0.9.9")
    assert autostart.parse_version("1.0.0-rc1") == (1, 0, 0)
    assert autostart.parse_version("garbage") == ()


def test_refresh_registers_when_missing(isolated_home):
    assert autostart.refresh_registration() == "registered"
    assert autostart.registered_version() == autostart.__version__


def test_refresh_reregisters_older_version_and_autostart(isolated_home):
    autostart.install_desktop_entry()
    autostart.set_enabled(True)
    f = autostart.applications_file()
    f.write_text(f.read_text().replace(f"X-AppVersion={autostart.__version__}", "X-AppVersion=0.0.1"))
    auto = autostart.autostart_file()
    auto.write_text("[Desktop Entry]\nExec=/old/jdh-0.0.1 --autostart\n")
    assert autostart.registered_version() == "0.0.1"
    assert autostart.refresh_registration() == "updated"
    assert autostart.registered_version() == autostart.__version__
    assert "/old/" not in auto.read_text() and "X-GNOME-Autostart-enabled=true" in auto.read_text()


def test_refresh_leaves_same_or_newer(isolated_home):
    autostart.install_desktop_entry()
    assert autostart.refresh_registration() == "current"
    assert autostart.refresh_registration("0.0.1") == "newer"


def test_unregister_cli(isolated_home, capsys):
    from jaba_da_hat.main import main

    assert main(["--register"]) == 0
    assert autostart.registered_version() is not None
    assert main(["--unregister"]) == 0
    assert autostart.registered_version() is None
    assert main(["--unregister"]) == 0
    assert "was not registered" in capsys.readouterr().out
