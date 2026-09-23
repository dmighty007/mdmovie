"""Runs analysis presets on a background thread, one at a time."""
from __future__ import annotations

import queue
import threading

from PySide6.QtCore import QObject, QThread, Signal

from mdmovie.analysis import runner
from mdmovie.analysis.registry import Cancelled


class _Worker(QThread):
    progress = Signal(str, int, int)      # label, done, total
    finished_key = Signal(str, str)       # key, error message ("" on success)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.jobs: queue.Queue = queue.Queue()
        self.cancel_flag = threading.Event()

    def run(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            key, project, series, label = job
            self.cancel_flag.clear()
            err = ""
            try:
                runner.compute(project, series, lambda i, n: self.progress.emit(label, i, n),
                               self.cancel_flag.is_set)
            except Cancelled:
                err = "cancelled"
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
            self.finished_key.emit(key, err)


class AnalysisManager(QObject):
    progress = Signal(str, int, int)
    result_ready = Signal(str)
    failed = Signal(str, str)
    busy_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queued: set[str] = set()
        self._cancelled: set[str] = set()  # not re-queued automatically until "Recompute"
        self._worker = _Worker(self)
        self._worker.progress.connect(self.progress)
        self._worker.finished_key.connect(self._done)
        self._worker.start()

    @property
    def busy(self) -> bool:
        return bool(self._queued)

    def ensure(self, project) -> None:
        """Queue every series in the project that has no result yet."""
        was_busy = self.busy
        for key, s in runner.missing_series(project):
            if key in self._queued or key in self._cancelled:
                continue
            self._queued.add(key)
            self._worker.jobs.put((key, project.clone(), s, s.label or s.preset))
        if self.busy and not was_busy:
            self.busy_changed.emit(True)

    def recompute(self, project, series) -> None:
        key = runner.series_key(project, series)
        if key and key not in self._queued:
            self._cancelled.discard(key)
            runner.forget(key)
            self.ensure(project)

    def cancel(self) -> None:
        """Cancel the running job and drop the queue."""
        try:
            while True:
                job = self._worker.jobs.get_nowait()
                if job:
                    self._queued.discard(job[0])
                    self._cancelled.add(job[0])
        except queue.Empty:
            pass
        self._worker.cancel_flag.set()

    def _done(self, key: str, err: str) -> None:
        self._queued.discard(key)
        if err == "cancelled":
            self._cancelled.add(key)
        if err:
            self.failed.emit(key, err)
        else:
            self.result_ready.emit(key)
        if not self.busy:
            self.busy_changed.emit(False)

    def shutdown(self) -> None:
        self.cancel()
        self._worker.jobs.put(None)
        self._worker.wait(3000)
