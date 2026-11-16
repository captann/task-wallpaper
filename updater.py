import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)

from PySide6.QtCore import (
    QObject,
    QThread,
    QTimer,
    Signal,
    Slot,
    Qt,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QWidget,
)

from app_version import (
    APP_VERSION,
    GITHUB_OWNER,
    GITHUB_REPOSITORY,
    INSTALLER_PREFIX,
    UPDATER_EXE,
)
from storage import DATA, STATE_FILE


UPDATE_ROOT = DATA.parent / "updates"
MAX_INSTALLER_SIZE = 512 * 1024 * 1024


def version_tuple(value):
    match = re.fullmatch(
        r"v?(\d+)\.(\d+)\.(\d+)",
        str(value).strip(),
    )

    if not match:
        raise ValueError(
            f"Неподдерживаемый номер версии: {value}"
        )

    return tuple(map(int, match.groups()))


def allowed_download_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    return (
        parsed.scheme == "https"
        and (
            host == "github.com"
            or host.endswith(".githubusercontent.com")
        )
    )


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        request,
        response,
        code,
        message,
        headers,
        new_url,
    ):
        if not allowed_download_url(new_url):
            raise RuntimeError(
                "Загрузка перенаправлена "
                "на неподдерживаемый адрес."
            )

        return super().redirect_request(
            request,
            response,
            code,
            message,
            headers,
            new_url,
        )


