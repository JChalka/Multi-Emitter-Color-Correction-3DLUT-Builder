from __future__ import annotations

import os
from typing import Any, Callable

import numpy as np
from scipy.spatial import Delaunay, cKDTree


def resolve_worker_count(requested_workers: int, grid_size: int, cpu_count: int | None = None) -> int:
	workers = max(1, int(requested_workers or (cpu_count or os.cpu_count() or 1)))
	return min(workers, max(1, int(grid_size)))


def build_delaunay_process_state(
	*,
	axis: np.ndarray,
	target_rgb_basis: np.ndarray,
	raw_rgb_basis: np.ndarray | None,
	family_capture_sets: dict | None,
	white_xyz_ref: np.ndarray,
	reference_white: Any,
	y_scale: float,
	args: Any,
	worker_count: int,
	feedback_candidate_model: dict | None,
	target_transform_matrix: np.ndarray | None = None,
) -> dict:
	family_capture_sets = family_capture_sets or {}
	return {
		"axis": axis,
		"target_rgb_basis": target_rgb_basis,
		"raw_rgb_basis": raw_rgb_basis if raw_rgb_basis is not None else target_rgb_basis,
		"target_transform_matrix": target_transform_matrix,
		"family_capture_xyz": {fk: value[0] for fk, value in family_capture_sets.items()},
		"family_capture_rgbw": {fk: value[1] for fk, value in family_capture_sets.items()},
		"white_xyz_ref": white_xyz_ref,
		"reference_white": reference_white,
		"y_scale": float(y_scale),
		"args": args,
		"worker_count": int(worker_count),
		"feedback_candidate_model": feedback_candidate_model,
	}


