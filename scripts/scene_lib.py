"""Общие функции для сборки интерьерной сцены в Blender (5.x, Cycles).

Проектный скрипт сборки подключает библиотеку так:

    import sys; sys.path.insert(0, "<путь к скиллу>/scripts")
    import scene_lib as L
    L.init(root=ROOT, tex_dir=os.path.join(ROOT, "textures"))
    meshes = L.import_scene(path_to_glb)
    ...

Все размеры в метрах, ось Z вверх, координаты мировые (после import_scene трансформы применены,
поэтому координаты объекта = мировые, и box-проекция текстур работает без искажений).
"""
import bpy, bmesh, os, math, colorsys
from mathutils import Vector, Matrix

ROOT = TEX = None
COLL = None          # коллекция, куда кладётся вся добавленная геометрия
M = {}               # реестр материалов: ключ -> bpy.types.Material
HIDDEN = []          # имена объектов, скрытых правилами


def init(root, tex_dir=None, collection="Дизайн", factory_reset=True):
    global ROOT, TEX, COLL
    ROOT, TEX = root, tex_dir or os.path.join(root, "textures")
    if factory_reset:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    COLL = bpy.data.collections.new(collection)
    bpy.context.scene.collection.children.link(COLL)
    M.clear(); HIDDEN.clear()