class UpdateJob(QThread):
    progress = Signal(int)

    def __init__(self, kind, payload=None, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.payload = payload
        self.result = None
        self.error = None

    def check_cancelled(self):
        if self.isInterruptionRequested():
            raise RuntimeError(
                "Операция обновления отменена."
            )

    def run(self):
        try:
            if self.kind == "check":
                self.result = self.check_release()
            else:
                self.result = self.download()

        except Exception as error:
            self.error = (
                f"{type(error).__name__}: {error}"
            )

    def check_release(self):
        api_url = (
            "https://api.github.com/repos/"
            f"{GITHUB_OWNER}/{GITHUB_REPOSITORY}"
            "/releases/latest"
        )

        request = Request(
            api_url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"TaskWallpaper/{APP_VERSION}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

        opener = build_opener()

        with opener.open(request, timeout=20) as response:
            raw = response.read(2 * 1024 * 1024 + 1)

        self.check_cancelled()

        if len(raw) > 2 * 1024 * 1024:
            raise RuntimeError(
                "Слишком большой ответ GitHub."
            )

        release = json.loads(raw.decode("utf-8"))

        if release.get("draft") or release.get("prerelease"):
            return None

        remote_version = version_tuple(
            release.get("tag_name", "")
        )

        if remote_version <= version_tuple(APP_VERSION):
            return None

        version = ".".join(map(str, remote_version))
        expected_name = (
            f"{INSTALLER_PREFIX}{version}.exe"
        )

        asset = next(
            (
                item
                for item in release.get("assets", [])
                if item.get("name") == expected_name
                and item.get("state") == "uploaded"
            ),
            None,
        )

        if asset is None:
            raise RuntimeError(
                "Новый релиз найден, но установщик "
                f"{expected_name} отсутствует."
            )

        digest = str(asset.get("digest") or "")

        if not re.fullmatch(
            r"sha256:[0-9a-fA-F]{64}",
            digest,
        ):
            raise RuntimeError(
                "У установщика нет подходящей SHA-256 "
                "в метаданных GitHub. "
                "Автоматическая загрузка заблокирована."
            )

        size = int(asset.get("size", 0))

        if not 0 < size <= MAX_INSTALLER_SIZE:
            raise RuntimeError(
                "Недопустимый размер установщика."
            )

        url = str(asset.get("browser_download_url", ""))
        parsed = urlparse(url)

        expected_prefix = (
            f"/{GITHUB_OWNER}/{GITHUB_REPOSITORY}"
            "/releases/download/"
        )

        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or not unquote(parsed.path).startswith(
                expected_prefix
            )
            or unquote(parsed.path).rsplit("/", 1)[-1]
            != expected_name
        ):
            raise RuntimeError(
                "Неожиданный адрес установщика."
            )

        return {
            "version": version,
            "notes": str(release.get("body") or ""),
            "name": expected_name,
            "url": url,
            "size": size,
            "sha256": digest.split(":", 1)[1].lower(),
        }

    def download(self):
        info = self.payload

        folder = UPDATE_ROOT / uuid.uuid4().hex
        folder.mkdir(parents=True, exist_ok=False)

        destination = folder / info["name"]
        partial = folder / (info["name"] + ".part")

        request = Request(
            info["url"],
            headers={
                "User-Agent": f"TaskWallpaper/{APP_VERSION}",
            },
        )

        opener = build_opener(
            SafeRedirectHandler()
        )

        digest = hashlib.sha256()
        received = 0
        previous_percent = -1

        try:
            with opener.open(
                request,
                timeout=20,
            ) as response:
                if not allowed_download_url(response.geturl()):
                    raise RuntimeError(
                        "Неподдерживаемый адрес загрузки."
                    )

                with partial.open("wb") as output:
                    while True:
                        self.check_cancelled()

                        chunk = response.read(256 * 1024)

                        if not chunk:
                            break

                        received += len(chunk)

                        if received > info["size"]:
                            raise RuntimeError(
                                "Файл больше заявленного размера."
                            )

                        output.write(chunk)
                        digest.update(chunk)

                        percent = int(
                            received * 100 / info["size"]
                        )

                        if percent != previous_percent:
                            previous_percent = percent
                            self.progress.emit(percent)

            self.check_cancelled()

            if received != info["size"]:
                raise RuntimeError(
                    "Установщик скачан не полностью."
                )

            if digest.hexdigest() != info["sha256"]:
                raise RuntimeError(
                    "SHA-256 не совпадает. "
                    "Установщик не будет запущен."
                )

            partial.replace(destination)

            return {
                "path": str(destination),
                "folder": str(folder),
                "sha256": info["sha256"],
                "version": info["version"],
            }

        except Exception:
            partial.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise


class UpdateManager(QObject):
    def __init__(self, window):
        super().__init__(window)

        self.window = window
        self.job = None
        self.manual_check = False
        self.release = None
        self.downloaded = None
        self.offered_version = None
        self.install_after_download = False

        self.helper_process = None
        self.helper_folder = None
        self.helper_deadline = 0

        state = window.state
        state.setdefault("updates_enabled", True)
        state.setdefault("updates_interval_hours", 24)
        state.setdefault("updates_auto_install", False)
        state.setdefault("updates_last_success", 0)

        self.next_attempt = 0

        self.timer = QTimer(self)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self.tick)

        self.helper_timer = QTimer(self)
        self.helper_timer.setInterval(200)
        self.helper_timer.timeout.connect(
            self.poll_helper
        )

    def create_panel(self):
        panel = QWidget()
        form = QFormLayout(panel)
        form.setContentsMargins(0, 8, 0, 0)

        form.addRow(
            QLabel(f"Обновления · версия {APP_VERSION}")
        )

        self.enabled_check = QCheckBox(
            "Проверять обновления автоматически"
        )
        self.enabled_check.setChecked(
            self.window.state["updates_enabled"]
        )

        self.interval_combo = QComboBox()
        self.interval_combo.addItem("Раз в час", 1)
        self.interval_combo.addItem("Раз в день", 24)
        self.interval_combo.setCurrentIndex(
            max(
                0,
                self.interval_combo.findData(
                    self.window.state[
                        "updates_interval_hours"
                    ]
                ),
            )
        )

        self.auto_check = QCheckBox(
            "Автоматически скачивать и устанавливать"
        )
        self.auto_check.setChecked(
            self.window.state["updates_auto_install"]
        )

        self.status = QLabel("")
        self.status.setWordWrap(True)

        check_button = QPushButton("Проверить сейчас")
        check_button.clicked.connect(
            lambda: self.check(manual=True)
        )

        self.action_button = QPushButton(
            "Скачать и обновить…"
        )
        self.action_button.setVisible(False)
        self.action_button.clicked.connect(
            self.action_clicked
        )

        form.addRow(self.enabled_check)
        form.addRow("Интервал:", self.interval_combo)
        form.addRow(self.auto_check)
        form.addRow(self.status)
        form.addRow(check_button)
        form.addRow(self.action_button)

        self.enabled_check.toggled.connect(
            self.settings_changed
        )
        self.interval_combo.currentIndexChanged.connect(
            self.settings_changed
        )
        self.auto_check.toggled.connect(
            self.settings_changed
        )

        return panel

    def start(self):
        self.timer.start()
        QTimer.singleShot(3000, self.tick)

    def settings_changed(self, *_):
        state = self.window.state
        state["updates_enabled"] = (
            self.enabled_check.isChecked()
        )
        state["updates_interval_hours"] = (
            self.interval_combo.currentData()
        )
        state["updates_auto_install"] = (
            self.auto_check.isChecked()
        )
        self.window.save_state()

    def is_closing(self):
        return getattr(
            self.window, "_closing", False
        )

    def safe_to_install(self):
        if self.is_closing():
            return False

        if QApplication.activeModalWidget() is not None:
            return False

        if QApplication.activePopupWidget() is not None:
            return False

        widget = QApplication.focusWidget()

        while widget is not None:
            if isinstance(
                widget,
                (
                    QLineEdit,
                    QTextEdit,
                    QPlainTextEdit,
                    QAbstractSpinBox,
                ),
            ):
                return False

            widget = widget.parentWidget()

        return True

    def tick(self):
        if self.is_closing():
            return

        if self.helper_process is not None:
            return

        if (
            self.downloaded is not None
            and self.install_after_download
            and self.safe_to_install()
        ):
            self.install()
            return

        if (
            self.release is not None
            and self.downloaded is None
            and self.job is None
            and self.offered_version
            != self.release["version"]
        ):
            self.offer()
            return

        if (
            self.job is not None
            or not self.window.state["updates_enabled"]
        ):
            return

        now = time.time()
        interval = (
            self.window.state["updates_interval_hours"]
            * 3600
        )

        if (
            now >= self.next_attempt
            and now - self.window.state[
                "updates_last_success"
            ] >= interval
        ):
            self.check(manual=False)

    def check(self, manual=False):
        if self.is_closing() or self.job is not None:
            return

        self.manual_check = manual
        self.status.setText(
            "Проверка обновлений…"
        )
        self.start_job("check")

    def start_job(self, kind, payload=None):
        self.job = UpdateJob(kind, payload, self)
        self.job.progress.connect(
            self.download_progress
        )
        self.job.finished.connect(
            self.job_finished
        )
        self.job.start()

    @Slot(int)
    def download_progress(self, percent):
        self.status.setText(
            f"Скачивание обновления: {percent}%"
        )

    @Slot()
    def job_finished(self):
        job = self.job

        if job is None:
            return

        kind = job.kind
        result = job.result
        error = job.error

        self.job = None
        job.deleteLater()

        if self.is_closing():
            return

        if error:
            self.next_attempt = time.time() + 3600
            self.status.setText(
                f"Обновление недоступно: {error}"
            )
            self.action_button.setEnabled(True)

            if kind == "download" or self.manual_check:
                QMessageBox.warning(
                    self.window,
                    "Обновление",
                    error,
                )
            return

        if kind == "check":
            self.window.state[
                "updates_last_success"
            ] = time.time()
            self.window.save_state()

            self.release = result

            if result is None:
                self.status.setText(
                    "Установлена актуальная версия."
                )
                self.action_button.setVisible(False)
                return

            self.status.setText(
                f"Доступна версия {result['version']}."
            )
            self.action_button.setText(
                "Скачать и обновить…"
            )
            self.action_button.setVisible(True)
            self.action_button.setEnabled(True)

            if self.manual_check:
                self.offer(manual=True)
            else:
                self.offer()

        else:
            self.downloaded = result
            self.status.setText(
                "Обновление скачано и проверено."
            )
            self.action_button.setText(
                "Установить обновление"
            )
            self.action_button.setEnabled(True)

            if (
                self.install_after_download
                and self.safe_to_install()
            ):
                self.install()
            else:
                self.status.setText(
                    "Обновление готово. Установка отложена "
                    "до завершения ввода или закрытия диалога."
                )

    def action_clicked(self):
        if self.downloaded is not None:
            self.install()
        else:
            self.offer(manual=True)

    def offer(self, manual=False):
        if self.release is None or self.job is not None:
            return

        if not manual and not self.safe_to_install():
            return

        if (
            not manual
            and self.window.state["updates_auto_install"]
        ):
            self.offered_version = self.release["version"]
            self.begin_download()
            return

        if not manual and not self.window.isVisible():
            self.offered_version = self.release["version"]

            if self.window.tray is not None:
                self.window.tray.showMessage(
                    "Task Wallpaper",
                    "Доступна версия "
                    f"{self.release['version']}.\n"
                    "Откройте приложение для обновления.",
                )
            return

        self.offered_version = self.release["version"]

        box = QMessageBox(self.window)
        box.setWindowTitle("Доступно обновление")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(
            f"Установлено: {APP_VERSION}\n"
            f"Доступно: {self.release['version']}\n\n"
            "Скачать обновление и установить его "
            "после завершения загрузки?"
        )
        box.setDetailedText(
            self.release["notes"][:20000]
        )

        always = QCheckBox(
            "В дальнейшем обновлять автоматически"
        )
        always.setChecked(
            self.window.state["updates_auto_install"]
        )
        box.setCheckBox(always)

        box.setStandardButtons(
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(
            QMessageBox.StandardButton.No
        )
        box.button(
            QMessageBox.StandardButton.Yes
        ).setText("Скачать и обновить")
        box.button(
            QMessageBox.StandardButton.No
        ).setText("Позже")

        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        self.auto_check.setChecked(
            always.isChecked()
        )
        self.settings_changed()

        self.begin_download()

    def begin_download(self):
        if not getattr(sys, "frozen", False):
            QMessageBox.information(
                self.window,
                "Запуск из исходников",
                "Проверка обновлений работает, "
                "но автоустановка доступна только "
                "в собранной EXE-версии.",
            )
            return

        helper = (
            Path(sys.executable).parent / UPDATER_EXE
        )

        if not helper.is_file():
            QMessageBox.warning(
                self.window,
                "Нет помощника обновления",
                f"Не найден файл:\n{helper}\n\n"
                "Пересоберите приложение вместе "
                "с помощником.",
            )
            return

        self.install_after_download = True
        self.action_button.setEnabled(False)
        self.start_job(
            "download",
            dict(self.release),
        )

    def install(self):
        if (
            self.downloaded is None
            or self.helper_process is not None
        ):
            return

        if not self.safe_to_install():
            self.status.setText(
                "Закройте диалог и завершите ввод текста, "
                "чтобы установить обновление."
            )
            return

        if not getattr(sys, "frozen", False):
            return

        try:
            self.window.save_state()
            folder = Path(
                self.downloaded["folder"]
            )
            # Убираем маркеры предыдущей попытки.
            (folder / "ready").unlink(missing_ok=True)
            (folder / "cancel").unlink(missing_ok=True)

            helper_source = (
                Path(sys.executable).parent / UPDATER_EXE
            )
            helper_copy = folder / UPDATER_EXE

            shutil.copy2(
                helper_source,
                helper_copy,
            )

            config = {
                "pid": os.getpid(),
                "installer": self.downloaded["path"],
                "sha256": self.downloaded["sha256"],
                "app_exe": str(
                    Path(sys.executable).resolve()
                ),
                "state_file": str(STATE_FILE),
            }

            config_path = folder / "update-job.json"
            config_path.write_text(
                json.dumps(
                    config,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            self.window.centralWidget().setEnabled(False)

            self.helper_process = subprocess.Popen(
                [
                    str(helper_copy),
                    str(config_path),
                ],
                cwd=str(folder),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(
                    subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                ),
                close_fds=True,
            )

            self.helper_folder = folder
            self.helper_deadline = time.monotonic() + 30

            self.status.setText(
                "Подготовка установки обновления…"
            )
            self.helper_timer.start()

        except Exception as error:
            self.window.centralWidget().setEnabled(True)
            QMessageBox.warning(
                self.window,
                "Не удалось начать обновление",
                str(error),
            )

    def poll_helper(self):
        if self.helper_process is None:
            self.helper_timer.stop()
            return

        ready = self.helper_folder / "ready"

        if ready.is_file():
            self.helper_timer.stop()
            self.window.quit_application()

            # Сохранение в quit_application могло завершиться
            # ошибкой. В этом случае не оставляем помощника
            # ждать будущего выхода пользователя.
            if not self.is_closing():
                self.cancel_helper()
            return

        if (
            self.helper_process.poll() is not None
            or time.monotonic() >= self.helper_deadline
        ):
            self.cancel_helper()
            QMessageBox.warning(
                self.window,
                "Обновление не начато",
                "Помощник обновления не подтвердил готовность. "
                "Приложение продолжает работать.",
            )

    def cancel_helper(self):
        self.helper_timer.stop()

        if self.helper_folder is not None:
            try:
                (self.helper_folder / "cancel").write_text(
                    "cancel",
                    encoding="ascii",
                )
            except OSError:
                pass

        self.helper_process = None
        self.helper_folder = None
        self.window.centralWidget().setEnabled(True)

    def shutdown(self):
        self.timer.stop()
        self.helper_timer.stop()

        if self.job is not None:
            self.job.requestInterruption()
            self.job.wait()