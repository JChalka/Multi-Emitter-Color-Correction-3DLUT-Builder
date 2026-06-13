from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import numpy as np
from numpy.lib.format import open_memmap


def axis_values(grid_size: int, sample_scale: float) -> np.ndarray:
	return np.linspace(0.0, float(sample_scale), int(grid_size), dtype=np.float64)


def write_comparison_csv(rows: list[dict[str, Any]], output_path: Path) -> None:
	if not rows:
		return
	with output_path.open("w", newline="", encoding="utf-8") as handle:
		writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
		writer.writeheader()
		writer.writerows(rows)


def write_utilization_csv(meta: list[dict], used_anchor_set: set[int], output_path: Path) -> None:
	with output_path.open("w", newline="", encoding="utf-8") as handle:
		fieldnames = [
			"capture_index",
			"used",
			"r16",
			"g16",
			"b16",
			"w16",
			"X",
			"Y",
			"Z",
			"n_averaged",
			"example_name",
		]
		writer = csv.DictWriter(handle, fieldnames=fieldnames)
		writer.writeheader()
		for index, row in enumerate(meta):
			writer.writerow({"capture_index": index, "used": index in used_anchor_set, **row})


def format_header_u16_entries(values: np.ndarray, values_per_line: int = 12) -> str:
	chunks: list[str] = []
	for start in range(0, values.size, values_per_line):
		end = min(start + values_per_line, values.size)
		chunks.append("    " + ", ".join(str(int(value)) for value in values[start:end]))
	return ",\n".join(chunks)


def write_rgbw_header(cube: np.ndarray, output_path: Path, lut_name: str, args: Any, source_grid_size: int) -> None:
	quantized = np.clip(np.round(cube), 0, 65535).astype(np.uint16)
	flat = quantized.reshape(-1, 4)
	entry_count = int(flat.shape[0])
	guard = f"HYPERHDR_{output_path.stem.upper()}_H".replace("-", "_")
	lines = [
		"// Auto-generated RGBW LUT header",
		f"// LUT name: {lut_name}",
		f"// Source solved grid size: {source_grid_size}",
		"",
		f"#ifndef {guard}",
		f"#define {guard}",
		"",
		"#include <stdint.h>",
		"",
		"#ifdef __AVR__",
		"  #include <avr/pgmspace.h>",
		"#elif defined(ESP32) || defined(ESP8266)",
		"  #include <pgmspace.h>",
		"#elif !defined(PROGMEM)",
		"  #define PROGMEM",
		"#endif",
		"",
		"static const uint32_t RGBW_LUT_FORMAT_VERSION = 1;",
		f"static const uint32_t RGBW_LUT_SOURCE_GRID_SIZE = {source_grid_size};",
		f"static const uint32_t RGBW_LUT_GRID_SIZE = {quantized.shape[0]};",
		f"static const uint32_t RGBW_LUT_ENTRY_COUNT = {entry_count};",
		"static const uint8_t RGBW_LUT_IS_SAMPLED_3D_GRID = 1;",
		"static const uint32_t RGBW_LUT_AXIS_MIN = 0;",
		f"static const uint32_t RGBW_LUT_AXIS_MAX = {int(round(args.sample_scale))};",
		"",
		f"static const uint16_t RGBW_LUT_R[{entry_count}] PROGMEM = {{",
		format_header_u16_entries(flat[:, 0]),
		"};",
		"",
		f"static const uint16_t RGBW_LUT_G[{entry_count}] PROGMEM = {{",
		format_header_u16_entries(flat[:, 1]),
		"};",
		"",
		f"static const uint16_t RGBW_LUT_B[{entry_count}] PROGMEM = {{",
		format_header_u16_entries(flat[:, 2]),
		"};",
		"",
		f"static const uint16_t RGBW_LUT_W[{entry_count}] PROGMEM = {{",
		format_header_u16_entries(flat[:, 3]),
		"};",
		"",
		f"#endif  // {guard}",
		"",
	]
	output_path.write_text("\n".join(lines), encoding="utf-8")


def save_lut_npy(cube: np.ndarray, output_path: Path) -> None:
	quantized = np.clip(np.round(cube), 0, 65535).astype(np.uint16)
	memmap = open_memmap(output_path, mode="w+", dtype=np.uint16, shape=quantized.shape)
	memmap[:] = quantized
	del memmap


__all__ = [
	"axis_values",
	"format_header_u16_entries",
	"save_lut_npy",
	"write_comparison_csv",
	"write_rgbw_header",
	"write_utilization_csv",
]
