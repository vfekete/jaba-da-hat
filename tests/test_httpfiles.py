"""HttpFiles against a slow local HTTP server: pause, resume (Range), servers without Range, cancel."""

from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import wait_until

DATA = os.urandom(256 * 1024)
CHUNK = 8 * 1024


def make_server(support_range: bool):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):  # noqa: N802
            start = 0
            rng = self.headers.get("Range", "")
            if support_range and rng.startswith("bytes="):
                start = int(rng[6:].split("-")[0])
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{len(DATA) - 1}/{len(DATA)}")
            else:
                self.send_response(200)
            self.send_header("Content-Length", str(len(DATA) - start))
            self.end_headers()
            try:
                for i in range(start, len(DATA), CHUNK):
                    self.wfile.write(DATA[i:i + CHUNK])
                    self.wfile.flush()
                    time.sleep(0.01)  # ~800 kB/s: slow enough to pause in the middle
            except (BrokenPipeError, ConnectionResetError):
                pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture(params=[True, False], ids=["range", "no-range"])
def server(request):
    srv = make_server(request.param)
    yield f"http://127.0.0.1:{srv.server_address[1]}/file.bin", request.param
    srv.shutdown()


def test_pause_resume_completes_with_identical_content(qapp, tmp_path, server):
    from jaba_da_hat.backend.httpfiles import HttpFiles, part_path

    url, _ = server
    http = HttpFiles()
    dest = str(tmp_path / "file.bin")
    done, results = [], []
    http.download("t", url, dest, lambda d, t: done.append((d, t)), lambda st, det: results.append((st, det)))
    assert wait_until(lambda: done and done[-1][0] > 32 * 1024, 5000)
    assert http.abort("t", "paused")
    assert wait_until(lambda: results, 3000)
    assert results[-1] == ("paused", "")
    assert 0 < part_path(dest).stat().st_size < len(DATA)

    http.download("t", url, dest, lambda d, t: done.append((d, t)), lambda st, det: results.append((st, det)))
    assert wait_until(lambda: len(results) == 2, 10000)
    assert results[-1] == ("completed", dest)
    assert open(dest, "rb").read() == DATA  # also when the server ignored Range and sent everything again
    assert done[-1] == (len(DATA), len(DATA))
    assert not part_path(dest).exists()


def test_cancel_removes_partial_file(qapp, tmp_path, server):
    from jaba_da_hat.backend.httpfiles import HttpFiles, part_path

    url, _ = server
    http = HttpFiles()
    dest = str(tmp_path / "file.bin")
    done, results = [], []
    http.download("t", url, dest, lambda d, t: done.append(d), lambda st, det: results.append(st))
    assert wait_until(lambda: done and done[-1] > 0, 5000)
    http.abort("t", "cancelled")
    assert wait_until(lambda: results, 3000)
    assert results == ["cancelled"]
    assert not part_path(dest).exists() and not os.path.exists(dest)


def test_http_error_is_reported(qapp, tmp_path):
    from jaba_da_hat.backend.httpfiles import HttpFiles

    class NotFound(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):  # noqa: N802
            self.send_error(404)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), NotFound)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        results = []
        http = HttpFiles()  # must outlive the request
        http.download("t", f"http://127.0.0.1:{srv.server_address[1]}/x", str(tmp_path / "x"),
                             lambda *_: None, lambda st, det: results.append((st, det)))
        assert wait_until(lambda: results, 5000)
        assert results[0][0] == "failed" and "404" in results[0][1]
        assert not (tmp_path / "x").exists()
    finally:
        srv.shutdown()
