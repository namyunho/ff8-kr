#!/bin/bash
# 글자 편집기를 Finder 에서 두 번 눌러 연다.
#
# 자료원이 둘이다.
#   kernel     아이템·마법·어빌리티·전투커맨드·전투결과·못 바꾸는 캐릭터 이름
#              (scripts/text_editor.py — 자리보다 넘치는 462건을 줄이는 화면)
#   필드 대사   워크시트 CSV (scripts/dialogue_editor.py — 기존 편집기 그대로)
#
# 워크시트 파일명에서 필드 번호를 읽는다 — `f293-worksheet.csv` -> 293.

cd "$(dirname "$0")" || exit 1

echo "FF8 한국어화 — 글자 편집기"
echo

# --- 자료원 고르기 ------------------------------------------------------------
echo "무엇을 편집하나:"
echo "   1) kernel   아이템·마법·어빌리티·전투커맨드·전투결과"
echo "   2) 필드 대사  워크시트 CSV"
echo
read -r -p "번호 (엔터면 1번): " pick
[ -z "$pick" ] && pick=1

# --- kernel -------------------------------------------------------------------
if [ "$pick" = "1" ]; then
  if [ ! -f work/text/kernel-text-ko.json ]; then
    echo "번역 자료가 없다: work/text/kernel-text-ko.json"
    read -r -p "엔터를 누르면 닫는다."
    exit 1
  fi

  # 스키마가 아직 옛것이면 먼저 옮긴다. 멱등이라 여러 번 돌려도 된다.
  if ! python3 -c "import json,sys; sys.exit(0 if 'ko_draft' in json.load(open('work/text/kernel-text-ko.json'))[0] else 1)"; then
    echo "자료를 편집기 스키마로 옮긴다 (사본을 남긴다)..."
    echo
    python3 scripts/migrate_kernel_rows.py || {
      read -r -p "옮기지 못했다. 엔터를 누르면 닫는다."
      exit 1
    }
    echo
  fi

  python3 scripts/text_editor.py --source kernel
  status=$?
  if [ $status -ne 0 ]; then
    echo
    echo "편집기가 오류로 끝났다 (종료코드 $status)."
    read -r -p "엔터를 누르면 닫는다."
  fi
  exit $status
fi

# --- 필드 대사 ----------------------------------------------------------------
shopt -s nullglob
sheets=(work/text/*worksheet*.csv)
shopt -u nullglob

if [ ${#sheets[@]} -eq 0 ]; then
  echo "워크시트가 없다: work/text/*worksheet*.csv"
  echo
  echo "먼저 만든다:"
  echo "  python3 scripts/extract_field_text.py --worksheet 293"
  echo
  read -r -p "엔터를 누르면 닫는다."
  exit 1
fi

if [ ${#sheets[@]} -eq 1 ]; then
  sheet="${sheets[0]}"
else
  echo
  echo "워크시트를 고른다:"
  for i in "${!sheets[@]}"; do
    printf "  %2d) %s\n" "$((i + 1))" "${sheets[$i]}"
  done
  echo
  read -r -p "번호 (엔터면 1번): " n
  [ -z "$n" ] && n=1
  if ! [[ "$n" =~ ^[0-9]+$ ]] || [ "$n" -lt 1 ] || [ "$n" -gt ${#sheets[@]} ]; then
    echo "잘못된 번호다."
    read -r -p "엔터를 누르면 닫는다."
    exit 1
  fi
  sheet="${sheets[$((n - 1))]}"
fi

base="$(basename "$sheet")"
field="$(echo "$base" | sed -n 's/^f\([0-9]\{1,\}\).*/\1/p')"

echo
echo "  워크시트  $sheet"
if [ -n "$field" ]; then
  echo "  필드      $field"
  echo
  python3 scripts/dialogue_editor.py "$sheet" --field "$field"
else
  echo "  필드      (파일명에서 못 읽었다 — 없이 연다)"
  echo
  python3 scripts/dialogue_editor.py "$sheet"
fi

status=$?
echo
if [ $status -ne 0 ]; then
  echo "편집기가 오류로 끝났다 (종료코드 $status)."
  read -r -p "엔터를 누르면 닫는다."
fi
