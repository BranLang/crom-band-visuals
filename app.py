#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CROM: a local app for band visuals (server + web interface).

  python app.py [--port 8765] [--open]

The interface lives in web/ (English and Slovak), the pictures are drawn by the modules in modules/
(one file per effect, found automatically). Every band has its own project in projects/<name>/ with
recipes, logos, covers and outputs. The server listens on 127.0.0.1 only, so only this computer sees it.
"""
import argparse
import collections
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402

import core  # noqa: E402
from core import Project  # noqa: E402
import ai  # noqa: E402

APP_VERSION = "2.0"
WEB_DIR = os.path.join(HERE, "web")
FONT_DIR = core.FONT_DIR
MAX_BODY = 40 * 1024 * 1024
DRAFTS_KEPT = 6
RESERVED = {"app", "_previews", "stills"}
OUT_EXT = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".png": "image/png",
           ".jpg": "image/jpeg", ".json": "application/json"}
VIDEO_EXT = (".mp4", ".mov", ".webm")
ASSET_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                ".ico": "image/x-icon", ".woff2": "font/woff2", ".json": "application/json"}
NEVER_SERVE = {"_settings.json", "_nastavenia.json"}
WIN = os.name == "nt"
DEFAULT_PROJECT = "demo"
PART_RE = re.compile(r"_(\d)_[a-z]+_")


def now_s():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def inside(root, path):
    """True when path is root itself or lies under it (after resolving .. and links)."""
    try:
        r = os.path.realpath(root)
        p = os.path.realpath(path)
        return os.path.commonpath([r, p]) == r
    except ValueError:
        return False


def same_dir(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


class ApiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status
        self.msg = msg


# ----------------------------------------------------------------------------------------------
# Projects and modules
# ----------------------------------------------------------------------------------------------

def all_modules():
    return core.discover()


def project(slug=None):
    """The project a request talks to; without a slug the default one."""
    if slug:
        P = Project.get(slug)
        if P is None:
            raise ApiError(404, f"Project {slug} does not exist")
        return P
    lst = Project.list_all()
    for P in lst:
        if P.slug == DEFAULT_PROJECT:
            return P
    if lst:
        return lst[0]
    return Project.create("My band", "The first project, rename it in project.json.")


def modules_for(P):
    return core.available_for(all_modules(), P)


def mod(name, P):
    mods = modules_for(P)
    if not mods:
        raise ValueError("No module is available in this project")
    if not name:
        return next(iter(mods.values()))
    M = mods.get(core.module_id(name))
    if M is None:
        raise ValueError(f"The module {name} is not in the project {P.name}")
    return M


def module_info(M):
    return {"id": M.MODULE_ID, "label": M.MODULE_LABEL, "desc": getattr(M, "MODULE_DESC", ""),
            "batch": getattr(M, "BATCH", None)}


def safe_slug(text):
    s = core.slugify(text) or "untitled"
    return "output_" + s if s in RESERVED else s


def preview_size(M, params, h):
    """Preview size for a requested height, keeping the output's shape."""
    ow, oh = core.parse_res(M.merge(params)["res"])
    h = int(max(72, min(int(h), 2160)))
    w = int(round(h * ow / oh / 2.0)) * 2
    return max(2, w), h


def draft_res(res):
    """Quick preview size: the long side 960 px, the output's shape kept."""
    w, h = core.parse_res(res)
    s = 960.0 / max(w, h)
    return f"{int(round(w * s / 2)) * 2}x{int(round(h * s / 2)) * 2}"


