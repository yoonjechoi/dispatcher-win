# dispatcher-win

Windows 에서 프로젝트별 Claude Code 세션에 일을 나눠 맡기는 플러그인이다. 비서 세션 하나에 말하면, 비서가 프로젝트마다 세션을 띄우고 번호를 붙여 관리하고, 일을 전달하고, 결과를 모아 보고한다.

macOS 판(`dispatcher`)은 cmux 터미널 앱을 조종한다. 이 Windows 판은 터미널 앱 없이 Claude Code 에 들어 있는 백그라운드 세션 기능으로 돈다.

## 설치

**Windows 10 · 11 전용이다.** Claude Code 에 이 문장을 붙여 넣으세요.

```
https://raw.githubusercontent.com/yoonjechoi/dispatcher-win/main/INSTALL.md 를 읽고 그대로 설치해 줘
```

먼저 사람이 할 일: Claude Code 설치와 로그인, Python 3.9 이상 설치. 자세한 순서와 확인은 [INSTALL.md](INSTALL.md) 에 있다. 설치가 끝나면 PowerShell 에서 `dispatcher start` 를 직접 친다(Claude 세션 안에서는 거절된다).

## 구조

```
사람 → 리모트 컨트롤(휴대폰 · 데스크톱 앱) 또는 PowerShell → 비서 세션(#100, 홈 폴더, 대화형)
                                   (작업 세션에도 리모트 컨트롤로 직접 붙을 수 있다)
                                                       │ dispatch 스킬, dispatcher 명령
                                                       ▼
                               프로젝트마다 백그라운드 세션 (#1, #2, #1-1 …)
                                                       │ SendMessage 로 보고
                                                       └──→ 비서 세션
```

| 하는 일 | 쓰는 것 |
|---|---|
| 세션 띄우기 | `claude --bg -n "#7 이름" --remote-control "#7 이름" --dangerously-skip-permissions --settings <팀 에이전트 켜기> --append-system-prompt <보고 규칙> "<지시>"` |
| 목록 · 상태 | `claude agents --json` + 번호 명단(`sessions.json`) |
| 화면으로 보기 | `claude attach <ID>` 를 Windows Terminal 새 탭으로 |
| 말 전달 | 세션 안의 `SendMessage` 도구. 꺼진 세션은 `claude --bg --resume` 으로 이어 띄우며 넣는다 |
| 끄기 | `claude stop <ID>` |

## 쓰는 방법

비서 세션에 말로 시킨다.

- 「새 일: 블로그 글 초안 써 줘」 → 폴더를 만들고 `#1 블로그 초안` 세션을 띄운다.
- 「1번에 제목 바꾸라고 해」 → 1번 세션에 전달한다.
- 「블로그 어디 있어?」 → 번호 · 폴더를 알려 주고 Windows Terminal 탭으로 연다.
- 「세션 목록」, 「1번 꺼」, 「디스패처 점검」

직접 치는 명령:

```
dispatcher list [--all] [--json]
dispatcher launch "<이름>" "<폴더>" "<지시>" [--work ..] [--group ..] [--role ..] [--in <번호>] [--no-auto-trust]
dispatcher send <대상> "<메시지>"        # 꺼진 세션을 이어 띄우며 넣는다. 떠 있으면 SendMessage 를 쓰라고 알린다
dispatcher show <대상> [--print]         # Windows Terminal 새 탭에 붙여 연다
dispatcher stop <대상> --confirm "<이름>"
dispatcher set <대상> --group .. --role .. --work ..
dispatcher trust "<폴더>"                # 사용자가 허락한 폴더만
dispatcher init | start [-c] | doctor | setup-shell
```

대상은 `7`, `#7`, `7-1`, 이름 일부, 세션 ID 를 받는다. 같은 이름이 끝난 기록과 떠 있는 세션에 함께 있으면 떠 있는 쪽을 고른다.

## 번호

| 대역 | 번호 | 어떤 세션 |
|---|---|---|
| 작업 | 1~99, 넘으면 1000 부터 | 새로 띄운 일 |
| 상주 | 100~999 | 비서 세션(홈 폴더에서 뜬 세션) |
| 하위 | 7-1, 7-2 … | `--in 7` 로 띄운 같은 프로젝트의 세션 |

번호는 세션에 고정되고 재사용하지 않는다. 이어 띄운 세션이 새 세션 ID 를 받아도 번호는 그대로다.

## 데이터 · 설정

