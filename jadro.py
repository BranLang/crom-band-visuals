#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CROM: spolocne jadro aplikacie.

Tu je vsetko, co moduly zdielaju:
  - cesty a projekty (kazda kapela ma vlastny priecinok v projekty/),
  - hladanie modulov v moduly/ (novy efekt = novy subor, nic sa nikde nezapisuje),
  - spolocna skupina Vystup (rozlisenie, fps, kvalita, pozadie: scena, priehladne, klucova farba),
  - kodovanie cez ffmpeg (mp4, ProRes 4444, WebM s alfou, PNG sekvencia, HAP),
  - male pomocne funkcie pre svetlo, sum, pisma a texty.

Funkcie od _p po emit su presne tie iste ako v povodnom engine.py, aby sa po presune
nezmenil ani jeden pixel.
"""
import importlib.util
import json
import math
import os
import re
import sys
import time
import unicodedata

import cv2
import numpy as np
from fontTools.ttLib import TTFont
from PIL import ImageFont

APP_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(APP_DIR, "fonts")
SHARED_FONTS = FONT_DIR
MODULES_DIR = os.path.join(APP_DIR, "moduly")
PROJECTS_DIR = os.path.join(APP_DIR, "projekty")
APP_NAME = "CROM"
APP_TAGLINE = "vizuály pre kapely"
CORE_VERSION = 1


# ----------------------------------------------------------------------------------------------
# Parameter schema helpers. A module's SCHEMA is the single source of truth for the UI,
# recipes and the renderer. rebuild names what has to be recomputed when the value changes.
# ----------------------------------------------------------------------------------------------

def _p(key, label, typ, default, rebuild=None, **kw):
    d = {"key": key, "label": label, "type": typ, "default": default}
    if rebuild:
        d["rebuild"] = rebuild
    d.update(kw)
    return d


def F(key, label, default, lo, hi, step, unit="", rebuild=None, hint=""):
    d = _p(key, label, "float", default, rebuild, min=lo, max=hi, step=step, unit=unit)
    if hint:
        d["hint"] = hint
    return d


def font_path(name):
    return os.path.join(FONT_DIR, os.path.basename(str(name)))


def coerce(spec, v):
    t = spec["type"]
    try:
        if t == "float":
            return float(min(max(float(v), spec["min"]), spec["max"]))
        if t == "int":
            return int(min(max(int(round(float(v))), spec["min"]), spec["max"]))
        if t == "bool":
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "yes", "on", "ano", "áno")
            return bool(v)
        if t == "color":
            s = str(v).strip()
            return s.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", s) else spec["default"]
        if t == "select":
            s = str(v)
            return s if s in [o[0] for o in spec["options"]] else spec["default"]
        if t == "font":
            s = os.path.basename(str(v))
            return s if os.path.isfile(font_path(s)) else spec["default"]
        if t == "asset":
            s = os.path.basename(str(v or "")).strip()
            return s if s and not s.startswith(".") else spec["default"]
        return str(v)
    except (TypeError, ValueError):
        return spec["default"]


# Building blocks for new modules: one SCHEMA in, the standard module functions out.

def params_of(schema):
    return {p["key"]: p for g in schema for p in g["params"]}


def defaults_of(params):
    return {k: coerce(p, p["default"]) for k, p in params.items()}


def merge_into(params, src=None):
    """Every parameter of the schema, checked and clamped; unknown keys are dropped."""
    src = src or {}
    return {k: coerce(spec, src[k] if k in src else spec["default"]) for k, spec in params.items()}


def level_key_of(params, p, levels):
    return json.dumps({k: p[k] for k in sorted(params) if params[k].get("rebuild") in levels},
                      sort_keys=True, ensure_ascii=True)


def read_recipe(path, module_id, merge):
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    params = d.get("params", d) if isinstance(d, dict) else {}
    name = d.get("name", os.path.splitext(os.path.basename(path))[0]) if isinstance(d, dict) else ""
    return {"name": name, "module": module_id, "params": merge(params)}


def make_recipe_doc(name, params, module_id, version, merge):
    return {"name": name, "module": module_id, "engine": version,
            "saved": time.strftime("%Y-%m-%d %H:%M:%S"), "params": merge(params)}


# ----------------------------------------------------------------------------------------------
# The shared Output group: size, frame rate, quality, seed and the background
# ----------------------------------------------------------------------------------------------

RES_WIDE = [["1920x1080", "Full HD 1920×1080"], ["2560x1440", "QHD 2560×1440"], ["1280x720", "HD 1280×720"]]
RES_ALL = RES_WIDE + [["1080x1920", "Na výšku 1080×1920"], ["1080x1080", "Štvorec 1080×1080"]]
FPS_OPTIONS = [["24", "24"], ["25", "25"], ["30", "30"], ["50", "50"], ["60", "60"]]
BG_MODES = [["scene", "Scéna (pozadie modulu)"], ["alpha", "Priehľadné (alfa kanál)"],
            ["color", "Kľúčová farba (green screen)"]]
ALPHA_FORMATS = {
    "prores4444": {"label": "ProRes 4444 (.mov, pre strih)", "ext": ".mov"},
    "webm": {"label": "WebM VP9 (.webm, pre web)", "ext": ".webm"},
    "png": {"label": "PNG sekvencia (priečinok obrázkov)", "ext": ""},
    "hap": {"label": "HAP Alpha (.mov, pre Resolume a VJ)", "ext": ".mov"},
}
VIDEO_EXT = (".mp4", ".mov", ".webm")


def bg_params(hint=""):
    d = _p("bg_mode", "Pozadie", "select", "scene", options=[list(o) for o in BG_MODES],
           hint=hint or "Priehľadné pozadie ide do strihu alebo VJ softvéru cez obraz. "
                        "Kľúčová farba je pre programy, ktoré alfu nevedia.")
    return [
        d,
        _p("bg_key", "Kľúčová farba", "color", "#00b140", show_if={"bg_mode": "color"},
           hint="Farba pozadia, ktorú strih vykľúčuje. Zelená #00b140 alebo modrá #0047bb."),
        _p("alpha_format", "Formát s alfou", "select", "prores4444",
           options=[[k, v["label"]] for k, v in ALPHA_FORMATS.items()], show_if={"bg_mode": "alpha"},
           hint="ProRes 4444 pre Premiere, DaVinci a Final Cut, HAP pre Resolume, WebM pre web, "
                "PNG sekvencia pre hocičo. Rýchly náhľad je vždy WebM."),
    ]


def output_params(seed=7, seed_rebuild=None, seed_hint="", fps_rebuild=None, res_options=None,
                  res_default="1920x1080", bg=True, bg_hint=""):
    res = RES_ALL if res_options is None else res_options
    seed_kw = {"min": 0, "max": 9999, "step": 1}
    if seed_hint:
        seed_kw["hint"] = seed_hint
    out = [
        _p("res", "Rozlíšenie", "select", res_default, options=[list(o) for o in res]),
        _p("fps", "Snímky za sekundu", "select", "30", fps_rebuild, options=[list(o) for o in FPS_OPTIONS]),
        _p("crf", "Kvalita (CRF)", "int", 14, min=8, max=28, step=1,
           hint="Menšie číslo = lepšia kvalita a väčší súbor."),
        _p("seed", "Náhoda", "int", seed, seed_rebuild, **seed_kw),
    ]
    if bg:
        out += bg_params(bg_hint)
    return out


# ----------------------------------------------------------------------------------------------
# Light, noise and blur helpers, kerned fonts and text helpers (moved verbatim from engine.py)
# ----------------------------------------------------------------------------------------------

def sstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0 + 1e-12), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def env01(t, a, b):
    if b <= a:
        return 1.0 if t >= b else 0.0
    x = min(max((t - a) / (b - a), 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


def hex_lin(h):
    h = h.lstrip("#")
    c = np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.float64) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return c.astype(np.float32)


def luma3(a):
    return a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722


def spectral_noise(n, seed, k_lo, k_hi, beta=2.0):
    rng = np.random.default_rng(seed)
    ky = np.fft.fftfreq(n) * n
    kx = np.fft.rfftfreq(n) * n
    kk = np.sqrt(ky[:, None] ** 2 + kx[None, :] ** 2)
    band = sstep(k_lo * 0.7, k_lo, kk) * (1.0 - sstep(k_hi, k_hi * 1.4, kk))
    amp = band * np.maximum(kk, 1.0) ** (-beta / 2)
    f = np.fft.irfft2(amp * np.exp(2j * np.pi * rng.random(kk.shape)), s=(n, n)).astype(np.float32)
    return (f - f.mean()) / (f.std() + 1e-8)


def wrap_sample(tex, mx, my):
    n = tex.shape[0]
    return cv2.remap(tex, np.mod(mx, n).astype(np.float32), np.mod(my, n).astype(np.float32),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)


def sample_pts(tex, x, y):
    """wrap_sample for 1-D point lists (cv2.remap maps must stay under 32767 per side)."""
    n = x.size
    if n == 0:
        return np.zeros(0, np.float32)
    m = 1024
    rows = (n + m - 1) // m
    pad = rows * m - n
    mx = np.pad(x.astype(np.float32), (0, pad)).reshape(rows, m)
    my = np.pad(y.astype(np.float32), (0, pad)).reshape(rows, m)
    return wrap_sample(tex, mx, my).ravel()[:n]


def splat_idx(W, H, x, y):
    """Bilinear splat of points at continuous coords (pixel i spans [i, i+1)). Returns idx, weight, point."""
    x = x - 0.5
    y = y - 0.5
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    fx = (x - x0).astype(np.float32)
    fy = (y - y0).astype(np.float32)
    I, Wt, P = [], [], []
    for dx, dy, w in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
        xi = x0 + dx
        yi = y0 + dy
        ok = (xi >= 0) & (xi < W) & (yi >= 0) & (yi < H)
        if not ok.any():
            continue
        I.append(yi[ok] * W + xi[ok])
        Wt.append(w[ok])
        P.append(np.flatnonzero(ok))
    if not I:
        return None
    return np.concatenate(I), np.concatenate(Wt), np.concatenate(P)


def accumulate(W, H, parts, cols):
    """parts: list of (idx, w, p) from splat_idx; cols: (n, 3) colour per point. Returns (H, W, 3)."""
    parts = [q for q in parts if q is not None]
    if not parts:
        return None
    idx = np.concatenate([q[0] for q in parts])
    w = np.concatenate([q[1] for q in parts])
    pt = np.concatenate([q[2] for q in parts])
    out = np.empty((H * W, 3), np.float32)
    for c in range(3):
        out[:, c] = np.bincount(idx, weights=w * cols[pt, c], minlength=H * W)
    return out.reshape(H, W, 3)


def accumulate1(W, H, part, vals):
    if part is None:
        return np.zeros((H, W), np.float32)
    idx, w, pt = part
    return np.bincount(idx, weights=w * vals[pt], minlength=H * W).astype(np.float32).reshape(H, W)


def draw_star(img, x, y, R, angle, col, core, rays, width):
    H, W = img.shape[:2]
    x0, x1 = max(int(x - R), 0), min(int(x + R) + 2, W)
    y0, y1 = max(int(y - R), 0), min(int(y + R) + 2, H)
    if x0 >= x1 or y0 >= y1:
        return
    gy, gx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    dx, dy = gx - x, gy - y
    r2 = dx * dx + dy * dy
    v = np.exp(-0.5 * r2 / core ** 2) + 0.18 * np.exp(-0.5 * r2 / (5 * core) ** 2)
    for th, ln in rays:
        a = math.radians(th + angle)
        al = np.abs(dx * math.cos(a) + dy * math.sin(a))
        ac = -dx * math.sin(a) + dy * math.cos(a)
        v += ln * np.exp(-al / (R * 0.2 * ln)) * np.exp(-0.5 * (ac / width) ** 2) * (1 - sstep(0.6 * R, R, al))
    img[y0:y1, x0:x1] += v[..., None] * np.asarray(col, np.float32)


def gblur(a, sig):
    if sig < 0.25:
        return a
    if sig <= 8:
        return cv2.GaussianBlur(a, (0, 0), sig)
    f = sig / 4.0
    h, w = a.shape[:2]
    sw, sh = max(2, int(round(w / f))), max(2, int(round(h / f)))
    small = cv2.resize(a, (sw, sh), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 3.6)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def bloom(img, rs):
    H, W = img.shape[:2]
    cur = np.maximum(img - 0.62, 0)
    levels = []
    for (lv, sg), wgt in zip(((2, 3.0), (4, 4.0), (8, 5.0), (16, 6.0)), (1.0, 0.7, 0.4, 0.2)):
        cur = cv2.resize(cur, (max(1, W // lv), max(1, H // lv)), interpolation=cv2.INTER_AREA)
        levels.append(cv2.GaussianBlur(cur, (0, 0), max(0.5, sg * rs)) * wgt)
    acc = levels[-1]
    for g in reversed(levels[:-1]):
        acc = cv2.resize(acc, (g.shape[1], g.shape[0]), interpolation=cv2.INTER_LINEAR) + g
    return cv2.resize(acc, (W, H), interpolation=cv2.INTER_LINEAR) * 0.42


def streak(img, tint):
    H, W = img.shape[:2]
    s = cv2.resize(np.maximum(img - 1.6, 0), (max(1, W // 8), max(1, H // 8)), interpolation=cv2.INTER_AREA)
    s = cv2.GaussianBlur(s, (0, 0), sigmaX=W / 8 * 0.11, sigmaY=0.6)
    s = s.mean(axis=2, keepdims=True) * tint
    return cv2.resize(s, (W, H), interpolation=cv2.INTER_LINEAR)


_LUTS = {}


def tone_lut(knee):
    key = round(float(knee), 4)
    if key not in _LUTS:
        vmax = 64.0
        i = np.linspace(0, 1, 65536)
        v = i * i * vmax
        y = np.where(v < knee, v, knee + (1 - knee) * np.tanh((v - knee) / (1 - knee)))
        _LUTS[key] = (np.clip(y, 0, 1) ** (1 / 2.2)).astype(np.float32), vmax
    return _LUTS[key]


class Smoke:
    """Stateless drifting mist (from the logo intro)."""

    def __init__(self, seed):
        self.n = spectral_noise(512, seed, 3, 18, beta=2.6)
        self.wx = spectral_noise(256, seed + 1, 1, 6)
        self.wy = spectral_noise(256, seed + 2, 1, 6)
        self.grid = None

    def density(self, t, W, H, z=1.0):
        if self.grid is None or self.grid[0].shape != (H, W):
            gy, gx = np.mgrid[0:H, 0:W].astype(np.float32)
            self.grid = ((gx - W / 2) / H, (gy - H / 2) / H)
        X, Y = self.grid[0] / z, self.grid[1] / z
        wx = wrap_sample(self.wx, X * 90 + 4 * t, Y * 90 + 7 * t) * 10
        wy = wrap_sample(self.wy, X * 90 - 3 * t, Y * 90 + 9 * t) * 10
        d = wrap_sample(self.n, X * 260 + wx + 6 * t, Y * 260 + wy + 22 * t)
        return np.clip((d + 0.2) / 2.0, 0, 1) ** 2


# ----------------------------------------------------------------------------------------------
# Fonts with real kerning (Pillow here has no raqm, so pairs come from GPOS / kern via fontTools)
# ----------------------------------------------------------------------------------------------

class FontInfo:
    def __init__(self, fn):
        self.file = os.path.basename(fn)
        self.path = font_path(self.file)
        tt = TTFont(self.path, lazy=True)
        self.upm = int(tt["head"].unitsPerEm)
        self.cmap = tt.getBestCmap() or {}
        self.adv = {g: m[0] for g, m in tt["hmtx"].metrics.items()}
        cap = int(getattr(tt["OS/2"], "sCapHeight", 0) or 0) if "OS/2" in tt else 0
        if cap <= 0:
            b = ImageFont.truetype(self.path, 1000).getbbox("H", anchor="ls")
            cap = int(round(-b[1] * self.upm / 1000.0)) or int(0.7 * self.upm)
        self.cap = cap
        nm = tt["name"]
        fam = nm.getDebugName(16) or nm.getDebugName(1) or self.file
        sub = nm.getDebugName(17) or nm.getDebugName(2) or ""
        self.display = fam if sub.lower() in ("", "regular", "normal", "book") else f"{fam} {sub}"
        self._lookups, self._legacy, self._pairs, self._pil = [], {}, {}, {}
        try:
            self._load_gpos(tt)
        except Exception:
            self._lookups = []
        if not self._lookups and "kern" in tt:
            try:
                for kt in tt["kern"].kernTables:
                    if hasattr(kt, "kernTable"):
                        self._legacy.update(kt.kernTable)
            except Exception:
                self._legacy = {}
        tt.close()

    def _load_gpos(self, tt):
        if "GPOS" not in tt:
            return
        t = tt["GPOS"].table
        if not (t.FeatureList and t.LookupList and t.ScriptList):
            return
        feats = t.FeatureList.FeatureRecord
        scripts = {sr.ScriptTag: sr.Script for sr in t.ScriptList.ScriptRecord}
        sc = scripts.get("latn") or scripts.get("DFLT") or next(iter(scripts.values()), None)
        idx = []
        if sc is not None and sc.DefaultLangSys is not None:
            for fi in sc.DefaultLangSys.FeatureIndex:
                if feats[fi].FeatureTag == "kern":
                    idx.extend(feats[fi].Feature.LookupListIndex)
        if not idx:
            for fr in feats:
                if fr.FeatureTag == "kern":
                    idx.extend(fr.Feature.LookupListIndex)
                    break
        for li in sorted(set(idx)):
            lk = t.LookupList.Lookup[li]
            subs = []
            for st in lk.SubTable:
                typ = lk.LookupType
                if typ == 9:
                    typ, st = st.ExtensionLookupType, st.ExtSubTable
                if typ != 2:
                    continue
                if st.Format == 1:
                    cov = {g: i for i, g in enumerate(st.Coverage.glyphs)}
                    sets = []
                    for ps in st.PairSet:
                        sets.append({r.SecondGlyph: (getattr(r.Value1, "XAdvance", 0) or 0) if r.Value1 else 0
                                     for r in ps.PairValueRecord})
                    subs.append((1, cov, sets))
                elif st.Format == 2:
                    cov = set(st.Coverage.glyphs)
                    c1 = st.ClassDef1.classDefs if st.ClassDef1 else {}
                    c2 = st.ClassDef2.classDefs if st.ClassDef2 else {}
                    recs = [[(getattr(r.Value1, "XAdvance", 0) or 0) if r.Value1 else 0 for r in c1r.Class2Record]
                            for c1r in st.Class1Record]
                    subs.append((2, cov, c1, c2, recs))
            if subs:
                self._lookups.append(subs)

    def glyph(self, ch):
        return self.cmap.get(ord(ch))

    def kern(self, left, right):
        key = (left, right)
        v = self._pairs.get(key)
        if v is not None:
            return v
        tot = 0
        for subs in self._lookups:
            for st in subs:
                if st[0] == 1:
                    i = st[1].get(left)
                    if i is None:
                        continue
                    val = st[2][i].get(right)
                    if val is None:
                        continue
                    tot += val
                    break
                if left not in st[1]:
                    continue
                c1, c2, recs = st[2].get(left, 0), st[3].get(right, 0), st[4]
                if c1 < len(recs) and c2 < len(recs[c1]):
                    tot += recs[c1][c2]
                break
        if not self._lookups:
            tot = self._legacy.get(key, 0)
        self._pairs[key] = tot
        return tot

    def pil(self, px):
        key = max(1.0, round(px * 4) / 4)
        f = self._pil.get(key)
        if f is None:
            f = ImageFont.truetype(self.path, key, layout_engine=ImageFont.Layout.BASIC)
            self._pil[key] = f
        return f


_FONTS = {}


def get_font(fn):
    fn = os.path.basename(fn)
    p = font_path(fn)
    mt = os.path.getmtime(p)
    f = _FONTS.get(fn)
    if f is None or f[0] != mt:
        f = (mt, FontInfo(fn))
        _FONTS[fn] = f
    return f[1]


def list_fonts():
    out = []
    if not os.path.isdir(FONT_DIR):
        return out
    for fn in sorted(os.listdir(FONT_DIR)):
        if fn.lower().endswith((".ttf", ".otf")):
            try:
                out.append({"file": fn, "name": get_font(fn).display})
            except Exception:
                pass
    return out


def split_lines(s):
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in str(s).replace("\r", "").split("\n")]
    return [ln for ln in lines if ln] or [""]


def strip_accents(ch):
    d = unicodedata.normalize("NFKD", ch)
    return "".join(c for c in d if not unicodedata.combining(c)) or ch


def slugify(text):
    s = " ".join(split_lines(text))
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return (s[:60].strip("_")) or "titulok"


# ----------------------------------------------------------------------------------------------
# Encoding
# ----------------------------------------------------------------------------------------------

def parse_res(s):
    w, h = str(s).lower().split("x")
    return int(w), int(h)


def ffmpeg_cmd(out, W, H, fps, crf, preset="slow", extra=None):
    return ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
            "-r", str(fps), "-i", "-", "-vf", "scale=out_color_matrix=bt709:out_range=tv",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"] + list(extra or []) + [
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-movflags", "+faststart", out]


def emit(**kw):
    print(json.dumps(kw, ensure_ascii=True), flush=True)


def out_kind(p):
    """scene = the module's own background, alpha = transparent, color = flat key colour."""
    k = str((p or {}).get("bg_mode", "scene"))
    return k if k in ("alpha", "color") else "scene"


