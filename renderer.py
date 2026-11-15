from datetime import datetime
from task_order import sorted_tasks
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
)


TEXT_FLAGS = (
    Qt.AlignmentFlag.AlignLeft
    | Qt.AlignmentFlag.AlignTop
)

WRAP_FLAGS = TEXT_FLAGS | Qt.TextFlag.TextWordWrap


def is_overdue(task, now=None):
    if task.get("done") or not task.get("deadline"):
        return False

    try:
        deadline = datetime.fromisoformat(task["deadline"])

        if deadline.tzinfo is not None:
            deadline = deadline.astimezone().replace(tzinfo=None)

        return deadline < (now or datetime.now())
    except (ValueError, TypeError):
        return False


def color_or(value, default):
    color = QColor(value)

    if not color.isValid():
        color = QColor(default)

    color.setAlpha(255)
    return color


def draw_sticker(painter, sticker, width, height):
    nx, ny, nw, nh = sticker["note_rect"]

    note_width = max(1, min(width, round(nw * width)))
    note_height = max(1, min(height, round(nh * height)))

    x = max(0, min(round(nx * width), width - note_width))
    y = max(0, min(round(ny * height), height - note_height))

    note = QRect(x, y, note_width, note_height)

    scale = max(0.5, height / 1080)
    padding = max(5, round(10 * scale))
    radius = max(3, round(6 * scale))

    normal = color_or(sticker["text_color"], "#202020")
    overdue = color_or(sticker["overdue_color"], "#c62828")
    background = color_or(sticker["note_color"], "#fff4bf")

    painter.save()

    try:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(note, radius, radius)

        clip = QPainterPath()
        clip.addRoundedRect(
            float(x),
            float(y),
            float(note_width),
            float(note_height),
            float(radius),
            float(radius),
        )
        painter.setClipPath(clip)

        font = QFont(sticker["font_family"])
        font.setPixelSize(
            max(8, round(sticker["font_size"] * scale))
        )

        metrics = QFontMetrics(font)
        text_x = x + padding
        content_width = max(1, note_width - 2 * padding)
        cursor_y = y + padding
        bottom = y + note_height - padding

        if sticker["show_header"]:
            title_font = QFont(sticker["font_family"])
            title_font.setPixelSize(
                max(10, round(20 * scale))
            )
            title_font.setBold(True)

            painter.setFont(title_font)
            painter.setPen(normal)

            title_metrics = QFontMetrics(title_font)
            title_height = (
                title_metrics.height() + round(4 * scale)
            )

            painter.drawText(
                QRect(
                    text_x,
                    cursor_y,
                    content_width,
                    title_height,
                ),
                TEXT_FLAGS,
                title_metrics.elidedText(
                    sticker.get(
                        "note_title", sticker["name"]
                    ),
                    Qt.TextElideMode.ElideRight,
                    content_width,
                ),
            )

            cursor_y += title_height

        painter.setFont(font)
        painter.setPen(normal)

        active = sorted_tasks(
            sticker,
            include_done=False,
        )

        # Без заголовков и без дедлайнов используем всю ширину.
        use_deadline = (
            sticker["show_columns"]
            or any(task.get("deadline") for task in active)
        )

        gap = round(12 * scale)

        if use_deadline:
            desired = (
                metrics.horizontalAdvance("00.00.0000 00:00")
                + round(4 * scale)
            )
            deadline_width = min(
                desired,
                max(1, int(content_width * 0.48)),
            )
            deadline_x = (
                text_x + content_width - deadline_width
            )
            text_width = max(
                1, deadline_x - gap - text_x
            )
        else:
            deadline_width = 0
            deadline_x = text_x + content_width
            text_width = content_width

        if sticker["show_columns"]:
            header_height = metrics.height()

            painter.drawText(
                QRect(
                    text_x, cursor_y,
                    text_width, header_height,
                ),
                TEXT_FLAGS,
                "Задача",
            )
            painter.drawText(
                QRect(
                    deadline_x, cursor_y,
                    deadline_width, header_height,
                ),
                TEXT_FLAGS,
                "Дедлайн",
            )

            cursor_y += header_height + round(4 * scale)

            separator = QColor(normal)
            separator.setAlpha(55)
            painter.setPen(separator)
            painter.drawLine(
                text_x,
                cursor_y,
                text_x + content_width,
                cursor_y,
            )

            painter.setPen(normal)
            cursor_y += round(6 * scale)

        if not active:
            if bottom > cursor_y:
                painter.drawText(
                    QRect(
                        text_x, cursor_y,
                        content_width, bottom - cursor_y,
                    ),
                    WRAP_FLAGS,
                    "Нет незавершённых задач.",
                )
            return

        now = datetime.now()
        footer_height = metrics.height()
        row_gap = round(8 * scale)

        for index, task in enumerate(active):
            bounds = metrics.boundingRect(
                QRect(0, 0, text_width, 100000),
                int(WRAP_FLAGS),
                task["title"],
            )
            text_height = max(
                metrics.height(), bounds.height()
            )
            row_height = text_height + row_gap

            remaining = len(active) - index
            reserve = (
                footer_height + row_gap
                if remaining > 1
                else 0
            )

            if cursor_y + text_height > bottom - reserve:
                painter.setPen(normal)

                if bottom - cursor_y >= footer_height:
                    painter.drawText(
                        QRect(
                            text_x, cursor_y,
                            content_width, footer_height,
                        ),
                        TEXT_FLAGS,
                        f"Ещё задач: {remaining}",
                    )
                break

            painter.setPen(
                overdue if is_overdue(task, now) else normal
            )

            painter.drawText(
                QRect(
                    text_x, cursor_y,
                    text_width, text_height,
                ),
                WRAP_FLAGS,
                task["title"],
            )

            if use_deadline:
                value = task.get("deadline")
                deadline_text = "—"

                if value:
                    try:
                        deadline_text = (
                            datetime.fromisoformat(value)
                            .strftime("%d.%m.%Y %H:%M")
                        )
                    except (ValueError, TypeError):
                        deadline_text = str(value)

                painter.drawText(
                    QRect(
                        deadline_x, cursor_y,
                        deadline_width, text_height,
                    ),
                    TEXT_FLAGS,
                    metrics.elidedText(
                        deadline_text,
                        Qt.TextElideMode.ElideRight,
                        deadline_width,
                    ),
                )

            cursor_y += row_height

    finally:
        painter.restore()


