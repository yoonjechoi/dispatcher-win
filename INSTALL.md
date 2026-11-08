dispatcher-win 설치 안내 (AI 에이전트용 문서)

이 문서는 Claude Code 같은 AI 에이전트가 읽고 사용자의 Windows PC 에 dispatcher-win 을 설치하는 순서다. 사람용 설명은 README.md 에 있다.

## 에이전트 규칙

- 단계 순서대로 한다. 단계마다 명령의 결과를 확인하고, 문서가 멈추라고 한 곳에서는 멈추고 이유를 알린다.
- 사용자 파일(PowerShell 프로필, 사용자 PATH, 설정 파일)을 바꾸기 전에는 무엇을 바꿀지 보여 주고 허락을 받는다. 이 문서가 「묻지 않고」라고 한 것만 바로 한다.
- 명령은 Bash 도구(Git Bash)로 친다. PowerShell 명령은 `powershell -NoProfile -Command "..."` 로 감싼다.
- 실패하면 고치려고 문서 밖의 우회(다른 HOME, 관리자 권한, 정책 변경)를 하지 않는다. 출력 그대로 사용자에게 알리고 멈춘다.

## 사람이 직접 할 일

| 할 일 | 이유 | 이 문서에서 |
|---|---|---|
| Claude Code 설치 · 로그인 | 로그인은 브라우저 승인이 필요하다 | 1단계에서 확인만 한다 |
| Python 3.9 이상 설치 | 설치 프로그램 승인이 필요할 수 있다 | 1단계에서 확인하고, 없으면 방법을 알려 준다 |
| `dispatcher start` 를 PowerShell 에서 직접 치기 | Claude 세션 안에서는 거절된다 | 8단계에서 안내한다 |
| 권한 무시 경고 화면 승인 | 처음 한 번 Claude Code 가 묻는다 | 8단계에서 안내한다 |

## 1단계. 환경 점검

먼저 운영체제만 본다.

```
uname -s
```

- `MINGW*`, `MSYS*`, `CYGWIN*` 이면 Windows 다. 다음으로 간다.
- `Darwin` 이면 멈춘다. 사용자에게 알린다: 「이 플러그인은 Windows 전용입니다. macOS 는 dispatcher(cmux 판)를 씁니다.」
- `Linux` 면 멈춘다. 사용자에게 알린다: 「이 플러그인은 Windows 전용입니다. Linux 판은 아직 없습니다.」 WSL 안의 Linux 도 여기에 해당한다.

Windows 면 나머지를 한 번에 본다.

```
cmd //c ver
claude --version
claude auth status
py -3 --version || python --version
git --version
where wt
```

| 항목 | 통과 기준 | 안 되면 |
|---|---|---|
| Windows 판 | 10 또는 11 | 그대로 진행하되 Windows 10 은 시험하지 않은 판이라고 알린다 |
| claude | 버전이 나온다 | 멈춘다. 「Claude Code 를 먼저 설치하세요: PowerShell 에서 `irm https://claude.ai/install.ps1 \| iex`」 |
| 로그인 | `"loggedIn": true` | 멈춘다. 「PowerShell 에서 `claude` 를 실행해 로그인한 뒤 다시 부르세요」 |
| Python | 3.9 이상 | 멈춘다. 「`winget install Python.Python.3.12` 로 설치하고 새 터미널에서 다시 부르세요」 |
| git | 버전이 나온다 | Claude Code 의 Bash 도구에 필요하다. 없으면 `winget install Git.Git` 을 안내하고 멈춘다 |
| Windows Terminal | 경로가 나온다 | 없어도 진행한다. `dispatcher show` 가 여는 명령만 알려 준다고 알린다 |

## 2단계. 플러그인 설치 · 업데이트

이미 설치돼 있는지 본다.

```
claude plugin list 2>&1 | grep -A3 dispatcher-win
```

- 없으면 묻지 않고 설치한다.
  ```
  claude plugin marketplace add yoonjechoi/dispatcher-win
  claude plugin install dispatcher-win@dispatcher-win --scope user
  ```
- 있으면 업데이트한다.
  ```
  claude plugin marketplace update dispatcher-win
  claude plugin update dispatcher-win@dispatcher-win --scope user
  ```

설치 폴더를 찾아 `D` 로 둔다. 이후 명령은 이 경로로 부른다(이 세션의 PATH 에는 아직 없다).

```
D="$(ls -td "$HOME"/.claude/plugins/cache/dispatcher-win/dispatcher-win/*/ | head -1)bin/dispatcher"
"$D" --help
```

`--help` 가 안 나오면 출력 그대로 알리고 멈춘다. 「Python 3.9 이상이 필요하다」면 1단계의 Python 을 다시 본다.

## 3단계. 이름과 홈 폴더 묻기

이미 설정이 있는지 본다.

```
cat "$HOME/.config/dispatcher/config.json" 2>/dev/null
```

`assistant_name` 과 `home_dir` 이 이미 있으면 묻지 않고 4단계로 간다(init 은 있는 값을 덮어쓰지 않는다). 없으면 사용자에게 한 번에 묻는다.