# ------------------------------------------------------------------ импорт
def import_scene(path):
    """Импорт GLB/GLTF/FBX/OBJ; все меши делаются уникальными, родители снимаются, трансформы применяются."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    else:
        raise ValueError(f"Неизвестный формат: {path}")
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    for o in bpy.data.objects:
        o.select_set(o.type == "MESH")
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.object.make_single_user(object=True, obdata=True)
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return meshes


def parse(name):
    """Имена объектов из онлайн-планировщика: 'тип|objN|_экземпляр|...'. Возвращает (тип, часть, полное имя).
    У стен вторая часть — комната, в которую смотрит грань: 'wall|R_<id пола>|_<id>|sN' -> ('wall', 'R_<id>', ...).
    У полов — id пола: 'floor|_<id>|0' -> ('floor', '_<id>', ...)."""
    p = name.split("|")
    return p[0], (p[1] if len(p) > 1 else ""), name


def hide(o):
    o.hide_render = True; o.hide_viewport = True; HIDDEN.append(o.name)


def setmat(o, key):
    o.data.materials.clear(); o.data.materials.append(M[key])


def bounds(o):
    vs = [o.matrix_world @ Vector(c) for c in o.bound_box]
    return (Vector([min(v[i] for v in vs) for i in range(3)]), Vector([max(v[i] for v in vs) for i in range(3)]))


# ------------------------------------------------------------------ материалы
def new_mat(name):
    m = bpy.data.materials.new(name); m.use_nodes = True
    return m, m.node_tree.nodes, m.node_tree.links, m.node_tree.nodes["Principled BSDF"]


def solid(name, color, rough=0.5, metal=0.0, coat=0.0, sheen=0.0, sheen_tint=None):
    """color — ЛИНЕЙНЫЕ значения (не sRGB). Из sRGB образца переводить через srgb_to_linear()."""
    m, n, l, b = new_mat(name)
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    b.inputs["Coat Weight"].default_value = coat
    b.inputs["Sheen Weight"].default_value = sheen
    if sheen_tint:
        b.inputs["Sheen Tint"].default_value = (*sheen_tint, 1)
    return m


def emission(name, color, strength):
    m = bpy.data.materials.new(name); m.use_nodes = True
    n = m.node_tree.nodes; n.clear()
    e = n.new("ShaderNodeEmission"); e.inputs["Color"].default_value = (*color, 1)
    e.inputs["Strength"].default_value = strength
    o = n.new("ShaderNodeOutputMaterial"); m.node_tree.links.new(e.outputs[0], o.inputs[0])
    return m


def window_glass(name, reflect=0.06):
    """Оконное/душевое прозрачное стекло: Transparent + немного Glossy с ПОСТОЯННЫМ весом.
    Fresnel не использовать: на обратной стороне тонкого стекла он даёт полное отражение и стекло чернеет.
    Transparent пропускает теневые лучи, поэтому свет снаружи проходит без каустик."""
    m = bpy.data.materials.new(name); m.use_nodes = True
    n = m.node_tree.nodes; l = m.node_tree.links; n.clear()
    tr = n.new("ShaderNodeBsdfTransparent")
    gl = n.new("ShaderNodeBsdfGlossy"); gl.inputs["Roughness"].default_value = 0.02
    mx = n.new("ShaderNodeMixShader"); mx.inputs[0].default_value = reflect
    l.new(tr.outputs[0], mx.inputs[1]); l.new(gl.outputs[0], mx.inputs[2])
    o = n.new("ShaderNodeOutputMaterial"); l.new(mx.outputs[0], o.inputs[0])
    return m


def glass(name, color=(0.95, 0.95, 0.95), rough=0.02):
    """Стекло с преломлением (плафоны, вазы). Для окон — window_glass."""
    m, n, l, b = new_mat(name)
    b.inputs["Base Color"].default_value = (*color, 1); b.inputs["Transmission Weight"].default_value = 1.0
    b.inputs["Roughness"].default_value = rough; b.inputs["IOR"].default_value = 1.45
    return m


def fluted_glass(name, rib_period_cm=1.2):
    """Рифлёное стекло (душевая перегородка в плоскости YZ): вертикальные рёбра бампом."""
    m = glass(name, (0.82, 0.82, 0.8), 0.15)
    n, l, b = m.node_tree.nodes, m.node_tree.links, m.node_tree.nodes["Principled BSDF"]
    geo = n.new("ShaderNodeNewGeometry"); wv = n.new("ShaderNodeTexWave"); wv.wave_type = "BANDS"
    wv.bands_direction = "Y"; wv.wave_profile = "SIN"; wv.inputs["Scale"].default_value = 0.314 / (rib_period_cm / 100)
    l.new(geo.outputs["Position"], wv.inputs["Vector"])
    bp = n.new("ShaderNodeBump"); bp.inputs["Strength"].default_value = 1.0; bp.inputs["Distance"].default_value = 0.006
    l.new(wv.outputs["Fac"], bp.inputs["Height"]); l.new(bp.outputs["Normal"], b.inputs["Normal"])
    return m


def _image(nodes, path, colorspace="sRGB", box=True):
    t = nodes.new("ShaderNodeTexImage")
    t.image = bpy.data.images.load(path, check_existing=True)
    t.image.colorspace_settings.name = colorspace
    if box:
        t.projection = "BOX"; t.projection_blend = 0.15
    return t


def textured(name, prefix, size_m, tint=(1, 1, 1), rough_mul=1.0, bump=0.0, sheen=0.0, rot90=False,
             desat=0.0, sat=1.0, res="2k"):
    """PBR-набор Poly Haven (<prefix>_diff/_rough_<res>.jpg в TEX) с box-проекцией в мировых координатах.
    size_m — реальный размер текстуры (поле dimensions в api.polyhaven.com/info/<id>).
    Нужны только файлы _diff_ и _rough_ (карта нормалей не используется — её не скачивать).
    tint умножает цвет (можно >1). desat>0 — берётся только яркость × desat (у льна голубой оттенок).
    sat<1 — приглушить насыщенность (например, жёлтый камень -> бежевый). Насыщенность снижается ДО tint,
    поэтому tint по образцу считай с тем же sat: tint_for_swatch(образец, текстура, sat=sat)."""
    m, n, l, b = new_mat(name)
    tc = n.new("ShaderNodeTexCoord"); mp = n.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1 / size_m,) * 3
    if rot90:
        mp.inputs["Rotation"].default_value = (0, 0, math.pi / 2)
    l.new(tc.outputs["Object"], mp.inputs["Vector"])
    d = _image(n, os.path.join(TEX, f"{prefix}_diff_{res}.jpg")); l.new(mp.outputs["Vector"], d.inputs["Vector"])
    mix = n.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.blend_type = "MULTIPLY"
    mix.inputs["Factor"].default_value = 1.0; mix.inputs["B"].default_value = (*tint, 1)
    src = d.outputs["Color"]
    if desat:
        bw = n.new("ShaderNodeRGBToBW"); l.new(src, bw.inputs[0])
        k = n.new("ShaderNodeMath"); k.operation = "MULTIPLY"; k.inputs[1].default_value = desat
        l.new(bw.outputs[0], k.inputs[0]); src = k.outputs[0]
    if sat != 1.0:
        hs = n.new("ShaderNodeHueSaturation"); hs.inputs["Saturation"].default_value = sat
        l.new(src, hs.inputs["Color"]); src = hs.outputs["Color"]
    l.new(src, mix.inputs["A"]); l.new(mix.outputs["Result"], b.inputs["Base Color"])
    rp = os.path.join(TEX, f"{prefix}_rough_{res}.jpg")
    if os.path.exists(rp):
        r = _image(n, rp, "Non-Color"); l.new(mp.outputs["Vector"], r.inputs["Vector"])
        mr = n.new("ShaderNodeMath"); mr.operation = "MULTIPLY"; mr.inputs[1].default_value = rough_mul
        l.new(r.outputs["Color"], mr.inputs[0]); l.new(mr.outputs[0], b.inputs["Roughness"])
        if bump:
            bn = n.new("ShaderNodeBump"); bn.inputs["Strength"].default_value = bump; bn.inputs["Distance"].default_value = 0.002
            l.new(r.outputs["Color"], bn.inputs["Height"]); l.new(bn.outputs["Normal"], b.inputs["Normal"])
    b.inputs["Sheen Weight"].default_value = sheen
    return m


def srgb_to_linear(c):
    return tuple(((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92 for v in c)


def tint_for_swatch(swatch_srgb_mean, texture_srgb_mean, sat=1.0):
    """Множитель tint для textured(), чтобы средний цвет текстуры совпал с образцом из каталога.
    Средние sRGB-цвета считать по пикселям (scripts/swatch.py, см. references/materials.md).
    sat — то же значение, что передаёшь в textured(): средний цвет текстуры сначала приглушается так же
    (приближённо, по среднему цвету), и tint считается уже от него."""
    a, b = srgb_to_linear(swatch_srgb_mean), srgb_to_linear(texture_srgb_mean)
    if sat != 1.0:
        h, s_, v = colorsys.rgb_to_hsv(*b); b = colorsys.hsv_to_rgb(h, min(1.0, s_ * sat), v)
    return tuple(x / y for x, y in zip(a, b))


def _wall_coords(n, l, floor=False, source="position"):
    """Координаты для процедурной раскладки: стены вдоль X и вдоль Y -> (x+y, z); пол -> (x, y)."""
    if source == "position":
        geo = n.new("ShaderNodeNewGeometry"); out = geo.outputs["Position"]
    else:
        geo = n.new("ShaderNodeTexCoord"); out = geo.outputs["Object"]
    sp = n.new("ShaderNodeSeparateXYZ"); l.new(out, sp.inputs[0])
    cb = n.new("ShaderNodeCombineXYZ")
    if floor:
        l.new(sp.outputs[0], cb.inputs[0]); l.new(sp.outputs[1], cb.inputs[1])
    else:
        add = n.new("ShaderNodeMath"); add.operation = "ADD"
        l.new(sp.outputs[0], add.inputs[0]); l.new(sp.outputs[1], add.inputs[1])
        l.new(add.outputs[0], cb.inputs[0]); l.new(sp.outputs[2], cb.inputs[1])
    return cb.outputs[0], out


def tiles(name, width, height, c1, c2, floor=False, offset=0.0, joint=0.002, joint_color=(0.66, 0.64, 0.6),
          veins=None, rough=0.35, noise_scale=2.5):
    """Плитка/панели точного размера (width x height, м), шов joint. offset=0.5 — ряды со смещением.
    c1/c2 — линейные цвета (камень с лёгкой фактурой между ними). veins — цвет прожилок мрамора или None.
    Раскладка начинается от пола (z=0)."""
    m, n, l, b = new_mat(name)
    vec, pos = _wall_coords(n, l, floor)
    ns = n.new("ShaderNodeTexNoise"); ns.inputs["Scale"].default_value = noise_scale
    ns.inputs["Detail"].default_value = 12; ns.inputs["Distortion"].default_value = 0.4
    l.new(pos, ns.inputs["Vector"])
    rp = n.new("ShaderNodeValToRGB")
    rp.color_ramp.elements[0].position = 0.35; rp.color_ramp.elements[0].color = (*c1, 1)
    rp.color_ramp.elements[1].position = 0.7; rp.color_ramp.elements[1].color = (*c2, 1)
    l.new(ns.outputs["Fac"], rp.inputs["Fac"]); col = rp.outputs["Color"]
    if veins:
        vn = n.new("ShaderNodeTexNoise"); vn.inputs["Scale"].default_value = 1.3
        vn.inputs["Detail"].default_value = 8; vn.inputs["Distortion"].default_value = 4.0
        l.new(pos, vn.inputs["Vector"])
        m1 = n.new("ShaderNodeMath"); m1.operation = "SUBTRACT"; m1.inputs[1].default_value = 0.5
        m2 = n.new("ShaderNodeMath"); m2.operation = "ABSOLUTE"
        m3 = n.new("ShaderNodeMath"); m3.operation = "MULTIPLY"; m3.inputs[1].default_value = 45; m3.use_clamp = True
        m4 = n.new("ShaderNodeMath"); m4.operation = "SUBTRACT"; m4.inputs[0].default_value = 0.7
        l.new(vn.outputs["Fac"], m1.inputs[0]); l.new(m1.outputs[0], m2.inputs[0]); l.new(m2.outputs[0], m3.inputs[0])
        l.new(m3.outputs[0], m4.inputs[1])
        mv = n.new("ShaderNodeMix"); mv.data_type = "RGBA"; mv.inputs["B"].default_value = (*veins, 1)
        l.new(m4.outputs[0], mv.inputs["Factor"]); l.new(col, mv.inputs["A"]); col = mv.outputs["Result"]
    dk = n.new("ShaderNodeMix"); dk.data_type = "RGBA"; dk.blend_type = "MULTIPLY"
    dk.inputs["Factor"].default_value = 1.0; dk.inputs["B"].default_value = (0.96, 0.95, 0.94, 1)
    l.new(col, dk.inputs["A"])
    br = n.new("ShaderNodeTexBrick"); br.offset = offset; br.offset_frequency = 2; br.squash = 1.0
    br.inputs["Scale"].default_value = 1.0; br.inputs["Brick Width"].default_value = width
    br.inputs["Row Height"].default_value = height; br.inputs["Mortar Size"].default_value = joint
    br.inputs["Mortar"].default_value = (*joint_color, 1); br.inputs["Bias"].default_value = 0.0
    l.new(vec, br.inputs["Vector"]); l.new(col, br.inputs["Color1"]); l.new(dk.outputs["Result"], br.inputs["Color2"])
    l.new(br.outputs["Color"], b.inputs["Base Color"])
    bp = n.new("ShaderNodeBump"); bp.invert = True; bp.inputs["Strength"].default_value = 0.4; bp.inputs["Distance"].default_value = 0.001
    l.new(br.outputs["Fac"], bp.inputs["Height"]); l.new(bp.outputs["Normal"], b.inputs["Normal"])
    b.inputs["Roughness"].default_value = rough
    return m


def marble_floor(name, tile=0.6, base=(0.9, 0.89, 0.87), vein=(0.55, 0.55, 0.55)):
    """Светлый мрамор плиткой tile x tile с серыми прожилками (процедурно, для пола)."""
    m, n, l, b = new_mat(name)
    tc = n.new("ShaderNodeTexCoord")
    ns = n.new("ShaderNodeTexNoise"); ns.inputs["Scale"].default_value = 1.2; ns.inputs["Detail"].default_value = 10
    ns.inputs["Distortion"].default_value = 3.0; l.new(tc.outputs["Object"], ns.inputs["Vector"])
    rp = n.new("ShaderNodeValToRGB"); cr = rp.color_ramp
    cr.elements[0].position = 0.47; cr.elements[1].position = 0.5
    cr.elements[0].color = (*base, 1); cr.elements[1].color = (*base, 1)
    mid = cr.elements.new(0.485); mid.color = (*vein, 1)
    l.new(ns.outputs["Fac"], rp.inputs["Fac"])
    br = n.new("ShaderNodeTexBrick"); br.offset = 0.0; br.inputs["Scale"].default_value = 1.0
    br.inputs["Brick Width"].default_value = tile; br.inputs["Row Height"].default_value = tile
    br.inputs["Mortar Size"].default_value = 0.0015; br.inputs["Mortar"].default_value = (0.6, 0.6, 0.6, 1)
    l.new(tc.outputs["Object"], br.inputs["Vector"]); l.new(rp.outputs["Color"], br.inputs["Color1"])
    l.new(rp.outputs["Color"], br.inputs["Color2"]); l.new(br.outputs["Color"], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 0.12
    return m


def photo_planks(name, image_path, tile_u, tile_v, along="Y", rough=0.45):
    """Пол из фото образца каталога (ламинат/паркет). Фото заранее обрезать до ЦЕЛОГО числа рядов досок
    (см. references/materials.md), иначе на стыке повторов появится двойная доска.
    tile_u — длина фото вдоль досок, tile_v — поперёк (м). along — ось, вдоль которой идут доски."""
    m, n, l, b = new_mat(name)
    geo = n.new("ShaderNodeNewGeometry"); sp = n.new("ShaderNodeSeparateXYZ"); l.new(geo.outputs["Position"], sp.inputs[0])
    du = n.new("ShaderNodeMath"); du.operation = "DIVIDE"; du.inputs[1].default_value = tile_u
    dv = n.new("ShaderNodeMath"); dv.operation = "DIVIDE"; dv.inputs[1].default_value = tile_v
    a, c = (1, 0) if along == "Y" else (0, 1)
    l.new(sp.outputs[a], du.inputs[0]); l.new(sp.outputs[c], dv.inputs[0])
    cb = n.new("ShaderNodeCombineXYZ"); l.new(du.outputs[0], cb.inputs[0]); l.new(dv.outputs[0], cb.inputs[1])
    t = n.new("ShaderNodeTexImage"); t.image = bpy.data.images.load(image_path, check_existing=True)
    l.new(cb.outputs[0], t.inputs["Vector"]); l.new(t.outputs["Color"], b.inputs["Base Color"])
    bw = n.new("ShaderNodeRGBToBW"); l.new(t.outputs["Color"], bw.inputs[0])
    bp = n.new("ShaderNodeBump"); bp.invert = True; bp.inputs["Strength"].default_value = 0.15
    l.new(bw.outputs[0], bp.inputs["Height"]); l.new(bp.outputs["Normal"], b.inputs["Normal"])
    b.inputs["Roughness"].default_value = rough
    return m


def photo_wallpaper(name, image_path, x, y0, y1, z0, z1, img_left_y=None, img_bottom_z=0.0, img_h=None):
    """Фотообои на стене в плоскости x=const (смотрит в +X). Картинка высотой img_h (по умолчанию вся стена),
    левый край в img_left_y (по умолчанию правый край картинки = y1, лишнее срезается слева).
    За краями картинки продолжаются краевые пиксели (EXTEND). Для стены вдоль Y другой ориентации
    поменять порядок y0/y1."""
    img = bpy.data.images.load(image_path, check_existing=True)
    img_h = img_h or (z1 - z0)
    w_img = img_h * img.size[0] / img.size[1]
    if img_left_y is None:
        img_left_y = y1 - w_img
    me = bpy.data.meshes.new(name); corners = [(y0, z0), (y1, z0), (y1, z1), (y0, z1)]
    me.from_pydata([(x, y, z) for y, z in corners], [], [(0, 1, 2, 3)])
    uv = me.uv_layers.new(name="UVMap")
    for li, (y, z) in enumerate(corners):
        uv.data[li].uv = ((y - img_left_y) / w_img, (z - img_bottom_z) / img_h)
    m, n, l, b = new_mat(name)
    t = n.new("ShaderNodeTexImage"); t.image = img; t.extension = "EXTEND"
    l.new(t.outputs["Color"], b.inputs["Base Color"]); b.inputs["Roughness"].default_value = 0.85
    o = bpy.data.objects.new(name, me); COLL.objects.link(o); o.data.materials.append(m)
    return o


# ------------------------------------------------------------------ геометрия
def _link(o):
    for c in list(o.users_collection):
        c.objects.unlink(o)
    COLL.objects.link(o)
    return o


def box(name, x0, x1, y0, y1, z0, z1, mat):
    me = bpy.data.meshes.new(name); bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new(name, me); COLL.objects.link(o)
    o.location = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2); o.scale = (x1 - x0, y1 - y0, z1 - z0)
    o.data.materials.append(M[mat]); return o


def ceilings_from_floors(meshes, height, mat, prefix="floor|"):
    """Потолки по точным контурам полов планировщика (любой формы, без перекрытий — полы не перекрываются).
    Пол планировщика — плита со скруглёнными кромками: берутся только верхние грани, иначе после подъёма
    верх и низ плиты совпадут и потолок станет чёрным. Возвращает список объектов-потолков."""
    out = []
    for o in meshes:
        if not o.name.startswith(prefix):
            continue
        bm = bmesh.new(); bm.from_mesh(o.data); bm.transform(o.matrix_world)
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.normal.z < 0.99], context="FACES")
        for v in bm.verts:
            v.co.z = height
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])          # нормалью вниз
        me = bpy.data.meshes.new("Потолок"); bm.to_mesh(me); bm.free(); me.materials.append(M[mat])
        c = bpy.data.objects.new("Потолок_" + parse(o.name)[1], me); COLL.objects.link(c); out.append(c)
    return out


def plane(name, x0, x1, y0, y1, z, mat, down=True):
    """Горизонтальная плоскость (потолок down=True / пол down=False).
    Соседние плоскости НЕ должны перекрываться: совпадающие поверхности затеняют друг друга -> чёрные пятна."""
    me = bpy.data.meshes.new(name)
    me.from_pydata([(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)], [], [(0, 1, 2, 3) if down else (3, 2, 1, 0)])
    o = bpy.data.objects.new(name, me); COLL.objects.link(o); o.data.materials.append(M[mat]); return o


def cyl(name, x, y, z0, z1, r, mat, verts=16):
    bpy.ops.mesh.primitive_cylinder_add(radius=r, depth=z1 - z0, location=(x, y, (z0 + z1) / 2), vertices=verts)
    o = bpy.context.object; o.name = name; _link(o); o.data.materials.append(M[mat]); return o


def blob(name, x, y, zc, r, sz, mat, verts=48):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=(x, y, zc), segments=verts, ring_count=verts // 2)
    o = bpy.context.object; o.name = name; o.scale = (1, 1, sz); bpy.ops.object.shade_smooth()
    _link(o); o.data.materials.append(M[mat]); return o


def bevel(o, w=0.025, seg=3, soft=False):
    """Скругление в МЕТРАХ: сначала масштаб объекта переносится в меш, иначе скругление искажается масштабом.
    soft=True — добавляет подразделение: мягкая «надутая» подушка."""
    o.data.transform(Matrix.Diagonal((*o.scale, 1.0))); o.scale = (1, 1, 1)
    md = o.modifiers.new("Скругление", "BEVEL"); md.width = w; md.segments = seg; md.limit_method = "NONE"
    if soft:
        ss = o.modifiers.new("Мягкость", "SUBSURF"); ss.levels = 2; ss.render_levels = 2
    return o


def empty(name, loc, rot_deg=(0, 0, 0)):
    e = bpy.data.objects.new(name, None); COLL.objects.link(e)
    e.location = loc; e.rotation_euler = tuple(math.radians(a) for a in rot_deg); return e


def child(o, parent):
    o.parent = parent; return o


def stadium(name, cx, y0, y1, cz, w, h, mat, seg=48):
    """Капсула (овальное зеркало) в плоскости XZ, толщина по Y от y0 до y1."""
    r = w / 2; off = (h - w) / 2; pts = []
    for i in range(seg + 1):
        a = math.pi * i / seg; pts.append((cx + r * math.cos(a), cz + off + r * math.sin(a)))
    for i in range(seg + 1):
        a = math.pi + math.pi * i / seg; pts.append((cx + r * math.cos(a), cz - off + r * math.sin(a)))
    bm = bmesh.new(); f = bm.faces.new([bm.verts.new((x, y0, z)) for x, z in pts])
    ext = bmesh.ops.extrude_face_region(bm, geom=[f])
    bmesh.ops.translate(bm, vec=(0, y1 - y0, 0), verts=[v for v in ext["geom"] if isinstance(v, bmesh.types.BMVert)])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new(name, me); COLL.objects.link(o); o.data.materials.append(M[mat]); return o


def basin(name, x0, x1, y0, y1, z0, z1, inset, depth, mat):
    """Плита-раковина с выемкой: верхняя грань вдавливается внутрь с наклонными стенками."""
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector((x0 + (v.co.x + .5) * (x1 - x0), y0 + (v.co.y + .5) * (y1 - y0), z0 + (v.co.z + .5) * (z1 - z0)))
    top = [f for f in bm.faces if f.normal.z > 0.9][0]
    bmesh.ops.inset_individual(bm, faces=[top], thickness=inset)
    for v in top.verts:
        v.co.z -= depth
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    o = bpy.data.objects.new(name, me); COLL.objects.link(o); o.data.materials.append(M[mat]); return o


def flatten_front(o, gap=0.0015, min_thick=0.016):
    """Рамочный (shaker) фасад из каталога -> гладкая панель того же габарита с зазором 3 мм по периметру."""
    vs = [v.co for v in o.data.vertices]
    mn = Vector([min(v[i] for v in vs) for i in range(3)]); mx = Vector([max(v[i] for v in vs) for i in range(3)])
    size = mx - mn; thin = min(range(3), key=lambda i: size[i])
    for i in range(3):
        if i != thin: mn[i] += gap; mx[i] -= gap
        else: mx[i] = max(mx[i], mn[i] + min_thick)
    bm = bmesh.new(); bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector([mn[i] + (v.co[i] + 0.5) * (mx[i] - mn[i]) for i in range(3)])
    mats = list(o.data.materials); old = o.data
    me = bpy.data.meshes.new(o.name + "_flat"); bm.to_mesh(me); bm.free()
    for m_ in mats: me.materials.append(m_)
    o.data = me; bpy.data.meshes.remove(old)


def curtain_bunch(name, x0, x1, y, z0, z1, mat, folds=7, amp=0.045, seg=8):
    """Штора, собранная складками (открытая), вдоль X на карнизе y. Однотонный материал — текстура на складках даёт рисунок."""
    n = folds * seg; verts = []; faces = []
    for i in range(n + 1):
        t = i / n; x = x0 + (x1 - x0) * t; dy = amp * math.sin(2 * math.pi * folds * t)
        verts += [(x, y + dy, z0), (x, y + dy, z1)]
    for i in range(n):
        a = 2 * i; faces.append((a, a + 2, a + 3, a + 1))
    me = bpy.data.meshes.new(name); me.from_pydata(verts, [], faces)
    for p in me.polygons: p.use_smooth = True
    o = bpy.data.objects.new(name, me); COLL.objects.link(o); o.data.materials.append(M[mat])
    md = o.modifiers.new("Толщина", "SOLIDIFY"); md.thickness = 0.004
    return o


def blinds(name, x0, x1, y, z0, z1, mat, tilt_deg=30, pitch=0.048):
    """Горизонтальные жалюзи в проёме вдоль X на глубине y (ламели приоткрыты на tilt_deg)."""
    box(f"{name}_карниз", x0, x1, y - 0.03, y + 0.03, z1, z1 + 0.06, mat)
    z = z1 - 0.05; i = 0
    while z > z0:
        s = box(f"{name}_ламель_{i}", x0 + 0.01, x1 - 0.01, y - 0.025, y + 0.025, z - 0.0015, z + 0.0015, mat)
        s.rotation_euler = (math.radians(tilt_deg), 0, 0); z -= pitch; i += 1


def floor_grille(name, x0, x1, y0, y1, frame_mat="graphite", slot_mat="black"):
    """Встроенный в пол конвектор: решётка заподлицо с полом."""
    box(f"{name}_канал", x0, x1, y0, y1, 0.0, 0.0008, slot_mat)
    for s in ((x0, x0 + 0.012, y0, y1), (x1 - 0.012, x1, y0, y1), (x0, x1, y0, y0 + 0.012), (x0, x1, y1 - 0.012, y1)):
        box(f"{name}_рамка_{s[0]:.2f}_{s[2]:.2f}", *s, 0.0, 0.0025, frame_mat)
    for k in range(int((x1 - x0 - 0.024) / 0.016)):
        x = x0 + 0.017 + k * 0.016
        box(f"{name}_ламель_{k}", x, x + 0.006, y0 + 0.012, y1 - 0.012, 0.0, 0.0022, frame_mat)


def import_asset(gltf_path, keep, loc, rot_deg=0, scale=1.0, mats=None, anchor="bottom"):
    """Импорт модели (GLTF Poly Haven). keep — префиксы имён мешей, которые оставить (варианты a/b/c...).
    Модель ставится нижней (anchor='bottom') или верхней ('top', люстра/вентилятор) точкой в loc.
    mats — {префикс_имени: ключ_материала}; порядок важен: более длинный префикс ставить ПОСЛЕ короткого."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=gltf_path)
    new = [o for o in bpy.data.objects if o not in before]
    kept = [o for o in new if o.type == "MESH" and any(o.name.startswith(k) for k in keep)]
    for o in kept:
        mw = o.matrix_world.copy(); o.parent = None; o.matrix_world = mw
    for o in new:
        if o not in kept: bpy.data.objects.remove(o, do_unlink=True)
    vs = [o.matrix_world @ Vector(c) for o in kept for c in o.bound_box]
    mn = Vector([min(v[i] for v in vs) for i in range(3)]); mx = Vector([max(v[i] for v in vs) for i in range(3)])
    piv = Vector(((mn.x + mx.x) / 2, (mn.y + mx.y) / 2, mn.z if anchor == "bottom" else mx.z))
    e = bpy.data.objects.new(os.path.basename(gltf_path).split(".")[0] + f"_{loc[0]:.1f}_{loc[1]:.1f}", None)
    COLL.objects.link(e); e.location = piv
    for o in kept:
        _link(o); o.parent = e; o.matrix_parent_inverse = e.matrix_world.inverted()
        for prefix, key in (mats or {}).items():
            if o.name.startswith(prefix):
                for i in range(len(o.data.materials)): o.data.materials[i] = M[key]
    e.location = loc; e.rotation_euler = (0, 0, math.radians(rot_deg)); e.scale = (scale,) * 3
    return e


