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
    assert "Icon=jaba-da-hat" in entry and "Categories=Network;InstantMessaging;Chat;" in entry
    assert any(p.name == "jaba-da-hat.png" for p in written)
    removed = autostart.uninstall_desktop_entry()
    assert set(removed) >= set(written)
