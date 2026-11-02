# Almeidafly: Mirror Bot, in layers

Mirror Bot is a stdlib-only Python sandbox robot that **only does what the xAI Grok API says**. Grok proposes one JSON command, and a human approves or denies it. There is no local heuristic and no autonomous loop.

The repo is built as **cumulative layers**. Each layer has its own commit, branch, tag, and GitHub release. Clone the repo and you can use all of the layers or none of them: check out an earlier tag, or turn layers off at runtime.

## Quick start
    git clone https://github.com/fitzyracing1/Almeidafly && cd Almeidafly
    export XAI_API_KEY=...            # env only; never commit it
    python3 mirror.py propose         # Grok proposes one command (nothing runs)
    python3 mirror.py approve         # run the pending command verbatim
    python3 mirror.py deny            # discard it
    python3 -m unittest discover -s tests -v    # offline tests, no network

Settings: `MIRROR_MODEL` (default `grok-4`) and `MIRROR_GOAL` (overrides the task text). Runtime files are `state.json`, `driver.log`, and `functions.json`. They are git-ignored.

## Turning layers on and off
`MIRROR_LAYERS` = `all` (the default), `none` (core only), or a comma list such as `functions,token_budget`. A layer whose file is missing at the checked-out tag is skipped. Every layer lives in its own module under `layers/` and plugs into the core (`bot.py`) through hooks. Running `python3 mirror.py` with no argument prints the active layers.

To use exactly the code of one layer, check out its tag: `git checkout v0.2.0` (or the branch `layer-2-hidden-grid-goal`).

## Layers
| Tag | Branch | Flag name | Adds |
|---|---|---|---|
| v0.0.0 | layer-0-core | (always on) | Grok-only driver, propose/approve/deny gate, strict allowlist (`forward` n 1-3, `left`, `right`, `stop`), refusals, `driver.log`, unbounded world |
| v0.1.0 | layer-1-functions | `functions` | Unknown command name → a second Grok call defines it from primitives (strictly validated: name regex, 1-8 steps, repeat 1-5, no other keys, no code). It becomes a pending new function and is saved to `functions.json` only on `approve` |
| v0.2.0 | layer-2-hidden-grid-goal | `hidden_grid` | Hidden 3x3 world (cells 0..2). `forward` stops there silently, and only the local log records `[local: blocked]`. The goal is to visit all 9 cells, and `goal reached` is logged once. No size, edge, or boundary word ever reaches Grok (tested) |
| v0.3.0 | layer-3-token-budget | `token_budget` | Reads `usage` from every Grok response and logs `cmd=tokens prompt= completion= total= running_total=`. Totals are kept in `state.tokens`. Every approve result shows `tokens <this item>/<total>`, and so does the goal line. Uses a compact prompt and compact JSON state (`{"p":[x,y],"h":..,"v":[..]}`), and sets `max_tokens` (`MIRROR_MAX_TOKENS`, default 1024). Grok is told that few tokens and reusable functions are part of the goal. `look` is human-only: `python3 mirror.py look` runs locally with no API call, and Grok proposing `look` is refused |
| v0.5.0 | layer-5-api-cost | `api_cost` | Cost optimizer for an API call you own, given as a JSON request file: `cost.py propose` measures the original with a real call, Grok proposes a cheaper equivalent (smaller prompt, cheaper model, lower `max_tokens`, cache-friendly order), and the candidate is measured with a real call. It is validated (allowed keys only, models from `/v1/models`, messages shape, same output shape). Nothing is saved until `cost.py approve`, which writes `<file>.optimized.json` and never modifies the original. First target is Mirror Bot's own call (`cost.py export-mirror`) |

`main` = layers 0-3 and 5. The stuck-fix layer (v0.4.0) was removed from main; its tag, branch and release stay for history only.

## Layer 5: API cost
    python3 cost.py export-mirror mirror_request.json     # Mirror Bot's own propose request
    MIRROR_LAYERS=functions,hidden_grid python3 cost.py export-mirror verbose.json   # same request without layer 3
    python3 cost.py propose mirror_request.json           # 3 real calls: before, optimizer, after
    python3 cost.py approve    # writes mirror_request.optimized.json
    python3 cost.py deny       # discard
    # use an approved request for Mirror Bot by setting MIRROR_MODEL / MIRROR_MAX_TOKENS to the values it chose
Settings: `MIRROR_COST_MODELS` (a comma list that overrides the live model list), `MIRROR_COST` (the pending-state file, `cost_state.json`).

## Safety
- Grok output is only data. There is no exec or eval. Functions can only call primitives, and they cannot call each other.
- Every action goes through `approve`. Refused output is never repaired or retried.
- `XAI_API_KEY` is read from the environment only. Runtime files are git-ignored.

## Hooks (for writing a layer)
A layer is a module in `layers/` and is listed in `bot.ALL_LAYERS`. It can define any of these optional hooks:
- `fresh(s)`
- `view(v, s)`
- `sys(s, compact)`
- `goal(g, s, compact)`
- `body(b)`
- `usage(s, u, tag)`
- `compact()`
- `inside(x, y)`
- `parse_name(c)`
- `define(s, name)`
- `approve_define(s, d)`
- `run(s, d)`
- `check(s, d)`
- `after_run(s, d, before)`
- `result(r, s, d)`
- `after_approve(s)`
- `goal_extra(s)`
- `cli()`

## Tests
`python3 -m unittest discover -s tests -v` runs offline and makes no network calls. Each layer has its own `tests/test_<layer>.py`. Each test file pins `MIRROR_LAYERS`, so it also covers that layer being on, off, and on alone.
