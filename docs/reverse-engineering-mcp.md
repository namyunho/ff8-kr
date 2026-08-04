# PS1 역공학 도구와 MCP 환경

이 문서는 `psx-gpx-cyberformula-kr`에서 검증한 IDA/idalib/Ghidra 상보 운용을
FF8 PlayStation 일본판(MIPS R3000A) 흐름에 맞게 옮긴 기록이다. 사이버 포뮬러
고유의 `ALLBIN.BIN` overlay 규칙과 CDDA 트랙 처리는 가져오지 않는다.
FF8은 단일 MODE2/2352 데이터 트랙에 중첩 컨테이너를 쓰는 구조라 전제가 다르다.

## 설치 기준선

확인일 2026-07-27, 이 Mac에서 다음 구성을 확인했다.

| 도구 | 경로 | 역할 |
|---|---|---|
| IDA Professional 9.4 | `/Applications/IDA Professional 9.4.app` | GUI 분석, 함수·xref·타입·바이트 확인 |
| ida-pro-mcp / idalib-mcp | `~/Library/Application Support/pipx/venvs/ida-pro-mcp/` | IDA GUI 브리지와 headless idalib MCP |
| Ghidra 12.1.2 | `/opt/homebrew/Cellar/ghidra/12.1.2/libexec` | MIPS 디컴파일 교차검증 |
| GhidraMCP 5.14.2 | `~/Library/Application Support/GhidraMCP/5.14.2/` | Ghidra GUI 브리지 (UDS + TCP `127.0.0.1:8089`) |
| OpenJDK 21.0.11 | `/opt/homebrew/opt/openjdk@21` | Ghidra 실행 |
| armips 0.11.0 | `~/.local/bin/armips` | MIPS R3000 코드 조립과 심볼 출력 |
| mkpsxiso / dumpsxiso | `~/.local/bin/` | 디스크 구조 덤프·재구성 교차검증 |
| xdelta3 | `/opt/homebrew/bin/xdelta3` | 배포용 차분 패치 생성·역적용 |

확인일 2026-07-31, 다음을 추가로 확인했다.

| 도구 | 경로 | 역할 | 상태 |
|---|---|---|---|
| DuckStation | `/Applications/DuckStation.app` | 최종 육안 확인 | 설치 확인 |
| PCSX-Redux | `/Applications/PCSX-Redux.app` (arm64) | 런타임 조사 — 브레이크포인트·워치포인트·VRAM·세이브스테이트 | 설치 확인, **기능 미검증** |
| LM Studio | `/Applications/LM Studio.app`, CLI `~/.lmstudio/bin/lms` | 로컬 초벌번역 엔드포인트 | 설치 확인 |
| kaitai-struct-compiler | — | 바이너리 형식 선언과 파서 생성 | **PATH 에서 안 잡힘** |

BIOS 파일은 저장소로 복사하지 않는다.

**런타임 검증 게이트는 부분적으로 열렸다.** 오프닝 31건이 DuckStation 에서
한국어로 나오는 것을 확인했다. 그러나 이것은 육안 확인이고, 아래 조사 항목은
스크립트로 조작 가능한 실행기가 있어야 진행된다.

Ghidra 헤드리스 실행 파일은 Homebrew 배치상 `bin/`이 아니라 `libexec/`
아래에 있다.

```text
/opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless
```

`scripts/run_ghidra.sh`가 이 경로와 `JAVA_HOME`을 묶어 준다. GUI는 중복 실행을
막고, 헤드리스는 GUI가 같은 프로젝트를 잡고 있으면 거부한다.

```bash
./scripts/run_ghidra.sh                                  # GUI 실행
./scripts/run_ghidra.sh --status                          # 실행 상태와 8089 확인
./scripts/run_ghidra.sh --headless 0x8002E4A0,0x8002C358  # 지정 주소만 디컴파일
```

### GhidraMCP 버전과 포트 — 실측

