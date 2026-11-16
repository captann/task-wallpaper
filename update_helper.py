import ctypes
import hashlib
import json
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path


def message(text):
    ctypes.windll.user32.MessageBoxW(
        None,
        text,
        "Task Wallpaper — обновление",
        0x10,
    )


def sha256_file(path):
    digest = hashlib.sha256()

    with open(path, "rb") as stream:
        for chunk in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def main():
    config_path = Path(sys.argv[1]).resolve()
    config = json.loads(
        config_path.read_text(encoding="utf-8")
    )

    folder = config_path.parent
    ready_file = folder / "ready"
    cancel_file = folder / "cancel"
    log_file = folder / "installer.log"

    kernel32 = ctypes.WinDLL(
        "kernel32",
        use_last_error=True,
    )

    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE

    kernel32.WaitForSingleObject.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD

    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(
        0x00100000,  # SYNCHRONIZE
        False,
        int(config["pid"]),
    )

    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        ready_file.write_text("ready", encoding="ascii")

        deadline = time.monotonic() + 180

        while True:
            if cancel_file.exists():
                return 0

            result = kernel32.WaitForSingleObject(
                handle,
                200,
            )

            if result == 0:
                break

            if result != 258:  # WAIT_TIMEOUT
                raise RuntimeError(
                    "Ошибка ожидания завершения приложения."
                )

            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "Приложение не завершилось за 3 минуты. "
                    "Обновление отменено."
                )

    finally:
        kernel32.CloseHandle(handle)

    if cancel_file.exists():
        return 0

    installer = Path(config["installer"]).resolve()
    app_exe = Path(config["app_exe"]).resolve()

    if sha256_file(installer) != config["sha256"]:
        raise RuntimeError(
            "Контрольная сумма установщика изменилась. "
            "Установка отменена."
        )

    state_file = Path(config["state_file"])

    if state_file.is_file():
        shutil.copy2(
            state_file,
            folder / "state-before-update.json",
        )

    result = subprocess.run(
        [
            str(installer),
            "/SP-",
            "/SILENT",
            "/NORESTART",
            "/RESTARTEXITCODE=3010",
            "/NOCLOSEAPPLICATIONS",
            "/NORESTARTAPPLICATIONS",
            f"/DIR={app_exe.parent}",
            f"/LOG={log_file}",
        ],
        check=False,
    )

    if result.returncode == 3010:
        message(
            "Обновление установлено, но требуется "
            "перезагрузка Windows.\n\n"
            f"Журнал установки:\n{log_file}"
        )
        return 0

    if result.returncode != 0:
        raise RuntimeError(
            "Установка не завершена.\n"
            f"Код: {result.returncode}\n\n"
            f"Журнал:\n{log_file}\n\n"
            "Проверьте состояние приложения перед "
            "повторным запуском."
        )

    subprocess.Popen(
        [str(app_exe)],
        cwd=str(app_exe.parent),
        close_fds=True,
    )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        message(
            f"{type(error).__name__}: {error}"
        )
        sys.exit(1)