def checker(h, w, cell=12):
    yy, xx = np.mgrid[0:h, 0:w]
    c = (((yy // cell) + (xx // cell)) % 2).astype(np.float32)
    v = (0.16 + 0.10 * c)[..., None]
    return np.repeat(v, 3, axis=2)


def flatten(frame):
    """RGB uint8 for JPEG: RGBA frames land on a grey checkerboard, so transparency stays visible."""
    if frame.ndim == 3 and frame.shape[2] == 4:
        a = frame[..., 3:4].astype(np.float32) / 255.0
        bg = checker(frame.shape[0], frame.shape[1]) * 255.0
        rgb = frame[..., :3].astype(np.float32) * a + bg * (1.0 - a)
        return np.clip(rgb + 0.5, 0, 255).astype(np.uint8)
    return frame


def jpeg(frame, q=92):
    ok, buf = cv2.imencode(".jpg", np.ascontiguousarray(flatten(frame)[..., ::-1]),
                           [int(cv2.IMWRITE_JPEG_QUALITY), int(q)])
    if not ok:
        raise RuntimeError("Could not encode the JPEG")
    return buf.tobytes()


def png(frame):
    if frame.ndim == 3 and frame.shape[2] == 4:
        bgr = cv2.cvtColor(np.ascontiguousarray(frame), cv2.COLOR_RGBA2BGRA)
    else:
        bgr = np.ascontiguousarray(frame[..., ::-1])
    ok, buf = cv2.imencode(".png", bgr, [int(cv2.IMWRITE_PNG_COMPRESSION), 1])
    if not ok:
        raise RuntimeError("Could not encode the PNG")
    return buf.tobytes()


def make_renderer(M, params, W, H, P):
    return M.Renderer(params, W, H, ctx=P)


# ----------------------------------------------------------------------------------------------
# Live previews: a few renderers kept warm, one per (project, module, size). They are not
# thread-safe, so one lock serialises every frame; simulations warm up in their own threads.
# ----------------------------------------------------------------------------------------------

class Previews:
    def __init__(self, keep=4):
        self.lock = threading.Lock()
        self.items = {}
        self.keep = keep

    def _renderer(self, M, params, W, H, P):
        key = (P.slug, M.MODULE_ID, W, H)
        it = self.items.get(key)
        if it is None:
            if len(self.items) >= self.keep:
                old = min(self.items, key=lambda k: self.items[k][1])
                del self.items[old]
            it = [make_renderer(M, params, W, H, P), 0.0]
            self.items[key] = it
        else:
            it[0].update(params)
        it[1] = time.monotonic()
        return it[0]

    def forget(self, P):
        with self.lock:
            for k in [k for k in self.items if k[0] == P.slug]:
                del self.items[k]

    def frame(self, M, params, t, W, H, clean=False, quick=False, P=None):
        with self.lock:
            a = time.perf_counter()
            R = self._renderer(M, params, W, H, P)
            b = time.perf_counter()
            fr = R.frame(float(t), clean=clean, quick=quick)
            c = time.perf_counter()
            info = dict(R.info())
            info.pop("timeline", None)
            info.update(build_ms=round((b - a) * 1000, 1), frame_ms=round((c - b) * 1000, 1),
                        alpha=bool(fr.ndim == 3 and fr.shape[2] == 4))
            return fr, info

    def strip(self, M, params, n, W, H, tw, th, P=None):
        """n evenly spaced thumbnails over the whole timeline, side by side in one image.
        Simulated modules may hand back the nearest simulated state (approx) while they warm up."""
        with self.lock:
            R = self._renderer(M, params, W, H, P)
            total = R.T_end
            tiles = []
            approx = False
            for i in range(n):
                t = (i + 0.5) / n * total
                fr = flatten(R.frame(t, clean=False, quick=True))
                approx = approx or bool(getattr(R, "last_approx", False))
                tiles.append(cv2.resize(fr, (tw, th), interpolation=cv2.INTER_AREA))
            return np.hstack(tiles), total, approx

    def still(self, M, params, t, P=None):
        """One exact frame at the output resolution. Not cached: full size is rarely asked for twice."""
        with self.lock:
            p = M.merge(params)
            W, H = core.parse_res(p["res"])
            R = make_renderer(M, p, W, H, P)
            return R.frame(float(t), clean=False), p, W, H


# ----------------------------------------------------------------------------------------------
# Render queue: one module subprocess at a time
# ----------------------------------------------------------------------------------------------

def kill_tree(pid):
    if WIN:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(os.getpgid(pid), 9)
        except (ProcessLookupError, PermissionError, OSError):
            pass


class Jobs:
    PUBLIC = ("id", "kind", "module", "project", "name", "title", "slug", "version", "state", "done", "total",
              "split", "fps_out", "rate", "eta", "msg", "files", "rel_dir", "created", "started", "ended", "sec",
              "hash", "res", "particles", "warnings", "batch", "format")

    def __init__(self):
        self.lock = threading.Lock()
        self.cv = threading.Condition(self.lock)
        self.items = []
        self.seq = 0
        self.current = None
        self.proc = None
        self.closing = False
        self.thread = threading.Thread(target=self._loop, name="render-queue", daemon=True)
        self.thread.start()

    # ---------- public ----------
    def add(self, kind, M, params, name, hash_=None, batch=None, P=None):
        P = P or project()
        p = M.merge(params)
        slug = safe_slug(M.file_slug(p))
        tl = M.timeline(p)
        fps = int(p["fps"])
        with self.cv:
            self.seq += 1
            jid = str(self.seq)
            job = {"id": jid, "kind": kind, "module": M.MODULE_ID, "project": P.slug, "project_dir": P.dir,
                   "out_root": P.out_root, "name": name or M.default_name(p),
                   "title": M.default_name(p), "slug": slug, "params": p,
                   "state": "queued", "done": 0, "total": int(round(tl["total"] * fps)),
                   "split": int(round(tl.get("t1", tl["total"]) * fps)), "fps_out": fps,
                   "rate": 0.0, "eta": None, "msg": "Waiting in the queue", "files": [], "created": now_s(),
                   "started": None, "ended": None, "sec": None, "hash": hash_, "batch": batch,
                   "particles": None, "warnings": [], "cancel": False,
                   "format": core.out_format(p, kind == "draft") if "bg_mode" in p else "mp4"}
            if kind == "draft":
                # a new quick preview replaces any older one that has not finished yet
                for j in self.items:
                    if j["kind"] == "draft" and j["state"] in ("queued", "running"):
                        self._cancel_locked(j)
                stamp = time.strftime("%m%d_%H%M%S") + f"_{jid}"
                job.update(version=None, out_dir=P.draft_dir, file_slug=f"{slug}_{stamp}", res=draft_res(p["res"]))
            else:
                v = self._next_version(P, slug)
                vs = f"v{v:02d}"
                job.update(version=vs, out_dir=os.path.join(P.out_root, slug, vs),
                           file_slug=f"{slug}_{vs}", res=p["res"])
            job["rel_dir"] = os.path.relpath(job["out_dir"], P.out_root).replace("\\", "/")
            self.items.append(job)
            # keep the list short: finished jobs beyond the newest 40 drop off
            fin = [j for j in self.items if j["state"] in ("done", "error", "cancelled")]
            for j in fin[:-40]:
                self.items.remove(j)
            self.cv.notify_all()
            return self._public(job)

    def cancel(self, jid):
        with self.cv:
            for j in self.items:
                if j["id"] == jid:
                    self._cancel_locked(j)
                    return self._public(j)
        return None

    def clear_finished(self):
        with self.cv:
            self.items = [j for j in self.items if j["state"] in ("queued", "running")]

    def list(self):
        with self.lock:
            return [self._public(j) for j in reversed(self.items)]

    def shutdown(self):
        with self.cv:
            self.closing = True
            for j in self.items:
                if j["state"] in ("queued", "running"):
                    self._cancel_locked(j)
            self.cv.notify_all()

    # ---------- internals ----------
    def _public(self, j):
        return {k: j.get(k) for k in self.PUBLIC}

    def _next_version(self, P, slug):
        used = set()
        d = os.path.join(P.out_root, slug)
        if os.path.isdir(d):
            for fn in os.listdir(d):
                m = re.fullmatch(r"v(\d+)", fn)
                if m and os.path.isdir(os.path.join(d, fn)):
                    used.add(int(m.group(1)))
        for j in self.items:
            if (j["kind"] == "final" and j["slug"] == slug and same_dir(j["out_root"], P.out_root)
                    and j.get("version") and j["state"] in ("queued", "running")):
                used.add(int(j["version"][1:]))
        return max(used) + 1 if used else 1

    def _cancel_locked(self, j):
        if j["state"] == "queued":
            j["state"] = "cancelled"
            j["msg"] = "Cancelled"
            j["ended"] = now_s()
        elif j["state"] == "running":
            j["cancel"] = True
            j["msg"] = "Cancelling…"
            if self.current is j and self.proc is not None:
                kill_tree(self.proc.pid)

    def _loop(self):
        while True:
            with self.cv:
                while not self.closing and not any(j["state"] == "queued" for j in self.items):
                    self.cv.wait()
                if self.closing:
                    return
                job = next(j for j in self.items if j["state"] == "queued")
                job["state"] = "running"
                job["started"] = now_s()
                job["msg"] = "Starting"
                self.current = job
            try:
                self._run(job)
            except Exception as e:  # never let the queue thread die
                traceback.print_exc()
                with self.lock:
                    job["state"] = "error"
                    job["msg"] = f"Server error: {e}"
            finally:
                with self.lock:
                    self.current = None
                    self.proc = None
                    if not job.get("ended"):
                        job["ended"] = now_s()

    def _run(self, job):
        out_dir = job["out_dir"]
        os.makedirs(out_dir, exist_ok=True)
        M = all_modules().get(job["module"])
        if M is None:
            raise RuntimeError(f"The module {job['module']} is gone")
        rec = os.path.join(out_dir, f".{job['file_slug']}_input.json")
        with open(rec, "w", encoding="utf-8") as f:
            json.dump(M.recipe_doc(job["name"], job["params"]), f, ensure_ascii=False, indent=2)
        cmd = [sys.executable, os.path.abspath(M.__file__), "render", rec, out_dir, "--slug", job["file_slug"],
               "--project", job["project_dir"]]
        if job["kind"] == "draft":
            cmd += ["--draft", "--draft-res", job["res"]]
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        kw = {}
        if not WIN:
            kw["start_new_session"] = True
        t0 = time.time()
        err_tail = collections.deque(maxlen=30)
        proc = subprocess.Popen(cmd, cwd=HERE, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                stdin=subprocess.DEVNULL, **kw)
        with self.lock:
            self.proc = proc
            if job["cancel"]:
                kill_tree(proc.pid)

        def read_err():
            for raw in proc.stderr:
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    err_tail.append(line)

        te = threading.Thread(target=read_err, daemon=True)
        te.start()
        got_done = False
        last_err = None
        planned = []
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            with self.lock:
                kind = ev.get("ev")
                if kind == "start":
                    job["msg"] = ev.get("msg", "")
                elif kind == "info":
                    job["total"] = ev.get("frames", job["total"])
                    job["split"] = ev.get("split", job["split"])
                    job["particles"] = ev.get("particles")
                    job["warnings"] = ev.get("warnings") or []
                    planned = list(ev.get("files") or [])
                    job["msg"] = f"Rendering on {ev.get('workers')} cores"
                elif kind == "progress":
                    job["done"] = ev.get("done", 0)
                    job["total"] = ev.get("total", job["total"])
                    job["rate"] = ev.get("fps", 0.0)
                    job["eta"] = ev.get("eta")
                elif kind == "done":
                    got_done = True
                    job["files"] = [f"{job['rel_dir']}/{fn}" for fn in ev.get("files") or []]
                    job["sec"] = ev.get("sec")
                elif kind == "error":
                    last_err = ev.get("msg")
        rc = proc.wait()
        te.join(timeout=2)
        try:
            os.remove(rec)
        except OSError:
            pass
        with self.lock:
            cancelled = job["cancel"]
            job["ended"] = now_s()
            if job["sec"] is None:
                job["sec"] = round(time.time() - t0, 1)
            if rc == 0 and got_done and not cancelled:
                job["state"] = "done"
                job["done"] = job["total"]
                job["eta"] = 0
                job["msg"] = "Done" if job["kind"] == "final" else "The preview is ready"
            else:
                job["state"] = "cancelled" if cancelled else "error"
                if cancelled:
                    job["msg"] = "Cancelled"
                else:
                    tail = [ln for ln in err_tail if ln.strip()]
                    job["msg"] = last_err or (tail[-1] if tail else f"The render exited with code {rc}")
        if job["state"] != "done":
            self._cleanup(job, planned)
        elif job["kind"] == "draft":
            self._rotate_drafts(out_dir)

    def _cleanup(self, job, planned):
        d = job["out_dir"]
        for fn in planned:
            fp = os.path.join(d, os.path.basename(fn))
            try:
                if os.path.isdir(fp):
                    shutil.rmtree(fp)       # a PNG sequence
                else:
                    os.remove(fp)
            except OSError:
                pass
        try:
            for fn in os.listdir(d):
                if fn.startswith(f".{job['file_slug']}_") and fn.endswith("_state.pkl"):
                    os.remove(os.path.join(d, fn))
        except OSError:
            pass
        if job["kind"] == "final":
            try:
                if not os.listdir(d):
                    os.rmdir(d)
                parent = os.path.dirname(d)
                if not os.listdir(parent):
                    os.rmdir(parent)
            except OSError:
                pass

    def _rotate_drafts(self, draft_dir):
        try:
            files = [os.path.join(draft_dir, f) for f in os.listdir(draft_dir)
                     if f.lower().endswith(VIDEO_EXT) and "_preview_" in f]
        except OSError:
            return
        files.sort(key=os.path.getmtime, reverse=True)
        for fp in files[DRAFTS_KEPT:]:
            try:
                os.remove(fp)
            except OSError:
                pass


# ----------------------------------------------------------------------------------------------
# Recipes, fonts, assets, outputs
# ----------------------------------------------------------------------------------------------

def recipe_files(P):
    out = []
    rd = P.recipe_dir
    if not os.path.isdir(rd):
        return out
    mods = all_modules()
    for fn in os.listdir(rd):
        fp = os.path.join(rd, fn)
        if not fn.lower().endswith(".json") or not os.path.isfile(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8") as f:
                d = json.load(f)
            p = d.get("params", {}) if isinstance(d, dict) else {}
            M = mods.get(core.module_id(d.get("module")))
            if M is None:
                out.append({"file": fn, "name": d.get("name") or fn[:-5], "saved": d.get("saved"),
                            "module": d.get("module"), "title": "", "broken": True, "mtime": os.path.getmtime(fp)})
                continue
            out.append({"file": fn, "name": d.get("name") or fn[:-5], "saved": d.get("saved"),
                        "module": M.MODULE_ID, "title": M.default_name(M.merge(p)), "metal": p.get("metal"),
                        "mtime": os.path.getmtime(fp)})
        except Exception:
            out.append({"file": fn, "name": fn[:-5], "saved": None, "module": None, "title": "", "broken": True,
                        "mtime": os.path.getmtime(fp)})
    out.sort(key=lambda r: r["mtime"], reverse=True)
    return out


def recipe_path(P, fn):
    fn = os.path.basename(str(fn or ""))
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+\.json", fn):
        raise ValueError("Invalid recipe name")
    return os.path.join(P.recipe_dir, fn)


def load_any_recipe(fp):
    """A recipe of any module: the file says which, older files without the field are titles."""
    with open(fp, "r", encoding="utf-8") as f:
        d = json.load(f)
    mid = core.module_id(d.get("module") if isinstance(d, dict) else None)
    M = all_modules().get(mid)
    if M is None:
        raise ValueError(f"The recipe belongs to the module {mid}, which is not here")
    return M.load_recipe(fp)


def save_recipe(P, body):
    name = str(body.get("name") or "").strip() or "Recipe"
    M = mod(body.get("module"), P)
    params = M.merge(body.get("params") or {})
    fn = body.get("file")
    rd = P.recipe_dir
    if fn and body.get("overwrite"):
        fp = recipe_path(P, fn)
    else:
        base = core.slugify(name) or "recipe"
        fp = os.path.join(rd, base + ".json")
        n = 2
        while os.path.exists(fp):
            fp = os.path.join(rd, f"{base}_{n}.json")
            n += 1
    os.makedirs(rd, exist_ok=True)
    doc = M.recipe_doc(name, params)
    tmp = fp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp, fp)
    return {"file": os.path.basename(fp), "name": name, "module": M.MODULE_ID, "saved": doc["saved"]}


def trash_recipe(P, fn):
    fp = recipe_path(P, fn)
    if not os.path.isfile(fp):
        raise FileNotFoundError("The recipe does not exist")
    os.makedirs(P.trash_dir, exist_ok=True)
    dst = os.path.join(P.trash_dir, os.path.basename(fp))
    if os.path.exists(dst):
        dst = os.path.join(P.trash_dir, time.strftime("%Y%m%d_%H%M%S_") + os.path.basename(fp))
    shutil.move(fp, dst)
    return {"trashed": os.path.basename(dst)}


def add_font(name, data):
    name = os.path.basename(urllib.parse.unquote(name or "")).strip()
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    if ext not in (".ttf", ".otf"):
        raise ValueError("A font must be .ttf or .otf")
    if not data or len(data) > 30 * 1024 * 1024:
        raise ValueError("The file is empty or too large")
    try:
        tt = TTFont(io.BytesIO(data), lazy=True)
        cmap = tt.getBestCmap() or {}
        ok = ("glyf" in tt or "CFF " in tt or "CFF2" in tt) and ord("A") in cmap
        tt.close()
    except Exception:
        ok = False
    if not ok:
        raise ValueError("This is not a readable font with Latin letters")
    stem = re.sub(r"[^A-Za-z0-9_\-]+", "_", stem).strip("_") or "font"
    fp = os.path.join(FONT_DIR, stem + ext)
    n = 2
    while os.path.exists(fp):
        with open(fp, "rb") as f:
            if f.read() == data:
                return os.path.basename(fp)
        fp = os.path.join(FONT_DIR, f"{stem}_{n}{ext}")
        n += 1
    with open(fp, "wb") as f:
        f.write(data)
    try:
        core.get_font(os.path.basename(fp))
    except Exception as e:
        os.remove(fp)
        raise ValueError(f"The font cannot be used: {e}")
    return os.path.basename(fp)


def add_asset(P, kind, name, data):
    """A logo or a cover saved byte for byte into the project (never resized or re-encoded)."""
    kind = "cover" if kind == "obal" else kind
    if kind not in core.ASSET_KINDS:
        raise ValueError("Unknown kind of file")
    name = os.path.basename(urllib.parse.unquote(name or "")).strip()
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    allowed = core.ASSET_KINDS[kind][1]
    if ext not in allowed:
        raise ValueError("Allowed: " + ", ".join(allowed))
    if not data or len(data) > MAX_BODY:
        raise ValueError("The file is empty or too large")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None or img.ndim < 2 or min(img.shape[:2]) < 16:
        raise ValueError("The image cannot be read")
    h, w = img.shape[:2]
    if max(w, h) > 8192:
        raise ValueError("The image is larger than 8192 px")
    has_alpha = img.ndim == 3 and img.shape[2] == 4 and int(img[..., 3].min()) < 255
    stem = re.sub(r"[^A-Za-z0-9_\-]+", "_", core.slugify(stem) or stem).strip("_") or kind
    d = P.asset_dir(kind)
    os.makedirs(d, exist_ok=True)
    fp = os.path.join(d, stem + ext)
    n = 2
    while os.path.exists(fp):
        with open(fp, "rb") as f:
            if f.read() == data:
                break
        fp = os.path.join(d, f"{stem}_{n}{ext}")
        n += 1
    else:
        with open(fp, "wb") as f:
            f.write(data)
    warn = []
    if kind == "logo" and not has_alpha:
        warn.append("The logo has no transparent background, the effects will treat it as a full rectangle.")
    return {"file": os.path.basename(fp), "kind": kind, "size": [w, h], "alpha": has_alpha, "warnings": warn}


def _part(fn):
    m = PART_RE.search(fn)
    return int(m.group(1)) if m else None


def _version_files(vd, base_rel):
    files = []
    module = None
    for fn in sorted(os.listdir(vd)):
        fp = os.path.join(vd, fn)
        if fn.startswith("."):
            continue
        if os.path.isdir(fp):
            if fn.endswith("_png"):
                frames = sorted(f for f in os.listdir(fp) if f.lower().endswith(".png"))
                if frames:
                    files.append({"name": fn, "rel": f"{base_rel}/{fn}", "kind": "sequence", "frames": len(frames),
                                  "first": f"{base_rel}/{fn}/{frames[0]}",
                                  "size": sum(os.path.getsize(os.path.join(fp, f)) for f in frames),
                                  "part": _part(fn)})
            continue
        ext = os.path.splitext(fn)[1].lower()
        if ext not in OUT_EXT or fn in NEVER_SERVE:
            continue
        kind = "video" if ext in VIDEO_EXT else "image" if ext in (".png", ".jpg") else "recipe"
        if kind == "recipe":
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    module = core.module_id(json.load(f).get("module"))
            except Exception:
                pass
        files.append({"name": fn, "rel": f"{base_rel}/{fn}", "size": os.path.getsize(fp), "kind": kind,
                      "part": _part(fn)})
    return files, module


def list_outputs(P):
    res = []
    root = P.out_root
    if not os.path.isdir(root):
        return res
    for slug in os.listdir(root):
        d = os.path.join(root, slug)
        if (slug in RESERVED or slug.startswith((".", "_")) or not os.path.isdir(d)
                or inside(d, HERE) or (inside(HERE, d) and not inside(HERE, root)) or same_dir(d, P.dir)):
            continue
        versions = []
        for v in os.listdir(d):
            vd = os.path.join(d, v)
            if not re.fullmatch(r"v\d+", v) or not os.path.isdir(vd):
                continue
            files, module = _version_files(vd, f"{slug}/{v}")
            if not files:
                continue
            mt = []
            for f in files:
                try:
                    mt.append(os.path.getmtime(os.path.join(vd, f["name"])))
                except OSError:
                    pass
            versions.append({"v": v, "rel": f"{slug}/{v}", "module": module or "titles",
                             "mtime": max(mt) if mt else 0, "files": files})
        if versions:
            versions.sort(key=lambda r: int(r["v"][1:]), reverse=True)
            res.append({"slug": slug, "versions": versions, "mtime": max(v["mtime"] for v in versions)})
    res.sort(key=lambda r: r["mtime"], reverse=True)
    return res


def save_still(M, P, params, t):
    fr, p, W, H = PREVIEWS.still(M, params, t, P)
    slug = safe_slug(M.file_slug(p))
    d = os.path.join(P.out_root, slug, "stills")
    os.makedirs(d, exist_ok=True)
    fp = os.path.join(d, f"{slug}_t{float(t):05.2f}_{W}x{H}.png")
    core.write_png(fp, fr)
    return os.path.relpath(fp, P.out_root).replace("\\", "/")


def output_file(P, rel):
    """Path of a served output file, or ApiError 404. Only known extensions under the project's
    output root, never inside the app folder, no dot segments, never the settings file."""
    parts = [s for s in str(rel).replace("\\", "/").split("/") if s]
    if not parts or any(s.startswith(".") for s in parts) or parts[-1] in NEVER_SERVE:
        raise ApiError(404, "Not found")
    root = P.out_root
    fp = os.path.join(root, *parts)
    ext = os.path.splitext(fp)[1].lower()
    blocked = inside(HERE, fp) and not inside(HERE, root)   # app code, unless the project keeps its outputs inside
    if ext not in OUT_EXT or not inside(root, fp) or blocked or not os.path.isfile(fp):
        raise ApiError(404, "Not found")
    return fp, OUT_EXT[ext]


def open_folder(P, rel):
    rel = str(rel or "").replace("\\", "/").strip("/")
    root = P.out_root
    if rel == "@fonts":
        target = FONT_DIR
    elif rel == "@recipes":
        target = P.recipe_dir
    elif rel == "@project":
        target = P.dir
    elif rel.startswith("@assets:"):
        kind = rel.split(":", 1)[1]
        kind = "cover" if kind == "obal" else kind
        if kind not in core.ASSET_KINDS:
            raise ValueError("Unknown kind of file")
        target = P.asset_dir(kind)
        os.makedirs(target, exist_ok=True)
    else:
        target = os.path.join(root, *rel.split("/")) if rel else root
    if not (inside(root, target) or inside(HERE, target)):
        raise ValueError("Outside the project folders")
    if os.path.isfile(target):
        target = os.path.dirname(target)
    if not os.path.isdir(target):
        raise FileNotFoundError("The folder does not exist")
    if WIN:
        os.startfile(target)  # noqa: S606 - opens Explorer on the user's own machine
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])
    return target


def sim_progress(M):
    f = getattr(M, "sim_progress", None)
    if callable(f):
        return f()
    sim = getattr(M, "SIM", None)
    return sim.progress() if sim is not None else None


def schema_doc(P, M):
    mods = modules_for(P)
    return {"module": M.MODULE_ID, "label": M.MODULE_LABEL, "desc": getattr(M, "MODULE_DESC", ""),
            "batch": getattr(M, "BATCH", None), "phases": getattr(M, "PHASES", None),
            "modules": [module_info(m) for m in mods.values()],
            "module_errors": dict(core.MODULE_ERRORS),
            "project": P.info(), "projects": [pr.info() for pr in Project.list_all()],
            "schema": M.SCHEMA, "presets": M.PRESETS, "lanes": M.LANES, "defaults": M.defaults(),
            "fonts": core.list_fonts(), "engine": getattr(M, "ENGINE_VERSION", 1),
            "app": APP_VERSION, "app_name": core.APP_NAME, "tagline": core.APP_TAGLINE,
            "core": core.CORE_VERSION, "out_root": P.out_root, "cpus": os.cpu_count(), "started": STARTED}


# ----------------------------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------------------------

PREVIEWS = Previews()
JOBS = Jobs()
STARTED = now_s()
# the Claude assistant renders through the same preview cache and job queue as the UI
ai.HOST = ai.AppHost(PREVIEWS, JOBS, preview_size, project, modules_for)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "CROM/" + APP_VERSION
    port = 8765

    def log_message(self, fmt, *args):  # quiet: the UI shows what matters
        pass

    # ---------- plumbing ----------
    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}")

    def _send(self, status, body=b"", ctype="application/json", headers=None):
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _body(self):
        # Read the body once and cache it: every POST reads it up front in _dispatch, so a route
        # that ignores the body never leaves it in the keep-alive socket for the next request.
        raw = getattr(self, "_raw", None)
        if raw is not None:
            return raw
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            self.close_connection = True  # the unread body would poison the next request
            raise ApiError(413, "Too large")
        self._raw = self.rfile.read(n) if n else b""
        return self._raw

    def _jbody(self):
        if getattr(self, "_jcache", None) is not None:
            return self._jcache
        raw = self._body()
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            if not raw.lstrip().startswith(b"{"):
                self._jcache = {}
                return self._jcache
        try:
            self._jcache = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            raise ApiError(400, "Bad JSON")
        if not isinstance(self._jcache, dict):
            raise ApiError(400, "Bad JSON")
        return self._jcache

    def _project(self, q):
        slug = (q.get("project") or [""])[0] or self.headers.get("X-Project") or ""
        if not slug and self.command == "POST":
            slug = self._jbody().get("project") or ""
        return project(slug or None)

    def _file(self, fp, ctype, cache=False):
        size = os.path.getsize(fp)
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        status = 200
        if rng:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
            if m:
                a, b = m.group(1), m.group(2)
                if a == "" and b:
                    start, end = max(0, size - int(b)), size - 1
                else:
                    start = int(a or 0)
                    end = min(int(b), size - 1) if b else size - 1
                if start >= size or start > end:
                    self._send(416, b"", "text/plain", {"Content-Range": f"bytes */{size}"})
                    return
                status = 206
        length = end - start + 1
        try:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "max-age=3600" if cache else "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Last-Modified", self.date_time_string(int(os.path.getmtime(fp))))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(fp, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def _guard(self, post):
        if not self._host_ok():
            raise ApiError(403, "Host not allowed")
        if post and self.headers.get("X-Crom") != "1":
            raise ApiError(403, "The app header is missing")

    def _dispatch(self, post):
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        q = urllib.parse.parse_qs(url.query)
        self._raw = None
        self._jcache = None
        try:
            if post:
                self._body()
            self._guard(post)
            if post:
                self._post(path, q)
            else:
                self._get(path, q)
        except ApiError as e:
            self._json({"error": e.msg}, e.status)
        except (FileNotFoundError, KeyError) as e:
            self._json({"error": str(e) or "Not found"}, 404)
        except ValueError as e:
            self._json({"error": str(e)}, 400)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as e:
            traceback.print_exc()
            self._json({"error": f"{e.__class__.__name__}: {e}"}, 500)

    def do_GET(self):
        self._dispatch(False)

    def do_HEAD(self):
        self._dispatch(False)

    def do_POST(self):
        self._dispatch(True)

    # ---------- GET ----------
    def _get(self, path, q):
        if path == "/api/schema":
            P = self._project(q)
            M = mod((q.get("module") or [""])[0], P)
            self._json(schema_doc(P, M))
        elif path == "/api/projects":
            self._json({"projects": [pr.info() for pr in Project.list_all()], "default": project().slug})
        elif path == "/api/fonts":
            self._json({"fonts": core.list_fonts()})
        elif path == "/api/jobs":
            self._json({"jobs": JOBS.list()})
        elif path == "/api/sim":
            P = self._project(q)
            M = mod((q.get("module") or [""])[0], P)
            self._json({"sim": sim_progress(M)})
        elif path == "/api/recipes":
            self._json({"recipes": recipe_files(self._project(q))})
        elif path == "/api/recipe":
            P = self._project(q)
            fp = recipe_path(P, (q.get("file") or [""])[0])
            rec = load_any_recipe(fp)
            self._json({"file": os.path.basename(fp), "name": rec["name"], "module": rec["module"],
                        "params": rec["params"]})
        elif path == "/api/outputs":
            self._json({"outputs": list_outputs(self._project(q))})
        elif path == "/api/ping":
            self._json({"ok": True, "started": STARTED, "app": APP_VERSION})
        elif path.startswith("/files/"):
            # /files/<project>/<rel inside the project's output root>
            slug, _, rel = path[len("/files/"):].partition("/")
            P = Project.get(slug)
            if P is None:
                raise ApiError(404, "Not found")
            fp, ctype = output_file(P, rel)
            self._file(fp, ctype)
        elif path.startswith("/asset/"):
            parts = path[len("/asset/"):].split("/")
            if len(parts) != 3:
                raise ApiError(404, "Not found")
            P = Project.get(parts[0])
            fp = P.asset_path(parts[1], parts[2]) if P else None
            if not fp or not os.path.isfile(fp):
                raise ApiError(404, "Not found")
            self._file(fp, ASSET_TYPES.get(os.path.splitext(fp)[1].lower(), "application/octet-stream"))
        elif path.startswith("/fonts/"):
            fn = os.path.basename(path)
            fp = os.path.join(FONT_DIR, fn)
            ext = os.path.splitext(fn)[1].lower()
            if ext not in (".ttf", ".otf") or not os.path.isfile(fp):
                raise ApiError(404, "Not found")
            self._file(fp, "font/ttf" if ext == ".ttf" else "font/otf", cache=True)
        elif path == "/api/ai/status":
            self._json(ai.status())
        elif path == "/api/ai/job":
            self._json(ai.job((q.get("id") or [""])[0], int((q.get("since") or ["0"])[0] or 0)))
        else:
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            fp = os.path.join(WEB_DIR, *[s for s in rel.split("/") if s])
            ext = os.path.splitext(fp)[1].lower()
            if not inside(WEB_DIR, fp) or not os.path.isfile(fp) or ext not in STATIC_TYPES:
                raise ApiError(404, "Not found")
            self._file(fp, STATIC_TYPES[ext])

    # ---------- POST ----------
    def _post(self, path, q):
        if path == "/api/frame":
            b = self._jbody()
            P = self._project(q)
            M = mod(b.get("module"), P)
            params = b.get("params") or {}
            W, H = preview_size(M, params, b.get("h", 540))
            fr, info = PREVIEWS.frame(M, params, float(b.get("t", 0.0)), W, H, bool(b.get("clean")),
                                      bool(b.get("quick")), P)
            a = time.perf_counter()
            if info.get("alpha"):
                data, ctype = png(fr), "image/png"
            else:
                data, ctype = jpeg(fr, int(b.get("q", 90))), "image/jpeg"
            info["encode_ms"] = round((time.perf_counter() - a) * 1000, 1)
            info["size"] = [W, H]
            self._send(200, data, ctype, {"X-Info": json.dumps(info, ensure_ascii=True)})
        elif path == "/api/timeline":
            # phases, markers, the default name and the file slug for the editor's timeline and labels
            b = self._jbody()
            P = self._project(q)
            M = mod(b.get("module"), P)
            p = M.merge(b.get("params") or {})
            self._json({"timeline": M.timeline(p), "name": M.default_name(p), "slug": safe_slug(M.file_slug(p)),
                        "format": core.out_format(p, False) if "bg_mode" in p else "mp4"})
        elif path == "/api/strip":
            b = self._jbody()
            P = self._project(q)
            M = mod(b.get("module"), P)
            params = b.get("params") or {}
            n = int(min(max(int(b.get("n", 12)), 2), 40))
            th = int(min(max(int(b.get("th", 72)), 36), 144))
            W, H = preview_size(M, params, 144)
            tw = max(2, int(round(th * W / H)))
            img, total, approx = PREVIEWS.strip(M, params, n, W, H, tw, th, P)
            self._send(200, jpeg(img, 80), "image/jpeg",
                       {"X-Info": json.dumps({"n": n, "tw": tw, "th": th, "total": total, "approx": approx})})
        elif path == "/api/render":
            b = self._jbody()
            P = self._project(q)
            M = mod(b.get("module"), P)
            kind = "draft" if b.get("draft") else "final"
            params = b.get("params") or {}
            batch = getattr(M, "BATCH", None)
            items = [t for t in (b.get("batch") or b.get("titles") or []) if str(t).strip()] if batch else []
            if kind == "final" and items:
                key = batch["key"]
                n = len(items)
                jobs = []
                for i, t in enumerate(items):
                    v = str(t).strip()
                    if batch.get("lines"):
                        v = v.replace(" / ", "\n")
                    jobs.append(JOBS.add("final", M, dict(params, **{key: v}), None, batch=f"{i + 1}/{n}", P=P))
                self._json({"jobs": jobs})
            else:
                self._json({"jobs": [JOBS.add(kind, M, params, b.get("name"), b.get("hash"), P=P)]})
        elif path == "/api/cancel":
            j = JOBS.cancel(str(self._jbody().get("id")))
            if j is None:
                raise ApiError(404, "The job does not exist")
            self._json({"job": j})
        elif path == "/api/jobs/clear":
            JOBS.clear_finished()
            self._json({"jobs": JOBS.list()})
        elif path == "/api/recipe":
            self._json(save_recipe(self._project(q), self._jbody()))
        elif path == "/api/recipe/delete":
            self._json(trash_recipe(self._project(q), self._jbody().get("file")))
        elif path == "/api/font":
            fn = add_font(self.headers.get("X-Filename"), self._body())
            self._json({"file": fn, "fonts": core.list_fonts()})
        elif path == "/api/asset":
            P = project((q.get("project") or [""])[0] or self.headers.get("X-Project") or None)
            r = add_asset(P, self.headers.get("X-Kind") or (q.get("kind") or [""])[0],
                          self.headers.get("X-Filename"), self._body())
            PREVIEWS.forget(P)
            r["project"] = P.info()
            self._json(r)
        elif path == "/api/project":
            b = self._jbody()
            P = Project.create(b.get("name"), b.get("description") or b.get("popis") or "")
            self._json({"project": P.info(), "projects": [pr.info() for pr in Project.list_all()]})
        elif path == "/api/modules/reload":
            core.discover(reload=True)
            P = self._project(q)
            self._json({"modules": [module_info(m) for m in modules_for(P).values()],
                        "module_errors": dict(core.MODULE_ERRORS)})
        elif path == "/api/still":
            b = self._jbody()
            P = self._project(q)
            self._json({"file": save_still(mod(b.get("module"), P), P, b.get("params") or {}, b.get("t", 0.0))})
        elif path == "/api/open":
            self._json({"opened": open_folder(self._project(q), self._jbody().get("rel"))})
        elif path == "/api/ai/settings":
            self._json(ai.set_settings(self._jbody()))
        elif path == "/api/ai/test":
            self._json(ai.test_key())
        elif path == "/api/ai/chat":
            self._json(ai.start_chat(self._jbody(), self._project(q)))
        elif path == "/api/ai/cancel":
            self._json(ai.cancel(self._jbody().get("id")))
        else:
            raise ApiError(404, "Not found")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second copy bind the same port; refuse instead.
    allow_reuse_address = not WIN


def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=f"{core.APP_NAME}, the app")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="open the browser")
    a = ap.parse_args()
    Handler.port = a.port
    url = f"http://127.0.0.1:{a.port}/"
    try:
        srv = Server(("127.0.0.1", a.port), Handler)
    except OSError:
        print(f"Port {a.port} is already in use. The app is probably running already: {url}")
        if a.open:
            webbrowser.open(url)
        sys.exit(1)
    moved = core.migrate_legacy()
    if moved:
        print("Renamed the folders of an older version to the new English names:", flush=True)
        for line in moved:
            print("  " + line, flush=True)
    mods = all_modules()
    P = project()
    print(f"{core.APP_NAME} {APP_VERSION} ({core.APP_TAGLINE}) is running on {url}", flush=True)
    print(f"Modules: {', '.join(m.MODULE_LABEL for m in mods.values()) or 'none'}", flush=True)
    for name, err in core.MODULE_ERRORS.items():
        print(f"  The module {name} did not load: {err}", flush=True)
    print(f"Projects: {', '.join(pr.name for pr in Project.list_all())} (default {P.name})", flush=True)
    print("Stop it with Ctrl+C or by closing this window.", flush=True)
    if a.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        JOBS.shutdown()
        srv.server_close()
        print("Bye.")


if __name__ == "__main__":
    main()
