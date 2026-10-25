"""Видео-проход по квартире с плавной камерой.

Маршрут задаётся точками (где стоит камера и куда смотрит). Скрипт сам распределяет время так, чтобы
скорость ходьбы и скорость поворота не превышали лимиты, сглаживает движение (без остановок в каждой точке
и без резких рывков) и «запекает» камеру в каждый кадр. Затем рендерит кадры в JPEG и склеивает ffmpeg в MP4.

Запуск:
  blender -b --python walkthrough.py -- <route.json> [--engine eevee|cycles] [--res 960] [--samples 32]
        [--name walkthrough_draft] [--duration 50] [--frames 1-300] [--stills] [--analyze] [--no-motion-blur]

route.json:
{
  "blend": "/abs/path/flat_design.blend",
  "out_dir": "/abs/path/renders",
  "fps": 25, "lens": 20, "eye": 1.6,
  "walk_speed": 0.6,
  "turn_rate": 35,
  "hide_prefixes": ["Жалюзи_гостиная_ламель"],
  "keys": [
    {"pos": [1.40, 4.35], "look": [1.40, 0.0, 1.40], "note": "вход"},
    {"pos": [1.62, 2.95], "look": [2.90, 2.10, 1.35], "hold": 1.0, "note": "санузел"},
    ...
  ]
}
walk_speed — м/с, спокойный шаг; turn_rate — град/с, комфортный поворот головы (больше 45 уже рвёт картинку
при 25 fps); hide_prefixes — что скрыть на время видео (например, поднять жалюзи); "..." в keys — остальные точки.
pos может быть [x, y] (высота = eye) или [x, y, z]. hold — пауза в точке, с.
--duration растягивает/сжимает всё время; если при этом лимиты превышаются, скрипт об этом пишет.
--analyze только печатает скорость и поворот по кадрам и выходит (рендера нет).
"""
import bpy, sys, os, math, json, subprocess
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
ROUTE = json.load(open(argv[0]))
def arg(name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default
ENGINE = arg("--engine", "eevee"); RES = int(arg("--res", "960")); SAMPLES = int(arg("--samples", "32"))
NAME = arg("--name", "walkthrough_draft"); DURATION = float(arg("--duration", "0") or 0)
FPS = int(ROUTE.get("fps", 25)); EYE = ROUTE.get("eye", 1.6)
WALK = ROUTE.get("walk_speed", 0.6); TURN = math.radians(ROUTE.get("turn_rate", 35))

# ------------------------------------------------------------- маршрут -> гладкие кривые
keys = ROUTE["keys"]
P = [Vector((k["pos"][0], k["pos"][1], k["pos"][2] if len(k["pos"]) > 2 else EYE)) for k in keys]
yaw, pitch = [], []
for k, p in zip(keys, P):
    d = Vector(k["look"]) - p
    a = math.atan2(d.y, d.x)
    if yaw:                                   # без перескока через ±180°
        while a - yaw[-1] > math.pi: a -= 2 * math.pi
        while a - yaw[-1] < -math.pi: a += 2 * math.pi
    yaw.append(a); pitch.append(math.atan2(d.z, math.hypot(d.x, d.y)))
N = len(P)

def cr(p0, p1, p2, p3, t):
    """Catmull-Rom между p1 и p2."""
    t2, t3 = t * t, t * t * t
    return 0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t3)

def along(arr, u):
    i = min(int(u), N - 2); t = u - i
    g = lambda j: arr[max(0, min(N - 1, j))]
    return cr(g(i - 1), g(i), g(i + 1), g(i + 2), t)

# длительность каждого перехода: не быстрее шага и не быстрее поворота
seg = []
for i in range(N - 1):
    L = sum((along(P, i + (s + 1) / 40) - along(P, i + s / 40)).length for s in range(40))
    seg.append(max(L / WALK, abs(yaw[i + 1] - yaw[i]) / TURN, abs(pitch[i + 1] - pitch[i]) / TURN, 0.4))
holds = [k.get("hold", 0.0) for k in keys]
timeline = []          # (длительность, u0, u1)
for i in range(N - 1):
    if holds[i]: timeline.append((holds[i], i, i))
    timeline.append((seg[i], i, i + 1))
if holds[-1]: timeline.append((holds[-1], N - 1, N - 1))
T = sum(t[0] for t in timeline)
scale = DURATION / T if DURATION else 1.0
if DURATION and scale < 0.95:
    print(f"ВНИМАНИЕ: маршрут при заданных лимитах длится {T:.1f} с; сжатие до {DURATION:.0f} с ускорит повороты "
          f"до ~{math.degrees(TURN) / scale:.0f}°/с. Лучше убрать лишние повороты или увеличить --duration.")
timeline = [(d * scale, a, b) for d, a, b in timeline]; T = sum(t[0] for t in timeline)
F = int(round(T * FPS)) + 1

# прогресс u(t) по кадрам (кусочно-линейный) + сглаживание гауссом ~0.4 с: без остановок в точках и с плавным стартом
u_raw = []
for f in range(F):
    t = f / FPS; acc = 0
    for d, a, b in timeline:
        if t <= acc + d or (d, a, b) == timeline[-1]:
            u_raw.append(a + (b - a) * min(1, max(0, (t - acc) / d)) if d else b); break
        acc += d
