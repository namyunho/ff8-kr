# 파이널 판타지 VIII PS1 한국어화

PlayStation용 《파이널 판타지 VIII》(일본판) Disc 1의 한국어 패치 프로젝트다.
현재는 **분석 단계**이며 원본 바이너리 수정은 시작하지 않았다.

원본 디스크 이미지와 BIOS는 저장소에 포함되지 않는다. 사용자가 직접 준비해
`roms/` 에 둔다.

## 현재 상태

| 항목 | 상태 |
|---|---|
| 디스크 구조 (ISO / IMG TOC) | 확정 |
| LZSS 압축 해제 | 확정 |
| 부트 EXE 경계와 IDA/Ghidra 기준선 | 확정 |
| 폰트 위치·형식·VRAM 슬롯 | 확정 |
| 텍스트 인코딩 (제어 코드 / 2바이트 문자) | 확정 |
| 한글 폰트 삽입 가능성 | 판정 완료 |
| 대사 문자열 저장 위치 | **미확정** |
| 글리프 인덱스 ↔ 문자 대응표 | **미확정** |
| 런타임(에뮬레이터) 검증 | **미착수** |

## 확정된 구조 요약

### 디스크

- `MODE2/2352` 단일 트랙, 311,519섹터, 732,692,688바이트
- CRC32 `2319B365` / MD5 `cd2a9d4a…` / SHA-256 `6e26677a…`
- 바깥 ISO9660에는 파일이 **3개**뿐:
  `SYSTEM.CNF`, `SLPS_018.80`, `FF8DISC1.IMG`
- `FF8DISC1.IMG` 첫 섹터가 `(u32 절대 LBA, u32 크기)` 목차이며 유효 엔트리
  **134개** (필드 98 / MIPS 오버레이 12 / LZSS 4 / AKAO 4 / 폰트 1 / TIM 1 / 미상 14)
- 이 목차가 덮는 범위는 약 20MB이고, 나머지 약 615MB는 `SMR` 매직의 FMV 스트림

> PC판의 `menu.fs` / `Data\menu\…` 경로는 이 디스크에 존재하지 않는다.
> PSX는 파일명 없이 숫자 인덱스로만 접근한다.

### 폰트

- 파일: IMG TOC **#130** (LBA 849, 33,764바이트)
- 글리프 셀 **12 × 12**, 텍스처 4bpp **256 × 252** = 21열 × 21행 = **441칸**
- 실효 계조 4단계 (CLUT 4색이 4회 반복 — 하위 2비트만 색)
- VRAM 슬롯: 뱅크0 `(832,256)` / CLUT `(896,256)` / 뱅크1 `(960,256)`
- **원본 코드는 뱅크 0만 적재한다.** 유일 호출자가 뱅크 인자를 상수 `0`으로 넘김

### 텍스트 인코딩

| 바이트 | 의미 |
|---|---|
| `0x00` | 종료 |
| `0x01` / `0x02` | 줄 초기화 / 줄바꿈 |
| `0x05` + 1B | 변수 삽입 |
| `0x07` | 페이지 리셋 |
| `0x18`~`0x1B` + 1B | 2바이트 문자, 뱅크 0 |
| `0x1C`~`0x1F` + 1B | 2바이트 문자, 뱅크 1 |
| `0x20`~`0xFF` | 1바이트 문자 (`index = byte − 32`) |

## 한글 폰트 판정

`fonts/` 의 갈무리 폰트 2종을 실측한 결과다.

| 항목 | `galmuri7` | `galmuri11` |
|---|---|---|
| 셀 / 글리프 수 | 8 × 8, 2,350자 | 16 × 16, 2,350자 |
| **실측 잉크** | **7 × 7** | **10 × 10** |
| 12 × 12 무손실 적합 | 2,350 / 2,350 | 2,350 / 2,350 |

**두 폰트 모두 화질 손실 없이 FF8의 12 × 12 셀에 들어간다.** 축소·리샘플이
필요 없다. 원본 일본어 글꼴의 시각 크기에 가까운 `galmuri11` 을 권장한다.

제약은 화질이 아니라 **슬롯 수**다. 자세한 판정과 대안은
[`docs/korean-font-feasibility.md`](docs/korean-font-feasibility.md) 를 본다.

PSX BIOS 폰트 활용은 기각했다. `SCPH-1001` 을 역공학한 결과 16 × 15 **한자**
2,934자와 악센트 라틴만 있고 **한글은 없으며**, FF8은 BIOS를 호출하지도 않는다.

## 문서

| 문서 | 내용 |
|---|---|
| [`docs/disc1-baseline.md`](docs/disc1-baseline.md) | 원본 식별값, ISO/IMG 구조, 압축, 그래픽 자산 |
| [`docs/font-analysis.md`](docs/font-analysis.md) | 폰트 파일 구조, VRAM 슬롯, 폭 테이블, 텍스트 인코딩 |
| [`docs/korean-font-feasibility.md`](docs/korean-font-feasibility.md) | 갈무리 폰트 삽입 판정과 실현 경로 |
| [`docs/reverse-engineering-mcp.md`](docs/reverse-engineering-mcp.md) | IDA / Ghidra 병렬 운용, MCP, import 규칙 |
| [`docs/git-workflow.md`](docs/git-workflow.md) | 브랜치 역할, 표준 흐름, 병합 전 확인 |
| [`AGENTS.md`](AGENTS.md) | 하드 불변식과 확정 주소표 |

## 도구

| 도구 | 용도 |
|---|---|
| IDA Professional 9.4 + `idalib-mcp` | 주력 정적 분석, xref, 바이트 질의 |
| Ghidra 12.1.2 + GhidraMCP | MIPS 디컴파일 교차검증 |
| armips | MIPS R3000 패치 조립 |
| mkpsxiso / dumpsxiso | 디스크 구조 덤프·재구성 대조 |
| xdelta3 | 배포용 차분 패치 |
| DuckStation | 런타임 검증 (미착수) |

IDA와 Ghidra는 대체 관계가 아니라 **서로 다른 실패 형태를 잡는 상보 관계**로
쓴다. 실제로 두 도구의 교차검증으로 "게임이 TIM header의 RECT 대신 하드코딩된
VRAM 좌표를 쓴다"는 판정을 얻었다.

## 사용법

```bash
# 원본 무결성 검증
python3 scripts/psx_disc.py verify

# 구조 조사
python3 scripts/psx_disc.py iso
python3 scripts/psx_disc.py toc
python3 scripts/psx_disc.py extract --index 130
python3 scripts/psx_disc.py report --output work/analysis/disc1-structure.json

# IDA 데이터베이스 생성
python3 scripts/build_ida_db.py work/disc1/SLPS_018.80 --force
```

## 라이선스와 범위

이 저장소는 분석 결과, 스크립트, 문서만 담는다. 게임 원본 데이터, 추출 자산,
패치된 이미지, BIOS는 포함하지 않으며 배포하지도 않는다.
`fonts/` 의 갈무리 폰트는 SIL Open Font License 를 따르며 라이선스 원문을
함께 둔다.