def initialize_delaunay_worker_state(
	state: dict,
	*,
	target_space_key: Callable[[np.ndarray, np.ndarray], np.ndarray],
	xyz_to_lab_vectorised: Callable[[np.ndarray, np.ndarray], np.ndarray],
	dual_pair_channels: dict[str, tuple[int, int]],
	neutral_family_keys: set[str] | tuple[str, ...],
	default_neutral_residual_weights: np.ndarray | None = None,
) -> dict:
	s = state.copy()

	raw_xyz = s.pop("family_capture_xyz", {})
	raw_rgbw = s.pop("family_capture_rgbw", {})

	family_tri: dict[str, Delaunay | None] = {}
	family_tree: dict[str, cKDTree | None] = {}
	family_key_tree: dict[str, cKDTree | None] = {}
	family_xyz: dict[str, np.ndarray] = {}
	family_rgbw: dict[str, np.ndarray] = {}

	for family_key in raw_xyz:
		xyz_values = raw_xyz[family_key]
		rgbw_values = raw_rgbw[family_key]
		family_xyz[family_key] = xyz_values
		family_rgbw[family_key] = rgbw_values
		family_key_tree[family_key] = cKDTree(target_space_key(xyz_values, s["white_xyz_ref"])) if len(xyz_values) > 0 else None
		if len(xyz_values) >= 5:
			try:
				family_tri[family_key] = Delaunay(xyz_values)
				family_tree[family_key] = cKDTree(xyz_values)
			except Exception:
				family_tri[family_key] = None
				family_tree[family_key] = cKDTree(xyz_values) if len(xyz_values) > 0 else None
		else:
			family_tri[family_key] = None
			family_tree[family_key] = cKDTree(xyz_values) if len(xyz_values) > 0 else None

	s["family_tri"] = family_tri
	s["family_tree"] = family_tree
	s["family_key_tree"] = family_key_tree
	s["family_xyz"] = family_xyz
	s["family_rgbw"] = family_rgbw

	mixed_w_xyz: list[np.ndarray] = []
	mixed_w_rgbw: list[np.ndarray] = []
	for family_key, xyz_values in family_xyz.items():
		rgbw_values = family_rgbw[family_key]
		keep = rgbw_values[:, 3] > 0
		if np.any(keep):
			mixed_w_xyz.append(xyz_values[keep])
			mixed_w_rgbw.append(rgbw_values[keep])
	if mixed_w_xyz:
		mixed_w_xyz_all = np.concatenate(mixed_w_xyz, axis=0)
		mixed_w_rgbw_all = np.concatenate(mixed_w_rgbw, axis=0)
		s["mixed_w_xyz"] = mixed_w_xyz_all
		s["mixed_w_rgbw"] = mixed_w_rgbw_all
		s["mixed_w_key_tree"] = cKDTree(target_space_key(mixed_w_xyz_all, s["white_xyz_ref"]))
	else:
		s["mixed_w_xyz"] = None
		s["mixed_w_rgbw"] = None
		s["mixed_w_key_tree"] = None

	white_ref = s["white_xyz_ref"]
	family_target_tree: dict[str, cKDTree] = {}
	family_target_xyz: dict[str, np.ndarray] = {}
	family_target_rgbw: dict[str, np.ndarray] = {}
	for family_key in dual_pair_channels:
		if family_key not in raw_xyz or len(raw_xyz[family_key]) == 0:
			continue
		xyz_values = raw_xyz[family_key]
		rgbw_values = raw_rgbw[family_key]
		lab_values = xyz_to_lab_vectorised(np.maximum(xyz_values, 0.0), white_ref)
		log_y = np.log(np.maximum(xyz_values[:, 1], 1e-6)).reshape(-1, 1)
		key_values = np.hstack([lab_values[:, 0:1] * 0.5, lab_values[:, 1:3], log_y * 2.0])
		family_target_tree[family_key] = cKDTree(key_values)
		family_target_xyz[family_key] = xyz_values
		family_target_rgbw[family_key] = rgbw_values
	s["family_target_tree"] = family_target_tree
	s["family_target_xyz"] = family_target_xyz
	s["family_target_rgbw"] = family_target_rgbw

	ref_sum = float(white_ref.sum())
	ref_x = float(white_ref[0]) / max(ref_sum, 1e-9)
	ref_y = float(white_ref[1]) / max(ref_sum, 1e-9)
	neutral_xyz_list: list[np.ndarray] = []
	neutral_rgbw_list: list[np.ndarray] = []
	for family_key in neutral_family_keys:
		if family_key not in raw_xyz or len(raw_xyz[family_key]) == 0:
			continue
		xyz_values = raw_xyz[family_key]
		rgbw_values = raw_rgbw[family_key]
		sums = xyz_values.sum(axis=1)
		safe = sums > 1e-9
		x = np.where(safe, xyz_values[:, 0] / np.maximum(sums, 1e-9), 0.0)
		y = np.where(safe, xyz_values[:, 1] / np.maximum(sums, 1e-9), 0.0)
		xy_dist = np.sqrt((x - ref_x) ** 2 + (y - ref_y) ** 2)
		keep = safe & (rgbw_values[:, 3] > 0) & (xy_dist <= 0.060)
		if keep.any():
			neutral_xyz_list.append(xyz_values[keep])
			neutral_rgbw_list.append(rgbw_values[keep])
	if neutral_xyz_list:
		neutral_xyz = np.concatenate(neutral_xyz_list, axis=0)
		neutral_rgbw = np.concatenate(neutral_rgbw_list, axis=0)
		sums = np.maximum(neutral_xyz.sum(axis=1), 1e-9)
		x = neutral_xyz[:, 0] / sums
		y = neutral_xyz[:, 1] / sums
		lab = xyz_to_lab_vectorised(np.maximum(neutral_xyz, 0.0), white_ref)
		log_y = np.log(np.maximum(neutral_xyz[:, 1], 1e-6))
		neutral_key = np.column_stack([x * 120.0, y * 120.0, lab[:, 0] * 0.03, lab[:, 1], lab[:, 2], log_y * 0.20])
		s["neutral_tree"] = cKDTree(neutral_key)
		s["neutral_xyz"] = neutral_xyz
		s["neutral_rgbw"] = neutral_rgbw

		sample_scale = float(getattr(s["args"], "sample_scale", 65535.0))
		full_w = neutral_rgbw[:, 3] >= (sample_scale * 0.98)
		if np.any(full_w):
			candidates = np.where(full_w)[0]
			xy_dist = np.sqrt((x[candidates] - ref_x) ** 2 + (y[candidates] - ref_y) ** 2)
			best = candidates[int(np.argmin(xy_dist))]
			residual = np.maximum(neutral_rgbw[best, :3] / max(sample_scale, 1.0), 0.0)
			residual_max = float(np.max(residual))
			if residual_max > 1e-6:
				weights = (residual_max + 0.02) / (residual + 0.02)
				weights = np.clip(weights, 1.0, 8.0)
				weights /= max(float(np.min(weights)), 1.0)
				s["neutral_residual_weights"] = weights.astype(np.float64)
			else:
				s["neutral_residual_weights"] = np.array([1.0, 4.0, 1.0], dtype=np.float64)
		else:
			s["neutral_residual_weights"] = np.array([1.0, 4.0, 1.0], dtype=np.float64)
	else:
		s["neutral_tree"] = None
		s["neutral_xyz"] = None
		s["neutral_rgbw"] = None
		if default_neutral_residual_weights is None:
			default_neutral_residual_weights = np.array([1.0, 4.0, 1.0], dtype=np.float64)
		s["neutral_residual_weights"] = np.asarray(default_neutral_residual_weights, dtype=np.float64)

	if "raw_rgb_basis" not in s:
		s["raw_rgb_basis"] = s["target_rgb_basis"]
	return s
