#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Claude asistent: z textovej požiadavky nastaví parametre, pozrie si náhľady a doladí ich.

Beží cez oficiálne SDK `anthropic` (volanie Anthropic API stojí peniaze, kľúč zadáva používateľ).
Kľúč sa číta zo súboru _nastavenia.json (zadaný v aplikácii) alebo z premennej prostredia
ANTHROPIC_API_KEY; aplikácia ho nikdy nevypisuje ani neposiela späť do prehliadača.

Asistent dostane nástroje: get_state, set_params, apply_preset, switch_module, render_preview,
render_strip a queue_draft. Náhľady sú skutočné snímky z modulov v moduly/, takže
model vidí, čo nastavil, a môže to opraviť. app.py používa: status(), set_settings(),
test_key(), start_chat(), job(), cancel().
"""
import base64
import collections
import difflib
import json
import os
import re
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import jadro  # noqa: E402

SDK_DIR = os.path.join(HERE, "_sdk")
SETTINGS_FILE = os.path.join(HERE, "_nastavenia.json")

PREVIEW_H = 540          # the frames the model sees: 960x540 (about 690 tokens each)
TILE_H = 216             # contact sheet tiles, 384x216 at 16:9
MAX_ITER = 14            # model turns per request
MAX_IMAGES = 10          # preview images per request
MAX_IMAGES_SESSION = 30
MAX_MESSAGES = 60        # a longer conversation starts over (keeps the context and the bill small)
MAX_MINUTES = 15
WAIT_SIM_S = 120         # how long a logo preview waits for the fire simulation
MAX_TOKENS = 16000
KEEP_SESSIONS = 12

# prices in USD per million tokens (Claude API docs, 2026-10); only for the estimate shown in the UI
MODELS = [
    {"id": "claude-opus-5-5", "label": "Claude Opus 5.5", "note": "najlepší úsudok, 4 / 20 USD za mil. tokenov",
     "in": 4.0, "out": 20.0, "cache_read": 0.20, "cache_write": 5.0},
    {"id": "claude-sonnet-5-5", "label": "Claude Sonnet 5.5", "note": "rýchly, 2 / 10 USD za mil. tokenov",
     "in": 2.0, "out": 10.0, "cache_read": 0.20, "cache_write": 2.5},
    {"id": "claude-haiku-5-5", "label": "Claude Haiku 5.5", "note": "najlacnejší, 0,10 / 0,50 USD za mil. tokenov",
     "in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write": 0.125},
]
MODEL_BY_ID = {m["id"]: m for m in MODELS}
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"

HOST = None   # app.py plugs in an AppHost (previews, render queue); tests may plug in their own


class Cancelled(Exception):
    pass


# ----------------------------------------------------------------------------------------------
# SDK and settings
# ----------------------------------------------------------------------------------------------

_SDK = {"mod": None, "err": None, "tried": False}


def sdk_hint():
    return f'"{sys.executable}" -m pip install --target "{SDK_DIR}" anthropic'


def load_sdk():
    """The anthropic module, imported lazily: from site-packages or from aplikacia/_sdk."""
    if _SDK["tried"]:
        return _SDK["mod"]
    _SDK["tried"] = True
    try:
        import anthropic  # noqa: F401
        _SDK["mod"] = anthropic
    except ImportError as e:
        if os.path.isdir(SDK_DIR) and SDK_DIR not in sys.path:
            sys.path.insert(0, SDK_DIR)
            try:
                import anthropic  # noqa: F401,F811
                _SDK["mod"] = anthropic
            except ImportError as e2:
                _SDK["err"] = str(e2)
        else:
            _SDK["err"] = str(e)
    return _SDK["mod"]


def _load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_settings(d):
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_FILE)


def api_key():
    """(key, source): the key typed into the app wins over the environment. Never shown anywhere."""
    k = str(_load_settings().get("api_key") or "").strip()
    if k:
        return k, "súbor"
    k = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if k:
        return k, "prostredie"
    return None, None


def prefs():
    d = _load_settings()
    model = d.get("model") if d.get("model") in MODEL_BY_ID else DEFAULT_MODEL
    effort = d.get("effort") if d.get("effort") in EFFORTS else DEFAULT_EFFORT
    return model, effort


def status():
    sdk = load_sdk()
    key, src = api_key()
    model, effort = prefs()
    with LOCK:
        run = RUNNING
        busy = bool(run and run.status in ("queued", "running"))
    return {"sdk": sdk is not None, "sdk_version": getattr(sdk, "__version__", None) if sdk else None,
            "sdk_error": _SDK["err"], "sdk_hint": sdk_hint(), "sdk_dir": SDK_DIR,
            "key": bool(key), "key_source": src, "settings_file": SETTINGS_FILE,
            "models": MODELS, "model": model, "effort": effort, "efforts": EFFORTS,
            "limits": {"iterations": MAX_ITER, "images": MAX_IMAGES, "minutes": MAX_MINUTES},
            "busy": busy, "running": run.id if busy else None}


def set_settings(body):
    """Key, model and effort from the UI. The key is stored as typed, locally, and never returned."""
    d = _load_settings()
    if body.get("clear_key"):
        d.pop("api_key", None)
    elif body.get("key") is not None:
        k = str(body.get("key")).strip()
        if k:
            if len(k) < 20 or re.search(r"\s", k):
                raise ValueError("To nevyzerá ako API kľúč (očakávam dlhý reťazec bez medzier, obvykle sk-ant-…).")
            d["api_key"] = k
    if body.get("model") is not None:
        if body["model"] not in MODEL_BY_ID:
            raise ValueError("Neznámy model")
        d["model"] = body["model"]
    if body.get("effort") is not None:
        if body["effort"] not in EFFORTS:
            raise ValueError("Neznáma úroveň úsilia")
        d["effort"] = body["effort"]
    _save_settings(d)
    return status()


def test_key():
    """A check without tokens: lists one model with the stored key (a network call, nothing is billed)."""
    sdk = load_sdk()
    if sdk is None:
        raise ValueError("Knižnica anthropic nie je nainštalovaná. Príkaz: " + sdk_hint())
    key, src = api_key()
    if not key:
        raise ValueError("Nie je zadaný API kľúč.")
    try:
        client = sdk.Anthropic(api_key=key, max_retries=1, timeout=30.0)
        page = client.models.list(limit=1)
        first = next(iter(getattr(page, "data", []) or []), None)
        return {"ok": True, "key_source": src, "sample_model": getattr(first, "id", None)}
    except Exception as e:  # typed SDK errors become one friendly line
        return {"ok": False, "key_source": src, "error": friendly(e)}


SDK_ERROR_NAMES = {"AuthenticationError", "PermissionDeniedError", "NotFoundError", "RateLimitError",
                   "BadRequestError", "APIConnectionError", "APITimeoutError", "APIStatusError",
                   "InternalServerError", "UnprocessableEntityError", "ConflictError"}


def friendly(e):
    name = e.__class__.__name__
    msg = getattr(e, "message", None) or str(e)
    code = getattr(e, "status_code", None)
    if name == "AuthenticationError":
        return "API kľúč neplatí (401). Skontroluj ho v nastaveniach asistenta."
    if name == "PermissionDeniedError":
        return "Kľúč nemá prístup k tomuto modelu (403)."
    if name == "NotFoundError":
        return f"Model alebo cesta neexistuje (404): {msg}"
    if name == "RateLimitError":
        ra = None
        try:
            ra = e.response.headers.get("retry-after")
        except Exception:
            pass
        return "Limit požiadaviek Anthropic API (429), skús o chvíľu." + (f" Počkaj {ra} s." if ra else "")
    if name == "BadRequestError":
        return f"Chybná požiadavka (400): {msg}"
    if name in ("APIConnectionError", "APITimeoutError"):
        return "Nepodarilo sa spojiť s api.anthropic.com (sieť, firewall alebo výpadok)."
    if name in SDK_ERROR_NAMES or code:
        return f"Chyba API {code or ''}: {msg}".strip()
    if isinstance(e, (RuntimeError, ValueError, KeyError)):
        return str(e)
    return f"{name}: {msg}"


# ----------------------------------------------------------------------------------------------
# What the model knows: one stable system prompt with both parameter catalogues (cached by the API)
# ----------------------------------------------------------------------------------------------

def _num(v):
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _param_line(spec):
    k, lb, t = spec["key"], spec["label"], spec["type"]
    unit = f" {spec['unit']}" if spec.get("unit") else ""
    hint = f" {spec['hint']}" if spec.get("hint") else ""
    if t in ("float", "int"):
        kind = "integer" if t == "int" else "number"
        s = f"- {k} ({lb}): {kind} {_num(spec['min'])}..{_num(spec['max'])}{unit}, default {_num(spec['default'])}."
    elif t == "color":
        s = f"- {k} ({lb}): colour #rrggbb, default {spec['default']}."
    elif t == "select":
        opts = " | ".join(f"{o[0]} = {o[1]}" for o in spec["options"])
        s = f"- {k} ({lb}): one of {opts}; default {spec['default']}."
    elif t == "font":
        s = f"- {k} ({lb}): font file name from the font list; default {spec['default']}."
    elif t == "textarea":
        s = f"- {k} ({lb}): text, lines separated by \\n; default {json.dumps(spec['default'], ensure_ascii=False)}."
    else:
        s = f"- {k} ({lb}): {t}, default {spec['default']!r}."
    return s + hint


def _catalogue(M):
    out = []
    for g in M.SCHEMA:
        out.append(f"[{g['id']}] {g['label']}")
        out.extend(_param_line(p) for p in g["params"])
    out.append("Presets (apply_preset): " + "; ".join(
        f"{p['id']} = {p['label']}" + (f" {json.dumps(p['params'], ensure_ascii=False)}" if p["params"] else " (defaults)")
        for p in M.PRESETS))
    return "\n".join(out)


_SYS = {"sig": None, "text": None}


def _module_block(i, M):
    desc = getattr(M, "AI_DESC", "") or getattr(M, "MODULE_DESC", "") or M.MODULE_LABEL
    return f'{i}. "{M.MODULE_ID}" ({M.MODULE_LABEL}): {desc}'


def system_prompt(P, mods):
    fonts = jadro.list_fonts()
    sig = (tuple(f["file"] for f in fonts), P.slug, P.name, tuple(mods))
    if _SYS["sig"] == sig and _SYS["text"]:
        return _SYS["text"]
    font_lines = ", ".join(f"{f['file']} ({f['name']})" for f in fonts) or "(no fonts installed)"
    mod_lines = "\n".join(_module_block(i + 1, M) for i, M in enumerate(mods.values()))
    cats = "\n\n".join(f'Parameter catalogue, module "{M.MODULE_ID}":\n{_catalogue(M)}' for M in mods.values())
    popis = (P.meta.get("popis") or "").strip()
    text = f"""You are the built-in assistant of "{jadro.APP_NAME}" ({jadro.APP_TAGLINE}), a local desktop tool that renders short videos for bands: song titles, logo intros, moving album covers and more. Everything is drawn by the tool's own engines; you steer them through parameters and check the result with real preview frames.