def out_format(p, draft=False):
    """Encoder for a render: mp4 unless the background is transparent. Drafts with alpha are WebM,
    because the browser can play those with the transparency."""
    if out_kind(p) != "alpha":
        return "mp4"
    if draft:
        return "webm"
    f = str(p.get("alpha_format", "prores4444"))
    return f if f in ALPHA_FORMATS else "prores4444"


def out_name(stem, fmt):
    """File (or folder, for a PNG sequence) name for one video of a render."""
    if fmt == "png":
        return stem + "_png"
    if fmt == "mp4":
        return stem + ".mp4"
    return stem + ALPHA_FORMATS[fmt]["ext"]


def key_rgb(p):
    """The key colour as 0..1 sRGB floats (compositing happens in display space)."""
    s = str((p or {}).get("bg_key", "#00b140"))
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", s):
        s = "#00b140"
    return np.array([int(s[i:i + 2], 16) for i in (1, 3, 5)], np.float32) / 255.0


def compose_out(o, A, mode, key=None):
    """Final uint8 frame from light o (0..1 float, already over black) and coverage A (0..1).
    scene: plain RGB. alpha: straight RGBA, colour = o / A. color: o over the flat key colour."""
    o = np.clip(o, 0.0, 1.0)
    if mode == "alpha":
        A = np.clip(A, 0.0, 1.0).astype(np.float32)
        rgb = np.where(A[..., None] > 1e-4, o / np.maximum(A, 1e-4)[..., None], 0.0)
        out = np.concatenate([np.clip(rgb, 0, 1), A[..., None]], axis=2)
        return (out * 255 + 0.5).astype(np.uint8)
    if mode == "color":
        k = key if key is not None else np.array([0.0, 0.694, 0.251], np.float32)
        A = np.clip(A, 0.0, 1.0)[..., None]
        o = o + k.reshape(1, 1, 3) * (1.0 - A)
    return (np.clip(o, 0, 1) * 255 + 0.5).astype(np.uint8)


