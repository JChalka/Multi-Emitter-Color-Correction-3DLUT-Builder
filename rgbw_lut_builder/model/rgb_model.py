from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import gamuts, projection, simplex


@dataclass(frozen=True)
class RGBModelConfig:
	gamut: str = "native"
	input_transfer: str = "linear"
	sample_scale: float = 65535.0


def solve_rgb_only(rgb_linear: np.ndarray, config: RGBModelConfig | None = None) -> np.ndarray:
	active_config = config or RGBModelConfig()
	linear = np.clip(np.asarray(rgb_linear, dtype=np.float64), 0.0, 1.0)
	target_xyz = gamuts.input_linear_to_XYZ(linear, gamut=active_config.gamut)
	matrix = np.column_stack([
		gamuts.PRIMARY_XYZ["R"],
		gamuts.PRIMARY_XYZ["G"],
		gamuts.PRIMARY_XYZ["B"],
	])
	weights = simplex.solve_linear_system(matrix, target_xyz)
	if weights is None:
		projected_xyz, projected, _strict_frac = projection.project_target_xyz_to_led_hull(target_xyz)
		solve_target = projected_xyz if projected else target_xyz
		weights, _residual = simplex.nnls_solve(matrix, solve_target)
	if float(np.max(weights)) > 1.0:
		weights = weights / float(np.max(weights))
	return np.clip(np.asarray(weights, dtype=np.float64), 0.0, 1.0)


def solve_rgb16(r16: float, g16: float, b16: float, config: RGBModelConfig | None = None) -> tuple[int, int, int]:
	active_config = config or RGBModelConfig()
	scale = max(float(active_config.sample_scale), 1e-12)
	rgb_linear = np.array([r16, g16, b16], dtype=np.float64) / scale
	weights = solve_rgb_only(rgb_linear, active_config)
	return tuple(int(round(float(value) * 65535.0)) for value in weights)
