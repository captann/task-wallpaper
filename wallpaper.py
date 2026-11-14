import ctypes
import os
from pathlib import Path


def set_wallpaper(path: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("Установка обоев поддерживается только в Windows.")

    path = path.resolve()

    if not path.is_file():
        raise FileNotFoundError(f"Изображение не найдено: {path}")

    spi_setdeskwallpaper = 0x0014
    spif_updateinifile = 0x0001
    spif_sendchange = 0x0002

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    function = user32.SystemParametersInfoW
    function.argtypes = [
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_wchar_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int

    success = function(
        spi_setdeskwallpaper,
        0,
        str(path),
        spif_updateinifile | spif_sendchange,
    )

    if not success:
        error = ctypes.get_last_error()
        raise OSError(error, "Windows не удалось изменить обои.")