sig = 0.4 * FPS; rad = int(3 * sig)
w = [math.exp(-0.5 * (k / sig) ** 2) for k in range(-rad, rad + 1)]
u = []
for f in range(F):
    s = sum(w[k + rad] * u_raw[max(0, min(F - 1, f + k))] for k in range(-rad, rad + 1))
    u.append(s / sum(w))

frames = []
for f in range(F):
    pos = along(P, min(u[f], N - 1 - 1e-6)); a = along(yaw, min(u[f], N - 1 - 1e-6)); b = along(pitch, min(u[f], N - 1 - 1e-6))
    frames.append((pos, a, b))

def report():
    sp, tr = [], []
    for i in range(1, F):
        sp.append((frames[i][0] - frames[i - 1][0]).length * FPS)
        tr.append(math.degrees(math.hypot(frames[i][1] - frames[i - 1][1], frames[i][2] - frames[i - 1][2])) * FPS)
    srt = sorted(tr)
    print(f"ROUTE {T:.1f} s, {F} frames | speed max {max(sp):.2f} m/s | turn max {max(tr):.0f}°/s, "
          f"95% {srt[int(len(srt) * 0.95)]:.0f}°/s")
    for i in range(0, F - 1, FPS * 2):
        print(f"  t={i / FPS:5.1f}s  speed={sp[i]:.2f} m/s  turn={tr[i]:5.1f}°/s")

report()
if "--analyze" in argv:
    raise SystemExit

# ------------------------------------------------------------- камера и сцена
bpy.ops.wm.open_mainfile(filepath=ROUTE["blend"])
sc = bpy.context.scene
cd = bpy.data.cameras.new("Кам_проход"); cd.lens = ROUTE.get("lens", 20); cd.sensor_width = 36; cd.clip_start = 0.05
cam = bpy.data.objects.new("Кам_проход", cd); sc.collection.objects.link(cam); cam.rotation_mode = "XYZ"
for f, (pos, a, b) in enumerate(frames):
    cam.location = pos; cam.rotation_euler = (math.pi / 2 + b, 0, a - math.pi / 2)
    cam.keyframe_insert("location", frame=f + 1); cam.keyframe_insert("rotation_euler", frame=f + 1)
sc.camera = cam; sc.render.fps = FPS; sc.frame_start = 1; sc.frame_end = F
if arg("--frames"):
    a, b = arg("--frames").split("-"); sc.frame_start, sc.frame_end = int(a), int(b)

for o in bpy.data.objects:
    if o.type == "LIGHT" and o.data.type == "AREA":
        o.visible_camera = False               # заполняющие и оконные area-лампы не должны попадать в кадр
    if o.name.startswith(tuple(ROUTE.get("hide_prefixes", []))):
        o.hide_render = True

sc.render.resolution_x = RES; sc.render.resolution_y = int(RES * 9 / 16); sc.render.resolution_percentage = 100
if ENGINE == "eevee":
    sc.render.engine = "BLENDER_EEVEE"; sc.eevee.taa_render_samples = SAMPLES
    if hasattr(sc.eevee, "use_raytracing"): sc.eevee.use_raytracing = True
else:
    sc.render.engine = "CYCLES"; sc.cycles.samples = SAMPLES; sc.cycles.use_denoising = True
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import scene_lib
    scene_lib.use_gpu()                        # Metal/OptiX/CUDA/..., kernel optimization OFF (иначе Blender 5.2 падает)
    sc.render.use_persistent_data = True
    if "--no-motion-blur" not in argv:
        sc.render.use_motion_blur = True; sc.render.motion_blur_shutter = 0.5    # убирает «стробоскоп» при поворотах

out_dir = os.path.join(ROUTE["out_dir"], NAME); os.makedirs(out_dir, exist_ok=True)
if hasattr(sc.render.image_settings, "media_type"): sc.render.image_settings.media_type = "IMAGE"
sc.render.image_settings.file_format = "JPEG"; sc.render.image_settings.quality = 92

if "--stills" in argv:                          # контрольные кадры в точках маршрута
    for i in range(N):
        f = min(range(F), key=lambda j: abs(u[j] - i)) + 1
        sc.frame_set(f); sc.render.filepath = os.path.join(out_dir, f"key_{i:02d}.jpg")
        bpy.ops.render.render(write_still=True)
    print("STILLS", out_dir)
else:
    sc.render.filepath = os.path.join(out_dir, "f_"); sc.render.use_overwrite = False   # можно продолжить прерванный
    bpy.ops.render.render(animation=True)
    mp4 = os.path.join(ROUTE["out_dir"], f"{NAME}.mp4")
    subprocess.run(["ffmpeg", "-y", "-framerate", str(FPS), "-start_number", str(sc.frame_start),
                    "-i", os.path.join(out_dir, "f_%04d.jpg"), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "18", "-movflags", "+faststart", mp4], check=True)
    print("VIDEO", mp4)
