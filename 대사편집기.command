#!/bin/bash
# 대사 편집기를 Finder 에서 두 번 눌러 연다.
#
# `scripts/dialogue_editor.py` 는 워크시트 CSV 와 필드 번호를 인자로 받는다.
# 매번 터미널에서 경로를 치기 번거로워 이 파일을 둔다. 워크시트가 여럿이면
# 목록을 보여 주고 고르게 한다.
#
# 파일명에서 필드 번호를 읽는다 — `f293-worksheet.csv` -> 293.

cd "$(dirname "$0")" || exit 1

echo "FF8 한국어화 — 대사 편집기"
echo

# --- 워크시트 찾기 -----------------------------------------------------------
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

# --- 고르기 -------------------------------------------------------------------
if [ ${#sheets[@]} -eq 1 ]; then
  pick="${sheets[0]}"
else
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
  pick="${sheets[$((n - 1))]}"
fi

# --- 필드 번호는 파일명에서 읽는다 --------------------------------------------
base="$(basename "$pick")"
field="$(echo "$base" | sed -n 's/^f\([0-9]\{1,\}\).*/\1/p')"

echo
echo "  워크시트  $pick"
if [ -n "$field" ]; then
  echo "  필드      $field"
  echo
  python3 scripts/dialogue_editor.py "$pick" --field "$field"
else
  echo "  필드      (파일명에서 못 읽었다 — 없이 연다)"
  echo
  python3 scripts/dialogue_editor.py "$pick"
fi

status=$?
echo
if [ $status -ne 0 ]; then
  echo "편집기가 오류로 끝났다 (종료코드 $status)."
  read -r -p "엔터를 누르면 닫는다."
fi
