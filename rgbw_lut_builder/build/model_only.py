from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
from numpy.lib.format import open_memmap

from . import lut_writer as build_lut_writer
from ..legacy import xy_target_rgbw_model as legacy_model
from ..model.rgb_model import RGBModelConfig, solve_rgb16
from ..model.rgbw_model import RGBWModelConfig, solve_rgbw16
from ..model.wx_modes import DEFAULT_WX_MODE, DEFAULT_WX_RADIAL_TARGET_POSITION, VALID_WX_MODES, normalize_lut_method, resolve_wx_mode
from ..paths import DEFAULT_LUT_OUTPUT_DIR


def run_legacy_model_only_cli(argv: list[str] | None = None) -> None:
	original_argv = sys.argv[:]
	try:
		sys.argv = [original_argv[0], *(argv or [])]
		legacy_model.main()
	finally:
		sys.argv = original_argv


def solve_model_node(
	r16: float,
	g16: float,
	b16: float,
	*,
	model_family: str = "rgbw",
	method: str = "strict_subgamut",
	gamut: str = "native",
	input_transfer: str = "linear",
	wx_mode: str = DEFAULT_WX_MODE,
	wx_radial_target_position: float = DEFAULT_WX_RADIAL_TARGET_POSITION,
	sample_scale: float = 65535.0,
) -> dict[str, object]:
	if model_family == "rgb":
		config = RGBModelConfig(gamut=gamut, input_transfer=input_transfer, sample_scale=sample_scale)
		output = solve_rgb16(r16, g16, b16, config)
		return {
			"model_family": "rgb",
			"config": asdict(config),
			"input_rgb16": [int(round(r16)), int(round(g16)), int(round(b16))],
			"output_rgb16": list(output),
		}

	config = RGBWModelConfig(
		gamut=gamut,
		input_transfer=input_transfer,
		sample_scale=sample_scale,
		method=method,
		wx_mode=resolve_wx_mode(method, wx_mode),
		wx_radial_target_position=wx_radial_target_position,
	)
	output = solve_rgbw16(r16, g16, b16, config)
	return {
		"model_family": "rgbw",
		"normalized_method": normalize_lut_method(method),
		"config": asdict(config),
		"input_rgb16": [int(round(r16)), int(round(g16)), int(round(b16))],
		"output_rgbw16": list(output),
	}


def _resolve_rgbw_config(
	*,
	method: str,
	gamut: str,
	input_transfer: str,
	wx_mode: str,
	wx_radial_target_position: float,
	sample_scale: float,
) -> tuple[RGBWModelConfig, str, str | None]:
	normalized_method = normalize_lut_method(method)
	effective_wx_mode = resolve_wx_mode(method, wx_mode) if normalized_method == "wx" else None
	config = RGBWModelConfig(
		gamut=gamut,
		input_transfer=input_transfer,
		sample_scale=sample_scale,
		method=normalized_method,
		wx_mode=effective_wx_mode or DEFAULT_WX_MODE,
		wx_radial_target_position=wx_radial_target_position,
	)
	return config, normalized_method, effective_wx_mode


def _default_cube_basename(
	*,
	model_family: str,
	gamut: str,
	input_transfer: str,
	grid_size: int,
	normalized_method: str | None = None,
	effective_wx_mode: str | None = None,
) -> str:
	parts = ["model", model_family, gamut]
	if normalized_method:
		parts.append(normalized_method)
	if effective_wx_mode:
		parts.append(effective_wx_mode)
	parts.extend([f"xfer{input_transfer}", str(int(grid_size)), "16bit"])
	return "_".join(parts)


