from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np


def utc_now_iso() -> str:
	return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def safe_profile_id(value: str | None) -> str:
	text = str(value or "").strip()
	if not text:
		text = "default_display"
	text = Path(text).stem if any(sep in text for sep in ("/", "\\")) else text
	safe = "".join(ch.lower() if ch.isalnum() else "_" for ch in text)
	safe = "_".join(part for part in safe.split("_") if part)
	return safe or "default_display"


def json_sanitize(value):
	if isinstance(value, dict):
		return {str(key): json_sanitize(item) for key, item in value.items()}
	if isinstance(value, (list, tuple)):
		return [json_sanitize(item) for item in value]
	if isinstance(value, set):
		return sorted(json_sanitize(item) for item in value)
	if isinstance(value, np.generic):
		return json_sanitize(value.item())
	if isinstance(value, float):
		return value if math.isfinite(value) else None
	if isinstance(value, (int, str, bool)) or value is None:
		return value
	return str(value)


def xyY_to_XYZ(x: float, y: float, Y: float) -> tuple[float | None, float | None, float | None]:
	if not (np.isfinite(x) and np.isfinite(y) and np.isfinite(Y)) or y <= 1e-12 or Y < 0.0:
		return None, None, None
	X = (x * Y) / y
	Z = ((1.0 - x - y) * Y) / y
	return float(X), float(Y), float(Z)


def profile_path_from_args(args: argparse.Namespace, *, default_config_dir: Path) -> tuple[Path, str]:
	config_dir = Path(getattr(args, "config_dir", default_config_dir))
	profile_arg = str(getattr(args, "display_profile", "default_display") or "default_display")
	explicit_id = str(getattr(args, "display_id", "") or "").strip()

	maybe_path = Path(profile_arg)
	if maybe_path.suffix.lower() == ".json" or maybe_path.is_absolute() or any(sep in profile_arg for sep in ("/", "\\")):
		profile_path = maybe_path if maybe_path.is_absolute() else (config_dir / "profiles" / maybe_path)
		display_id = safe_profile_id(explicit_id or profile_path.stem)
	else:
		display_id = safe_profile_id(explicit_id or profile_arg)
		profile_path = config_dir / "profiles" / f"{display_id}.json"
	return profile_path, display_id


def load_or_create_display_profile(
	args: argparse.Namespace,
	reference_white: Any,
	*,
	default_config_dir: Path,
) -> tuple[dict, Path]:
	profile_path, display_id = profile_path_from_args(args, default_config_dir=default_config_dir)
	now = utc_now_iso()
	profile: dict = {}
	if profile_path.exists():
		try:
			profile = json.loads(profile_path.read_text(encoding="utf-8"))
		except Exception:
			profile = {}

	if not profile:
		profile = {
			"schema_version": 1,
			"display_id": display_id,
			"display_name": display_id,
			"created_at": now,
			"notes": "",
		}

	profile["schema_version"] = int(profile.get("schema_version", 1) or 1)
	profile["display_id"] = safe_profile_id(str(profile.get("display_id") or display_id))
	profile["last_used_at"] = now
	profile.setdefault("display_name", profile["display_id"])
	profile["reference_white"] = {
		"x": float(reference_white.x),
		"y": float(reference_white.y),
		"Y": float(reference_white.Y),
	}
	profile["builder_profile"] = {
		"sample_scale": float(getattr(args, "sample_scale", 65535.0)),
		"target_white_balance_mode": str(getattr(args, "target_white_balance_mode", "reference-white")),
	}
	profile["paths"] = {
		"input_dir": str(getattr(args, "input_dir", "")),
		"config_dir": str(getattr(args, "config_dir", default_config_dir)),
	}

	profile_path.parent.mkdir(parents=True, exist_ok=True)
	profile_path.write_text(json.dumps(json_sanitize(profile), indent=2), encoding="utf-8")
	setattr(args, "display_id", profile["display_id"])
	setattr(args, "display_profile_path", str(profile_path))
	return profile, profile_path


def feedback_bank_paths(
	args: argparse.Namespace,
	display_id: str,
	*,
	default_config_dir: Path,
) -> tuple[Path, Path, Path, Path]:
	config_dir = Path(getattr(args, "config_dir", default_config_dir))
	bank_arg = str(getattr(args, "feedback_bank", "auto") or "auto")
	dict_dir = config_dir / "dictionaries" / safe_profile_id(display_id)
	if bank_arg.lower() != "auto":
		bank_path = Path(bank_arg)
		if not bank_path.is_absolute():
			bank_path = dict_dir / bank_path
	else:
		bank_path = dict_dir / "verifier_feedback_bank.json"
	return bank_path, dict_dir / "verifier_pass_bank.json", dict_dir / "verifier_fail_bank.json", dict_dir / "sessions"


