from __future__ import annotations

import math
import os
from typing import Any

import numpy as np


def available_memory_bytes() -> int | None:
	try:
		import psutil  # type: ignore

		return int(psutil.virtual_memory().available)
	except Exception:
		pass

	if os.name == "nt":
		try:
			import ctypes

			class MEMORYSTATUSEX(ctypes.Structure):
				_fields_ = [
					("dwLength", ctypes.c_ulong),
					("dwMemoryLoad", ctypes.c_ulong),
					("ullTotalPhys", ctypes.c_ulonglong),
					("ullAvailPhys", ctypes.c_ulonglong),
					("ullTotalPageFile", ctypes.c_ulonglong),
					("ullAvailPageFile", ctypes.c_ulonglong),
					("ullTotalVirtual", ctypes.c_ulonglong),
					("ullAvailVirtual", ctypes.c_ulonglong),
					("sullAvailExtendedVirtual", ctypes.c_ulonglong),
				]

			stat = MEMORYSTATUSEX()
			stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
			if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
				return int(stat.ullAvailPhys)
		except Exception:
			return None

	try:
		pages = os.sysconf("SC_AVPHYS_PAGES")
		page_size = os.sysconf("SC_PAGE_SIZE")
		return int(pages * page_size)
	except Exception:
		return None


