from __future__ import annotations

import numpy as np


PRIMARIES_XY: dict[str, np.ndarray] = {
	"R": np.array([0.6853, 0.3147]),
	"G": np.array([0.1379, 0.7480]),
	"B": np.array([0.1295, 0.0663]),
	"W": np.array([0.3299, 0.3582]),
}

MAX_Y: dict[str, float] = {
	"R": 149.658631,
	"G": 563.961804,
	"B": 129.540105,
	"W": 1511.803150,
}

D65_XY = np.array([0.3127, 0.3290], dtype=np.float64)


def xy_Y_to_XYZ(xy: np.ndarray, Y: float) -> np.ndarray:
	x, y = float(xy[0]), float(xy[1])
	return np.array([(x / y) * Y, Y, ((1.0 - x - y) / y) * Y], dtype=np.float64)


def XYZ_to_xy(XYZ: np.ndarray) -> np.ndarray:
	total = float(np.sum(XYZ))
	if total < 1e-12:
		return D65_XY.copy()
	return np.asarray(XYZ[:2], dtype=np.float64) / total


def _build_gamut_matrix(primaries_xy: dict[str, np.ndarray], white_xy: np.ndarray) -> np.ndarray:
	def _xy_to_xyz1(xy: np.ndarray) -> np.ndarray:
		x, y = float(xy[0]), float(xy[1])
		return np.array([x / y, 1.0, (1.0 - x - y) / y], dtype=np.float64)

	primaries = np.column_stack([_xy_to_xyz1(primaries_xy[channel]) for channel in "RGB"])
	white_xyz = _xy_to_xyz1(white_xy)
	scales = np.linalg.solve(primaries, white_xyz)
	return primaries * scales


def _eotf_srgb(values: np.ndarray) -> np.ndarray:
	normalized = values / 255.0
	return np.where(normalized <= 0.04045, normalized / 12.92, ((normalized + 0.055) / 1.055) ** 2.4)


def _eotf_gamma(gamma: float):
	def _inner(values: np.ndarray) -> np.ndarray:
		return (values / 255.0) ** gamma

	return _inner


REC709_PRIMARIES = {"R": np.array([0.6400, 0.3300]), "G": np.array([0.3000, 0.6000]), "B": np.array([0.1500, 0.0600])}
REC2020_PRIMARIES = {"R": np.array([0.7080, 0.2920]), "G": np.array([0.1700, 0.7970]), "B": np.array([0.1310, 0.0460])}
DCI_P3_PRIMARIES = {"R": np.array([0.6800, 0.3200]), "G": np.array([0.2650, 0.6900]), "B": np.array([0.1500, 0.0600])}
ADOBE_RGB_PRIMARIES = {"R": np.array([0.6400, 0.3300]), "G": np.array([0.2100, 0.7100]), "B": np.array([0.1500, 0.0600])}

NAMED_GAMUTS: dict[str, tuple[np.ndarray, object, str]] = {
	"native": (_build_gamut_matrix(PRIMARIES_XY, D65_XY), _eotf_gamma(1.0), "Native LED primaries, D65 white, linear"),
	"rec709": (_build_gamut_matrix(REC709_PRIMARIES, D65_XY), _eotf_srgb, "Rec.709/sRGB primaries, D65"),
	"rec2020": (_build_gamut_matrix(REC2020_PRIMARIES, D65_XY), _eotf_gamma(2.4), "Rec.2020 primaries, D65"),
	"dci-p3": (_build_gamut_matrix(DCI_P3_PRIMARIES, D65_XY), _eotf_gamma(2.6), "Display P3 primaries, D65"),
	"adobe-rgb": (_build_gamut_matrix(ADOBE_RGB_PRIMARIES, D65_XY), _eotf_gamma(2.2), "Adobe RGB primaries, D65"),
}

VALID_GAMUTS = list(NAMED_GAMUTS.keys())
PRIMARY_XYZ: dict[str, np.ndarray] = {channel: xy_Y_to_XYZ(xy, MAX_Y[channel]) for channel, xy in PRIMARIES_XY.items()}
SUB_GAMUTS: list[tuple[str, str, str]] = [("R", "G", "W"), ("R", "B", "W"), ("B", "G", "W")]