def load_feedback_candidate_model_for_args(
	args: argparse.Namespace,
	*,
	default_config_dir: Path,
	iter_feedback_bank_observations: Callable[[dict], list[dict]],
	feedback_obs_from_verifier_row: Callable[[dict, str, float], dict | None],
	build_feedback_candidate_model_from_observations: Callable[..., dict],
) -> dict | None:
	mode = str(getattr(args, "feedback_mode", "diagnostic") or "diagnostic").lower()
	if mode not in {"candidate", "reevaluate"}:
		return None

	de_threshold = float(getattr(args, "feedback_trust_pass_dE", 2.5))
	observations: list[dict] = []

	bank_arg = str(getattr(args, "feedback_bank", "auto") or "auto")
	bank_paths: list[Path] = []
	if bank_arg and bank_arg.lower() != "auto":
		bank_paths.append(Path(bank_arg))
	else:
		display_id = safe_profile_id(getattr(args, "display_id", "") or getattr(args, "display_profile", "default_display") or "default_display")
		try:
			bank_paths.append(feedback_bank_paths(args, display_id, default_config_dir=default_config_dir)[0])
		except Exception:
			pass

	for bank_path in bank_paths:
		try:
			if bank_path.exists():
				observations.extend(iter_feedback_bank_observations(json.loads(bank_path.read_text(encoding="utf-8"))))
		except Exception:
			pass

	verifier_dir = getattr(args, "verifier_diagnostics_dir", None)
	if verifier_dir:
		try:
			resolved_dir = Path(verifier_dir)
			if resolved_dir.exists() and resolved_dir.is_dir():
				for csv_path in sorted(resolved_dir.glob("family_hull_latest_quick_verify*.csv")):
					with csv_path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
						for row in csv.DictReader(handle):
							observation = feedback_obs_from_verifier_row(row, csv_path.name, de_threshold)
							if observation is not None:
								observations.append(observation)
		except Exception:
			pass

	if not observations:
		return None
	return build_feedback_candidate_model_from_observations(observations, dE_threshold=de_threshold)


