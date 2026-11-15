import json
from PySide6.QtCore import QSize

from render_service import (
    RenderService,
    remove_temporary_files,
)
from task_order import (
    SORT_OPTIONS,
    parse_local_datetime,
    sorted_tasks,
)
from PySide6.QtGui import QPalette
import shutil
import sys
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtCore import (
    QDateTime,
    QLockFile,
    QSignalBlocker,
    QTimer,
    Qt,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QImage,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStyle,
    QSystemTrayIcon,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from preview import WallpaperPreview
from renderer import is_overdue
from storage import (
    BACKGROUNDS,
    DATA,
    STYLE_DEFAULTS,
    STYLE_KEYS,
    load_state,
    make_sticker,
    normalize_sticker,
    prepare_data,
    save_state,
    sticker_layout,
)
from wallpaper import set_wallpaper


ROOT = Path(__file__).resolve().parent
ICON_FILE = ROOT / "assets" / "app.ico"


class TaskDialog(QDialog):
    def __init__(self, parent=None, task=None):
        super().__init__(parent)

        self.setWindowTitle("Задача")
        self.resize(460, 180)
        task = task or {}

        self.title_input = QLineEdit(
            task.get("title", "")
        )

        self.has_deadline = QCheckBox("Есть дедлайн")
        self.deadline_input = QDateTimeEdit()
        self.deadline_input.setCalendarPopup(True)
        self.deadline_input.setDisplayFormat(
            "dd.MM.yyyy HH:mm"
        )
        self.deadline_input.setDateTime(
            QDateTime.currentDateTime()
        )

        if task.get("deadline"):
            value = datetime.fromisoformat(
                task["deadline"]
            )
            self.deadline_input.setDateTime(
                QDateTime(
                    value.year,
                    value.month,
                    value.day,
                    value.hour,
                    value.minute,
                    0,
                )
            )
            self.has_deadline.setChecked(True)

        self.deadline_input.setEnabled(
            self.has_deadline.isChecked()
        )
        self.has_deadline.toggled.connect(
            self.deadline_input.setEnabled
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)

        form = QFormLayout(self)
        form.addRow("Описание:", self.title_input)
        form.addRow(self.has_deadline)
        form.addRow("Дедлайн:", self.deadline_input)
        form.addRow(buttons)

    def validate(self):
        if not self.title_input.text().strip():
            QMessageBox.warning(
                self, "Задача", "Введите описание."
            )
            return

        self.accept()

    def values(self):
        deadline = None

        if self.has_deadline.isChecked():
            deadline = (
                self.deadline_input.dateTime()
                .toPython()
                .replace(
                    second=0,
                    microsecond=0,
                    tzinfo=None,
                )
                .isoformat(timespec="minutes")
            )

        return {
            "title": self.title_input.text().strip(),
            "deadline": deadline,
        }


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.state = load_state()
        self.state.setdefault("notifications_enabled", True)
        self.state.setdefault("reminder_minutes", 30)
        self.state.setdefault("notify_at_deadline", True)
        self.image = None
        self.tray = None
        self.last_signature = None
        self.syncing = False
        self._closing = False
        self._apply_requested = False
        self._apply_silent = True
        self._export_paths = []

        self.render_service = RenderService(self)
        self.render_service.ready.connect(
            self.render_finished
        )
        self.render_service.stopped.connect(
            self.render_thread_stopped
        )

        self.render_notice_timer = QTimer(self)
        self.render_notice_timer.setSingleShot(True)
        self.render_notice_timer.timeout.connect(
            self.show_render_notice
        )

        self.render_status = QLabel("")
        self.statusBar().addWidget(
            self.render_status
        )

        self.geometry_save_timer = QTimer(self)
        self.geometry_save_timer.setSingleShot(True)
        self.geometry_save_timer.timeout.connect(
            self.finish_geometry_change
        )

        self.setWindowTitle(
            "Task Wallpaper"
        )
        self.resize(1350, 850)

        self.build_ui()


        QApplication.instance().aboutToQuit.connect(
            self.render_service.shutdown_blocking
        )

        self.build_tray()

        self.populate_backgrounds()
        self.populate_presets()
        self.rebuild_tabs()
        self.sync_controls()
        self.regenerate()
        self.save_state()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_deadlines)
        self.timer.start(15_000)
        QTimer.singleShot(
            1500,
            self.check_deadlines,
        )

    def queue_render(self, delay_ms=80):
        if self._closing:
            return

        screen = QApplication.primaryScreen()
        size = screen.size()
        ratio = screen.devicePixelRatio()

        width = max(1, round(size.width() * ratio))
        height = max(1, round(size.height() * ratio))

        # Превью ограничено по размеру, даже если монитор 4K.
        preview_limit = QSize(
            max(
                320,
                min(
                    1600,
                    round(self.preview.width() * ratio),
                ),
            ),
            max(
                240,
                min(
                    1000,
                    round(self.preview.height() * ratio),
                ),
            ),
        )

        preview_size = QSize(
            width,
            height,
        ).scaled(
            preview_limit,
            Qt.AspectRatioMode.KeepAspectRatio,
        )

        # Не увеличиваем маленькое исходное разрешение.
        preview_width = min(
            width, preview_size.width()
        )
        preview_height = min(
            height, preview_size.height()
        )

        # Передаём отдельный снимок данных.
        # Не копируем служебную историю уведомлений.
        stickers = []

        for source in self.state["stickers"]:
            sticker = {
                key: deepcopy(value)
                for key, value in source.items()
                if key != "tasks"
            }

            sticker["tasks"] = [
                {
                    "title": task.get("title", ""),
                    "deadline": task.get("deadline"),
                    "done": task.get("done", False),
                    "created": task.get("created", ""),
                }
                for task in source["tasks"]
            ]

            stickers.append(sticker)

        background = self.state["active_background"]

        job = {
            "background": (
                str(DATA / background)
                if background
                else ""
            ),
            "stickers": stickers,
            "screen_size": (width, height),
            "preview_size": (
                preview_width,
                preview_height,
            ),
            "apply_wallpaper": self._apply_requested,
            "export_paths": list(self._export_paths),
            "data_dir": str(DATA),
        }

        self.render_service.submit(
            job,
            delay_ms=delay_ms,
        )

        if (
            not self.render_notice_timer.isActive()
            and not self.render_status.text()
        ):
            self.render_notice_timer.start(1000)

    def show_render_notice(self):
        if (
            not self._closing
            and self.render_service.has_work()
        ):
            self.render_status.setText(
                "Обновление изображения…"
            )

    def render_finished(self, result):
        if self._closing:
            remove_temporary_files(result)
            return

        self.render_notice_timer.stop()
        self.render_status.clear()

        apply_requested = self._apply_requested
        apply_silent = self._apply_silent
        had_exports = bool(self._export_paths)

        self._apply_requested = False
        self._apply_silent = True
        self._export_paths = []

        if result.get("cancelled"):
            remove_temporary_files(result)
            return

        if result.get("error"):
            remove_temporary_files(result)

            message = result["error"]

            if had_exports or (
                apply_requested and not apply_silent
            ):
                QMessageBox.warning(
                    self,
                    "Ошибка формирования изображения",
                    message,
                )
            else:
                self.statusBar().showMessage(
                    message,
                    15000,
                )
            return

        image = result.get("image")

        if image is not None and not image.isNull():
            self.image = image
            self.update_preview()

        errors = []
        exported_count = 0
        wallpaper_applied = False

        try:
            temporary = result.get(
                "wallpaper_temp"
            )

            if temporary:
                slot = 1 - int(
                    self.state.get(
                        "wallpaper_slot", 0
                    )
                )

                destination = (
                    DATA / f"wallpaper_{slot}.bmp"
                )

                try:
                    Path(temporary).replace(
                        destination
                    )

                    # Применяем только принятый актуальный результат.
                    set_wallpaper(destination)

                    self.state["wallpaper_slot"] = slot
                    wallpaper_applied = True

                except Exception as error:
                    errors.append(
                        f"Не удалось применить обои:\n{error}"
                    )

            for item in result.get("exports", []):
                try:
                    Path(item["temporary"]).replace(
                        Path(item["destination"])
                    )
                    exported_count += 1

                except OSError as error:
                    errors.append(
                        "Не удалось завершить экспорт:\n"
                        f"{item['destination']}\n{error}"
                    )

        finally:
            remove_temporary_files(result)

        if wallpaper_applied:
            self.save_state()

        if errors:
            message = "\n\n".join(errors)

            if had_exports or not apply_silent:
                QMessageBox.warning(
                    self,
                    "Ошибка",
                    message,
                )
            else:
                self.statusBar().showMessage(
                    message,
                    15000,
                )

        elif exported_count:
            self.statusBar().showMessage(
                f"Изображений сохранено: {exported_count}",
                5000,
            )

        elif wallpaper_applied:
            self.statusBar().showMessage(
                "Обои обновлены",
                5000,
            )

    def render_thread_stopped(self):
        if self._closing:
            QApplication.instance().quit()

    def active_sticker(self):
        return next(
            (
                sticker
                for sticker in self.state["stickers"]
                if sticker["id"]
                == self.state["active_sticker_id"]
            ),
            self.state["stickers"][0],
        )

    def find_sticker(self, sticker_id):
        return next(
            (
                sticker
                for sticker in self.state["stickers"]
                if sticker["id"] == sticker_id
            ),
            None,
        )

    def save_state(self):
        save_state(self.state)

    def add_button(self, layout, title, callback):
        button = QPushButton(title)
        button.clicked.connect(
            lambda checked=False: callback()
        )
        layout.addWidget(button)
        return button

    def sort_settings_changed(self, *_):
        if self.syncing:
            return

        sticker = self.active_sticker()

        sticker["sort_mode"] = (
            self.sort_combo.currentData()
            or "deadline_asc"
        )
        sticker["done_last"] = (
            self.done_last_check.isChecked()
        )

        self.changed()

    def notification_settings_changed(self, *_):
        enabled = self.notifications_check.isChecked()

        self.state["notifications_enabled"] = enabled
        self.state["reminder_minutes"] = (
            self.reminder_minutes_spin.value()
        )
        self.state["notify_at_deadline"] = (
            self.notify_at_deadline_check.isChecked()
        )

        self.reminder_minutes_spin.setEnabled(enabled)
        self.notify_at_deadline_check.setEnabled(enabled)

        self.save_state()

    def notifications_available(self):
        return (
            self.tray is not None
            and self.tray.isVisible()
            and QSystemTrayIcon.supportsMessages()
        )

    def test_notification(self):
        if not self.notifications_available():
            QMessageBox.information(
                self,
                "Уведомления недоступны",
                "Не удалось использовать уведомления трея. "
                "Проверьте наличие значка приложения "
                "в области уведомлений.",
            )
            return

        self.tray.showMessage(
            "Task Wallpaper",
            "Тестовое уведомление.\n"
            "Так будут выглядеть напоминания о дедлайнах.",
            QSystemTrayIcon.MessageIcon.Information,
            10000,
        )

    def check_reminders(self):
        if not self.state.get(
            "notifications_enabled", True
        ):
            return

        if not self.notifications_available():
            return

        now = datetime.now()
        minutes = int(
            self.state.get("reminder_minutes", 30)
        )
        notify_at_deadline = self.state.get(
            "notify_at_deadline", True
        )

        pending = []

        for sticker in self.state["stickers"]:
            for task in sticker["tasks"]:
                if task.get("done"):
                    continue

                raw_deadline = task.get("deadline")
                deadline = parse_local_datetime(
                    raw_deadline
                )

                if deadline is None:
                    continue

                seconds_left = (
                    deadline - now
                ).total_seconds()

                # Изменение срока, интервала или ID
                # создаёт новый цикл напоминаний.
                notification_key = (
                    f"{sticker['id']}|"
                    f"{task['id']}|"
                    f"{raw_deadline}|"
                    f"{minutes}"
                )

                previous = task.get(
                    "_notification_state", {}
                )

                if (
                    not isinstance(previous, dict)
                    or previous.get("key")
                    != notification_key
                ):
                    previous = {
                        "key": notification_key
                    }

                phase = None
                phase_label = None

                if (
                    0 < seconds_left <= minutes * 60
                    and not previous.get("before")
                ):
                    phase = "before"
                    phase_label = "Скоро дедлайн"

                elif (
                    notify_at_deadline
                    and -600 <= seconds_left <= 0
                    and not previous.get("due")
                ):
                    phase = "due"
                    phase_label = "Дедлайн наступил"

                if phase is None:
                    continue

                pending.append(
                    {
                        "task": task,
                        "state": dict(previous),
                        "phase": phase,
                        "label": phase_label,
                        "sticker_name": sticker["name"],
                        "deadline": deadline,
                    }
                )

        if not pending:
            return

        # Одно сводное уведомление вместо серии,
        # которая могла бы перекрывать сама себя.
        pending.sort(
            key=lambda item: (
                item["phase"] != "due",
                item["deadline"],
            )
        )

        lines = []

        for item in pending[:5]:
            title = (
                item["task"]["title"]
                .replace("\n", " ")
                .strip()
            )

            if len(title) > 90:
                title = title[:87] + "..."

            lines.append(
                f"{item['label']} — "
                f"{item['sticker_name']}: {title}\n"
                f"Срок: "
                f"{item['deadline']:%d.%m.%Y %H:%M}"
            )

        if len(pending) > 5:
            lines.append(
                f"Ещё задач: {len(pending) - 5}. "
                "Откройте приложение."
            )

        has_due = any(
            item["phase"] == "due"
            for item in pending
        )

        icon = (
            QSystemTrayIcon.MessageIcon.Warning
            if has_due
            else QSystemTrayIcon.MessageIcon.Information
        )

        self.tray.showMessage(
            "Task Wallpaper — дедлайны",
            "\n\n".join(lines),
            icon,
            10000,
        )

        # Запоминаем отправку, чтобы не повторять
        # одно сообщение каждые 15 секунд.
        for item in pending:
            record = item["state"]
            record[item["phase"]] = True
            item["task"]["_notification_state"] = record

        self.save_state()

    def build_ui(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        self.setCentralWidget(central)

        tabs_row = QHBoxLayout()
        tabs_row.setSpacing(8)

        # Кнопка находится вне прокручиваемой полосы вкладок.
        plus = self.add_button(
            tabs_row,
            "+",
            self.add_sticker,
        )
        plus.setFixedWidth(84)
        plus.setMinimumHeight(32)
        plus.setToolTip("Создать новый список")

        plus_font = plus.font()
        plus_font.setPointSize(16)
        plus.setFont(plus_font)

        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setUsesScrollButtons(True)

        # Вкладки можно переставлять мышью.
        self.tabs.setMovable(True)

        self.tabs.setElideMode(
            Qt.TextElideMode.ElideRight
        )
        self.tabs.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.tabs.setToolTip(
            "Перетаскивай вкладки для изменения слоёв: "
            "слева — задний план, справа — передний."
        )

        self.tabs.currentChanged.connect(
            self.tab_changed
        )
        self.tabs.tabMoved.connect(
            self.tabs_reordered
        )
        self.tabs.customContextMenuRequested.connect(
            self.tab_menu
        )
        self.tabs.tabBarDoubleClicked.connect(
            self.rename_tab_by_double_click
        )

        tabs_row.addWidget(self.tabs, 1)
        outer.addLayout(tabs_row)

        splitter = QSplitter(
            Qt.Orientation.Horizontal
        )
        outer.addWidget(splitter, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(360)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        scroll.setWidget(left)
        splitter.addWidget(scroll)

        left_layout.addWidget(
            QLabel("Общий фон рабочего стола")
        )

        self.background_combo = QComboBox()
        left_layout.addWidget(self.background_combo)
        background_buttons = QHBoxLayout()

        self.add_button(
            background_buttons,
            "Добавить фон…",
            self.add_background,
        )

        self.add_button(
            background_buttons,
            "Удалить фон",
            self.remove_background,
        )

        left_layout.addLayout(background_buttons)

        left_layout.addWidget(
            QLabel("Задачи выбранного стикера")
        )
        sort_form = QFormLayout()

        self.sort_combo = QComboBox()

        for label, value in SORT_OPTIONS:
            self.sort_combo.addItem(label, value)

        self.done_last_check = QCheckBox(
            "Выполненные задачи внизу"
        )

        sort_form.addRow(
            "Сортировка:",
            self.sort_combo,
        )
        sort_form.addRow(self.done_last_check)

        left_layout.addLayout(sort_form)

        self.sort_combo.currentIndexChanged.connect(
            self.sort_settings_changed
        )
        self.done_last_check.toggled.connect(
            self.sort_settings_changed
        )
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(
            ["Готово", "Задача", "Дедлайн"]
        )
        self.table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.setColumnWidth(0, 65)
        self.table.setColumnWidth(2, 145)
        self.table.setMinimumHeight(190)
        self.table.itemChanged.connect(
            self.task_checked
        )
        self.table.cellDoubleClicked.connect(
            lambda *_: self.edit_task()
        )
        self.delete_task_shortcut = QShortcut(
            QKeySequence("Del"),
            self.table,
        )

        # Работает только при фокусе на самой таблице,
        # а не во всём окне и не в дочерних редакторах.
        self.delete_task_shortcut.setContext(
            Qt.ShortcutContext.WidgetShortcut
        )

        # Удержание Del не должно запускать удаление повторно.
        self.delete_task_shortcut.setAutoRepeat(False)

        self.delete_task_shortcut.activated.connect(
            self.delete_task_by_keyboard
        )
        left_layout.addWidget(self.table)

        row = QHBoxLayout()
        self.add_button(row, "Добавить", self.add_task)
        self.add_button(row, "Изменить", self.edit_task)
        self.add_button(row, "Удалить", self.delete_task)
        left_layout.addLayout(row)

        form = QFormLayout()

        self.note_title = QLineEdit()
        form.addRow(
            "Название списка:", self.note_title
        )

        self.visible_check = QCheckBox(
            "Показывать этот стикер на обоях"
        )
        form.addRow(self.visible_check)

        self.note_width = QSpinBox()
        self.note_width.setRange(15, 100)
        self.note_width.setSuffix(" % экрана")
        form.addRow("Ширина:", self.note_width)

        self.note_height = QSpinBox()
        self.note_height.setRange(10, 100)
        self.note_height.setSuffix(" % экрана")
        form.addRow("Высота:", self.note_height)

        self.font_size = QSpinBox()
        self.font_size.setRange(10, 48)
        form.addRow("Размер текста:", self.font_size)

        self.font_family = QFontComboBox()
        form.addRow("Шрифт:", self.font_family)

        self.show_header = QCheckBox(
            "Показывать название на стикере"
        )
        self.show_columns = QCheckBox(
            "Показывать «Задача / Дедлайн»"
        )
        form.addRow(self.show_header)
        form.addRow(self.show_columns)

        self.color_buttons = {}

        for key, label in [
            ("note_color", "Цвет блокнота:"),
            ("text_color", "Цвет текста:"),
            ("overdue_color", "Цвет просроченных:"),
        ]:
            button = QPushButton()
            button.clicked.connect(
                lambda checked=False, key=key:
                self.choose_color(key)
            )
            self.color_buttons[key] = button
            form.addRow(label, button)

        left_layout.addLayout(form)



        row = QHBoxLayout()
        self.add_button(
            row, "Дублировать список", self.duplicate_sticker
        )
        self.add_button(
            row, "Удалить список", self.delete_sticker
        )
        left_layout.addLayout(row)

        self.auto_apply = QCheckBox(
            "Автоматически обновлять обои"
        )
        left_layout.addWidget(self.auto_apply)
        left_layout.addWidget(
            QLabel("Уведомления для всех списков")
        )

        self.notifications_check = QCheckBox(
            "Уведомлять о дедлайнах"
        )
        self.notifications_check.setChecked(
            self.state["notifications_enabled"]
        )
        left_layout.addWidget(self.notifications_check)

        reminder_form = QFormLayout()

        self.reminder_minutes_spin = QSpinBox()
        self.reminder_minutes_spin.setRange(1, 10080)
        self.reminder_minutes_spin.setSuffix(" мин.")
        self.reminder_minutes_spin.setValue(
            self.state["reminder_minutes"]
        )

        reminder_form.addRow(
            "Напомнить за:",
            self.reminder_minutes_spin,
        )
        left_layout.addLayout(reminder_form)

        self.notify_at_deadline_check = QCheckBox(
            "Уведомлять при наступлении дедлайна"
        )
        self.notify_at_deadline_check.setChecked(
            self.state["notify_at_deadline"]
        )
        left_layout.addWidget(
            self.notify_at_deadline_check
        )

        self.add_button(
            left_layout,
            "Проверить уведомления",
            self.test_notification,
        )

        self.notifications_check.toggled.connect(
            self.notification_settings_changed
        )
        self.reminder_minutes_spin.valueChanged.connect(
            self.notification_settings_changed
        )
        self.notify_at_deadline_check.toggled.connect(
            self.notification_settings_changed
        )

        notifications_enabled = (
            self.notifications_check.isChecked()
        )
        self.reminder_minutes_spin.setEnabled(
            notifications_enabled
        )
        self.notify_at_deadline_check.setEnabled(
            notifications_enabled
        )

        left_layout.addWidget(
            QLabel("Шаблоны общего оформления")
        )
        self.preset_combo = QComboBox()
        left_layout.addWidget(self.preset_combo)

        row = QHBoxLayout()
        self.add_button(
            row, "Сохранить", self.save_preset
        )
        self.add_button(
            row, "Загрузить", self.load_preset
        )
        self.add_button(
            row, "Удалить", self.delete_preset
        )
        left_layout.addLayout(row)
        left_layout.addStretch()

        right = QWidget()
        right_layout = QVBoxLayout(right)
        splitter.addWidget(right)

        hint = QLabel(
            "Нажми на стикер, чтобы выбрать его. "
            "Перетаскивай мышью; меняй размер "
            "за синий квадрат справа внизу.\n"
            "Порядок слоёв задаётся вкладками: "
            "слева — нижний, справа — верхний. "
            "Вкладки можно перетаскивать."
        )
        hint.setWordWrap(True)
        right_layout.addWidget(hint)

        self.preview = WallpaperPreview()
        self.preview.sticker_selected.connect(
            self.select_sticker
        )
        self.preview.geometry_changed.connect(
            self.preview_geometry_changed
        )
        right_layout.addWidget(self.preview, 1)

        self.add_button(
            right_layout,
            "Применить к рабочему столу",
            self.refresh_wallpaper_now,
        )
        self.add_button(
            right_layout,
            "Сохранить картинку…",
            self.export_image,
        )

        splitter.setSizes([480, 870])

        self.background_combo.currentIndexChanged.connect(
            self.background_changed
        )
        self.note_width.valueChanged.connect(
            self.geometry_controls_changed
        )
        self.note_height.valueChanged.connect(
            self.geometry_controls_changed
        )

        self.note_title.editingFinished.connect(
            self.settings_changed
        )
        self.visible_check.toggled.connect(
            self.settings_changed
        )
        self.font_size.valueChanged.connect(
            self.settings_changed
        )
        self.font_family.currentFontChanged.connect(
            self.settings_changed
        )
        self.show_header.toggled.connect(
            self.settings_changed
        )
        self.show_columns.toggled.connect(
            self.settings_changed
        )
        self.auto_apply.toggled.connect(
            self.settings_changed
        )
        self.author_label = QLabel("Идея - Елизавета Шишакова. Автор - Николай Немов.")

        author_font = self.author_label.font()
        author_font.setPointSize(8)
        self.author_label.setFont(author_font)

        # Приглушённый цвет из палитры приложения.
        self.author_label.setForegroundRole(
            QPalette.ColorRole.PlaceholderText
        )

        self.author_label.setContentsMargins(8, 0, 8, 0)
        self.author_label.setToolTip(
            "Task Wallpaper story"
        )

        self.statusBar().addPermanentWidget(
            self.author_label
        )

    def rebuild_tabs(self):
        blocker = QSignalBlocker(self.tabs)

        while self.tabs.count():
            self.tabs.removeTab(0)

        active_index = 0

        for index, sticker in enumerate(
            self.state["stickers"]
        ):
            title = sticker["name"]

            if not sticker["visible"]:
                title += " (скрыт)"

            self.tabs.addTab(title)
            self.tabs.setTabData(index, sticker["id"])
            self.tabs.setTabToolTip(index, sticker["name"])

            if (
                sticker["id"]
                == self.state["active_sticker_id"]
            ):
                active_index = index

        self.tabs.setCurrentIndex(active_index)
        del blocker

    def tab_changed(self, index):
        if self.syncing or index < 0:
            return

        self.select_sticker(self.tabs.tabData(index))

    def delete_task_by_keyboard(self):
        app = QApplication.instance()

        # Главное окно должно быть активным.
        if app.activeWindow() is not self:
            return

        # Никаких удалений при открытом модальном окне
        # или всплывающем меню.
        if app.activeModalWidget() is not None:
            return

        if app.activePopupWidget() is not None:
            return

        # Del обрабатываем только в таблице.
        # В полях названия, выбора шрифта и других
        # элементах ввода он выполняет обычное действие.
        if app.focusWidget() is not self.table:
            return

        # Нужна именно выделенная строка, а не просто
        # сохранённый индекс текущей строки.
        selection = self.table.selectionModel()

        if (
            selection is None
            or not selection.selectedRows()
        ):
            return

        if self.selected_task() is None:
            return

        # Пока открыто подтверждение удаления,
        # повторный вызов этого сочетания запрещён.
        self.delete_task_shortcut.setEnabled(False)

        try:
            self.delete_task()
        finally:
            self.delete_task_shortcut.setEnabled(True)

    def rename_tab_by_double_click(self, index):
        # Двойной щелчок по пустому месту ничего не делает.
        if index < 0:
            return

        sticker_id = self.tabs.tabData(index)

        if self.find_sticker(sticker_id) is None:
            return

        self.select_sticker(sticker_id)
        self.rename_sticker()

    def tabs_reordered(self, from_index, to_index):
        if self.syncing:
            return

        # Берём фактический порядок ID из полосы вкладок.
        ordered_ids = [
            self.tabs.tabData(index)
            for index in range(self.tabs.count())
        ]

        stickers_by_id = {
            sticker["id"]: sticker
            for sticker in self.state["stickers"]
        }

        # Защита от неполного состояния при изменении интерфейса.
        if (
            len(ordered_ids) != len(stickers_by_id)
            or set(ordered_ids) != set(stickers_by_id)
        ):
            return

        self.state["stickers"] = [
            stickers_by_id[sticker_id]
            for sticker_id in ordered_ids
        ]

        current_index = self.tabs.currentIndex()

        if current_index >= 0:
            self.state["active_sticker_id"] = (
                self.tabs.tabData(current_index)
            )

        self.sync_controls()
        self.changed()

    def select_sticker(self, sticker_id):
        if self.find_sticker(sticker_id) is None:
            return

        self.state["active_sticker_id"] = sticker_id

        blocker = QSignalBlocker(self.tabs)

        for index in range(self.tabs.count()):
            if self.tabs.tabData(index) == sticker_id:
                self.tabs.setCurrentIndex(index)
                break

        del blocker

        self.sync_controls()
        self.update_preview()
        self.save_state()

    def tab_menu(self, position):
        index = self.tabs.tabAt(position)

        if index < 0:
            return

        self.select_sticker(self.tabs.tabData(index))

        menu = QMenu(self)
        menu.addAction(
            "Переименовать", self.rename_sticker
        )
        menu.addAction(
            "Дублировать", self.duplicate_sticker
        )
        menu.addAction(
            "Показать / скрыть", self.toggle_sticker
        )
        menu.addSeparator()
        menu.addAction(
            "Удалить", self.delete_sticker
        )
        menu.exec(self.tabs.mapToGlobal(position))

    def add_sticker(self):
        name, accepted = QInputDialog.getText(
            self,
            "Новый список",
            "Название:",
        )

        if not accepted or not name.strip():
            return

        sticker = make_sticker(name.strip())
        offset = (len(self.state["stickers"]) % 6) * 0.04
        sticker["note_rect"] = [
            0.05 + offset,
            0.05 + offset,
            0.32,
            0.35,
        ]

        self.state["stickers"].append(sticker)
        self.state["active_sticker_id"] = sticker["id"]

        self.rebuild_tabs()
        self.sync_controls()
        self.changed()

    def rename_sticker(self):
        sticker = self.active_sticker()

        name, accepted = QInputDialog.getText(
            self,
            "Переименовать список",
            "Название:",
            text=sticker["name"],
        )

        if accepted and name.strip():
            sticker["name"] = name.strip()
            sticker["note_title"] = name.strip()
            self.rebuild_tabs()
            self.sync_controls()
            self.changed()

    def duplicate_sticker(self):
        sticker = deepcopy(self.active_sticker())
        sticker["id"] = uuid.uuid4().hex
        sticker["name"] += " — копия"
        sticker["note_title"] = sticker["name"]

        for task in sticker["tasks"]:
            task["id"] = uuid.uuid4().hex

        x, y, width, height = sticker["note_rect"]
        sticker["note_rect"] = [
            min(1.0 - width, x + 0.03),
            min(1.0 - height, y + 0.03),
            width,
            height,
        ]

        self.state["stickers"].append(sticker)
        self.state["active_sticker_id"] = sticker["id"]

        self.rebuild_tabs()
        self.sync_controls()
        self.changed()

    def delete_sticker(self):
        if len(self.state["stickers"]) == 1:
            QMessageBox.information(
                self,
                "Список",
                "Последний список удалить нельзя. "
                "Его можно скрыть или переименовать.",
            )
            return

        sticker = self.active_sticker()

        answer = QMessageBox.question(
            self,
            "Удалить список",
            f"Удалить «{sticker['name']}» "
            "вместе со всеми его задачами?",
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        self.state["stickers"] = [
            item
            for item in self.state["stickers"]
            if item["id"] != sticker["id"]
        ]
        self.state["active_sticker_id"] = (
            self.state["stickers"][0]["id"]
        )

        self.rebuild_tabs()
        self.sync_controls()
        self.changed()

    def toggle_sticker(self):
        sticker = self.active_sticker()
        sticker["visible"] = not sticker["visible"]

        self.rebuild_tabs()
        self.sync_controls()
        self.changed()



    def sync_controls(self):
        self.syncing = True

        try:
            sticker = self.active_sticker()
            sort_index = self.sort_combo.findData(
                sticker.get("sort_mode", "deadline_asc")
            )

            self.sort_combo.setCurrentIndex(
                max(0, sort_index)
            )
            self.done_last_check.setChecked(
                sticker.get("done_last", True)
            )

            self.note_title.setText(
                sticker["note_title"]
            )
            self.visible_check.setChecked(
                sticker["visible"]
            )
            self.note_width.setValue(
                round(sticker["note_rect"][2] * 100)
            )
            self.note_height.setValue(
                round(sticker["note_rect"][3] * 100)
            )
            self.font_size.setValue(
                sticker["font_size"]
            )
            self.font_family.setCurrentFont(
                QFont(sticker["font_family"])
            )
            self.show_header.setChecked(
                sticker["show_header"]
            )
            self.show_columns.setChecked(
                sticker["show_columns"]
            )
            self.auto_apply.setChecked(
                self.state["auto_apply"]
            )

            self.update_color_buttons()
            self.refresh_tasks()

        finally:
            self.syncing = False

    def settings_changed(self, *_):
        if self.syncing:
            return

        sticker = self.active_sticker()

        title = self.note_title.text().strip()

        if not title:
            title = sticker["name"]
            self.note_title.setText(title)

        sticker.update(
            name=title,
            note_title=title,
            visible=self.visible_check.isChecked(),
            font_size=self.font_size.value(),
            font_family=(
                self.font_family.currentFont().family()
            ),
            show_header=self.show_header.isChecked(),
            show_columns=self.show_columns.isChecked(),
        )

        self.state["auto_apply"] = (
            self.auto_apply.isChecked()
        )

        self.rebuild_tabs()
        self.changed()

    def update_color_buttons(self):
        sticker = self.active_sticker()

        for key, button in self.color_buttons.items():
            color = QColor(sticker[key])
            brightness = (
                0.299 * color.red()
                + 0.587 * color.green()
                + 0.114 * color.blue()
            )
            foreground = (
                "#111111"
                if brightness >= 150
                else "#ffffff"
            )

            button.setText(color.name().upper())
            button.setStyleSheet(
                "QPushButton {"
                f"background-color: {color.name()};"
                f"color: {foreground};"
                "border: 1px solid #888;"
                "border-radius: 4px;"
                "padding: 6px;"
                "}"
            )

    def choose_color(self, key):
        sticker = self.active_sticker()
        color = QColorDialog.getColor(
            QColor(sticker[key]),
            self,
            "Выбрать цвет",
        )

        if color.isValid():
            sticker[key] = color.name()
            self.update_color_buttons()
            self.changed()

    def geometry_controls_changed(self, *_):
        if self.syncing:
            return

        sticker = self.active_sticker()
        x, y, _, _ = sticker["note_rect"]
        width = self.note_width.value() / 100
        height = self.note_height.value() / 100

        sticker["note_rect"] = [
            max(0.0, min(x, 1.0 - width)),
            max(0.0, min(y, 1.0 - height)),
            width,
            height,
        ]
        self.changed()

    def preview_geometry_changed(self, sticker_id, rect):
        if self._closing:
            return

        sticker = self.find_sticker(sticker_id)

        if sticker is None:
            return

        sticker["note_rect"] = list(rect)

        if (
            sticker_id
            == self.state["active_sticker_id"]
        ):
            width_blocker = QSignalBlocker(
                self.note_width
            )
            height_blocker = QSignalBlocker(
                self.note_height
            )

            self.note_width.setValue(
                round(rect[2] * 100)
            )
            self.note_height.setValue(
                round(rect[3] * 100)
            )

            del width_blocker
            del height_blocker

        # Сразу перемещаем рамку выбора.
        self.update_preview()

        # Во время движения запрашиваем лёгкое превью.
        # Более старые ожидающие запросы заменяются.
        self.queue_render(delay_ms=35)

        # Полноразмерный результат — после паузы.
        self.geometry_save_timer.start(350)

    def finish_geometry_change(self):
        if self._closing:
            return

        self.save_state()

        if self.state["auto_apply"]:
            self._apply_requested = True

        self.queue_render(delay_ms=0)

    def selected_task(self):
        row = self.table.currentRow()

        if row < 0:
            return None

        item = self.table.item(row, 0)

        if item is None:
            return None

        task_id = item.data(Qt.ItemDataRole.UserRole)

        return next(
            (
                task
                for task in self.active_sticker()["tasks"]
                if task["id"] == task_id
            ),
            None,
        )

    def add_task(self):
        sticker = self.active_sticker()
        dialog = TaskDialog(self)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            sticker["tasks"].append(
                {
                    "id": uuid.uuid4().hex,
                    "created": datetime.now().isoformat(
                        timespec="seconds"
                    ),
                    "done": False,
                    **dialog.values(),
                }
            )
            self.changed()

    def edit_task(self):
        task = self.selected_task()

        if task is None:
            return

        dialog = TaskDialog(self, task)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            task.update(dialog.values())
            self.changed()

    def delete_task(self):
        task = self.selected_task()

        if task is None:
            return

        answer = QMessageBox.question(
            self,
            "Удалить задачу",
            f"Удалить «{task['title']}»?",
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        sticker = self.active_sticker()
        sticker["tasks"] = [
            item
            for item in sticker["tasks"]
            if item["id"] != task["id"]
        ]
        self.changed()

    def task_checked(self, item):
        if self.syncing or item.column() != 0:
            return

        task_id = item.data(Qt.ItemDataRole.UserRole)

        for task in self.active_sticker()["tasks"]:
            if task["id"] == task_id:
                task["done"] = (
                    item.checkState()
                    == Qt.CheckState.Checked
                )
                break

        self.changed()

    def refresh_tasks(self):
        blocker = QSignalBlocker(self.table)

        selected_id = None
        row = self.table.currentRow()

        if row >= 0 and self.table.item(row, 0):
            selected_id = self.table.item(row, 0).data(
                Qt.ItemDataRole.UserRole
            )

        tasks = sorted_tasks(
            self.active_sticker(),
            include_done=True,
        )
        self.table.setRowCount(len(tasks))

        for row, task in enumerate(tasks):
            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            check.setData(
                Qt.ItemDataRole.UserRole, task["id"]
            )
            check.setCheckState(
                Qt.CheckState.Checked
                if task["done"]
                else Qt.CheckState.Unchecked
            )
            self.table.setItem(row, 0, check)

            date_text = "—"

            if task.get("deadline"):
                date_text = datetime.fromisoformat(
                    task["deadline"]
                ).strftime("%d.%m.%Y %H:%M")

            for column, text in [
                (1, task["title"]),
                (2, date_text),
            ]:
                cell = QTableWidgetItem(text)
                cell.setToolTip(text)

                if task["done"]:
                    cell.setForeground(QColor("#888888"))
                elif is_overdue(task):
                    # Цвет таблицы отделён от цветов обоев.
                    cell.setForeground(QColor("#e57373"))

                self.table.setItem(row, column, cell)

            if task["id"] == selected_id:
                self.table.selectRow(row)

        del blocker

    def populate_backgrounds(self):
        blocker = QSignalBlocker(
            self.background_combo
        )
        self.background_combo.clear()
        self.background_combo.addItem(
            "Однотонный фон", ""
        )

        for item in self.state["backgrounds"]:
            self.background_combo.addItem(
                item["name"], item["file"]
            )

        index = self.background_combo.findData(
            self.state["active_background"]
        )
        self.background_combo.setCurrentIndex(
            max(0, index)
        )
        del blocker

    def background_changed(self, *_):
        if self.syncing:
            return

        self.state["active_background"] = (
            self.background_combo.currentData() or ""
        )
        self.changed()

    def add_background(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Добавить фоны",
            "",
            "Изображения (*.png *.jpg *.jpeg *.bmp *.webp)",
        )

        for value in paths:
            source = Path(value)

            if QImage(str(source)).isNull():
                QMessageBox.warning(
                    self,
                    "Изображение",
                    f"Не удалось открыть:\n{source}",
                )
                continue

            destination = BACKGROUNDS / (
                uuid.uuid4().hex
                + source.suffix.lower()
            )
            shutil.copy2(source, destination)

            relative = (
                destination.relative_to(DATA).as_posix()
            )
            self.state["backgrounds"].append(
                {
                    "name": source.name,
                    "file": relative,
                }
            )
            self.state["active_background"] = relative

        self.populate_backgrounds()
        self.changed()

    def remove_background(self):
        value = self.background_combo.currentData()

        if not value:
            return

        answer = QMessageBox.question(
            self,
            "Удалить фон",
            "Удалить фон из коллекции? "
            "Шаблоны, использующие его, потеряют этот фон.",
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        self.state["backgrounds"] = [
            item
            for item in self.state["backgrounds"]
            if item["file"] != value
        ]

        copied_file = (DATA / value).resolve()

        if copied_file.is_relative_to(
            BACKGROUNDS.resolve()
        ):
            copied_file.unlink(missing_ok=True)

        self.state["active_background"] = ""
        self.populate_backgrounds()
        self.changed()

    def changed(self):
        if self._closing:
            return

        self.geometry_save_timer.stop()

        self.save_state()
        self.refresh_tasks()

        self.last_signature = (
            self.deadline_signature()
        )

        if self.state["auto_apply"]:
            self._apply_requested = True

        self.queue_render(delay_ms=100)

    def deadline_signature(self):
        now = datetime.now()

        return tuple(
            (
                sticker["id"],
                task["id"],
                is_overdue(task, now),
            )
            for sticker in self.state["stickers"]
            for task in sticker["tasks"]
        )

    def check_deadlines(self):
        if self._closing:
            return

        # Напоминания не ждут окончания отрисовки.
        self.check_reminders()

        signature = self.deadline_signature()

        if signature == self.last_signature:
            return

        self.last_signature = signature
        self.refresh_tasks()

        if self.state["auto_apply"]:
            self._apply_requested = True

        self.queue_render(delay_ms=0)

    def regenerate(self):
        if self._closing:
            return

        # Это статус данных, для которых уже запрошена
        # отрисовка, а не статус последней готовой картинки.
        self.last_signature = (
            self.deadline_signature()
        )

        self.queue_render()

    def update_preview(self):
        if self.image is not None:
            self.preview.set_content(
                self.image,
                self.state["stickers"],
                self.state["active_sticker_id"],
            )

    def refresh_wallpaper_now(self):
        self.apply_wallpaper(silent=False)

    def apply_wallpaper(self, silent=False):
        if self._closing:
            return

        self._apply_requested = True

        # Если есть хотя бы одно явное нажатие кнопки,
        # ошибку нужно показать пользователю.
        self._apply_silent = (
            self._apply_silent and silent
        )

        self.queue_render(delay_ms=0)

    def export_image(self):
        if self._closing:
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить изображение",
            "wallpaper.png",
            "PNG (*.png)",
        )

        if not path:
            return

        if not Path(path).suffix:
            path += ".png"

        path = str(Path(path).resolve())

        if path not in self._export_paths:
            self._export_paths.append(path)

        self.queue_render(delay_ms=0)

        self.statusBar().showMessage(
            "Экспорт изображения запрошен…",
            3000,
        )

    def populate_presets(self):
        self.preset_combo.clear()

        for preset in self.state["presets"]:
            self.preset_combo.addItem(
                preset["name"], preset["id"]
            )

    def save_preset(self):
        name, accepted = QInputDialog.getText(
            self,
            "Сохранить шаблон",
            "Название шаблона:",
        )
        name = name.strip()

        if not accepted or not name:
            return

        existing = next(
            (
                item
                for item in self.state["presets"]
                if item["name"] == name
            ),
            None,
        )

        if existing is not None:
            answer = QMessageBox.question(
                self,
                "Перезаписать",
                f"Перезаписать шаблон «{name}»?",
            )

            if answer != QMessageBox.StandardButton.Yes:
                return

        settings = {
            "active_background": (
                self.state["active_background"]
            ),
            "stickers": [
                sticker_layout(sticker)
                for sticker in self.state["stickers"]
            ],
        }

        if existing is None:
            existing = {
                "id": uuid.uuid4().hex,
                "name": name,
            }
            self.state["presets"].append(existing)

        existing["settings"] = settings
        preset_id = existing["id"]

        self.save_state()
        self.populate_presets()
        self.preset_combo.setCurrentIndex(
            self.preset_combo.findData(preset_id)
        )

    def load_preset(self):
        preset_id = self.preset_combo.currentData()

        preset = next(
            (
                item
                for item in self.state["presets"]
                if item["id"] == preset_id
            ),
            None,
        )

        if preset is None:
            return

        settings = deepcopy(preset["settings"])
        background = settings.get(
            "active_background", ""
        )

        if background and not (DATA / background).is_file():
            QMessageBox.warning(
                self,
                "Фон отсутствует",
                "Фон шаблона не найден. "
                "Будет использован однотонный фон.",
            )
            background = ""

        self.state["active_background"] = background

        if "stickers" not in settings:
            # Шаблон старого формата:
            # применяем оформление к выбранному списку.
            sticker = self.active_sticker()

            for key in STYLE_KEYS:
                sticker[key] = deepcopy(
                    settings.get(
                        key, STYLE_DEFAULTS[key]
                    )
                )

            if "note_title" in settings:
                title = settings["note_title"]
                sticker["name"] = title
                sticker["note_title"] = title

            normalized = normalize_sticker(sticker)
            sticker.update(normalized)

        else:
            current = {
                sticker["id"]: sticker
                for sticker in self.state["stickers"]
            }
            ordered = []

            for layout in settings["stickers"]:
                sticker_id = layout["id"]
                sticker = current.pop(
                    sticker_id, None
                )

                if sticker is None:
                    sticker = make_sticker(
                        layout.get("name", "Список дел")
                    )

                tasks = sticker["tasks"]
                sticker.update(deepcopy(layout))
                sticker["tasks"] = tasks

                ordered.append(
                    normalize_sticker(sticker)
                )

            # Дополнительные текущие списки не удаляем.
            ordered.extend(current.values())

            if ordered:
                self.state["stickers"] = ordered

        if self.find_sticker(
            self.state["active_sticker_id"]
        ) is None:
            self.state["active_sticker_id"] = (
                self.state["stickers"][0]["id"]
            )

        self.populate_backgrounds()
        self.rebuild_tabs()
        self.sync_controls()
        self.changed()

    def delete_preset(self):
        preset_id = self.preset_combo.currentData()

        if not preset_id:
            return

        answer = QMessageBox.question(
            self,
            "Удалить шаблон",
            "Удалить выбранный шаблон?",
        )

        if answer != QMessageBox.StandardButton.Yes:
            return

        self.state["presets"] = [
            item
            for item in self.state["presets"]
            if item["id"] != preset_id
        ]

        self.save_state()
        self.populate_presets()

    def build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        icon = self.windowIcon()

        if icon.isNull():
            icon = self.style().standardIcon(
                QStyle.StandardPixmap.SP_ComputerIcon
            )

        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip(
            "Task Wallpaper"
        )

        menu = QMenu(self)

        for title, callback in [
            ("Открыть", self.show_window),
            ("Обновить обои", self.refresh_wallpaper_now),
        ]:
            action = QAction(title, self)
            action.triggered.connect(
                lambda checked=False, callback=callback:
                callback()
            )
            menu.addAction(action)

        menu.addSeparator()

        action = QAction("Выйти", self)
        action.triggered.connect(
            self.quit_application
        )
        menu.addAction(action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            self.tray_activated
        )
        self.tray.messageClicked.connect(
            self.show_window
        )
        self.tray.show()

    def tray_activated(self, reason):
        if (
            reason
            == QSystemTrayIcon.ActivationReason.DoubleClick
        ):
            self.show_window()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_application(self):
        if self._closing:
            return

        self._closing = True

        self.timer.stop()
        self.geometry_save_timer.stop()
        self.render_notice_timer.stop()

        self.save_state()

        self.setEnabled(False)
        self.render_status.setText(
            "Завершение фоновой операции…"
        )

        if self.tray is not None:
            self.tray.hide()

        # Выход произойдёт после сигнала stopped.
        self.render_service.stop()

    def closeEvent(self, event):
        if self._closing:
            event.ignore()
            return

        self.save_state()

        if self.tray is not None:
            self.hide()
            event.ignore()
        else:
            event.ignore()
            self.quit_application()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("TaskWallpaper")
    app.setApplicationDisplayName(
        "Task Wallpaper"
    )
    app.setOrganizationName("TaskWallpaper")
    app.setStyle("Fusion")

    if ICON_FILE.is_file():
        app.setWindowIcon(QIcon(str(ICON_FILE)))

    try:
        prepare_data()
    except Exception as error:
        QMessageBox.critical(
            None,
            "Ошибка подготовки данных",
            str(error),
        )
        return 1

    instance_lock = QLockFile(
        str(DATA / "application.lock")
    )

    if not instance_lock.tryLock(100):
        QMessageBox.information(
            None,
            "Task Wallpaper",
            "Новая версия уже запущена. "
            "Откройте её через значок в трее.",
        )
        return 0

    try:
        window = MainWindow()
    except Exception as error:
        QMessageBox.critical(
            None,
            "Ошибка запуска",
            f"{type(error).__name__}: {error}\n\n"
            "Проверьте код и файл настроек. "
            "Данные старой версии находятся "
            "в отдельной папке.",
        )
        return 1

    # Завершаем приложение явно после остановки потока.
    app.setQuitOnLastWindowClosed(False)

    start_in_tray = "--tray" in sys.argv

    if start_in_tray:
        window.state["auto_apply"] = True

        blocker = QSignalBlocker(
            window.auto_apply
        )
        window.auto_apply.setChecked(True)
        del blocker

        window.save_state()

    if not start_in_tray or window.tray is None:
        window.show()

    def initial_refresh():
        window.refresh_tasks()
        window.regenerate()

        if window.state["auto_apply"]:
            window.apply_wallpaper(silent=True)

    QTimer.singleShot(0, initial_refresh)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())