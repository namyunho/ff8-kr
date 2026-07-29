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
| 대사 문자열 저장 위치 | 확정 — 필드 DAT 섹션 [8] = MSD |
| 글리프 인덱스 ↔ 문자 대응표 | 판독 완료 880/882 (실행 검증 전이라 **추정**) |
| 필드 텍스트 전수 추출 | 완료 — DAT 302개 / 메시지 9,442건 |
| 번역문 바이트 수용량 | 측정 완료 — 최악 시나리오도 302필드 전부 적합 |
| LZSS 압축기 | 최적 파싱. DAT 304개 전부 원본보다 작다 |
| 번역 파이프라인 | 내보내기 → 초벌번역 → 되받기 → 검사 → 음절 계수. 도구 완비 |
| 기계 초벌번역 | 진행 중 |
| 번역문의 서로 다른 음절 수 | **미확정** — 전체 초벌번역이 나와야 판정한다 |
| 런타임(에뮬레이터) 검증 | 오프닝 31건이 DuckStation 에서 한국어로 나온다. R7 미해결 |

정적 분석 결과는 실행 검증과 연결되기 전까지 **추정**으로 남긴다. 자세한
진행과 통과 조건은 [`docs/roadmap.md`](docs/roadmap.md) 가 정본이다.

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

### 텍스트 경로

```text
field.bin(TOC#2) → 해제본 +0x28668 목록 1,003엔트리
  → 3종 세트 [MIM, DAT, LZK] 반복. DAT 는 인덱스 ≡ 2 (mod 3)
  → DAT 섹션 포인터 [8] = MSD, 형식은 [u32 오프셋 배열], N = offset[0]/4
```

목록 1,003엔트리를 전수로 훑으면 **DAT 304개**이고 그중 2개는 MSD 가 비어 대사가
없다. 남은 302개에 **MSD 메시지 9,442건**이 있다. DAT 는 목록만으로 가려지지
않고, 해제한 뒤 포인터 12개가 단조 증가하고 `ptr[11]` 이 해제 크기와 맞물리는지로
판정한다.

