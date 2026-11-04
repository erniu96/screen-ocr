"""Reconstruct visual text rows from the verified wxocr rectangle contract.

This is a plain-text presentation layer, not a protocol decoder. It never edits
recognised letters, guesses unknown fields, or changes the original result.
The geometry policy is deliberately conservative: distant or substantially
overlapping boxes stay separate. Complex columns and rotated text need a richer
document-layout model; this module promises horizontal visual-row order only.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import statistics
import unicodedata


ALGORITHM = "geometry_rows_v1"


class LayoutError(ValueError):
    """A recognised fragment cannot be placed using its supplied rectangle."""


@dataclass(frozen=True)
class _Fragment:
    index: int
    text: str
    left: float
    top: float
    right: float
    bottom: float

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def character_pitch(self) -> float:
        # A CJK/full-width glyph occupies roughly two Latin character cells.
        # Combining marks do not add advance. This estimates spacing only;
        # it does not normalise or rewrite the recognised Unicode characters.
        units = sum(0 if unicodedata.combining(char) else
                    2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
                    for char in self.text)
        pitch = (self.right - self.left) / max(units, 1)
        return min(max(pitch, self.height * 0.15), self.height * 0.9)


def _fragments(result: dict) -> list[_Fragment]:
    lines = result["ocr_response"]
    if not isinstance(lines, list):
        raise LayoutError("ocr_response must be a list")
    fragments = []
    for index, line in enumerate(lines):
        text = line["text"]
        if not isinstance(text, str):
            raise LayoutError(f"Fragment {index} text must be a string")
        coordinates = [line[field] for field in ("left", "top", "right", "bottom")]
        if any(type(value) not in (int, float) or not math.isfinite(value)
               for value in coordinates):
            raise LayoutError(f"Fragment {index} has non-finite or non-numeric coordinates")
        left, top, right, bottom = coordinates
        if right <= left or bottom <= top:
            raise LayoutError(f"Fragment {index} has an empty or inverted rectangle")
        fragments.append(_Fragment(index, text, left, top, right, bottom))
    return fragments


def _same_row(first: _Fragment, second: _Fragment) -> bool:
    height = min(first.height, second.height)
    overlap = min(first.bottom, second.bottom) - max(first.top, second.top)
    return (overlap >= height * 0.6 and
            abs(first.center_y - second.center_y) <= height * 0.5)


def _rows(fragments: list[_Fragment]) -> list[list[_Fragment]]:
    rows: list[list[_Fragment]] = []
    for fragment in sorted(fragments, key=lambda item: (item.center_y, item.left, item.index)):
        # Every member must agree: pairwise checks prevent a tall/overlapping
        # box from transitively joining two distinct neighbouring text rows.
        matches = [row for row in rows if all(_same_row(fragment, member) for member in row)]
        if matches:
            closest = min(matches, key=lambda row:
                          abs(fragment.center_y - statistics.median(item.center_y for item in row)))
            closest.append(fragment)
        else:
            rows.append([fragment])
    return sorted(rows, key=lambda row: (statistics.median(item.center_y for item in row),
                                        min(item.left for item in row)))


def _separator(previous: _Fragment, current: _Fragment, gap: float) -> str:
    if not previous.text or not current.text:
        return ""
    if previous.text[-1].isspace() or current.text[0].isspace():
        return ""  # Keep the engine's existing boundary whitespace verbatim.
    # Adjacent word fragments have little or no gap; a visibly wider gap means
    # a word separator. No dictionary, case correction, or spelling repair.
    threshold = max(min(previous.character_pitch, current.character_pitch) * 0.5,
                    min(previous.height, current.height) * 0.12)
    return " " if gap >= threshold else ""


def reconstruct_text(result: dict) -> dict:
    """Return text and a traceable layout without modifying ``result``.

    ``source_indices`` are zero-based positions in the raw ``ocr_response``.
    A line joins only nearby, horizontally aligned fragments. Large gutters
    remain line breaks; narrow columns can still need a richer layout model.
    Output order is top-to-bottom then left-to-right, not column-major reading.
    """
    fragments = _fragments(result)
    output: list[dict] = []
    warnings: set[str] = set()

    def emit(group: list[_Fragment], separators: list[str]) -> None:
        text = group[0].text + "".join(separator + item.text
                                      for separator, item in zip(separators, group[1:], strict=True))
        output.append({
            "text": text,
            "source_indices": [item.index for item in group],
            "separators": list(separators),
            "left": min(item.left for item in group),
            "top": min(item.top for item in group),
            "right": max(item.right for item in group),
            "bottom": max(item.bottom for item in group),
        })

    for row in _rows(fragments):
        ordered = sorted(row, key=lambda item: (item.left, item.right, item.index))
        group = [ordered[0]]
        separators: list[str] = []
        for current in ordered[1:]:
            previous = group[-1]
            gap = current.left - previous.right
            height = min(previous.height, current.height)
            # A wide gutter can denote independent columns; overlapping boxes
            # can denote alternatives/rotation. Keep both rather than delete
            # characters or concatenate them into an invented phrase.
            if gap > height * 1.6 or gap < -height * 0.25:
                warnings.add("wide_gaps_kept_separate" if gap > 0 else "overlapping_boxes_kept_separate")
                emit(group, separators)
                group, separators = [current], []
            else:
                separators.append(_separator(previous, current, gap))
                group.append(current)
        emit(group, separators)

    return {
        "algorithm": ALGORITHM,
        "text": "\n".join(line["text"] for line in output),
        "source_fragment_count": len(fragments),
        "output_line_count": len(output),
        "lines": output,
        "warnings": sorted(warnings),
    }