def light_alpha(o):
    """Coverage of pure light over black: the brightest channel."""
    return np.clip(np.asarray(o, np.float32), 0, 1).max(axis=2)


def blank_out(H, W, mode, key=None):
    """An empty frame: black, fully transparent or the key colour."""
    if mode == "alpha":
        return np.zeros((H, W, 4), np.uint8)
    if mode == "color":
        k = key if key is not None else np.array([0.0, 0.694, 0.251], np.float32)
        return np.broadcast_to((np.clip(k, 0, 1) * 255 + 0.5).astype(np.uint8), (H, W, 3)).copy()
    return np.zeros((H, W, 3), np.uint8)


_TAGS709 = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]


def _raw_input(W, H, fps, pix):
    return ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", pix, "-s", f"{W}x{H}",
            "-r", str(fps), "-i", "-"]


def video_cmd(out, W, H, fps, crf, preset="slow", fmt="mp4", x264=None):
    """ffmpeg command that reads raw frames from stdin: rgb24 for mp4, rgba for the alpha formats."""
    if fmt in (None, "", "mp4"):
        return ffmpeg_cmd(out, W, H, fps, crf, preset, x264)
    draft = preset in ("veryfast", "superfast", "ultrafast", "faster")
    if fmt == "prores4444":
        return (_raw_input(W, H, fps, "rgba") + ["-vf", "scale=out_color_matrix=bt709:out_range=tv",
                "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-vendor", "apl0"]
                + _TAGS709 + [out])
    if fmt == "webm":
        speed = ["-deadline", "realtime", "-cpu-used", "8"] if draft else ["-deadline", "good", "-cpu-used", "2"]
        return (_raw_input(W, H, fps, "rgba") + ["-vf", "scale=out_color_matrix=bt709:out_range=tv",
                "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", str(min(63, int(crf) + 10)),
                "-row-mt", "1", "-auto-alt-ref", "0"] + speed + _TAGS709 + [out])
    if fmt == "hap":
        if W % 4 or H % 4:
            raise ValueError("HAP potrebuje šírku a výšku deliteľnú 4.")
        return _raw_input(W, H, fps, "rgba") + ["-c:v", "hap", "-format", "hap_alpha", out]
    if fmt == "png":
        os.makedirs(out, exist_ok=True)
        stem = os.path.basename(os.path.normpath(out))
        if stem.endswith("_png"):
            stem = stem[:-4]
        return _raw_input(W, H, fps, "rgba") + ["-f", "image2", "-c:v", "png", "-pix_fmt", "rgba",
                                               os.path.join(out, stem + "_%05d.png")]
    raise ValueError(f"Neznámy formát videa: {fmt}")


