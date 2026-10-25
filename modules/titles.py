#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Titles: a song name in embossed metal lettering (a module of the CROM app).

Every frame is a pure function of (parameters, time t), with no state between frames.
So the preview can jump anywhere and the final render runs on every core.

  python modules/titles.py stills recipe.json folder --times 2,6,9,12,16,20 --res 960x540
  python modules/titles.py render recipe.json folder [--res 1920x1080] [--fps 30] [--crf 14] [--draft]
                           [--project projects/<band>]

Every parameter is in SCHEMA. A new slider = a new line in SCHEMA + its use in Title.
Shared things (schema, fonts, light, encoding, projects) are in core.py.
"""
import argparse
import json
import math
import os
import pickle
import subprocess
import sys
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
if APP not in sys.path:
    sys.path.insert(0, APP)

from core import (F, Project, Smoke, _p, accumulate, accumulate1, bloom, blank_out, coerce, compose_out,
    draw_star, emit, env01, ffmpeg_cmd, key_rgb, light_alpha, out_format, out_kind, out_name, video_cmd, gblur, get_font, hex_lin, luma3, output_params, parse_res, sample_pts,
    slugify, spectral_noise, splat_idx, split_lines, sstep, streak, strip_accents, tone_lut,
    wrap_sample, write_png)  # noqa: E402

ENGINE_VERSION = 1
MODULE_ID = "titles"
MODULE_LABEL = "Titles"
MODULE_ORDER = 10
MODULE_DESC = "A song name in embossed lettering: it rises out of the dark and dissolves into stardust."
BATCH = {"key": "title", "label": "Batch of titles", "lines": True,
         "help": "Each line is one song name; all of them render with the same recipe."}


# ----------------------------------------------------------------------------------------------
# Parameter schema: the single source of truth for the UI, recipes and the renderer.
# rebuild: "layout" = new letter raster, "shade" = new lighting maps, "order" = new dissolve order.
# Params without rebuild are read per frame and cost nothing to change.
# ----------------------------------------------------------------------------------------------


SCHEMA = [
    {"id": "text", "label": "Text", "params": [
        _p("title", "Title", "textarea", "Song Title\nGoes Here", "layout",
           hint="Each line of text is one line of the title."),
        _p("font", "Font", "font", "CinzelDecorative-Regular.ttf", "layout"),
        _p("case", "Letter case", "select", "asis", "layout",
           options=[["asis", "As typed"], ["upper", "UPPER CASE"], ["lower", "lower case"]]),
        F("size", "Size", 10, 2, 20, 0.1, "% of height", "layout", "Height of a capital letter in the main line."),
        F("tracking", "Tracking", 60, -100, 400, 5, "/1000 em", "layout"),
        F("word_space", "Word spacing", 100, 30, 300, 5, "%", "layout"),
        _p("emphasis", "Main line", "select", "last", "layout",
           options=[["none", "All the same"], ["first", "First line larger"], ["last", "Last line larger"]]),
        F("line_ratio", "Smaller lines", 62, 30, 100, 1, "%", "layout"),
        F("line_gap", "Line spacing", 50, 5, 200, 1, "% of letter", "layout"),
        F("max_width", "Maximum width", 84, 30, 98, 1, "% of frame", "layout",
          "A wider title shrinks to fit."),
        F("pos_y", "Vertical position", 50, 15, 85, 0.5, "%"),
    ]},
    {"id": "material", "label": "Material", "params": [
        _p("metal", "Metal colour", "color", "#ffc56e"),
        _p("spec_tint", "Highlight colour", "color", "#fff0d2"),
        F("bevel", "Bevel width", 9, 1, 30, 0.5, "% of letter", "layout"),
        F("bevel_round", "Bevel roundness", 55, 0, 100, 1, "%", "layout"),
        F("depth", "Relief depth", 100, 20, 300, 5, "%", "shade"),
        F("env_sky", "Sky reflection", 1.3, 0, 4, 0.05),
        F("env_floor", "Ground reflection", 0.05, 0, 1, 0.01),
        F("env_band", "Horizon line", 0.6, 0, 4, 0.05),
        F("band_pos", "Horizon position", 5, -60, 60, 1, "", "shade"),
        F("face_grad", "Face gradient", 45, 0, 100, 1, "%", "shade",
          "Lighter tops and darker bottoms of the letters."),
        F("texture", "Metal texture", 30, 0, 100, 1, "%"),
        F("tex_scale", "Texture grain", 100, 25, 400, 5, "%", "shade"),
    ]},
    {"id": "light", "label": "Light", "params": [
        F("key", "Key light", 0.8, 0, 3, 0.05),
        F("key_angle", "Light direction", 125, 0, 360, 1, "°", "shade", "90° is from above, 180° from the left."),
        F("key_elev", "Light elevation", 40, 5, 85, 1, "°", "shade"),
        F("spec", "Shine", 1.2, 0, 5, 0.05),
        F("gloss", "Shine sharpness", 60, 4, 400, 1, "", "shade"),
        F("ambient", "Ambient light", 0.06, 0, 1, 0.01),
        F("rim", "Rim light", 1.0, 0, 5, 0.05, hint="Light on the edges; it lights up first."),
        F("rim_angle", "Rim light direction", 90, 0, 360, 1, "°", "shade"),
        _p("rim_color", "Rim light colour", "color", "#ffd59a"),
        F("sweep", "Light sweep", 1.0, 0, 4, 0.05),
        F("sweep_width", "Sweep width", 100, 20, 300, 5, "%"),
        F("sweep_angle", "Sweep angle", 22, -60, 60, 1, "°"),
        F("glint", "Glint star", 1.0, 0, 3, 0.05, hint="A small star that travels with the light sweep."),
    ]},
    {"id": "entry", "label": "Entry", "params": [
        F("t_in", "Entry length", 10, 3, 30, 0.5, "s"),
        F("t_hold", "Hold length", 5, 0, 30, 0.5, "s"),
        F("fade_in", "Out of the dark by", 35, 0, 100, 1, "% of entry"),
        F("rim_from", "Rim light from", 0, 0, 100, 1, "% of entry"),
        F("rim_to", "Rim light to", 45, 0, 100, 1, "% of entry"),
        F("key_from", "Key light from", 20, 0, 100, 1, "% of entry"),
        F("key_to", "Key light to", 80, 0, 100, 1, "% of entry"),
        F("focus", "Defocus", 16, 0, 60, 0.5, "px", hint="The title starts blurred and comes into focus."),
        F("focus_to", "In focus at", 60, 1, 100, 1, "% of entry"),
        F("push", "Camera push-in", 9, 0, 40, 0.5, "%", hint="How much smaller the title is at the start."),
        F("rise", "Rise", 1.2, -10, 10, 0.1, "% of height"),
        F("sweep_at", "Light sweep from", 64, 0, 100, 1, "% of entry"),
        F("sweep_dur", "Light sweep length", 22, 3, 80, 1, "% of entry"),
        F("breath", "Glow breathing", 30, 0, 100, 1, "%", hint="A gentle pulse during the hold."),
        F("sparkle", "Sparkles", 35, 0, 100, 1, "%", hint="Little stars that now and then flash on the letters."),
    ]},
    {"id": "dissolve", "label": "Dissolve", "params": [
        F("t_out", "Dissolve length", 10, 3, 30, 0.5, "s"),
        _p("dir", "Dissolve direction", "select", "ltr", "order",
           options=[["ltr", "Left to right"], ["rtl", "Right to left"], ["center", "From the centre"],
                    ["edges", "From the edges"], ["ttb", "Top to bottom"], ["btt", "Bottom to top"],
                    ["letters", "Letter by letter"], ["random", "Random"]]),
        F("jitter", "Irregularity", 45, 0, 100, 1, "%", "order"),
        F("dis_from", "Dissolve from", 6, 0, 90, 1, "% of dissolve"),
        F("dis_to", "Last grain", 55, 5, 100, 1, "% of dissolve"),
        F("preheat", "Glowing front", 0.7, 0, 3, 0.05, "s", hint="How long a letter glows before it falls apart."),
        F("heat", "Flash", 1.0, 0, 4, 0.05),
        F("wind", "Wind", 1.0, 0, 5, 0.05),
        F("wind_angle", "Wind direction", 12, -180, 180, 1, "°", hint="0° is to the right, 90° up."),
        F("turb", "Swirls", 1.0, 0, 5, 0.05),
        F("lift", "Grain lift", 0.6, -3, 5, 0.05),
        F("spread", "Spread", 0.8, 0, 5, 0.05),
        F("life", "Grain life", 3.6, 0.5, 10, 0.1, "s"),
        _p("dust_color", "Stardust colour", "color", "#c4dcff"),
        F("grain", "Grain size", 100, 50, 400, 5, "%"),
        F("stars", "Larger stars", 8, 0, 100, 1, "‰", "order"),
        F("twinkle", "Twinkle", 60, 0, 100, 1, "%"),
        F("end_fade", "Fade to black", 1.5, 0, 5, 0.1, "s"),
    ]},
    {"id": "atmo", "label": "Atmosphere and image", "params": [
        _p("bg", "Background", "color", "#000000"),
        F("backlight", "Glow behind the text", 0.0, 0, 3, 0.05),
        _p("back_color", "Glow colour", "color", "#8a4a18"),
        F("mist", "Mist", 0.2, 0, 3, 0.05),
        _p("mist_color", "Mist colour", "color", "#6e4d30"),
        F("motes", "Dust in the air", 0.6, 0, 3, 0.05),
        F("glow", "Glow (bloom)", 0.6, 0, 3, 0.05),
        F("streak", "Anamorphic streaks", 0.18, 0, 3, 0.05),
        F("vignette", "Vignette", 0.3, 0, 1, 0.01),
        F("exposure", "Exposure", 0.0, -3, 3, 0.05, "EV"),
        F("saturation", "Saturation", 1.0, 0, 2, 0.01),
        F("knee", "Soft highlights", 85, 50, 99, 1, "%"),
        F("film_grain", "Film grain", 35, 0, 100, 1, "%"),
    ]},
    {"id": "output", "label": "Output", "params": output_params(
        seed=7, seed_rebuild="shade", seed_hint="A different number gives a different layout of grains, sparks and texture.",
        bg=True, bg_hint="Transparent background: only the lettering, sparks and dust remain, without the background "
                         "colour, back light, mist and vignette. The key colour is for programs that cannot read alpha.")},
]

PRESETS = [
    {"id": "gold", "label": "Gold", "params": {}},
    {"id": "bronze", "label": "Old bronze", "params": {
        "metal": "#d8a165", "spec_tint": "#ffd9a8", "env_sky": 1.0, "env_band": 0.3, "texture": 60,
        "rim_color": "#ffb06a", "dust_color": "#ffd2a0", "mist_color": "#5a3a22", "streak": 0.15}},
    {"id": "silver", "label": "Elven silver", "params": {
        "metal": "#dfe7f2", "spec_tint": "#ffffff", "rim_color": "#cfe2ff", "dust_color": "#d6e8ff",
        "mist_color": "#2c3a4c", "back_color": "#203a60"}},
    {"id": "ember", "label": "Dragon glow", "params": {
        "metal": "#ff9e52", "spec_tint": "#ffd7a0", "rim_color": "#ff8a3c", "dust_color": "#ffb070",
        "heat": 1.8, "mist_color": "#5a2810", "back_color": "#902e08"}},
]

# Timing lanes for the editor's timeline, one draggable bar each.
# phase "in": values are % of t_in from 0 s; "out": % of t_out from the cut; "end": seconds back from the end.
# "from" missing = the bar starts at the phase start; "len" = duration param instead of an end param.
# shape: "up" ramps 0 -> 1, "down" ramps 1 -> 0, "bar" is an event.
LANES = [
    {"id": "fade", "label": "From dark", "phase": "in", "to": "fade_in", "shape": "up"},
    {"id": "focus", "label": "Focus", "phase": "in", "to": "focus_to", "shape": "down"},
    {"id": "rim", "label": "Rim light", "phase": "in", "from": "rim_from", "to": "rim_to", "shape": "up"},
    {"id": "key", "label": "Key light", "phase": "in", "from": "key_from", "to": "key_to", "shape": "up"},
    {"id": "sweep", "label": "Light sweep", "phase": "in", "from": "sweep_at", "len": "sweep_dur", "shape": "bar"},
    {"id": "dis", "label": "Dissolve", "phase": "out", "from": "dis_from", "to": "dis_to", "shape": "bar"},
    {"id": "end", "label": "Fade to black", "phase": "end", "len": "end_fade", "shape": "down"},
]

PARAMS = {p["key"]: p for g in SCHEMA for p in g["params"]}
LEVELS = ("layout", "shade", "order")


def defaults():
    return {k: p["default"] for k, p in PARAMS.items()}


def merge(params=None):
    d = defaults()
    for k, v in (params or {}).items():
        if k in PARAMS:
            d[k] = coerce(PARAMS[k], v)
    return d


def level_key(p, levels):
    return json.dumps({k: p[k] for k in sorted(PARAMS) if PARAMS[k].get("rebuild") in levels},
                      sort_keys=True, ensure_ascii=True)


def timeline(p):
    p = merge(p)
    ti, th, to = p["t_in"], p["t_hold"], p["t_out"]
    t1 = ti + th
    te = t1 + to
    mk = [
        (ti * p["fade_in"] / 100, "out of the dark"),
        (ti * p["focus_to"] / 100, "in focus"),
        (ti * p["sweep_at"] / 100, "light sweep"),
        (t1 + to * p["dis_from"] / 100, "dissolve starts"),
        (t1 + to * p["dis_to"] / 100, "last grain"),
        (te - p["end_fade"], "fade to black"),
    ]
    return {
        "total": te, "t1": t1, "fps": int(p["fps"]),
        "phases": [
            {"id": "in", "label": "Entry", "t0": 0.0, "t1": ti, "video": 1},
            {"id": "hold", "label": "Hold", "t0": ti, "t1": t1, "video": 1},
            {"id": "out", "label": "Dissolve", "t0": t1, "t1": te, "video": 2},
        ],
        "markers": [{"t": round(t, 3), "label": lb} for t, lb in mk],
    }
def apply_case(s, mode):
    if mode == "upper":
        return s.upper()
    if mode == "lower":
        return s.lower()
    return s


# ----------------------------------------------------------------------------------------------
# The title renderer
# ----------------------------------------------------------------------------------------------

class Title:
    def __init__(self, params, W, H, ctx=None):
        self.ctx = ctx
        self.W, self.H = int(W), int(H)
        self.rs = self.H / 1080.0
        self.q = (max(2, self.W // 4), max(2, self.H // 4))
        self.keys = {lv: None for lv in LEVELS}
        self.ver = 0
        self._memo = {}
        self._ao = None
        self.update(params)

    def __getstate__(self):
        d = dict(self.__dict__)
        d["_memo"] = {}
        return d

    # ---------- parameter handling ----------
    def update(self, params):
        p = merge(params)
        self.p = p
        kl = level_key(p, ("layout",))
        ks = level_key(p, ("layout", "shade"))
        ko = level_key(p, LEVELS)
        if kl != self.keys["layout"]:
            self._build_layout()
            self.keys = {"layout": kl, "shade": None, "order": None}
        if ks != self.keys["shade"]:
            self._build_shade()
            self.keys["shade"], self.keys["order"] = ks, None
            self.ver += 1
            self._memo = {}
        if ko != self.keys["order"]:
            self._build_order()
            self.keys["order"] = ko
        self._prepare()

    def memo(self, name, key, fn):
        hit = self._memo.get(name)
        if hit is not None and hit[0] == key:
            return hit[1]
        val = fn()
        self._memo[name] = (key, val)
        return val

    def info(self):
        return {"warnings": list(self.g["warn"]), "particles": int(self.o["N"]),
                "canvas": [int(self.g["Wc"]), int(self.g["Hc"])], "timeline": timeline(self.p),
                "sim": None, "approx": False}

    # ---------- layout: letters, kerning, raster, bevel height ----------
    def _build_layout(self):
        p, W, H = self.p, self.W, self.H
        fi = get_font(p["font"])
        lines = split_lines(apply_case(p["title"], p["case"]))
        n = len(lines)
        emph = p["emphasis"]
        scales = [1.0 if (emph == "none" or (emph == "first" and i == 0) or (emph == "last" and i == n - 1))
                  else p["line_ratio"] / 100.0 for i in range(n)]
        warn = []
        trk = p["tracking"] / 1000.0 * fi.upm
        space = fi.adv.get(fi.glyph(" ") or "space", fi.upm // 4) * p["word_space"] / 100.0
        shaped = []
        for ln in lines:
            items, x, prev = [], 0.0, None
            for ch in ln:
                if ch.isspace():
                    x += space + trk
                    prev = None
                    continue
                g = fi.glyph(ch)
                if g is None:
                    alt = strip_accents(ch)
                    g = fi.glyph(alt) if alt != ch else None
                    if g is None:
                        warn.append(f"The font has no “{ch}” character, it was left out.")
                        continue
                    warn.append(f"The font has no “{ch}”, used “{alt}” instead.")
                    ch = alt
                if prev is not None:
                    x += fi.kern(prev, g)
                items.append((ch, x))
                x += fi.adv.get(g, 0) + trk
                prev = g
            shaped.append((items, max(0.0, x - trk) if items else 0.0))
        cap = p["size"] / 100.0 * H
        widest = max(w * cap * sc / fi.cap for (it, w), sc in zip(shaped, scales))
        maxw = p["max_width"] / 100.0 * W
        if widest > maxw > 0:
            f = maxw / widest
            cap *= f
            warn.append(f"The title is wide, it was scaled down to {f * 100:.0f} % of its size.")
        caps = [cap * s for s in scales]
        gap = p["line_gap"] / 100.0 * cap
        bases, y = [], 0.0
        for c in caps:
            y += c
            bases.append(y)
            y += gap
        block_h = bases[-1]
        rss = 4 if (min(widest, maxw) + 2 * cap) * (block_h + 2 * cap) < 2.6e6 else 2
        glyphs, fonts = [], []
        for li, ((items, wu), c) in enumerate(zip(shaped, caps)):
            k = c / fi.cap
            font = fi.pil(k * fi.upm * rss)
            fonts.append(font)
            if not items:
                continue
            pens = [(ch, int(round(xu * k * rss))) for ch, xu in items]
            boxes = [font.getbbox(ch, anchor="ls") for ch, _ in pens]
            lo = min(px + b[0] for (ch, px), b in zip(pens, boxes))
            hi = max(px + b[2] for (ch, px), b in zip(pens, boxes))
            shift = -(lo + hi) // 2
            base = int(round(bases[li] * rss))
            for (ch, px), b in zip(pens, boxes):
                glyphs.append({"ch": ch, "line": li, "x": px + shift, "base": base, "box": tuple(int(v) for v in b)})
        if glyphs:
            xmin = min(g["x"] + g["box"][0] for g in glyphs)
            xmax = max(g["x"] + g["box"][2] for g in glyphs)
            ymin = min(g["base"] + g["box"][1] for g in glyphs)
            ymax = max(g["base"] + g["box"][3] for g in glyphs)
        else:
            xmin, xmax, ymin, ymax = -rss * 4, rss * 4, 0, int(block_h * rss) + rss
        m = rss * (int(0.12 * cap) + 6)
        cx0 = (xmin - m) // rss
        cy0 = (ymin - m) // rss
        cx1 = -(-(xmax + m) // rss)
        cy1 = -(-(ymax + m) // rss)
        Wc, Hc = int(cx1 - cx0), int(cy1 - cy0)
        mask = np.zeros((Hc * rss, Wc * rss), np.uint8)
        gid = np.full(mask.shape, -1, np.int16)
        for gi, gl in enumerate(glyphs):
            b = gl["box"]
            w, h = b[2] - b[0], b[3] - b[1]
            if w <= 0 or h <= 0:
                continue
            im = Image.new("L", (w, h), 0)
            ImageDraw.Draw(im).text((-b[0], -b[1]), gl["ch"], font=fonts[gl["line"]], fill=255, anchor="ls")
            a = np.asarray(im)
            X = gl["x"] + b[0] - cx0 * rss
            Y = gl["base"] + b[1] - cy0 * rss
            sub = mask[Y:Y + h, X:X + w]
            np.maximum(sub, a, out=sub)
            gid[Y:Y + h, X:X + w][a >= 128] = gi
            gl["cx"] = (gl["x"] + (b[0] + b[2]) / 2) / rss - cx0
            gl["cy"] = (gl["base"] + (b[1] + b[3]) / 2) / rss - cy0
        maskf = mask.astype(np.float32) * (1.0 / 255.0)
        A1 = cv2.resize(maskf, (Wc, Hc), interpolation=cv2.INTER_AREA)
        dist = cv2.distanceTransform((mask >= 128).astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
        if rss == 4:
            d2 = cv2.resize(dist, (Wc * 2, Hc * 2), interpolation=cv2.INTER_AREA) / rss
            a2 = cv2.resize(maskf, (Wc * 2, Hc * 2), interpolation=cv2.INTER_AREA)
        else:
            d2, a2 = dist / rss, maskf
        del dist, maskf
        # rows belong to the nearest line (lines never overlap vertically)
        mids = np.array([bases[i] - caps[i] / 2 for i in range(n)], np.float32)
        capa = np.array(caps, np.float32)
        rows2 = (np.arange(Hc * 2, dtype=np.float32) + 0.5) / 2 + cy0
        lid2 = np.argmin(np.abs(rows2[:, None] - mids[None, :]), axis=1)
        bev2 = np.maximum(0.7, p["bevel"] / 100.0 * capa[lid2])[:, None]
        xx = np.clip(d2 / bev2, 0, 1)
        r = p["bevel_round"] / 100.0
        prof = (1 - r) * xx + r * np.sqrt(np.clip(1 - (1 - xx) ** 2, 0, 1))
        hgt2 = (prof * bev2).astype(np.float32)
        yrel2 = ((rows2 - mids[lid2]) / (capa[lid2] / 2)).astype(np.float32)[:, None]
        g1 = gid[rss // 2::rss, rss // 2::rss][:Hc, :Wc].astype(np.int32)
        dil = cv2.dilate((g1 + 1).astype(np.uint16), np.ones((5, 5), np.uint8))
        g1 = np.where(g1 >= 0, g1, dil.astype(np.int32) - 1).astype(np.int16)
        del gid, mask
        ys, xs = np.nonzero(A1 > 0.5)
        ink = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1) if xs.size else (0, 0, Wc, Hc)
        im = int(np.argmax(scales))
        self.g = {
            "font": fi.file, "font_name": fi.display, "lines": lines, "scales": scales, "caps": caps,
            "bases": bases, "block_h": block_h, "cap": cap, "rss": rss, "Wc": Wc, "Hc": Hc,
            "cx0": int(cx0), "cy0": int(cy0), "A1": A1, "a2": a2, "hgt2": hgt2, "yrel2": yrel2,
            "gid1": g1, "glyphs": glyphs, "ink": ink, "warn": warn,
            "glint_y": bases[im] - 0.78 * caps[im] - cy0,
            "A_blur": cv2.GaussianBlur(A1, (0, 0), max(0.8, 0.3 * cap)),
        }
        self.cap = cap

    # ---------- shading maps (premultiplied by coverage, title canvas at 1x) ----------
    def _build_shade(self):
        p, g = self.p, self.g
        Wc, Hc = g["Wc"], g["Hc"]
        hgt2, a2 = g["hgt2"], g["a2"]
        dep = p["depth"] / 100.0
        gx = cv2.Sobel(hgt2, cv2.CV_32F, 1, 0, ksize=3) * (2.0 / 8.0 * dep)
        gy = cv2.Sobel(hgt2, cv2.CV_32F, 0, 1, ksize=3) * (2.0 / 8.0 * dep)
        inv = 1.0 / np.sqrt(gx * gx + gy * gy + 1.0)
        nx, ny, nz = -gx * inv, -gy * inv, inv
        th, ph = math.radians(p["key_angle"]), math.radians(p["key_elev"])
        L = np.array([math.cos(ph) * math.cos(th), -math.cos(ph) * math.sin(th), math.sin(ph)], np.float32)
        lam = np.clip(nx * L[0] + ny * L[1] + nz * L[2], 0, 1)
        Hv = L + np.array([0, 0, 1], np.float32)
        Hv /= np.linalg.norm(Hv)
        spec = np.clip(nx * Hv[0] + ny * Hv[1] + nz * Hv[2], 0, 1) ** p["gloss"]
        ry = 2 * nz * ny
        e = -ry - (p["face_grad"] / 100.0 * 0.9) * g["yrel2"]
        hz = p["band_pos"] / 100.0 * 0.5
        sky = sstep(hz - 0.2, hz + 0.35, e)
        band = np.exp(-0.5 * ((e - hz - 0.03) / 0.07) ** 2)
        ra = math.radians(p["rim_angle"])
        rdx, rdy = math.cos(ra), -math.sin(ra)
        nxy = np.sqrt(nx * nx + ny * ny) + 1e-6
        facing = np.clip((nx * rdx + ny * rdy) / nxy, 0, 1)
        edge = 1 - nz
        rim = edge ** 1.2 * (0.25 + 0.75 * facing ** 0.8)

        def down(mp):
            return cv2.resize((mp * a2).astype(np.float32), (Wc, Hc), interpolation=cv2.INTER_AREA)

        A = g["A1"]
        s = {"A": A, "LAM": down(lam), "SPEC": down(spec), "SKY": down(sky), "BAND": down(band),
             "RIM": down(rim), "FACET": down(edge)}
        s["NX"] = down(nx) / np.maximum(A, 1e-3)
        seed = int(p["seed"])
        n1 = spectral_noise(512, seed + 3, 10, 48, beta=1.6)
        n2 = spectral_noise(512, seed + 4, 60, 200, beta=1.0)
        sc = 512.0 / (6.0 * self.cap) * 100.0 / p["tex_scale"]
        yy, xx = np.mgrid[0:Hc, 0:Wc].astype(np.float32)
        tex = 0.7 * wrap_sample(n1, xx * sc, yy * sc) + 0.5 * wrap_sample(n2, xx * sc + 77, yy * sc + 31)
        s["TEX"] = (tex / (tex.std() + 1e-6)).astype(np.float32)
        s["GX"], s["GY"] = xx + 0.5, yy + 0.5
        self.s = s

    # ---------- dissolve order, particles, sparkles, atmosphere seeds ----------
    def _build_order(self):
        p, g, s = self.p, self.g, self.s
        Wc, Hc = g["Wc"], g["Hc"]
        A = s["A"]
        seed = int(p["seed"])
        rng = np.random.default_rng(seed + 101)
        ys, xs = np.nonzero(A > 0.02)
        N = int(xs.size)
        X = xs.astype(np.float32)
        Y = ys.astype(np.float32)
        act_map = np.zeros((Hc, Wc), np.float32)
        o = {"N": N}
        if N:
            x0, x1, y0, y1 = X.min(), X.max() + 1, Y.min(), Y.max() + 1
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            d = p["dir"]
            if d == "ltr":
                base = (X - x0) / (x1 - x0)
            elif d == "rtl":
                base = 1 - (X - x0) / (x1 - x0)
            elif d == "ttb":
                base = (Y - y0) / (y1 - y0)
            elif d == "btt":
                base = 1 - (Y - y0) / (y1 - y0)
            elif d in ("center", "edges"):
                rr = np.hypot((X - cx) / (x1 - x0), (Y - cy) / (x1 - x0))
                base = rr / (rr.max() + 1e-6)
                if d == "edges":
                    base = 1 - base
            elif d == "letters":
                gl = g["glyphs"]
                order = sorted(range(len(gl)), key=lambda i: (gl[i]["line"], gl[i]["cx"]))
                rank = np.zeros(max(1, len(gl)), np.float32)
                for r_, i in enumerate(order):
                    rank[i] = r_
                gi = np.clip(g["gid1"][ys, xs].astype(np.int32), 0, len(rank) - 1)
                lo = np.full(len(rank), 1e9, np.float32)
                hi = np.full(len(rank), -1e9, np.float32)
                np.minimum.at(lo, gi, X)
                np.maximum.at(hi, gi, X)
                local = (X - lo[gi]) / np.maximum(hi[gi] - lo[gi], 1)
                base = (rank[gi] + 0.85 * local) / max(1, len(gl))
            else:
                base = np.zeros(N, np.float32)
            nsm = spectral_noise(256, seed + 7, 3, 14, beta=1.8)
            scn = 256.0 / (8.0 * self.cap)
            smooth = sample_pts(nsm, X * scn, Y * scn)
            smooth = np.clip(0.5 + 0.22 * smooth, 0, 1)
            j = p["jitter"] / 100.0
            act = (1 - j) * base + j * (0.8 * smooth + 0.2 * rng.random(N))
            act = ((act - act.min()) / (act.max() - act.min() + 1e-6)).astype(np.float32)
            act_map[ys, xs] = act
            o.update({
                "px": X + 0.5 + rng.uniform(-0.5, 0.5, N).astype(np.float32),
                "py": Y + 0.5 + rng.uniform(-0.5, 0.5, N).astype(np.float32),
                "pidx": (ys * Wc + xs).astype(np.int64), "act": act,
                "jit": rng.random(N).astype(np.float32),
                "lifef": rng.uniform(0.55, 1.45, N).astype(np.float32),
                "vx": rng.standard_normal(N).astype(np.float32),
                "vy": rng.standard_normal(N).astype(np.float32),
                "ph": rng.uniform(0, 2 * np.pi, N).astype(np.float32),
                "fq": rng.uniform(5, 16, N).astype(np.float32),
                "big": rng.random(N) < p["stars"] / 1000.0,
            })
            w = (s["SPEC"] / np.maximum(A, 1e-3)) ** 2 * A + 0.02 * A
            w = w.ravel().astype(np.float64)
            w /= w.sum()
            ne = 700
            pick = rng.choice(w.size, size=ne, p=w)
            o["spk"] = {"t0": np.sort(rng.uniform(0, 240, ne)), "dur": rng.uniform(0.35, 0.85, ne),
                        "x": (pick % Wc) + 0.5, "y": (pick // Wc) + 0.5, "size": rng.uniform(0.28, 0.62, ne),
                        "inten": rng.uniform(0.45, 1.0, ne), "ang": rng.uniform(0, 45, ne), "sel": rng.random(ne)}
        o["act_map"] = act_map
        psi = spectral_noise(256, seed + 5, 2, 9, beta=2.0)
        cxn = np.gradient(psi, axis=0)
        cyn = -np.gradient(psi, axis=1)
        nrm = np.sqrt((cxn ** 2 + cyn ** 2).mean()) + 1e-6
        o["curlx"], o["curly"] = (cxn / nrm).astype(np.float32), (cyn / nrm).astype(np.float32)
        nm = 140
        o["motes"] = {"x": rng.random(nm), "y": rng.random(nm), "vx": rng.normal(0.006, 0.004, nm),
                      "vy": -rng.uniform(0.004, 0.016, nm), "near": rng.random(nm) < 0.22,
                      "b": rng.uniform(0.25, 1.0, nm), "ph": rng.uniform(0, 2 * np.pi, nm),
                      "fq": rng.uniform(0.6, 2.2, nm)}
        o["smoke"] = Smoke(seed + 11)
        self.o = o

    # ---------- per-parameter constants ----------
    def _prepare(self):
        p, g = self.p, self.g
        self.T_in = p["t_in"]
        self.T1 = p["t_in"] + p["t_hold"]
        self.T_end = self.T1 + p["t_out"]
        self.fps = int(p["fps"])
        self.ax = self.W / 2.0
        self.ay = p["pos_y"] / 100.0 * self.H
        self.ox = int(round(self.W / 2.0)) + g["cx0"]
        self.oy = int(round(self.ay - g["block_h"] / 2.0)) + g["cy0"]
        self.c_metal = hex_lin(p["metal"])
        self.c_spec = hex_lin(p["spec_tint"])
        self.c_rim = hex_lin(p["rim_color"])
        self.c_bg = hex_lin(p["bg"])
        self.c_dust = hex_lin(p["dust_color"])
        self.c_dust_n = self.c_dust / max(float(luma3(self.c_dust)), 1e-4)
        self.c_mist = hex_lin(p["mist_color"])
        self.c_back = hex_lin(p["back_color"])
        mn = self.c_metal / max(float(luma3(self.c_metal)), 1e-4)
        white = np.array([1.0, 0.93, 0.8], np.float32)
        self.c_heat = (0.35 * mn + 0.65 * white).astype(np.float32)
        self.c_sweep = (0.45 * mn + 0.55 * white).astype(np.float32)
        self.c_glint = np.array([1.0, 0.9, 0.72], np.float32)
        self.c_spark = (0.3 * mn + 0.7 * np.array([1.0, 0.95, 0.86], np.float32)).astype(np.float32)
        rn = self.c_rim / max(float(self.c_rim.max()), 1e-4)
        self.c_streak = rn.reshape(1, 1, 3).astype(np.float32)
        self.c_mote = (0.5 * mn + 0.5 * self.c_rim / max(float(luma3(self.c_rim)), 1e-4)).astype(np.float32)

    # ---------- the frame ----------
    def frame(self, t, fidx=None, clean=False, quick=False):
        """quick is accepted for API parity with the logo module; a title frame is always exact."""
        p, W, H = self.p, self.W, self.H
        t = float(t)
        if fidx is None:
            fidx = int(round(t * self.fps))
        u = t / self.T_in if self.T_in > 0 else 1.0
        fin = env01(u, 0.0, p["fade_in"] / 100.0) if p["fade_in"] > 0 else 1.0
        fe = p["end_fade"]
        te = self.T_end
        if fe > 0:
            fout = 1.0 - env01(t, te - fe, te - 1.0 / self.fps)
        else:
            fout = 0.0 if t >= te else 1.0
        G = fin * fout
        mode = out_kind(p)
        if G <= 0.0:
            return blank_out(H, W, mode, key_rgb(p))
        br = self._breath(t)
        img = np.empty((H, W, 3), np.float32)
        if mode == "scene":
            img[:] = self.c_bg
            self._ao = None
        else:
            img[:] = 0.0
            self._ao = np.zeros((H, W), np.float32)   # how much of each pixel the letters cover
        if not clean:
            self._atmos(img, t, u, mode == "scene")
        self._title(img, t, u, br)
        if not clean:
            self._sparkles(img, t)
        self._particles(img, t)
        return self._finish(img, G, br, fidx, clean)

    def _breath(self, t):
        b = self.p["breath"] / 100.0
        if b <= 0 or t <= self.T_in:
            return 0.0
        return b * math.sin(2 * math.pi * (t - self.T_in) / 4.2) * env01(t, self.T_in, self.T_in + 1.2)

    def _camera(self, u):
        if u >= 1.0:
            return 1.0, 0.0
        e = 1.0 - (1.0 - max(u, 0.0)) ** 2.4
        return 1.0 - self.p["push"] / 100.0 * (1.0 - e), self.p["rise"] / 100.0 * self.H * (1.0 - e)

    def _focus(self, u):
        ft = self.p["focus_to"] / 100.0
        if self.p["focus"] <= 0 or u >= ft:
            return 0.0
        return self.p["focus"] * self.rs * (1.0 - max(u, 0.0) / ft) ** 1.6

    def _light(self, envA, rimA, br):
        p, s = self.p, self.s
        A = s["A"]
        X = envA * (p["ambient"] * A + p["key"] * 0.5 * s["LAM"] + p["env_floor"] * (A - s["SKY"])
                    + p["env_sky"] * s["SKY"] + p["env_band"] * s["BAND"])
        col = X[..., None] * self.c_metal
        if p["spec"] > 0:
            col += (envA * p["spec"] * s["SPEC"])[..., None] * self.c_spec
        if p["rim"] > 0 and rimA > 0:
            col += (rimA * p["rim"] * (1.0 + 0.3 * br) * s["RIM"])[..., None] * self.c_rim
        if p["texture"] > 0:
            col *= (1.0 + p["texture"] / 100.0 * 0.5 * s["TEX"])[..., None]
        return col

    def _settled(self):
        p = self.p
        key = (self.ver, p["metal"], p["spec_tint"], p["rim_color"], p["ambient"], p["key"], p["env_floor"],
               p["env_sky"], p["env_band"], p["spec"], p["rim"], p["texture"])
        return self.memo("settled", key, lambda: self._light(1.0, 1.0, 0.0).reshape(-1, 3))

    def _sweep_state(self, u):
        p = self.p
        sa, sd = p["sweep_at"] / 100.0, p["sweep_dur"] / 100.0
        if p["sweep"] <= 0 or sd <= 0 or u < sa or u > sa + sd:
            return None
        q = (u - sa) / sd
        e = 0.65 * q + 0.35 * q * q * (3 - 2 * q)
        k = math.tan(math.radians(p["sweep_angle"]))
        x0, y0, x1, y1 = self.g["ink"]
        us = [x + k * y for x in (x0, x1) for y in (y0, y1)]
        umin, umax = min(us), max(us)
        wf = p["sweep_width"] / 100.0
        sw, sc = 0.9 * self.cap * wf, 0.15 * self.cap * wf
        span = umax - umin + 6 * sw
        c = umin - 3 * sw + span * e
        speed = span / (sd * self.T_in) * (0.65 + 0.35 * 6 * q * (1 - q))  # d(c)/dt in canvas px per second
        blur = speed * 0.5 / self.fps
        return {"q": q, "k": k, "c": c, "sw": sw, "sc": math.sqrt(sc * sc + blur * blur / 12.0)}

    def _canvas(self, t, u, br):
        p, s = self.p, self.s
        envA = env01(u, p["key_from"] / 100.0, p["key_to"] / 100.0)
        rimA = env01(u, p["rim_from"] / 100.0, p["rim_to"] / 100.0)
        A = s["A"]
        if u >= 1.0 and br == 0.0:
            col = self._settled().reshape(A.shape + (3,)).copy()
        else:
            col = self._light(envA, rimA, br)
        sw = self._sweep_state(u)
        if sw is not None:
            k = sw["k"]
            U = self.memo("U", k, lambda: s["GX"] + k * s["GY"] - 0.6 * self.cap * s["NX"])
            du = U - sw["c"]
            wide = np.exp(-0.5 * (du / sw["sw"]) ** 2)
            core = np.exp(-0.5 * (du / sw["sc"]) ** 2)
            col += (p["sweep"] * 0.3 * wide * A)[..., None] * self.c_metal
            col += (p["sweep"] * 2.4 * core * (0.3 * A + 1.7 * s["FACET"]))[..., None] * self.c_sweep
        to = p["t_out"]
        ds, de = p["dis_from"] / 100.0, p["dis_to"] / 100.0
        ph = max(p["preheat"], 1e-3)
        if t >= self.T1 + to * ds - ph and self.o["N"]:
            tact = self.T1 + to * (ds + (de - ds) * self.o["act_map"])
            hh = np.clip((t - (tact - ph)) / ph, 0, 1)
            keep = 1.0 - np.clip((t - tact) / 0.08, 0, 1)
            heat = hh * hh * keep
            col *= keep[..., None]
            if p["heat"] > 0:
                col += (heat * A * p["heat"] * 2.2)[..., None] * self.c_heat
            A = A * keep
        return col, A, sw

    def _title(self, img, t, u, br):
        W, H = self.W, self.H
        g = self.g
        col, A, sw = self._canvas(t, u, br)
        s, dy = self._camera(u)
        tx = self.ax + s * (self.ox - self.ax)
        ty = self.ay + s * (self.oy - self.ay) + dy
        sig = self._focus(u)
        Wc, Hc = g["Wc"], g["Hc"]
        if s == 1.0 and dy == 0.0 and sig < 0.25:
            fx0, fy0 = max(0, self.ox), max(0, self.oy)
            fx1, fy1 = min(W, self.ox + Wc), min(H, self.oy + Hc)
            if fx1 > fx0 and fy1 > fy0:
                cx0, cy0 = fx0 - self.ox, fy0 - self.oy
                cs = col[cy0:cy0 + fy1 - fy0, cx0:cx0 + fx1 - fx0]
                ca = A[cy0:cy0 + fy1 - fy0, cx0:cx0 + fx1 - fx0]
                sub = img[fy0:fy1, fx0:fx1]
                sub *= (1.0 - ca)[..., None]
                sub += cs
                if self._ao is not None:
                    sa = self._ao[fy0:fy1, fx0:fx1]
                    sa *= 1.0 - ca
                    sa += ca
        else:
            pad = int(math.ceil(3 * sig)) + 2
            rx0, ry0 = max(0, int(math.floor(tx)) - pad), max(0, int(math.floor(ty)) - pad)
            rx1, ry1 = min(W, int(math.ceil(tx + s * Wc)) + pad), min(H, int(math.ceil(ty + s * Hc)) + pad)
            if rx1 > rx0 and ry1 > ry0:
                M = np.float32([[s, 0, tx - rx0], [0, s, ty - ry0]])
                lay = np.concatenate([col, A[..., None]], axis=2)
                w = cv2.warpAffine(lay, M, (rx1 - rx0, ry1 - ry0), flags=cv2.INTER_LINEAR,
                                   borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
                if sig >= 0.25:
                    w = gblur(w, sig)
                sub = img[ry0:ry1, rx0:rx1]
                sub *= (1.0 - w[..., 3:4])
                sub += w[..., :3]
                if self._ao is not None:
                    sa = self._ao[ry0:ry1, rx0:rx1]
                    sa *= 1.0 - w[..., 3]
                    sa += w[..., 3]
        if sw is not None and self.p["glint"] > 0:
            yc = g["glint_y"]
            xc = sw["c"] - sw["k"] * yc
            ab = g["A_blur"]
            xi, yi = int(round(xc)), int(round(yc))
            cov = float(ab[min(max(yi, 0), Hc - 1), min(max(xi, 0), Wc - 1)]) if 0 <= xi < Wc else 0.0
            env_q = math.sin(math.pi * min(max(sw["q"], 0), 1)) ** 0.7
            gs = self.p["glint"] * min(1.0, cov * 1.6) * env_q
            if gs > 0.01:
                fx, fy = tx + s * xc - 0.5, ty + s * yc - 0.5
                rs = self.rs
                draw_star(img, fx, fy, 1.9 * self.cap * s * (0.8 + 0.4 * cov), 18 + 40 * sw["q"],
                          self.c_glint * 5.0 * gs, max(0.7, 1.6 * rs), ((0, 1), (90, 1), (45, 0.35), (135, 0.35)),
                          max(0.5, 0.8 * rs))

    def _sparkles(self, img, t):
        p, o = self.p, self.o
        amt = p["sparkle"] / 100.0
        if amt <= 0 or not o["N"] or t < self.T_in * 0.92:
            return
        ev = o["spk"]
        act = (ev["t0"] <= t) & (t < ev["t0"] + ev["dur"]) & (ev["sel"] < amt) & (ev["t0"] > self.T_in * 0.92)
        idx = np.flatnonzero(act)
        if not idx.size:
            return
        to = p["t_out"]
        ds, de = p["dis_from"] / 100.0, p["dis_to"] / 100.0
        am = o["act_map"]
        rs = self.rs
        for i in idx:
            q = (t - ev["t0"][i]) / ev["dur"][i]
            e = math.sin(math.pi * q) ** 2
            x, y = ev["x"][i], ev["y"][i]
            xi, yi = int(x), int(y)
            tact = self.T1 + to * (ds + (de - ds) * am[yi, xi])
            e *= 1.0 - env01(t, tact - p["preheat"] - 0.3, tact - 0.05)
            if e <= 0.01:
                continue
            draw_star(img, self.ox + x - 0.5, self.oy + y - 0.5, ev["size"][i] * self.cap, ev["ang"][i] + 25 * q,
                      self.c_spark * (3.2 * ev["inten"][i] * e), max(0.6, 1.1 * rs),
                      ((0, 1), (90, 1), (45, 0.3), (135, 0.3)), max(0.45, 0.7 * rs))

    def _ppos(self, ii, a):
        o, p = self.o, self.p
        cap = self.cap
        wa = math.radians(p["wind_angle"])
        wx, wy = math.cos(wa), -math.sin(wa)
        acc = p["wind"] * 0.9 * cap
        lift = p["lift"] * 0.35 * cap
        spd = p["spread"] * 0.25 * cap
        px, py = o["px"][ii], o["py"][ii]
        x = px + o["vx"][ii] * spd * a + 0.5 * acc * wx * a * a
        y = py + o["vy"][ii] * spd * a - lift * a + 0.5 * acc * wy * a * a
        if p["turb"] > 0:
            sc = 46.0 / (1.6 * cap)
            TX, TY = px * sc + a * 9.0, py * sc - a * 5.0
            amp = p["turb"] * 0.55 * cap * a ** 1.5
            x = x + sample_pts(o["curlx"], TX, TY) * amp
            y = y + sample_pts(o["curly"], TX, TY) * amp
        return x + self.ox, y + self.oy

    def _particles(self, img, t):
        p, o = self.p, self.o
        if not o["N"] or t < self.T1:
            return
        W, H = self.W, self.H
        to = p["t_out"]
        ds, de = p["dis_from"] / 100.0, p["dis_to"] / 100.0
        tb = self.T1 + to * (ds + (de - ds) * o["act"]) + 0.06 * o["jit"]
        age = t - tb
        life = p["life"] * o["lifef"]
        ii = np.flatnonzero((age > 0) & (age < life))
        if not ii.size:
            return
        a = age[ii]
        lf = life[ii]
        src = self._settled()[o["pidx"][ii]]
        lum = luma3(src)
        shift = sstep(0.05, 0.9, a)
        col = src * (1.0 - shift)[:, None] + (lum * 1.4 * shift)[:, None] * self.c_dust_n
        flare = 1.0 + p["heat"] * 2.5 * np.exp(-a / 0.18)
        tw = p["twinkle"] / 100.0
        twk = 1.0 + tw * (2.2 * (0.5 + 0.5 * np.sin(o["ph"][ii] + a * o["fq"][ii])) ** 6 - 0.3)
        fade = sstep(0.0, 0.05, a) * (1.0 - sstep(0.5 * lf, lf, a))
        col = (col * (flare * twk * fade)[:, None]).astype(np.float32)
        S = 4 if W >= 1600 else 3
        sh = 0.5 / self.fps
        parts = []
        first = None
        for k in range(S):
            ak = np.maximum(a - sh * k / (S - 1), 0.0) if S > 1 else a
            x, y = self._ppos(ii, ak)
            if k == 0:
                first = (x, y)
            q = splat_idx(W, H, x, y)
            if q is not None:
                parts.append((q[0], q[1] / S, q[2]))
        buf = accumulate(W, H, parts, col)
        if buf is not None:
            gr = p["grain"] / 100.0
            if gr > 1.0:
                buf = cv2.GaussianBlur(buf, (0, 0), (gr - 1.0) * 0.9 * self.rs + 0.3)
            img += buf
        big = o["big"][ii]
        if big.any():
            W2, H2 = max(2, W // 2), max(2, H // 2)
            x, y = first
            q = splat_idx(W2, H2, x[big] * 0.5, y[big] * 0.5)
            sb = accumulate(W2, H2, [q], col[big] * 2.6)
            if sb is not None:
                r2 = self.rs * 0.5
                core = cv2.GaussianBlur(sb, (0, 0), max(0.5, 1.1 * r2))
                cross = (cv2.GaussianBlur(sb, (0, 0), sigmaX=max(1.0, 7 * r2), sigmaY=0.45)
                         + cv2.GaussianBlur(sb, (0, 0), sigmaX=0.45, sigmaY=max(1.0, 7 * r2)))
                img += cv2.resize(core * 1.6 + cross * 2.2, (W, H), interpolation=cv2.INTER_LINEAR)

    def _atmos(self, img, t, u, scene=True):
        p = self.p
        lvl = env01(u, 0.05, 0.7)
        if lvl <= 0:
            return
        W4, H4 = self.q
        buf = np.zeros((H4, W4, 3), np.float32)
        used = False
        if scene and p["backlight"] > 0:
            bl = self.memo("bl", (W4, H4, round(self.ay, 2)), self._backlight_map)
            buf += (bl * (p["backlight"] * 0.22 * lvl))[..., None] * self.c_back
            used = True
        if scene and p["mist"] > 0:
            # slow, wide fog banks: 2.4x larger than the logo-intro smoke, about a third of its speed
            d = self.o["smoke"].density(t * 0.3, W4, H4, 2.4)
            vm = self.memo("vmask", (W4, H4, round(self.ay, 2)), self._mist_mask)
            buf += (d * vm * (p["mist"] * 0.22 * lvl))[..., None] * self.c_mist
            used = True
        if p["motes"] > 0:
            buf += self._motes(t, p["motes"]) * lvl
            used = True
        if used:
            img += cv2.resize(buf, (self.W, self.H), interpolation=cv2.INTER_LINEAR)

    def _backlight_map(self):
        W4, H4 = self.q
        gy, gx = np.mgrid[0:H4, 0:W4].astype(np.float32)
        cy = self.ay / 4.0
        return np.exp(-0.5 * (((gx - W4 / 2) / (0.42 * W4)) ** 2 + ((gy - cy) / (0.2 * H4)) ** 2)).astype(np.float32)

    def _mist_mask(self):
        W4, H4 = self.q
        gy = np.arange(H4, dtype=np.float32)[:, None]
        cy = self.ay / 4.0
        return (0.35 + 0.65 * np.exp(-0.5 * ((gy - cy) / (0.22 * H4)) ** 2)).astype(np.float32) * np.ones((1, W4), np.float32)

    def _motes(self, t, amt):
        m = self.o["motes"]
        W4, H4 = self.q
        n = len(m["x"])
        k = min(n, max(1, int(round(n * min(amt, 2.0) / 2.0))))
        gain = max(1.0, amt / 2.0)
        x = (np.mod(m["x"][:k] + m["vx"][:k] * t, 1.1) - 0.05) * W4
        y = (np.mod(m["y"][:k] + m["vy"][:k] * t, 1.1) - 0.05) * H4
        b = m["b"][:k] * (0.55 + 0.45 * np.sin(m["ph"][:k] + t * m["fq"][:k])) * gain
        near = m["near"][:k]
        r4 = H4 / 270.0
        out = np.zeros((H4, W4), np.float32)
        for sel, sig, peak in ((~near, max(0.5, 0.6 * r4), 0.05), (near, max(1.0, 3.0 * r4), 0.035)):
            if not sel.any():
                continue
            q = splat_idx(W4, H4, x[sel], y[sel])
            v = (b[sel] * peak * 2 * math.pi * sig * sig).astype(np.float32)
            out += cv2.GaussianBlur(accumulate1(W4, H4, q, v), (0, 0), sig)
        return out[..., None] * self.c_mote

    def _vignette(self):
        W, H = self.W, self.H
        gy, gx = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.hypot((gx - W / 2) / (W / 2), (gy - H / 2) / (W / 2))
        return (1 - self.p["vignette"] * np.clip(r / 1.15, 0, 1) ** 2.4).astype(np.float32)[..., None]

    def _finish(self, img, G, br, fidx, clean):
        p = self.p
        gl = p["glow"] * (1.0 + 0.3 * br)
        if gl > 0:
            img += bloom(img, self.rs) * gl
        if p["streak"] > 0:
            img += streak(img, self.c_streak) * p["streak"]
        if p["vignette"] > 0 and self._ao is None:
            img *= self.memo("vig", p["vignette"], self._vignette)
        k = G * (2.0 ** p["exposure"])
        if k != 1.0:
            img *= k
        np.maximum(img, 0, out=img)
        img = img * img / (img + 0.002)
        lut, vmax = tone_lut(p["knee"] / 100.0)
        idx = (np.sqrt(np.minimum(img, vmax) * (1.0 / vmax)) * 65535).astype(np.uint16)
        out = lut[idx]
        luma = luma3(out)
        if p["saturation"] != 1.0:
            out = luma[..., None] + p["saturation"] * (out - luma[..., None])
        if not clean and p["film_grain"] > 0:
            rng = np.random.default_rng(fidx * 7919 + int(p["seed"]))
            gr = p["film_grain"] / 100.0 * 0.04
            out += (rng.standard_normal(luma.shape, dtype=np.float32) * gr * np.sqrt(np.maximum(luma, 0)))[..., None]
        if self._ao is None:
            return (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)
        A = np.maximum(light_alpha(out), G * self._ao)
        return compose_out(out, A, out_kind(p), key_rgb(p))


# ----------------------------------------------------------------------------------------------
# Recipes and CLI
# ----------------------------------------------------------------------------------------------

def load_recipe(path):
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    params = d.get("params", d) if isinstance(d, dict) else {}
    return {"name": d.get("name", os.path.splitext(os.path.basename(path))[0]) if isinstance(d, dict) else "",
            "module": MODULE_ID, "params": merge(params)}


def recipe_doc(name, params):
    return {"name": name, "module": MODULE_ID, "engine": ENGINE_VERSION,
            "saved": time.strftime("%Y-%m-%d %H:%M:%S"), "params": merge(params)}


def default_name(p):
    return " ".join(split_lines(p["title"])) or "Title"


def file_slug(p):
    return slugify(p["title"])


Renderer = Title


_WT = None


def _winit(path):
    global _WT
    cv2.setNumThreads(1)
    with open(path, "rb") as f:
        _WT = pickle.load(f)


def _wframe(i):
    return _WT.frame(i / _WT.fps, fidx=i).tobytes()


def cmd_stills(a):
    rec = load_recipe(a.recipe)
    p = rec["params"]
    W, H = parse_res(a.res or p["res"])
    t0 = time.time()
    proj = Project(a.project) if getattr(a, "project", None) else None
    T = Title(p, W, H, ctx=proj)
    print(f"build {time.time() - t0:.2f}s  particles {T.o['N']}  canvas {T.g['Wc']}x{T.g['Hc']}  {T.g['warn']}")
    os.makedirs(a.out, exist_ok=True)
    for ts in a.times.split(","):
        t = float(ts)
        t1 = time.time()
        fr = T.frame(t, clean=a.clean)
        name = os.path.join(a.out, f"{a.prefix}t{t:05.2f}.png")
        write_png(name, fr)
        print(f"{name}  {(time.time() - t1) * 1000:.0f} ms")


def cmd_render(a):
    import multiprocessing as mp
    rec = load_recipe(a.recipe)
    p = rec["params"]
    if a.res:
        p["res"] = a.res
    if a.fps:
        p["fps"] = str(a.fps)
    if a.crf is not None:
        p["crf"] = a.crf
    p = merge(p)
    W, H = parse_res(p["res"])
    fps, crf = int(p["fps"]), int(p["crf"])
    preset = "slow"
    if a.draft:
        W, H = parse_res(a.draft_res)
        crf, preset = 22, "veryfast"
    t_start = time.time()
    emit(ev="start", msg="Preparing letters and light maps")
    proj = Project(a.project) if getattr(a, "project", None) else None
    T = Title(p, W, H, ctx=proj)
    N1 = int(round(T.T1 * fps))
    N = int(round(T.T_end * fps))
    slug = a.slug or slugify(p["title"])
    os.makedirs(a.out, exist_ok=True)
    tag = f"{W}x{H}_{fps}fps"
    fmt = out_format(p, a.draft)
    kind = out_kind(p)
    if kind == "alpha":
        tag += "_alpha"
    elif kind == "color":
        tag += "_key"
    if a.draft:
        files = [os.path.join(a.out, out_name(f"{slug}_preview_{tag}", fmt))]
    else:
        files = [os.path.join(a.out, out_name(f"{slug}_1_entry_{tag}", fmt)),
                 os.path.join(a.out, out_name(f"{slug}_2_dissolve_{tag}", fmt))]
    state = os.path.join(a.out, f".{slug}_{os.getpid()}_state.pkl")
    with open(state, "wb") as f:
        pickle.dump(T, f, protocol=pickle.HIGHEST_PROTOCOL)
    procs = [subprocess.Popen(video_cmd(fp, W, H, fps, crf, preset, fmt), stdin=subprocess.PIPE) for fp in files]
    nw = max(2, min(a.workers or (os.cpu_count() or 4) - 1, 16))
    emit(ev="info", frames=N, split=N1, workers=nw, files=[os.path.basename(x) for x in files],
         particles=int(T.o["N"]), warnings=T.g["warn"])
    try:
        mpc = mp.get_context("spawn")
        tlast = 0.0
        with mpc.Pool(nw, initializer=_winit, initargs=(state,)) as pool:
            for i, buf in enumerate(pool.imap(_wframe, range(N), chunksize=2)):
                pr = procs[0] if (a.draft or i < N1) else procs[1]
                pr.stdin.write(buf)
                if not a.draft and i == N1 - 1:
                    procs[0].stdin.close()
                now = time.time()
                if now - tlast > 0.5 or i == N - 1:
                    tlast = now
                    el = now - t_start
                    rate = (i + 1) / max(el, 1e-6)
                    emit(ev="progress", done=i + 1, total=N, fps=round(rate, 2), eta=round((N - i - 1) / max(rate, 1e-6), 1))
        for pr in procs:
            if pr.stdin and not pr.stdin.closed:
                pr.stdin.close()
        for pr in procs:
            if pr.wait() != 0:
                raise RuntimeError("ffmpeg exited with an error")
        out = list(files)
        if not a.draft:
            still = os.path.join(a.out, f"{slug}_still_{W}x{H}{'_alpha' if kind == 'alpha' else ''}.png")
            fr = T.frame(T.T_in + min(1.0, p["t_hold"] * 0.5), clean=True)
            write_png(still, fr)
            rp = os.path.join(a.out, f"{slug}_recipe.json")
            with open(rp, "w", encoding="utf-8") as f:
                json.dump(recipe_doc(rec.get("name") or slug, p), f, ensure_ascii=False, indent=2)
            out += [still, rp]
        emit(ev="done", files=[os.path.basename(x) for x in out], sec=round(time.time() - t_start, 1))
    except BaseException as e:
        for pr in procs:
            try:
                pr.kill()
            except Exception:
                pass
        emit(ev="error", msg=str(e) or e.__class__.__name__)
        raise
    finally:
        try:
            os.remove(state)
        except OSError:
            pass


def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="Titles, a module of the CROM app")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stills")
    s.add_argument("recipe")
    s.add_argument("out")
    s.add_argument("--times", default="2,5,7.5,9,12,16,18,21")
    s.add_argument("--res", default=None)
    s.add_argument("--prefix", default="")
    s.add_argument("--clean", action="store_true")
    s.add_argument("--project", default=None)
    r = sub.add_parser("render")
    r.add_argument("recipe")
    r.add_argument("out")
    r.add_argument("--res", default=None)
    r.add_argument("--fps", type=int, default=None)
    r.add_argument("--crf", type=int, default=None)
    r.add_argument("--slug", default=None)
    r.add_argument("--workers", type=int, default=None)
    r.add_argument("--draft", action="store_true")
    r.add_argument("--draft-res", default="960x540")
    r.add_argument("--project", default=None)
    a = ap.parse_args()
    if a.cmd == "stills":
        cmd_stills(a)
    else:
        cmd_render(a)


if __name__ == "__main__":
    main()
