from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QImage


def remove_green_background(source: Path) -> QImage:
    image = QImage(str(source)).convertToFormat(QImage.Format.Format_ARGB32)
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            distance = ((color.red()) ** 2 + (255 - color.green()) ** 2 + (color.blue()) ** 2) ** 0.5
            alpha = max(0, min(255, round((distance - 42) / 85 * 255)))
            if alpha < 255:
                color.setGreen(min(color.green(), max(color.red(), color.blue())))
            color.setAlpha(alpha)
            image.setPixelColor(x, y, color)
    return image


def png_bytes(image: QImage, size: int) -> bytes:
    scaled = image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    payload = QByteArray()
    buffer = QBuffer(payload)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    scaled.save(buffer, "PNG")
    return bytes(payload)


def write_ico(image: QImage, destination: Path) -> None:
    sizes = (16, 24, 32, 48, 64, 128, 256)
    payloads = [png_bytes(image, size) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = len(header) + 16 * len(sizes)
    entries = []
    for size, payload in zip(sizes, payloads, strict=True):
        dimension = 0 if size == 256 else size
        entries.append(struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(payload), offset))
        offset += len(payload)
    destination.write_bytes(header + b"".join(entries) + b"".join(payloads))


def main() -> int:
    source, png_path, ico_path = map(Path, sys.argv[1:4])
    image = remove_green_background(source)
    image.save(str(png_path), "PNG")
    write_ico(image, ico_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
