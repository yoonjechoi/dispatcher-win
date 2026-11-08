"""시험용 가짜 claude. 상태는 FAKE_CLAUDE_DIR/state.json 에 둔다.
--bg 는 신뢰(DISPATCHER_CLAUDE_JSON)를 보고 거절하거나 세션을 만든다. 받은 인자는 calls.jsonl 에 남긴다."""
import json
import os
import sys
import uuid

base = os.environ["FAKE_CLAUDE_DIR"]
state_path = os.path.join(base, "state.json")
args = sys.argv[1:]
with open(os.path.join(base, "calls.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": args, "cwd": os.getcwd(), "env_claude": sorted(k for k in os.environ if k.startswith("CLAUDE"))},
                       ensure_ascii=False) + "\n")


def load():
    try:
        with open(state_path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"sessions": []}


def save(state):
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def trusted(folder):
    try:
        with open(os.environ["DISPATCHER_CLAUDE_JSON"], encoding="utf-8") as f:
            projects = json.load(f).get("projects", {})
    except FileNotFoundError:
        projects = {}
    key = os.path.realpath(folder).replace("\\", "/")
    if projects.get(key, {}).get("hasTrustDialogAccepted"):
        return True
    if os.path.isdir(os.path.join(folder, ".git")):  # 저장소 폴더는 위 폴더의 신뢰를 이어받지 않는다.
        return False
    while True:
        parent = os.path.dirname(key)
        if parent == key:
            return False
        key = parent
        if projects.get(key, {}).get("hasTrustDialogAccepted"):
            return True


def value(flag):
    return args[args.index(flag) + 1] if flag in args else None


if args[:1] == ["--version"]:
    print("9.9.9 (Claude Code)")
elif args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": os.environ.get("FAKE_LOGGED_IN", "1") == "1"}))
elif args[:1] == ["agents"]:
    state = load()
    rows = [s for s in state["sessions"] if "--all" in args or s["status"] != "done"]
    print(json.dumps(rows))
elif args[:1] == ["stop"]:
    state = load()
    for s in state["sessions"]:
        if s["id"] == args[1]:
            s["status"] = s["state"] = "done"
    save(state)
    print(f"stopped {args[1]}")
elif "--bg" in args:
    if os.environ.get("FAKE_BG_FAIL"):
        print("boom", file=sys.stderr)
        sys.exit(1)
    if not trusted(os.getcwd()):
        print(f"Workspace not trusted. Run `claude` in {os.getcwd()} once and accept the trust prompt, then retry.",
              file=sys.stderr)
        sys.exit(1)
    state = load()
    resume = value("--resume")
    sid = str(uuid.uuid4()) if (not resume or os.environ.get("FAKE_RESUME_NEW_ID")) else resume
    bg = uuid.uuid4().hex[:8]
    state["sessions"] = [s for s in state["sessions"] if s["sessionId"] != sid]
    state["sessions"].append({"pid": 1, "id": bg, "cwd": os.getcwd(), "kind": "background", "startedAt": len(state["sessions"]),
                              "sessionId": sid, "name": value("-n"), "status": "idle", "state": "working"})
    save(state)
    print(f"backgrounded · {bg} · {value('-n')}")
else:
    # 대화형(start). 시험에서는 받은 인자만 남기고 끝난다.
    print("interactive")