메뉴 계열은 따로다 — `mngrp.bin`(TOC#22) 서브엔트리 1, 2단 u16 상대 오프셋 표,
그룹 16, 메시지 579건.

### 글리프 대응표

`data/glyph-map-bank0.json` 이 정본이다. 뱅크0 882칸 중 **픽셀이 있는 879자
전부**에 문자를 배정했다(63은 공백, 880·881은 쓰이지 않는 빈 자리).

눈으로 읽은 값이라 세 가지로 되짚었고 셋 다 오류를 잡아냈다.

1. 같은 문자가 두 인덱스에 배정됐는지 — 816(間→聞), 577(客→各)
2. 글리프와 배정 문자를 나란히 렌더해 대조 — 347..351 이 한 칸씩 밀려 있었다
3. 실제 대사를 디코드해 일본어로 읽히는지 — 749(償→後), 350(術→出)

2번은 특히 중요하다. **오십음순 구간이라도 통째로 밀리면 순서 검산으로는
잡히지 않는다.**

### 번역문이 들어갈 자리

인덱스 `0..223` 은 1바이트, `224` 이상은 2바이트로 실린다. 일본어는 가나가 많아
글리프의 **84.7%가 1바이트**이고 글자당 평균 1.15바이트다.

| 시나리오 | 탐욕 압축 | **최적 파싱 압축** |
|---|---|---|
| 손대지 않고 재압축만 | 2 / 302 | **0 / 302** |
| 한글을 전부 224 뒤에 (최악) | 27 / 302 | **0 / 302** |
| 빈도 상위 224자를 1바이트 자리에 | 0 / 302 | **0 / 302** |

압축기를 최적 파싱으로 바꾸자 **최악 시나리오조차 전부 들어간다.** 배치
최적화는 이제 필수가 아니라 여유를 늘리는 수단이다. 그래도 본문이 심하게
쏠려 있어(가장 잦은 224자가 본문의 93.00%를 덮는다) 상위 224자를 1바이트
자리에 놓으면 글자당 평균이 1.07바이트로 지금(1.15)보다 작아진다.

최악 시나리오는 **번역문의 글자 수가 원문과 같다**고 본 것이다. 빈도 수치도
일본어 원문을 한국어의 대리 지표로 쓴 것이라 실제 값은 번역 초안에 같은 도구를
다시 돌려 재야 한다.

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
| [`docs/roadmap.md`](docs/roadmap.md) | **이정표** — 단계별 진행 상황, 통과 조건, 위험 목록 |
| [`docs/disc1-baseline.md`](docs/disc1-baseline.md) | 원본 식별값, ISO/IMG 구조, 모듈 적재 경로, 압축, 그래픽 자산 |
| [`docs/external-sources.md`](docs/external-sources.md) | 외부 커뮤니티 자료의 주장별 대조 결과 |
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

### 텍스트

```bash
# 글리프 대응표 상태와 판독 시트
python3 scripts/build_glyph_map.py --status
python3 scripts/build_glyph_map.py --sheets work/glyphmap --start 608
python3 scripts/build_glyph_map.py --ingest data/glyph-sheets/sheet-608.tsv

# 필드 하나 뜯어보기
python3 scripts/extract_field_text.py 293 --render work/text/f293.png
python3 scripts/extract_field_text.py 293 --worksheet work/text/f293.csv

# 메시지 바이트 ↔ 문자열
python3 scripts/glyph_text.py --hex "6a 46 b6 6c 19 c3 19 ba"
python3 scripts/glyph_text.py --check work/text/opening-scenes.json

# 필드 전체 텍스트 DB 와 `ja` 채우기
python3 scripts/build_text_db.py work/text/field-messages.json
python3 scripts/fill_japanese.py work/text/opening-scenes.json

# 번역문 수용량 측정
python3 scripts/analyze_text_budget.py

# 번역 내보내기 → 되받기 → 검사 → 음절 계수
python3 scripts/export_for_translation.py work/translate
python3 scripts/import_translation.py work/translate-draft work/translate
python3 scripts/import_translation.py work/translate-draft work/translate --propagate
python3 scripts/import_translation.py work/translate-draft work/translate --status
python3 scripts/check_translation.py work/translate --report work/translate-check.json
python3 scripts/count_korean_syllables.py work/translate --layout work/hangul-layout.json
python3 scripts/build_hangul_font.py --glyph-map work/hangul-layout.json --banks 1

# 압축기 전수 검증 (무손실 · 섹터)
python3 scripts/lzss.py --check

# 재조립 왕복 검증
python3 scripts/verify_roundtrip.py --sample 20
```

`work/` 는 추적하지 않는다. 위 명령으로 언제든 다시 만든다.

### 텍스트 표기 규칙

`ja` 칸은 **제어 코드를 하나도 버리지 않는다.** 대사 사이의 코드를 지우면 창
밖으로 흘러넘치거나 이름·수치 삽입이 깨진다. 줄바꿈 `02` 도 개행 문자로 펴지
않는다 — 표 계열 편집기를 거치면 조용히 사라지는 쪽이 개행이다.

```text
カドワキ先生{02}「あぁ、{03:30}。{02} 私はチョット保健室をあけるんで{02} 後はよろしく頼むよ」
```

| 표기 | 뜻 |
|---|---|
| `{01}` | 파라미터 없는 제어 코드 |
| `{03:30}` | 파라미터 있는 제어 코드 |
| `{b1:123}` | 필드 전용 폰트(뱅크1) 글리프 |

`decode` 한 문자열을 `encode` 하면 원본 바이트가 그대로 나온다. 필드 302개
9,442 메시지에서 왕복 실패 0건이다.

### 줄과 쪽을 재는 규칙

**줄을 끊는 것은 `{02}` 하나가 아니다.** `{01}` 은 행 카운터를 1로 되돌리므로
(`sub_8002E4A0` 확정) 폭과 줄 수를 잴 때 함께 나눠야 한다. 842건이 이 코드를
쓰고, 나누지 않으면 쪽 경계를 넘는 글자가 한 줄로 합산돼 폭이 부풀었다.

| 나누는 방식 | 최대 줄 폭 | 302px 초과 |
|---|---|---|
| `{02}` 만 | 659px | 303건 |
| `{02}` + `{01}` | **448px** | **3건** |

`{b1:N}` 은 제어 코드가 아니라 **화면에 그려지는 글자**다. 폭 계산에서 빼면 안
된다. `scripts/text_metrics.py` 가 이 두 규칙의 정본이며 내보내기·검사기가 함께
쓴다. 바이트 셈은 `ko` 에 `ja` 를 그대로 넣는 항등 시험으로 검증했다 — 9,160건
전부 원본 바이트 수와 정확히 일치한다.

### 번역 파이프라인

```text
build_text_db.py      필드 DAT → 전수 텍스트 DB
export_for_translation.py   필드마다 워크시트(JSON)와 압축 프롬프트(TXT)
  (번역기)            id<탭>번역문 TSV 를 work/translate-draft/ 에
import_translation.py 되받기 · 같은 원문 전파 · 남은 것 배치 분할
check_translation.py  넣을 수 있는지 여덟 가지로 검사
count_korean_syllables.py   서로 다른 음절 수와 빈도순 배치 후보
build_hangul_font.py  그 배치로 FF8 형식 뱅크 생성
```

되받기는 `ko` 말고 아무것도 받지 않는다. JSON 을 통째로 돌려받으면 번역기가
원문이나 예산 칸을 조용히 바꿀 수 있다.

**전체 메시지의 34.6%가 다른 곳에도 똑같이 있는 원문이다.** 고유 원문은
6,667종뿐이고, 카드 규칙 안내문 하나가 60곳에 반복된다. `--propagate` 가 같은
원문에 같은 번역을 채워 번역량을 줄이고 표기가 필드마다 갈리는 것을 막는다.

## 라이선스와 범위

이 저장소는 분석 결과, 스크립트, 문서만 담는다. 게임 원본 데이터, 추출 자산,
패치된 이미지, BIOS는 포함하지 않으며 배포하지도 않는다.
`fonts/` 의 갈무리 폰트는 SIL Open Font License 를 따르며 라이선스 원문을
함께 둔다.
