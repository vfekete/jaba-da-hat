from jaba_da_hat.textutil import human_size, linkify


def test_escapes_markup_and_never_produces_images():
    out = linkify('<img src="https://evil.example/x.png"><script>alert(1)</script>')
    assert "<img" not in out and "<script" not in out
    assert "&lt;img" in out


def test_links():
    out = linkify("see https://example.org/a?b=1&c=2, and www.example.net.")
    assert '<a href="https://example.org/a?b=1&amp;c=2">' in out
    assert '<a href="https://www.example.net">www.example.net</a>.' in out
    assert out.count("<a ") == 2


def test_image_url_is_only_a_link():
    out = linkify("https://example.org/cat.jpg")
    assert out == '<a href="https://example.org/cat.jpg">https://example.org/cat.jpg</a>'


def test_newlines_and_quotes():
    assert linkify('a\n"b"') == "a<br>&quot;b&quot;"


def test_human_size():
    assert human_size(10) == "10 B"
    assert human_size(2048) == "2.0 KiB"
    assert human_size(5 * 1024 * 1024) == "5.0 MiB"
