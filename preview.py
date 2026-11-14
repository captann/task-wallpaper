from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class WallpaperPreview(QWidget):
    sticker_selected = Signal(str)
    geometry_changed = Signal(str, list)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)

        self.image = None
        self.stickers = []
        self.active_id = None

        self.mode = None
        self.drag_id = None
        self.start_position = None
        self.start_rect = None

    def set_content(self, image, stickers, active_id):
        self.image = image
        self.active_id = active_id

        self.stickers = [
            {
                "id": sticker["id"],
                "note_rect": list(sticker["note_rect"]),
                "visible": sticker.get("visible", True),
            }
            for sticker in stickers
        ]

        self.update()

    def image_rect(self):
        if self.image is None:
            return QRectF()

        scale = min(
            self.width() / self.image.width(),
            self.height() / self.image.height(),
        )
        width = self.image.width() * scale
        height = self.image.height() * scale

        return QRectF(
            (self.width() - width) / 2,
            (self.height() - height) / 2,
            width,
            height,
        )

    def active_sticker(self):
        return next(
            (
                sticker
                for sticker in self.stickers
                if sticker["id"] == self.active_id
                and sticker["visible"]
            ),
            None,
        )

    def block_rect(self, sticker):
        area = self.image_rect()
        x, y, width, height = sticker["note_rect"]

        return QRectF(
            area.left() + x * area.width(),
            area.top() + y * area.height(),
            width * area.width(),
            height * area.height(),
        )

    def handle_rect(self, sticker):
        block = self.block_rect(sticker)
        return QRectF(
            block.right() - 12,
            block.bottom() - 12,
            12,
            12,
        )

    def hit_test(self, position):
        for sticker in reversed(self.stickers):
            if (
                sticker["visible"]
                and self.block_rect(sticker).contains(position)
            ):
                return sticker

        return None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(
            self.rect(), self.palette().window()
        )

        if self.image is None:
            return

        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform
        )
        painter.drawImage(self.image_rect(), self.image)

        active = self.active_sticker()

        if active is not None:
            painter.setPen(
                QPen(QColor("#398cff"), 2)
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.block_rect(active))

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#398cff"))
            painter.drawRect(self.handle_rect(active))

    def mousePressEvent(self, event):
        if (
            event.button() != Qt.MouseButton.LeftButton
            or self.image is None
        ):
            return

        position = event.position()
        active = self.active_sticker()

        if (
            active is not None
            and self.handle_rect(active).contains(position)
        ):
            sticker = active
            mode = "resize"
        else:
            sticker = self.hit_test(position)
            mode = "move"

        if sticker is None:
            return

        sticker_id = sticker["id"]
        starting_rect = list(sticker["note_rect"])

        self.sticker_selected.emit(sticker_id)

        self.mode = mode
        self.drag_id = sticker_id
        self.start_position = position
        self.start_rect = starting_rect

    def mouseMoveEvent(self, event):
        if self.image is None:
            return

        if self.mode is None:
            active = self.active_sticker()

            if (
                active is not None
                and self.handle_rect(active).contains(
                    event.position()
                )
            ):
                self.setCursor(
                    Qt.CursorShape.SizeFDiagCursor
                )
            elif self.hit_test(event.position()) is not None:
                self.setCursor(
                    Qt.CursorShape.SizeAllCursor
                )
            else:
                self.setCursor(
                    Qt.CursorShape.ArrowCursor
                )
            return

        area = self.image_rect()

        if area.width() <= 0 or area.height() <= 0:
            return

        dx = (
            event.position().x() - self.start_position.x()
        ) / area.width()
        dy = (
            event.position().y() - self.start_position.y()
        ) / area.height()

        x, y, width, height = self.start_rect

        if self.mode == "move":
            x = max(0.0, min(1.0 - width, x + dx))
            y = max(0.0, min(1.0 - height, y + dy))
        else:
            min_width = min(0.15, 1.0 - x)
            min_height = min(0.10, 1.0 - y)

            width = max(
                min_width,
                min(1.0 - x, width + dx),
            )
            height = max(
                min_height,
                min(1.0 - y, height + dy),
            )

        self.geometry_changed.emit(
            self.drag_id,
            [x, y, width, height],
        )

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.mode = None
            self.drag_id = None
            self.start_position = None
            self.start_rect = None