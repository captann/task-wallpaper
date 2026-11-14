import json
import os
import shutil
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path


LOCAL = Path(
    os.environ.get(
        "LOCALAPPDATA",
        str(Path.home() / "AppData" / "Local"),
    )
)

DATA = LOCAL / "TaskWallpaper" / "data"
OLD_DATA = LOCAL / "TaskWallpaper" / "data"
BACKGROUNDS = DATA / "backgrounds"
STATE_FILE = DATA / "state.json"

STYLE_DEFAULTS = {
    "note_rect": [0.62, 0.55, 0.38, 0.45],
    "font_size": 18,
    "font_family": "GOST type B ",
    "note_color": "#fff4bf",
    "text_color": "#202020",
    "overdue_color": "#c62828",
    "show_header": True,
    "show_columns": True,
}

STYLE_KEYS = tuple(STYLE_DEFAULTS)


def make_sticker(name="Список дел"):
    return {
        "id": uuid.uuid4().hex,
        "name": name,
        "note_title": name,
        "visible": True,
        "tasks": [],
        **deepcopy(STYLE_DEFAULTS),
    }


def prepare_data():
    if not DATA.exists():
        DATA.parent.mkdir(parents=True, exist_ok=True)

        if (OLD_DATA / "state.json").is_file():
            shutil.copytree(
                OLD_DATA,
                DATA,
                ignore=shutil.ignore_patterns(
                    "application.lock",
                    "*.tmp",
                ),
            )
        else:
            DATA.mkdir()

    BACKGROUNDS.mkdir(parents=True, exist_ok=True)


def normalize_sticker(value):
    result = make_sticker(value.get("name", "Список дел"))
    result.update(deepcopy(value))

    if not result.get("id"):
        result["id"] = uuid.uuid4().hex

    result["name"] = str(result.get("name") or "Список дел")
    result["note_title"] = str(
        result.get("note_title", result["name"])
    )

    rect = result.get("note_rect", STYLE_DEFAULTS["note_rect"])

    try:
        x, y, width, height = map(float, rect)
        width = max(0.15, min(1.0, width))
        height = max(0.10, min(1.0, height))
        x = max(0.0, min(1.0 - width, x))
        y = max(0.0, min(1.0 - height, y))
        result["note_rect"] = [x, y, width, height]
    except (ValueError, TypeError):
        result["note_rect"] = deepcopy(
            STYLE_DEFAULTS["note_rect"]
        )

    result["font_size"] = max(
        10, min(48, int(result["font_size"]))
    )

    for task in result["tasks"]:
        task.setdefault("id", uuid.uuid4().hex)
        task.setdefault("title", "")
        task.setdefault("deadline", None)
        task.setdefault("done", False)
        task.setdefault("created", "")

    return result


def load_state():
    if STATE_FILE.exists():
        state = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(state, dict):
            raise ValueError("Файл настроек должен содержать объект JSON.")
    else:
        state = {}

    if state and "stickers" not in state:
        backup = DATA / (
            "state_before_multi_"
            + datetime.now().strftime("%Y%m%d_%H%M%S")
            + ".json"
        )
        shutil.copy2(STATE_FILE, backup)

        first = make_sticker(
            state.get("note_title") or "Список дел"
        )
        first["tasks"] = deepcopy(state.get("tasks", []))

        for key in STYLE_KEYS:
            if key in state:
                first[key] = deepcopy(state[key])

        first["note_title"] = state.get(
            "note_title", first["name"]
        )

        state["stickers"] = [first]
        state["active_sticker_id"] = first["id"]

        for key in (*STYLE_KEYS, "tasks", "note_title"):
            state.pop(key, None)

    stickers = [
        normalize_sticker(item)
        for item in state.get("stickers", [])
    ]

    if not stickers:
        stickers = [make_sticker()]

    ids = set()

    for sticker in stickers:
        if sticker["id"] in ids:
            sticker["id"] = uuid.uuid4().hex
        ids.add(sticker["id"])

    state["stickers"] = stickers

    if state.get("active_sticker_id") not in ids:
        state["active_sticker_id"] = stickers[0]["id"]

    state.setdefault("backgrounds", [])
    state.setdefault("active_background", "")
    state.setdefault("presets", [])
    state.setdefault("auto_apply", True)
    state.setdefault("wallpaper_slot", 0)
    state["schema_version"] = 2

    return state


def save_state(state):
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(STATE_FILE)


def sticker_layout(sticker):
    keys = (
        "id",
        "name",
        "note_title",
        "visible",
        *STYLE_KEYS,
    )
    return {
        key: deepcopy(sticker[key])
        for key in keys
    }