`%USERPROFILE%\.config\dispatcher\` 에 둔다(`DISPATCHER_HOME` 으로 바꿀 수 있다). 플러그인을 지워도 남는다.

| 파일 | 내용 |
|---|---|
| `config.json` | 설정 |
| `sessions.json` | 번호 명단 |
| `trust.log` | 디스패처가 신뢰를 적은 폴더 기록 |

| 키 | 기본값 | 뜻 |
|---|---|---|
| `assistant_name` | `비서` | 비서 세션 이름. 표시는 `#100 이름` |
| `assistant_role`, `owner_name` | `비서`, 없음 | 홈 폴더 `CLAUDE.md` 의 소개 문장 |
| `home_dir` | 없음 | 비서 세션 폴더 |
| `workspace_root` | `home_dir` 의 상위 | 새 프로젝트 폴더를 만들 자리 |
| `trusted_roots` | `[workspace_root]` | 이 아래 폴더는 자동 신뢰 |
| `auto_trust` | `roots` | `off` 면 자동 신뢰를 끈다 |

## 폴더 신뢰와 권한

- 백그라운드 세션은 신뢰하지 않은 폴더에서 뜨지 않는다(「Workspace not trusted」). 위 폴더를 신뢰하면 그 아래 일반 폴더는 이어받지만, git 저장소 폴더는 따로 신뢰가 필요하다.
- 디스패처는 `trusted_roots` 아래 폴더면 `%USERPROFILE%\.claude.json` 에 그 폴더의 신뢰를 적고 다시 띄운다. 출력에 「자동 신뢰함」이 나오고 `trust.log` 에 남는다. **신뢰한 폴더의 훅과 설정은 실행된다.** 믿지 않는 외부 저장소는 `--no-auto-trust` 로 띄운다.
- 홈 폴더는 `dispatcher start` 가 띄우기 전에 신뢰를 적는다(`trust.log` 에 `"how": "start"`).
- 목록 밖 폴더, `..` 가 든 경로, `workspace_root` 를 처음 정한 폴더는 자동 신뢰하지 않고 종료 코드 5 로 멈춘다. 사용자가 허락하면 `dispatcher trust "<폴더>"`.
- 모든 세션(비서 · 작업)은 팀 에이전트를 켜고 뜬다(`--settings '{"env":{"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS":"1"}}'`).
- 작업 세션은 권한 무시(`--dangerously-skip-permissions`)로 뜬다. 처음 한 번은 Claude Code 의 경고 화면을 사람이 승인해야 한다. `dispatcher start` 로 비서 세션을 처음 띄울 때 나온다.
- 백그라운드 세션은 git 저장소에서 스스로 worktree 를 만들려는 경향이 있다. 보고 규칙에 「지금 폴더에서 직접 일하고, 지시가 없으면 worktree 를 만들지 않는다」를 넣어 막는다.

## 보고 규칙

`launch` 는 `--append-system-prompt` 로 이 규칙을 넣는다.

> 너는 dispatcher 가 띄운 세션이다. 큰 작업이 끝나거나 사용자 판단이 필요하면 (비서 이름) 세션(이름: …)에 SendMessage 로 보고해라. … 작업은 지금 폴더에서 직접 한다. 지시가 없으면 git worktree 를 만들지 않는다.

`--in` 으로 띄운 세션에는 부모 · 형제 번호와 조율 규칙이 더 들어간다.

## 한계

- 작업 세션의 화면은 평소에 보이지 않는다. `dispatcher show` 로 Windows Terminal 탭에 붙여서 본다.
- 비서 · 작업 세션 모두 리모트 컨트롤을 켜고 뜬다. 휴대폰 · 데스크톱 Claude 앱 목록에 「#7 이름」으로 나와 직접 붙을 수 있다. 다만 리모트 컨트롤을 한 번도 대화형으로 켜 본 적 없는 PC 에서는 「/rc connecting…」에 머물렀다. `dispatcher start` 로 비서 세션을 먼저 띄우면 풀린다.
- 세션이 질문 · 승인을 기다리면 목록에 「입력 대기」로 나온다. macOS 판의 선택창 알림 훅은 아직 없다.
- macOS 판의 화면 분할, `--worktree`, `rename-sync`, 묶음 설정(`groups`)은 없다.
- 시험한 환경: Windows 11 24H2 Pro, Claude Code 2.1.319, Python 3.12. Windows 10 은 시험하지 못했다.

## 개발

```
python tests/test_dispatcher.py      # 가짜 claude 로 도는 단위 시험. Windows · macOS 둘 다 돈다
```

`bin/dispatcher.py` 가 본체이고 `bin/dispatcher`(Git Bash 용), `bin/dispatcher.cmd`(PowerShell · cmd 용)는 입구다. 표준 라이브러리만 쓴다. 배포는 `.claude-plugin/plugin.json` 의 version 을 올리고 push 한 뒤 `claude plugin update dispatcher-win@dispatcher-win`.

라이선스: MIT
