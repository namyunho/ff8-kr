# AGENTS.md — 파이널 판타지 VIII PS1 한국어화

이 저장소는 PlayStation용 《파이널 판타지 VIII》(일본판) Disc 1의 한국어 패치를
만든다. **진행 단계와 다음 게이트는 `docs/roadmap.md`가 정본이다.** 현재 상태
요약은 `README.md`, 매체·구조 기준선은 `docs/disc1-baseline.md`, 글꼴과 문자
인코딩은 `docs/font-analysis.md`, 한글 폰트 판정은
`docs/korean-font-feasibility.md`, 번역 경로는 `docs/translation-pipeline.md`,
외부 자료 대조는 `docs/external-sources.md`, 도구 운용은
`docs/reverse-engineering-mcp.md`, 브랜치와 커밋 규칙은
`docs/git-workflow.md`를 정본으로 삼는다.

**외부 커뮤니티 자료를 판정 근거로 쓰지 않는다.** 탐색 방향을 좁히는 데만 쓰고,
채택 전에 이 디스크에 대조해 `docs/external-sources.md`에 결과를 남긴다.

## 하드 불변식

1. 원본 BIN/CUE, BIOS, 추출 파일, RAM/VRAM 덤프와 패치된 전체 이미지는 커밋하지
   않는다. 기본 원본 위치는 `roms/`이며 `.gitignore`를 유지한다.
   `fonts/` 아래 한국어 폰트 소스는 원본 매체가 아니므로 예외로 추적한다.
2. 원본은 크기와 CRC32/MD5/SHA-256을 모두 검증한 뒤 읽는다. 알려진 값과 다르면
   분석·빌드를 중단한다.
3. ISO 파일 좌표(2048), raw 2352바이트 sector 좌표, IMG TOC의 절대 LBA,
   PS-X EXE 파일 오프셋, runtime virtual address를 섞지 않는다.
4. Mode 2 sector는 Form 1/2와 복제 subheader를 판정한다. 변경 sector만 올바른
   EDC/ECC 규칙으로 다시 만들며, 변경하지 않은 sector를 정규화하지 않는다.
5. PS-X EXE는 파일 `+0x800`의 payload가 header의 load address에 적재된다.
   이 환산을 IMG 안의 다른 파일에 적용하지 않는다.
6. **IMG TOC는 디스크 절대 LBA를 담는다.** 자산 크기를 바꾸면 뒤 파일이 밀려
   TOC 전체를 다시 써야 한다. 크기를 유지하는 in-place 교체를 기본으로 한다.
7. **TIM은 `blockSize`가 아니라 RECT가 정본이다.** 게임 로더가 `blockSize`를
   읽지 않는다. 폰트 파일에서 실제로 두 값이 어긋난다.
8. MIPS R3000A 훅은 branch/load delay, live register, `$gp`, cache 갱신과
   overlay 수명을 검산한다. 생성한 모든 명령은 armips와 독립 디스어셈블 결과로
   대조한다.
9. 추출·재조립은 무수정 round-trip을 먼저 통과한다. 최종 이미지 변경은 불변
   원본에 대한 예상 쓰기 범위로 모두 설명돼야 한다.
10. 미확정 판정을 확정으로 표현하지 않는다. 정적 분석 결과는 실행 검증과
    연결되기 전까지 **추정**으로 남긴다.

## 원본 매체

```text
roms/Final Fantasy VIII (Japan, Asia) (Disc 1).cue
roms/Final Fantasy VIII (Japan, Asia) (Disc 1).bin
```

단일 `MODE2/2352` 데이터 트랙, 311,519섹터, CRC32 `2319B365`.

```bash
python3 scripts/psx_disc.py verify
python3 scripts/psx_disc.py toc
```

## 역공학 도구 선택

- IDA Pro / `idalib-mcp`: 짧은 루틴, 바이트, xref, 함수 경계와 반복 질의의 주력.
- Ghidra MCP / `analyzeHeadless`: 긴 제어 흐름이나 포인터 전달을 MIPS 디컴파일로
  교차검증할 때 사용.