# ------------------------------------------------------------------ свет
def light(name, kind, loc, energy, color=(1, 0.85, 0.7), size=0.05, rot=(0, 0, 0), spot=None, sizey=None, aim=None):
    ld = bpy.data.lights.new(name, kind); ld.energy = energy; ld.color = color
    if kind == "AREA":
        ld.shape = "RECTANGLE"; ld.size = size; ld.size_y = sizey or size
    elif kind in ("POINT", "SPOT"):
        ld.shadow_soft_size = size
    if spot:
        ld.spot_size = math.radians(spot); ld.spot_blend = 0.6
    o = bpy.data.objects.new(name, ld); COLL.objects.link(o); o.location = loc; o.rotation_euler = rot
    if aim is not None:
        o.rotation_euler = (Vector(aim) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return o


def fill_light(name, x, y, sx, sy, energy, ceiling_z=3.0):
    """Мягкий заполняющий свет под потолком, невидимый камере и отражениям."""
    o = light(name, "AREA", (x, y, ceiling_z - 0.03), energy, color=(1, 0.92, 0.84), size=sx, sizey=sy)
    o.visible_camera = False; o.visible_glossy = False
    return o


def track_spots(prefix, x0, y0, x1, y1, n, energy, ceiling_z=3.0, spot=55, mat="black"):
    """Чёрный трек на потолке со спотами, светящими вниз."""
    if abs(x1 - x0) > abs(y1 - y0):
        box(prefix, x0, x1, y0 - 0.015, y0 + 0.015, ceiling_z - 0.045, ceiling_z, mat)
    else:
        box(prefix, x0 - 0.015, x0 + 0.015, y0, y1, ceiling_z - 0.045, ceiling_z, mat)
    for i in range(n):
        f = (i + 0.5) / n; x = x0 + (x1 - x0) * f; y = y0 + (y1 - y0) * f
        cyl(f"{prefix}_спот_{i}", x, y, ceiling_z - 0.2, ceiling_z - 0.045, 0.028, mat)
        light(f"{prefix}_свет_{i}", "SPOT", (x, y, ceiling_z - 0.21), energy, color=(1, 0.84, 0.66), size=0.02, spot=spot)


def sky_world(horizon=(0.85, 0.87, 0.88), zenith=(0.42, 0.58, 0.82), strength=1.0):
    """Небо градиентом (предсказуемее Preetham/Nishita для интерьеров)."""
    sc = bpy.context.scene; w = bpy.data.worlds.new("Небо"); sc.world = w; w.use_nodes = True
    n, l = w.node_tree.nodes, w.node_tree.links; bg = n["Background"]
    gc = n.new("ShaderNodeTexCoord"); gs = n.new("ShaderNodeSeparateXYZ"); l.new(gc.outputs["Generated"], gs.inputs[0])
    gr = n.new("ShaderNodeValToRGB"); l.new(gs.outputs[2], gr.inputs["Fac"])
    gr.color_ramp.elements[0].position = 0.5; gr.color_ramp.elements[0].color = (*horizon, 1)
    gr.color_ramp.elements[1].position = 1.0; gr.color_ramp.elements[1].color = (*zenith, 1)
    l.new(gr.outputs["Color"], bg.inputs[0]); bg.inputs[1].default_value = strength
    return w


def sun(energy=3.5, elevation_deg=38, azimuth_deg=-15, color=(1, 0.93, 0.82)):
    o = light("Солнце", "SUN", (0, 0, 10), energy, color=color)
    o.rotation_euler = (math.radians(90 - elevation_deg), 0, math.radians(azimuth_deg)); o.data.angle = math.radians(2)
    return o


# ------------------------------------------------------------------ камеры и рендер
def camera(name, loc, target=None, lens=18, ortho_scale=None):
    cd = bpy.data.cameras.new(name); cd.lens = lens; cd.sensor_width = 36; cd.clip_start = 0.05
    o = bpy.data.objects.new(name, cd); COLL.objects.link(o); o.location = loc
    if ortho_scale:
        cd.type = "ORTHO"; cd.ortho_scale = ortho_scale; o.rotation_euler = (0, 0, 0)
    elif target is not None:
        o.rotation_euler = (Vector(target) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return o


def use_gpu():
    """Включить GPU для Cycles: Metal на Mac, иначе OptiX/CUDA/HIP/oneAPI; без GPU — CPU.
    kernel_optimization_level=OFF обязателен для Blender 5.2.x на Metal: фоновая компиляция
    специализированных ядер роняет Blender (NSURL nil в MetalKernelPipeline::compile)."""
    sc = bpy.context.scene
    prefs = bpy.context.preferences.addons["cycles"].preferences
    if hasattr(prefs, "kernel_optimization_level"):
        prefs.kernel_optimization_level = "OFF"   # ДО get_devices: иначе Metal успевает начать компиляцию с FULL
    for dev in ("METAL", "OPTIX", "CUDA", "HIP", "ONEAPI"):
        try:
            prefs.compute_device_type = dev; prefs.get_devices()
            if any(d.type != "CPU" for d in prefs.devices): break
        except TypeError:
            continue
    for d in prefs.devices: d.use = True
    if hasattr(prefs, "kernel_optimization_level"):
        prefs.kernel_optimization_level = "OFF"
    sc.cycles.device = "GPU" if any(d.type != "CPU" for d in prefs.devices) else "CPU"


def setup_cycles(samples=256, exposure=0.0, look="AgX - Medium High Contrast", max_bounces=8):
    """Cycles на GPU (см. use_gpu), шумодав, AgX."""
    sc = bpy.context.scene; sc.render.engine = "CYCLES"
    use_gpu()
    sc.cycles.samples = samples; sc.cycles.use_denoising = True
    sc.cycles.max_bounces = max_bounces
    sc.cycles.caustics_reflective = False; sc.cycles.caustics_refractive = False
    sc.view_settings.view_transform = "AgX"; sc.view_settings.look = look; sc.view_settings.exposure = exposure
    sc.render.image_settings.file_format = "PNG"


def render_views(views, which, out_dir, res=1600, groups=None):
    """views: {имя: (позиция, цель или None для плана, объектив, [группы_скрыть])}.
    which: список имён/префиксов/суффиксов или ['all']. Точное имя важнее префикса; если префиксу подходят
    несколько видов, берётся первый и печатается предупреждение. Имена с '_bath_' и план — вертикально (3:4).
    groups: {имя_группы: [объекты]} — скрываются только на своих видах."""
    sc = bpy.context.scene; groups = groups or {}
    cams = {}
    for k, (loc, tgt, lens, _) in views.items():
        cams[k] = camera("Кам_" + k, loc, (0, 0, 0), 50, ortho_scale=lens) if tgt is None else camera("Кам_" + k, loc, tgt, lens)
    if which == ["all"]:
        which = list(views)
    os.makedirs(out_dir, exist_ok=True); done = []
    for w in which:
        if w in views:
            view = w
        else:
            cand = [k for k in views if k.startswith(w) or k.endswith("_" + w)]
            if not cand:
                raise KeyError(f"Нет вида '{w}'. Есть: {', '.join(views)}")
            if len(cand) > 1:
                print(f"ВНИМАНИЕ: '{w}' подходит к {cand}; рендерю {cand[0]}, остальные — полным именем")
            view = cand[0]
        sc.camera = cams[view]
        hide_now = {o for g in views[view][3] for o in groups.get(g, [])}
        for g in groups.values():
            for o in g: o.hide_render = o in hide_now
        portrait = views[view][1] is None or "_bath_" in view
        sc.render.resolution_x = int(res * 0.75) if portrait else res
        sc.render.resolution_y = res if portrait else int(res * 0.75)
        sc.render.filepath = os.path.join(out_dir, f"{view}.png")
        bpy.ops.render.render(write_still=True); done.append(sc.render.filepath)
        print("RENDERED", sc.render.filepath)
    for g in groups.values():
        for o in g: o.hide_render = False
    return cams, done
