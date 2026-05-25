from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))

from rgbw_lut_builder.gui import analyze_rgbw_captures, build_delaunay_rgbw_lut, build_measured_rgbw_lut, rgbw_lut_gui


BUILD_SURFACES: dict[str, Callable[[], None]] = {
	"gui": rgbw_lut_gui.main,
	"measured": build_measured_rgbw_lut.main,
	"delaunay": build_delaunay_rgbw_lut.main,
	"analyze": analyze_rgbw_captures.main,
}


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
	parser = argparse.ArgumentParser(description="Standalone RGBW LUT builder front door.")
	parser.add_argument(
		"surface",
		nargs="?",
		default="gui",
		choices=sorted(BUILD_SURFACES),
		help="Which transitioned surface to run.",
	)
	return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> None:
	args, remaining = parse_args(argv)
	sys.argv = [f"{sys.argv[0]}:{args.surface}", *remaining]
	BUILD_SURFACES[args.surface]()


if __name__ == "__main__":
	main()
