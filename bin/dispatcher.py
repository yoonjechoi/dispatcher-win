#!/usr/bin/env python3
"""Windows 용 세션 디스패처. 사용법은 dispatcher --help. Python 3.9 이상, 표준 라이브러리만 쓴다.

세션은 Claude Code 의 백그라운드 기능(claude --bg, claude agents, claude attach, claude stop)으로 띄우고 다룬다.
터미널 앱을 조종하지 않는다. 세션끼리의 말은 세션 안의 SendMessage 도구가 나른다.
시험을 위해 macOS · Linux 에서도 돈다(가짜 claude 로 단위 시험)."""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

try:
    import msvcrt  # Windows
except ImportError:  # 시험용(macOS · Linux)
    msvcrt = None
    import fcntl

for _stream in (sys.stdout, sys.stderr):
    try:  # Windows 콘솔 기본 코드 페이지로는 한글이 깨지거나 예외가 난다.
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

DEFAULT_NAME = "비서"
SESSION_RULE = ("너는 dispatcher 가 띄운 세션이다. 큰 작업이 끝나거나 사용자 판단이 필요하면 {assistant} 세션{target}에 "
                "SendMessage 로 보고해라. 이름이 바뀌어 안 닿으면 dispatcher list 의 상주 세션에서 찾아라. "
                "나눌 수 있는 일은 Agent 로 팀원을 띄워 동시에 진행하고, 팀원끼리는 SendMessage 로 주고받아라. "
                "작업은 지금 폴더에서 직접 한다. 지시가 없으면 git worktree 를 만들지 않는다.")
NOT_TRUSTED = "Workspace not trusted"
STARTING_SECONDS = 300
LABELS = {"busy": "작업 중", "working": "작업 중", "running": "작업 중", "idle": "쉬는 중", "waiting": "입력 대기",
          "blocked": "입력 대기", "unknown": "시작 중", "done": "끝남", "stopped": "멈춤", "failed": "실패"}
ENDED = ("done", "stopped", "failed", "killed")

RESIDENT, WORK = "상주", "작업"
RESIDENT_FIRST, RESIDENT_LAST, WORK_LAST, WORK_OVERFLOW = 100, 999, 99, 1000

EXIT_OK, EXIT_NONE, EXIT_MANY, EXIT_UNSUPPORTED, EXIT_NOT_IDLE, EXIT_TRUST, EXIT_CONFIRM, EXIT_FAILED, EXIT_SLOW = range(9)


def expand(path):
    return os.path.abspath(os.path.expanduser(path))


def data_dir():
    return expand(os.environ.get("DISPATCHER_HOME") or "~/.config/dispatcher")


def claude_json_path():
    return expand(os.environ.get("DISPATCHER_CLAUDE_JSON") or "~/.claude.json")


def claude_cmd():
    """claude 실행 명령(목록). 시험에서는 DISPATCHER_CLAUDE 로 가짜를 준다. .py 면 지금 파이썬으로 돌린다."""
    given = os.environ.get("DISPATCHER_CLAUDE")
    if given:
        return [sys.executable, given] if given.endswith(".py") else [given]
    found = shutil.which("claude")
    if found:
        return [found]
    for candidate in ("~/.local/bin/claude.exe", "~/.local/bin/claude"):
        if os.path.isfile(expand(candidate)):
            return [expand(candidate)]
    return ["claude"]


def clean_env():
    # 부르는 세션의 CLAUDE* 값이 남으면 새 세션이 자기를 그 세션의 자식으로 안다.
    return {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}


def run(args, timeout=60, **kw):
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                              stdin=subprocess.DEVNULL, **kw)
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(args, 127, "", str(error))


def claude(*args, timeout=60, **kw):
    return run([*claude_cmd(), *args], timeout=timeout, env=clean_env(), **kw)


# ---------- 데이터 ----------

