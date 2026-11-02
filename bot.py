"""Mirror Bot core (layer 0): a sandbox robot that only does what the xAI Grok API says.
Strict allowlist, propose/approve/deny gate, refusals, driver.log, unbounded world.
Optional layers in layers/ plug in through hooks; toggle with MIRROR_LAYERS."""
import json, os, sys, datetime, importlib, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
def path(env, name): return os.environ.get(env, os.path.join(HERE, name))
HEADS = "NESW"; DV = {"N": (0, -1), "E": (1, 0), "S": (0, 1), "W": (-1, 0)}
PRIMS = {"forward", "left", "right", "stop"}
ALL_LAYERS = ["functions", "hidden_grid", "token_budget", "api_cost"]

# ---------- layer plumbing ----------
def layer_names():
    want = os.environ.get("MIRROR_LAYERS", "all").strip()
    if want == "all": return list(ALL_LAYERS)
    if want in ("", "none"): return []
    return [n.strip() for n in want.split(",")]
def layers():
    mods = []
    for n in ALL_LAYERS:
        if n in layer_names():
            try: mods.append(importlib.import_module("layers." + n))
            except ModuleNotFoundError: pass  # layer not present at this tag
    return mods
def active(): return [m.__name__.split(".")[-1] for m in layers()]
def hook(name, *a): return [getattr(m, name)(*a) for m in layers() if hasattr(m, name)]
def first(name, *a):
    for m in layers():
        if hasattr(m, name):
            r = getattr(m, name)(*a)
            if r is not None: return r
    return None
def chain(name, val, *a):
    for m in layers():
        if hasattr(m, name): val = getattr(m, name)(val, *a)
    return val

# ---------- state / log ----------
def fresh():
    s = {"x": 0, "y": 0, "heading": "E", "visited": [[0, 0]], "pending": None}
    hook("fresh", s); return s
def load():
    p = path("MIRROR_STATE", "state.json")
    if not os.path.exists(p): return fresh()
    with open(p) as fh: s = json.load(fh)
    base = fresh(); s = {k: s[k] for k in base if k in s}
    return dict(base, **s)
def save(s):
    with open(path("MIRROR_STATE", "state.json"), "w") as fh: json.dump(s, fh, indent=1)
def dlog(cmd, raw, result):
    with open(path("MIRROR_LOG", "driver.log"), "a") as f:
        f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')}\tcmd={cmd}\traw={json.dumps(raw)}\tresult={result}\n")
def cj(o): return json.dumps(o, separators=(",", ":"))
def compact(): return bool(first("compact"))
def view(s):
    """The only state Grok ever sees."""
    v = {"x": s["x"], "y": s["y"], "heading": s["heading"], "visited": s["visited"]}
    if s["pending"]: v["pending"] = s["pending"]
    return chain("view", v, s)
def inside(x, y): return all(hook("inside", x, y))  # core world is unbounded

# ---------- Grok ----------
CORE_SYS = ("You drive Mirror Bot, a sandbox robot (x right, y down; N is y-1; left/right turn 90 degrees in place). "
            'Reply with exactly ONE JSON object and nothing else. Commands: {"cmd":"forward","n":1} (n integer 1-3) '
            '{"cmd":"left"} {"cmd":"right"} {"cmd":"stop"}.')
CORE_SYS_C = 'Drive a robot. x right,y down,N=y-1. Reply ONE JSON only: {"cmd":"left"|"right"|"stop"} or {"cmd":"forward","n":1-3}.'
CORE_GOAL = "Explore: visit new cells, then stop."
def request_body(messages):
    return chain("body", {"model": os.environ.get("MIRROR_MODEL", "grok-4"), "messages": messages})
