"""Средний цвет участков картинки (образец каталога, кусок референса) + полоска кропов для проверки глазами.

Запуск (Python с Pillow, см. SKILL.md -> «Окружение»; ОБЯЗАТЕЛЬНО с -I):
  python -I swatch.py <out.jpg> <img> x0 y0 x1 y1 [имя] [<img> x0 y0 x1 y1 [имя] ...]
  python -I swatch.py <out.jpg> --texture <diff.jpg> ...     # средний цвет всей текстуры

Координаты — в пикселях ИСХОДНОЙ картинки (размер печатается). Печатает средний sRGB (0..1) для
L.tint_for_swatch() / L.srgb_to_linear(). Полоску out.jpg обязательно посмотри: кроп с рендера легко попадает
на тень, стул или раму — тогда среднее бессмысленно. По рендерам бери только ровные освещённые участки.
"""
import sys, os
from PIL import Image, ImageDraw, ImageFont, ImageStat

def font(size=12):
    """Шрифт с кириллицей (встроенный шрифт Pillow кириллицу не рисует)."""
    for f in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "C:/Windows/Fonts/arial.ttf"):
        if os.path.exists(f): return ImageFont.truetype(f, size)
    return ImageFont.load_default()

args = sys.argv[1:]
out, args = args[0], args[1:]
crops = []
while args:
    if args[0] == "--texture":
        p = args[1]; args = args[2:]
        crops.append((os.path.basename(p), p, None))
        continue
    p, box = args[0], tuple(int(v) for v in args[1:5]); args = args[5:]
    name = os.path.basename(p)
    if args and not os.path.exists(args[0]) and args[0] != "--texture":
        name, args = args[0], args[1:]
    crops.append((name, p, box))

tiles = []
for name, p, box in crops:
    im = Image.open(p).convert("RGB"); size = im.size
    if box:
        im = im.crop(box)
    mean = [round(v / 255, 3) for v in ImageStat.Stat(im).mean]
    print(f"{name}: sRGB {tuple(mean)}   (картинка {size[0]}x{size[1]}, кроп {box or 'вся'})")
    t = im.copy(); t.thumbnail((220, 220)); tiles.append((name, t, mean))

W = sum(t.width + 10 for _, t, _ in tiles) + 10; Hh = max(t.height for _, t, _ in tiles) + 60
sheet = Image.new("RGB", (W, Hh), (245, 243, 240)); d = ImageDraw.Draw(sheet); x = 10; fnt = font()
for name, t, mean in tiles:
    sheet.paste(t, (x, 10))
    d.rectangle([x, t.height + 16, x + 40, t.height + 46], fill=tuple(int(v * 255) for v in mean))
    d.text((x + 46, t.height + 18), name[:28], fill=(0, 0, 0), font=fnt)
    d.text((x + 46, t.height + 32), str(tuple(mean)), fill=(60, 60, 60), font=fnt)
    x += t.width + 10
sheet.save(out, quality=88)
print("SWATCH", out)