설치된 확장은 **GhidraMCP 5.14.2**이며 다음을 실측으로 확인했다.

- 서버 포트는 **8089**다. `8080`은 구형 LaurieWired GhidraMCP의 포트이고
  이 설치본에는 해당하지 않는다.
- 서버는 **Ghidra 시작과 동시에 뜬다.** 프로그램을 열어야 포트가 열린다는
  서술은 틀렸다. 다만 디컴파일 등 프로그램 대상 질의는 당연히 프로그램이
  열려 있어야 한다.
- 브리지는 TCP보다 **UDS를 먼저** 쓴다. 소켓은
  `$TMPDIR/ghidra-mcp-$USER/ghidra-<pid>.sock`에 생긴다.
- 브리지가 노출하는 도구는 처음에 **8개뿐**이다(`list_instances`,
  `connect_instance`, `list_tool_groups`, `load_tool_group`,
  `unload_tool_group`, `check_tools`, `search_tools`, `import_file`).
  나머지 251개는 인스턴스에 붙은 뒤 **그룹 단위로 적재**된다. 도구 목록이
  짧다고 해서 연결이 실패한 것이 아니다.

```bash
curl -s http://127.0.0.1:8089/instances     # 404 가 정상 — 라우트가 다르다
./scripts/run_ghidra.sh --status            # 포트 개방 여부 확인
```

## MCP 서버

프로젝트 범위 서버는 루트 `.mcp.json`에 등록했다.

| 서버 | 동작 조건 | 우선 용도 |
|---|---|---|
| `ida-pro-mcp` | IDA GUI에서 대상 DB를 열고 플러그인 HTTP 서버 실행 | 사람과 같은 DB를 보며 조사 |
| `idalib-mcp` | GUI 불필요, 동일 IDB 동시 개방 금지 | 반복 disasm·xref·바이트 질의 |
| `ghidra` | Ghidra CodeBrowser와 GhidraMCP 확장 실행 | 디컴파일과 긴 제어 흐름 대조 |

`.mcp.json`은 **MCP 클라이언트를 새로 시작해야 반영된다.** 등록만으로는 진행
중인 세션에 도구가 바로 붙지 않는다. GUI 기반 서버(`ida-pro-mcp`, `ghidra`)는
해당 앱과 프로그램을 먼저 열어야 응답한다. headless `idalib`은 동일 IDB를 다른
프로세스와 동시에 열지 않는다. 병렬 분석이 필요하면 원본에서 만든 서로 다른
IDB 사본을 `work/` 아래에 둔다.

MCP가 붙지 않은 상태에서도 아래 헤드리스 경로로 동일한 분석을 수행할 수 있고,
실제로 현재 기준선은 이 경로로 만들었다. MCP는 반복 질의를 편하게 하는
수단이지 판정의 근거가 아니다.

## PS-X EXE import

`SLPS_018.80`의 확인된 값은 다음과 같다.

| 항목 | 값 |
|---|---:|
| entry | `0x8001152C` |
| load address | `0x80010000` |
| text size | `0x190800` |
| 파일 payload 시작 | `0x800` |
| data / bss / stack | 모두 `0` |

파일 오프셋과 runtime 주소 관계는 이 실행 파일의 text 범위에서만
`runtime = 0x80010000 + (file_offset - 0x800)`이다. IMG 안의 overlay나
다른 파일에 이 공식을 그대로 쓰지 않는다.

### IDA

idalib의 자동 open은 PS-X EXE를 raw imagebase `0`으로 연다. 따라서 새 PS-X EXE를
곧바로 `idb_open` 하지 않는다. 먼저 loader fixup으로 payload를 header의 load
address에 매핑한 DB를 만든다.

```bash
python3 scripts/build_ida_db.py work/disc1/SLPS_018.80 --force
```

출력은 `work/ida/SLPS_018.80.psx.i64`다. 다음 조건을 확인하기 전에는 xref나
디컴파일 결과를 사용하지 않는다.

