from __future__ import annotations

import numpy as np

from .gamuts import PRIMARIES_XY, PRIMARY_XYZ, SUB_GAMUTS
from . import simplex


def barycentric_2d(point: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray | None:
	return simplex.barycentric_2d(point, a, b, c)


def xy_in_triangle(point: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray, eps: float = 1e-6) -> bool:
	return simplex.xy_in_triangle(point, a, b, c, eps=eps)


def find_sub_gamut(xy: np.ndarray) -> tuple[str, str, str] | None:
	for subgamut in SUB_GAMUTS:
		if xy_in_triangle(xy, PRIMARIES_XY[subgamut[0]], PRIMARIES_XY[subgamut[1]], PRIMARIES_XY[subgamut[2]]):
			return subgamut
	return None


def solve_xyz(ch_a: str, ch_b: str, ch_c: str, target_xyz: np.ndarray) -> np.ndarray | None:
	matrix = np.column_stack([PRIMARY_XYZ[ch_a], PRIMARY_XYZ[ch_b], PRIMARY_XYZ[ch_c]])
	return simplex.solve_linear_system(matrix, target_xyz)


__all__ = ["barycentric_2d", "find_sub_gamut", "solve_xyz", "xy_in_triangle"]
