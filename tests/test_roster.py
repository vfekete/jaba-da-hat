from jaba_da_hat.models import UNGROUPED, Contact, NotificationModel, RosterModel


def rows(model):
    return [(model.data(model.index(i), model.roleNames().__iter__() and 0x100 + 0),
             model.data(model.index(i), 0x100 + 2)) for i in range(model.rowCount())]


def test_rows_per_group_and_placeholders(qapp):
    m = RosterModel()
    m.set_local_groups(["Empty", "Friends"])
    m.set_contacts([
        Contact("a@example.org", "A", ["Friends", "Work"]),
        Contact("b@example.org", "B", []),
    ])
    r = rows(m)
    assert ("a@example.org", "Friends") in r and ("a@example.org", "Work") in r
    assert ("", "Empty") in r  # placeholder row for an empty user-defined group
    assert r[-1] == ("b@example.org", UNGROUPED)  # ungrouped sorts last
    assert m.groups() == ["Empty", "Friends", "Work"]


def test_upsert_keeps_presence_and_unread(qapp):
    m = RosterModel()
    m.set_contacts([Contact("a@example.org", "A")])
    m.update_presence("a@example.org", "away", "brb")
    m.add_unread("a@example.org", 2)
    m.upsert(Contact("a@example.org", "Renamed", ["G"]))
    c = m.contact("a@example.org")
    assert (c.name, c.presence, c.status_text, c.unread) == ("Renamed", "away", "brb", 2)
    assert m.total_unread() == 2
    m.add_unread("a@example.org", 0)
    assert m.total_unread() == 0


def test_notifications_dedupe_subscription(qapp):
    n = NotificationModel()
    n.add("subscription", "x", "s@example.net")
    n.add("subscription", "y", "s@example.net")
    n.add("error", "boom")
    assert n.count == 2
    n.remove_where(kind="error")
    assert [i["text"] for i in n.items()] == ["y"]