- segment 목록에 `TEXT`가 `0x80010000..0x801A0800`으로 있을 것
- entry가 `0x8001152C`이고 이름이 `_start`일 것
- 함수가 인식되고 PsyQ 런타임 signature가 적용될 것

현재 실측값은 segment `RAM/TEXT/RAM` 3개, 함수 **1,472개**, PsyQ signature가
붙은 이름 있는 함수 **417개**(`SsInitHot`, `PadGetState`, `LoadImage`,
`DrawSync` 등)다.

### Ghidra

Ghidra는 PS-X EXE 로더를 기본 제공하지 않으므로 payload만 잘라 raw import 한다.

```bash
# header 0x800 을 제외한 text payload 분리
python3 - <<'PY'
import struct
d=open('work/disc1/SLPS_018.80','rb').read()
_,_,load,size=struct.unpack_from('<4I',d,0x10)
open('work/ghidra/SLPS_018.80.text.bin','wb').write(d[0x800:0x800+size])
PY

GH=/opt/homebrew/Cellar/ghidra/12.1.2/libexec
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
"$GH/support/analyzeHeadless" work/ghidra/proj FF8 \
  -import work/ghidra/SLPS_018.80.text.bin \
  -processor "MIPS:LE:32:default" \
  -loader BinaryLoader -loader-baseAddr 0x80010000 \
  -overwrite
```

`0x800` header를 포함한 채 import하면 base가 어긋나 IDA와 주소가 맞지 않는다.
반드시 payload만 넣고 base를 `0x80010000`으로 준다.

특정 함수만 디컴파일해 IDA와 대조한다.

```bash
FF8_DECOMP_TARGETS="0x80035EC4,0x8002C358" \
"$GH/support/analyzeHeadless" work/ghidra/proj FF8 \
  -process SLPS_018.80.text.bin -noanalysis \
  -scriptPath scripts/ghidra -postScript DecompileTargets.java
```

## 오버레이 적재 base 역산

IMG TOC 의 오버레이는 헤더가 없어 load address 를 모른다. 잘못된 base 로 열면
주소가 전부 어긋나 분석이 무의미해진다.

`jal` 이 절대 주소를 인코딩한다는 점을 이용해 역산한다. 올바른 base 에서는
오버레이 내부를 가리키는 `jal` 타깃이 **함수 프롤로그(`addiu $sp, $sp, -N`)**
에 집중된다. 틀린 base 에서는 적중률이 몇 퍼센트로 떨어지므로 판정이 선명하다.

```bash
python3 scripts/psx_disc.py extract --index 12
python3 scripts/wrap_psx_overlay.py work/extracted/img_012_lba97933.bin --solve
```

실측 결과다. 1순위와 2순위가 15배 이상 벌어진다.

| 오버레이 | base | 내부 jal | 프롤로그 적중 | 차순위 |
|---|---|---:|---:|---:|
| #12 (LBA 97,933) | `0x801E4000` | 248 | **65.7 %** | 4.1 % |
| #4 (LBA 97,859) | `0x801F0000` | 285 | **64.6 %** | 1.8 % |

base 가 정해지면 조사용 PS-X EXE 로 감싸 기존 도구 흐름에 태운다.

```bash
python3 scripts/wrap_psx_overlay.py work/extracted/img_012_lba97933.bin \
    --base 0x801E4000
python3 scripts/build_ida_db.py work/overlay/img_012_lba97933.psxexe \
    --output work/ida/ov12.i64 --force
```

오버레이는 서로 다른 base 를 쓰므로 **DB 를 합치지 않는다.** 같은 파일이라도
적재 위치가 다르면 별도 DB 로 연다.

### 부트 EXE 함수 호출 지점 찾기

