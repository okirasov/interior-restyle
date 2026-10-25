"""Разбор 3D-сцены планировки: список объектов по типам с габаритами + вид сверху и изометрия.

Запуск:
  blender -b --python inspect_scene.py -- <scene.glb|.fbx|.obj> <out_dir>

Пишет в out_dir:
  objects.txt  — тип | экземпляр | min xyz | max xyz (по группам имён 'тип|objN|_экземпляр');
                 у стен — ещё комната R_<id>, в конце — список полов с площадью и мебелью на них
  parts.txt    — каждая деталь мебели: имя | min | max | вершин | базовый цвет | tex — по нему видно,
                 какая objN фасад, какая корпус, где служебные точки (по 4–24 вершины) и техника (.00N)
  summary.txt  — габарит квартиры, высота потолка (по стенам), число объектов по типам
  top.png      — вид сверху с сеткой через 1 м и подписями координат (верх картинки = +Y, право = +X)
  iso.png      — изометрия
Перед рендером сцены этот разбор обязателен: по нему пишутся правила материалов и координаты встроенной мебели.
"""
import bpy, sys, os, math, collections
from mathutils import Vector
from mathutils.bvhtree import BVHTree

argv = sys.argv[sys.argv.index("--") + 1:]
SRC, OUT = argv[0], argv[1]
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scene_lib as L

L.init(root=os.path.dirname(SRC))
meshes = L.import_scene(SRC)

groups = collections.defaultdict(list); rooms = collections.defaultdict(set)
for o in meshes:
    p = o.name.split("|")
    inst = next((x for x in p if x.startswith("_")), "")
    mn, mx = L.bounds(o)
    groups[(p[0], inst)].append((mn, mx))
    if p[0] == "wall" and len(p) > 1:
        rooms[inst].add(p[1])
lines = []; counts = collections.Counter()
allmn = Vector((1e9,) * 3); allmx = Vector((-1e9,) * 3)
for (t, inst), bbs in sorted(groups.items()):
    mn = Vector([min(b[0][i] for b in bbs) for i in range(3)]); mx = Vector([max(b[1][i] for b in bbs) for i in range(3)])
    counts[t] += 1
    if not t.startswith(("CapUpper", "Mesh_")):
        allmn = Vector(map(min, allmn, mn)); allmx = Vector(map(max, allmx, mx))
    if t.startswith(("Mesh_", "CapUpper")):
        continue                       # служебные части розеток и верхние заглушки стен
    room = f" [{','.join(sorted(rooms[inst]))}]" if t == "wall" else ""
    lines.append(f"{t} | {inst}{room} | {tuple(round(v, 2) for v in mn)} | {tuple(round(v, 2) for v in mx)} | parts={len(bbs)}")
SKIP = ("wall", "floor", "CapUpper", "FacadeWall", "Mesh_", "socket", "portal")
lines.append("\n# Полы (в открытой планировке один пол на несколько зон — зоны делит мебель/штора)")
for o in meshes:
    if o.name.startswith("floor|"):
        top = [f for f in o.data.polygons if f.normal.z > 0.99]
        area = sum(f.area for f in top); fmn, fmx = L.bounds(o)
        bvh = BVHTree.FromPolygons([o.matrix_world @ v.co for v in o.data.vertices], [list(f.vertices) for f in top])
        on = sorted({t for (t, i), bbs in groups.items() if not t.startswith(SKIP)   # мебель, чей центр над этим полом
                     if bvh.ray_cast(Vector((sum((b[0].x + b[1].x) / 2 for b in bbs) / len(bbs),
                                             sum((b[0].y + b[1].y) / 2 for b in bbs) / len(bbs), 5)), Vector((0, 0, -1)))[0]})
        lines.append(f"{o.name} | площадь {area:.1f} м² | {tuple(round(v, 2) for v in fmn)} | {tuple(round(v, 2) for v in fmx)} | {', '.join(on)}")
open(os.path.join(OUT, "objects.txt"), "w").write("\n".join(lines))

