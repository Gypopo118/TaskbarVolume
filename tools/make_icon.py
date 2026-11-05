#!/usr/bin/env python3
"""Генерация icon.png (256x256) и многоразмерного icon.ico."""

import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZE = 256
BG = (43, 43, 43, 255)
BORDER = (62, 62, 62, 255)
GREEN = (76, 194, 76, 255)
WHITE = (240, 240, 240, 255)

SCALE = 4  # сглаживание через суперсэмплинг


def rounded_mask(size, radius, inset=0):
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        [inset, inset, size - 1 - inset, size - 1 - inset],
        radius=radius,
        fill=255,
    )
    return mask


def build(size=SIZE):
    big = size * SCALE
    radius = int(big * 0.22)
    inset = int(big * 0.032)

    # Фон и зелёная заливка рисуются внутри скруглённой маски.
    content = Image.new("RGBA", (big, big), BG)
    fill_top = int(big * 0.50)
    ImageDraw.Draw(content).rectangle(
        [0, fill_top, big, big], fill=GREEN
    )
    mask = rounded_mask(big, radius, inset)
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    image.paste(content, (0, 0), mask)
    draw = ImageDraw.Draw(image)

    # Динамик
    cx, cy = big * 0.44, big * 0.46
    body_w = big * 0.075
    body_h = big * 0.13
    cone_w = big * 0.16
    cone_h = big * 0.30
    draw.rectangle(
        [cx - body_w * 0.5, cy - body_h * 0.5, cx + body_w * 0.5, cy + body_h * 0.5],
        fill=WHITE,
    )
    draw.polygon(
        [
            (cx + body_w * 0.5, cy - body_h * 0.5),
            (cx + body_w * 0.5 + cone_w * 0.45, cy - cone_h * 0.5),
            (cx + body_w * 0.5 + cone_w * 0.45, cy + cone_h * 0.5),
            (cx + body_w * 0.5, cy + body_h * 0.5),
        ],
        fill=WHITE,
    )

    # Волны
    line_width = max(2, int(big * 0.028))
    for radius_wave, start, extent in (
        (big * 0.12, -55, 110),
        (big * 0.20, -50, 100),
    ):
        box = [
            cx + body_w * 0.6,
            cy - radius_wave,
            cx + body_w * 0.6 + radius_wave * 2,
            cy + radius_wave,
        ]
        draw.arc(box, start=start, end=start + extent, fill=WHITE, width=line_width)

    # Контур
    draw.rounded_rectangle(
        [inset, inset, big - 1 - inset, big - 1 - inset],
        radius=radius,
        outline=BORDER,
        width=int(big * 0.016),
    )

    return image.resize((size, size), Image.LANCZOS)


def main():
    icon = build(SIZE)
    png_path = os.path.join(ROOT, "icon.png")
    ico_path = os.path.join(ROOT, "icon.ico")
    icon.save(png_path)
    icon.save(
        ico_path,
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"Сохранено: {png_path}")
    print(f"Сохранено: {ico_path}")


if __name__ == "__main__":
    main()