def write_png(path, img):
    """PNG from an RGB or RGBA uint8 array. Works with any characters in the path (cv2.imwrite does not)."""
    a = np.asarray(img)
    if a.ndim == 3 and a.shape[2] == 4:
        a = a[..., [2, 1, 0, 3]]
    elif a.ndim == 3:
        a = a[..., ::-1]
    ok, buf = cv2.imencode(".png", np.ascontiguousarray(a))
    if not ok:
        raise RuntimeError("PNG sa nepodarilo zakódovať")
    buf.tofile(path)


def read_image(path, flags=cv2.IMREAD_UNCHANGED):
    """cv2.imread that works with any characters in the path. Returns None when it cannot be read."""
    try:
        data = np.fromfile(path, np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


# ----------------------------------------------------------------------------------------------
# Projects: one folder per band or client in projekty/, with its own recipes, assets and outputs
# ----------------------------------------------------------------------------------------------

PROJECT_FILE = "projekt.json"
ASSET_KINDS = {
    "logo": ("logo", (".png", ".webp")),
    "obal": ("obaly", (".png", ".jpg", ".jpeg", ".webp")),
}
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,59}")


def _ascii_slug(text, fallback):
    s = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s[:40].strip("_") or fallback


class Project:
    """A band (or any client): projekty/<slug>/ with projekt.json, recepty/, logo/, obaly/ and outputs."""

    def __init__(self, path):
        self.dir = os.path.normpath(os.path.abspath(path))
        self.slug = os.path.basename(self.dir)
        self.meta = {}
        try:
            with open(os.path.join(self.dir, PROJECT_FILE), "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.meta = d
        except (OSError, ValueError):
            pass

    def __repr__(self):
        return f"Project({self.slug!r})"

    @property
    def name(self):
        return str(self.meta.get("nazov") or self.slug)

    @property
    def out_root(self):
        """Where finished versions go. projekt.json "vystupy" may point elsewhere (relative to the project)."""
        return os.path.normpath(os.path.join(self.dir, str(self.meta.get("vystupy") or "vystupy")))

    @property
    def draft_dir(self):
        return os.path.join(self.out_root, "_nahlady")

    @property
    def recipe_dir(self):
        return os.path.join(self.dir, "recepty")

    @property
    def trash_dir(self):
        return os.path.join(self.recipe_dir, "_kos")

    def asset_dir(self, kind):
        return os.path.join(self.dir, ASSET_KINDS[kind][0])

    def assets(self, kind):
        d = self.asset_dir(kind)
        if not os.path.isdir(d):
            return []
        ext = ASSET_KINDS[kind][1]
        return sorted((fn for fn in os.listdir(d)
                       if fn.lower().endswith(ext) and not fn.startswith((".", "_"))
                       and os.path.isfile(os.path.join(d, fn))), key=str.lower)

    def asset_path(self, kind, name):
        """Full path of an asset file, or None. Only plain file names directly in the asset folder."""
        if kind not in ASSET_KINDS or not name:
            return None
        fn = os.path.basename(str(name))
        if fn != str(name) or fn.startswith((".", "_")) or not fn.lower().endswith(ASSET_KINDS[kind][1]):
            return None
        fp = os.path.join(self.asset_dir(kind), fn)
        return fp if os.path.isfile(fp) else None

    def info(self):
        return {"slug": self.slug, "name": self.name, "popis": str(self.meta.get("popis") or ""),
                "moduly": self.meta.get("moduly") if isinstance(self.meta.get("moduly"), list) else None,
                "assets": {k: self.assets(k) for k in ASSET_KINDS}}

    @staticmethod
    def list_all():
        out = []
        if not os.path.isdir(PROJECTS_DIR):
            return out
        for fn in os.listdir(PROJECTS_DIR):
            d = os.path.join(PROJECTS_DIR, fn)
            if SLUG_RE.fullmatch(fn) and os.path.isfile(os.path.join(d, PROJECT_FILE)):
                out.append(Project(d))
        return sorted(out, key=lambda pr: pr.name.lower())

    @staticmethod
    def get(slug):
        s = str(slug or "")
        if not SLUG_RE.fullmatch(s):
            return None
        d = os.path.join(PROJECTS_DIR, s)
        return Project(d) if os.path.isfile(os.path.join(d, PROJECT_FILE)) else None

    @staticmethod
    def create(name, popis=""):
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:80]
        if not name:
            raise ValueError("Projekt potrebuje názov.")
        base = _ascii_slug(name, "projekt")
        slug, n = base, 2
        while os.path.exists(os.path.join(PROJECTS_DIR, slug)):
            slug, n = f"{base}-{n}", n + 1
        d = os.path.join(PROJECTS_DIR, slug)
        for sub in ("recepty", "logo", "obaly", "vystupy"):
            os.makedirs(os.path.join(d, sub), exist_ok=True)
        meta = {"nazov": name, "popis": str(popis or "").strip()[:400],
                "vytvoreny": time.strftime("%Y-%m-%d %H:%M:%S")}
        with open(os.path.join(d, PROJECT_FILE), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        return Project(d)


def project_asset(ctx, kind, name):
    """Asset path from a render context (a Project or None)."""
    if ctx is None or not hasattr(ctx, "asset_path"):
        return None
    return ctx.asset_path(kind, name)


# ----------------------------------------------------------------------------------------------
# Modules: every moduly/*.py with the module interface is found on start; nothing to register
# ----------------------------------------------------------------------------------------------

REQUIRED = ("MODULE_ID", "MODULE_LABEL", "SCHEMA", "PRESETS", "LANES", "defaults", "merge", "level_key",
            "timeline", "default_name", "file_slug", "load_recipe", "recipe_doc", "Renderer")
_RESERVED = {"jadro", "app", "ai", "baseline"}
_NAME_RE = re.compile(r"[a-z][a-z0-9_]*")
MODULE_ERRORS = {}
_FOUND = None


def _load_module(name, path):
    old = sys.modules.get(name)
    if old is not None:
        f = getattr(old, "__file__", None)
        if f and os.path.normcase(os.path.abspath(f)) == os.path.normcase(os.path.abspath(path)):
            return old
        raise ImportError(f"názov {name} už používa iný modul Pythonu, premenuj súbor")
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    try:
        spec.loader.exec_module(m)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return m


def discover(reload=False):
    """{MODULE_ID: module} for every working module in moduly/, in menu order. Broken files land in
    MODULE_ERRORS with the reason, the rest keeps working."""
    global _FOUND
    if _FOUND is not None and not reload:
        return _FOUND
    MODULE_ERRORS.clear()
    found = []
    seen = {}
    names = sorted(os.listdir(MODULES_DIR)) if os.path.isdir(MODULES_DIR) else []
    for fn in names:
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        name = fn[:-3]
        try:
            if not _NAME_RE.fullmatch(name):
                raise ImportError("názov súboru smie mať len malé písmená, číslice a _")
            if name in _RESERVED or name in getattr(sys, "stdlib_module_names", ()):
                raise ImportError(f"názov {name} je vyhradený, premenuj súbor")
            m = _load_module(name, os.path.join(MODULES_DIR, fn))
            miss = [a for a in REQUIRED if not hasattr(m, a)]
            if miss:
                raise AttributeError("chýba " + ", ".join(miss))
            mid = str(m.MODULE_ID)
            if mid in seen:
                raise ValueError(f"MODULE_ID {mid} už má {seen[mid]}")
            seen[mid] = fn
            found.append(m)
        except (Exception, SystemExit) as e:  # noqa: BLE001
            MODULE_ERRORS[name] = f"{e.__class__.__name__}: {e}"
    found.sort(key=lambda m: (getattr(m, "MODULE_ORDER", 100), str(m.MODULE_LABEL).lower()))
    _FOUND = {str(m.MODULE_ID): m for m in found}
    return _FOUND


def available_for(mods, project):
    """Modules offered in a project: a module may name its projects (PROJECTS), a project may name
    its modules (projekt.json "moduly")."""
    allow = None
    if project is not None:
        lst = project.meta.get("moduly")
        if isinstance(lst, list) and lst:
            allow = {str(x) for x in lst}
    out = {}
    for mid, m in mods.items():
        only = getattr(m, "PROJECTS", None)
        if only and (project is None or project.slug not in only):
            continue
        if allow is not None and mid not in allow:
            continue
        out[mid] = m
    return out

# ----------------------------------------------------------------------------------------------
# Command line for modules whose frames do not depend on each other (no simulation):
#   python moduly/<modul>.py render recept.json priecinok [--res 1920x1080] [--draft] [--project ...]
#   python moduly/<modul>.py stills recept.json priecinok [--times 0.5,2,4]
# The app starts render with --project; frames are drawn in parallel worker processes.
# Optional module hooks: X264_EXTRA (list of x264 options), still_time(p), RENDER_MSG.
# ----------------------------------------------------------------------------------------------

_CLI_R = None


def cli_project(path=None):
    """The project for a module run from the command line: --project, else the first one."""
    if path and os.path.isfile(os.path.join(path, PROJECT_FILE)):
        return Project(path)
    if path:
        P = Project.get(path)
        if P is not None:
            return P
    lst = Project.list_all()
    return lst[0] if lst else None


def _cli_winit(mod_file, p, W, H, pdir):
    global _CLI_R
    cv2.setNumThreads(1)
    name = os.path.splitext(os.path.basename(mod_file))[0]
    M = _load_module(name, mod_file)
    _CLI_R = M.Renderer(p, W, H, ctx=Project(pdir) if pdir else None)


def _cli_wframe(fi):
    R = _CLI_R
    return np.ascontiguousarray(R.frame(fi / R.fps, fidx=fi, clean=False)).tobytes()


def _cli_render(M, a):
    import multiprocessing as mp
    import subprocess
    P = cli_project(a.project)
    rec = M.load_recipe(a.recipe)
    p = dict(rec["params"])
    if a.res:
        p["res"] = a.res
    if a.fps:
        p["fps"] = str(a.fps)
    if a.crf is not None:
        p["crf"] = a.crf
    p = M.merge(p)
    W, H = parse_res(p["res"])
    fps, crf = int(p["fps"]), int(p["crf"])
    preset = "slow"
    if a.draft:
        W, H = parse_res(a.draft_res)
        crf, preset = 22, "veryfast"
    t_start = time.time()
    emit(ev="start", msg=getattr(M, "RENDER_MSG", "Pripravujem snímky"))
    R = M.Renderer(p, W, H, ctx=P)
    N = max(1, int(round(R.T_end * fps)))
    slug = a.slug or M.file_slug(p)
    os.makedirs(a.out, exist_ok=True)
    kind = out_kind(p)
    tag = f"{W}x{H}_{fps}fps" + {"alpha": "_alfa", "color": "_kluc"}.get(kind, "")
    fmt = out_format(p, a.draft)
    fp = os.path.join(a.out, out_name(f"{slug}_nahlad_{tag}" if a.draft else f"{slug}_{tag}", fmt))
    proc = subprocess.Popen(video_cmd(fp, W, H, fps, crf, preset, fmt, getattr(M, "X264_EXTRA", None)),
                            stdin=subprocess.PIPE)
    nw = max(1, min(a.workers or (os.cpu_count() or 4) - 1, 16, N))
    warn = list((R.info() or {}).get("warnings") or [])
    emit(ev="info", frames=N, split=N, workers=nw, files=[os.path.basename(fp)], warnings=warn)
    try:
        done, tlast = 0, 0.0
        mpc = mp.get_context("spawn")
        with mpc.Pool(nw, initializer=_cli_winit,
                      initargs=(os.path.abspath(M.__file__), p, W, H, P.dir if P else None)) as pool:
            for buf in pool.imap(_cli_wframe, range(N), chunksize=2):
                proc.stdin.write(buf)
                done += 1
                now = time.time()
                if now - tlast > 0.5 or done == N:
                    rate = done / max(now - t_start, 1e-6)
                    emit(ev="progress", done=done, total=N, fps=round(rate, 2),
                         eta=round((N - done) / max(rate, 1e-6), 1))
                    tlast = now
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg skončil s chybou")
        out = [fp]
        if not a.draft:
            st = getattr(M, "still_time", None)
            ts = float(st(p)) if callable(st) else R.T_end * 0.5
            still = os.path.join(a.out, f"{slug}_ustaleny_{W}x{H}{'_alfa' if kind == 'alpha' else ''}.png")
            write_png(still, R.frame(ts, clean=True))
            rp = os.path.join(a.out, f"{slug}_recept.json")
            with open(rp, "w", encoding="utf-8") as f:
                json.dump(M.recipe_doc(rec.get("name") or M.default_name(p), p), f, ensure_ascii=False, indent=2)
            out += [still, rp]
        emit(ev="done", files=[os.path.basename(x) for x in out], sec=round(time.time() - t_start, 1))
    except BaseException as e:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        emit(ev="error", msg=str(e) or e.__class__.__name__)
        raise


def _cli_stills(M, a):
    P = cli_project(a.project)
    p = M.load_recipe(a.recipe)["params"]
    W, H = parse_res(a.res or p["res"])
    R = M.Renderer(p, W, H, ctx=P)
    os.makedirs(a.out, exist_ok=True)
    for s in a.times.split(","):
        if not s.strip():
            continue
        t = min(max(float(s), 0.0), R.T_end)
        t0 = time.time()
        name = os.path.join(a.out, f"{a.prefix}t{t:05.2f}.png")
        write_png(name, R.frame(t, clean=a.clean))
        print(f"{name}  {(time.time() - t0) * 1000:.0f} ms", flush=True)


def module_cli(M, desc=None):
    """Entry point for `python moduly/<modul>.py ...` (call it under if __name__ == "__main__")."""
    import argparse
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=desc or f"{M.MODULE_LABEL}, modul aplikácie {APP_NAME}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stills")
    s.add_argument("recipe")
    s.add_argument("out")
    s.add_argument("--times", default="0,1,2,3,4")
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
        _cli_stills(M, a)
    else:
        _cli_render(M, a)
