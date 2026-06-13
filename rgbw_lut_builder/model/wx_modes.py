from __future__ import annotations

DEFAULT_WX_MODE = "wx_radial_virtual"
DEFAULT_WX_RADIAL_TARGET_POSITION = 0.70

VALID_WX_MODES = (
	"wx_radial_virtual",
	"wx_virtual_axis_maxbright",
	"wx_lp_legacy",
)

WX_MODE_ALIASES = {
	"wx": DEFAULT_WX_MODE,
	"radial": "wx_radial_virtual",
	"wx_radial": "wx_radial_virtual",
	"wx_radial_virtual": "wx_radial_virtual",
	"radial_virtual": "wx_radial_virtual",
	"virtual_axis": "wx_virtual_axis_maxbright",
	"maxbright": "wx_virtual_axis_maxbright",
	"max_bright": "wx_virtual_axis_maxbright",
	"brightness": "wx_virtual_axis_maxbright",
	"wx_virtual_axis": "wx_virtual_axis_maxbright",
	"wx_virtual_axis_maxbright": "wx_virtual_axis_maxbright",
	"wx_legacy_virtual_axis": "wx_virtual_axis_maxbright",
	"lp": "wx_lp_legacy",
	"wx_lp": "wx_lp_legacy",
	"lp_legacy": "wx_lp_legacy",
	"lp_maxwhite": "wx_lp_legacy",
	"wx_lp_legacy": "wx_lp_legacy",
	"wx_lp_maxwhite": "wx_lp_legacy",
}


def normalize_wx_mode(wx_mode: str | None) -> str:
	key = str(wx_mode or DEFAULT_WX_MODE).strip().lower().replace("-", "_")
	if key not in WX_MODE_ALIASES:
		raise ValueError(
			f"Unsupported wx_mode={wx_mode!r}; expected one of {list(VALID_WX_MODES)} or aliases {sorted(WX_MODE_ALIASES)}"
		)
	return WX_MODE_ALIASES[key]


def normalize_lut_method(method: str | None) -> str:
	value = str(method or "strict_subgamut").strip().lower().replace("-", "_")
	if value == "both":
		return "strict_subgamut"
	if value in {"sub", "subgamut", "sub_gamut", "strict", "strict_subgamut"}:
		return "strict_subgamut"
	if value == "rgb":
		return "rgb"
	if value in WX_MODE_ALIASES or value == "wx":
		return "wx"
	raise ValueError(
		f"Unsupported method={method!r}; expected 'rgb', 'strict_subgamut', 'wx', or a direct WX mode alias"
	)


def resolve_wx_mode(method: str | None, wx_mode: str | None = None) -> str:
	method_value = str(method or "").strip().lower().replace("-", "_")
	if method_value in WX_MODE_ALIASES and method_value != "wx":
		return normalize_wx_mode(method_value)
	return normalize_wx_mode(wx_mode)
