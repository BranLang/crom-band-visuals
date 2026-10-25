"""CROM MCP server: lets Claude (Claude Code, Claude Desktop) drive the CROM app.

Pure Python, no packages. Speaks MCP over stdio (one JSON-RPC message per line) and talks to the
CROM app over its local HTTP API. If the app is not running, it starts it in the background.

    claude mcp add crom -- python mcp_server.py
    python mcp_server.py --setup     (registers it in Claude Code and Claude Desktop, asks first)

Environment:
    CROM_PORT   port of the app (default 8765)
    CROM_NO_AUTOSTART=1   never start the app, only connect
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("CROM_PORT") or 8765)
BASE = f"http://127.0.0.1:{PORT}"
SERVER_INFO = {"name": "crom", "version": "1.0"}
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
_started_proc = None


def log(*a):
    print("[crom-mcp]", *a, file=sys.stderr, flush=True)


# ------------------------------------------------------------------ HTTP to the app

class AppError(Exception):
    pass


def _http(method, path, body=None, project=None, timeout=120, raw=False):
    headers = {"X-Crom": "1"}
    if project:
        headers["X-Project"] = project
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            if raw:
                return payload, r.headers.get("Content-Type", ""), r.headers.get("X-Info")
            return json.loads(payload.decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", "replace")
        try:
            msg = json.loads(msg).get("error") or msg
        except ValueError:
            pass
        raise AppError(f"CROM answered {e.code}: {msg}")


def _alive():
    try:
        _http("GET", "/api/ping", timeout=2)
        return True
    except Exception:
        return False


def ensure_app():
    global _started_proc
    if _alive():
        return
    if os.environ.get("CROM_NO_AUTOSTART") == "1":
        raise AppError(f"CROM is not running on {BASE}. Start it with start.bat or start.sh.")
    py = sys.executable
    venv = (os.path.join(HERE, ".venv", "Scripts", "python.exe") if os.name == "nt"
            else os.path.join(HERE, ".venv", "bin", "python"))
    if os.path.isfile(venv):
        py = venv
    log("starting the app on port", PORT)
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    logf = open(os.path.join(HERE, "_mcp_app.log"), "a", encoding="utf-8")
    _started_proc = subprocess.Popen([py, os.path.join(HERE, "app.py"), "--port", str(PORT)], cwd=HERE,
                                     stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     env=dict(os.environ, PYTHONIOENCODING="utf-8"), creationflags=flags)
    for _ in range(60):
        time.sleep(0.5)
        if _alive():
            return
        if _started_proc.poll() is not None:
            break
    raise AppError("CROM could not be started, see _mcp_app.log in the app folder.")


def api(method, path, body=None, project=None, **kw):
    ensure_app()
    return _http(method, path, body, project, **kw)


# ------------------------------------------------------------------ helpers

def _schema(module, project):
    q = "?module=" + urllib.parse.quote(module or "")
    return api("GET", "/api/schema" + q, project=project)


def _params(module, params, preset, project):
    params = dict(params or {})
    if preset:
        d = _schema(module, project)
        pr = next((p for p in d.get("presets") or [] if p["id"] == preset), None)
        if pr is None:
            raise AppError(f"The preset {preset!r} does not exist. Available: {', '.join(p['id'] for p in d['presets'])}")
        params = dict(pr.get("params") or {}, **params)
    return params


def _out_root(project):
    d = api("GET", "/api/schema", project=project)
    return d.get("out_root") or ""


def _abs_files(job, project):
    root = _out_root(project or job.get("project"))
    return [os.path.normpath(os.path.join(root, f)) for f in job.get("files") or []]


def text(obj):
    if not isinstance(obj, str):
        obj = json.dumps(obj, ensure_ascii=False, indent=1)
    return {"content": [{"type": "text", "text": obj}]}


# ------------------------------------------------------------------ tools

P_PROJECT = {"type": "string", "description": "Project slug (one band or client). Omit for the default project."}
P_MODULE = {"type": "string", "description": "Module id, e.g. titles, logo_reveal, waving_cover. See crom_status."}
P_PARAMS = {"type": "object", "description": "Parameter overrides by key (see crom_schema). Missing keys keep defaults."}
P_PRESET = {"type": "string", "description": "Optional preset id applied under params."}


def t_status(a):
    ping = api("GET", "/api/ping")
    d = api("GET", "/api/schema", project=a.get("project"))
    return text({"app": d.get("app_name"), "version": d.get("app"), "url": BASE + "/", "started": ping.get("started"),
                 "project": d["project"]["slug"], "out_root": d.get("out_root"),
                 "modules": [{"id": m["id"], "label": m["label"], "desc": m.get("desc")} for m in d["modules"]],
                 "module_errors": d.get("module_errors") or {},
                 "projects": [p["slug"] for p in d.get("projects") or []]})


def t_projects(a):
    return text(api("GET", "/api/projects"))


def t_create_project(a):
    return text(api("POST", "/api/project", {"name": a["name"], "description": a.get("description") or ""}))


def t_schema(a):
    d = _schema(a["module"], a.get("project"))
    groups = []
    for g in d["schema"]:
        ps = []
        for p in g["params"]:
            row = {k: p[k] for k in ("key", "label", "type", "default", "min", "max", "unit", "options", "kind", "hint")
                   if k in p}
            ps.append(row)
        groups.append({"group": g.get("label"), "params": ps})
    return text({"module": d["module"], "label": d["label"], "desc": d.get("desc"), "batch": d.get("batch"),
                 "presets": [{"id": p["id"], "label": p["label"], "params": p.get("params")} for p in d["presets"]],
                 "assets": d["project"].get("assets"), "fonts": d.get("fonts"), "schema": groups})


def t_preview(a):
    params = _params(a["module"], a.get("params"), a.get("preset"), a.get("project"))
    body = {"module": a["module"], "params": params, "t": float(a.get("t", 0.0)), "h": int(a.get("height", 540)),
            "q": 85}
    data, ctype, info = api("POST", "/api/frame", body, project=a.get("project"), raw=True)
    mime = "image/png" if "png" in ctype else "image/jpeg"
    out = [{"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": mime}]
    if info:
        try:
            inf = json.loads(info)
            keep = {k: v for k, v in inf.items() if not k.endswith("_ms")}
            out.append({"type": "text", "text": json.dumps(keep, ensure_ascii=False)})
        except ValueError:
            pass
    return {"content": out}


def t_strip(a):
    params = _params(a["module"], a.get("params"), a.get("preset"), a.get("project"))
    body = {"module": a["module"], "params": params, "n": int(a.get("frames", 12)), "th": int(a.get("thumb_height", 96))}
    data, _ctype, info = api("POST", "/api/strip", body, project=a.get("project"), raw=True)
    return {"content": [{"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": "image/jpeg"},
                        {"type": "text", "text": info or ""}]}


def t_timeline(a):
    params = _params(a["module"], a.get("params"), a.get("preset"), a.get("project"))
    return text(api("POST", "/api/timeline", {"module": a["module"], "params": params}, project=a.get("project")))


def _wait(ids, timeout):
    end = time.time() + timeout
    while True:
        jobs = [j for j in api("GET", "/api/jobs")["jobs"] if j["id"] in ids]
        if all(j["state"] not in ("queued", "running") for j in jobs) or time.time() > end:
            return jobs
        time.sleep(1.5)


def _job_view(j):
    keep = ("id", "kind", "module", "project", "name", "state", "msg", "done", "total", "eta", "sec", "res",
            "format", "version", "batch", "warnings")
    v = {k: j.get(k) for k in keep}
    if j.get("files"):
        v["files"] = _abs_files(j, j.get("project"))
    return v


def t_render(a):
    params = _params(a["module"], a.get("params"), a.get("preset"), a.get("project"))
    body = {"module": a["module"], "params": params, "draft": bool(a.get("draft")), "name": a.get("name")}
    if a.get("batch"):
        body["batch"] = list(a["batch"])
    jobs = api("POST", "/api/render", body, project=a.get("project"))["jobs"]
    wait = a.get("wait", True)
    if wait:
        jobs = _wait({j["id"] for j in jobs}, float(a.get("timeout", 600)))
    return text({"jobs": [_job_view(j) for j in jobs],
                 "note": "Still running: check crom_jobs later." if any(j["state"] in ("queued", "running") for j in jobs)
                 else ""})


def t_jobs(a):
    jobs = api("GET", "/api/jobs")["jobs"]
    if a.get("id"):
        jobs = [j for j in jobs if j["id"] == str(a["id"])]
        if a.get("wait"):
            jobs = _wait({str(a["id"])}, float(a.get("timeout", 600)))
    return text({"jobs": [_job_view(j) for j in jobs[: int(a.get("limit", 10))]]})


def t_cancel(a):
    return text(api("POST", "/api/cancel", {"id": str(a["id"])}))


def t_outputs(a):
    root = _out_root(a.get("project"))
    res = []
    for g in api("GET", "/api/outputs", project=a.get("project"))["outputs"][: int(a.get("limit", 10))]:
        for v in g["versions"]:
            res.append({"slug": g["slug"], "version": v["v"], "module": v["module"],
                        "files": [os.path.normpath(os.path.join(root, f["rel"])) for f in v["files"]]})
    return text({"out_root": root, "outputs": res})


def t_recipes(a):
    return text(api("GET", "/api/recipes", project=a.get("project")))


def t_load_recipe(a):
    q = "?file=" + urllib.parse.quote(a["file"])
    return text(api("GET", "/api/recipe" + q, project=a.get("project")))


def t_save_recipe(a):
    params = _params(a["module"], a.get("params"), a.get("preset"), a.get("project"))
    return text(api("POST", "/api/recipe", {"name": a["name"], "module": a["module"], "params": params},
                    project=a.get("project")))


def t_add_asset(a):
    fp = a["path"]
    if not os.path.isfile(fp):
        raise AppError(f"The file does not exist: {fp}")
    ensure_app()
    with open(fp, "rb") as f:
        data = f.read()
    headers = {"X-Crom": "1", "X-Kind": a["kind"], "X-Filename": urllib.parse.quote(os.path.basename(fp)),
               "Content-Type": "application/octet-stream"}
    if a.get("project"):
        headers["X-Project"] = a["project"]
    req = urllib.request.Request(BASE + "/api/asset", data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return text(json.loads(r.read().decode("utf-8")))
    except urllib.error.HTTPError as e:
        raise AppError(f"CROM answered {e.code}: {e.read().decode('utf-8', 'replace')}")


def t_reload(a):
    return text(api("POST", "/api/modules/reload", {}, project=a.get("project")))


TOOLS = [
    ("crom_status", "Is CROM running, which modules (effects) and projects exist, where outputs go. Call first.",
     {"project": P_PROJECT}, [], t_status, True),
    ("crom_projects", "List projects (one per band or client) with their uploaded assets (logos, covers).",
     {}, [], t_projects, True),
    ("crom_create_project", "Create a new project folder for a band or client.",
     {"name": {"type": "string"}, "description": {"type": "string"}}, ["name"], t_create_project, False),
    ("crom_schema", "All parameters of a module with defaults and ranges, its presets and the project's assets.",
     {"module": P_MODULE, "project": P_PROJECT}, ["module"], t_schema, True),
    ("crom_preview", "Render one preview frame at time t (seconds) and return it as an image. Fast, no files written.",
     {"module": P_MODULE, "params": P_PARAMS, "preset": P_PRESET, "t": {"type": "number"},
      "height": {"type": "integer", "description": "Preview height in px, default 540."}, "project": P_PROJECT},
     ["module"], t_preview, True),
    ("crom_strip", "Contact sheet of evenly spaced frames across the whole animation, as one image.",
     {"module": P_MODULE, "params": P_PARAMS, "preset": P_PRESET, "frames": {"type": "integer"},
      "thumb_height": {"type": "integer"}, "project": P_PROJECT}, ["module"], t_strip, True),
    ("crom_timeline", "Phases and timing of the animation plus the output name and format.",
     {"module": P_MODULE, "params": P_PARAMS, "preset": P_PRESET, "project": P_PROJECT}, ["module"], t_timeline, True),
    ("crom_render", "Render a video. draft=true is a quick low-res preview; otherwise a full-quality numbered version "
                    "(v01, v02...). batch renders one final per title with the same recipe (modules with batch only). "
                    "Waits for the result by default and returns the file paths.",
     {"module": P_MODULE, "params": P_PARAMS, "preset": P_PRESET, "draft": {"type": "boolean"},
      "name": {"type": "string"}, "batch": {"type": "array", "items": {"type": "string"}},
      "wait": {"type": "boolean"}, "timeout": {"type": "number"}, "project": P_PROJECT},
     ["module"], t_render, False),
    ("crom_jobs", "Render queue: state, progress and output files. Pass id (and wait=true) for one job.",
     {"id": {"type": "string"}, "wait": {"type": "boolean"}, "timeout": {"type": "number"},
      "limit": {"type": "integer"}}, [], t_jobs, True),
    ("crom_cancel", "Cancel a queued or running render.", {"id": {"type": "string"}}, ["id"], t_cancel, False),
    ("crom_outputs", "Finished videos of a project, newest first, with absolute file paths.",
     {"project": P_PROJECT, "limit": {"type": "integer"}}, [], t_outputs, True),
    ("crom_recipes", "Saved recipes (named parameter sets) of a project.", {"project": P_PROJECT}, [], t_recipes, True),
    ("crom_load_recipe", "Load a saved recipe: module and params.",
     {"file": {"type": "string"}, "project": P_PROJECT}, ["file"], t_load_recipe, True),
    ("crom_save_recipe", "Save params as a named recipe so the look can be reused later.",
     {"name": {"type": "string"}, "module": P_MODULE, "params": P_PARAMS, "preset": P_PRESET, "project": P_PROJECT},
     ["name", "module"], t_save_recipe, False),
    ("crom_add_asset", "Copy a local file into the project (kind logo = PNG logo, cover = album cover).",
     {"path": {"type": "string", "description": "Absolute path of the file."},
      "kind": {"type": "string", "enum": ["logo", "cover"]}, "project": P_PROJECT}, ["path", "kind"], t_add_asset, False),
    ("crom_reload_modules", "Reload effect modules after a module file in modules/ was added or changed.",
     {"project": P_PROJECT}, [], t_reload, False),
]
BY_NAME = {t[0]: t for t in TOOLS}


def tool_list():
    out = []
    for name, desc, props, req, _fn, ro in TOOLS:
        out.append({"name": name, "description": desc,
                    "inputSchema": {"type": "object", "properties": props, "required": req},
                    "annotations": {"readOnlyHint": ro}})
    return out


# ------------------------------------------------------------------ JSON-RPC loop

def handle(msg):
    method = msg.get("method")
    if method == "initialize":
        want = (msg.get("params") or {}).get("protocolVersion")
        return {"protocolVersion": want if want in PROTOCOLS else PROTOCOLS[0],
                "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO,
                "instructions": "CROM renders music-video effects (song titles, logo intros, logo reveals, waving "
                                "album covers) for bands. Start with crom_status, read crom_schema for the module, "
                                "look with crom_preview or crom_strip, then crom_render (draft first). Logos and "
                                "covers are used pixel-exact; never ask the app to redraw them."}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": tool_list()}
    if method == "tools/call":
        p = msg.get("params") or {}
        t = BY_NAME.get(p.get("name"))
        if t is None:
            raise KeyError(f"Unknown tool {p.get('name')}")
        try:
            return t[4](p.get("arguments") or {})
        except (AppError, KeyError, ValueError, OSError) as e:
            return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
    if method in ("resources/list",):
        return {"resources": []}
    if method in ("prompts/list",):
        return {"prompts": []}
    raise NotImplementedError(method)


def main():
    stdin = sys.stdin.buffer
    out = sys.stdout.buffer
    for raw in stdin:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "id" not in msg:  # notification
            continue
        try:
            res = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}
        except NotImplementedError as e:
            res = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"Method not found: {e}"}}
        except Exception as e:
            log("error", repr(e))
            res = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": str(e)}}
        out.write((json.dumps(res, ensure_ascii=False) + "\n").encode("utf-8"))
        out.flush()


def setup():
    """Interactive helper (connect_claude.bat): registers this server in Claude Code and Claude Desktop."""
    import shutil
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    py = sys.executable
    me = os.path.abspath(__file__)
    entry = {"command": py, "args": [me]}
    print("CROM: connect to Claude")
    print()
    claude = shutil.which("claude")
    if claude:
        ans = input("Found Claude Code. Add CROM to Claude Code? [Y/n] ").strip().lower()
        if ans in ("", "y", "yes", "a", "ano"):
            subprocess.call([claude, "mcp", "remove", "--scope", "user", "crom"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            rc = subprocess.call([claude, "mcp", "add", "--scope", "user", "crom", "--", py, me])
            print("Claude Code: done." if rc == 0 else "Claude Code: that did not work, see the message above.")
    else:
        print("Claude Code was not found (that is fine if you only use Claude Desktop).")
    cfg_dir = (os.path.expanduser("~/Library/Application Support/Claude") if sys.platform == "darwin"
               else os.path.join(os.environ.get("APPDATA", ""), "Claude"))
    cfg = os.path.join(cfg_dir, "claude_desktop_config.json")
    print()
    if os.path.isdir(cfg_dir):
        ans = input("Found Claude Desktop. Add CROM to its settings? [Y/n] ").strip().lower()
        if ans in ("", "y", "yes", "a", "ano"):
            data = {}
            if os.path.isfile(cfg):
                try:
                    with open(cfg, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except ValueError:
                    print(f"The file {cfg} is not valid JSON, leaving it alone. Add CROM by hand as shown below.")
                    data = None
                if data is not None:
                    shutil.copy2(cfg, cfg + ".backup")
            if data is not None:
                data.setdefault("mcpServers", {})["crom"] = entry
                with open(cfg, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print("Claude Desktop: done. Quit Claude Desktop completely (also from the tray or menu bar) and open it again.")
    else:
        print("Claude Desktop was not found.")
    print()
    print('To set it up by hand, this goes into claude_desktop_config.json under "mcpServers":')
    print()
    print(json.dumps({"crom": entry}, indent=2))


if __name__ == "__main__":
    if "--setup" in sys.argv[1:]:
        setup()
    else:
        main()