def auto_measured_candidate_axis_cap(
	n_rows: int,
	requested_axis: int,
	*,
	args: Any | None = None,
	worker_count: int | None = None,
) -> int:
	requested_axis = max(1, int(requested_axis))
	if n_rows <= 0:
		return requested_axis

	hard_cap = int(getattr(args, "knn_max_candidate_axis", 160))
	min_axis = int(getattr(args, "knn_min_candidate_axis", 32))
	mem_fraction = float(getattr(args, "knn_memory_fraction", 0.50))
	bytes_per = float(getattr(args, "knn_bytes_per_row_candidate", 112.0))
	workers = int(worker_count or getattr(args, "workers", 0) or (os.cpu_count() or 1))

	hard_cap = max(1, hard_cap)
	min_axis = max(1, min_axis)
	mem_fraction = float(np.clip(mem_fraction, 0.05, 0.90))
	bytes_per = max(32.0, bytes_per)
	workers = max(1, workers)

	base_cap = min(requested_axis, hard_cap)
	avail = available_memory_bytes()
	if avail is None or avail <= 0:
		return max(1, min(base_cap, max(min_axis, base_cap)))

	per_worker_budget = max(1.0, (float(avail) * mem_fraction) / float(workers))
	mem_cap = max(1, int(per_worker_budget // (float(n_rows) * bytes_per)))
	cap = min(base_cap, mem_cap)
	if mem_cap >= min_axis and base_cap >= min_axis:
		cap = max(cap, min_axis)
	return max(1, cap)


def ratio_anchor_tile_shape(
	n_rows: int,
	requested_k: int,
	*,
	args: Any | None = None,
	worker_count: int | None = None,
) -> tuple[int, int, int]:
	n_rows = max(1, int(n_rows))
	requested_k = max(1, int(requested_k))
	mem_fraction = float(getattr(args, "knn_memory_fraction", 0.50))
	workers = int(worker_count or getattr(args, "workers", 0) or (os.cpu_count() or 1))
	mem_fraction = float(np.clip(mem_fraction, 0.05, 0.90))
	workers = max(1, workers)

	avail = available_memory_bytes()
	if avail is None or avail <= 0:
		return min(n_rows, max(1, int(math.ceil(n_rows / float(workers))))), requested_k, -1

	budget = max(1.0, (float(avail) * mem_fraction) / float(workers))

	f8 = np.dtype(np.float64).itemsize
	i8 = np.dtype(np.intp).itemsize
	b1 = np.dtype(np.bool_).itemsize
	row_state_bytes = ((4 + 3 + 4 + 3) * f8 + (7 * f8) + (3 * f8) + (3 * np.dtype(np.int32).itemsize) + (2 * b1))
	index_bytes_per_row = requested_k * i8
	bytes_per_row_k_tile = ((3 + 4) * f8 + (3 + 4) * f8 + (3 * f8) + (9 * f8) + (2 * b1) + i8)

	def estimate(row_tile: int, k_tile: int) -> float:
		return row_tile * row_state_bytes + row_tile * index_bytes_per_row + row_tile * k_tile * bytes_per_row_k_tile

	row_tile = n_rows
	while row_tile > 1 and estimate(row_tile, 1) > budget:
		row_tile = max(1, row_tile // 2)

	remaining = budget - (row_tile * row_state_bytes + row_tile * index_bytes_per_row)
	if remaining <= 0.0:
		k_tile = 1
	else:
		k_tile = int(remaining // max(1.0, row_tile * bytes_per_row_k_tile))
		k_tile = max(1, min(requested_k, k_tile))
	while k_tile < requested_k and estimate(row_tile, min(requested_k, k_tile * 2)) <= budget:
		k_tile = min(requested_k, k_tile * 2)
	return max(1, row_tile), max(1, k_tile), int(budget)


def summarize_delaunay_build(
	coarse_rows: list[dict],
	xyz_points: np.ndarray,
	rgbw_points: np.ndarray,
	used_anchor_set: set[int],
	raw_count: int,
	args: Any,
	target_rgb_basis: np.ndarray,
	white_channel_xy: tuple[float, float],
	equal_rgb_xy: tuple[float, float],
	y_scale: float = 1.0,
) -> dict:
	del rgbw_points, used_anchor_set
	gains = np.array([row["white_gain_abs"] for row in coarse_rows], dtype=float)
	n_unique = len(xyz_points)
	return {
		"mode": "delaunay",
		"solver": "family_hull",
		"settings": {
			"input_dir": str(args.input_dir),
			"target_white_balance_mode": args.target_white_balance_mode,
			"sample_scale": args.sample_scale,
			"coarse_grid_size": args.coarse_grid_size,
			"full_grid_size": args.full_grid_size,
			"white_x": args.white_x,
			"white_y": args.white_y,
			"white_Y": args.white_Y,
			"y_scale": y_scale,
			"delta_e_tiebreak": getattr(args, "delta_e_tiebreak", 2.0),
			"chroma_gate": getattr(args, "chroma_gate", 15.0),
		},
		"build_mode": "delaunay",
		"basis_xyz_per_q16": {
			"r16": target_rgb_basis[:, 0].tolist(),
			"g16": target_rgb_basis[:, 1].tolist(),
			"b16": target_rgb_basis[:, 2].tolist(),
		},
		"capture_stats": {
			"raw_rows": raw_count,
			"unique_drive_states": n_unique,
			"duplicates_averaged": raw_count - n_unique,
		},
		"basis_sanity": {
			"equal_rgb_neutral_xy": list(equal_rgb_xy),
			"white_channel_xy": list(white_channel_xy),
			"reference_white_xy": [args.white_x, args.white_y],
		},
		"coarse_diagnostics": {
			"white_gain_p01": float(np.quantile(gains, 0.01)) if len(gains) > 0 else float("nan"),
			"white_gain_p10": float(np.quantile(gains, 0.10)) if len(gains) > 0 else float("nan"),
			"white_gain_p50": float(np.quantile(gains, 0.50)) if len(gains) > 0 else float("nan"),
			"white_gain_p90": float(np.quantile(gains, 0.90)) if len(gains) > 0 else float("nan"),
			"white_gain_p99": float(np.quantile(gains, 0.99)) if len(gains) > 0 else float("nan"),
		},
	}


__all__ = [
	"auto_measured_candidate_axis_cap",
	"available_memory_bytes",
	"ratio_anchor_tile_shape",
	"summarize_delaunay_build",
]
