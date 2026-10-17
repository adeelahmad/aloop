# Spec — `duration-parse`

A small Rust library that parses a human duration string into a total number of
whole seconds.

## Goal

Expose `parse_duration(input: &str) -> Result<u64, ParseError>` that converts a
compact duration string into seconds.

## Supported syntax

- Unit-suffixed integer terms, concatenated, e.g. `"1h30m"`, `"45s"`, `"2h"`, `"90m"`.
- Units: `h` (hours), `m` (minutes), `s` (seconds). Each unit may appear at most once.
- Whitespace between terms is allowed and ignored: `"1h 30m"` == `"1h30m"`.

## Acceptance criteria

1. `parse_duration("45s")` returns `Ok(45)`.
2. `parse_duration("1h30m")` returns `Ok(5400)`.
3. `parse_duration("2h")` returns `Ok(7200)`.
4. An empty string returns `Err(ParseError::Empty)`.
5. An unknown unit (e.g. `"10d")`) returns `Err(ParseError::UnknownUnit('d'))`.
6. A repeated unit (e.g. `"1h2h"`) returns `Err(ParseError::DuplicateUnit('h'))`.
7. A term with no digits (e.g. `"h"`) returns `Err(ParseError::MissingValue)`.

## Out of scope

- Days/weeks/years, fractional values (`"1.5h"`), and locale-aware text
  ("one hour"). Reject these via the error path above rather than supporting them.

## Constraints

- No external crates — standard library only.
- `parse_duration` must not panic on any input; all failure modes return `Err`.
