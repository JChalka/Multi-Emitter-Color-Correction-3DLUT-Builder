from __future__ import annotations


def safe_int(value: str | None) -> int:
	try:
		return int(float(value or 0))
	except (TypeError, ValueError):
		return 0


def safe_float(value: str | None) -> float:
	try:
		return float(value or 0.0)
	except (TypeError, ValueError):
		return float("nan")


def is_ok(value: str | None) -> bool:
	return str(value or "").strip().lower() == "true"


__all__ = ["is_ok", "safe_float", "safe_int"]