오버레이는 부트 EXE 함수를 고정 주소로 호출한다. `jal` 은 대상 주소를 26비트로
인코딩하므로 특정 함수 호출은 **디스크 상에서 유일한 4바이트 패턴**이 된다.

```python
instr = (0x03 << 26) | ((target >> 2) & 0x03FFFFFF)
pattern = struct.pack("<I", instr)      # 4바이트 정렬 위치만 유효
```

예: `jal 0x8002D670` → `9C B5 00 0C`. 이 패턴으로 디스크를 훑으면 해당 API 를
쓰는 오버레이가 바로 나온다. 4바이트 정렬이 아닌 일치는 데이터 우연이므로
버린다.

## 도구 선택 원칙

IDA/idalib과 Ghidra는 대체 관계가 아니다.

- 짧은 루틴, 직접 xref, 바이트와 반복 질의는 IDA/idalib을 먼저 쓴다.
- 포인터 전달, 상태 구조와 긴 분기 흐름이 읽기 어려우면 Ghidra 디컴파일로
  해당 함수만 대조한다.
- 정적 도구의 결과는 실행기에서 실제 적재 module·레지스터·VRAM 소비 시점과
  연결돼야 runtime 사실로 승격한다.
- 두 정적 도구가 같은 결과를 내더라도 잘못된 base, overlay 또는 코드/데이터
  경계를 공유했다면 독립 증거가 아니다.

### Hex-Rays 가 인자를 통째로 버리는 구간이 있다

부트 EXE 의 CD 로더 계열에서 IDA 디컴파일이 **`lw $a0` / `lw $a1` 을 출력에서
누락한다.** 예를 들어 `sub_80011CA8` 은 이렇게 보인다.

```c
sub_800386B0(a1: 0, a2: 0, a3: (int)dword_80066188, a4: 0);   // 틀렸다
```

실제 코드는 다르다.

```asm
lw   $a0, dword_80099810     ; LBA
lw   $a1, [0x80099814]       ; size
lui  $a2, 0x801B             ; dest
jal  sub_800386B0
move $a3, $zero
```

`sub_8003924C` 도 `nullsub_5(); return 0;` 으로 잘못 풀린다. 실제로는 점프
테이블 `off_80055110[byte_80089231]` 을 `jalr` 로 호출하고 갱신된 상태값을
반환한다. 디컴파일만 보면 `while (sub_8003924C() != 0);` 이 no-op 로 읽혀
**로더 전체를 놓친다.**

두 오류 모두 Ghidra 는 내지 않았다. `FUN_800385e4` 와 `FUN_80035c84` 에서
인자와 표 접근을 정확히 냈다. **이 구간은 디스어셈블 또는 Ghidra 가 정본이며,
IDA 디컴파일 결과를 근거로 삼지 않는다.**

`scripts/trace_module_loader.py` 가 이 문제를 우회한다. 디컴파일을 쓰지 않고
호출 지점 앞의 직선 구간을 직접 실행해 `$a0/$a1/$a2` 를 복원한다.

```bash
python3 scripts/trace_module_loader.py work/disc1/SLPS_018.80 --archives
```

### 외부 자료는 대조용으로만 쓴다

커뮤니티 문서(Qhimm Modding Wiki 의 FF8 PlayStation 매체 문서)에 IMG 루트
파일 목록과 필드 DAT 섹션 구성이 정리돼 있다. 이 저장소는 **파일 이름을
외부 자료에서 가져오지 않는다.** 이름은 부트 EXE 안의 이름 문자열 표
(`0x80054340` 의 두 번째 u32)에서 직접 읽었고, 외부 자료는 그 결과가 맞는지
대조하는 데만 썼다.

대조 결과 **일본판 인덱스가 외부 자료의 ID 와 어긋난다.** `wm2field.tbl` 은
양쪽 모두 3 이지만(크기 1,728바이트가 정확히 일치한다) `mngrp.bin` 은 외부
21 / 이 디스크 22 다. 즉 두 인덱스 사이에 일본판 전용 엔트리가 하나 더 있다.
**외부 ID 를 그대로 옮겨 쓰면 안 된다.**

