"""Лист предпросмотра: несколько рендеров на одной картинке, чтобы посмотреть их за один раз.

Запуск (Python с Pillow, см. SKILL.md -> «Окружение»):
  python -I contact_sheet.py <out.jpg> <img1> <img2> ... [--cols 2] [--cell 640]

Горизонтальные и вертикальные кадры вписываются в ячейку 4:3 с сохранением пропорций, подпись — имя файла.
"""
import sys, os
from PIL import Image, ImageDraw

args = sys.argv[1:]
def opt(name, default):
    if name in args:
        i = args.index(name); v = args[i + 1]; del args[i:i + 2]; return int(v)
    return default
cols = opt("--cols", 2); cell = opt("--cell", 640)
out, files = args[0], args[1:]
cw, ch = cell, int(cell * 0.75)
rows = (len(files) + cols - 1) // cols
sheet = Image.new("RGB", (cw * cols, ch * rows), (245, 243, 240)); d = ImageDraw.Draw(sheet)
for i, f in enumerate(files):
    im = Image.open(f).convert("RGB"); im.thumbnail((cw - 8, ch - 8))
    x = (i % cols) * cw + (cw - im.width) // 2; y = (i // cols) * ch + (ch - im.height) // 2
    sheet.paste(im, (x, y))
    label = os.path.splitext(os.path.basename(f))[0]
    d.rectangle([(i % cols) * cw + 4, (i // cols) * ch + 4, (i % cols) * cw + 8 + 7 * len(label), (i // cols) * ch + 20], fill=(0, 0, 0))
    d.text(((i % cols) * cw + 7, (i // cols) * ch + 6), label, fill=(255, 255, 255))
sheet.save(out, quality=85)
print("SHEET", out, len(files))
