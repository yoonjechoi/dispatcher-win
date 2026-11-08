"""가짜 claude 로 도는 단위 시험. python tests/test_dispatcher.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROGRAM = os.path.join(ROOT, "bin", "dispatcher.py")
FAKE = os.path.join(ROOT, "tests", "fake_claude.py")


class DispatcherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.home = os.path.join(self.tmp, "data")
        self.fake = os.path.join(self.tmp, "fake")
        self.work = os.path.join(self.tmp, "work")
        self.desk = os.path.join(self.work, "desk")
        os.makedirs(self.fake)
        os.makedirs(self.desk)
        self.claude_json = os.path.join(self.tmp, "claude.json")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "DISPATCHER", "FAKE"))}
        self.env.update(DISPATCHER_HOME=self.home, DISPATCHER_CLAUDE=FAKE, DISPATCHER_CLAUDE_JSON=self.claude_json,
                        FAKE_CLAUDE_DIR=self.fake, PYTHONIOENCODING="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cmd(self, *args, env=None, **kw):
        return subprocess.run([sys.executable, PROGRAM, *args], capture_output=True, text=True, encoding="utf-8",
                              env={**self.env, **(env or {})}, **kw)

    def write_config(self, **values):
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "config.json"), "w", encoding="utf-8") as f:
            json.dump(values, f, ensure_ascii=False)

    def trust(self, folder):
        try:
            with open(self.claude_json, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            data = {}
        data.setdefault("projects", {})[os.path.realpath(folder).replace("\\", "/")] = {"hasTrustDialogAccepted": True}
        with open(self.claude_json, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def book(self):
        with open(os.path.join(self.home, "sessions.json"), encoding="utf-8") as f:
            return json.load(f)

    def calls(self):
        try:
            with open(os.path.join(self.fake, "calls.jsonl"), encoding="utf-8") as f:
                return [json.loads(line) for line in f]
        except FileNotFoundError:
            return []

    def bg_calls(self):
        return [c for c in self.calls() if "--bg" in c["args"]]

    def ready(self):
        """비서 설정과 신뢰된 작업 폴더."""
        self.write_config(assistant_name="미소", home_dir=self.desk, workspace_root=self.work)
        self.trust(self.work)

    def launch(self, name, folder, prompt="일해", *extra, env=None):
        return self.run_cmd("launch", name, folder, prompt, *extra, env=env)

    # ---------- launch ----------

    def test_launch_numbers_and_registers_real_session_id(self):
        self.ready()
        a = self.launch("알파", os.path.join(self.work, "alpha"))
        b = self.launch("베타", os.path.join(self.work, "beta"))
        self.assertEqual((a.returncode, b.returncode), (0, 0), a.stdout + b.stdout)
        sessions = self.book()["sessions"]
        names = {e["no"]: e["name"] for e in sessions.values()}
        self.assertEqual(names, {1: "알파", 2: "베타"})
        for sid, entry in sessions.items():
            self.assertFalse(sid.startswith("pending-"))
            self.assertEqual(entry["session_id"], sid)
            self.assertNotIn("pending", entry)
            self.assertTrue(entry["bg_id"])
        args = self.bg_calls()[0]["args"]
        self.assertEqual(args[args.index("-n") + 1], "#1 알파")
        self.assertEqual(args[args.index("--remote-control") + 1], "#1 알파")
        self.assertIn("--dangerously-skip-permissions", args)
        self.assertEqual(args[-1], "일해")

    def test_launch_rule_names_assistant_and_strips_claude_env(self):
        self.ready()
        self.launch("알파", os.path.join(self.work, "alpha"), env={"CLAUDE_CODE_SESSION_ID": "caller", "CLAUDECODE": "1"})
        call = self.bg_calls()[0]
        rule = call["args"][call["args"].index("--append-system-prompt") + 1]
        self.assertIn("미소 세션", rule)
        self.assertIn("worktree 를 만들지 않는다", rule)
        self.assertEqual(call["env_claude"], [])

    def test_launch_creates_missing_folder(self):
        self.ready()
        folder = os.path.join(self.work, "new-one")
        result = self.launch("새것", folder)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(os.path.isdir(folder))
        self.assertEqual(os.path.realpath(self.bg_calls()[0]["cwd"]), os.path.realpath(folder))

    def test_untrusted_repo_inside_roots_is_auto_trusted_and_logged(self):
        self.ready()
        repo = os.path.join(self.work, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        result = self.launch("저장소", repo)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("자동 신뢰함", result.stdout)
        self.assertEqual(len(self.bg_calls()), 2)
        with open(self.claude_json, encoding="utf-8") as f:
            self.assertTrue(json.load(f)["projects"][os.path.realpath(repo).replace("\\", "/")]["hasTrustDialogAccepted"])
        with open(os.path.join(self.home, "trust.log"), encoding="utf-8") as f:
            self.assertIn('"how": "auto"', f.read())

    def test_folder_outside_roots_stops_with_code_5_and_leaves_no_entry(self):
        self.ready()
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        result = self.launch("밖", outside)
        self.assertEqual(result.returncode, 5, result.stdout)
        self.assertIn("trusted_roots 밖", result.stdout)
        self.assertEqual(self.book()["sessions"], {})
        self.assertFalse(os.path.exists(os.path.join(self.home, "trust.log")))

    def test_no_auto_trust_flag_and_off_setting(self):
        self.ready()
        repo = os.path.join(self.work, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        self.assertEqual(self.launch("저장소", repo, "일해", "--no-auto-trust").returncode, 5)
        self.write_config(assistant_name="미소", home_dir=self.desk, workspace_root=self.work, auto_trust="off")
        result = self.launch("저장소", repo)
        self.assertEqual(result.returncode, 5)
        self.assertIn("auto_trust 가 off", result.stdout)

    def test_first_folder_learns_root_but_is_not_auto_trusted(self):
        self.write_config(assistant_name="미소")
        first = os.path.join(self.work, "first")
        result = self.launch("처음", first)
        self.assertEqual(result.returncode, 5, result.stdout)
        self.assertIn("workspace_root 를 정함", result.stdout)
        with open(os.path.join(self.home, "config.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["workspace_root"], os.path.realpath(self.work))

    def test_dotdot_path_is_never_auto_trusted(self):
        self.ready()
        repo = os.path.join(self.work, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        result = self.launch("저장소", os.path.join(self.work, "x", "..", "repo"))
        self.assertEqual(result.returncode, 5)
        self.assertIn("..", result.stdout)

    def test_launch_failure_removes_placeholder(self):
        self.ready()
        result = self.launch("실패", os.path.join(self.work, "f"), env={"FAKE_BG_FAIL": "1"})
        self.assertEqual(result.returncode, 7)
        self.assertEqual(self.book()["sessions"], {})
        self.assertEqual(self.launch("다음", os.path.join(self.work, "g")).returncode, 0)
        self.assertEqual([e["no"] for e in self.book()["sessions"].values()], [2])  # 번호는 재사용하지 않는다.

    def test_in_gives_sub_number_and_family_rule(self):
        self.ready()
        self.launch("본체", os.path.join(self.work, "main"))
        result = self.launch("시험", os.path.join(self.work, "main-test"), "일해", "--in", "1")
        self.assertEqual(result.returncode, 0, result.stdout)
        entries = sorted(self.book()["sessions"].values(), key=lambda e: str(e["no"]))
        self.assertEqual([e["no"] for e in entries], [1, "1-1"])
        args = self.bg_calls()[-1]["args"]
        self.assertEqual(args[args.index("-n") + 1], "#1-1 시험")
        self.assertIn("부모 세션은 1번 본체", args[args.index("--append-system-prompt") + 1])

    def test_resume_live_session_is_refused(self):
        self.ready()
        self.launch("알파", os.path.join(self.work, "alpha"))
        result = self.launch("알파", os.path.join(self.work, "alpha"), "더 해", "--resume", "1")
        self.assertEqual(result.returncode, 4)
        self.assertEqual(len(self.bg_calls()), 1)

    # ---------- list · stop · send ----------

    def test_list_shows_bands_and_desk_display(self):
        self.ready()
        self.launch("알파", os.path.join(self.work, "alpha"))
        result = self.run_cmd("list")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[작업]", result.stdout)
        self.assertIn("알파", result.stdout)
        self.assertIn("백그라운드", result.stdout)
        views = json.loads(self.run_cmd("list", "--json").stdout)
        self.assertEqual(views[0]["display"], "#1 알파")

    def test_unknown_live_session_gets_number_and_desk_is_resident(self):
        self.ready()
        state = {"sessions": [{"pid": 1, "id": "aaaa1111", "cwd": self.desk, "kind": "interactive", "startedAt": 1,
                               "sessionId": "desk-sid", "name": "아무 이름", "status": "idle", "state": "idle"}]}
        with open(os.path.join(self.fake, "state.json"), "w", encoding="utf-8") as f:
            json.dump(state, f)
        views = json.loads(self.run_cmd("list", "--json").stdout)
        self.assertEqual((views[0]["no"], views[0]["band"], views[0]["display"]), (100, "상주", "#100 미소"))

    def test_blocked_state_shows_waiting(self):
        self.ready()
        self.launch("알파", os.path.join(self.work, "alpha"))
        path = os.path.join(self.fake, "state.json")
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        state["sessions"][0]["state"] = "blocked"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)
        self.assertIn("입력 대기", self.run_cmd("list").stdout)

    def test_stop_needs_confirm_then_stops(self):
        self.ready()
        self.launch("알파", os.path.join(self.work, "alpha"))
        self.assertEqual(self.run_cmd("stop", "1").returncode, 6)
        self.assertEqual(self.run_cmd("stop", "1", "--confirm", "베타").returncode, 6)
        result = self.run_cmd("stop", "1", "--confirm", "알파")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(any(c["args"][:1] == ["stop"] for c in self.calls()))
        self.assertIn("꺼짐", self.run_cmd("list").stdout)

    def test_send_live_points_to_sendmessage_and_off_resumes(self):
        self.ready()
        self.launch("알파", os.path.join(self.work, "alpha"))
        live = self.run_cmd("send", "1", "안녕")
        self.assertEqual(live.returncode, 3)
        self.assertIn("SendMessage", live.stdout)
        self.run_cmd("stop", "1", "--confirm", "알파")
        sid = next(iter(self.book()["sessions"]))
        result = self.run_cmd("send", "1", "이어서 해")
        self.assertEqual(result.returncode, 0, result.stdout)
        args = self.bg_calls()[-1]["args"]
        self.assertEqual(args[args.index("--resume") + 1], sid)
        self.assertEqual(args[-1], "이어서 해")
        self.assertEqual(list(self.book()["sessions"]), [sid])

    def test_send_keeps_number_when_resume_gets_new_id(self):
        self.ready()
        self.launch("알파", os.path.join(self.work, "alpha"))
        self.run_cmd("stop", "1", "--confirm", "알파")
        old = next(iter(self.book()["sessions"]))
        result = self.run_cmd("send", "1", "이어서", env={"FAKE_RESUME_NEW_ID": "1"})
        self.assertEqual(result.returncode, 0, result.stdout)
        sessions = self.book()["sessions"]
        self.assertEqual(len(sessions), 1)
        (sid, entry), = sessions.items()
        self.assertNotEqual(sid, old)
        self.assertEqual((entry["no"], entry["old_session_ids"]), (1, [old]))
        views = json.loads(self.run_cmd("list", "--json").stdout)
        self.assertEqual([(v["no"], v["live"]) for v in views], [(1, True)])

    def test_show_print_only(self):
        self.ready()
        self.launch("알파", os.path.join(self.work, "alpha"))
        bg = next(iter(self.book()["sessions"].values()))["bg_id"]
        result = self.run_cmd("show", "알파", "--print")
        self.assertEqual(result.returncode, 0)
        self.assertIn(f"attach {bg}", result.stdout)

    def test_target_not_found_and_many(self):
        self.ready()
        self.launch("알파 하나", os.path.join(self.work, "a1"))
        self.launch("알파 둘", os.path.join(self.work, "a2"))
        self.assertEqual(self.run_cmd("stop", "없는것").returncode, 1)
        self.assertEqual(self.run_cmd("stop", "알파").returncode, 2)

    def test_name_prefers_the_only_live_match(self):
        self.ready()
        self.launch("알파", os.path.join(self.work, "alpha"))
        self.run_cmd("stop", "1", "--confirm", "알파")
        self.run_cmd("list", "--all")
        self.launch("알파", os.path.join(self.work, "alpha2"))
        result = self.run_cmd("show", "알파", "--print")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("#2 알파", result.stdout)

    def test_launch_does_not_import_old_ended_sessions(self):
        self.ready()
        state = {"sessions": [{"pid": 1, "id": "old11111", "cwd": self.work, "kind": "background", "startedAt": 1,
                               "sessionId": "old-sid", "name": "옛것", "status": "done", "state": "done"}]}
        with open(os.path.join(self.fake, "state.json"), "w", encoding="utf-8") as f:
            json.dump(state, f)
        self.launch("새것", os.path.join(self.work, "new"))
        self.assertEqual([e["name"] for e in self.book()["sessions"].values()], ["새것"])
        self.assertEqual(next(iter(self.book()["sessions"].values()))["no"], 1)

    def test_set_group_and_role(self):
        self.ready()
        self.launch("알파", os.path.join(self.work, "alpha"))
        self.assertEqual(self.run_cmd("set", "1", "--group", "묶음", "--role", "개발").returncode, 0)
        entry = next(iter(self.book()["sessions"].values()))
        self.assertEqual((entry["group"], entry["role"]), ("묶음", "개발"))

    # ---------- trust · init · start · doctor · hook ----------

    def test_trust_command(self):
        self.ready()
        folder = os.path.join(self.tmp, "elsewhere")
        os.makedirs(folder)
        self.assertEqual(self.run_cmd("trust", folder).returncode, 0)
        with open(self.claude_json, encoding="utf-8") as f:
            self.assertIn(os.path.realpath(folder).replace("\\", "/"), json.load(f)["projects"])
        self.assertEqual(self.run_cmd("trust", os.path.join(folder, "..", "x")).returncode, 5)

    def test_init_writes_once_and_keeps_existing(self):
        home = os.path.join(self.tmp, "desk2")
        result = self.run_cmd("init", "--name", "미소", "--role", "비서", "--owner", "민준", "--home", home)
        self.assertEqual(result.returncode, 0, result.stdout)
        with open(os.path.join(home, "CLAUDE.md"), encoding="utf-8") as f:
            self.assertIn("민준님의 비서 미소", f.read())
        again = self.run_cmd("init", "--name", "다른이름", "--home", home)
        self.assertIn("있는 값을 유지함: assistant_name = 미소", again.stdout)
        self.assertIn("있는 파일을 유지함", again.stdout)

    def test_start_refuses_inside_claude_and_without_terminal(self):
        self.ready()
        inside = self.run_cmd("start", env={"CLAUDE_CODE_SESSION_ID": "x"})
        self.assertEqual(inside.returncode, 3)
        self.assertEqual(self.run_cmd("start", stdin=subprocess.DEVNULL).returncode, 3)

    def test_start_registers_resident_and_passes_flags(self):
        self.ready()
        result = self.run_cmd("start", env={"DISPATCHER_START_ANYWAY": "1"}, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stdout)
        entry = next(iter(self.book()["sessions"].values()))
        self.assertEqual((entry["no"], entry["band"], entry["name"]), (100, "상주", "미소"))
        call = [c for c in self.calls() if "--session-id" in c["args"]][0]
        self.assertEqual(call["args"][call["args"].index("--remote-control") + 1], "#100 미소")
        self.assertEqual(call["args"][call["args"].index("--session-id") + 1], entry["session_id"])
        self.assertEqual(os.path.realpath(call["cwd"]), os.path.realpath(self.desk))

    def test_doctor_reports_login(self):
        self.ready()
        ok = self.run_cmd("doctor")
        self.assertIn("Claude 로그인: 됨", ok.stdout)
        bad = self.run_cmd("doctor", env={"FAKE_LOGGED_IN": "0"})
        self.assertIn("[실패] Claude 로그인", bad.stdout)
        self.assertEqual(bad.returncode, 7)

    def test_home_context_only_in_home(self):
        self.ready()
        here = self.run_cmd("home-context", env={"CLAUDE_PROJECT_DIR": self.desk})
        self.assertIn("dispatcher-win:dispatch", here.stdout)
        other = self.run_cmd("home-context", env={"CLAUDE_PROJECT_DIR": self.work})
        self.assertEqual(other.stdout, "")
        blocker = os.path.join(self.tmp, "file")
        open(blocker, "w").close()
        broken = self.run_cmd("home-context", env={"DISPATCHER_HOME": os.path.join(blocker, "data")})
        self.assertEqual(broken.returncode, 0)

    def test_setup_shell_writes_shim_once(self):
        folder = os.path.join(self.tmp, "bin")
        result = self.run_cmd("setup-shell", "--dir", folder)
        self.assertEqual(result.returncode, 0, result.stdout)
        with open(os.path.join(folder, "dispatcher.cmd"), "rb") as f:
            body = f.read()
        self.assertIn(b"\r\n", body)
        self.assertIn(b"dispatcher-win\\dispatcher-win", body)
        self.assertEqual(self.run_cmd("setup-shell", "--dir", folder).returncode, 0)
        with open(os.path.join(folder, "dispatcher.cmd"), "w") as f:
            f.write("someone else")
        self.assertEqual(self.run_cmd("setup-shell", "--dir", folder).returncode, 7)

    def test_broken_sessions_file_is_not_overwritten(self):
        self.ready()
        os.makedirs(self.home, exist_ok=True)
        with open(os.path.join(self.home, "sessions.json"), "w", encoding="utf-8") as f:
            f.write("{broken")
        self.assertNotEqual(self.run_cmd("list").returncode, 0)
        with open(os.path.join(self.home, "sessions.json"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "{broken")


if __name__ == "__main__":
    unittest.main(verbosity=1)
