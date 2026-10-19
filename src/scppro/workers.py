from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from .sftp import TransferCancelled, error_text


@dataclass(eq=False)
class DecisionRequest:
    kind: str
    payload: Any
    default: Any
    response: Any = None
    event: threading.Event = field(default_factory=threading.Event)

    def resolve(self, response: Any) -> None:
        if not self.event.is_set():
            self.response = response
            self.event.set()


class DecisionBridge(QObject):
    requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._requests: set[DecisionRequest] = set()
        self._lock = threading.Lock()
        self._closed = False

    def ask(self, kind: str, payload: Any, default: Any, cancellation: threading.Event) -> Any:
        request = DecisionRequest(kind, payload, default)
        with self._lock:
            if self._closed:
                raise TransferCancelled()
            self._requests.add(request)
        try:
            self.requested.emit(request)
            while not request.event.wait(0.1):
                if cancellation.is_set():
                    request.resolve(default)
                    raise TransferCancelled()
            if cancellation.is_set():
                raise TransferCancelled()
            return request.response
        finally:
            with self._lock:
                self._requests.discard(request)

    def cancel_all(self, close: bool = False) -> None:
        with self._lock:
            self._closed = self._closed or close
            for request in self._requests:
                request.resolve(request.default)


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()
    progress = Signal(object)


class Worker(QRunnable):
    def __init__(self, operation: Callable[[], Any], secret: str = ""):
        super().__init__()
        self.operation = operation
        self.secret = secret
        self.signals = WorkerSignals()

    def run(self):
        try:
            result = self.operation()
            self.signals.result.emit(result)
        except Exception as exc:
            self.signals.error.emit(error_text(exc, self.secret))
        finally:
            self.secret = ""
            self.operation = lambda: None
            self.signals.finished.emit()