### 실제로 잡아낸 사례

`sub_8002C358`(폰트 적재기)에서 두 도구가 독립적으로 같은 제어 흐름, 같은
452바이트 복사 루프, 같은 목적지 `0x800821F8` / `0x800823BC`, 같은 VRAM 상수
`0x01000340` / `0x010003C0` / `0x00E00120`을 냈다. 이 일치 덕분에 다음을
확정할 수 있었다.

**게임은 TIM header의 RECT를 쓰지 않고 하드코딩된 VRAM 좌표를 쓴다.**
파일만 보고 TIM header의 `VRAM(640,0)`을 폰트 위치로 적으면 틀린다. 또한
`blockSize` 필드가 실제 데이터 길이와 어긋나는데, `LoadImage`가 RECT의
`w*h`로 전송량을 정하므로 **RECT가 정본이고 `blockSize`는 신뢰할 수 없다.**
정적 파일 파싱만으로는 도달할 수 없는 판정이다.

## 런타임 게이트 — 조작 가능한 실행기

미해결 위험 넷(R2·R3·R4·R7)이 **같은 결핍 하나**에 걸려 있다. 실행 중 상태를
밖에서 읽고 멈출 수단이 없다.

| 위험 | 필요한 관찰 |
|---|---|
| R2 뱅크1 적재 경로 | TDW 가 VRAM `(960,256)` 에 올라가는 순간의 호출자 |
| R3 VRAM 빈 구간 | 필드 배경까지 올라간 뒤의 실제 점유 |
| R4 런타임 게이트 | RAM·VRAM 이 정적 판정과 맞는지 |
| R7 MSD 교체가 뒤 섹션을 깨뜨린다 | 밀려난 주소를 **누가 들고 있었나** |

**R7 은 특히 정적 분석으로 못 푼다.** xref 는 "이 코드가 이 주소를 참조한다"
까지만 말한다. 실제 질문은 실행 중에 어떤 포인터가 옛 값을 들고 있었느냐이고,
계산된 주소와 오버레이 교체를 거치면 정적으로는 보이지 않는다.

### PCSX-Redux GDB 스텁 — 실측 (2026-07-31)

붙어서 실제로 해 보고 확인했다. `scripts/psx_gdb.py` 가 통로다.

| 기능 | 응답 | **실제 동작** |
|---|---|---|
| 메모리 읽기 `m` | OK | **된다** |
| 메모리 쓰기 `M` | OK | **된다** (써 넣고 되돌리는 것을 확인) |
| 레지스터 `g` | 72개 | **된다** (PC 는 인덱스 37) |
| 브레이크포인트 `Z0`/`Z1` | **OK** | **안 된다** |
| 워치포인트 `Z2`/`Z3`/`Z4` | **OK** | **안 된다** |

**`Z` 패킷은 구현돼 있지 않다.** 결정적 증거는 `qSupported` 응답이다.

    PacketSize=47ff;qXfer:threads:read;QStartNoAckMode;qXfer:features:read;qXfer:memorymap:read

GDB 규약에서 스텁은 **지원하는 것만** 여기 알린다. `swbreak`·`hwbreak` 가
없다 — 이 스텁은 브레이크포인트를 지원한다고 말한 적이 없다.

실제 응답도 상황에 따라 갈린다. `Dynarec CPU` 를 켠 채로는 `OK` 를 돌려주고
아무 일도 안 하며, 끄면 아예 무응답(타임아웃)이다. 어느 쪽이든 동작하지
않는다.

**두 번 잘못 짚었다.** 처음에는 `OK` 다섯 개를 보고 "워치포인트 전부 지원"
이라 적었고, 다음에는 "Dynarec 때문" 이라며 설정을 탓했다. 둘 다 틀렸다.
`qSupported` 를 먼저 읽었으면 바로 갈렸다 — **스텁에게 무엇을 할 수 있는지
물어보는 것이 먼저다.**