def _solve_subgamut_channels(channels: tuple[str, str, str], target_xyz: np.ndarray) -> np.ndarray | None:
	matrix = np.column_stack([PRIMARY_XYZ[channel] for channel in channels])
	try:
		weights = np.linalg.solve(matrix, target_xyz)
	except np.linalg.LinAlgError:
		return None
	if np.any(weights < -1e-6):
		return None
	return np.maximum(weights, 0.0)


def _compute_scale_factor() -> tuple[float, tuple[str, str, str]]:
	d65_xyz = xy_Y_to_XYZ(D65_XY, 1.0)
	for subgamut in SUB_GAMUTS:
		weights = _solve_subgamut_channels(subgamut, d65_xyz)
		if weights is not None:
			return (1.0 / float(np.max(weights))) if float(np.max(weights)) > 0 else 1.0, subgamut
	raise RuntimeError("D65 white point is not within any LED sub-gamut")


SCALE_K, WHITE_SUB_GAMUT = _compute_scale_factor()


def input_to_XYZ(rgb_u8, gamut: str = "native", input_transfer: str = "gamut") -> np.ndarray:
	rgb = np.asarray(rgb_u8, dtype=np.float64)
	matrix, eotf, _description = NAMED_GAMUTS[gamut]
	if input_transfer == "linear":
		linear = np.clip(rgb / 255.0, 0.0, 1.0)
	elif input_transfer == "gamut":
		linear = eotf(rgb)  # type: ignore[operator]
	else:
		raise ValueError(f"Unknown input_transfer: {input_transfer!r}")
	return (matrix @ linear) * SCALE_K


def apply_input_transfer_normalized(values: np.ndarray, gamut: str, input_transfer: str = "linear") -> np.ndarray:
	normalized = np.clip(np.asarray(values, dtype=np.float64), 0.0, 1.0)
	if input_transfer == "linear":
		return normalized
	if input_transfer != "gamut":
		raise ValueError(f"Unknown input_transfer: {input_transfer!r}")
	if gamut == "rec709":
		return np.where(normalized <= 0.04045, normalized / 12.92, ((normalized + 0.055) / 1.055) ** 2.4)
	if gamut == "rec2020":
		return normalized ** 2.4
	if gamut == "dci-p3":
		return normalized ** 2.6
	if gamut == "adobe-rgb":
		return normalized ** 2.2
	return normalized


def encode_linear_to_normalized_source(values: np.ndarray, gamut: str, input_transfer: str = "linear") -> np.ndarray:
	normalized = np.clip(np.asarray(values, dtype=np.float64), 0.0, 1.0)
	if input_transfer == "linear":
		return normalized
	if input_transfer != "gamut":
		raise ValueError(f"Unknown input_transfer: {input_transfer!r}")
	if gamut == "rec709":
		return np.where(normalized <= 0.0031308, normalized * 12.92, 1.055 * np.power(normalized, 1.0 / 2.4) - 0.055)
	if gamut == "rec2020":
		return np.power(normalized, 1.0 / 2.4)
	if gamut == "dci-p3":
		return np.power(normalized, 1.0 / 2.6)
	if gamut == "adobe-rgb":
		return np.power(normalized, 1.0 / 2.2)
	return normalized


def input_linear_to_XYZ(linear_rgb: np.ndarray, gamut: str = "native") -> np.ndarray:
	linear = np.clip(np.asarray(linear_rgb, dtype=np.float64), 0.0, 1.0)
	matrix, _eotf, _description = NAMED_GAMUTS[gamut]
	return (matrix @ linear) * SCALE_K


__all__ = [
	"ADOBE_RGB_PRIMARIES",
	"DCI_P3_PRIMARIES",
	"D65_XY",
	"MAX_Y",
	"NAMED_GAMUTS",
	"PRIMARY_XYZ",
	"PRIMARIES_XY",
	"REC2020_PRIMARIES",
	"REC709_PRIMARIES",
	"SCALE_K",
	"SUB_GAMUTS",
	"VALID_GAMUTS",
	"WHITE_SUB_GAMUT",
	"XYZ_to_xy",
	"apply_input_transfer_normalized",
	"encode_linear_to_normalized_source",
	"input_linear_to_XYZ",
	"input_to_XYZ",
	"xy_Y_to_XYZ",
]