class Store:
    """~/.config/dispatcher/ 의 json 을 잠금과 함께 읽고 쓴다."""

    def __enter__(self):
        os.makedirs(data_dir(), exist_ok=True)
        self.lock = open(os.path.join(data_dir(), ".lock"), "a+")
        if msvcrt:
            self.lock.seek(0)
            for _ in range(600):  # LK_LOCK 은 10초만 기다리고 실패한다. 직접 다시 시도한다.
                try:
                    msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                raise RuntimeError("명단 잠금을 못 얻음")
        else:
            fcntl.flock(self.lock, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        if msvcrt:
            self.lock.seek(0)
            msvcrt.locking(self.lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
        self.lock.close()

    @staticmethod
    def read(name, default):
        try:
            with open(os.path.join(data_dir(), name), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return default
        # 깨진 파일을 빈 값으로 덮어쓰면 고정 번호가 사라진다. 그대로 멈춘다.

    @staticmethod
    def write(name, data):
        write_json(os.path.join(data_dir(), name), data)


def write_json(path, data, indent=2):
    fd, temp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
        f.write("\n")
    for attempt in range(20):  # Windows 는 다른 프로세스가 연 파일을 바꾸지 못할 때가 있다.
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            time.sleep(0.1)
    os.unlink(temp)
    raise RuntimeError(f"파일을 못 씀: {path}")


def usable_root(path):
    # 홈이나 드라이브 맨 위를 기준으로 잡으면 그 아래가 모두 자동 신뢰된다. 추정으로는 이렇게 넓히지 않는다.
    if not path or path == expand("~") or os.path.dirname(path) == path:
        return None
    return path


def settle(saved):
    """저장한 값에서 나머지를 푼다. 사용자마다 폴더가 달라 코드에 경로를 두지 않는다."""
    home = saved.get("home_dir")
    root = saved.get("workspace_root") or (usable_root(os.path.dirname(expand(home))) if home else None)
    return {"auto_trust": "roots", "workspace_root": root, "trusted_roots": [root] if root else [],
            "assistant_role": DEFAULT_NAME, "owner_name": None, **saved,
            "assistant_name": saved.get("assistant_name") or DEFAULT_NAME,
            "home_dir": home or (os.path.join(root, "inbox") if root else None)}


def config():
    with Store() as store:
        saved = store.read("config.json", None)
        if saved is None:
            saved = {"auto_trust": "roots"}
            store.write("config.json", saved)
    return settle(saved)


def same_path(a, b):
    return bool(a and b) and os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def real_home(settings):
    return os.path.realpath(expand(settings["home_dir"])) if settings["home_dir"] else None


def learn_workspace_root(given):
    """처음 띄우는 폴더의 상위를 기준으로 저장한다. 저장했으면 그 경로."""
    with Store() as store:
        saved = store.read("config.json", None) or {}
        if settle(saved)["workspace_root"] or path_problem(given):
            return None
        root = usable_root(os.path.dirname(os.path.realpath(expand(given))))
        if root:
            store.write("config.json", {**saved, "workspace_root": root})
    return root


def empty_sessions():
    return {"next_no": 1, "next_resident": RESIDENT_FIRST, "sessions": {}}


def number_key(no):
    parts = str(no).split("-")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def display_name(no, name):
    return f"#{no} {name}".strip() if name else f"#{no}"


def plain_name(name):
    """명단에는 번호를 뺀 이름을 둔다. 표시 이름은 번호에서 다시 만든다."""
    found = re.fullmatch(r"#\d+(?:-\d+)?(?:\s+(.*))?", name or "", re.S)
    return (found.group(1) or None) if found else name


def next_number(book, band):
    """번호는 여기서만 준다. 카운터는 늘기만 하므로 지운 번호도 다시 나오지 않는다.
    작업 1~99, 상주 100~999. 작업이 99 를 넘으면 1000 부터 잇는다."""
    if band == RESIDENT:
        number = book["next_resident"]
        if number > RESIDENT_LAST:
            raise RuntimeError(f"상주 번호를 다 썼다: {RESIDENT_FIRST}~{RESIDENT_LAST}")
        book["next_resident"] = number + 1
        return number
    number = book["next_no"]
    if WORK_LAST < number < WORK_OVERFLOW:
        number = WORK_OVERFLOW
    book["next_no"] = number + 1
    return number


def load_book(store):
    return store.read("sessions.json", None) or empty_sessions()


def register(book, session_id, band=None, parent=None, **fields):
    entry = book["sessions"].get(session_id)
    if entry is None and parent:
        top = book["sessions"][parent]
        sub = top.get("next_sub", 1)
        top["next_sub"] = sub + 1
        entry = {"no": f"{top['no']}-{sub}", "session_id": session_id, "band": top.get("band", WORK), "parent": parent,
                 "sub": sub}
        book["sessions"][session_id] = entry
    elif entry is None:
        band = band or WORK
        entry = {"no": next_number(book, band), "session_id": session_id, "band": band}
        book["sessions"][session_id] = entry
    entry.update({k: v for k, v in fields.items() if v is not None})
    return entry


# ---------- 살아 있는 세션 ----------

def agents(include_done=False):
    result = claude("agents", "--json", *(["--all"] if include_done else []), timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"claude agents 실패: {(result.stderr or '').strip() or result.returncode}")
    merged = {}
    for item in json.loads(result.stdout or "[]"):
        sid = item.get("sessionId") or item.get("id")
        if sid not in merged or is_live(item):
            merged[sid] = item
    return merged


def raw_state(item):
    """백그라운드는 일을 마치면 state=done, status=idle 로 살아 있다. 프로세스 상태인 status 를 먼저 보고,
    state 가 blocked 이면(질문 · 승인 대기) 그쪽을 쓴다."""
    if item.get("state") == "blocked":
        return "blocked"
    return item.get("status") or item.get("state") or "unknown"


def is_live(item):
    return raw_state(item) not in ENDED


def band_of(item, home):
    return RESIDENT if same_path(item.get("cwd"), home) else WORK


def snapshot(include_done=False, save=True):
    """살아 있는 세션을 명단에 맞추고, 화면에 쓸 목록을 돌려준다."""
    live = agents(include_done)
    settings = config()
    home = real_home(settings)
    views = {}
    with Store() as store:
        book = load_book(store)
        for sid, item in sorted(live.items(), key=lambda kv: kv[1].get("startedAt") or 0):
            fields = dict(band=band_of(item, home), name=plain_name(item.get("name")), cwd=item.get("cwd"),
                          kind=item.get("kind"), bg_id=item.get("id"))
            if not is_live(item):
                if include_done:
                    register(book, sid, **fields)
                continue
            entry = register(book, sid, **fields, last_seen=int(time.time()))
            entry.pop("pending", None)
            state = raw_state(item)
            views[sid] = {**entry, "live": True, "live_name": item.get("name"), "pid": item.get("pid"),
                          "raw_state": state, "status": LABELS.get(state, state),
                          "location": "백그라운드" if item.get("kind") == "background" else "대화형"}
        for sid, entry in book["sessions"].items():
            if sid in views:
                continue
            item = live.get(sid, {})
            status = LABELS.get(raw_state(item), "꺼짐") if item else "꺼짐"
            if entry.get("pending") and time.time() - entry.get("launched_at", 0) < STARTING_SECONDS:
                status = "시작 중"
            views[sid] = {**entry, "live": False, "status": status, "raw_state": "off", "location": "-"}
        if save:
            store.write("sessions.json", book)
    for view in views.values():
        view["display"] = display_name(view["no"], view.get("name"))
    desk = desk_view(views.values(), home)
    if desk:
        desk["display"] = display_name(desk["no"], settings["assistant_name"])
    return sorted(views.values(), key=lambda v: number_key(v["no"]))


def resolve(query, views):
    if not query.strip():
        return []
    by_id = [v for v in views if query in (v["session_id"], v.get("bg_id"))]
    if by_id:
        return by_id
    if re.fullmatch(r"#?\d+(-\d+)?", query):
        return [v for v in views if str(v["no"]) == query.lstrip("#")]
    exact = [v for v in views if query in (v.get("name"), v.get("display"), v.get("live_name"))]
    if exact:
        return exact
    if len(query) >= 6:
        by_id = [v for v in views if v["session_id"].startswith(query)]
        if by_id:
            return by_id
    return [v for v in views if any(query in (v.get(k) or "") for k in ("name", "display", "live_name"))]


def describe(v):
    return f"{v['no']:>3} | {v.get('name') or '(이름 없음)'} | {v['location']} | {v['status']} | {v.get('cwd') or '-'}"


def pick(query, views):
    found = resolve(query, views)
    if not found:
        print("없음")
        sys.exit(EXIT_NONE)
    live = [v for v in found if v["live"]]
    if len(found) > 1 and len(live) == 1 and not re.fullmatch(r"#?\d+(-\d+)?", query):
        return live[0]  # 같은 이름이 끝난 기록과 떠 있는 세션에 함께 있으면 떠 있는 쪽이다.
    if len(found) > 1:
        print("여러 개:")
        print("\n".join(describe(v) for v in found))
        sys.exit(EXIT_MANY)
    return found[0]


def is_desk(entry, home):
    return entry.get("band") == RESIDENT and not entry.get("parent") and same_path(entry.get("cwd"), home)


def desk_view(views, home):
    desks = [v for v in views if is_desk(v, home)]
    desks.sort(key=lambda v: (bool(v["live"]), v.get("last_seen") or 0, number_key(v["no"])))
    return desks[-1] if desks else None


def desk_name(views):
    desk = desk_view(views, real_home(config()))
    return (desk.get("live_name") or desk["display"]) if desk else None


def session_rule(views):
    name = desk_name(views)
    return SESSION_RULE.format(assistant=config()["assistant_name"], target=f"(이름: {name})" if name else "")


def family_rule(views, top):
    relatives = [v for v in views if top in (v["session_id"], v.get("parent"))]
    parent = next(v for v in relatives if v["session_id"] == top)
    siblings = [f"{v['no']}번 {v.get('name') or '(이름 없음)'}" for v in relatives if v is not parent]
    return (f" 너의 부모 세션은 {parent['no']}번 {parent.get('name') or '(이름 없음)'} 이다. "
            f"같은 프로젝트의 형제 세션: {', '.join(siblings) or '아직 없음'}. "
            "이 목록은 띄울 때의 것이다. 최신 목록은 dispatcher list 로 확인해라. "
            f"끝났거나 사용자 판단이 필요하면 {config()['assistant_name']} 세션에 보고한다. "
            "형제 세션과 파일이나 작업이 겹치면 그 세션에 직접 SendMessage 로 조율한다.")


# ---------- 폴더 신뢰 ----------

def trust_key(folder):
    # Claude Code 는 Windows 경로를 C:/Users/... 처럼 / 로 적는다.
    return os.path.realpath(expand(folder)).replace("\\", "/")


def read_claude_json():
    try:
        with open(claude_json_path(), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def trusted(folder):
    """이 폴더나 위 폴더를 신뢰했는가. git 저장소 폴더는 위 폴더의 신뢰를 이어받지 못할 수 있어,
    최종 판정은 claude 의 거절 문구로 한다. 이 함수는 점검(doctor)에만 쓴다."""
    projects = {k.lower(): v for k, v in (read_claude_json().get("projects") or {}).items()}
    path = trust_key(folder)
    while True:
        if (projects.get(path.lower()) or {}).get("hasTrustDialogAccepted"):
            return path
        parent = os.path.dirname(path) if "/" in path else ""
        if not parent or parent == path:
            return None
        path = parent


def record_trust(folder):
    """~/.claude.json 에 폴더 신뢰를 적는다. 다른 Claude 프로세스도 이 파일을 쓰므로 쓴 뒤 다시 읽어 확인한다."""
    key = trust_key(folder)
    for attempt in range(5):
        data = read_claude_json()
        projects = data.setdefault("projects", {})
        if (projects.get(key) or {}).get("hasTrustDialogAccepted"):
            return True
        projects.setdefault(key, {})["hasTrustDialogAccepted"] = True
        write_json(claude_json_path(), data)
        time.sleep(0.3)
        if ((read_claude_json().get("projects") or {}).get(key) or {}).get("hasTrustDialogAccepted"):
            return True
    return False


def path_problem(given):
    if ".." in re.split(r"[\\/]", given):
        return "경로에 .. 가 있음"
    if any(part.startswith("~") for part in re.split(r"[\\/]", given)[1:]):
        return "경로에 ~ 로 시작하는 폴더 이름이 글자 그대로 있음"
    return None


def auto_trust_refusal(given):
    """자동 신뢰를 막는 사유. 괜찮으면 None. 설정이 이상하면 넓히지 않고 막는다."""
    problem = path_problem(given)
    if problem:
        return problem
    settings = config()
    if settings["auto_trust"] != "roots":
        return f"auto_trust 가 {settings['auto_trust']}"
    roots = settings["trusted_roots"]
    if not isinstance(roots, list):
        return "trusted_roots 가 목록이 아님"
    real = os.path.normcase(os.path.realpath(expand(given)))
    for root in roots:
        if isinstance(root, str) and root and not path_problem(root) and usable_root(os.path.realpath(expand(root))):
            if real.startswith(os.path.normcase(os.path.realpath(expand(root))) + os.sep):
                return None
    return "trusted_roots 밖의 폴더"


def log_trust(**fields):
    with Store():
        with open(os.path.join(data_dir(), "trust.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **fields}, ensure_ascii=False) + "\n")


# ---------- 명령 ----------

def cmd_list(args):
    views = snapshot(args.all)
    if args.json:
        print(json.dumps(views, ensure_ascii=False, indent=2))
        return EXIT_OK
    print("번호 | 세션 | 위치 | 상태 | 폴더")
    for band in (RESIDENT, WORK):
        rows = [v for v in views if v.get("band", WORK) == band]
        if rows:
            print(f"[{band}]")
            print("\n".join(("  └ " if v.get("parent") else "") + describe(v) for v in rows))
    return EXIT_OK


def bg_launch(folder, flags, prompt):
    """claude --bg 로 띄운다. (결과, 백그라운드 ID) 를 돌려준다."""
    result = claude("--bg", *flags, prompt, cwd=folder, timeout=120)
    text = (result.stdout or "") + (result.stderr or "")
    found = re.search(r"backgrounded\s*·\s*([0-9a-f]{6,})", text)
    return result, text, found.group(1) if found else None


def find_bg(bg_id, tries=15):
    for _ in range(tries):
        for sid, item in agents(True).items():
            if item.get("id") == bg_id:
                return sid
        time.sleep(1)
    return None


def cmd_launch(args):
    folder = expand(args.folder)
    if args.resume and args.in_:
        print("--resume 과 --in 은 함께 쓰지 않는다")
        return EXIT_FAILED
    views = snapshot()
    target = parent = None
    if args.resume:
        target = pick(args.resume, views)
        if target["live"]:
            print(f"이미 떠 있음: {describe(target)}. 떠 있는 세션에는 SendMessage 로 보낸다")
            return EXIT_NOT_IDLE
        if target.get("cwd") and not same_path(target["cwd"], folder):
            print(f"세션 기록은 원래 폴더에 있다: {target['cwd']}")
            folder = target["cwd"]
    if args.in_:
        parent = pick(args.in_, views)
        if parent.get("parent"):
            print("하위 세션 밑에는 띄우지 않는다. 부모 번호를 준다")
            return EXIT_FAILED
    if not os.path.isdir(folder):
        os.makedirs(folder)
        print(f"폴더를 만듦: {folder}")
    learned = learn_workspace_root(args.folder)
    if learned:
        print(f"workspace_root 를 정함: {learned}")

    with Store() as store:
        book = load_book(store)
        if target:
            entry = book["sessions"][target["session_id"]]
        else:
            # --bg 는 --session-id 를 따르지 않는다. 띄운 뒤 진짜 ID 로 옮긴다.
            placeholder = "pending-" + uuid.uuid4().hex
            entry = register(book, placeholder, band=WORK, parent=parent["session_id"] if parent else None,
                             name=args.name, cwd=folder, pending=True, launched_at=int(time.time()), launched=True,
                             role=args.role, work=args.work, group=args.group, started=time.strftime("%Y-%m-%d"))
        store.write("sessions.json", book)
    display = display_name(entry["no"], args.name)
    rule = session_rule(views) + (family_rule(views, parent["session_id"]) if parent else "")
    flags = ["-n", display, "--remote-control", display, "--dangerously-skip-permissions", "--append-system-prompt", rule]
    if target:
        flags += ["--resume", target["session_id"]]
    for extra in args.plugin_dir:
        flags += ["--plugin-dir", expand(extra)]

    result, text, bg_id = bg_launch(folder, flags, args.prompt)
    if not bg_id and NOT_TRUSTED in text:
        refusal = "--no-auto-trust" if not args.auto_trust else (
            "workspace_root 를 이번에 정한 첫 폴더" if learned else auto_trust_refusal(args.folder))
        if refusal:
            forget_pending(entry)
            print(f"폴더 신뢰가 필요해 띄우지 않음({refusal}): {os.path.realpath(folder)}")
            print("사용자에게 이 폴더를 신뢰할지 묻고, 허락받은 뒤에만 dispatcher trust \"<폴더>\" 를 실행하고 다시 띄운다")
            return EXIT_TRUST
        if not record_trust(folder):
            forget_pending(entry)
            print(f"폴더 신뢰를 적지 못함: {folder}")
            return EXIT_FAILED
        log_trust(folder=os.path.realpath(folder), no=entry["no"], name=args.name, how="auto")
        print(f"자동 신뢰함: {os.path.realpath(folder)}")
        result, text, bg_id = bg_launch(folder, flags, args.prompt)
    if not bg_id:
        forget_pending(entry)
        print(f"띄우지 못함: {text.strip()[:500] or result.returncode}")
        return EXIT_FAILED

    if target:
        session_id = follow_new_id(target["session_id"], bg_id)
        print(f"이어서 띄움: {display} | {folder} | 백그라운드 {bg_id}")
        return EXIT_OK if session_id else EXIT_SLOW
    session_id = find_bg(bg_id)
    with Store() as store:
        book = load_book(store)
        sessions = book["sessions"]
        if not target:
            old = next((k for k, v in sessions.items() if v is not None and v.get("no") == entry["no"]), None)
            moved = sessions.pop(old) if old else entry
            if session_id:
                if session_id in sessions:  # snapshot 이 먼저 새 번호로 잡았으면 그 번호를 버린다.
                    sessions.pop(session_id)
                moved["session_id"] = session_id
                moved.pop("pending", None)
            sessions[moved["session_id"] if session_id else old] = moved
            entry = moved
        entry["bg_id"] = bg_id
        store.write("sessions.json", book)
    where = f" (하위, 부모 {parent['no']}번)" if parent else ""
    print(f"띄움: {display}{where} | {folder} | 백그라운드 {bg_id}")
    if not session_id:
        print("세션 ID 를 아직 못 찾았다. 잠시 뒤 dispatcher list 로 확인한다")
        return EXIT_SLOW
    return EXIT_OK


def follow_new_id(old_sid, bg_id):
    """이어 띄운 세션이 새 세션 ID 를 받을 수 있다. 번호를 그대로 두고 명단의 열쇠만 옮긴다."""
    new_sid = find_bg(bg_id)
    with Store() as store:
        book = load_book(store)
        sessions = book["sessions"]
        entry = sessions[old_sid]
        entry["bg_id"] = bg_id
        if new_sid and new_sid != old_sid:
            sessions.pop(new_sid, None)
            del sessions[old_sid]
            entry.setdefault("old_session_ids", []).append(old_sid)
            entry["session_id"] = new_sid
            sessions[new_sid] = entry
            for child in sessions.values():
                if child.get("parent") == old_sid:
                    child["parent"] = new_sid
        store.write("sessions.json", book)
    return new_sid


def forget_pending(entry):
    with Store() as store:
        book = load_book(store)
        for key, value in list(book["sessions"].items()):
            if value.get("no") == entry["no"] and value.get("pending"):
                del book["sessions"][key]
        store.write("sessions.json", book)


def cmd_trust(args):
    """사용자가 허락한 폴더만 신뢰를 적는다."""
    problem = path_problem(args.folder)
    if problem:
        print(f"신뢰하지 않음: {problem}")
        return EXIT_TRUST
    folder = expand(args.folder)
    if not os.path.isdir(folder):
        print(f"폴더가 없음: {folder}")
        return EXIT_NONE
    if not record_trust(folder):
        print(f"폴더 신뢰를 적지 못함: {folder}")
        return EXIT_FAILED
    log_trust(folder=os.path.realpath(folder), how="user")
    print(f"신뢰함: {os.path.realpath(folder)}")
    return EXIT_OK


def cmd_send(args):
    """떠 있는 세션에는 SendMessage 를 쓴다. 꺼진 세션은 같은 대화를 백그라운드로 이어 메시지를 넣는다."""
    views = snapshot()
    target = pick(args.target, views)
    if target["live"]:
        print(f"떠 있는 세션이다. SendMessage 로 \"{target.get('live_name') or target['display']}\" 에 보낸다")
        return EXIT_UNSUPPORTED
    if not target.get("cwd") or not os.path.isdir(target["cwd"]):
        print(f"폴더가 없어 이을 수 없음: {target.get('cwd')}")
        return EXIT_FAILED
    flags = ["-n", target["display"], "--remote-control", target["display"], "--dangerously-skip-permissions",
             "--append-system-prompt", session_rule(views),
             "--resume", target["session_id"]]
    result, text, bg_id = bg_launch(target["cwd"], flags, args.message)
    if not bg_id:
        print(f"보내지 못함: {text.strip()[:500] or result.returncode}")
        return EXIT_FAILED
    follow_new_id(target["session_id"], bg_id)
    print(f"이어서 보냄: {target['display']} | 백그라운드 {bg_id}")
    return EXIT_OK


def cmd_show(args):
    target = pick(args.target, snapshot())
    if not target["live"] or not target.get("bg_id"):
        print(f"떠 있지 않음: {describe(target)}")
        return EXIT_NONE
    command = [*claude_cmd(), "attach", target["bg_id"]]
    wt = shutil.which("wt") or shutil.which("wt.exe")
    print(f"{target['display']} | {target.get('cwd')}")
    if args.print_only or not wt:
        print("열기: " + subprocess.list2cmdline(command))
        return EXIT_OK if args.print_only else EXIT_UNSUPPORTED
    result = run([wt, "-w", "0", "new-tab", "--title", target["display"], "-d", target.get("cwd") or ".", *command],
                 timeout=20)
    if result.returncode != 0:
        print(f"Windows Terminal 을 못 열었다({(result.stderr or '').strip() or result.returncode}). 열기: "
              + subprocess.list2cmdline(command))
        return EXIT_UNSUPPORTED
    print("Windows Terminal 새 탭에 열었다")
    return EXIT_OK


def confirm_word(target):
    return target.get("name") or str(target["no"])


def cmd_stop(args):
    views = snapshot()
    target = pick(args.target, views)
    label = f"{target['no']}번 {target.get('name') or '(이름 없음)'}"
    if not target["live"]:
        print(f"이미 꺼져 있음: {label}")
        return EXIT_NONE
    if target.get("kind") != "background":
        print(f"대화형 세션은 그 터미널에서 닫는다: {label}")
        return EXIT_UNSUPPORTED
    if args.confirm not in (confirm_word(target), target["display"]):
        print(f"확인 필요: {describe(target)}")
        print(f'끄려면: dispatcher stop {target["no"]} --confirm "{confirm_word(target)}"')
        return EXIT_CONFIRM
    result = claude("stop", target["bg_id"], timeout=60)
    if result.returncode != 0:
        print(f"끄지 못함: {(result.stderr or result.stdout or '').strip() or result.returncode}")
        return EXIT_FAILED
    print(f"끔: {label}")
    return EXIT_OK


def cmd_set(args):
    views = snapshot()
    target = pick(args.target, views)
    with Store() as store:
        book = load_book(store)
        entry = book["sessions"][target["session_id"]]
        for key in ("group", "role", "work"):
            value = getattr(args, key)
            if value is not None:
                entry[key] = value or None
        store.write("sessions.json", book)
    print(f"바꿈: {target['display']} | 묶음 {entry.get('group') or '-'} | 역할 {entry.get('role') or '-'}")
    return EXIT_OK


HOME_NOTE = """# {name}

나는 {owner}님의 {role} {name}이다. 일을 직접 하지 않고, 프로젝트별 세션에 나눠 맡기고 결과를 모아 보고한다. 일을 받으면 `dispatcher-win:dispatch` 스킬을 따른다.

세션 명단과 설정은 `~/.config/dispatcher/` 에 있다. 명령은 `dispatcher` 이다.
"""


def stdin_is_terminal():
    """Windows 는 NUL 장치도 isatty() 가 참이다. 콘솔 모드를 읽어 진짜 콘솔인지 본다."""
    if not sys.stdin or not sys.stdin.isatty():
        return False
    if msvcrt is None:
        return True
    import ctypes
    mode = ctypes.c_uint32()
    try:
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    except OSError:
        return False
    return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))


def ask(label, given, default):
    if given:
        return given
    if not stdin_is_terminal():
        return default
    return input(f"{label}" + (f" [{default}]" if default else "") + ": ").strip() or default


def cmd_init(args):
    """있는 값과 파일은 덮어쓰지 않는다."""
    with Store() as store:
        saved = store.read("config.json", None) or {}
    current = settle(saved)
    try:
        values = {"assistant_name": ask("이름", args.name, current["assistant_name"]),
                  "assistant_role": ask("역할", args.role, current["assistant_role"]),
                  "owner_name": ask("사용자 이름", args.owner, current["owner_name"]),
                  "home_dir": ask("작업 폴더", args.home, current["home_dir"])}
    except (EOFError, KeyboardInterrupt):
        print("\n그만둠")
        return EXIT_FAILED
    if not values["home_dir"]:
        print("작업 폴더가 필요하다: dispatcher init --home <폴더>")
        return EXIT_FAILED
    values["home_dir"] = expand(values["home_dir"])
    with Store() as store:
        saved = store.read("config.json", None) or {}
        merged = {"auto_trust": "roots", **{k: v for k, v in values.items() if v}, **saved}
        store.write("config.json", merged)
    for key, value in values.items():
        kept = settle(merged)[key]
        if value and (not same_path(kept, value) if key == "home_dir" else kept != value):
            print(f"있는 값을 유지함: {key} = {kept} (입력한 값은 안 씀: {value})")
    final = settle(merged)
    home = expand(final["home_dir"])
    os.makedirs(home, exist_ok=True)
    note = os.path.join(home, "CLAUDE.md")
    try:
        with open(note, "x", encoding="utf-8") as f:
            f.write(HOME_NOTE.format(name=final["assistant_name"], role=final["assistant_role"],
                                     owner=final["owner_name"] or "사용자"))
        print(f"만듦: {note}")
    except FileExistsError:
        print(f"있는 파일을 유지함: {note}")
    print(f"설정: {os.path.join(data_dir(), 'config.json')}")
    print(f"작업 폴더 기준(workspace_root): {final['workspace_root'] or '첫 launch 때 정함'}")
    print("다음: 터미널(PowerShell)에서 dispatcher start")
    return EXIT_OK


def start_flags(display, session_id, resume):
    flags = ["--dangerously-skip-permissions", "--remote-control", display, "-n", display]
    return flags + (["-c"] if resume else ["--session-id", session_id])


def cmd_start(args):
    """비서 세션을 이 터미널에서 시작한다. 끝날 때까지 돌아오지 않는다."""
    settings = config()
    home = expand(settings["home_dir"]) if settings["home_dir"] else None
    if not home or not os.path.isdir(home):
        print(f"홈 폴더가 없다({home or '설정 없음'}). dispatcher init 을 먼저 한다")
        return EXIT_FAILED
    if os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDECODE"):
        print("Claude 세션 안에서는 시작하지 않는다. 터미널(PowerShell)에서 직접 실행한다")
        return EXIT_UNSUPPORTED
    if not stdin_is_terminal() and not os.environ.get("DISPATCHER_START_ANYWAY"):
        print("터미널에서 직접 실행한다(입력이 터미널이 아님)")
        return EXIT_UNSUPPORTED
    views = snapshot()
    desk = desk_view(views, real_home(settings))
    if desk and desk["live"]:
        print(f"이미 떠 있음: {describe(desk)}")
        return EXIT_FAILED
    session_id = str(uuid.uuid4())
    if args.resume:
        display = desk["display"] if desk else settings["assistant_name"]
    else:
        with Store() as store:
            book = load_book(store)
            entry = register(book, session_id, band=RESIDENT, name=settings["assistant_name"], cwd=home,
                             started=time.strftime("%Y-%m-%d"), pending=True, launched_at=int(time.time()))
            store.write("sessions.json", book)
        display = display_name(entry["no"], settings["assistant_name"])
    print(f"시작: {display} | {home}")
    sys.stdout.flush()
    # Windows 의 execv 는 새 프로세스를 띄우고 바로 돌아와 터미널 입력이 둘로 갈린다. 자식으로 돌리고 기다린다.
    try:
        return subprocess.call([*claude_cmd(), *start_flags(display, session_id, args.resume)], cwd=home, env=clean_env())
    except KeyboardInterrupt:
        return EXIT_OK


def cmd_doctor(args):
    problems = 0

    def line(ok, label, detail=""):
        nonlocal problems
        problems += 0 if ok else 1
        print(f"[{'정상' if ok else '실패'}] {label}" + (f": {detail}" if detail else ""))

    line(sys.version_info >= (3, 9), "Python", sys.version.split()[0])
    line(os.name == "nt", "운영체제", sys.platform + ("" if os.name == "nt" else " (Windows 전용판이다)"))
    version = claude("--version", timeout=30)
    line(version.returncode == 0, "claude", (version.stdout or version.stderr or "").strip()[:80])
    status = claude("auth", "status", timeout=30)
    try:
        logged = json.loads(status.stdout or "{}").get("loggedIn")
    except ValueError:
        logged = False
    line(bool(logged), "Claude 로그인", "됨" if logged else "안 됨: 터미널에서 claude 를 실행해 로그인한다")
    listing = claude("agents", "--json", timeout=60)
    line(listing.returncode == 0, "claude agents", "" if listing.returncode == 0 else (listing.stderr or "").strip()[:200])
    wt = shutil.which("wt") or shutil.which("wt.exe")
    print(f"[{'정상' if wt else '참고'}] Windows Terminal: {wt or '없음 (show 는 열 명령만 알려 준다)'}")
    settings = config()
    line(bool(settings["home_dir"]) and os.path.isdir(expand(settings["home_dir"])), "홈 폴더",
         settings["home_dir"] or "설정 없음: dispatcher init")
    root = settings["workspace_root"]
    print(f"[참고] workspace_root: {root or '없음 (첫 launch 때 정함)'}")
    print(f"[참고] auto_trust: {settings['auto_trust']} | trusted_roots: {settings['trusted_roots']}")
    if root:
        found = trusted(root)
        print(f"[{'정상' if found else '참고'}] workspace_root 신뢰: "
              + (f"됨({found})" if found else "안 됨. 처음 띄울 때 사용자에게 묻는다"))
    print(f"[참고] 데이터: {data_dir()}")
    return EXIT_OK if not problems else EXIT_FAILED


SHIM_MARK = "rem dispatcher-win shim"
SHIM = """@echo off
{mark}: runs the newest installed dispatcher-win.
setlocal
set "base=%USERPROFILE%\\.claude\\plugins\\cache\\dispatcher-win\\dispatcher-win"
for /f "delims=" %%v in ('dir /b /ad /o-d "%base%" 2^>nul') do (
  set "ver=%%v"
  goto found
)
echo dispatcher-win plugin is not installed. 1>&2
exit /b 7
:found
call "%base%\\%ver%\\bin\\dispatcher.cmd" %*
exit /b %ERRORLEVEL%
"""


def cmd_setup_shell(args):
    """터미널에서 dispatcher 를 바로 부르게 ~/.local/bin/dispatcher.cmd 를 둔다. 남이 만든 같은 이름 파일은 덮어쓰지 않는다."""
    folder = expand(args.dir or "~/.local/bin")
    path = os.path.join(folder, "dispatcher.cmd")
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            if SHIM_MARK not in f.read():
                print(f"다른 파일이 있어 덮어쓰지 않음: {path}")
                return EXIT_FAILED
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="ascii", newline="\r\n") as f:
        f.write(SHIM.format(mark=SHIM_MARK))
    on_path = any(same_path(p, folder) for p in os.environ.get("PATH", "").split(os.pathsep) if p)
    print(f"만듦: {path}")
    print("PATH 에 있음" if on_path else f"PATH 에 없음: {folder} 를 사용자 PATH 에 더해야 한다")
    return EXIT_OK


def cmd_home_context(args):
    """SessionStart 훅. 홈 폴더가 아니면 아무것도 하지 않는다. 실패해도 세션을 막지 않는다."""
    try:
        settings = config()
        here = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        if settings["home_dir"] and same_path(here, expand(settings["home_dir"])):
            print("이 폴더는 작업 접수 창구다. 일을 받으면 dispatcher-win:dispatch 스킬을 따른다.")
    except Exception:  # noqa: BLE001 - 훅은 어떤 경우에도 세션을 막지 않는다.
        pass
    return EXIT_OK


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: 오류: {message}", file=sys.stderr)
        sys.exit(EXIT_FAILED)


def parser():
    p = Parser(prog="dispatcher", description="프로젝트별 Claude Code 세션을 번호로 관리한다 (Windows 판)")
    sub = p.add_subparsers(dest="command", required=True, parser_class=Parser)

    s = sub.add_parser("list", help="세션 목록")
    s.add_argument("-a", "--all", action="store_true", help="끝난 백그라운드 세션도 명단에 넣는다")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("launch", help="새 세션을 백그라운드로 띄운다")
    s.add_argument("name")
    s.add_argument("folder")
    s.add_argument("prompt")
    s.add_argument("--work", help="하는 일")
    s.add_argument("--role")
    s.add_argument("--group")
    s.add_argument("--resume", metavar="대상", help="꺼진 세션의 대화를 잇는다")
    s.add_argument("--in", dest="in_", metavar="대상", help="이 세션의 하위 번호(7-1)로 띄운다")
    s.add_argument("--no-auto-trust", dest="auto_trust", action="store_false")
    s.add_argument("--plugin-dir", action="append", default=[])
    s.set_defaults(func=cmd_launch)

    s = sub.add_parser("trust", help="사용자가 허락한 폴더를 신뢰한다")
    s.add_argument("folder")
    s.set_defaults(func=cmd_trust)

    s = sub.add_parser("send", help="꺼진 세션에 메시지를 넣어 잇는다(떠 있으면 SendMessage)")
    s.add_argument("target")
    s.add_argument("message")
    s.set_defaults(func=cmd_send)

    s = sub.add_parser("show", help="세션을 Windows Terminal 새 탭에 연다")
    s.add_argument("target")
    s.add_argument("--print", dest="print_only", action="store_true", help="열지 않고 명령만 알려 준다")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("stop", help="백그라운드 세션을 끈다")
    s.add_argument("target")
    s.add_argument("--confirm", help="세션 이름(이름이 없으면 번호)")
    s.set_defaults(func=cmd_stop)

    s = sub.add_parser("set", help="묶음 · 역할을 고친다")
    s.add_argument("target")
    s.add_argument("--group")
    s.add_argument("--role")
    s.add_argument("--work")
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("init", help="비서 이름 · 홈 폴더를 정한다")
    s.add_argument("--name")
    s.add_argument("--role")
    s.add_argument("--owner")
    s.add_argument("--home")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("start", help="비서 세션을 이 터미널에서 시작한다")
    s.add_argument("-c", "--continue", dest="resume", action="store_true", help="지난 대화를 잇는다")
    s.set_defaults(func=cmd_start)

    s = sub.add_parser("doctor", help="점검")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("setup-shell", help="터미널용 dispatcher.cmd 를 ~/.local/bin 에 둔다")
    s.add_argument("--dir", help="둘 폴더(기본 ~/.local/bin)")
    s.set_defaults(func=cmd_setup_shell)

    s = sub.add_parser("home-context", help=argparse.SUPPRESS)
    s.set_defaults(func=cmd_home_context)
    return p


def main():
    args = parser().parse_args()
    try:
        code = args.func(args)
    except RuntimeError as error:
        print(f"실패: {error}")
        code = EXIT_FAILED
    sys.exit(code or EXIT_OK)


if __name__ == "__main__":
    main()
