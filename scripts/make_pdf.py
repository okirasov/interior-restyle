"""PDF-презентация рендеров: A4 альбомная, обложка + разделы по комнатам с подписями.

Запуск (Python с reportlab и Pillow, см. SKILL.md -> «Окружение»; ОБЯЗАТЕЛЬНО с -I):
  python -I make_pdf.py <config.json> <out.pdf>

config.json:
{
  "renders_dir": "/abs/path/renders",
  "title": ["Квартира", "в новой планировке"],      # строки крупного заголовка обложки
  "kicker": "ДИЗАЙН-ПРОЕКТ",
  "intro": ["Стиль исходного проекта, перенесённый", "на новую планировку: ..."],
  "date_line": "Октябрь 2026 · Blender, Cycles",
  "footer": "Квартира · дизайн-проект · новая планировка",
  "cover_image": "13_iso.png",
  "sections": [
    {"title": "Кухня", "pages": [
        {"title": "Кухня с барной стойкой", "images": ["01_kitchen.png"], "captions": ["Белые колонны ..."]},
        {"title": "Душ · Раковина", "images": ["09_bath_shower.png", "10_bath_sink.png"], "captions": ["...", "..."]}
    ]}
  ]
}
Страница с одним изображением — на всю ширину, с двумя — рядом (удобно для вертикальных кадров санузла).
"""
import os, sys, json, tempfile
from PIL import Image
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import Color

cfg = json.load(open(sys.argv[1])); OUT = sys.argv[2]
R = cfg["renders_dir"]; TMP = tempfile.mkdtemp(prefix="pdfjpg_")

def find_font(names):
    for d in ("/System/Library/Fonts/Supplemental", "/Library/Fonts", "/usr/share/fonts/truetype/dejavu",
              "/usr/share/fonts/TTF", "C:/Windows/Fonts"):
        for n in names:
            p = os.path.join(d, n)
            if os.path.exists(p): return p
    raise SystemExit("Нет TTF-шрифта с кириллицей (Arial/DejaVu)")
pdfmetrics.registerFont(TTFont("Body", find_font(["Arial.ttf", "DejaVuSans.ttf", "arial.ttf"])))
pdfmetrics.registerFont(TTFont("Bold", find_font(["Arial Bold.ttf", "DejaVuSans-Bold.ttf", "arialbd.ttf"])))

W, H = landscape(A4); M = 28
INK, MUTED, ACCENT, PAPER = Color(.17, .15, .13), Color(.45, .42, .38), Color(.62, .48, .33), Color(.975, .965, .95)
TOP, BOTTOM = H - M - 38, 40
c = canvas.Canvas(OUT, pagesize=(W, H)); c.setTitle(" ".join(cfg.get("title", ["Рендеры"])))
page = [0]

def jpg(name):
    src = os.path.join(R, name); dst = os.path.join(TMP, os.path.splitext(name)[0] + ".jpg")
    Image.open(src).convert("RGB").save(dst, "JPEG", quality=90, optimize=True); return dst

def fit(name, x, y, w, h):
    with Image.open(os.path.join(R, name)) as im: iw, ih = im.size
    s = min(w / iw, h / ih); dw, dh = iw * s, ih * s; dx, dy = x + (w - dw) / 2, y + (h - dh) / 2
    c.drawImage(jpg(name), dx, dy, dw, dh); return dx, dy, dw, dh

def bg(): c.setFillColor(PAPER); c.rect(0, 0, W, H, stroke=0, fill=1)

def wrap(text, x, y, w, size=9):
    c.setFont("Body", size); c.setFillColor(MUTED); line = ""
    for word in text.split():
        t = (line + " " + word).strip()
        if pdfmetrics.stringWidth(t, "Body", size) > w: c.drawString(x, y, line); y -= size + 3; line = word
        else: line = t
    if line: c.drawString(x, y, line)

def content_page(section, p):
    bg(); c.setFillColor(ACCENT); c.setFont("Bold", 8.5); c.drawString(M, H - M - 2, section.upper())
    c.setFillColor(INK); c.setFont("Bold", 17); c.drawString(M, H - M - 24, p["title"])
    imgs, caps = p["images"], p.get("captions", [])
    n = len(imgs); gap = 16; cw = (W - 2 * M - gap * (n - 1)) / n
    for k, name in enumerate(imgs):
        x, y, w, h = fit(name, M + k * (cw + gap), BOTTOM + 14, cw, TOP - BOTTOM - 22)
        if k < len(caps): wrap(caps[k], x, y - 14, w)
    page[0] += 1; c.setFont("Body", 8); c.setFillColor(MUTED)
    c.drawString(M, 14, cfg.get("footer", "")); c.drawRightString(W - M, 14, f"{section}   {page[0]}"); c.showPage()

# обложка
bg()
if cfg.get("cover_image"): fit(cfg["cover_image"], W * 0.46, M, W * 0.54 - M, H - 2 * M)
c.setFillColor(ACCENT); c.setFont("Bold", 9); c.drawString(M, H - 90, cfg.get("kicker", ""))
c.setFillColor(INK); c.setFont("Bold", 30)
for i, t in enumerate(cfg.get("title", [])): c.drawString(M, H - 128 - i * 34, t)
c.setFont("Body", 11); c.setFillColor(MUTED)
for i, t in enumerate(cfg.get("intro", [])): c.drawString(M, H - 200 - i * 16, t)
c.setFont("Body", 9); c.drawString(M, 120, "Содержание"); c.setFillColor(INK)
for i, s in enumerate(cfg["sections"]):
    col, row = divmod(i, 4); c.drawString(M + col * 120, 104 - row * 12, f"{i + 1:02d}   {s['title']}")
c.setFillColor(MUTED); c.drawString(M, M, cfg.get("date_line", "")); c.showPage()

for i, s in enumerate(cfg["sections"]):
    for p in s["pages"]:
        content_page(f"{i + 1:02d} · {s['title']}", p)
c.save()
print("PDF", OUT, page[0] + 1, "pages")
