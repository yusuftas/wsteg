"""Render the wsteg icon (PNG for the app/Linux, ICO for the Windows exe)."""
import math
import os
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def render(size):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256
    bg = QLinearGradient(0, 0, size, size)
    bg.setColorAt(0, QColor("#1b2233"))
    bg.setColorAt(1, QColor("#0d1017"))
    p.setBrush(bg)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 48 * s, 48 * s)
    # spectrogram-like bars
    for i in range(9):
        h = (40 + 70 * abs(math.sin(i * 0.9 + 0.4))) * s
        x = (40 + i * 20) * s
        c = QColor("#4ea1ff")
        c.setAlpha(90 + i * 15)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(x, 200 * s - h, 12 * s, h), 3 * s, 3 * s)
    # waveform with a hidden "bit" square wave
    path = QPainterPath(QPointF(32 * s, 92 * s))
    for k in range(0, 193):
        x = 32 + k
        y = 92 + 30 * math.sin(k / 12.0) * math.exp(-((k - 96) / 90) ** 2)
        path.lineTo(QPointF(x * s, y * s))
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor("#e8833a"), 9 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawPath(path)
    p.end()
    return img


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    big = render(256)
    big.save(os.path.join(ROOT, "packaging", "wsteg.png"))
    big.save(os.path.join(ROOT, "wsteg", "gui", "wsteg.png"))
    ok = render(256).save(os.path.join(ROOT, "packaging", "wsteg.ico"), "ICO")
    print("ico", ok)
