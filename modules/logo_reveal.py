#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Logo reveal: a general CROM module for any logo (a PNG with transparency).

The logo appears (fade, wipe, focus or rise from below) while it gently settles from a larger
scale; a glow lights up around it, a shine sweeps across it and sparks fly off its edges.
When the effects are over the logo holds still and is the original image pixel for pixel (no
redrawing, no resampling), so the logo's shape and colours are never damaged.

  python modules/logo_reveal.py stills recipe.json folder [--times 0,1.5,4]
  python modules/logo_reveal.py render recipe.json folder [--res 1920x1080] [--draft] [--project projects/demo]
"""
import math
import os
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

MODULE_ID = "logo_reveal"
MODULE_LABEL = "Logo reveal"
ENGINE_VERSION = 1
MODULE_ORDER = 25
MODULE_DESC = "Any logo appears with a glow, a shine and sparks. At rest the logo is exactly the original image."
AI_DESC = ("Universal logo reveal for any band logo (PNG with transparency). The logo appears with one of the "
           "reveal styles (fade, wipe, blur, rise) while slowly pushing in from a larger scale, with an optional "
           "glow behind it, a light sweep across it and sparks flying off its edges. After the effects the logo "
           "holds still and is pixel-identical to the source file. Times are in seconds.")
RENDER_MSG = "Preparing the logo"

STYLES = [["fade", "Fade"], ["wipe", "Wipe from the left"], ["wipe_c", "Wipe from the centre"],
          ["blur", "Focus from a blur"], ["rise", "Rise from below"]]


# ----------------------------------------------------------------------------------------------
# Parameters
# ----------------------------------------------------------------------------------------------

SCHEMA = [
    {"id": "logo", "label": "Logo", "params": [
        E._p("logo", "Logo image", "asset", "logo.png", "geo", kind="logo",
             hint="A PNG with transparency from the project's logos folder. Add new ones with the upload button."),
        E.F("width", "Max. width", 60.0, 5, 100, 1, "% of frame", "geo"),
        E.F("height", "Max. height", 45.0, 5, 100, 1, "% of frame", "geo",
            hint="The logo fits inside both limits and keeps its aspect ratio."),
        E.F("pos_y", "Vertical position", 0.0, -40, 40, 0.5, "% of frame", "geo",
            hint="0 = centred, a negative number moves the logo up."),
        E._p("bg_color", "Background colour", "color", "#050507"),
        E.F("vignette", "Darker edges", 0.35, 0, 1, 0.01),
    ]},
    {"id": "reveal", "label": "Reveal", "params": [
        E._p("style", "Style", "select", "fade", options=STYLES),
        E.F("t_in", "Start", 0.4, 0, 10, 0.05, "s"),
        E.F("dur", "Reveal length", 1.8, 0.1, 10, 0.05, "s"),
        E.F("soft", "Edge softness", 0.18, 0.01, 1, 0.01, "", hint="For wipes: the width of the transition."),
        E.F("push", "Push-in", 6.0, 0, 30, 0.5, "%",
            hint="How much larger the logo is at the start. It settles as the reveal ends."),
        E.F("push_extra", "Push-in run-out", 1.2, 0, 10, 0.1, "s", hint="How long it keeps settling after the reveal."),
    ]},
    {"id": "glow", "label": "Glow", "params": [
        E._p("glow_color", "Glow colour", "color", "#ffc46b"),
        E.F("glow", "Strength during reveal", 0.9, 0, 3, 0.05),
        E.F("glow_rest", "Strength at rest", 0.0, 0, 2, 0.05, hint="0 = only the clean logo at rest."),
        E.F("glow_size", "Glow size", 3.0, 0.2, 15, 0.1, "% of frame", "geo"),
    ]},
    {"id": "shine", "label": "Shine", "params": [
        E._p("shine_on", "Shine sweep across the logo", "bool", True),
        E.F("shine_t", "Shine start", 2.4, 0, 20, 0.05, "s"),
        E.F("shine_dur", "Sweep length", 0.9, 0.1, 5, 0.05, "s"),
        E.F("shine_width", "Band width", 0.12, 0.02, 0.6, 0.01),
        E.F("shine_gain", "Shine brightness", 0.8, 0, 3, 0.05),
        E._p("shine_color", "Shine colour", "color", "#fff4e0"),
    ]},
    {"id": "sparks", "label": "Sparks", "params": [
        E._p("sparks", "Number of sparks", "int", 160, "geo", min=0, max=1500, step=10),
        E._p("spark_color", "Spark colour", "color", "#ffb04a"),
        E.F("spark_speed", "Speed", 1.0, 0.1, 4, 0.05),
        E.F("spark_life", "Spark life", 1.4, 0.2, 5, 0.05, "s"),
        E.F("spark_gain", "Brightness", 1.0, 0, 4, 0.05),
    ]},
    {"id": "timing", "label": "Length", "params": [
        E.F("total", "Total length", 6.0, 1, 60, 0.5, "s"),
        E.F("out", "Fade out at the end", 0.0, 0, 5, 0.05, "s", hint="0 = the video ends on the still logo."),
    ]},
    {"id": "output", "label": "Output", "params": E.output_params(
        seed=7, seed_rebuild="geo", seed_hint="A different layout of the sparks.",
        bg_hint="Transparent background: the logo with its glow and sparks remains, ready to lay over any video.")},
]

PRESETS = [
    {"id": "classic", "label": "Classic (fade, glow, shine)", "params": {}},
    {"id": "clean", "label": "Clean (fade only)", "params": {
        "push": 3.0, "glow": 0.0, "shine_on": False, "sparks": 0, "vignette": 0.2}},
    {"id": "fire", "label": "Fire (wipe and lots of sparks)", "params": {
        "style": "wipe", "dur": 2.2, "glow": 1.4, "glow_color": "#ff7a2a", "sparks": 700, "spark_speed": 1.4,
        "spark_color": "#ff8a2a", "shine_color": "#ffd9a0"}},
    {"id": "cold", "label": "Cold (focus, blue glow)", "params": {
        "style": "blur", "dur": 2.4, "glow_color": "#7fb8ff", "spark_color": "#a8d4ff", "sparks": 120,
        "shine_color": "#e8f4ff", "bg_color": "#03060c"}},
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


def _times(p):
    t_in, dur = float(p["t_in"]), float(p["dur"])
    t_rev = t_in + dur
    t_push = t_rev + float(p["push_extra"]) if float(p["push"]) > 0 else t_rev
    t_shine = float(p["shine_t"]) + float(p["shine_dur"]) if p["shine_on"] else 0.0
    t_spark = t_rev + float(p["spark_life"]) * 1.25 if int(p["sparks"]) > 0 else 0.0
    t_glow = t_rev + GLOW_FALL if abs(float(p["glow"]) - float(p["glow_rest"])) > 1e-6 else t_rev
    t_spark = max(t_spark, t_glow)
    total = float(p["total"])
    t_out = total - float(p["out"])
    return t_in, t_rev, t_push, t_shine, t_spark, t_out, total


def still_time(p):
    """First moment when every effect has finished (the clean resting logo)."""
    p = merge(p)
    t_in, t_rev, t_push, t_shine, t_spark, t_out, total = _times(p)
    t = max(t_rev, t_push, t_shine, t_spark) + 0.05
    return min(t, max(t_out - 1.0 / int(p["fps"]), 0.0))


def timeline(p):
    p = merge(p)
    fps = int(p["fps"])
    t_in, t_rev, t_push, t_shine, t_spark, t_out, total = _times(p)
    ph = []
    if t_in > 0:
        ph.append({"id": "before", "label": "Dark", "t0": 0.0, "t1": min(t_in, total), "video": 1})
    ph.append({"id": "reveal", "label": "Reveal", "t0": min(t_in, total), "t1": min(t_rev, total), "video": 1})
    rest0 = min(max(t_rev, t_push, t_shine, t_spark), t_out)
    if rest0 > t_rev:
        ph.append({"id": "effects", "label": "Shine and sparks", "t0": min(t_rev, total), "t1": rest0, "video": 1})
    if t_out > rest0:
        ph.append({"id": "rest", "label": "Rest", "t0": rest0, "t1": t_out, "video": 1})
    if total > t_out:
        ph.append({"id": "exit", "label": "Fade out", "t0": t_out, "t1": total, "video": 1})
    mk = []
    if p["shine_on"]:
        mk.append({"t": round(float(p["shine_t"]), 3), "label": "shine"})
    mk.append({"t": round(still_time(p), 3), "label": "logo at rest"})
    return {"total": total, "t1": total, "fps": fps, "phases": ph, "markers": mk}


def default_name(p):
    return f"Reveal {os.path.splitext(str(p.get('logo') or ''))[0] or 'logo'}"


def file_slug(p):
    return E._ascii_slug(os.path.splitext(str(p.get("logo") or "logo"))[0], "logo") + "_reveal"


def load_recipe(path):
    return E.read_recipe(path, MODULE_ID, merge)


def recipe_doc(name, params):
    return E.make_recipe_doc(name, params, MODULE_ID, ENGINE_VERSION, merge)


# ----------------------------------------------------------------------------------------------
# Renderer
# ----------------------------------------------------------------------------------------------

_IMG = OrderedDict()
_IMG_LOCK = threading.Lock()


def load_logo(path):
    """Logo as straight RGB float32 0..1 plus alpha 0..1, cached by path and mtime."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    with _IMG_LOCK:
        if key in _IMG:
            _IMG.move_to_end(key)
            return _IMG[key]
    img = E.read_image(path, cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.dtype != np.uint8:
        img = (img.astype(np.float32) / np.iinfo(img.dtype).max * 255 + 0.5).astype(np.uint8)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        rgb, a = img[..., 2::-1], img[..., 3]
    else:
        rgb, a = img[..., ::-1], np.full(img.shape[:2], 255, np.uint8)
    out = (np.ascontiguousarray(rgb), np.ascontiguousarray(a))
    with _IMG_LOCK:
        _IMG[key] = out
        while len(_IMG) > 4:
            _IMG.popitem(last=False)
    return out


def _rgb(hexs):
    return np.array([int(hexs[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255.0


GLOW_FALL = 1.2   # seconds for the reveal glow to settle to glow_rest


def _ease(x):
    x = min(max(x, 0.0), 1.0)
    return 1.0 - (1.0 - x) ** 3


class Renderer:
    def __init__(self, params, W, H, ctx=None):
        self.ctx = ctx
        self.W, self.H = int(W), int(H)
        self.geo_key = None
        self.warn = []
        self.logo_name = None
        self.update(params)

    def update(self, params):
        p = merge(params)
        self.p = p
        self.fps = int(p["fps"])
        self.T_end = self.T1 = float(p["total"])
        self.N = max(1, int(round(self.T_end * self.fps)))
        self.mode = E.out_kind(p)
        self.key = E.key_rgb(p)
        self.bg = _rgb(p["bg_color"])
        gk = (level_key(p, LEVELS), self.W, self.H, getattr(self.ctx, "dir", None))
        if gk != self.geo_key:
            self._build(p)
            self.geo_key = gk
        self._vig = None

    def _logo_path(self, p):
        self.warn = []
        fp = E.project_asset(self.ctx, "logo", p["logo"])
        if fp is not None:
            self.logo_name = p["logo"]
            return fp
        self.logo_name = None
        lst = self.ctx.assets("logo") if self.ctx is not None and hasattr(self.ctx, "assets") else []
        if lst:
            self.logo_name = lst[0]
            self.warn.append(f"The logo {p['logo']} is not in the project, using {lst[0]}.")
            return self.ctx.asset_path("logo", lst[0])
        self.warn.append("The project has no logo yet. Add a PNG with the upload button.")
        return None

    def _build(self, p):
        fp = self._logo_path(p)
        lg = load_logo(fp) if fp else None
        self.rgb = None
        if lg is None:
            if fp:
                self.warn.append("The logo could not be read.")
            return
        rgb8, a8 = lg
        if a8.min() == 255:
            self.warn.append("The logo has no transparent background, it will show as a rectangle. Use a PNG with transparency.")
        h0, w0 = a8.shape
        W, H = self.W, self.H
        s = min(W * float(p["width"]) / 100.0 / w0, H * float(p["height"]) / 100.0 / h0)
        lw, lh = max(1, int(round(w0 * s))), max(1, int(round(h0 * s)))
        if s > 1.0:
            self.warn.append(f"The logo is only {w0}×{h0} px, at this size it is enlarged {s:.1f}×. "
                             "Upload a larger PNG for a sharp result.")
        if (lw, lh) == (w0, h0):
            rgb, a = rgb8.astype(np.float32) / 255.0, a8.astype(np.float32) / 255.0
        else:
            # Resize in premultiplied space so transparent pixels do not bleed a dark fringe.
            interp = cv2.INTER_AREA if s < 1.0 else cv2.INTER_CUBIC
            a = a8.astype(np.float32) / 255.0
            pm = rgb8.astype(np.float32) / 255.0 * a[..., None]
            pm = cv2.resize(pm, (lw, lh), interpolation=interp)
            a = np.clip(cv2.resize(a, (lw, lh), interpolation=interp), 0, 1)
            rgb = np.where(a[..., None] > 1e-4, np.clip(pm / np.maximum(a, 1e-4)[..., None], 0, 1), 0)
            # The resting logo is quantised once here, so the rest frame is exactly this image.
            rgb = np.round(rgb * 255) / 255.0
            a = np.round(a * 255) / 255.0
        self.rgb = rgb.astype(np.float32)
        self.a = a.astype(np.float32)
        self.lw, self.lh = lw, lh
        cx = W / 2.0
        cy = H / 2.0 + float(p["pos_y"]) / 100.0 * H
        self.x0 = int(round(cx - lw / 2.0))
        self.y0 = int(round(cy - lh / 2.0))
        self.cx, self.cy = self.x0 + lw / 2.0, self.y0 + lh / 2.0
        if self.x0 < 0 or self.y0 < 0 or self.x0 + lw > W or self.y0 + lh > H:
            self.warn.append("The logo goes past the edge of the frame, make it smaller or move it.")
        # Glow source: the logo alpha on the full canvas, blurred once.
        self.glow_sig = max(float(p["glow_size"]) / 100.0 * min(W, H), 0.5)
        self.glow_map = self._glow_from(self._place(self.a))
        self._sparks(p)

    def _glow_from(self, La):
        """Soft glow around the visible logo, blurred at quarter size (same path for every frame)."""
        W, H = self.W, self.H
        w4, h4 = max(1, W // 4), max(1, H // 4)
        sm = cv2.resize(La, (w4, h4), interpolation=cv2.INTER_AREA)
        sig = max(self.glow_sig / 4.0, 0.3)
        g = 0.55 * cv2.GaussianBlur(sm, (0, 0), max(sig * 0.35, 0.3)) + 0.72 * cv2.GaussianBlur(sm, (0, 0), sig)
        return np.clip(cv2.resize(g, (W, H), interpolation=cv2.INTER_LINEAR), 0, None).astype(np.float32)

    def _place(self, img, scale=1.0, dy=0.0, interp=cv2.INTER_LINEAR):
        """Logo layer (H×W or H×W×3) on the canvas. Scale 1 with no offset is an exact paste."""
        W, H = self.W, self.H
        shp = (H, W) + img.shape[2:]
        if abs(scale - 1.0) < 1e-6 and abs(dy) < 1e-6:
            out = np.zeros(shp, np.float32)
            x0, y0 = self.x0, self.y0
            sx0, sy0 = max(0, -x0), max(0, -y0)
            dx0, dy0 = max(0, x0), max(0, y0)
            w = min(self.lw - sx0, W - dx0)
            h = min(self.lh - sy0, H - dy0)
            if w > 0 and h > 0:
                out[dy0:dy0 + h, dx0:dx0 + w] = img[sy0:sy0 + h, sx0:sx0 + w]
            return out
        # Map logo pixel centres around the logo centre.
        M = np.array([[scale, 0, self.cx - scale * self.lw / 2.0],
                      [0, scale, self.cy + dy - scale * self.lh / 2.0]], np.float64)
        return cv2.warpAffine(img, M, (W, H), flags=interp, borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    def _sparks(self, p):
        n = int(p["sparks"])
        self.sp = None
        if n <= 0:
            return
        rng = np.random.default_rng(int(p["seed"]) * 7919 + 13)
        a = self.a
        edge = cv2.morphologyEx((a > 0.5).astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
        ys, xs = np.nonzero(edge)
        if len(xs) == 0:
            ys, xs = np.nonzero(a > 0.5)
        if len(xs) == 0:
            return
        k = rng.integers(0, len(xs), n)
        px = xs[k] + self.x0 + rng.uniform(-0.5, 0.5, n)
        py = ys[k] + self.y0 + rng.uniform(-0.5, 0.5, n)
        t_in, dur = float(p["t_in"]), float(p["dur"])
        style = p["style"]
        if style in ("wipe", "wipe_c"):
            # Sparks are born where the wipe edge passes.
            u = (xs[k] + 0.5) / self.lw
            if style == "wipe_c":
                u = np.abs(u - 0.5) * 2.0
            born = t_in + dur * np.clip(u, 0, 1) + rng.normal(0, 0.04, n) * dur
        else:
            born = t_in + dur * rng.beta(1.6, 3.0, n)
        ang = rng.uniform(0, 2 * np.pi, n)
        spd = rng.lognormal(0, 0.45, n)
        self.sp = {"x": px, "y": py, "born": born, "ang": ang, "spd": spd,
                   "life": rng.uniform(0.55, 1.0, n), "size": rng.uniform(0.6, 1.6, n),
                   "flick": rng.uniform(0, 2 * np.pi, n)}

    def info(self):
        return {"warnings": list(self.warn), "canvas": [self.W, self.H], "timeline": timeline(self.p),
                "logo": self.logo_name, "rest_time": still_time(self.p)}

    # ---------- per frame ----------
    def _spark_layer(self, t):
        sp, p = self.sp, self.p
        W, H = self.W, self.H
        lay = np.zeros((H, W), np.float32)
        if sp is None or float(p["spark_gain"]) <= 0:
            return lay
        life = float(p["spark_life"]) * sp["life"]
        age = t - sp["born"]
        live = (age >= 0) & (age < life)
        if not live.any():
            return lay
        u = (age / life)[live]
        unit = min(W, H) / 1080.0
        v = 140.0 * float(p["spark_speed"]) * unit * sp["spd"][live]
        ag = age[live]
        drag = (1.0 - np.exp(-1.6 * ag)) / 1.6
        x = sp["x"][live] + np.cos(sp["ang"][live]) * v * drag
        y = sp["y"][live] + np.sin(sp["ang"][live]) * v * drag - 60.0 * unit * ag * ag
        bright = (1.0 - u) ** 1.5 * (0.75 + 0.25 * np.sin(sp["flick"][live] + ag * 23.0))
        r = np.maximum(sp["size"][live] * 1.6 * unit, 0.6)
        xi = np.round(x).astype(int)
        yi = np.round(y).astype(int)
        ok = (xi >= 0) & (xi < W) & (yi >= 0) & (yi < H)
        np.add.at(lay, (yi[ok], xi[ok]), (bright * r * r)[ok].astype(np.float32))
        lay = cv2.GaussianBlur(lay, (0, 0), 1.2 * max(unit, 0.5)) * 2.2
        return lay

    def _vignette(self):
        if self._vig is None:
            W, H = self.W, self.H
            yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
            d = np.sqrt(((xx + 0.5) / W - 0.5) ** 2 * (W / max(H, 1)) ** 2 + ((yy + 0.5) / H - 0.5) ** 2)
            self._vig = (1.0 - float(self.p["vignette"]) * E.sstep(0.25, 0.95, d)).astype(np.float32)
        return self._vig

    def frame(self, t, fidx=None, clean=False, quick=False):
        if fidx is not None:
            t = fidx / self.fps
        t = float(t)
        p = self.p
        W, H = self.W, self.H
        scene_bg = self.bg.reshape(1, 1, 3) * self._vignette()[..., None] if self.mode == "scene" else None
        if self.rgb is None:
            if self.mode == "scene":
                return (np.clip(np.broadcast_to(scene_bg, (H, W, 3)), 0, 1) * 255 + 0.5).astype(np.uint8)
            return E.blank_out(H, W, self.mode, self.key)
        t_in, t_rev, t_push, t_shine, t_spark, t_out, total = _times(p)
        dur = max(t_rev - t_in, 1e-6)
        r = min(max((t - t_in) / dur, 0.0), 1.0)          # reveal progress 0..1
        fade_out = 1.0 if t < t_out else max(0.0, 1.0 - (t - t_out) / max(total - t_out, 1e-6))
        # Push-in: from (1 + push) to exactly 1 at t_push.
        push = float(p["push"]) / 100.0
        q = 1.0 if t_push <= 0 else _ease((t - 0.0) / max(t_push, 1e-6)) if t < t_push else 1.0
        scale = 1.0 + push * (1.0 - q)
        style = p["style"]
        dy = 0.0
        if style == "rise" and r < 1.0:
            dy = (1.0 - _ease(r)) * 0.06 * H
        rgb = self.rgb
        a = self.a
        if style == "blur" and 0.0 < r < 1.0:
            sig = (1.0 - _ease(r)) * 0.02 * min(W, H)
            if sig > 0.3:
                pm = cv2.GaussianBlur(rgb * a[..., None], (0, 0), sig)
                a = cv2.GaussianBlur(a, (0, 0), sig)
                rgb = np.where(a[..., None] > 1e-4, pm / np.maximum(a, 1e-4)[..., None], 0)
        # Reveal mask in logo pixels.
        if style in ("wipe", "wipe_c") and 0.0 < r < 1.0:
            soft = float(p["soft"])
            u = (np.arange(self.lw, dtype=np.float32) + 0.5) / self.lw
            if style == "wipe_c":
                u = np.abs(u - 0.5) * 2.0
            edge = r * (1.0 + soft) - soft
            m = E.sstep(edge + soft, edge, u).astype(np.float32)[None, :]
            vis = a * m
        else:
            op = 1.0 if style in ("wipe", "wipe_c") else _ease(r) if style != "fade" else E.env01(r, 0.0, 1.0)
            vis = a * (1.0 if r >= 1.0 else (op if r > 0 else 0.0))
        cubic = cv2.INTER_CUBIC
        Lrgb = self._place(rgb * vis[..., None], scale, dy, cubic)    # premultiplied logo light
        La = np.clip(self._place(vis, scale, dy, cubic), 0, 1)
        # Light sweep across the logo, masked by its own alpha.
        if p["shine_on"] and float(p["shine_gain"]) > 0:
            s0 = float(p["shine_t"])
            sd = max(float(p["shine_dur"]), 1e-6)
            if s0 <= t <= s0 + sd:
                k = (t - s0) / sd
                w = float(p["shine_width"])
                yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
                uu = ((xx - self.x0) / max(self.lw, 1) + (yy - self.y0) / max(self.lw, 1) * 0.45)
                c = -w * 2 + k * (1.45 + w * 4)
                band = np.exp(-((uu - c) / w) ** 2).astype(np.float32)
                env = math.sin(math.pi * k) ** 0.7
                Lrgb = Lrgb + _rgb(p["shine_color"]).reshape(1, 1, 3) * (band * La * float(p["shine_gain"]) * env)[..., None]
        # Glow: strongest while the logo is revealing, settles to glow_rest.
        g_peak = float(p["glow"])
        g_rest = float(p["glow_rest"])
        if r <= 0:
            g = 0.0
        else:
            g = g_rest + (g_peak - g_rest) * (1.0 - _ease(max(t - t_rev, 0.0) / GLOW_FALL))
            if t < t_rev:
                g = g_rest * _ease(r) + (g_peak - g_rest) * math.sin(math.pi * 0.5 * min(r * 1.3, 1.0))
        light = np.zeros((H, W, 3), np.float32)
        if g > 1e-4:
            full = r >= 1.0 and abs(scale - 1) < 1e-6 and dy == 0
            gm = self.glow_map if full else self._glow_from(La)
            light += _rgb(p["glow_color"]).reshape(1, 1, 3) * (gm * g)[..., None]
        if t < t_spark + 1e-6 and self.sp is not None:
            sl = self._spark_layer(t) * float(p["spark_gain"])
            if sl.any():
                light += _rgb(p["spark_color"]).reshape(1, 1, 3) * sl[..., None]
        light *= (1.0 - La)[..., None]                    # glow and sparks behind the logo
        o = Lrgb + light
        A = La + (1.0 - La) * E.light_alpha(light)
        if fade_out < 1.0:
            o *= fade_out
            A *= fade_out
        if self.mode == "scene":
            o = o + scene_bg * (1.0 - A)[..., None]
            return (np.clip(o, 0, 1) * 255 + 0.5).astype(np.uint8)
        return E.compose_out(o, A, self.mode, self.key)

    def _scale_canvas(self, img, scale, dy):
        M = np.array([[scale, 0, (1 - scale) * self.cx], [0, scale, (1 - scale) * self.cy + dy]], np.float64)
        return cv2.warpAffine(img, M, (self.W, self.H), flags=cv2.INTER_LINEAR, borderValue=0)


if __name__ == "__main__":
    E.module_cli(sys.modules[__name__])
