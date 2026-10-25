"""Разбор 3D-сцены планировки: список объектов по типам с габаритами + вид сверху и изометрия.

Запуск:
  blender -b --python inspect_scene.py -- <scene.glb|.fbx|.obj> <out_dir>

Пишет в out_dir:
  objects.txt  — тип | экземпляр | min xyz | max xyz (по группам имён 'тип|objN|_экземпляр')
  summary.txt  — габарит квартиры, высота потолка (по стенам), число объектов по типам
  top.png      — ортогональный вид сверху (верхние заглушки стен и фасад скрыты)
  iso.png      — изометрия
Перед рендером сцены этот разбор обязателен: по нему пишутся правила материалов и координаты встроенной мебели.
"""
import bpy, sys, os, math, collections
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
SRC, OUT = argv[0], argv[1]
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scene_lib as L

L.init(root=os.path.dirname(SRC))
meshes = L.import_scene(SRC)

groups = collections.defaultdict(list)
for o in meshes:
    p = o.name.split("|")
    inst = next((x for x in p if x.startswith("_")), "")
    mn, mx = L.bounds(o)
    groups[(p[0], inst)].append((mn, mx))
lines = []; counts = collections.Counter()
allmn = Vector((1e9,) * 3); allmx = Vector((-1e9,) * 3)
for (t, inst), bbs in sorted(groups.items()):
    mn = Vector([min(b[0][i] for b in bbs) for i in range(3)]); mx = Vector([max(b[1][i] for b in bbs) for i in range(3)])
    counts[t] += 1
    if not t.startswith(("CapUpper", "Mesh_")):
        allmn = Vector(map(min, allmn, mn)); allmx = Vector(map(max, allmx, mx))
    if t.startswith(("Mesh_", "CapUpper")):
        continue                       # служебные части розеток и верхние заглушки стен
    lines.append(f"{t} | {inst} | {tuple(round(v, 2) for v in mn)} | {tuple(round(v, 2) for v in mx)} | parts={len(bbs)}")
open(os.path.join(OUT, "objects.txt"), "w").write("\n".join(lines))
walls = [L.bounds(o)[1].z for o in meshes if o.name.startswith("wall")]
summary = [f"Габарит (без заглушек): x {allmn.x:.2f}..{allmx.x:.2f}, y {allmn.y:.2f}..{allmx.y:.2f}, z {allmn.z:.2f}..{allmx.z:.2f}",
           f"Высота стен (макс): {max(walls):.2f}" if walls else "Стены не найдены по префиксу 'wall'",
           "Объекты по типам: " + ", ".join(f"{k}×{v}" for k, v in counts.most_common() if not k.startswith(("Mesh_", "CapUpper")))]
open(os.path.join(OUT, "summary.txt"), "w").write("\n".join(summary))
print("\n".join(summary))

for o in meshes:
    if o.name.startswith(("CapUpper", "FacadeWall")):
        o.hide_render = True
sc = bpy.context.scene
sc.render.engine = "BLENDER_EEVEE"
L.sky_world(strength=1.2); L.sun(energy=3, elevation_deg=60)
w, h = allmx.x - allmn.x, allmx.y - allmn.y; c = (allmn + allmx) / 2
cam = L.camera("Кам_план", (c.x, c.y, allmx.z - 0.4), ortho_scale=max(w, h) * 1.08)
cam.data.clip_end = 50
sc.camera = cam
# ortho_scale задаёт бо́льшую сторону кадра, поэтому пропорции кадра = пропорции квартиры
sc.render.resolution_x = int(1400 * w / h) if w < h else 1400
sc.render.resolution_y = 1400 if w < h else int(1400 * h / w)
sc.view_settings.view_transform = "AgX"; sc.view_settings.exposure = -0.8
sc.render.filepath = os.path.join(OUT, "top.png"); bpy.ops.render.render(write_still=True)
d = max(w, h)
cam2 = L.camera("Кам_изо", (c.x + d * 0.8, c.y - d, d * 1.1), (c.x, c.y, 0.3), 35)
sc.camera = cam2; sc.render.resolution_x = 1200; sc.render.resolution_y = 800
sc.render.filepath = os.path.join(OUT, "iso.png"); bpy.ops.render.render(write_still=True)
print("INSPECTED", OUT)
