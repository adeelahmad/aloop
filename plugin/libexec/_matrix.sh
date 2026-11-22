#!/usr/bin/env bash
# _matrix.sh — the one parser of a standards.md gate matrix: the fenced command lines under a
# "Gate matrix" / "Cross-cutting gates" heading. The GREEN/RED/FINAL gates run exactly these
# lines; the standards role's stop gate refuses a standards.md where they come out empty.
standards_matrix_lines() {
  awk '
    /^#+.*([Gg]ate matrix|[Cc]ross-cutting gates)/ {insec=1; next}
    insec && /^#+[[:space:]]/                        {insec=0}
    insec && /^```/                                  {infence=!infence; next}
    insec && infence && NF                           {print}
  ' "$1"
}
