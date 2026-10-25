#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Waving cover: a CROM module. The album cover waves gently like a hanging cloth.

A port of the NULL VOID CurtainWave node (ComfyUI) the module grew out of. It only moves pixels
geometrically and adds mild fold shading; no pixel is invented, so the logo and the album title
are never damaged. "pin:" boxes stay perfectly still (the source pixel for pixel), other boxes
move as one rigid piece so the letters do not bend.

The video is a loop: every segment (wave variant) starts and ends in the same state, so segments
with different variants can be chained in any order without a visible cut.

  python modules/waving_cover.py stills recipe.json folder [--times 0,2.5,5] [--res 1080x1080]
  python modules/waving_cover.py render recipe.json folder [--res 1080x1080] [--draft] [--project projects/demo]
"""
import math
import os
import random
import re
import sys
import threading
from collections import OrderedDict

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
if APP not in sys.path:
    sys.path.insert(0, APP)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import core as E  # noqa: E402

MODULE_ID = "waving_cover"
MODULE_LABEL = "Waving cover"
ENGINE_VERSION = 1
MODULE_ORDER = 30
MODULE_DESC = "The album cover waves gently like a hanging cloth. The logo and title stay intact, the video loops."
AI_DESC = ("A still album cover waves gently like a hanging cloth (pinned at the top, more motion towards the "
           "bottom, folds travelling down). Pure geometric warping plus mild fold shading, nothing is redrawn. "
           "'boxes' are rectangles in cover pixels: 'pin:x0,y0,x1,y1' stays perfectly still (use it for the band "
           "logo and the album title), plain 'x0,y0,x1,y1' moves rigidly without bending. The video is a chain of "
           "seamless loop segments; 'order' lists the wave variant of each segment.")
RENDER_MSG = "Preparing the cover waves"

# (fx, fy, cycles per segment, amplitude, phase); fx / fy in cycles per cover height
BASE_WAVES = [
    (0.00, 0.45, 1, 0.40, 0.0),   # whole cloth sway, travelling down
    (1.20, 0.80, 1, 0.35, 1.3),   # main folds
    (-2.10, 1.30, 2, 0.17, 2.1),  # secondary folds
    (3.40, 2.30, 3, 0.08, 0.7),   # fine ripple
]
LIGHT = (-0.6, -0.8)  # from the upper left
DEMO_BOXES = "pin:300,90,900,330"
DEMO_COVER = "cover.png"
# One keyframe (the loop point) and flat I/P quality: a keyframe in the middle shows as a texture
# "pulse" on fine hatching (measured on the original cover when the node was built).
X264_EXTRA = ["-tune", "grain", "-x264-params", "ipratio=1.0:pbratio=1.0:no-mbtree=1:keyint=infinite:scenecut=0"]
RES = E.RES_ALL + [["1200x1200", "Square 1200×1200"]]


def variation_waves(seed):
    rng = random.Random(seed)
    waves = []
    for amp in (0.30, 0.18, 0.10):
        fx = rng.uniform(0.7, 3.2) * rng.choice((-1.0, 1.0))
        fy = rng.uniform(0.5, 2.2)
        cycles = rng.uniform(0.8, 3.0)  # windowed by the gust, so it need not be a whole number
        waves.append((fx, fy, cycles, amp, rng.uniform(0.0, 2.0 * math.pi)))
    return waves, rng.uniform(-0.6, 0.6), rng.uniform(0.0, 2.0 * math.pi)


def parse_boxes(text):
    """One box per line (or ;): 'x0,y0,x1,y1' moves rigidly, 'pin:x0,y0,x1,y1' stays put."""
    boxes, bad = [], 0
    for part in str(text or "").replace("\n", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        pinned = part.lower().startswith("pin:")
        if pinned:
            part = part[4:]
        try:
            nums = [float(n) for n in part.replace(" ", "").split(",") if n]
        except ValueError:
            nums = []
        if len(nums) == 4 and nums[2] > nums[0] and nums[3] > nums[1]:
            boxes.append((nums, pinned))
        else:
            bad += 1
    return boxes, bad


def parse_order(text):
    out = [int(n) for n in re.findall(r"\d+", str(text or ""))][:200]
    return [min(n, 99999) for n in out] or [1]


# ----------------------------------------------------------------------------------------------
# Parameters
# ----------------------------------------------------------------------------------------------

SCHEMA = [
    {"id": "cover", "label": "Cover", "params": [
        E._p("cover", "Cover image", "asset", DEMO_COVER, "geo", kind="cover",
             hint="An image from the project's covers folder. Add new ones with the upload button."),
        E._p("fit", "Fit", "select", "contain", "geo",
             options=[["contain", "Whole cover in frame"], ["cover", "Fill the frame (crops the edges)"]]),
        E.F("size", "Cover size", 100.0, 20, 150, 1, "%", "geo"),
        E._p("bg_color", "Colour around the cover", "color", "#000000",
             hint="The area the cover does not reach. With a transparent background it stays transparent."),
    ]},
    {"id": "wave", "label": "Waving", "params": [
        E.F("amp", "Sideways wave strength", 1.0, 0, 8, 0.05, "% of height",
            hint="1 % = 12 px on a 1200 px cover (the original gentle version)."),
        E.F("vert", "Up and down motion", 0.25, 0, 4, 0.05, "% of height"),
        E.F("stiff", "Stiffness at the top", 1.8, 0.3, 6, 0.1, "", "geo",
            hint="A bigger number = the top moves less and the motion is lower down."),
        E.F("top_motion", "Motion at the very top", 0.03, 0, 1, 0.01, "", "geo",
            hint="0 = the top edge is as if nailed down, 1 = the whole cover moves the same."),
        E.F("gust", "Wind gusts", 0.6, 0, 3, 0.05, hint="Strength of the random waves in the middle of each segment."),
        E.F("shading", "Fold shading", 0.10, 0, 0.5, 0.01),
    ]},
    {"id": "locks", "label": "Protect logo and title", "params": [
        E._p("boxes", "Protected boxes", "textarea", DEMO_BOXES, "geo",
             hint="In cover pixels, one per line: x0,y0,x1,y1. With pin: in front it stays perfectly still "
                  "(logo, album title); without it, it moves as one piece and does not bend."),
        E.F("feather", "Soft edge around", 40.0, 1, 400, 1, "cover px", "geo"),
    ]},
    {"id": "loop", "label": "Loop", "params": [
        E.F("loop", "Segment length", 5.0, 1, 30, 0.5, "s", hint="Each segment is one wave; it starts and ends the same."),
        E._p("order", "Variant order", "text", "1 6 4 2 5 3 1 5 4 6 3 2",
             hint="Wave variant numbers, one segment per number. 12 segments of 5 s = 60 s."),
    ]},
    {"id": "output", "label": "Output", "params": E.output_params(
        seed=0, seed_hint="Added to every variant number. 0 = the original waves.",
        res_options=RES, res_default="1080x1080", bg=True,
        bg_hint="Transparent background: only the cover remains, the area around it is transparent.")},
]

PRESETS = [
    {"id": "gentle", "label": "Gentle (the delivered version)", "params": {}},
    {"id": "stronger", "label": "Stronger", "params": {
        "amp": 1.5, "vert": 1 / 3, "stiff": 1.6, "top_motion": 0.03, "gust": 0.7, "shading": 0.14,
        "order": "4 5 6 2 1 3 5 4 2 1 6 3"}},
    {"id": "calm", "label": "Almost still", "params": {"amp": 0.45, "vert": 0.1, "gust": 0.3, "shading": 0.05}},
]

LANES = []

PARAMS = E.params_of(SCHEMA)
LEVELS = ("geo",)


def defaults():
    return E.defaults_of(PARAMS)


def merge(params=None):
    return E.merge_into(PARAMS, params)


def level_key(p, levels):
    return E.level_key_of(PARAMS, p, levels)


def seg_frames(p):
    return max(2, int(round(float(p["loop"]) * int(p["fps"]))))


def timeline(p):
    p = merge(p)
    fps = int(p["fps"])
    n = seg_frames(p)
    order = parse_order(p["order"])
    L = n / fps
    phases = [{"id": f"s{k}", "label": f"Wave {v}", "t0": round(k * L, 4), "t1": round((k + 1) * L, 4), "video": 1}
              for k, v in enumerate(order)]
    return {"total": len(order) * L, "t1": len(order) * L, "fps": fps, "phases": phases,
            "markers": [{"t": round(k * L, 3), "label": f"segment {k + 1}"} for k in range(1, len(order))]}


def _stem(p):
    return E._ascii_slug(os.path.splitext(str(p.get("cover") or "cover"))[0], "cover")


def default_name(p):
    return f"Waving {os.path.splitext(str(p.get('cover') or ''))[0] or 'cover'}"


def file_slug(p):
    return f"{_stem(p)}_wave"


def still_time(p):
    return 0.0


def load_recipe(path):
    return E.read_recipe(path, MODULE_ID, merge)


def recipe_doc(name, params):
    return E.make_recipe_doc(name, params, MODULE_ID, ENGINE_VERSION, merge)


# ----------------------------------------------------------------------------------------------
# Renderer
# ----------------------------------------------------------------------------------------------

_IMG = OrderedDict()
_IMG_LOCK = threading.Lock()


def load_cover(path):
    """Cover as float32 RGB 0..1, cached by path and modification time."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    with _IMG_LOCK:
        if key in _IMG:
            _IMG.move_to_end(key)
            return _IMG[key]
    img = E.read_image(path, cv2.IMREAD_COLOR)
    if img is None:
        return None
    rgb = np.ascontiguousarray(img[..., ::-1]).astype(np.float32) / 255.0
    with _IMG_LOCK:
        _IMG[key] = rgb
        while len(_IMG) > 4:
            _IMG.popitem(last=False)
    return rgb


