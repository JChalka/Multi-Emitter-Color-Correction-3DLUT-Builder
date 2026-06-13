from __future__ import annotations

import numpy as np

from . import gamuts, simplex


def project_target_xyz_to_led_hull(target_xyz: np.ndarray) -> tuple[np.ndarray, bool, np.ndarray]:
	target_xyz = np.asarray(target_xyz, dtype=np.float64)
	out_zero = np.zeros(4, dtype=np.float64)
	if not np.isfinite(target_xyz).all() or float(target_xyz[1]) <= 1e-12:
		return target_xyz, False, out_zero

	target_xy = gamuts.XYZ_to_xy(target_xyz)
	in_hull = simplex.xy_in_triangle(
		target_xy,
		gamuts.PRIMARIES_XY["R"],
		gamuts.PRIMARIES_XY["G"],
		gamuts.PRIMARIES_XY["B"],
		eps=1e-9,
	)
	if in_hull:
		return target_xyz, False, out_zero

	best_frac = np.zeros(4, dtype=np.float64)
	best_xyz = np.zeros(3, dtype=np.float64)
	best_residual = np.inf
	for tri in gamuts.SUB_GAMUTS:
		matrix = np.column_stack([gamuts.PRIMARY_XYZ[channel] for channel in tri])
		weights, residual = simplex.nnls_solve(matrix, target_xyz)
		weights = np.maximum(np.asarray(weights, dtype=np.float64), 0.0)
		max_weight = float(np.max(weights)) if weights.size else 0.0
		if max_weight > 1.0:
			weights = weights / max_weight
		frac = np.zeros(4, dtype=np.float64)
		for channel, value in zip(tri, weights):
			frac["RGBW".index(channel)] = float(value)
		xyz = simplex.rgbw_fraction_to_xyz(frac)
		if not np.isfinite(xyz).all() or float(xyz[1]) <= 1e-12:
			continue
		residual_value = float(residual) if np.isfinite(residual) else float("inf")
		if residual_value < best_residual:
			best_residual = residual_value
			best_frac = frac
			best_xyz = xyz

	if float(best_xyz[1]) <= 1e-12:
		return target_xyz, False, out_zero
	projected_xy = gamuts.XYZ_to_xy(best_xyz)
	return gamuts.xy_Y_to_XYZ(projected_xy, 1.0), True, best_frac


__all__ = ["project_target_xyz_to_led_hull"]