읽기·쓰기가 되므로 관측은 가능하다. 다만 "누가 이 주소를 건드렸나" 는 직접
못 묻고, **표식을 심어 두고 지워지는지 보는** 우회로를 쓴다.

### 도구 선택 — DuckStation 은 자동화되지 않는다

DuckStation 에는 스크립팅 API 가 없다. 디버거 UI 는 사람이 클릭하는 용도다.
호환성이 좋으므로 **최종 육안 확인용으로 유지**하되, 조사는 다른 실행기로 한다.

PCSX-Redux 를 조사용으로 둔다. 역공학을 목적으로 만든 물건이고 다음을 가진다고
알려져 있다. **이 저장소에서 아직 검증하지 않았다.**

- 메모리 읽기/쓰기 브레이크포인트와 워치포인트
- GDB 서버 (표준 프로토콜)
- Lua 스크립팅 API
- VRAM 뷰어, 세이브스테이트

착수 전에 두 가지를 먼저 확인하고 결과를 이 문서에 적는다.

1. **GDB 서버가 워치포인트를 받는가.** 받으면 브리지가 필요 없다 —
   `gdb-multiarch` 로 Bash 에서 바로 몬다.
2. 세이브스테이트 저장·복원이 스크립트로 되는가.

BizHawk(PSX 코어 + Lua)도 대안이다. 디버거는 약하지만 mesen 계열 Lua 와 성격이
같아 학습 비용이 낮다.

### MCP 가 필요한 경계

도구를 붙일 때 판단 기준은 "MCP 가 있는가"가 아니라 **밖에서 명령을 받는가**다.

| 도구가 가진 것 | 필요한 것 |
|---|---|
| CLI / HTTP API / 소켓 | 없다. `Bash` 로 직접 몬다 |
| 스크립팅 언어만 (Lua 등) | 얇은 브리지 한 겹 — 명령 파일 폴링, 결과 파일 |
| GUI 만 | 쓸 수 없다 |

MCP 는 상태가 길게 유지되고 호출마다 그 상태를 이어가야 할 때만 값을 한다.
CLI 나 HTTP 로 닿는 것에 MCP 를 새로 만들지 않는다.

### 세이브스테이트가 조사를 실험으로 바꾼다

지금은 화면을 확인하려면 매번 오프닝부터 플레이해야 한다. 조사 지점 직전
세이브스테이트를 만들어 두면 **시도 비용이 몇 초**가 되고, 같은 조건을 반복할
수 있으므로 관찰이 재현 가능한 증거가 된다. R7 은 복도(350) 진입 직전
스테이트가 있어야 실질적으로 팬다.

## 메모리 접근 추적

브레이크포인트가 "이 순간"을 본다면 추적은 **"이 주소를 건드린 모든 것"**을
본다. 구간 실행 동안 명령 트레이스와 메모리 접근을 남기고 주소로 질의한다.

이것으로 푸는 것.

- R7 의 미해결 항목 1·2 — 필드 DAT 섹션 9·10·11 의 정체, DAT 안에 절대 주소를
  담은 자리가 더 있는지
- 필드 스크립트(JSM)의 메시지 opcode — M7 의 선행 과제로 남아 있다

정적 도구로는 원리상 안 된다. 함수 경계 밖에서 계산되는 주소는 디스어셈블러가
따라가지 못한다.

## 시각 회귀 자동화

이 프로젝트의 실패는 크래시가 아니라 **잘못 그려짐**으로 나타난다. 폰트 잘림,
창 넘침, 글리프 어긋남은 눈으로만 잡히는데 지금은 표본만 본다.

