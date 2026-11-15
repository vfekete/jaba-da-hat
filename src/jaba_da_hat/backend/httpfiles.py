"""HTTP side of file transfer: uploads to an XEP-0363 slot and downloads of shared links.

Runs on the Qt main thread with QNetworkAccessManager. Downloads go to "<dest>.part" and are
renamed when complete, so a paused or failed download resumes with an HTTP Range request.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QFile, QIODevice, QObject, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest, QSslCertificate, QSslConfiguration

log = logging.getLogger(__name__)
# Headers a slot may ask us to send; everything else is ignored (XEP-0363 §5).
ALLOWED_PUT_HEADERS = ("authorization", "cookie", "expires")


def part_path(dest: str) -> Path:
    return Path(dest + ".part")


def trust_ca_file(path: str) -> None:
    """Add a CA (PEM) to the certificates Qt networking trusts, e.g. for the test server."""
    conf = QSslConfiguration.defaultConfiguration()
    conf.addCaCertificates(QSslCertificate.fromPath(path))
    QSslConfiguration.setDefaultConfiguration(conf)


@dataclass
class _Job:
    reply: QNetworkReply
    on_progress: Callable[[int, int], None]  # done, total (0 = unknown)
    on_done: Callable[[str, str], None]  # state ("completed" | "failed" | "cancelled" | "paused"), detail
    stop_as: str = ""  # set by abort(): the state to report instead of a network error
    extra: dict = field(default_factory=dict)


class HttpFiles(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._nam.setRedirectPolicy(QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        self._jobs: dict[str, _Job] = {}

    def busy(self, tid: str) -> bool:
        return tid in self._jobs

    # ---- download ----------------------------------------------------
    def download(self, tid: str, url: str, dest: str, on_progress, on_done) -> None:
        """Fetch *url* into *dest*, continuing a previous ``.part`` file when the server allows it."""
        part = part_path(dest)
        part.parent.mkdir(parents=True, exist_ok=True)
        offset = part.stat().st_size if part.exists() else 0
        req = QNetworkRequest(QUrl(url))
        req.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute,
                         QNetworkRequest.CacheLoadControl.AlwaysNetwork)
        if offset:
            req.setRawHeader(b"Range", f"bytes={offset}-".encode())
        reply = self._nam.get(req)
        job = _Job(reply, on_progress, on_done, extra={"part": part, "dest": dest, "offset": offset, "fh": None})
        self._jobs[tid] = job
        reply.readyRead.connect(lambda: self._write(job))
        reply.downloadProgress.connect(lambda got, total: job.on_progress(
            job.extra["offset"] + got, job.extra["offset"] + total if total > 0 else 0))
        reply.finished.connect(lambda: self._download_finished(tid, job))

    def _write(self, job: _Job) -> None:
        if job.extra["fh"] is None:
            status = job.reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if job.extra["offset"] and status != 206:  # server ignored the Range: start over
                job.extra["offset"] = 0
                job.extra["fh"] = open(job.extra["part"], "wb")
            else:
                job.extra["fh"] = open(job.extra["part"], "ab")
        job.extra["fh"].write(bytes(job.reply.readAll()))

    def _download_finished(self, tid: str, job: _Job) -> None:
        self._jobs.pop(tid, None)
        reply = job.reply
        if reply.bytesAvailable():
            self._write(job)
        if job.extra["fh"] is not None:
            job.extra["fh"].close()
        part, dest = job.extra["part"], job.extra["dest"]
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        err = reply.error()
        reply.deleteLater()
        if job.stop_as:
            if job.stop_as == "cancelled":
                part.unlink(missing_ok=True)
            job.on_done(job.stop_as, "")
        elif err != QNetworkReply.NetworkError.NoError:
            job.on_done("failed", _network_error(reply, status))
        elif status == 416 and part.exists():  # Range beyond the end: we already have everything
            os.replace(part, dest)
            job.on_done("completed", dest)
        else:
            if not part.exists():
                part.touch()
            os.replace(part, dest)
            job.on_done("completed", dest)

    # ---- upload ------------------------------------------------------
    def upload(self, tid: str, put_url: str, headers: dict[str, str], path: str, content_type: str,
               on_progress, on_done) -> None:
        f = QFile(path)
        if not f.open(QIODevice.OpenModeFlag.ReadOnly):
            on_done("failed", f"Cannot read {path}: {f.errorString()}")
            return
        req = QNetworkRequest(QUrl(put_url))
        req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, content_type)
        req.setHeader(QNetworkRequest.KnownHeaders.ContentLengthHeader, f.size())
        for name, value in headers.items():
            if name.lower() in ALLOWED_PUT_HEADERS:
                req.setRawHeader(name.encode(), value.replace("\n", " ").encode())
        reply = self._nam.put(req, f)
        f.setParent(reply)  # the file stays open exactly as long as the request
        job = _Job(reply, on_progress, on_done)
        self._jobs[tid] = job
        reply.uploadProgress.connect(lambda sent, total: job.on_progress(sent, max(total, 0)))
        reply.finished.connect(lambda: self._upload_finished(tid, job))

    def _upload_finished(self, tid: str, job: _Job) -> None:
        self._jobs.pop(tid, None)
        reply = job.reply
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        err = reply.error()
        reply.deleteLater()
        if job.stop_as:
            job.on_done(job.stop_as, "")
        elif err != QNetworkReply.NetworkError.NoError or status not in (200, 201):
            job.on_done("failed", _network_error(reply, status))
        else:
            job.on_done("completed", "")

    # ---- control -----------------------------------------------------
    def abort(self, tid: str, report_as: str) -> bool:
        """Stop a running job; it then reports *report_as* ("paused" or "cancelled")."""
        job = self._jobs.get(tid)
        if job is None:
            return False
        job.stop_as = report_as
        job.reply.abort()
        return True

    def abort_all(self) -> None:
        for tid in list(self._jobs):
            self.abort(tid, "failed")


def _network_error(reply: QNetworkReply, status) -> str:
    if status and int(status) >= 400:
        reason = reply.attribute(QNetworkRequest.Attribute.HttpReasonPhraseAttribute) or ""
        return f"Server answered {int(status)} {reason}".strip()
    return reply.errorString()