Current project: "{P.name}"{(" - " + popis) if popis else ""}.

Modules available in this project:
{mod_lines}

Hard rule: logos and album artwork are the band's own pixels. Parameters only add light, motion, particles, camera moves and grading around them. Never claim to redraw a logo, change its letters, or alter a cover's title text.

Transparent output: the parameters bg_mode, bg_key and alpha_format (where a module has them) choose a normal scene background, a transparent video (alpha) or a flat key colour for chroma keying. Change them only when asked.

Available fonts: {font_lines}

How to work:
- The request comes with the current state (module and parameters that differ from the defaults). Call get_state when you need every value or the timeline.
- Change parameters with set_params (plain numbers, hex colours, option ids). Unknown keys are rejected, out-of-range numbers are clamped and reported. apply_preset sets a whole look; switch_module changes the module (its parameters reset to defaults).
- Judge with your eyes: after meaningful changes call render_preview at telling moments of the timeline (the lit peak, the middle of a hold, the middle of an exit). render_strip shows the whole arc in one picture. Transparent areas show as a grey checkerboard. The image budget per request is limited; the tool tells you how many are left. Do not render just to be sure when nothing changed.
- Keep timings consistent: starts before ends, lanes in order, everything inside the total length. The tools return the engine's warnings; fix what they say.
- Change only what the request implies. Leave res, fps, crf, seed, the background mode and any text alone unless asked.
- When a request describes a song's mood, choose colours, light, particles, direction and timing to match it, and say in one sentence why.
- If something cannot be done with these parameters, say so plainly instead of pretending.
- queue_draft renders a quick small draft video of the current parameters; use it only when the request says drafts are allowed, and only once at the end.
- Reply in the language of the request. Slovak requests get Slovak answers in the informal "ty" form. The final answer is short: what you changed in plain words (not only parameter keys), why, and at most one suggestion. Do not repeat the parameter dump.