def write_verifier_feedback_bank(
	verifier_dir: Path,
	output_dir: Path,
	args: argparse.Namespace,
	display_profile: dict,
	*,
	default_config_dir: Path,
	dE_threshold: float = 2.5,
	parse_verifier_feedback_rows: Callable[[Path, float], tuple[list[dict], dict[str, list[dict]]]],
	resolve_target_match_candidates: Callable[[list[dict], list[Path]], list[dict]],
	feedback_result_id: Callable[[dict], str],
	channel_direction_hints_for_observation: Callable[[dict, dict | None], dict],
	feedback_target_xyz: Callable[[dict], list[float | None] | None],
	feedback_measured_xyz: Callable[[dict], list[float | None] | None],
	feedback_capture_delta: Callable[[dict, dict | None], dict | None],
	merge_feedback_observation: Callable[[dict[str, dict], dict, str, str], None],
	observation_sort_key: Callable[[dict], tuple],
	build_feedback_entry_stats: Callable[[list[dict]], tuple[dict, dict, dict]],
	legacy_feedback_observations: Callable[[dict, str], list[dict]],
) -> dict | None:
	verifier_dir = Path(verifier_dir)
	if not verifier_dir.exists() or not verifier_dir.is_dir():
		return None

	display_id = safe_profile_id(display_profile.get("display_id", getattr(args, "display_id", "default_display")))
	bank_path, pass_path, fail_path, sessions_dir = feedback_bank_paths(args, display_id, default_config_dir=default_config_dir)
	sessions_dir.mkdir(parents=True, exist_ok=True)
	bank_path.parent.mkdir(parents=True, exist_ok=True)

	verifier_rows, target_match_by_key = parse_verifier_feedback_rows(verifier_dir, dE_threshold)
	if not verifier_rows:
		return None

	search_dirs: list[Path] = []
	for value in (getattr(args, "input_dir", None), verifier_dir, verifier_dir.parent, output_dir):
		if value:
			try:
				search_dirs.append(Path(value))
			except Exception:
				pass

	target_candidates_by_key = {
		key: resolve_target_match_candidates(matches, search_dirs)
		for key, matches in target_match_by_key.items()
	}

	by_key: dict[str, list[dict]] = {}
	for row in verifier_rows:
		by_key.setdefault(row["rgb_key"], []).append(row)

	now = utc_now_iso()
	session_id = now.replace("+00:00", "Z").replace(":", "").replace("-", "")
	session_summary = {
		"session_id": session_id,
		"created_at": now,
		"display_id": display_id,
		"verifier_dir": str(verifier_dir),
		"dE_threshold": dE_threshold,
		"rows": len(verifier_rows),
		"pass_rows": sum(1 for row in verifier_rows if row["status"] == "pass"),
		"fail_rows": sum(1 for row in verifier_rows if row["status"] == "fail"),
		"unique_rgb": len(by_key),
	}

	existing: dict = {}
	if bank_path.exists():
		try:
			existing = json.loads(bank_path.read_text(encoding="utf-8"))
		except Exception:
			existing = {}

	existing_entries = existing.get("entries", {}) if isinstance(existing, dict) else {}
	bank = {
		"schema_version": 2,
		"diagnostic_only": True,
		"display_id": display_id,
		"display_profile": display_profile,
		"created_at": existing.get("created_at", now) if isinstance(existing, dict) else now,
		"updated_at": now,
		"feedback_mode": str(getattr(args, "feedback_mode", "diagnostic")),
		"dE_threshold": dE_threshold,
		"sessions": list(existing.get("sessions", [])) if isinstance(existing, dict) else [],
		"entries": {},
	}
	bank["sessions"].append(session_summary)
	bank["sessions"] = bank["sessions"][-48:]

	detail_rows: list[dict] = []
	session_entries: dict[str, dict] = {}

	for key, previous in existing_entries.items() if isinstance(existing_entries, dict) else []:
		if not isinstance(previous, dict):
			continue
		observations = legacy_feedback_observations(previous, now)
		observations = sorted(observations, key=observation_sort_key)
		pass_stats, fail_stats, latest_result = build_feedback_entry_stats(observations)
		bank["entries"][key] = {
			**{k: v for k, v in previous.items() if k not in ("pass_stats", "fail_stats", "latest_result", "observations")},
			"schema_version": 2,
			"rgb_key": key,
			"display_id": display_id,
			"observations": observations,
			"observation_count": len(observations),
			"pass_stats": pass_stats,
			"fail_stats": fail_stats,
			"latest_result": latest_result,
		}

	for key, rows_for_key in sorted(by_key.items()):
		input_rgb = rows_for_key[-1]["input_rgb"]
		previous_entry = bank["entries"].get(key, {})
		existing_observations = previous_entry.get("observations", []) if isinstance(previous_entry, dict) else []
		observations_by_id = {
			str(observation.get("observation_id")): dict(observation)
			for observation in existing_observations
			if isinstance(observation, dict) and observation.get("observation_id")
		}

		target_candidates = target_candidates_by_key.get(key, [])
		best_capture = target_candidates[0] if target_candidates else None
		session_observation_ids: list[str] = []

		for row in rows_for_key:
			observation_id = feedback_result_id(row)
			hints = channel_direction_hints_for_observation(row, best_capture)
			measured_x = row["measured"].get("x")
			measured_y = row["measured"].get("y")
			measured_Y = row["measured"].get("Y")
			observation = {
				"schema_version": 2,
				"observation_id": observation_id,
				"first_seen_at": now,
				"last_seen_at": now,
				"seen_count": 1,
				"sessions": [session_id],
				"rgb_key": key,
				"display_id": display_id,
				"input_rgb": row["input_rgb"],
				"input_mask": row.get("input_mask"),
				"input_common_min": row.get("input_common_min"),
				"input_max": row.get("input_max"),
				"target": row["target"],
				"target_XYZ": feedback_target_xyz(row["target"]),
				"measured_xyY": [measured_x, measured_y, measured_Y],
				"measured_XYZ": feedback_measured_xyz(row["measured"]),
				"xy_dx": row.get("xy_dx"),
				"xy_dy": row.get("xy_dy"),
				"dE": row.get("verifier_dE"),
				"status": row.get("status"),
				"ok": bool(row.get("ok")),
				"lut_rgbw": row.get("lut_rgbw"),
				"lut_r": row.get("lut_r"),
				"lut_g": row.get("lut_g"),
				"lut_b": row.get("lut_b"),
				"lut_w": row.get("lut_w"),
				"out_rgb_max": row.get("out_rgb_max"),
				"out_w_to_common": row.get("out_w_to_common"),
				"selected_family": row.get("selected_family", ""),
				"selected_route": row.get("selected_route", ""),
				"source_file": row.get("source_file", ""),
				"patch": row.get("patch", ""),
				"failure_flags": list(row.get("failure_flags", [])),
				"channel_direction_hints": hints,
				"capture_delta_rgbw": feedback_capture_delta(row, best_capture),
				"best_capture": best_capture,
				"target_match_candidates": target_candidates,
			}
			merge_feedback_observation(observations_by_id, observation, session_id, now)
			session_observation_ids.append(observation_id)

		observations = sorted(observations_by_id.values(), key=observation_sort_key)
		pass_stats, fail_stats, latest_result = build_feedback_entry_stats(observations)
		entry = {
			"schema_version": 2,
			"rgb_key": key,
			"display_id": display_id,
			"input_rgb": input_rgb,
			"target": rows_for_key[-1]["target"],
			"target_XYZ": feedback_target_xyz(rows_for_key[-1]["target"]),
			"latest_result": latest_result,
			"pass_stats": pass_stats,
			"fail_stats": fail_stats,
			"best_capture": best_capture,
			"target_match_candidates": target_candidates,
			"observations": observations,
			"observation_count": len(observations),
		}
		bank["entries"][key] = entry
		session_entries[key] = {
			**entry,
			"observations": [observations_by_id[obs_id] for obs_id in session_observation_ids if obs_id in observations_by_id],
			"session_observation_ids": session_observation_ids,
		}

		for observation in session_entries[key]["observations"]:
			best = observation.get("best_capture") or {}
			hints = observation.get("channel_direction_hints") or {}
			detail_rows.append({
				"rgb_key": key,
				"observation_id": observation.get("observation_id"),
				"input_r": input_rgb[0],
				"input_g": input_rgb[1],
				"input_b": input_rgb[2],
				"status": observation.get("status"),
				"dE": observation.get("dE"),
				"lut_rgbw": ",".join(str(value) for value in (observation.get("lut_rgbw") or [])),
				"measured_xyY": ",".join(str(value) for value in (observation.get("measured_xyY") or [])),
				"xy_dx": observation.get("xy_dx"),
				"xy_dy": observation.get("xy_dy"),
				"pass_count": pass_stats.get("pass_count"),
				"fail_count": fail_stats.get("fail_count"),
				"best_pass_dE": pass_stats.get("best_dE"),
				"best_capture_available": bool(best),
				"best_capture_xy_dist": best.get("xy_dist", ""),
				"best_capture_Y_log_ratio": best.get("Y_log_ratio", ""),
				"best_capture_rgbw": ",".join(str(best.get(name, "")) for name in ("cap_r16", "cap_g16", "cap_b16", "cap_w16")) if best else "",
				"capture_delta_rgbw": json.dumps(json_sanitize(observation.get("capture_delta_rgbw")), sort_keys=True),
				"channel_direction_hints": "|".join(f"{channel}:{move}" for channel, move in hints.items()),
				"failure_flags": "|".join(observation.get("failure_flags", [])),
				"seen_count": observation.get("seen_count"),
				"sessions": "|".join(str(session) for session in observation.get("sessions", [])),
			})

	pass_entries: dict[str, dict] = {}
	fail_entries: dict[str, dict] = {}
	for key, entry in bank["entries"].items():
		observations = entry.get("observations", []) if isinstance(entry, dict) else []
		pass_obs = [observation for observation in observations if observation.get("status") == "pass"]
		fail_obs = [observation for observation in observations if observation.get("status") == "fail"]
		if pass_obs:
			pass_entry = dict(entry)
			pass_entry["observations"] = pass_obs
			pass_entry["observation_count"] = len(pass_obs)
			pass_entries[key] = pass_entry
		if fail_obs:
			fail_entry = dict(entry)
			fail_entry["observations"] = fail_obs
			fail_entry["observation_count"] = len(fail_obs)
			fail_entries[key] = fail_entry

	bank_path.write_text(json.dumps(json_sanitize(bank), indent=2), encoding="utf-8")
	pass_path.write_text(json.dumps(json_sanitize({
		"schema_version": 2,
		"display_id": display_id,
		"entries": pass_entries,
	}), indent=2), encoding="utf-8")
	fail_path.write_text(json.dumps(json_sanitize({
		"schema_version": 2,
		"display_id": display_id,
		"entries": fail_entries,
	}), indent=2), encoding="utf-8")

	session_path = sessions_dir / f"{session_id}_feedback_bank.json"
	session_csv = sessions_dir / f"{session_id}_feedback_detail.csv"
	session_path.write_text(json.dumps(json_sanitize({
		"session": session_summary,
		"schema_version": 2,
		"entries": session_entries,
	}), indent=2), encoding="utf-8")
	if detail_rows:
		with session_csv.open("w", newline="", encoding="utf-8") as handle:
			writer = csv.DictWriter(handle, fieldnames=list(detail_rows[0].keys()))
			writer.writeheader()
			writer.writerows(detail_rows)

	return {
		"bank_path": bank_path,
		"pass_path": pass_path,
		"fail_path": fail_path,
		"session_path": session_path,
		"session_csv": session_csv,
		"summary": session_summary,
	}
