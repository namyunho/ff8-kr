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
| GhidraMCP 12.1.2 | `~/tools/ghidra-mcp/` | Ghidra GUI 브리지 (HTTP `127.0.0.1:8080`) |
| OpenJDK 21.0.11 | `/opt/homebrew/opt/openjdk@21` | Ghidra 실행 |
| armips 0.11.0 | `~/.local/bin/armips` | MIPS R3000 코드 조립과 심볼 출력 |
| mkpsxiso / dumpsxiso | `~/.local/bin/` | 디스크 구조 덤프·재구성 교차검증 |
| xdelta3 | `/opt/homebrew/bin/xdelta3` | 배포용 차분 패치 생성·역적용 |

DuckStation은 사용자 데이터 디렉터리
`~/Library/Application Support/DuckStation`만 확인했고 실행 파일은 확인하지
않았다. **런타임 검증 게이트는 아직 열리지 않았다.** BIOS 파일은 저장소로
복사하지 않는다.

Ghidra 헤드리스 실행 파일은 Homebrew 배치상 `bin/`이 아니라 `libexec/`
아래에 있다.

```text
/opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless
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