class Renderer:
    def __init__(self, params, W, H, ctx=None):
        self.ctx = ctx
        self.W, self.H = int(W), int(H)
        self.geo_key = None
        self.var_cache = OrderedDict()
        self.warn = []
        self.cover_name = None
        self.update(params)

    # ---------- parameters ----------
    def update(self, params):
        p = merge(params)
        self.p = p
        self.fps = int(p["fps"])
        self.n = seg_frames(p)
        self.order = parse_order(p["order"])
        self.N = self.n * len(self.order)
        self.T_end = self.T1 = self.N / self.fps
        self.mode = E.out_kind(p)
        self.key = E.key_rgb(p)
        c = str(p.get("bg_color") or "#000000")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", c):
            c = "#000000"
        self.bg = np.array([int(c[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255.0
        gk = (level_key(p, LEVELS), self.W, self.H, getattr(self.ctx, "dir", None))
        if gk != self.geo_key:
            self._build_geo(p)
            self.geo_key = gk

    def _cover_path(self, p):
        self.warn = []
        fp = E.project_asset(self.ctx, "cover", p["cover"])
        if fp is not None:
            self.cover_name = p["cover"]
            return fp
        self.cover_name = None
        lst = self.ctx.assets("cover") if self.ctx is not None and hasattr(self.ctx, "assets") else []
        if lst:
            self.cover_name = lst[0]
            self.warn.append(f"The cover {p['cover']} is not in the project, using {lst[0]}.")
            return self.ctx.asset_path("cover", lst[0])
        self.warn.append("The project has no cover yet. Add an image with the upload button.")
        return None

    def _build_geo(self, p):
        fp = self._cover_path(p)
        img = load_cover(fp) if fp else None
        self.var_cache.clear()
        self.locks = []
        if img is None:
            if fp:
                self.warn.append("The cover could not be read.")
            self.src = None
            return
        h0, w0 = img.shape[:2]
        W, H = self.W, self.H
        s = (min if p["fit"] == "contain" else max)(W / w0, H / h0) * float(p["size"]) / 100.0
        cw, ch = max(2, int(round(w0 * s))), max(2, int(round(h0 * s)))
        self.sx, self.sy = cw / w0, ch / h0
        if (cw, ch) == (w0, h0):
            src = img
        else:
            interp = cv2.INTER_AREA if cw < w0 else cv2.INTER_CUBIC
            src = cv2.resize(img, (cw, ch), interpolation=interp)
        self.src = np.ascontiguousarray(src, np.float32)
        x0, y0 = (W - cw) // 2, (H - ch) // 2           # rect in canvas pixels
        vx0, vy0, vx1, vy1 = max(x0, 0), max(y0, 0), min(x0 + cw, W), min(y0 + ch, H)
        self.win = (vx0, vy0, vx1, vy1)
        self.ch = ch
        # Pixel centres of the visible window, in rect pixels (j + 0.5 like the node).
        jx = np.arange(vx0 - x0, vx1 - x0, dtype=np.float32)
        jy = np.arange(vy0 - y0, vy1 - y0, dtype=np.float32)
        self.jx, self.jy = jx, jy
        xs, ys = jx + 0.5, jy + 0.5
        self.U = (xs.astype(np.float64) / ch)[None, :]
        self.V = (ys.astype(np.float64) / ch)[:, None]
        tm = float(p["top_motion"])
        self.env = (tm + (1.0 - tm) * np.clip(ys / ch, 0, 1) ** float(p["stiff"])).astype(np.float32)[:, None]
        self.base = [self._wave_maps(w) for w in BASE_WAVES]
        lx, ly = LIGHT
        self.slope_max = sum(a * 2 * math.pi * abs(fx * lx + fy * ly) for fx, fy, _, a, _ in BASE_WAVES)
        # Lock boxes, given in cover pixels.
        boxes, bad = parse_boxes(p["boxes"])
        if bad:
            self.warn.append(f"{bad} line(s) in the protected boxes do not have the form x0,y0,x1,y1 and were skipped.")
        if boxes and p["boxes"].strip() == DEMO_BOXES and self.cover_name != DEMO_COVER:
            self.warn.append("The protected boxes are set for the demo cover, so they are off for this cover. "
                             "Enter boxes around the logo and title of this cover.")
            boxes = []
        if not boxes:
            self.warn.append("No protected box: the logo and title wave with the cover.")
        feather = max(float(p["feather"]) * self.sy, 1e-6)
        X, Y = xs[None, :], ys[:, None]
        for (bx0, by0, bx1, by1), pinned in boxes:
            if bx1 > w0 + 0.5 or by1 > h0 + 0.5:
                self.warn.append(f"The box {int(bx0)},{int(by0)},{int(bx1)},{int(by1)} reaches outside the cover "
                                 f"({w0}×{h0} px).")
            b = (bx0 * self.sx, by0 * self.sy, bx1 * self.sx, by1 * self.sy)
            ddx = np.clip(np.maximum(b[0] - X, X - b[2]), 0, None)
            ddy = np.clip(np.maximum(b[1] - Y, Y - b[3]), 0, None)
            dist = np.sqrt(ddx * ddx + ddy * ddy)
            t = np.clip(1.0 - dist / feather, 0, 1)
            mask = (t * t * (3 - 2 * t)).astype(np.float32)
            core = dist <= 0
            if not mask.any():
                continue
            self.locks.append((mask, core, pinned))

    def _wave_maps(self, w):
        fx, fy, cycles, a, phase = w
        A = 2 * math.pi * (fx * self.U + fy * self.V) + phase
        lx, ly = LIGHT
        return (np.sin(A).astype(np.float32), np.cos(A).astype(np.float32), cycles, a,
                2 * math.pi * (fx * lx + fy * ly))

    def _variation(self, seed):
        if seed in self.var_cache:
            self.var_cache.move_to_end(seed)
            return self.var_cache[seed]
        waves, skew, gph = variation_waves(seed)
        v = ([self._wave_maps(w) for w in waves], skew, gph)
        self.var_cache[seed] = v
        while len(self.var_cache) > 3:
            self.var_cache.popitem(last=False)
        return v

    def info(self):
        return {"warnings": list(self.warn), "canvas": [self.W, self.H], "timeline": timeline(self.p),
                "segments": len(self.order), "cover": self.cover_name}

    # ---------- frames ----------
    def fields(self, seed, tau):
        p = self.p
        gust = float(p["gust"]) * math.sin(math.pi * tau) ** 2
        groups = [(self.base, 1.0)]
        if gust != 0.0:
            maps, skew, gph = self._variation(seed)
            gust *= 1.0 + skew * math.sin(2 * math.pi * tau + gph)
            groups.append((maps, gust))
        shp = (self.V.shape[0], self.U.shape[1])
        sway = np.zeros(shp, np.float32)
        bob = np.zeros(shp, np.float32)
        slope = np.zeros(shp, np.float32)
        for group, weight in groups:
            for S, C, cycles, a, g in group:
                b = 2 * math.pi * cycles * tau
                cb, sb = np.float32(math.cos(b)), np.float32(math.sin(b))
                s = S * cb - C * sb                    # sin(A - b)
                wa = np.float32(weight * a)
                sway += wa * s
                bob += wa * (C * cb + S * sb)          # cos(A - b)
                slope += np.float32(weight * a * g) * s
        env = self.env
        dx = np.float32(float(p["amp"]) / 100.0 * self.ch) * env * sway
        dy = np.float32(float(p["vert"]) / 100.0 * self.ch) * env * bob
        shade = 1.0 + np.float32(float(p["shading"])) * env * slope / np.float32(max(self.slope_max, 1e-6))
        for mask, core, pinned in self.locks:
            if pinned:
                mdx = mdy = 0.0
                msh = 1.0
            else:
                n = max(int(core.sum()), 1)
                mdx = float(dx[core].sum()) / n
                mdy = float(dy[core].sum()) / n
                msh = float(shade[core].sum()) / n
            im = 1.0 - mask
            dx = dx * im + np.float32(mdx) * mask
            dy = dy * im + np.float32(mdy) * mask
            shade = shade * im + np.float32(msh) * mask
        return dx, dy, shade

    def frame(self, t, fidx=None, clean=False, quick=False):
        if fidx is None:
            fidx = int(round(float(t) * self.fps))
        fidx = min(max(int(fidx), 0), self.N - 1)
        W, H = self.W, self.H
        if self.src is None:
            if self.mode == "scene":
                return np.broadcast_to((self.bg * 255 + 0.5).astype(np.uint8), (H, W, 3)).copy()
            return E.blank_out(H, W, self.mode, self.key)
        seg, i = divmod(fidx, self.n)
        seed = self.order[seg] + int(self.p["seed"])
        dx, dy, shade = self.fields(seed, i / self.n)
        mx = (self.jx[None, :] - dx).astype(np.float32)
        my = (self.jy[:, None] - dy).astype(np.float32)
        warped = cv2.remap(self.src, mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        warped = np.clip(warped * shade[..., None], 0.0, 1.0)
        vx0, vy0, vx1, vy1 = self.win
        o = np.zeros((H, W, 3), np.float32)
        A = np.zeros((H, W), np.float32)
        o[vy0:vy1, vx0:vx1] = warped
        A[vy0:vy1, vx0:vx1] = 1.0
        if self.mode == "scene":
            o += self.bg.reshape(1, 1, 3) * (1.0 - A)[..., None]
        return E.compose_out(o, A, self.mode, self.key)


if __name__ == "__main__":
    E.module_cli(sys.modules[__name__])