{cats}
"""
    _SYS["sig"], _SYS["text"] = sig, text
    return text


def _strict(props, required):
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


TOOLS = [
    {"name": "get_state", "strict": True, "eager_input_streaming": True,
     "description": "Current module, every parameter value, which differ from the defaults, and the timeline (total length, phases, markers).",
     "input_schema": _strict({}, [])},
    {"name": "set_params", "eager_input_streaming": True,
     "description": "Change parameters of the current module. 'changes' maps parameter keys to new values: numbers for number/integer parameters, '#rrggbb' for colours, an option id for selects, a font file name for fonts, text for the title. Returns what was applied, what was clamped, errors for unknown keys or bad values, and the engine's warnings.",
     "input_schema": {"type": "object", "properties": {
         "changes": {"type": "object", "description": "parameter key -> new value",
                     "additionalProperties": {"type": ["string", "number", "boolean"]}}},
         "required": ["changes"]}},
    {"name": "apply_preset", "strict": True, "eager_input_streaming": True,
     "description": "Apply one of the module's presets by id. It resets the look keys the presets manage to defaults and applies the preset's values; timing and text stay.",
     "input_schema": _strict({"id": {"type": "string", "description": "preset id from the catalogue"}}, ["id"])},
    {"name": "switch_module", "strict": True, "eager_input_streaming": True,
     "description": "Switch to another module of this project (ids are listed in the system prompt). The parameters of the new module start at their defaults.",
     "input_schema": _strict({"module": {"type": "string", "description": "module id"}}, ["module"])},
    {"name": "render_preview", "strict": True, "eager_input_streaming": True,
     "description": "Render one real frame of the current parameters at time t (seconds from the start of the whole timeline) at preview size (540 px high) and return it as an image with its phase and the engine's warnings. Costs one image of the budget.",
     "input_schema": _strict({"t": {"type": "number", "description": "seconds from 0"}}, ["t"])},
    {"name": "render_strip", "strict": True, "eager_input_streaming": True,
     "description": "Render n evenly spaced frames over the whole timeline (4 to 12) as one labelled contact sheet, to judge the arc of the animation. Costs one image of the budget.",
     "input_schema": _strict({"n": {"type": "integer", "description": "number of frames, 4..12"}}, ["n"])},
    {"name": "queue_draft", "strict": True, "eager_input_streaming": True,
     "description": "Queue a quick small draft video of the current parameters in the app's render queue. Allowed only when the request says drafts are allowed.",
     "input_schema": _strict({}, [])},
]
TOOL_BY_NAME = {t["name"]: t for t in TOOLS}


def check_input(name, inp):
    """None when the parsed tool input matches its schema, else what is wrong (eager streaming may truncate)."""
    spec = TOOL_BY_NAME.get(name)
    if spec is None:
        return f"unknown tool {name!r}"
    if not isinstance(inp, dict):
        return "input is not an object"
    sch = spec["input_schema"]
    props = sch.get("properties", {})
    for k in sch.get("required", []):
        if k not in inp:
            return f"missing field {k!r}"
    if sch.get("additionalProperties") is False:
        extra = [k for k in inp if k not in props]
        if extra:
            return f"unexpected fields {extra}"
    for k, v in inp.items():
        ps = props.get(k)
        if ps is None:
            continue
        t = ps.get("type")
        if t == "number" and (isinstance(v, bool) or not isinstance(v, (int, float))):
            return f"{k} must be a number"
        if t == "integer" and (isinstance(v, bool) or not isinstance(v, int)
                               and not (isinstance(v, float) and v.is_integer())):
            return f"{k} must be an integer"
        if t == "string" and not isinstance(v, str):
            return f"{k} must be a string"
        if t == "object" and not isinstance(v, dict):
            return f"{k} must be an object"
        if "enum" in ps and v not in ps["enum"]:
            return f"{k} must be one of {ps['enum']}"
    return None


# ----------------------------------------------------------------------------------------------
# Parameter changes checked against the schema
# ----------------------------------------------------------------------------------------------

def apply_changes(M, params, changes):
    """(applied, notes, errors): values checked and clamped per the module schema; nothing else is touched."""
    applied, notes, errors = {}, [], []
    fonts = None
    for k, v in changes.items():
        spec = M.PARAMS.get(str(k))
        if spec is None:
            near = difflib.get_close_matches(str(k), list(M.PARAMS), n=3, cutoff=0.5)
            errors.append(f"unknown parameter {k!r}" + (f", did you mean {near}?" if near else ""))
            continue
        t = spec["type"]
        if t in ("float", "int"):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                try:
                    v = float(str(v).strip().replace(",", "."))
                except ValueError:
                    errors.append(f"{k}: expected a number, got {v!r}")
                    continue
            c = jadro.coerce(spec, v)
            if abs(float(c) - float(v)) > 1e-9:
                notes.append(f"{k}: {_num(float(v))} clamped to {_num(float(c))} (range {_num(spec['min'])}..{_num(spec['max'])})")
            applied[k] = c
        elif t == "color":
            s = str(v).strip().lower()
            if re.fullmatch(r"#[0-9a-f]{3}", s):
                s = "#" + "".join(ch * 2 for ch in s[1:])
            if re.fullmatch(r"#[0-9a-f]{6}", s):
                applied[k] = s
            else:
                errors.append(f"{k}: colour must be '#rrggbb', got {v!r}")
        elif t == "select":
            ids = [o[0] for o in spec["options"]]
            s = str(v).strip()
            hit = s if s in ids else next((o[0] for o in spec["options"] if o[1].lower() == s.lower()), None)
            if hit is None:
                errors.append(f"{k}: must be one of {ids}, got {v!r}")
            else:
                applied[k] = hit
        elif t == "font":
            if fonts is None:
                fonts = jadro.list_fonts()
            s = str(v).strip().lower()
            hit = next((f["file"] for f in fonts if s in (f["file"].lower(), f["name"].lower(),
                                                           os.path.splitext(f["file"])[0].lower())), None)
            if hit is None:
                errors.append(f"{k}: unknown font {v!r}; available: {[f['file'] for f in fonts]}")
            else:
                applied[k] = hit
        else:
            applied[k] = str(v)
    return applied, notes, errors


def preset_params(M, pid):
    pres = next((p for p in M.PRESETS if p["id"] == str(pid) or p["label"].lower() == str(pid).lower()), None)
    if pres is None:
        raise ValueError(f"unknown preset {pid!r}; available: {[p['id'] for p in M.PRESETS]}")
    managed = {k for p in M.PRESETS for k in p["params"]}
    d = M.defaults()
    out = {k: d[k] for k in managed}
    out.update(pres["params"])
    return pres, out


def changed_from_defaults(M, params):
    p, d = M.merge(params), M.defaults()
    return {k: p[k] for k in p if p[k] != d[k]}


def phase_at(tl, t):
    phases = tl.get("phases") or []
    for ph in phases:
        if ph["t0"] <= t < ph["t1"] or ph is phases[-1]:
            span = max(ph["t1"] - ph["t0"], 1e-9)
            return {"phase": ph["id"], "label": ph["label"], "pct": round(100.0 * (t - ph["t0"]) / span, 1),
                    "video": ph.get("video")}
    return None


def _jd(o):
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if hasattr(o, "model_dump"):
        return o.model_dump()
    return str(o)


def dumps(o):
    return json.dumps(o, ensure_ascii=False, default=_jd)


def _flat(frame):
    """RGBA over a grey checkerboard, so the model sees what is transparent."""
    if frame.ndim != 3 or frame.shape[2] != 4:
        return frame
    h, w = frame.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    chk = np.where((((yy // 16) + (xx // 16)) & 1)[..., None] == 1, 160, 105).astype(np.float32)
    a = frame[..., 3:4].astype(np.float32) / 255.0
    return np.clip(frame[..., :3].astype(np.float32) * a + chk * (1 - a), 0, 255).astype(np.uint8)


def jpeg_b64(frame, q=82, max_w=None):
    img = _flat(frame)
    if max_w and img.shape[1] > max_w:
        h = int(round(img.shape[0] * max_w / img.shape[1]))
        img = cv2.resize(img, (max_w, h), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", np.ascontiguousarray(img[..., ::-1]), [int(cv2.IMWRITE_JPEG_QUALITY), int(q)])
    if not ok:
        raise RuntimeError("JPEG sa nepodaril")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def contact_sheet(tiles, labels, per_row=4, band=22):
    """Tiles of one size in rows, each with a black label band under it."""
    th, tw = tiles[0].shape[:2]
    rows = []
    for r in range(0, len(tiles), per_row):
        cells = []
        for i in range(r, min(r + per_row, len(tiles))):
            cell = np.zeros((th + band, tw, 3), np.uint8)
            cell[:th] = tiles[i]
            cv2.putText(cell, labels[i], (6, th + band - 7), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (235, 235, 235), 1,
                        cv2.LINE_AA)
            cells.append(cell)
        while len(cells) < per_row:
            cells.append(np.zeros((th + band, tw, 3), np.uint8))
        rows.append(np.hstack(cells))
    return np.vstack(rows)


# ----------------------------------------------------------------------------------------------
# The host: how the assistant reaches the live previews and the render queue of app.py
# ----------------------------------------------------------------------------------------------

class AppHost:
    def __init__(self, previews, jobs, preview_size, get_project=None, mods_for=None):
        self.previews = previews
        self.jobs = jobs
        self._ps = preview_size
        self._project = get_project
        self._mods = mods_for

    def project(self, slug=None):
        return self._project(slug) if self._project else None

    def modules(self, P):
        return self._mods(P) if self._mods else jadro.available_for(jadro.discover(), P)

    def preview_size(self, M, params, h):
        return self._ps(M, params, h)

    def frame(self, M, params, t, W, H, quick, P=None):
        return self.previews.frame(M, params, t, W, H, clean=False, quick=quick, P=P)

    def strip(self, M, params, n, W, H, tw, th, P=None):
        return self.previews.strip(M, params, n, W, H, tw, th, P=P)

    def warnings(self, M, params, P=None):
        W, H = self._ps(M, params, PREVIEW_H)
        with self.previews.lock:
            R = self.previews._renderer(M, params, W, H, P)
            return list(R.info().get("warnings") or [])

    def queue_draft(self, M, params, name, P=None):
        return self.jobs.add("draft", M, params, name, P=P)


def sim_progress(M):
    f = getattr(M, "sim_progress", None)
    if callable(f):
        return f()
    sim = getattr(M, "SIM", None)
    return sim.progress() if sim is not None else None


# ----------------------------------------------------------------------------------------------
# One conversation with the model
# ----------------------------------------------------------------------------------------------

LOCK = threading.Lock()
SESSIONS = collections.OrderedDict()
RUNNING = None
_SEQ = [0]


class ChatSession:
    def __init__(self, model, effort):
        _SEQ[0] += 1
        self.id = f"{int(time.time())}_{_SEQ[0]}"
        self.model = model
        self.effort = effort
        self.created = time.time()
        self.messages = []
        self.steps = []
        self.lock = threading.Lock()
        self.status = "idle"
        self.error = None
        self.text = ""
        self.live = ""
        self.usage = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0, "calls": 0}
        self.images = 0
        self.images_total = 0
        self.iterations = 0
        self.cancel = False
        self.started = None
        self.ended = None
        self.P = None
        self.mods = {}
        self.module = ""
        self.start_module = ""
        self.params = {}
        self.start_params = {}
        self.allow_render = False
        self.prompt = ""
        self.thread = None
        self._no_top_cache = False

    # ---------- public view ----------
    def view(self, since=0):
        M = self.mods.get(self.module)
        p = M.merge(self.params) if M else dict(self.params)
        changed = {}
        if M is not None and self.start_module == self.module:
            s0 = M.merge(self.start_params)
            changed = {k: [s0[k], p[k]] for k in p if s0.get(k) != p[k]}
        with self.lock:
            steps = self.steps[int(since):]
            n = len(self.steps)
        return {"id": self.id, "session": self.id, "status": self.status, "module": self.module, "params": p,
                "module_changed": self.start_module != self.module, "changed": changed,
                "text": self.text, "live": self.live, "error": self.error, "usage": dict(self.usage),
                "cost_usd": self.cost(), "model": self.model, "effort": self.effort,
                "iterations": self.iterations, "images": self.images, "steps": steps, "n_steps": n,
                "started": self.started, "ended": self.ended, "allow_render": self.allow_render,
                "sec": round((self.ended or time.time()) - self.started, 1) if self.started else None,
                "messages": len(self.messages),
                "project": self.P.slug if self.P else None}

    def cost(self):
        m = MODEL_BY_ID.get(self.model) or MODELS[0]
        u = self.usage
        return round((u["input"] * m["in"] + u["cache_write"] * m["cache_write"] + u["cache_read"] * m["cache_read"]
                      + u["output"] * m["out"]) / 1e6, 4)

    # ---------- start ----------
    def begin(self, P, mods, module, params, allow_render, prompt):
        if self.P is not None and self.P.slug != P.slug:
            self.messages = []   # another band: the old conversation does not apply
        self.P = P
        self.mods = mods
        M = mods[module]
        self.module = module
        self.start_module = module
        self.params = M.merge(params)
        self.start_params = dict(self.params)
        self.allow_render = bool(allow_render)
        self.prompt = prompt
        self.status = "running"
        self.error = None
        self.text = ""
        self.live = ""
        self.images = 0
        self.iterations = 0
        self.cancel = False
        self.started = time.time()
        self.ended = None
        self._step("prompt", prompt)
        self.thread = threading.Thread(target=self._run, name="ai-chat", daemon=True)
        self.thread.start()

    def _step(self, kind, text="", **extra):
        with self.lock:
            d = {"i": len(self.steps), "kind": kind, "text": text,
                 "at": round(time.time() - (self.started or time.time()), 1)}
            d.update(extra)
            self.steps.append(d)
            return d

    def _check_cancel(self):
        if self.cancel:
            raise Cancelled()

    # ---------- the loop ----------
    def user_text(self, prompt):
        M = self._M()
        p = M.merge(self.params)
        tl = M.timeline(p)
        lines = [prompt.strip(), "",
                 f"[state] project: {self.P.name}",
                 f"[state] module: {self.module} ({M.MODULE_LABEL})",
                 f"[state] changed_from_defaults: {dumps(changed_from_defaults(M, p))}",
                 f"[state] timeline: total {round(tl['total'], 2)} s, phases "
                 + ", ".join(f"{ph['id']} {round(ph['t0'], 2)}-{round(ph['t1'], 2)} s" for ph in tl["phases"]),
                 f"[state] drafts_allowed: {'yes' if self.allow_render else 'no'}"]
        if "title" in p:
            lines.insert(4, f"[state] title: {dumps(p['title'])}")
        return "\n".join(lines)

    def request_kwargs(self):
        kw = {"model": self.model, "max_tokens": MAX_TOKENS,
              "system": [{"type": "text", "text": system_prompt(self.P, self.mods), "cache_control": {"type": "ephemeral"}}],
              "tools": TOOLS, "messages": self.messages,
              "output_config": {"effort": self.effort}}
        if not self._no_top_cache:
            kw["cache_control"] = {"type": "ephemeral"}
        return kw

    def _call(self, client):
        kw = self.request_kwargs()
        try:
            return self._stream(client, kw)
        except TypeError as e:
            # an older SDK without the top-level cache_control shortcut: drop it and go on
            if "cache_control" in str(e) and not self._no_top_cache:
                self._no_top_cache = True
                return self._stream(client, self.request_kwargs())
            raise

    def _stream(self, client, kw):
        self.live = ""
        with client.messages.stream(**kw) as stream:
            for event in stream:
                if self.cancel:
                    raise Cancelled()
                if getattr(event, "type", "") == "text":
                    if len(self.live) < 20000:
                        self.live += getattr(event, "text", "") or ""
            return stream.get_final_message()

    def _append_user_prompt(self):
        """The request as a user turn. A conversation cut off mid-tool first gets its open tool calls closed,
        and two user turns in a row are merged into one (the API would merge them anyway)."""
        def _g(b, k):
            return b.get(k) if isinstance(b, dict) else getattr(b, k, None)
        blocks = []
        last = self.messages[-1] if self.messages else None
        if last is not None and last.get("role") == "assistant":
            for b in last.get("content") or []:
                if _g(b, "type") == "tool_use":
                    blocks.append({"type": "tool_result", "tool_use_id": _g(b, "id"), "is_error": True,
                                   "content": "Cancelled by the user before the tool ran."})
        blocks.append({"type": "text", "text": self.user_text(self.prompt)})
        if last is not None and last.get("role") == "user":
            last["content"] = list(last.get("content") or []) + blocks
        else:
            self.messages.append({"role": "user", "content": blocks})

    def _account(self, usage):
        if usage is None:
            return
        g = lambda n: int(getattr(usage, n, 0) or 0)  # noqa: E731
        self.usage["input"] += g("input_tokens")
        self.usage["cache_write"] += g("cache_creation_input_tokens")
        self.usage["cache_read"] += g("cache_read_input_tokens")
        self.usage["output"] += g("output_tokens")
        self.usage["calls"] += 1

    def _run(self):
        global RUNNING
        try:
            sdk = load_sdk()
            if sdk is None:
                raise RuntimeError("Knižnica anthropic nie je nainštalovaná. Príkaz: " + sdk_hint())
            key, _src = api_key()
            if not key:
                raise RuntimeError("Nie je zadaný API kľúč (nastavenia asistenta alebo premenná ANTHROPIC_API_KEY).")
            if HOST is None:
                raise RuntimeError("Asistent nemá pripojené náhľady (HOST).")
            client = sdk.Anthropic(api_key=key, max_retries=2, timeout=600.0)
            self._append_user_prompt()
            deadline = time.monotonic() + MAX_MINUTES * 60
            json_retries = 0
            while True:
                self._check_cancel()
                if time.monotonic() > deadline:
                    raise RuntimeError(f"Časový limit {MAX_MINUTES} min, asistent sa zastavil.")
                if self.iterations >= MAX_ITER:
                    self._step("info", f"Dosiahnutý limit {MAX_ITER} krokov, končím s tým, čo je nastavené.")
                    self.text = self.text or "Dosiahol som limit krokov; parametre nechávam tak, ako sú teraz."
                    break
                self.iterations += 1
                try:
                    response = self._call(client)
                    json_retries = 0
                except ValueError as e:
                    # JSON of a tool input the SDK could not parse at all; the turn is re-issued (bounded)
                    json_retries += 1
                    self._step("info", f"Model poslal nečitateľný vstup nástroja, skúšam znova ({json_retries}/2).")
                    if json_retries > 2:
                        raise RuntimeError(f"Model opakovane poslal nečitateľný vstup nástroja: {e}")
                    continue
                self._account(getattr(response, "usage", None))
                content = list(getattr(response, "content", []) or [])
                self.messages.append({"role": "assistant", "content": content})
                self.live = ""
                texts = [getattr(b, "text", "") for b in content if getattr(b, "type", "") == "text"]
                for t in texts:
                    if t.strip():
                        self._step("text", t)
                stop = getattr(response, "stop_reason", None)
                tool_uses = [b for b in content if getattr(b, "type", "") == "tool_use"]
                if stop == "refusal":
                    sd = getattr(response, "stop_details", None)
                    why = " ".join(str(x) for x in (getattr(sd, "category", None), getattr(sd, "explanation", None)) if x)
                    raise RuntimeError("Model požiadavku odmietol." + (f" ({why})" if why else ""))
                if stop == "pause_turn":
                    continue
                if not tool_uses:
                    self.text = "\n\n".join(t for t in texts if t.strip())
                    break
                if stop == "max_tokens":
                    raise RuntimeError("Odpoveď modelu bola odseknutá uprostred volania nástroja (max_tokens).")
                results = []
                for b in tool_uses:
                    self._check_cancel()
                    results.append(self._run_tool(b))
                self.messages.append({"role": "user", "content": results})
            self.status = "done"
        except Cancelled:
            self.status = "cancelled"
            self.error = "Zrušené"
            self._step("info", "Zrušené používateľom.")
        except Exception as e:
            # my own messages and API errors go to the log as one line; a traceback only for the unexpected
            if isinstance(e, (RuntimeError, ValueError)) or e.__class__.__name__ in SDK_ERROR_NAMES:
                print(f"[ai] {e.__class__.__name__}: {e}", flush=True)
            else:
                traceback.print_exc()
            self.status = "error"
            self.error = friendly(e)
            self._step("error", self.error)
        finally:
            self.ended = time.time()
            self.live = ""
            with LOCK:
                if RUNNING is self:
                    RUNNING = None

    # ---------- tools ----------
    def _run_tool(self, block):
        name = getattr(block, "name", "")
        inp = getattr(block, "input", None)
        tid = getattr(block, "id", "")
        bad = check_input(name, inp)
        if bad:
            self._step("tool", name, args=dumps(inp)[:300], result=f"neplatný vstup: {bad}", error=True)
            return {"type": "tool_result", "tool_use_id": tid, "is_error": True,
                    "content": dumps({"INVALID_JSON": bad, "input": dumps(inp)[:300]})}
        fn = getattr(self, "tool_" + name)
        try:
            content, is_error, log, thumb = fn(inp)
        except Cancelled:
            raise
        except Exception as e:
            traceback.print_exc()
            content, is_error, log, thumb = f"Nástroj zlyhal: {e.__class__.__name__}: {e}", True, None, None
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        shown = log if log is not None else next((c["text"] for c in content if c.get("type") == "text"), "")
        self._step("tool", name, args=dumps(inp)[:300], result=str(shown)[:600], error=bool(is_error), thumb=thumb)
        res = {"type": "tool_result", "tool_use_id": tid, "content": content}
        if is_error:
            res["is_error"] = True
        return res

    def _M(self):
        return self.mods[self.module]

    def tool_get_state(self, inp):
        M = self._M()
        p = M.merge(self.params)
        out = {"module": M.MODULE_ID, "label": M.MODULE_LABEL, "params": p,
               "changed_from_defaults": changed_from_defaults(M, p), "timeline": M.timeline(p),
               "warnings": HOST.warnings(M, p, self.P)}
        return dumps(out), False, f"{len(out['changed_from_defaults'])} zmien oproti predvoleným", None

    def tool_set_params(self, inp):
        M = self._M()
        changes = inp.get("changes") or {}
        if not changes:
            return dumps({"applied": {}, "errors": ["changes is empty"]}), True, "prázdne", None
        applied, notes, errors = apply_changes(M, self.params, changes)
        if applied:
            self.params = M.merge({**self.params, **applied})
        p = M.merge(self.params)
        tl = M.timeline(p)
        out = {"applied": applied, "clamped": notes, "errors": errors,
               "timeline": {"total": round(tl["total"], 2), "phases": [[ph["id"], round(ph["t0"], 2), round(ph["t1"], 2)]
                                                                         for ph in tl["phases"]]},
               "warnings": HOST.warnings(M, p, self.P)}
        log = ", ".join(f"{k}={_num(v) if isinstance(v, float) else v}" for k, v in applied.items())
        if errors:
            log += (" | " if log else "") + "chyby: " + "; ".join(errors)
        return dumps(out), bool(errors) and not applied, log, None

    def tool_apply_preset(self, inp):
        M = self._M()
        pres, vals = preset_params(M, inp.get("id"))
        self.params = M.merge({**self.params, **vals})
        return dumps({"preset": pres["id"], "label": pres["label"], "set": vals}), False, pres["label"], None

    def tool_switch_module(self, inp):
        mid = str(inp.get("module"))
        if mid not in self.mods:
            raise ValueError(f"unknown module {mid!r}; this project has {list(self.mods)}")
        self.module = mid
        self.params = self.mods[mid].defaults()
        M = self.mods[mid]
        out = {"module": mid, "label": M.MODULE_LABEL, "params": self.params, "timeline": M.timeline(self.params)}
        return dumps(out), False, M.MODULE_LABEL, None

    def _budget(self):
        if self.images >= MAX_IMAGES or self.images_total >= MAX_IMAGES_SESSION:
            return ("Image budget exhausted for this request. Decide from what you have seen and finish with your "
                    "summary; the user can ask again if more checking is needed.")
        return None

    def _wait_sim(self, M, need):
        """Lets the logo's background simulation reach frame `need`, so the exact frame is cheap afterwards."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < WAIT_SIM_S:
            pr = sim_progress(M)
            if pr is None:
                return
            if pr.get("error") or pr["done"] >= min(int(need), int(pr["total"])):
                return
            if not pr.get("busy") and time.monotonic() - t0 > 1.0:
                return  # nothing is simulating; the exact frame steps the simulation itself
            self._check_cancel()
            time.sleep(0.25)

    def tool_render_preview(self, inp):
        M = self._M()
        over = self._budget()
        if over:
            return over, True, "vyčerpaný limit obrázkov", None
        p = M.merge(self.params)
        tl = M.timeline(p)
        fps = int(p["fps"])
        t = min(max(float(inp.get("t", 0.0)), 0.0), max(tl["total"] - 1.0 / fps, 0.0))
        W, H = HOST.preview_size(M, p, PREVIEW_H)
        fr, info = HOST.frame(M, p, t, W, H, quick=True, P=self.P)
        if info.get("approx"):
            self._wait_sim(M, int(round(t * fps)) + 1)
            self._check_cancel()
            fr, info = HOST.frame(M, p, t, W, H, quick=False, P=self.P)
        self.images += 1
        self.images_total += 1
        meta = {"t": round(t, 3), "at": phase_at(tl, t), "size": [W, H], "warnings": info.get("warnings") or [],
                "approx": bool(info.get("approx")), "images_left": max(MAX_IMAGES - self.images, 0)}
        content = [{"type": "text", "text": dumps(meta)},
                   {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": jpeg_b64(fr, 82)}}]
        ph = meta["at"] or {}
        log = f"t = {t:.2f} s" + (f" ({ph.get('label')} {ph.get('pct')} %)" if ph else "")
        return content, False, log, jpeg_b64(fr, 70, max_w=320)

    def tool_render_strip(self, inp):
        M = self._M()
        over = self._budget()
        if over:
            return over, True, "vyčerpaný limit obrázkov", None
        n = int(min(max(int(inp.get("n", 8)), 4), 12))
        p = M.merge(self.params)
        tl = M.timeline(p)
        W, H = HOST.preview_size(M, p, TILE_H)
        if sim_progress(M) is not None:
            HOST.frame(M, p, 0.0, W, H, quick=True, P=self.P)   # points the simulation at these parameters
            self._wait_sim(M, int(round(tl["total"] * int(p["fps"]))))
            self._check_cancel()
        img, total, approx = HOST.strip(M, p, n, W, H, W, H, P=self.P)
        tiles = [img[:, i * W:(i + 1) * W] for i in range(n)]
        times = [(i + 0.5) / n * total for i in range(n)]
        labels = []
        for i, t in enumerate(times):
            ph = phase_at(tl, t) or {}
            labels.append(f"{i + 1}  t={t:.1f}s" + (f"  {ph.get('phase')} {ph.get('pct'):.0f}%" if ph else ""))
        sheet = contact_sheet(tiles, labels)
        self.images += 1
        self.images_total += 1
        meta = {"n": n, "tiles": [{"i": i + 1, "t": round(t, 2), "at": phase_at(tl, t)} for i, t in enumerate(times)],
                "approx": bool(approx), "images_left": max(MAX_IMAGES - self.images, 0),
                "warnings": HOST.warnings(M, p, self.P)}
        content = [{"type": "text", "text": dumps(meta)},
                   {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": jpeg_b64(sheet, 80)}}]
        return content, False, f"{n} políčok cez {total:.1f} s", jpeg_b64(sheet, 70, max_w=480)

    def tool_queue_draft(self, inp):
        if not self.allow_render:
            return ("Drafts are not allowed in this request. Tell the user the parameters are set and that the "
                    "Náhľad button renders a draft."), True, "náhľad nie je povolený", None
        M = self._M()
        p = M.merge(self.params)
        job = HOST.queue_draft(M, p, "AI: " + M.default_name(p), P=self.P)
        return dumps({"queued": True, "job": job.get("id"), "name": job.get("name"), "res": job.get("res"),
                      "frames": job.get("total")}), False, f"náhľad vo fronte (úloha {job.get('id')})", None


# ----------------------------------------------------------------------------------------------
# Entry points for app.py
# ----------------------------------------------------------------------------------------------

def start_chat(body, P=None):
    global RUNNING
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Napíš, čo má asistent urobiť.")
    if len(prompt) > 8000:
        raise ValueError("Požiadavka je pridlhá (max 8000 znakov).")
    if load_sdk() is None:
        raise ValueError("Knižnica anthropic nie je nainštalovaná. Príkaz: " + sdk_hint())
    if not api_key()[0]:
        raise ValueError("Nie je zadaný API kľúč. Zadaj ho v nastaveniach asistenta.")
    if HOST is None:
        raise ValueError("Asistent nemá pripojené náhľady (HOST).")
    if P is None:
        P = HOST.project(body.get("project") or None)
    mods = HOST.modules(P)
    if not mods:
        raise ValueError("V tomto projekte nie je žiadny modul.")
    module = str(body.get("module") or next(iter(mods)))
    if module not in mods:
        raise ValueError(f"Modul {module} v projekte {P.name} nie je.")
    params = body.get("params")
    params = {} if params is None else params
    if not isinstance(params, dict):
        raise ValueError("params musí byť objekt")
    model, effort = prefs()
    model = body.get("model") if body.get("model") in MODEL_BY_ID else model
    effort = body.get("effort") if body.get("effort") in EFFORTS else effort
    with LOCK:
        if RUNNING is not None and RUNNING.status in ("queued", "running"):
            raise ValueError("Asistent ešte pracuje. Počkaj, kým skončí, alebo ho zruš.")
        sid = str(body.get("session") or "")
        sess = SESSIONS.get(sid)
        if sess is not None and (sess.model != model or len(sess.messages) >= MAX_MESSAGES):
            sess = None  # a new model or a long history: start a fresh conversation
        if sess is None:
            sess = ChatSession(model, effort)
            SESSIONS[sess.id] = sess
            while len(SESSIONS) > KEEP_SESSIONS:
                SESSIONS.popitem(last=False)
        sess.effort = effort
        RUNNING = sess
        sess.begin(P, mods, module, params, body.get("allow_render"), prompt)
    return sess.view(0)


def job(sid, since=0):
    sess = SESSIONS.get(str(sid or ""))
    if sess is None:
        raise KeyError("Konverzácia neexistuje")
    return sess.view(since)


def cancel(sid):
    sess = SESSIONS.get(str(sid or ""))
    if sess is None:
        raise KeyError("Konverzácia neexistuje")
    sess.cancel = True
    return sess.view(0)


def sessions():
    return [{"id": s.id, "status": s.status, "model": s.model, "prompt": s.prompt[:80], "cost_usd": s.cost(),
             "messages": len(s.messages)} for s in reversed(list(SESSIONS.values()))]
