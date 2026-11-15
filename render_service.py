from pathlib import Path
from time import perf_counter

from PySide6.QtCore import (
    QObject,
    QSize,
    QThread,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QImageReader

from renderer import render_wallpaper


def remove_temporary_files(result):
    if not isinstance(result, dict):
        return

    paths = []

    if result.get("wallpaper_temp"):
        paths.append(result["wallpaper_temp"])

    for item in result.get("exports", []):
        paths.append(item["temporary"])

    for value in paths:
        try:
            Path(value).unlink(missing_ok=True)
        except OSError:
            pass


class RenderCancelled(Exception):
    pass


class RenderWorker(QObject):
    finished = Signal(object)

    def __init__(self):
        super().__init__()
        self.background_key = None
        self.background_image = None

    def check_interruption(self):
        if QThread.currentThread().isInterruptionRequested():
            raise RenderCancelled()

    def load_background(self, path, width, height):
        if not path:
            self.background_key = None
            self.background_image = None
            return None

        source = Path(path)
        stat = source.stat()

        key = (
            str(source.resolve()),
            stat.st_mtime_ns,
            stat.st_size,
            width,
            height,
        )

        if (
            key == self.background_key
            and self.background_image is not None
        ):
            return self.background_image

        # Освобождаем предыдущий кэш до загрузки нового.
        self.background_key = None
        self.background_image = None

        self.check_interruption()

        reader = QImageReader(str(source))
        reader.setAutoTransform(True)

        source_size = reader.size()

        if source_size.isValid():
            decode_size = source_size.scaled(
                QSize(width, height),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            )

            # Для больших исходников просим декодер
            # сразу уменьшить изображение, если он это умеет.
            if (
                decode_size.width() < source_size.width()
                and decode_size.height() < source_size.height()
            ):
                reader.setScaledSize(decode_size)

        image = reader.read()

        if image.isNull():
            raise OSError(
                f"Не удалось прочитать фон:\n{source}\n"
                f"{reader.errorString()}"
            )

        self.check_interruption()

        image = image.scaled(
            width,
            height,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )

        left = max(0, (image.width() - width) // 2)
        top = max(0, (image.height() - height) // 2)

        if image.width() != width or image.height() != height:
            image = image.copy(
                left,
                top,
                width,
                height,
            )

        self.background_key = key
        self.background_image = image

        return image

    @Slot(object)
    def render(self, job):
        started = perf_counter()

        result = {
            "revision": job["revision"],
            "wallpaper_temp": None,
            "exports": [],
            "image": None,
            "error": None,
            "cancelled": False,
        }

        try:
            self.check_interruption()

            width, height = job["screen_size"]
            preview_width, preview_height = job["preview_size"]

            full_size = (
                job["apply_wallpaper"]
                or bool(job["export_paths"])
            )

            if full_size:
                output_width, output_height = width, height
            else:
                output_width = preview_width
                output_height = preview_height

            background = self.load_background(
                job["background"],
                output_width,
                output_height,
            )

            self.check_interruption()

            image = render_wallpaper(
                background_path="",
                stickers=job["stickers"],
                width=width,
                height=height,
                output_size=(
                    output_width,
                    output_height,
                ),
                background_image=background,
            )

            self.check_interruption()

            if job["apply_wallpaper"]:
                temporary = (
                    Path(job["data_dir"])
                    / f".render-{job['revision']}.bmp"
                )

                result["wallpaper_temp"] = str(temporary)

                if not image.save(str(temporary), "BMP"):
                    raise OSError(
                        "Не удалось сохранить временные обои."
                    )

            for index, destination in enumerate(
                job["export_paths"]
            ):
                self.check_interruption()

                target = Path(destination)

                # Временный файл рядом с итоговым:
                # последующая замена не требует копировать
                # всё изображение между дисками.
                temporary = target.with_name(
                    f".{target.name}."
                    f"render-{job['revision']}-{index}.tmp.png"
                )

                result["exports"].append(
                    {
                        "temporary": str(temporary),
                        "destination": str(target),
                    }
                )

                if not image.save(str(temporary), "PNG"):
                    raise OSError(
                        f"Не удалось сохранить изображение:\n"
                        f"{target}"
                    )

            self.check_interruption()

            # В интерфейс передаём только уменьшенное превью.
            if (
                image.width() != preview_width
                or image.height() != preview_height
            ):
                preview = image.scaled(
                    preview_width,
                    preview_height,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            else:
                preview = image

            result["image"] = preview

        except RenderCancelled:
            result["cancelled"] = True
            remove_temporary_files(result)
            result["wallpaper_temp"] = None
            result["exports"] = []

        except Exception as error:
            remove_temporary_files(result)
            result["wallpaper_temp"] = None
            result["exports"] = []
            result["error"] = (
                f"{type(error).__name__}: {error}"
            )

        result["elapsed"] = perf_counter() - started
        self.finished.emit(result)


class RenderService(QObject):
    dispatch = Signal(object)
    ready = Signal(object)
    stopped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.thread = QThread(self)
        self.worker = RenderWorker()
        self.worker.moveToThread(self.thread)

        self.dispatch.connect(self.worker.render)
        self.worker.finished.connect(self.on_finished)

        self.thread.finished.connect(
            self.worker.deleteLater
        )
        self.thread.finished.connect(
            self.stopped.emit
        )

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.pump)

        self.revision = 0
        self.pending = None
        self.running = False
        self.stopping = False

        self.thread.start()

    def submit(self, job, delay_ms=80):
        if self.stopping:
            return

        self.revision += 1

        # Предыдущий ожидающий запрос заменяется,
        # а не добавляется в неограниченную очередь.
        self.pending = dict(job)
        self.pending["revision"] = self.revision

        self.timer.start(delay_ms)

    def has_work(self):
        return (
            self.running
            or self.pending is not None
        )

    @Slot()
    def pump(self):
        if (
            self.stopping
            or self.running
            or self.pending is None
        ):
            return

        job = self.pending
        self.pending = None
        self.running = True

        self.dispatch.emit(job)

    @Slot(object)
    def on_finished(self, result):
        self.running = False

        if (
            self.stopping
            or result["revision"] != self.revision
        ):
            remove_temporary_files(result)
        else:
            self.ready.emit(result)

        if (
            not self.stopping
            and self.pending is not None
            and not self.timer.isActive()
        ):
            self.pump()

    def stop(self):
        if self.stopping:
            return

        self.stopping = True
        self.timer.stop()
        self.pending = None

        self.thread.requestInterruption()
        self.thread.quit()

    def shutdown_blocking(self):
        self.stop()
        self.thread.wait()