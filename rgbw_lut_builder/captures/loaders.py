from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from .validators import is_ok, safe_float, safe_int


FAMILY_DEFS: list[tuple[str, int, bool]] = [
	("r", 0b001, False),
	("g", 0b010, False),
	("b", 0b100, False),
	("w", 0b000, True),
	("rg", 0b011, False),
	("rb", 0b101, False),
	("gb", 0b110, False),
	("rw", 0b001, True),
	("gw", 0b010, True),
	("bw", 0b100, True),
	("rgb", 0b111, False),
	("rgw", 0b011, True),
	("rbw", 0b101, True),
	("gbw", 0b110, True),
	("rgbw", 0b111, True),
]


def load_captures(input_dir: Path) -> tuple[np.ndarray, np.ndarray, int, list[dict]]:
	"""Load all ok=True captures from input_dir and deduplicate by drive tuple."""
	channels = ("r16", "g16", "b16", "w16")
	buckets: dict[tuple[int, int, int, int], list[np.ndarray]] = {}
	bucket_names: dict[tuple[int, int, int, int], list[str]] = {}
	raw_count = 0

	for csv_path in sorted(Path(input_dir).glob("*.csv")):
		with csv_path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
			for row in csv.DictReader(handle):
				if not is_ok(row.get("ok")):
					continue
				drives = tuple(safe_int(row.get(channel)) for channel in channels)
				if sum(drives) <= 0:
					continue
				xyz = np.array(
					[safe_float(row.get("X")), safe_float(row.get("Y")), safe_float(row.get("Z"))],
					dtype=np.float64,
				)
				if not np.isfinite(xyz).all() or xyz[1] <= 0.0:
					continue
				raw_count += 1
				buckets.setdefault(drives, []).append(xyz)
				bucket_names.setdefault(drives, []).append(str(row.get("name", "")))

	if not buckets:
		raise RuntimeError(f"No valid captures found in {input_dir}")

	xyz_rows: list[np.ndarray] = []
	rgbw_rows: list[np.ndarray] = []
	meta: list[dict] = []
	for drives in sorted(buckets):
		xyz_stack = np.stack(buckets[drives], axis=0)
		xyz_mean = xyz_stack.mean(axis=0)
		xyz_rows.append(xyz_mean)
		rgbw_rows.append(np.asarray(drives, dtype=np.float64))
		meta.append(
			{
				"r16": drives[0],
				"g16": drives[1],
				"b16": drives[2],
				"w16": drives[3],
				"X": float(xyz_mean[0]),
				"Y": float(xyz_mean[1]),
				"Z": float(xyz_mean[2]),
				"n_averaged": len(buckets[drives]),
				"example_name": bucket_names[drives][0],
			}
		)

	return np.array(xyz_rows, dtype=np.float64), np.array(rgbw_rows, dtype=np.float64), raw_count, meta


def build_family_capture_sets(
	xyz_points: np.ndarray,
	rgbw_points: np.ndarray,
	family_defs: list[tuple[str, int, bool]] | None = None,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
	"""Partition deduplicated captures by emitter family."""
	defs = family_defs or FAMILY_DEFS
	red, green, blue, white = (rgbw_points[:, index] for index in range(4))
	sets: dict[str, tuple[np.ndarray, np.ndarray]] = {}

	for family_key, rgb_mask, uses_white in defs:
		red_on = bool(rgb_mask & 0b001)
		green_on = bool(rgb_mask & 0b010)
		blue_on = bool(rgb_mask & 0b100)
		mask = np.ones(len(xyz_points), dtype=bool)
		mask &= (red > 0) if red_on else (red == 0)
		mask &= (green > 0) if green_on else (green == 0)
		mask &= (blue > 0) if blue_on else (blue == 0)
		mask &= (white > 0) if uses_white else (white == 0)
		if mask.any():
			sets[family_key] = (xyz_points[mask], rgbw_points[mask])
	return sets


__all__ = ["FAMILY_DEFS", "build_family_capture_sets", "load_captures"]