- DuckStation: 최종 육안 확인. **스크립팅 API 가 없어 자동화되지 않는다.**
- PCSX-Redux: 런타임 조사(브레이크포인트·워치포인트·VRAM·세이브스테이트).
  설치는 확인했고 **기능은 아직 검증하지 않았다.**
- 저장 파일과 runtime 표현이 다르면 정적 결과를 실행 증거로 승격하지 않는다.
- PS-X EXE는 직접 `idb_open` 하지 말고 `scripts/build_ida_db.py`로 올바른
  `TEXT`/entry가 있는 `.i64`를 만든 뒤 연다.

도구를 새로 붙일 때 기준은 "MCP 가 있는가"가 아니라 **밖에서 명령을 받는가**다.
CLI·HTTP·소켓이면 `Bash` 로 직접 몬다. 스크립팅 언어만 있으면 얇은 브리지를
한 겹 두른다. GUI 만 있으면 자동화 대상이 아니다. **CLI 나 HTTP 로 닿는 것에
MCP 를 새로 만들지 않는다.**

MCP 설정과 import 규칙은 `docs/reverse-engineering-mcp.md`를 따른다. `.mcp.json`은
클라이언트를 새로 시작해야 반영된다. GUI MCP는 해당 앱과 프로그램을 먼저 열어야
한다. headless idalib은 동일 IDB를 다른 프로세스와 동시에 열지 않는다.

## 확정된 핵심 주소

| 대상 | 값 |
|---|---|
| 부트 EXE | `SLPS_018.80`, entry `0x8001152C`, load `0x80010000`, text `0x190800` |
| 폰트 파일 | IMG TOC #130 (`sysfnt.tdw`), LBA 849, 33,764바이트 |
| 폰트 픽셀 오프셋 | **`0x05E4`** — CLUT 블록의 `blockSize` 1,036 이 정한다 |
| 폰트 적재기 | `sub_8002C358` — 유일 호출자 `sub_80011CA8`, 뱅크 인자 상수 `0` |
| 글리프 폭 | `sub_8002E3EC` — 니블 팩, 뱅크 비트 `0x400` |
| 문자열 측정 | `sub_8002E4A0` — **텍스트 인코딩 정본** |
| TIM 로더 | `sub_80035EC4` |
| 폭 테이블 RAM | 뱅크0 `0x800821F8` / 뱅크1 `0x800823BC` (각 452바이트) |
| VRAM 폰트 슬롯 | 뱅크0 `(832,256)`, CLUT `(896,256)`, 뱅크1 `(960,256)` |
| IMG TOC 적재 | LBA 826, 1,072바이트 = 134엔트리 → RAM `0x80099400` |
| CD 읽기 | `sub_800385E4` 요청 세터 / `sub_8003924C` 상태 머신 펌프 |
| 아카이브 해석기 | `sub_80035C84` — 기준 `0x80099420` (= TOC#4), `아카이브 = TOC − 4` |
| 오버레이 목적지 표 | `0x80054340` — `(u32 dest, u32 이름포인터)` × 23 |
| 서브엔트리 디렉터리 | `0x800543F8` — `(u32 오프셋+플래그, u32 크기)`, `>> 11` 로 섹터 |
| 메뉴 메시지 | `mngrp.bin`(TOC#22) 서브 1, LBA 98,036, 10,240B → RAM `0x801E1000` |
| 필드 색인 | `field.bin`(TOC#2) 해제본 `+0x28668`, 1,003엔트리 `[u32 LBA][u32 크기]` |
| 필드 텍스트 | 각 필드 DAT 의 섹션 포인터 `[8]` = MSD, `[u32 오프셋 배열]` |

## 기여 표기

**작성 도구를 저자·공동저자·출처로 기입하지 않는다.** 커밋 메시지, PR 본문,
문서 어디에도 도구 이름이 들어간 트레일러를 넣지 않는다.

## 기본 검증

```bash
python3 scripts/psx_disc.py verify
python3 scripts/psx_disc.py toc
python3 scripts/build_ida_db.py work/disc1/SLPS_018.80 --force
```
