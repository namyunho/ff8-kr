#!/usr/bin/env python3
"""4중 인터리브 패치. 글자 칸을 882 에서 **1,764** 로 올린다.

## 왜

번역이 필드 대사에 그치지 않는다. 메뉴·아이템·마법·GF·튜토리얼까지 가면
서로 다른 글자가 실측 추정으로 **약 1,175종**이다. 뱅크0 하나(882칸, 부호를
빼면 828)로는 모자란다.

## 어떻게

4bpp 픽셀에 글리프를 **1비트씩 4개** 넣는다. 갈무리는 원래 1비트 픽셀 폰트라
화질 손실이 0 이다.

    평면 = (뱅크비트 ? 2 : 0) | (인덱스 & 1)
    셀   = (인덱스 & 0x3FF) >> 1          441칸 x 4 = 1,764

**텍스처는 뱅크0 것 하나만 쓴다.** 뱅크1 에서는 인코딩 공간과 폭 테이블만
빌린다. 이게 핵심이다 — 뱅크1 의 VRAM `(832,256)` 은 동영상이 지우지만
뱅크0 의 `(960,256)` 은 살아남는 것을 실기에서 확인했다.

## 패치 자리

    0x8002c420   slti 0x11 -> 0x21    CLUT 높이 32 허용
    0x8002c470   0x1c0 -> 0x370       폭표를 884바이트 복사 (1,768니블)
    0x8002c3b8   bne -> 0x8002c51c    필드 한자표 적재 차단
    0x8002c408   0x120 -> 0x3c0       CLUT 적재 x = 960
    0x8002c410   0xe0  -> 0x1d8       CLUT 적재 y = 472
    조회 7곳     0x23bc -> 0x23b1     통합 폭표의 뱅크1 시작(+441바이트)
    기준 5곳     0x3812 -> 0x763c     CLUT id 기준 = (472 << 6) | (960 >> 4)
    그리기 4곳   tpage 분기 6슬롯 교체 (아래)

## 그리기 4곳이 안전한 이유

CLUT 계산 지점에는 빈 슬롯이 없다. 그런데 tpage 분기가 통째로 필요 없어진다 —
두 뱅크가 같은 텍스처를 쓰므로. 그 6슬롯을 이렇게 바꾼다.

    lui  tp, 0xe100
    ori  tp, tp, 0x41f       뱅크0 tpage 로 고정
    lhu  at, off(prim)       아까 저장한 CLUT
    addu at, at, v0          += 뱅크비트(0x400). v0 에 이미 들어 있다
    sh   at, off(prim)
    andi idx, idx, 0x3ff

**분기 목적지가 하나도 안 움직인다.** 팔레트를 16줄 아래(y=240~255)에 두면
16 x 0x40 = 0x400 이라 뱅크 비트가 이미 제자리에 있어 시프트도 필요 없다.

`at` 을 임시로 쓴다. 어셈블러 임시 레지스터라 컴파일된 코드가 문장 사이로
값을 물고 가지 않는다.

    python3 scripts/patch_font_4plane.py --show
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mips_asm import assemble                      # noqa: E402
from mips_dis import Exe, decode                   # noqa: E402

CLUT_LIMIT = 0x8002C420         # slti v0, v0, 0x11
WIDTH_COPY = 0x8002C470         # addiu t0, a2, 0x1c0
BANK_BRANCH = 0x8002C3B8        # bne t0, zero, 뱅크1 갈래
LOADER_RETURN = 0x8002C51C
BANK1_TABLE_OLD = 0x23BC        # 뱅크1 폭표 (별도)
BANK1_TABLE_NEW = 0x23B1        # 통합표 안의 뱅크1 시작 = 0x21f8 + 441

# **팔레트를 옮긴다.** 원본 CLUT `(288,224)` 아래 16줄을 평면2·3 용으로 썼다가
# 실기에서 뱅크1 글자만 노이즈로 나왔다. 그 자리는 폭 16짜리 남의 CLUT 가
# 덮는다 — 한 시점의 0 은 빈 자리가 아니다. 뱅크0 텍스처 사각형 안쪽으로
# 옮긴다. 동영상 두 편 뒤에도 100% 온전한 것이 실측된 유일한 자리다.
CLUT_X_SITE = 0x8002C408        # addiu v0, zero, 0x120   -> 288
CLUT_Y_SITE = 0x8002C410        # addiu v0, zero, 0xe0    -> 224
CLUT_VRAM_X, CLUT_VRAM_Y = 960, 472

# **팔레트 상수는 짝을 이룬다.** 짝수 글리프용과 홀수 글리프용이 한 줄
# 차이(`+0x40`)로 붙어 있다. 처음에 짝수 것만 고쳤더니 홀수 인덱스 글리프가
# 게임 전체에서 안 보였다 — 옛 자리를 가리키는데 거기엔 아무것도 없어서
# 투명하게 그려진다. 검사가 "0x3812 가 5곳" 만 봐서 빠진 짝을 가려 줬다.
#
#     addiu a0, zero, 0x3812      짝수
#     addiu a0, zero, 0x3852      홀수  = +0x40 (한 줄 아래)
CLUT_IDS = {
    0x3812: (CLUT_VRAM_Y << 6) | (CLUT_VRAM_X >> 4),            # 0x763c
    0x3852: ((CLUT_VRAM_Y + 1) << 6) | (CLUT_VRAM_X >> 4),      # 0x767c
}
CLUT_ID_COUNT = {0x3812: 5, 0x3852: 4}      # EXE 안에서 기대하는 곳 수

# 그리기 4곳. (분기 주소, tpage 레지, 인덱스 레지, CLUT 저장 오프셋, 프리미티브 레지)
DRAW_SITES = (
    (0x8002E870, "v1", "a2", 0x12, "a1"),
    (0x8002EA2C, "v1", "t2", 0x02, "t3"),
    (0x8002ECA0, "a3", "a2", 0x12, "s2"),
    (0x8002F0DC, "a3", "a2", 0x12, "s2"),
)

# **적재를 먼저 하고 그 사이에 tpage 를 만든다.** R3000 은 적재 지연 슬롯이
# 있어서 `lhu` 바로 다음 명령은 **아직 옛 값을 읽는다**. 처음에는 이렇게 짰다가
# 실기에서 크래시했다.
#
#     lhu   at, off(prim)
#     addu  at, at, v0      <- 실기에서는 at 이 옛 값이다
#
# 에뮬레이터는 봐주고 실기는 안 봐주는 대표적인 자리다. 슬롯을 더 쓰지 않고
# 순서만 바꿔 적재와 사용 사이에 두 명령을 끼웠다.
DRAW_PATCH = """
    lhu   at, {off:#x}({prim})
    lui   {tp}, 0xe100
    ori   {tp}, {tp}, 0x41f
    addu  at, at, v0
    sh    at, {off:#x}({prim})
    andi  {idx}, {idx}, 0x3ff
"""


def expect(exe: Exe, addr: int, want: int, what: str) -> None:
    got = exe.word(addr)
    if got != want:
        raise ValueError(f"{addr:#010x} 가 {got:08x} 다. {what} 이어야 한다 "
                         f"({want:08x}) — 다른 판본이거나 이미 패치됐다")


# ----------------------------------------------------------------------
# 그림자 — 글리프마다 프리미티브를 하나 더 만든다
#
# 4중 인터리브는 글리프당 1비트라 한 글자가 색 하나뿐이다. 원본처럼 그림자를
# 넣으려면 **같은 글리프를 한 번 더, 1픽셀 밀어, 어두운 팔레트로** 그리는 수밖에
# 없다(2비트로 바꾸면 칸이 756 으로 줄어 한글이 안 들어간다).
#
# 사슬이 거꾸로 엮인다는 점이 도움이 된다. `swl s4, 0x2(s2)` 가 **앞** 프리미티브
# 주소를 태그에 적으므로 나중에 만든 것이 먼저 그려진다. 글리프 뒤에 그림자를
# 만들면 그림자가 밑에 깔린다 — 순서 뒤집기가 저절로 맞는다.
#
# 거는 자리는 루프 꼬리의 `j 0x8002f000` 이다. **`jal` 이 아니라 `j`** 라
# `ra` 를 건드리지 않아 함수 안에서 안전하다. 지연 슬롯(`addu s3, s3, v0`)은
# 그대로 두고 루틴이 끝에서 되돌아온다.
#
# 프리미티브 풀에는 경계 검사가 있다(0x8002a878). 넘치면 오류 경로로 빠지므로
# **루틴이 직접 한계를 보고 넘칠 것 같으면 그림자를 건너뛴다.** 넘쳐서 깨지는
# 대신 그림자만 사라진다.
# **여백이 아니라 빈 함수였다.** `0x80011ac0` 이 `jal 0x8001f5a4` 로 부른다.
# 그 안은 nop 4,096바이트이고 `0x800205a4` 의 `jr ra` 로 끝난다 — 아무것도 안
# 하지만 실제로 불리는 코드다. 여기에 루틴을 얹었으면 그 호출이 글리프 루프로
# 뛰어들었을 것이다. 검사가 이걸 잡았다.
#
# 앞머리를 `jr ra` 로 바꿔 즉시 돌아가게 하고(하던 일이 nop 1,024개였으니
# 동작은 같다) 그 뒤부터 쓴다.
SHADOW_STUB = 0x8001F5A4        # jal 로 불리는 빈 함수
SHADOW_CODE = 0x8001F5AC        # 그 뒤 — 여기부터 우리 것
SHADOW_HOOK = 0x8002F1A4        # j 0x8002f000  (루프 꼬리)
SHADOW_LOOP = 0x8002F000
PRIM_CTX = 0x800821E0           # [여기] -> {현재 포인터, 한계, …}
SHADOW_CLUT = 0x7E3C            # (504 << 6) | (960 >> 4)

# 자리마다 레지스터가 다르다. 틀 하나에 이름만 갈아 끼운다.
#
#   prim   프리미티브 기준 주소. 루프 꼬리에서는 **이미 0x18 전진한 상태**라
#          방금 만든 글리프는 prim-0x18 에 있다
#   link   앞 프리미티브를 가리키는 값(주소 << 8). 우리가 그림자로 갱신한다
#   extra  같이 전진시켜야 하는 두 번째 커서 (없으면 빈 줄)
#   s0~s5  긁어 써도 되는 레지스터. 루프 머리에서 다시 만들어지는 것들이다
SHADOW_ASM = """
    lui   at, 0x8008
    lw    at, {ctx_lo:#x}(at)
    addiu {s1}, {prim}, 0x18
    lw    {s0}, 0x4(at)
    nop
    sltu  {s0}, {s0}, {s1}
    bne   {s0}, zero, {back:#x}
    nop

    lw    {s0}, -0x18({prim})
    lw    {s1}, -0x14({prim})
    lw    {s2}, -0x10({prim})
    lw    {s3}, -0xc({prim})
    lw    {s4}, -0x8({prim})
    lw    {s5}, -0x4({prim})
    sw    {s0}, 0x0({prim})
    sw    {s1}, 0x4({prim})
    sw    {s2}, 0x8({prim})
    sw    {s3}, 0xc({prim})
    sw    {s4}, 0x10({prim})
    sw    {s5}, 0x14({prim})

    lhu   {s0}, 0xc({prim})
    lhu   {s1}, 0xe({prim})
    addiu {s0}, {s0}, 0x1
    addiu {s1}, {s1}, 0x1
    sh    {s0}, 0xc({prim})
    sh    {s1}, 0xe({prim})

    lhu   {s0}, 0x12({prim})
    nop
    andi  {s1}, {s0}, 0x40
    andi  {s0}, {s0}, 0x400
    srl   {s0}, {s0}, 3
    addu  {s1}, {s1}, {s0}
    addiu {s1}, {s1}, {clut:#x}
    sh    {s1}, 0x12({prim})

    lw    {s0}, 0x0({prim})
    srl   {s1}, {link}, 8
    lui   {s2}, 0xff00
    and   {s0}, {s0}, {s2}
    sll   {s1}, {s1}, 8
    srl   {s1}, {s1}, 8
    or    {s0}, {s0}, {s1}
    sw    {s0}, 0x0({prim})

    sll   {link}, {prim}, 8
    addiu {prim}, {prim}, 0x18
{extra}    beq   zero, zero, {loop:#x}
    nop
"""

# (이름, 꼬리 분기 자리, 원래 워드, 루프 머리, prim, link, extra, 긁을 레지스터)
SHADOW_SITES = (
    ("필드 대사", 0x8002F1A4, 0x0800BC00, 0x8002F000, "s2", "s4", "",
     ("v0", "v1", "a1", "a2", "a3", "t0")),
    ("메뉴 계열", 0x8002EAF0, 0x0800BA52, 0x8002E948, "a1", "t5",
     "    addiu t3, t3, 0x18\n",
     ("v0", "v1", "a0", "t0", "t1", "t2")),
    # 세 번째 EXE 그리기 자리. 필드와 구조가 같다 — prim=s2, 사슬=s4, 커서 하나.
    # 사슬은 `sll fp, s2, 8` -> `addu t0, fp, zero` -> `addu s4, t0, zero` 로
    # 넘어가므로 꼬리 시점에는 s4 가 방금 만든 글리프를 가리킨다.
    ("메뉴 창", 0x8002ED68, 0x0800BAF3, 0x8002EBCC, "s2", "s4", "",
     ("v0", "v1", "a1", "a2", "a3", "t0")),
)


# ----------------------------------------------------------------------
# 오버레이(menumain.ovl)용 세 번째 변형
#
# 메뉴 글자는 `menumain.ovl`(TOC #4, 0x801f0000 적재)이 자기 루프로 그린다.
# **프리미티브가 20바이트**라 앞의 둘(24바이트)과 모양이 다르다.
#
#     0x00 태그   0x04 색/코드   0x08 화면 x,y   0x0c u,v   0x0e CLUT   0x10 12x12
#
# 오버레이 안에는 코드를 놓을 자리가 없다 — 0 구간 둘 다 뛰어드는 곳이 있다.
# 그래서 루틴은 EXE 쪽 여유에 두고 **`j` 로 오간다.** `b` 는 상대 16비트라
# 1.9MB 를 못 넘지만 `j` 는 26비트 절대라 닿는다. 오버레이 적재 주소가
# 고정이므로 되돌아올 목적지도 고정이다.
OVL_BASE = 0x801F0000           # menumain.ovl 적재 주소
OVL_HOOK = 6424                 # 파일 오프셋. `j 0x801f1804` (루프 꼬리)
OVL_LOOP = 0x801F1804           # 루프 머리
OVL_STRIDE = 0x14
OVL_SHADOW_AT = 0        # apply() 가 채운다

OVL_SHADOW_ASM = """
    lui   at, 0x8008
    lw    at, {ctx_lo:#x}(at)
    addiu v1, s1, 0x14
    lw    v0, 0x4(at)
    nop
    sltu  v0, v0, v1
    bne   v0, zero, {back:#x}
    nop

    lw    v0, -0x14(s1)
    lw    v1, -0x10(s1)
    lw    a0, -0xc(s1)
    lw    a1, -0x8(s1)
    lw    a2, -0x4(s1)
    sw    v0, 0x0(s1)
    sw    v1, 0x4(s1)
    sw    a0, 0x8(s1)
    sw    a1, 0xc(s1)
    sw    a2, 0x10(s1)

    lhu   v0, 0x8(s1)
    lhu   v1, 0xa(s1)
    addiu v0, v0, 0x1
    addiu v1, v1, 0x1
    sh    v0, 0x8(s1)
    sh    v1, 0xa(s1)

    lhu   v0, 0xe(s1)
    nop
    andi  v1, v0, 0x40
    andi  v0, v0, 0x400
    srl   v0, v0, 3
    addu  v1, v1, v0
    addiu v1, v1, {clut:#x}
    sh    v1, 0xe(s1)

    lw    v0, 0x0(s1)
    srl   v1, s2, 8
    lui   a0, 0xff00
    and   v0, v0, a0
    sll   v1, v1, 8
    srl   v1, v1, 8
    or    v0, v0, v1
    sw    v0, 0x0(s1)

    sll   s2, s1, 8
    addiu s1, s1, 0x14
    nop
    nop
"""


# ----------------------------------------------------------------------
# 네 번째 자리 — TOC #26 안의 모듈 (메뉴 화면을 실제로 그리는 것)
#
# `menumain.ovl` 에 훅을 걸었는데도 메뉴에 그림자가 안 졌다. 메뉴 화면을 그리는
# 것은 **아카이브 TOC #26 안의 모듈**이었다. 목차의 dest 가 `0x200` 이라
# 오버레이가 아닌 것처럼 보이지만 안의 코드가 따로 `0x8009a000` 에 실린다.
#
# 적재 주소는 `jal` 목적지 분포로 역산했다 — 후보 베이스마다 "목적지가 함수
# 프롤로그(`addiu sp,sp,-N`)에 떨어지는가" 를 세면 신호가 뚜렷하다.
# `0x8009a000` 에서 3,960개 중 1,715개가 맞았다.
#
# 루프 모양은 EXE 메뉴 변형과 같다(24바이트, 커서 둘). 레지스터만 다르다 —
# **`t1` 이 두 번째 커서**라 긁는 데 못 쓴다.
#
#   prim=t4  사슬=t7  두 번째 커서=t1  긁기=v0,v1,a0,a1,t0,t2
MOD_ARCHIVE = 26                # IMG 목차 색인
MOD_BASE = 0x8009A000
MOD_HOOK = 224688               # 파일 오프셋. `j 0x800d0c8c` (루프 꼬리)
MOD_LOOP = 0x800D0C8C
MOD_SHADOW_AT = 0        # apply() 가 채운다

# 끝을 `j` 로 바꾼다. EXE 의 루틴에서 700KB 떨어져 상대 분기로는 못 닿는다.
MOD_SHADOW_ASM = SHADOW_ASM.replace(
    "    beq   zero, zero, {loop:#x}\n    nop\n", "    nop\n    nop\n")


# ----------------------------------------------------------------------
# 다섯 번째 자리 — 삽입식 사슬 (메뉴 창을 실제로 그리는 경로)
#
# 훅을 넷 걸고도 메뉴에 그림자가 안 졌다. 아주 초기에 "패리티를 안 더하는
# 다섯 번째 경로" 로 적어 두고 넘긴 `0x80033118` 이 진짜였다.
#
# 앞의 넷과 사슬 방식이 다르다. **고정 기준점 `s5` 뒤에 끼워 넣는다.**
#
#     sll at, s2, 8        at = 새 프리미티브
#     lwl s4, 0x2(s5)      s4 = 기준점의 현재 링크
#     swl at, 0x2(s5)      s5 -> 새 것
#     swl s4, 0x2(s2)      새 것 -> 원래 링크
#
# 나중에 끼운 것이 머리에 가까우므로 **그림자를 글리프 뒤에 끼우면** 그림자가
# 먼저 그려진다 — 앞의 넷과 결론이 같다.
#
# 레지스터가 빡빡하다. a0(커서)·t0·s1·s6·fp 가 살아 있고 확실히 죽은 것은
# v0·v1 뿐이다. 그래서 **스택에 저장·복원**한다. 분석으로 아끼는 것보다
# 안전하고, 자리는 넉넉하다.
INS_HOOK = 0x8003317C           # j 0x8003311c  (루프 꼬리)
INS_LOOP = 0x8003311C
INS_WAS = 0x0800CC47
INS_ANCHOR = "s5"               # 삽입 기준점
INS_PRIM = "s2"
INS_CURSOR = "a0"

INS_SHADOW_ASM = """
    addiu sp, sp, -0x20
    sw    v0, 0x0(sp)
    sw    v1, 0x4(sp)
    sw    at, 0x8(sp)
    sw    t0, 0xc(sp)
    sw    t1, 0x10(sp)

    lui   at, 0x8008
    lw    at, {ctx_lo:#x}(at)
    addiu v1, {prim}, 0x14
    lw    v0, 0x4(at)
    nop
    sltu  v0, v0, v1
    bne   v0, zero, {back:#x}
    nop

    lw    v0, -0x14({prim})
    lw    v1, -0x10({prim})
    lw    at, -0xc({prim})
    lw    t0, -0x8({prim})
    lw    t1, -0x4({prim})
    sw    v0, 0x0({prim})
    sw    v1, 0x4({prim})
    sw    at, 0x8({prim})
    sw    t0, 0xc({prim})
    sw    t1, 0x10({prim})

    lhu   v0, 0x8({prim})
    lhu   v1, 0xa({prim})
    addiu v0, v0, 0x1
    addiu v1, v1, 0x1
    sh    v0, 0x8({prim})
    sh    v1, 0xa({prim})

    lhu   v0, 0xe({prim})
    nop
    andi  v1, v0, 0x40
    andi  v0, v0, 0x400
    srl   v0, v0, 3
    addu  v1, v1, v0
    addiu v1, v1, {clut:#x}
    sh    v1, 0xe({prim})

    lui   t1, 0xff00
    lw    v0, 0x0({anchor})
    lw    v1, 0x0({prim})
    nop
    and   v1, v1, t1
    sll   t0, v0, 8
    srl   t0, t0, 8
    or    v1, v1, t0
    sw    v1, 0x0({prim})

    and   v0, v0, t1
    sll   t0, {prim}, 8
    srl   t0, t0, 8
    or    v0, v0, t0
    sw    v0, 0x0({anchor})

    addiu {prim}, {prim}, 0x14
    addiu {cursor}, {cursor}, 0x14

    lw    v0, 0x0(sp)
    lw    v1, 0x4(sp)
    lw    at, 0x8(sp)
    lw    t0, 0xc(sp)
    lw    t1, 0x10(sp)
    addiu sp, sp, 0x20
    beq   zero, zero, {loop:#x}
    nop
"""


def jump(target: int) -> int:
    """`j target` 을 손으로 짠다. 어셈블러에 없다."""
    return (2 << 26) | ((target >> 2) & 0x03FFFFFF)


def shadow_free(exe: Exe, where: int, size: int) -> None:
    """코드를 놓을 자리가 정말 비었는지 본다. **0 이라고 빈 자리가 아니다.**

    실행 중 RAM 에서도 0 인 것을 따로 확인했고, 여기서는 그 구간이 함수 사이
    여백인지(앞이 `jr ra`, 뒤가 프롤로그) 그리고 아무도 가리키지 않는지를 본다.
    """
    for offset in range(0, size, 4):
        if exe.word(where + offset) != 0:
            raise ValueError(f"{where + offset:#x} 가 0 이 아니다")
    lo, hi = where, where + size
    for off in range(exe.HEADER, exe.HEADER + exe.size, 4):
        word = int.from_bytes(exe.data[off:off + 4], "little")
        here = exe.load + off - exe.HEADER
        op = word >> 26
        if op in (2, 3):                                    # j / jal
            target = (here & 0xF0000000) | ((word & 0x3FFFFFF) << 2)
        elif op in (4, 5, 6, 7) or (op == 1):               # 조건 분기
            target = here + 4 + (((word & 0xFFFF) ^ 0x8000) - 0x8000) * 4
        else:
            continue
        if lo <= target < hi:
            raise ValueError(f"{here:#x} 가 {target:#x} 로 뛴다 — 빈 자리가 아니다")


def clut_id_sites(exe: Exe, old: int) -> list[int]:
    """CLUT id 기준값을 즉치로 쓰는 곳을 전부 찾는다.

    짝수용(`0x3812`)은 그리기 4곳 말고 `0x80033118` 이 하나 더 있다 —
    패리티를 안 더하는 다섯 번째 글자 그리기 경로다. 4중 인터리브에서 제대로
    도는지는 아직 확인 안 했지만, **팔레트를 옮기면 여기도 같이 옮겨야** 한다.
    """
    out = []
    for off in range(exe.HEADER, exe.HEADER + exe.size, 4):
        word = int.from_bytes(exe.data[off:off + 4], "little")
        if word >> 26 in (8, 9) and (word & 0xFFFF) == old:
            out.append(exe.load + off - exe.HEADER)
    return out


def width_sites(exe: Exe) -> list[int]:
    """뱅크1 폭표를 가리키는 `addiu rX, rY, 0x23bc` 를 전부 찾는다."""
    out = []
    for off in range(exe.HEADER, exe.HEADER + exe.size, 4):
        word = int.from_bytes(exe.data[off:off + 4], "little")
        if word >> 26 == 9 and (word & 0xFFFF) == BANK1_TABLE_OLD:
            out.append(exe.load + off - exe.HEADER)
    return out


def _install_shadow(exe: Exe, done: list) -> None:
    for i, line in enumerate(("jr ra", "nop")):
        exe.put_word(SHADOW_STUB + i * 4,
                     int.from_bytes(assemble(line, SHADOW_STUB + i * 4), "little"))
    done.append((SHADOW_STUB, "빈 함수를 즉시 반환으로 — 뒤를 쓴다"))

    cursor = SHADOW_CODE
    for name, hook, was, loop, prim, link, extra, scratch in SHADOW_SITES:
        fields = dict(ctx_lo=PRIM_CTX & 0xFFFF, clut=SHADOW_CLUT, loop=loop,
                      prim=prim, link=link, extra=extra,
                      **{f"s{n}": r for n, r in enumerate(scratch)})
        code = assemble(SHADOW_ASM.format(back=cursor, **fields), cursor)
        code = assemble(
            SHADOW_ASM.format(back=cursor + len(code) - 8, **fields), cursor)
        shadow_free(exe, cursor, len(code))
        for i in range(0, len(code), 4):
            exe.put_word(cursor + i, int.from_bytes(code[i:i + 4], "little"))
        expect(exe, hook, was, f"{name} 루프 꼬리 분기")
        exe.put_word(hook, int.from_bytes(
            assemble(f"beq zero, zero, {cursor:#x}", hook), "little"))
        done.append((cursor, f"그림자 루틴 [{name}] {len(code) // 4}명령"))
        done.append((hook, f"[{name}] 루프 꼬리 -> 그림자"))
        cursor += len(code)

    # 오버레이용. 훅은 EXE 가 아니라 menumain.ovl 에 걸리므로
    # `patch_overlay_clut.py` 가 이 주소를 받아 간다.
    fields = dict(ctx_lo=PRIM_CTX & 0xFFFF, clut=SHADOW_CLUT)
    code = bytearray(assemble(OVL_SHADOW_ASM.format(back=cursor, **fields), cursor))
    back = cursor + len(code) - 8
    code = bytearray(assemble(OVL_SHADOW_ASM.format(back=back, **fields), cursor))
    code[-8:-4] = jump(OVL_LOOP).to_bytes(4, "little")     # 마지막 nop -> j
    shadow_free(exe, cursor, len(code) + 16)
    for i in range(0, len(code), 4):
        exe.put_word(cursor + i, int.from_bytes(code[i:i + 4], "little"))
    global OVL_SHADOW_AT
    OVL_SHADOW_AT = cursor
    done.append((cursor, f"그림자 루틴 [메뉴 오버레이] {len(code) // 4}명령"))
    cursor += len(code)

    # 네 번째 — TOC #26 안의 모듈. 24바이트 프리미티브에 커서 둘.
    fields = dict(ctx_lo=PRIM_CTX & 0xFFFF, clut=SHADOW_CLUT, loop=MOD_LOOP,
                  prim="t4", link="t7", extra="    addiu t1, t1, 0x18\n",
                  **{f"s{n}": r for n, r in
                     enumerate(("v0", "v1", "a0", "a1", "t0", "t2"))})
    code = bytearray(assemble(MOD_SHADOW_ASM.format(back=cursor, **fields), cursor))
    back = cursor + len(code) - 8
    code = bytearray(assemble(MOD_SHADOW_ASM.format(back=back, **fields), cursor))
    code[-8:-4] = jump(MOD_LOOP).to_bytes(4, "little")
    shadow_free(exe, cursor, len(code) + 16)
    for i in range(0, len(code), 4):
        exe.put_word(cursor + i, int.from_bytes(code[i:i + 4], "little"))
    global MOD_SHADOW_AT
    MOD_SHADOW_AT = cursor
    done.append((cursor, f"그림자 루틴 [메뉴 모듈] {len(code) // 4}명령"))
    cursor += len(code)

    # 다섯 번째 — 삽입식 사슬. 건너뛸 때도 스택을 되돌려야 하므로 목적지가
    # 마지막 분기가 아니라 **복원 블록(끝에서 8명령)** 이다.
    fields = dict(ctx_lo=PRIM_CTX & 0xFFFF, clut=SHADOW_CLUT, loop=INS_LOOP,
                  prim=INS_PRIM, anchor=INS_ANCHOR, cursor=INS_CURSOR)
    code = assemble(INS_SHADOW_ASM.format(back=cursor, **fields), cursor)
    back = cursor + len(code) - 32
    code = assemble(INS_SHADOW_ASM.format(back=back, **fields), cursor)
    shadow_free(exe, cursor, len(code) + 16)
    for i in range(0, len(code), 4):
        exe.put_word(cursor + i, int.from_bytes(code[i:i + 4], "little"))
    expect(exe, INS_HOOK, INS_WAS, "삽입식 루프 꼬리")
    exe.put_word(INS_HOOK, int.from_bytes(
        assemble(f"beq zero, zero, {cursor:#x}", INS_HOOK), "little"))
    done.append((cursor, f"그림자 루틴 [메뉴 창-삽입식] {len(code) // 4}명령"))
    done.append((INS_HOOK, "[메뉴 창-삽입식] 루프 꼬리 -> 그림자"))



def apply(exe: Exe, show: bool, shadow: bool = True) -> list[tuple[int, str]]:
    done: list[tuple[int, str]] = []

    expect(exe, CLUT_LIMIT, 0x28420011, "slti v0, v0, 0x11")
    exe.put_word(CLUT_LIMIT, int.from_bytes(
        assemble("slti v0, v0, 0x25", CLUT_LIMIT), "little"))
    done.append((CLUT_LIMIT, "CLUT 높이 제한 16 -> 36 (테마 32 + 그림자 4)"))

    expect(exe, WIDTH_COPY, 0x24C801C0, "addiu t0, a2, 0x1c0")
    exe.put_word(WIDTH_COPY, int.from_bytes(
        assemble("addiu t0, a2, 0x370", WIDTH_COPY), "little"))
    done.append((WIDTH_COPY, "폭표 복사 452 -> 884바이트"))

    expect(exe, BANK_BRANCH, 0x15000022, "bne t0, zero, 뱅크1 갈래")
    exe.put_word(BANK_BRANCH, int.from_bytes(
        assemble(f"bne t0, zero, {LOADER_RETURN:#x}", BANK_BRANCH), "little"))
    done.append((BANK_BRANCH, "필드 한자표 적재 차단"))

    expect(exe, CLUT_X_SITE, 0x24020120, "addiu v0, zero, 0x120")
    exe.put_word(CLUT_X_SITE, int.from_bytes(
        assemble(f"addiu v0, zero, {CLUT_VRAM_X:#x}", CLUT_X_SITE), "little"))
    expect(exe, CLUT_Y_SITE, 0x240200E0, "addiu v0, zero, 0xe0")
    exe.put_word(CLUT_Y_SITE, int.from_bytes(
        assemble(f"addiu v0, zero, {CLUT_VRAM_Y:#x}", CLUT_Y_SITE), "little"))
    done.append((CLUT_X_SITE,
                 f"CLUT 적재 위치 (288,224) -> ({CLUT_VRAM_X},{CLUT_VRAM_Y})"))

    for old, new in CLUT_IDS.items():
        ids = clut_id_sites(exe, old)
        want = CLUT_ID_COUNT[old]
        if len(ids) != want:
            raise ValueError(f"CLUT id {old:#x} 가 {len(ids)}곳이다. "
                             f"{want}곳이어야 한다")
        for addr in ids:
            word = exe.word(addr)
            exe.put_word(addr, (word & ~0xFFFF) | new)
        done.append((ids[0], f"CLUT id {len(ids)}곳 {old:#x} -> {new:#x}"))

    sites = width_sites(exe)
    if len(sites) != 7:
        raise ValueError(f"뱅크1 폭표 참조가 {len(sites)}곳이다. 7곳이어야 한다")
    for addr in sites:
        word = exe.word(addr)
        exe.put_word(addr, (word & ~0xFFFF) | BANK1_TABLE_NEW)
    done.append((sites[0], f"뱅크1 폭표 {len(sites)}곳 -> 통합표 +441"))

    if shadow:
        _install_shadow(exe, done)

    for branch, tp, idx, off, prim in DRAW_SITES:
        code = assemble(DRAW_PATCH.format(tp=tp, idx=idx, off=off, prim=prim),
                        branch)
        if len(code) != 24:
            raise ValueError(f"{branch:#x}: 패치가 {len(code)}바이트다. 24 여야 한다")
        for i in range(6):
            exe.put_word(branch + i * 4,
                         int.from_bytes(code[i * 4:i * 4 + 4], "little"))
        done.append((branch, "tpage 분기 -> CLUT 에 뱅크비트 더하기"))
        if show:
            print(f"\n  {branch:#010x} 새 6슬롯")
            for i in range(6):
                a = branch + i * 4
                print(f"    {a:#010x}  {exe.word(a):08x}  "
                      f"{decode(exe.word(a), a)}")
    return done


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exe", type=Path, default=Path("work/disc1/SLPS_018.80"))
    parser.add_argument("--output", type=Path,
                        default=Path("work/patch-4plane/SLPS_018.80"))
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--no-shadow", action="store_true",
                        help="그림자를 빼고 만든다 (실기 문제 가르기용)")
    args = parser.parse_args()

    try:
        exe = Exe(str(args.exe))
        done = apply(exe, args.show, not args.no_shadow)
    except (ValueError, FileNotFoundError) as error:
        print(error, file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(bytes(exe.data))
    before = Exe(str(args.exe))
    changed = sum(1 for i in range(0, exe.size, 4)
                  if before.data[exe.HEADER + i:exe.HEADER + i + 4]
                  != exe.data[exe.HEADER + i:exe.HEADER + i + 4])
    print(f"패치 {len(done)}건, 바뀐 워드 {changed}개")
    for addr, what in done:
        print(f"  {addr:#010x}  {what}")
    print(f"→ {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
