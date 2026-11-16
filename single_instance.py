import hashlib
import os
from pathlib import Path
from time import monotonic

from PySide6.QtCore import (
    QObject,
    QLockFile,
    QTimer,
    Signal,
)
from PySide6.QtNetwork import (
    QLocalServer,
    QLocalSocket,
)
from PySide6.QtWidgets import QApplication


class SingleInstance(QObject):
    secondary_finished = Signal(bool, str)

    def __init__(self, data_dir, parent=None):
        super().__init__(parent)

        self.data_dir = Path(data_dir)

        # Один канал на одну папку пользовательских данных.
        # Исходники и установленная версия с одинаковым DATA
        # будут обращаться к одному экземпляру.
        normalized = os.path.normcase(
            str(self.data_dir.resolve())
        )
        identifier = hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest()[:24]

        self.server_name = (
            f"TaskWallpaper-{identifier}"
        )

        self.lock = QLockFile(
            str(self.data_dir / "application.lock")
        )

        self.server = QLocalServer(self)
        self.server.setSocketOptions(
            QLocalServer.SocketOption.UserAccessOption
        )
        self.server.newConnection.connect(
            self.accept_connections
        )

        self.window = None
        self.show_when_ready = False
        self.is_primary = False

        self.client = None
        self.client_buffer = bytearray()
        self.client_command = b"SHOW\n"
        self.client_deadline = 0
        self.client_finished = False

        self.retry_timer = QTimer(self)
        self.retry_timer.setInterval(250)
        self.retry_timer.timeout.connect(
            self.try_notify_primary
        )

    def try_primary(self):
        if not self.lock.tryLock(0):
            if (
                self.lock.error()
                != QLockFile.LockError.LockFailedError
            ):
                raise RuntimeError(
                    "Не удалось создать блокировку приложения. "
                    "Проверьте доступ к папке данных:\n"
                    f"{self.data_dir}"
                )

            return False

        self.is_primary = True

        # Удалять старый адрес разрешено только после
        # получения блокировки. На Windows вызов ничего
        # не удаляет, но полезен при переносе на другие ОС.
        QLocalServer.removeServer(self.server_name)

        if not self.server.listen(self.server_name):
            message = self.server.errorString()
            self.lock.unlock()
            self.is_primary = False

            raise RuntimeError(
                "Не удалось открыть канал связи приложения:\n"
                f"{message}"
            )

        return True

    def attach_window(self, window):
        self.window = window

        if self.show_when_ready:
            self.show_when_ready = False
            QTimer.singleShot(
                0,
                self.activate_window,
            )

    def activate_window(self):
        if self.window is None:
            self.show_when_ready = True
            return

        if getattr(self.window, "_closing", False):
            return

        # Не закрываем открытый редактор задачи
        # и не создаём новое главное окно.
        self.window.show_window()

        modal = QApplication.activeModalWidget()

        if modal is not None:
            modal.raise_()
            modal.activateWindow()

    def accept_connections(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()

            if socket is None:
                continue

            socket.setReadBufferSize(1024)
            socket.setProperty("command_buffer", b"")
            socket.setProperty("command_handled", False)

            timeout = QTimer(socket)
            timeout.setSingleShot(True)
            timeout.setInterval(5000)
            timeout.timeout.connect(socket.abort)
            timeout.start()

            socket.readyRead.connect(
                lambda socket=socket:
                self.read_command(socket)
            )
            socket.disconnected.connect(
                socket.deleteLater
            )

            # Данные могли прийти до подключения readyRead.
            self.read_command(socket)

    def read_command(self, socket):
        if socket.property("command_handled"):
            return

        buffer = bytes(
            socket.property("command_buffer") or b""
        )
        buffer += bytes(socket.readAll())

        if len(buffer) > 128:
            socket.abort()
            return

        socket.setProperty(
            "command_buffer",
            buffer,
        )

        if b"\n" not in buffer:
            return

        socket.setProperty(
            "command_handled",
            True,
        )

        command = buffer.split(b"\n", 1)[0].strip()

        if (
            self.window is not None
            and getattr(self.window, "_closing", False)
        ):
            reply = b"CLOSING\n"

        elif command == b"SHOW":
            self.activate_window()
            reply = b"OK\n"

        elif command == b"PING":
            # Повторная автозагрузка не открывает окно.
            reply = b"OK\n"

        else:
            reply = b"ERROR\n"

        socket.write(reply)
        socket.flush()

        # Qt отправит накопленные данные перед отключением.
        socket.disconnectFromServer()

    def notify_primary(self, show_window=True):
        self.client_command = (
            b"SHOW\n"
            if show_window
            else b"PING\n"
        )
        self.client_deadline = monotonic() + 8.0
        self.client_finished = False
        self.client_buffer.clear()

        self.client = QLocalSocket(self)
        self.client.connected.connect(
            self.send_command
        )
        self.client.readyRead.connect(
            self.read_reply
        )
        self.client.disconnected.connect(
            self.client_buffer.clear
        )

        self.retry_timer.start()
        QTimer.singleShot(
            0,
            self.try_notify_primary,
        )

    def try_notify_primary(self):
        if self.client_finished:
            return

        if monotonic() >= self.client_deadline:
            self.finish_secondary(
                False,
                "Работающий экземпляр не ответил.\n\n"
                "Возможно, приложение ещё запускается, "
                "завершается или не отвечает.\n"
                "Подождите немного и попробуйте снова.\n\n"
                "Второй экземпляр не запущен, "
                "чтобы не повредить данные.",
            )
            return

        if (
            self.client.state()
            == QLocalSocket.LocalSocketState.UnconnectedState
        ):
            self.client_buffer.clear()
            self.client.connectToServer(
                self.server_name
            )

    def send_command(self):
        if self.client_finished:
            return

        self.client.write(
            self.client_command
        )
        self.client.flush()

    def read_reply(self):
        if self.client_finished:
            return

        self.client_buffer.extend(
            bytes(self.client.readAll())
        )

        if b"\n" not in self.client_buffer:
            return

        reply = bytes(
            self.client_buffer
        ).split(b"\n", 1)[0].strip()

        if reply == b"OK":
            self.finish_secondary(True, "")

        elif reply == b"CLOSING":
            self.finish_secondary(
                False,
                "Приложение сейчас завершает работу.\n"
                "Повторите запуск через несколько секунд.",
            )

        else:
            self.finish_secondary(
                False,
                "Не удалось передать команду "
                "работающему приложению.",
            )

    def finish_secondary(self, success, message):
        if self.client_finished:
            return

        self.client_finished = True
        self.retry_timer.stop()

        if self.client is not None:
            self.client.abort()

        self.secondary_finished.emit(
            success,
            message,
        )

    def close(self):
        self.retry_timer.stop()

        if self.client is not None:
            self.client_finished = True
            self.client.abort()

        self.server.close()

        if self.is_primary:
            self.lock.unlock()
            self.is_primary = False