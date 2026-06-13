from __future__ import annotations

import numpy as np

from .gamuts import PRIMARY_XYZ


def barycentric_2d(point: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray | None:
	matrix = np.array([[a[0] - c[0], b[0] - c[0]], [a[1] - c[1], b[1] - c[1]]], dtype=np.float64)
	rhs = np.asarray(point, dtype=np.float64) - c
	try:
		lam = np.linalg.solve(matrix, rhs)
	except np.linalg.LinAlgError:
		return None
	return np.array([lam[0], lam[1], 1.0 - lam[0] - lam[1]], dtype=np.float64)


def xy_in_triangle(point: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray, eps: float = 1e-6) -> bool:
	weights = barycentric_2d(point, a, b, c)
	return weights is not None and bool(np.all(weights >= -eps))


def solve_linear_system(matrix: np.ndarray, target: np.ndarray) -> np.ndarray | None:
	try:
		weights = np.linalg.solve(np.asarray(matrix, dtype=np.float64), np.asarray(target, dtype=np.float64))
	except np.linalg.LinAlgError:
		return None
	if np.any(weights < -1e-6):
		return None
	return np.maximum(weights, 0.0)


def nnls_solve(matrix: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, float]:
	try:
		from scipy.optimize import nnls as scipy_nnls

		return scipy_nnls(np.asarray(matrix, dtype=np.float64), np.asarray(target, dtype=np.float64))
	except ImportError:
		pass

	weights = np.zeros(np.asarray(matrix).shape[1], dtype=np.float64)
	matrix = np.asarray(matrix, dtype=np.float64)
	target = np.asarray(target, dtype=np.float64)
	for _ in range(500):
		grad = matrix.T @ (matrix @ weights - target)
		weights = np.maximum(weights - 0.01 * grad, 0.0)
	return weights, float(np.linalg.norm(matrix @ weights - target))


def rgbw_fraction_to_xyz(frac: np.ndarray) -> np.ndarray:
	weights = np.clip(np.asarray(frac, dtype=np.float64), 0.0, None)
	xyz = np.zeros(3, dtype=np.float64)
	for index, channel in enumerate("RGBW"):
		xyz += float(weights[index]) * PRIMARY_XYZ[channel]
	return xyz


def chromaticity_constraint_rows_for_columns(xy: np.ndarray, matrix: np.ndarray) -> np.ndarray:
	xy = np.asarray(xy, dtype=np.float64)
	matrix = np.asarray(matrix, dtype=np.float64)
	x, y = float(xy[0]), float(xy[1])
	return np.vstack([
		(1.0 - x) * matrix[0] - x * matrix[1] - x * matrix[2],
		-y * matrix[0] + (1.0 - y) * matrix[1] - y * matrix[2],
	])


__all__ = [
	"barycentric_2d",
	"chromaticity_constraint_rows_for_columns",
	"nnls_solve",
	"rgbw_fraction_to_xyz",
	"solve_linear_system",
	"xy_in_triangle",
]