def post(body):
    key = os.environ.get("XAI_API_KEY")
    if not key: raise RuntimeError("XAI_API_KEY not set")
    req = urllib.request.Request("https://api.x.ai/v1/chat/completions", json.dumps(body).encode(),
          {"Authorization": "Bearer " + key, "Content-Type": "application/json", "User-Agent": "mirror-bot/3.0"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())
def call_grok(messages, s=None, tag="propose"):
    r = post(request_body(messages))
    if s is not None: hook("usage", s, r.get("usage") or {}, tag)
    return r["choices"][0]["message"]["content"] or ""
def build_messages(s):
    c = compact()
    sysm = " ".join([CORE_SYS_C if c else CORE_SYS] + [x for x in hook("sys", s, c) if x])
    goal = os.environ.get("MIRROR_GOAL") or chain("goal", CORE_GOAL, s, c)
    st = cj(view(s)) if c else json.dumps(view(s))
    return [{"role": "system", "content": sysm}, {"role": "user", "content": goal + (" " if c else " Current state:\n") + st}]
def ask_grok(s):
    if "MIRROR_FAKE_REPLY" in os.environ: return os.environ["MIRROR_FAKE_REPLY"]  # test injection only
    return call_grok(build_messages(s), s, "propose")

# ---------- parsing ----------
def parse_prim(d):
    if not isinstance(d, dict) or d.get("cmd") not in PRIMS: return None, "not a primitive"
    if d["cmd"] == "forward":
        n = d.get("n")
        if set(d) != {"cmd", "n"} or type(n) is not int or not 1 <= n <= 3: return None, "forward needs integer n 1-3 and no other fields"
    elif set(d) != {"cmd"}: return None, "unexpected fields"
    return d, None
def parse(raw):
    """-> (command, None) | ({"new": name}, None) | (None, reason)"""
    try: d = json.loads(raw)
    except Exception: return None, "invalid JSON"
    if not isinstance(d, dict) or not isinstance(d.get("cmd"), str): return None, "not a command object"
    c = d["cmd"]
    if c in PRIMS: return parse_prim(d)
    if set(d) != {"cmd"}: return None, "unexpected fields"
    r = first("parse_name", c)
    return r if r is not None else (None, f"unknown command '{c}'")

# ---------- commands ----------
def cmd_propose(s):
    def out(raw, r, tag="propose"): dlog(tag, raw, r); return r
    if s["pending"]: return out(None, "refused: a command is already pending (approve or deny it)")
    try: raw = ask_grok(s)
    except Exception as e: return out(None, f"refused: API error: {e}")
    d, err = parse(raw.strip())
    if err: return out(raw, f"refused: {err}")
    if "new" in d:
        dlog("propose", raw, f"unknown command '{d['new']}': asking Grok to define it")
        return first("define", s, d["new"])
    errs = [e for e in hook("check", s, d) if e]
    if errs: return out(raw, f"refused: {errs[0]}")
    s["pending"] = d; return out(raw, f"pending {json.dumps(d)} (nothing executed)")
def run_prim(s, d):
    c = d["cmd"]
    if c == "forward":
        dx, dy = DV[s["heading"]]; moved = 0
        for _ in range(d["n"]):
            nx, ny = s["x"] + dx, s["y"] + dy
            if not inside(nx, ny): break  # silent stop; noted only in the local log
            s["x"], s["y"] = nx, ny; moved += 1
            if [nx, ny] not in s["visited"]: s["visited"].append([nx, ny])
        return f"forward {d['n']} -> ({s['x']},{s['y']}) {s['heading']}" + (f" [local: blocked, moved {moved}/{d['n']}]" if moved < d["n"] else "")
    if c in ("left", "right"):
        s["heading"] = HEADS[(HEADS.index(s["heading"]) + (1 if c == "right" else -1)) % 4]; return f"{c} -> heading {s['heading']}"
    return "stop: no action"
def cmd_approve(s):
    d = s["pending"]
    if not d: r = "refused: nothing pending"; dlog("approve", None, r); return r
    s["pending"] = None
    if "define" in d: r = first("approve_define", s, d) or "refused: functions layer disabled"
    else:
        before = (s["x"], s["y"], s["heading"])
        if d["cmd"] in PRIMS: r = run_prim(s, d)
        else: r = first("run", s, d) or f"refused: {d['cmd']} not available"
        hook("after_run", s, d, before)
        r += f" | visited {len(s['visited'])}"
    r = chain("result", r, s, d)
    dlog("approve", d, r)
    for extra in hook("after_approve", s): 
        if extra: r += " | " + extra
    return r
def cmd_deny(s):
    d = s["pending"]; s["pending"] = None
    r = f"denied {json.dumps(d)}" if d else "nothing pending"; dlog("deny", d, r); return r
def commands():
    c = {"propose": cmd_propose, "approve": cmd_approve, "deny": cmd_deny}
    for extra in hook("cli"): c.update(extra)
    return c
def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv; c = commands()
    if len(argv) != 1 or argv[0] not in c: print("usage: mirror.py " + "|".join(c) + "   (layers: " + (",".join(active()) or "none") + ")"); sys.exit(2)
    s = load(); print(c[argv[0]](s)); save(s)