1. 비서 이름(예: 미소). 기본 「비서」
2. 비서 역할(예: 비서실장). 기본 「비서」
3. 사용자 이름(예: 민준). 비서가 부를 이름
4. 홈 폴더. 비서 세션이 도는 폴더. 기본 `~/workspace/desk`. 새 프로젝트 폴더는 이 폴더의 상위(`~/workspace`)에 만들어진다

## 4단계. init

```
"$D" init --name "<이름>" --role "<역할>" --owner "<사용자 이름>" --home "<홈 폴더>"
```

출력의 「있는 값을 유지함」은 정상이다. 그대로 알린다.

## 5단계. 작업 폴더 기준 신뢰

출력의 `workspace_root` 를 사용자에게 보여 주고 묻는다: 「이 폴더 아래에서 디스패처가 띄우는 세션은 폴더 신뢰를 묻지 않고 실행됩니다. 그 폴더의 훅과 설정도 실행됩니다. 괜찮을까요?」

- 허락하면: `"$D" trust "<workspace_root>"`. 홈 폴더는 `dispatcher start` 가 스스로 신뢰를 적는다
- 거절하면: 신뢰하지 않는다. 처음 세션을 띄울 때 폴더마다 묻게 된다고 알린다.

## 6단계. doctor

```
"$D" doctor
```

| 줄 | 판단 |
|---|---|
| `[실패] Claude 로그인` | 1단계로 돌아간다 |
| `[실패] claude agents` | Claude Code 를 최신으로 올리라고 알린다(`claude update`) |
| `[실패] 홈 폴더` | 4단계를 다시 한다 |
| `[참고] …` | 정보다. 그대로 전한다 |

## 7단계. 터미널에서 부를 수 있게

PowerShell · cmd 에서 `dispatcher` 를 바로 치려면 `%USERPROFILE%\.local\bin\dispatcher.cmd` 를 둔다. 사용자에게 「`~/.local/bin/dispatcher.cmd` 파일 하나를 만듭니다. 설치된 최신 dispatcher-win 을 부르는 짧은 파일입니다」라고 알리고 허락받은 뒤:

```
"$D" setup-shell
```

- 「PATH 에 있음」이면 끝이다. Claude Code 설치가 이 폴더를 PATH 에 넣는다.
- 「PATH 에 없음」이면 사용자에게 묻고 허락받은 뒤 사용자 PATH 에 더한다.
  ```
  powershell -NoProfile -Command "[Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path','User') + ';' + \"$env:USERPROFILE\.local\bin\", 'User')"
  ```
- 「다른 파일이 있어 덮어쓰지 않음」이면 멈추고 그 파일을 사용자에게 보여 준다.

## 8단계. 확인과 보고

사용자에게 이 형식으로 보고한다.

```
dispatcher-win 설치 결과
- Windows: <판>
- 플러그인: <버전>, 처음 설치 / 업데이트
- 설정: <이름>(역할 <역할>, 사용자 <사용자 이름>), 홈 폴더 <폴더>, 작업 폴더 기준 <workspace_root>
- 신뢰: <허락함 / 안 함>
- doctor: <실패 줄 또는 모두 정상>
- 터미널 명령: <dispatcher.cmd 만듦 / 안 함>

이제 사람이 할 일:
1. 새 PowerShell 창을 연다(PATH 를 새로 읽는다).
2. dispatcher start 를 친다. 비서 세션이 홈 폴더에서 뜬다.
3. 처음이면 Claude Code 가 권한 무시 경고를 묻는다. 승인한다(홈 폴더 신뢰는 start 가 적는다).
4. 휴대폰이나 데스크톱 Claude 앱의 세션 목록에 「#100 <이름>」이 보이면 끝이다. 거기서 일을 시키면 된다. 비서가 띄운 작업 세션(「#1 …」)도 목록에 나와 직접 붙을 수 있다.
다음부터 이어서 시작할 때는 dispatcher start -c
```

## 실패 대응

| 증상 | 할 일 |
|---|---|
| `claude plugin marketplace add` 가 실패 | 인터넷 연결과 GitHub 접속을 확인하라고 알리고 멈춘다 |
| `Workspace not trusted` 가 launch 출력에 남음 | 그 폴더가 `trusted_roots` 밖이다. 사용자에게 묻고 `dispatcher trust` |
| `--bg with bypassPermissions requires accepting the disclaimer` | 8단계 3번(권한 무시 경고 승인)을 아직 안 했다. 사람이 `dispatcher start` 를 한 번 하게 한다 |
| 작업 세션이 앱 목록에 안 나오고 `/rc connecting…` 에 머묾 | 그 PC 에서 리모트 컨트롤을 대화형으로 켠 적이 없다. 사람이 `dispatcher start` 를 먼저 한 번 하게 한다 |
| 한글이 깨져 보임 | 표시 문제다. 파일은 UTF-8 로 저장된다. Windows Terminal 을 쓰라고 권한다 |
| `dispatcher` 를 못 찾음(새 터미널) | 7단계를 다시 본다 |