def base_color(m):
    if not (m and m.use_nodes and "Principled BSDF" in m.node_tree.nodes): return None
    return tuple(round(c, 2) for c in m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value[:3])
parts = []
for o in sorted(meshes, key=lambda o: o.name):
    if o.name.startswith(SKIP): continue
    mn, mx = L.bounds(o); m = o.data.materials[0] if o.data.materials else None
    tex = "tex" if m and m.use_nodes and any(n.type == "TEX_IMAGE" for n in m.node_tree.nodes) else ""
    parts.append(f"{o.name} | {tuple(round(v, 2) for v in mn)} | {tuple(round(v, 2) for v in mx)} | "
                 f"{len(o.data.vertices)} | {base_color(m)} | {tex}")
open(os.path.join(OUT, "parts.txt"), "w").write("\n".join(parts))
walls = [L.bounds(o)[1].z for o in meshes if o.name.startswith("wall")]
summary = [f"Габарит (без заглушек): x {allmn.x:.2f}..{allmx.x:.2f}, y {allmn.y:.2f}..{allmx.y:.2f}, z {allmn.z:.2f}..{allmx.z:.2f}",
           f"Высота стен (макс): {max(walls):.2f}" if walls else "Стены не найдены по префиксу 'wall'",
           "План top.png: верх = +Y, право = +X, сетка через 1 м",
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
sc.view_settings.view_transform = "AgX"; sc.view_settings.look = "AgX - Medium High Contrast"; sc.view_settings.exposure = -0.2

# сетка через 1 м с подписями (только на плане): тонкие тёмные линии над сценой и цифры по краям
L.M["_grid"] = L.emission("_Сетка", (0.05, 0.05, 0.05), 1.0)
grid = []; zg = cam.location.z - 0.15; ts = min(w, h) * 0.025   # ниже камеры плана, выше мебели
for gx in range(math.ceil(allmn.x), math.floor(allmx.x) + 1):
    grid.append(L.box(f"_сетка_x{gx}", gx - 0.006, gx + 0.006, allmn.y, allmx.y, zg, zg + 0.001, "_grid"))
for gy in range(math.ceil(allmn.y), math.floor(allmx.y) + 1):
    grid.append(L.box(f"_сетка_y{gy}", allmn.x, allmx.x, gy - 0.006, gy + 0.006, zg, zg + 0.001, "_grid"))
def label(text, x, y, align):
    cu = bpy.data.curves.new("_подпись", "FONT"); cu.body = text; cu.size = ts; cu.align_x = align; cu.align_y = "CENTER"
    t = bpy.data.objects.new("_подпись_" + text, cu); L.COLL.objects.link(t); t.location = (x, y, zg + 0.01)
    t.data.materials.append(L.M["_grid"]); grid.append(t)
for gx in range(math.ceil(allmn.x), math.floor(allmx.x) + 1):
    label(f"x{gx:+d}", gx, allmn.y - ts * 0.9, "CENTER")
for gy in range(math.ceil(allmn.y), math.floor(allmx.y) + 1):
    label(f"y{gy:+d}", allmn.x - ts * 0.3, gy, "RIGHT")
pad = 2 * ts * 2.2                               # поле под подписи
cam.data.ortho_scale = max(w, h) + 2 * pad
sc.render.resolution_x = int(1400 * (w + 2 * pad) / (h + 2 * pad)) if w < h else 1400
sc.render.resolution_y = 1400 if w < h else int(1400 * (h + 2 * pad) / (w + 2 * pad))
sc.render.filepath = os.path.join(OUT, "top.png"); bpy.ops.render.render(write_still=True)
for o in grid:
    o.hide_render = True
d = max(w, h)
cam2 = L.camera("Кам_изо", (c.x + d * 0.8, c.y - d, d * 1.1), (c.x, c.y, 0.3), 35)
sc.camera = cam2; sc.render.resolution_x = 1200; sc.render.resolution_y = 800
sc.render.filepath = os.path.join(OUT, "iso.png"); bpy.ops.render.render(write_still=True)
print("INSPECTED", OUT)
