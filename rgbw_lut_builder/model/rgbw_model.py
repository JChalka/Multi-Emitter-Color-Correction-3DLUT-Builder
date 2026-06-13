from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..legacy import xy_target_rgbw_model as legacy_model
from . import gamuts, projection, simplex, topology
from .wx_modes import DEFAULT_WX_MODE, DEFAULT_WX_RADIAL_TARGET_POSITION, normalize_lut_method, resolve_wx_mode


@dataclass(frozen=True)
class RGBWModelConfig:
	gamut: str = "native"
	input_transfer: str = "linear"
	sample_scale: float = 65535.0
	method: str = "strict_subgamut"
	wx_mode: str = DEFAULT_WX_MODE
	wx_radial_target_position: float = DEFAULT_WX_RADIAL_TARGET_POSITION


def solve_strict_rgbw(rgb_linear: np.ndarray, config: RGBWModelConfig | None = None) -> np.ndarray:
	active_config = config or RGBWModelConfig()
	linear = np.clip(np.asarray(rgb_linear, dtype=np.float64), 0.0, 1.0)
	target_xyz = gamuts.input_linear_to_XYZ(linear, gamut=active_config.gamut)
	projected_xyz, projected, strict_frac = projection.project_target_xyz_to_led_hull(target_xyz)
	if projected:
		return strict_frac
	target_xy = gamuts.XYZ_to_xy(target_xyz)

	subgamut = topology.find_sub_gamut(target_xy)
	weights: np.ndarray | None = None
	if subgamut is not None:
		weights = topology.solve_xyz(subgamut[0], subgamut[1], subgamut[2], target_xyz)

	if subgamut is None or weights is None:
		best_residual = np.inf
		best_subgamut: tuple[str, str, str] | None = None
		best_weights: np.ndarray | None = None
		for candidate in gamuts.SUB_GAMUTS:
			matrix = np.column_stack([gamuts.PRIMARY_XYZ[channel] for channel in candidate])
			candidate_weights, residual = simplex.nnls_solve(matrix, target_xyz)
			if residual < best_residual:
				best_residual = residual
				best_subgamut = candidate
				best_weights = candidate_weights
		subgamut = best_subgamut
		weights = best_weights

	output = np.zeros(4, dtype=np.float64)
	if subgamut is None or weights is None:
		return output
	max_weight = float(np.max(weights))
	if max_weight > 1.0:
		weights = weights / max_weight
	for channel, weight in zip(subgamut, weights):
		output["RGBW".index(channel)] = float(weight)
	return np.clip(output, 0.0, 1.0)


def solve_wx_rgbw(rgb_linear: np.ndarray, config: RGBWModelConfig | None = None) -> np.ndarray:
	active_config = config or RGBWModelConfig()
	linear = np.clip(np.asarray(rgb_linear, dtype=np.float64), 0.0, 1.0)
	return legacy_model._solve_wx_fraction_from_linear(
		linear,
		gamut=active_config.gamut,
		wx_mode=resolve_wx_mode(active_config.method, active_config.wx_mode),
		wx_radial_target_position=float(active_config.wx_radial_target_position),
	)


def solve_rgbw_model(rgb_linear: np.ndarray, config: RGBWModelConfig | None = None) -> np.ndarray:
	active_config = config or RGBWModelConfig()
	method = normalize_lut_method(active_config.method)
	if method == "strict_subgamut":
		return solve_strict_rgbw(rgb_linear, active_config)
	if method == "wx":
		return solve_wx_rgbw(rgb_linear, active_config)
	raise ValueError(f"RGBW model does not support method={active_config.method!r}")


def solve_rgbw16(r16: float, g16: float, b16: float, config: RGBWModelConfig | None = None) -> tuple[int, int, int, int]:
	active_config = config or RGBWModelConfig()
	scale = max(float(active_config.sample_scale), 1e-12)
	rgb_linear = np.array([r16, g16, b16], dtype=np.float64) / scale
	weights = solve_rgbw_model(rgb_linear, active_config)
	return tuple(int(round(float(value) * 65535.0)) for value in weights)