def build_model_lut_cube(
	*,
	output_dir: Path,
	grid_size: int = 33,
	model_family: str = "rgbw",
	method: str = "strict_subgamut",
	gamut: str = "native",
	input_transfer: str = "linear",
	wx_mode: str = DEFAULT_WX_MODE,
	wx_radial_target_position: float = DEFAULT_WX_RADIAL_TARGET_POSITION,
	sample_scale: float = 65535.0,
	basename: str | None = None,
	write_header: bool = False,
	header_grid_size: int = 0,
) -> dict[str, object]:
	output_dir = Path(output_dir)
	output_dir.mkdir(parents=True, exist_ok=True)
	grid_size = int(grid_size)
	if grid_size < 2:
		raise ValueError(f"Unsupported grid_size={grid_size}; expected >= 2")

	normalized_method: str | None = None
	effective_wx_mode: str | None = None
	if model_family == "rgb":
		config = RGBModelConfig(gamut=gamut, input_transfer=input_transfer, sample_scale=sample_scale)
		channel_order = ["R", "G", "B"]
		output_family = "RGB16"
		output_channels = 3
	else:
		config, normalized_method, effective_wx_mode = _resolve_rgbw_config(
			method=method,
			gamut=gamut,
			input_transfer=input_transfer,
			wx_mode=wx_mode,
			wx_radial_target_position=wx_radial_target_position,
			sample_scale=sample_scale,
		)
		channel_order = ["R", "G", "B", "W"]
		output_family = "RGBW16"
		output_channels = 4

	basename = basename or _default_cube_basename(
		model_family=model_family,
		gamut=gamut,
		input_transfer=input_transfer,
		grid_size=grid_size,
		normalized_method=normalized_method,
		effective_wx_mode=effective_wx_mode,
	)
	npy_path = output_dir / f"{basename}.npy"
	probe_csv_path = output_dir / f"{basename}_probes.csv"
	summary_path = output_dir / f"{basename}_summary.json"

	axis = build_lut_writer.axis_values(grid_size, sample_scale)
	cube = open_memmap(npy_path, mode="w+", dtype=np.uint16, shape=(grid_size, grid_size, grid_size, output_channels))
	t0 = time.time()
	progress_every = max(1, grid_size // 16)

	for ri, r16 in enumerate(axis):
		for gi, g16 in enumerate(axis):
			for bi, b16 in enumerate(axis):
				if model_family == "rgb":
					cube[ri, gi, bi, :] = np.asarray(solve_rgb16(r16, g16, b16, config), dtype=np.uint16)
				else:
					cube[ri, gi, bi, :] = np.asarray(solve_rgbw16(r16, g16, b16, config), dtype=np.uint16)
		if ri == 0 or (ri + 1) % progress_every == 0 or (ri + 1) == grid_size:
			elapsed = time.time() - t0
			print(f"  solved R slice {ri + 1}/{grid_size}  elapsed={elapsed:.1f}s", flush=True)

	cube.flush()

	probe_inputs = [
		("black", 0.0, 0.0, 0.0),
		("red", sample_scale, 0.0, 0.0),
		("green", 0.0, sample_scale, 0.0),
		("blue", 0.0, 0.0, sample_scale),
		("white", sample_scale, sample_scale, sample_scale),
		("white_half", sample_scale / 2.0, sample_scale / 2.0, sample_scale / 2.0),
	]
	probe_rows: list[dict[str, object]] = []
	for name, r16, g16, b16 in probe_inputs:
		result = solve_model_node(
			r16,
			g16,
			b16,
			model_family=model_family,
			method=method,
			gamut=gamut,
			input_transfer=input_transfer,
			wx_mode=wx_mode,
			wx_radial_target_position=wx_radial_target_position,
			sample_scale=sample_scale,
		)
		row = {
			"name": name,
			"input_r16": int(round(r16)),
			"input_g16": int(round(g16)),
			"input_b16": int(round(b16)),
			"output_family": output_family,
		}
		if model_family == "rgb":
			out_r, out_g, out_b = result["output_rgb16"]
			row.update({"out_r": out_r, "out_g": out_g, "out_b": out_b})
		else:
			out_r, out_g, out_b, out_w = result["output_rgbw16"]
			row.update({"out_r": out_r, "out_g": out_g, "out_b": out_b, "out_w": out_w})
		probe_rows.append(row)
	build_lut_writer.write_comparison_csv(probe_rows, probe_csv_path)

	summary = {
		"builder": "rgbw_lut_builder.build.model_only",
		"build_kind": "package_model_cube",
		"model_family": model_family,
		"grid_size": grid_size,
		"shape": [grid_size, grid_size, grid_size, output_channels],
		"dtype": str(np.dtype(np.uint16)),
		"output_bit_depth": 16,
		"output_family": output_family,
		"output_channels": channel_order,
		"channel_order": channel_order,
		"sample_scale": float(sample_scale),
		"input_gamut": gamut,
		"input_transfer": input_transfer,
		"normalized_method": normalized_method,
		"wx_mode": effective_wx_mode,
		"wx_target_position": wx_radial_target_position if effective_wx_mode else None,
		"config": asdict(config),
		"npy_path": str(npy_path),
		"probe_csv_path": str(probe_csv_path),
		"elapsed_seconds": float(time.time() - t0),
	}

	if write_header:
		if model_family != "rgbw":
			raise ValueError("write_header is currently supported only for RGBW model cubes")
		h_grid = int(header_grid_size or grid_size)
		header_cube = np.empty((h_grid, h_grid, h_grid, 4), dtype=np.uint16)
		header_axis = build_lut_writer.axis_values(h_grid, sample_scale)
		for ri, r16 in enumerate(header_axis):
			for gi, g16 in enumerate(header_axis):
				for bi, b16 in enumerate(header_axis):
					header_cube[ri, gi, bi, :] = np.asarray(solve_rgbw16(r16, g16, b16, config), dtype=np.uint16)
		header_path = output_dir / f"{basename}_{h_grid}.h"
		build_lut_writer.write_rgbw_header(
			header_cube,
			header_path,
			basename,
			argparse.Namespace(sample_scale=sample_scale),
			grid_size,
		)
		summary["header_path"] = str(header_path)
		summary["header_grid_size"] = h_grid

	summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
	summary["summary_path"] = str(summary_path)

	print(f"  wrote {npy_path}", flush=True)
	print(f"  wrote {probe_csv_path}", flush=True)
	print(f"  wrote {summary_path}", flush=True)
	return summary


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
	parser = argparse.ArgumentParser(description="Model-only build/package entrypoint.")
	subparsers = parser.add_subparsers(dest="command", required=False)

	subparsers.add_parser("legacy-cli", help="Run the copied legacy model-only CLI.")

	solve = subparsers.add_parser("solve-node", help="Solve one RGB or RGBW model-only node through the package API.")
	solve.add_argument("--model-family", choices=["rgb", "rgbw"], default="rgbw")
	solve.add_argument("--method", default="strict_subgamut")
	solve.add_argument("--wx-mode", default=DEFAULT_WX_MODE, choices=list(VALID_WX_MODES) + ["wx_legacy_virtual_axis"])
	solve.add_argument("--wx-radial-target-position", type=float, default=DEFAULT_WX_RADIAL_TARGET_POSITION)
	solve.add_argument("--gamut", default="native")
	solve.add_argument("--input-transfer", default="linear")
	solve.add_argument("--sample-scale", type=float, default=65535.0)
	solve.add_argument("--rgb16", nargs=3, metavar=("R16", "G16", "B16"), type=float, required=True)

	build = subparsers.add_parser("build-cube", help="Build a package-owned RGB16 or RGBW16 model LUT cube.")
	build.add_argument("--model-family", choices=["rgb", "rgbw"], default="rgbw")
	build.add_argument("--method", default="strict_subgamut")
	build.add_argument("--wx-mode", default=DEFAULT_WX_MODE, choices=list(VALID_WX_MODES) + ["wx_legacy_virtual_axis"])
	build.add_argument("--wx-radial-target-position", type=float, default=DEFAULT_WX_RADIAL_TARGET_POSITION)
	build.add_argument("--gamut", default="native")
	build.add_argument("--input-transfer", default="linear")
	build.add_argument("--sample-scale", type=float, default=65535.0)
	build.add_argument("--grid-size", type=int, default=33)
	build.add_argument("--output-dir", type=Path, default=DEFAULT_LUT_OUTPUT_DIR)
	build.add_argument("--basename")
	build.add_argument("--write-header", action="store_true")
	build.add_argument("--header-grid-size", type=int, default=0)

	return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> None:
	args, remaining = parse_args(argv)
	command = args.command or "legacy-cli"

	if command == "legacy-cli":
		run_legacy_model_only_cli(remaining)
		return

	if command == "solve-node":
		r16, g16, b16 = args.rgb16
		result = solve_model_node(
			r16,
			g16,
			b16,
			model_family=args.model_family,
			method=args.method,
			gamut=args.gamut,
			input_transfer=args.input_transfer,
			wx_mode=args.wx_mode,
			wx_radial_target_position=args.wx_radial_target_position,
			sample_scale=args.sample_scale,
		)
		print(json.dumps(result, indent=2))
		return

	if command == "build-cube":
		result = build_model_lut_cube(
			output_dir=args.output_dir,
			grid_size=args.grid_size,
			model_family=args.model_family,
			method=args.method,
			gamut=args.gamut,
			input_transfer=args.input_transfer,
			wx_mode=args.wx_mode,
			wx_radial_target_position=args.wx_radial_target_position,
			sample_scale=args.sample_scale,
			basename=args.basename,
			write_header=args.write_header,
			header_grid_size=args.header_grid_size,
		)
		print(json.dumps(result, indent=2))
		return

	raise ValueError(f"Unsupported command={command!r}")


if __name__ == "__main__":
	main()