def render_wallpaper(
    background_path,
    stickers,
    width,
    height,
    output_size=None,
    background_image=None,
):
    # width/height — логическое разрешение рабочего стола.
    # output_size — реальное разрешение создаваемого изображения.
    if output_size is None:
        output_width, output_height = width, height
    else:
        output_width, output_height = output_size

    output_width = max(1, int(output_width))
    output_height = max(1, int(output_height))

    image = QImage(
        output_width,
        output_height,
        QImage.Format.Format_RGB32,
    )

    if image.isNull():
        raise MemoryError(
            "Не удалось выделить память для изображения."
        )

    image.fill(QColor("#263343"))

    painter = QPainter(image)

    if not painter.isActive():
        raise RuntimeError(
            "Не удалось начать отрисовку изображения."
        )

    try:
        painter.setRenderHint(
            QPainter.RenderHint.Antialiasing
        )
        painter.setRenderHint(
            QPainter.RenderHint.TextAntialiasing
        )
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform
        )

        # Сохраняем одинаковую геометрию и размеры текста
        # в превью и полноразмерных обоях.
        painter.scale(
            output_width / width,
            output_height / height,
        )

        if (
            background_image is not None
            and not background_image.isNull()
        ):
            painter.drawImage(
                QRect(0, 0, width, height),
                background_image,
            )

        elif background_path:
            background = QImage(background_path)

            if background.isNull():
                raise OSError(
                    f"Не удалось открыть фон:\n"
                    f"{background_path}"
                )

            background = background.scaled(
                width,
                height,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )

            painter.drawImage(
                (width - background.width()) // 2,
                (height - background.height()) // 2,
                background,
            )

        for sticker in stickers:
            if sticker.get("visible", True):
                draw_sticker(
                    painter,
                    sticker,
                    width,
                    height,
                )

    finally:
        painter.end()

    return image