`세이브스테이트 → N프레임 진행 → 프레임버퍼 캡처 → 기준 이미지 대조`가 한
명령이 되면 302개 필드를 전부 자동으로 훑을 수 있다. 필요한 것은 실행기의
프레임 스텝과 프레임버퍼 접근뿐이며, 위 런타임 게이트가 열리면 따라온다.

`scripts/check_translation.py` 가 넣기 전에 잡고, 시각 회귀가 넣은 뒤에 잡는다.
둘이 겹치지 않는다 — 검사기는 바이트와 폭을 계산할 뿐 실제 렌더를 보지 않는다.

## 형식 명세를 선언적으로

DAT·MIM·TDW·MSD 파서를 전부 손으로 파이썬에 썼다. 그 결과 **세 스크립트가 같은
줄 나누기 계산을 따로 하다 같은 곳에서 틀렸다**(`{01}` 미분할). 공용 모듈로
모아 고쳤지만, 형식 자체가 선언되어 있었으면 애초에 갈리지 않는다.

Kaitai Struct 같은 선언적 명세로 옮기면 파서가 명세에서 생성되므로 읽기와
쓰기가 갈리지 않고 왕복 검증이 형식에 붙는다. 성능이 아니라 **실수를 줄이는**
쪽이므로 우선순위는 런타임 게이트보다 뒤다.

CLI 도구이므로 MCP 가 필요 없다. 설치되면 `Bash` 로 직접 돌린다.

## 두 정적 도구 사이의 지식 공유

IDA 와 Ghidra 가 둘 다 붙어 있는데 알아낸 것이 서로 넘어가지 않는다. 같은
함수를 두 번 읽는 비용이 든다.

다만 **공유할수록 독립성이 줄어든다.** 이 문서의 원칙대로 두 도구가 같은
잘못된 base 나 코드/데이터 경계를 공유하면 일치해도 독립 증거가 아니다.
그러므로 공유하되 다음을 지킨다.

- 이름과 타입은 옮겨도 된다 — 읽기 편의다
- **판정은 옮기지 않는다.** 어느 도구에서 나온 결과인지 남긴다
- 교차검증이 필요한 함수는 한쪽 결과를 보기 전에 다른 쪽을 먼저 읽는다

`sub_8002C358` 판정(게임이 TIM RECT 대신 하드코딩 VRAM 좌표를 쓴다)은 두 도구가
**독립적으로** 같은 결과를 냈기 때문에 확정할 수 있었다. 한쪽 결과를 보고 다른
쪽을 맞췄다면 그 값이 없다.

## MIPS R3000A 검산 항목

- branch/jump delay slot
- load delay와 hazard
- `$gp` 기준 데이터와 함수별 live register
- KSEG0/KSEG1 alias와 cache 상태
- self-modifying code나 RAM patch 뒤 instruction cache 갱신
- overlay가 다시 적재될 때 훅·데이터의 수명

## 디스크 도구 경계

`scripts/psx_disc.py`는 원본을 쓰지 않는 프로젝트 전용 조사 도구다.
`dumpsxiso`/`mkpsxiso`는 ISO 구조를 독립적으로 기록하고 재구성 가능성을
대조하는 데 사용한다.

FF8 Disc 1은 CDDA 트랙이 없고 단일 MODE2/2352 데이터 트랙만 쓴다. 다만
`FF8DISC1.IMG` 내부에 XA/STR 스트림이 있을 수 있으므로, 재구성 시 다음을
별도로 확인한다.

- raw sector form(Form 1/2)과 복제 subheader, EDC/ECC
- 변경하지 않은 sector의 바이트 동일성
- IMG TOC의 절대 LBA가 재배치로 깨지지 않는지

**IMG TOC는 절대 LBA를 담는다.** 파일 크기를 바꿔 뒤 파일이 밀리면 TOC 전체를
다시 써야 한다. 크기를 유지하는 in-place 교체를 기본으로 삼는다.

원본 식별과 기본 위치는 `config/original-media.json`과
`scripts/psx_disc.py verify`가 관리한다.
