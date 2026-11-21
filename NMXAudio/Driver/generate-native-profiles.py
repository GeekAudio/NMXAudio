#!/usr/bin/env python3
"""Compile AppleALC resources into generic native HDA profile tables.

The generated C header contains codec/layout/function-group selectors, ordered
PathMap node chains, and only standard SET_CONFIG_DEFAULT / EAPD initialization
verbs. AppleHDA binary patches and mixer/control semantics are intentionally
reported, not translated into pretend native support.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path, PurePosixPath
import plistlib
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from applealc_plist import load_plist


SUPPORTED_INIT_VERBS = {0x70C, 0x71C, 0x71D, 0x71E, 0x71F}
PIN_CONFIG_VERBS = {0x71C, 0x71D, 0x71E, 0x71F}
PROFILE_ABI = "nmx-native-profile-v1"


class CompileError(Exception):
    """Invalid catalog shape that makes safe deterministic generation impossible."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CompileError(message)


def integer(value: Any, label: str, low: int = 0, high: int = 0xFFFFFFFF) -> int:
    require(type(value) is int, f"{label} must be an integer")
    require(low <= value <= high, f"{label} is outside {low}..{high}: {value}")
    return value


def darwin_bound(value: Any, label: str) -> int:
    if type(value) is int:
        return integer(value, label, 0, 255)
    require(isinstance(value, str), f"{label} must be a Darwin major or version string")
    require(bool(re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", value)),
            f"{label} is not a numeric Darwin version: {value!r}")
    major = int(value.split(".", 1)[0])
    require(0 <= major <= 255, f"{label} is outside the supported Darwin range")
    return major


def is_active(record: Dict[str, Any], darwin_major: int, label: str) -> bool:
    minimum = record.get("MinKernel")
    maximum = record.get("MaxKernel")
    min_major = darwin_bound(minimum, f"{label}.MinKernel") if minimum is not None else None
    max_major = darwin_bound(maximum, f"{label}.MaxKernel") if maximum is not None else None
    if min_major is not None and max_major is not None:
        require(min_major <= max_major, f"{label} has MinKernel greater than MaxKernel")
    return ((min_major is None or darwin_major >= min_major) and
            (max_major is None or darwin_major <= max_major))


def read_plist(path: Path) -> Any:
    try:
        return load_plist(path.read_bytes(), allow_xml_fragment=True)
    except (OSError, plistlib.InvalidFileException, ValueError, TypeError) as error:
        raise CompileError(f"cannot parse {path.name}: {error}") from error


def safe_resource_file(codec_dir: Path, resource_path: Any, label: str) -> Tuple[Path, str]:
    require(isinstance(resource_path, str) and resource_path,
            f"{label}.Path must be a non-empty string")
    relative = PurePosixPath(resource_path)
    require(not relative.is_absolute() and "\\" not in resource_path and
            all(part not in ("", ".", "..") for part in relative.parts),
            f"unsafe {label}.Path: {resource_path!r}")

    source_name = resource_path[:-5] if resource_path.endswith(".zlib") else resource_path
    source_relative = PurePosixPath(source_name)
    require(source_relative.suffix == ".xml", f"unexpected {label}.Path: {resource_path!r}")
    candidate = codec_dir.joinpath(*source_relative.parts)
    try:
        resolved_root = codec_dir.resolve(strict=True)
        resolved_file = candidate.resolve(strict=True)
        resolved_file.relative_to(resolved_root)
    except (OSError, ValueError) as error:
        raise CompileError(f"unresolved or unsafe {label}.Path: {resource_path!r}") from error
    require(resolved_file.is_file(), f"{label}.Path is not a regular file: {resource_path!r}")
    return resolved_file, source_relative.as_posix()


def resource_records(
    resources: Path,
    darwin_major: int,
) -> Tuple[Dict[str, Any], Dict[str, int], Dict[Tuple[int, int, int], List[Dict[str, Any]]]]:
    require(resources.is_dir() and not resources.is_symlink(),
            f"Resources directory is missing or is a symlink: {resources}")
    vendors_path = resources / "Vendors.plist"
    pins_path = resources / "PinConfigs.kext" / "Contents" / "Info.plist"
    require(vendors_path.is_file(), "Resources/Vendors.plist is missing")
    require(pins_path.is_file(), "Resources/PinConfigs.kext/Contents/Info.plist is missing")

    vendors_data = read_plist(vendors_path)
    require(isinstance(vendors_data, dict) and vendors_data,
            "Vendors.plist must be a non-empty dictionary")
    vendors: Dict[str, int] = {}
    for name, value in vendors_data.items():
        require(isinstance(name, str) and name, "vendor names must be non-empty strings")
        vendors[name] = integer(value, f"vendor {name}", 0, 0xFFFF)

    pin_root = read_plist(pins_path)
    personalities = pin_root.get("IOKitPersonalities") if isinstance(pin_root, dict) else None
    require(isinstance(personalities, dict), "PinConfigs has no IOKitPersonalities dictionary")
    personality = personalities.get("as.vit9696.AppleALC")
    require(isinstance(personality, dict), "AppleALC PinConfigs personality is missing")
    raw_entries = personality.get("HDAConfigDefault")
    require(isinstance(raw_entries, list), "HDAConfigDefault must be an array")

    pins: Dict[Tuple[int, int, int], List[Dict[str, Any]]] = defaultdict(list)
    for index, entry in enumerate(raw_entries):
        label = f"HDAConfigDefault[{index}]"
        require(isinstance(entry, dict), f"{label} must be a dictionary")
        codec_id = integer(entry.get("CodecID"), f"{label}.CodecID")
        layout_id = integer(entry.get("LayoutID"), f"{label}.LayoutID", 0, 0xFFFFFFFF)
        function_group = integer(entry.get("FuncGroup", 1), f"{label}.FuncGroup", 0, 0xFF)
        if not is_active(entry, darwin_major, label):
            continue
        config_data = entry.get("ConfigData", b"")
        wake_data = entry.get("WakeConfigData", b"")
        if "WakeVerbReinit" in entry:
            require(type(entry["WakeVerbReinit"]) is bool,
                    f"{label}.WakeVerbReinit must be boolean")
        require(isinstance(config_data, bytes), f"{label}.ConfigData must be data")
        require(isinstance(wake_data, bytes), f"{label}.WakeConfigData must be data")
        require(len(config_data) % 4 == 0, f"{label}.ConfigData has a truncated verb")
        require(len(wake_data) % 4 == 0, f"{label}.WakeConfigData has a truncated verb")
        normalized = dict(entry)
        normalized["_source_index"] = index
        pins[(codec_id, layout_id, function_group)].append(normalized)
    return pin_root, vendors, pins


def codec_infos(resources: Path) -> List[Tuple[Path, Dict[str, Any], int]]:
    result = []
    for path in sorted(resources.glob("*/Info.plist")):
        if path.parent.name == "PinConfigs.kext":
            continue
        info = read_plist(path)
        require(isinstance(info, dict), f"{path.parent.name}/Info.plist must be a dictionary")
        vendor = info.get("Vendor")
        codec = info.get("CodecID")
        require(isinstance(vendor, str) and vendor, f"{path.parent.name}/Info.plist has no Vendor")
        codec = integer(codec, f"{path.parent.name}.CodecID", 0, 0xFFFF)
        result.append((path, info, codec))
    require(result, "no codec Info.plist files found")
    return result


def selected_reference(
    references: Any,
    wanted_id: int,
    darwin_major: int,
    label: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    require(isinstance(references, list), f"{label} references must be an array")
    selected = []
    for index, reference in enumerate(references):
        item_label = f"{label}[{index}]"
        require(isinstance(reference, dict), f"{item_label} must be a dictionary")
        ref_id = integer(reference.get("Id"), f"{item_label}.Id")
        if ref_id == wanted_id and is_active(reference, darwin_major, item_label):
            selected.append(reference)
    if not selected:
        return None, f"no active {label} reference for ID {wanted_id} on Darwin {darwin_major}"
    if len(selected) != 1:
        return None, f"multiple active {label} references for ID {wanted_id} on Darwin {darwin_major}"
    return selected[0], None


def flatten_path_map(value: Any, label: str) -> List[List[Dict[str, Any]]]:
    """Flatten AppleALC's nested alternatives into ordered node chains."""
    routes: List[List[Dict[str, Any]]] = []

    def visit(node: Any, path: str) -> None:
        if isinstance(node, str):
            # AppleALC uses an optional leading string as a human-readable
            # PathMap label. It is metadata, not a node chain.
            return
        require(isinstance(node, list), f"{label}{path} must be an array or PathMap label")
        if node and all(isinstance(item, dict) and "NodeID" in item for item in node):
            chain = []
            for index, item in enumerate(node):
                nid = integer(item.get("NodeID"), f"{label}{path}[{index}].NodeID", 0, 0xFF)
                # Keep a normalized copy for unsupported-control diagnostics.
                chain.append({"NodeID": nid, **{key: value for key, value in item.items() if key != "NodeID"}})
            require(chain, f"{label}{path} contains an empty route")
            routes.append(chain)
            return
        for index, child in enumerate(node):
            if isinstance(child, str):
                continue
            visit(child, f"{path}[{index}]")

    visit(value, "")
    require(routes, f"{label} has no node chains")
    return routes


def unsupported_properties(value: Any) -> Counter:
    """Count feature keys not represented by the native profile ABI."""
    counts = Counter()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key != "NodeID":
                    counts[str(key)] += 1
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return counts


def unsupported_layout_properties(layout: Dict[str, Any]) -> Counter:
    counts = Counter()
    for key in layout:
        if key not in {"LayoutID", "PathMapRef"}:
            counts[f"layout.{key}"] += 1
    for path_index, path_ref in enumerate(layout.get("PathMapRef", [])):
        if not isinstance(path_ref, dict):
            continue
        for key in path_ref:
            if key not in {"CodecID", "PathMapID", "Inputs", "Outputs"}:
                value = path_ref[key]
                if isinstance(value, dict):
                    for child in value:
                        counts[f"layout.{key}.{child}"] += 1
                elif value not in ({}, [], None):
                    counts[f"layout.{key}"] += 1
    return counts


def parse_init_verbs(data: bytes, label: str) -> Tuple[List[Dict[str, int]], List[Dict[str, Any]]]:
    """Return supported standard writes and a list of unsupported commands."""
    verbs: List[Dict[str, int]] = []
    unsupported: List[Dict[str, Any]] = []
    for offset in range(0, len(data), 4):
        word = int.from_bytes(data[offset:offset + 4], "big")
        codec_address = (word >> 28) & 0xF
        nid = (word >> 20) & 0xFF
        command = (word >> 8) & 0xFFF
        payload = word & 0xFF
        reason = None
        if codec_address != 0:
            reason = "nonzero_codec_address_not_represented_by_native_verb_abi"
        elif command not in SUPPORTED_INIT_VERBS:
            reason = "verb_outside_standard_pin_config_and_eapd_subset"
        if reason:
            unsupported.append({
                "offset": offset,
                "encoded": f"0x{word:08x}",
                "codecAddress": codec_address,
                "nodeId": nid,
                "command": f"0x{command:03x}",
                "payload": f"0x{payload:02x}",
                "reason": reason,
            })
        else:
            verbs.append({"nid": nid, "command": command, "payload": payload})
    return verbs, unsupported


def pin_defaults(verbs: Sequence[Dict[str, int]]) -> Tuple[List[Dict[str, int]], List[int]]:
    values: Dict[int, Dict[int, int]] = defaultdict(dict)
    for verb in verbs:
        command = verb["command"]
        if command in PIN_CONFIG_VERBS:
            byte_index = command - 0x71C
            values[verb["nid"]][byte_index] = verb["payload"]
    complete = []
    incomplete = []
    for nid in sorted(values):
        if set(values[nid]) != {0, 1, 2, 3}:
            incomplete.append(nid)
            continue
        config = sum(values[nid][byte_index] << (byte_index * 8) for byte_index in range(4))
        complete.append({"nid": nid, "config": config})
    return complete, incomplete


def path_ref_codec_ids(path_ref: Dict[str, Any], default_codec_id: int, label: str) -> List[int]:
    if "CodecID" not in path_ref:
        return [default_codec_id]
    codec_ids = path_ref["CodecID"]
    require(isinstance(codec_ids, list), f"{label}.CodecID must be an array")
    ids = [integer(value, f"{label}.CodecID[]") for value in codec_ids]
    require(ids, f"{label}.CodecID must not be empty")
    # Preserve the source list while removing accidental duplicate ids.
    return list(dict.fromkeys(ids))


def make_empty_profile(codec_id: int, layout_id: int, function_group: int) -> Dict[str, Any]:
    return {
        "codecId": codec_id,
        "layoutId": layout_id,
        "functionGroup": function_group,
        "routes": [],
        "pathmapIds": [],
        "layoutSources": [],
        "platformSources": [],
        "unsupported": [],
        "pinEntries": [],
        "pins": [],
        "bootVerbs": [],
        "wakeVerbs": [],
        "pinConfigStatus": "absent",
    }


def add_reason(profile: Dict[str, Any], code: str, **details: Any) -> None:
    value = {"code": code}
    value.update(details)
    if value not in profile["unsupported"]:
        profile["unsupported"].append(value)


def compile_catalog(resources: Path, darwin_major: int) -> Dict[str, Any]:
    _pin_root, vendors, pin_entries = resource_records(resources, darwin_major)
    infos = codec_infos(resources)
    profiles: Dict[Tuple[int, int, int], Dict[str, Any]] = {}
    issues: List[Dict[str, Any]] = []
    patch_summary = {"active": 0, "inactive": 0, "codecFiles": []}

    # First create route-selector rows from the original Info.plist and XML
    # references. These rows do not depend on PinConfigs being present.
    for info_path, info, codec_product_id in infos:
        vendor_id = vendors.get(info.get("Vendor"))
        require(vendor_id is not None,
                f"{info_path.parent.name}/Info.plist references unknown vendor {info.get('Vendor')!r}")
        default_codec_id = (vendor_id << 16) | codec_product_id
        files = info.get("Files", {})
        require(isinstance(files, dict), f"{info_path.parent.name}/Info.plist Files must be a dictionary")
        layout_refs = files.get("Layouts", [])
        platform_refs = files.get("Platforms", [])
        require(isinstance(layout_refs, list), f"{info_path.parent.name}.Files.Layouts must be an array")
        require(isinstance(platform_refs, list), f"{info_path.parent.name}.Files.Platforms must be an array")

        patches = info.get("Patches", [])
        require(isinstance(patches, list), f"{info_path.parent.name}.Patches must be an array")
        active_patches = 0
        inactive_patches = 0
        for index, patch in enumerate(patches):
            require(isinstance(patch, dict), f"{info_path.parent.name}.Patches[{index}] must be a dictionary")
            if is_active(patch, darwin_major, f"{info_path.parent.name}.Patches[{index}]"):
                active_patches += 1
            else:
                inactive_patches += 1
        patch_summary["active"] += active_patches
        patch_summary["inactive"] += inactive_patches
        patch_summary["codecFiles"].append({
            "codecDirectory": info_path.parent.name,
            "activeAppleHdaPatchRecordsNotEmitted": active_patches,
            "inactiveAppleHdaPatchRecords": inactive_patches,
        })

        for ref_index, layout_ref in enumerate(layout_refs):
            label = f"{info_path.parent.name}.Files.Layouts[{ref_index}]"
            require(isinstance(layout_ref, dict), f"{label} must be a dictionary")
            layout_id = integer(layout_ref.get("Id"), f"{label}.Id")
            if not is_active(layout_ref, darwin_major, label):
                continue
            platform_ref, ref_error = selected_reference(
                platform_refs, layout_id, darwin_major, f"{info_path.parent.name}.Files.Platforms"
            )
            if ref_error:
                issues.append({"codecDirectory": info_path.parent.name,
                               "layoutId": layout_id, "reason": ref_error})
                continue

            try:
                layout_path, layout_relative = safe_resource_file(
                    info_path.parent, layout_ref.get("Path"), label
                )
                platform_path, platform_relative = safe_resource_file(
                    info_path.parent, platform_ref.get("Path"),
                    f"{info_path.parent.name}.Files.Platforms[{layout_id}]"
                )
                layout = read_plist(layout_path)
                platform = read_plist(platform_path)
                require(isinstance(layout, dict), f"{layout_relative} must contain a dictionary")
                require(isinstance(platform, dict), f"{platform_relative} must contain a dictionary")
                # A single AppleALC XML file can be shared by multiple
                # File/Id selectors. The reference Id is the runtime layout
                # selector; LayoutID inside the XML is preserved as metadata
                # and is not required to equal that selector.
                if "LayoutID" in layout:
                    integer(layout["LayoutID"], f"{layout_relative}.LayoutID")
                path_refs = layout.get("PathMapRef", [])
                require(isinstance(path_refs, list), f"{layout_relative}.PathMapRef must be an array")
                platform_maps = platform.get("PathMaps", [])
                require(isinstance(platform_maps, list), f"{platform_relative}.PathMaps must be an array")
                maps_by_id: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
                for map_index, path_map in enumerate(platform_maps):
                    require(isinstance(path_map, dict),
                            f"{platform_relative}.PathMaps[{map_index}] must be a dictionary")
                    map_id = integer(path_map.get("PathMapID"),
                                     f"{platform_relative}.PathMaps[{map_index}].PathMapID")
                    # AppleALC platforms may use repeated IDs for multiple
                    # parallel alternatives. Their XML order is meaningful to
                    # the route selector, so retain every matching map.
                    maps_by_id[map_id].append(path_map)
            except CompileError as error:
                issues.append({"codecDirectory": info_path.parent.name,
                               "layoutId": layout_id, "reason": str(error)})
                continue

            for path_index, path_ref in enumerate(path_refs):
                path_label = f"{layout_relative}.PathMapRef[{path_index}]"
                if not isinstance(path_ref, dict):
                    issues.append({"codecDirectory": info_path.parent.name,
                                   "layoutId": layout_id,
                                   "reason": f"{path_label} must be a dictionary"})
                    continue
                try:
                    pathmap_id = integer(path_ref.get("PathMapID"), f"{path_label}.PathMapID")
                    codec_ids = path_ref_codec_ids(path_ref, default_codec_id, path_label)
                    path_maps = maps_by_id.get(pathmap_id, [])
                    require(path_maps,
                            f"{platform_relative} has no referenced PathMapID {pathmap_id}")
                    chains = []
                    for duplicate_index, path_map in enumerate(path_maps):
                        chains.extend(flatten_path_map(
                            path_map.get("PathMap"),
                            f"{platform_relative}.PathMap[{pathmap_id}][{duplicate_index}]"
                        ))
                except CompileError as error:
                    issues.append({"codecDirectory": info_path.parent.name,
                                   "layoutId": layout_id,
                                   "pathmapId": path_ref.get("PathMapID"),
                                   "reason": str(error)})
                    continue

                path_control_keys = unsupported_properties(chains)
                for codec_id in codec_ids:
                    # Seed all function groups already present in PinConfigs;
                    # otherwise function group 1 is the generic HDA default.
                    groups = sorted(group for (entry_codec, entry_layout, group) in pin_entries
                                    if entry_codec == codec_id and entry_layout == layout_id)
                    if not groups:
                        groups = [1]
                    for function_group in groups:
                        key = (codec_id, layout_id, function_group)
                        profile = profiles.setdefault(
                            key, make_empty_profile(codec_id, layout_id, function_group)
                        )
                        profile["routes"].extend({
                            "pathmapId": pathmap_id,
                            "nodes": [item["NodeID"] for item in chain],
                        } for chain in chains)
                        if pathmap_id not in profile["pathmapIds"]:
                            profile["pathmapIds"].append(pathmap_id)
                        for route_key in path_control_keys:
                            profile.setdefault("_pathControlKeys", Counter())[route_key] += path_control_keys[route_key]
                        for route_key, count in unsupported_layout_properties(layout).items():
                            profile.setdefault("_layoutControlKeys", Counter())[route_key] += count
                        for route_key in platform:
                            if route_key != "PathMaps":
                                profile.setdefault("_platformControlKeys", Counter())[route_key] += 1
                        source_layout = f"{info_path.parent.name}/{layout_relative}"
                        source_platform = f"{info_path.parent.name}/{platform_relative}"
                        if source_layout not in profile["layoutSources"]:
                            profile["layoutSources"].append(source_layout)
                        if source_platform not in profile["platformSources"]:
                            profile["platformSources"].append(source_platform)

            # Info codec identity is a useful fallback only when XML omits the
            # CodecID selector. The source may intentionally map another codec.
            if not path_refs:
                issues.append({"codecDirectory": info_path.parent.name,
                               "layoutId": layout_id,
                               "reason": f"{layout_relative} contains no PathMapRef; no selector emitted"})

    # Attach active PinConfigs. Ambiguous duplicate records are never guessed.
    for key, entries in sorted(pin_entries.items()):
        profile = profiles.get(key)
        if profile is None:
            issues.append({"codecId": f"0x{key[0]:08x}", "layoutId": key[1],
                           "functionGroup": key[2],
                           "reason": "active PinConfig has no matching route selector; not emitted"})
            continue
        profile["pinEntries"] = [entry["_source_index"] for entry in entries]
        if len(entries) > 1:
            signatures = {(entry.get("ConfigData", b""), entry.get("WakeConfigData", b""),
                           bool(entry.get("WakeVerbReinit", False))) for entry in entries}
            if len(signatures) > 1:
                profile["pinConfigStatus"] = "ambiguous"
                add_reason(profile, "ambiguous_pin_config_entries",
                           sourceEntryIndices=[entry["_source_index"] for entry in entries])
                continue
            # Byte-identical duplicate records are redundant and safe to collapse.
        entry = entries[0]
        profile["pinConfigStatus"] = "present"
        if "WakeVerbReinit" in entry:
            require(type(entry["WakeVerbReinit"]) is bool,
                    f"HDAConfigDefault[{entry['_source_index']}].WakeVerbReinit must be boolean")
        if "FuncGroup" not in entry:
            add_reason(profile, "function_group_defaulted_to_audio_group_1")
        if "AFGLowPowerState" in entry:
            add_reason(profile, "afg_low_power_state_not_emitted")

        boot_verbs, unsupported_boot = parse_init_verbs(entry.get("ConfigData", b""), "ConfigData")
        if not entry.get("ConfigData"):
            profile["pinConfigStatus"] = "unsupported"
            add_reason(profile, "empty_config_data")
        elif unsupported_boot:
            profile["pinConfigStatus"] = "unsupported"
            add_reason(profile, "unsafe_boot_sequence_omitted",
                       unsupportedVerbs=unsupported_boot)
            # Never run only the recognized portion of an original sequence.
        else:
            profile["bootVerbs"] = boot_verbs
            pins, incomplete_nids = pin_defaults(boot_verbs)
            profile["pins"] = pins
            if incomplete_nids:
                add_reason(profile, "incomplete_pin_default_bytes",
                           nodeIds=[f"0x{nid:02x}" for nid in incomplete_nids])

        wake_data = entry.get("WakeConfigData", b"")
        wake_reinit = bool(entry.get("WakeVerbReinit", False))
        if wake_reinit and not entry.get("ConfigData"):
            add_reason(profile, "wake_reinit_without_safe_boot_config")
        elif wake_reinit and wake_data:
            wake_verbs, unsupported_wake = parse_init_verbs(wake_data, "WakeConfigData")
            if unsupported_wake:
                add_reason(profile, "unsafe_wake_sequence_omitted",
                           unsupportedVerbs=unsupported_wake)
            elif profile["pinConfigStatus"] != "unsupported":
                profile["wakeVerbs"] = wake_verbs
        elif wake_reinit and not wake_data:
            add_reason(profile, "wake_reinit_missing_wake_config_data")
        elif wake_data:
            add_reason(profile, "wake_config_data_not_reinitialized")

    # Route-only rows are valid. Unsupported controls and platform semantics
    # remain visible in the companion report, never silently treated as applied.
    for profile in profiles.values():
        path_keys = profile.pop("_pathControlKeys", Counter())
        if path_keys:
            add_reason(profile, "path_node_control_semantics_not_emitted",
                       controls=dict(sorted(path_keys.items())))
        layout_keys = profile.pop("_layoutControlKeys", Counter())
        if layout_keys:
            add_reason(profile, "layout_endpoint_semantics_not_emitted",
                       controls=dict(sorted(layout_keys.items())))
        platform_keys = profile.pop("_platformControlKeys", Counter())
        if platform_keys:
            add_reason(profile, "platform_semantics_not_emitted",
                       controls=dict(sorted(platform_keys.items())))
        if profile["pinConfigStatus"] == "absent":
            add_reason(profile, "no_active_pin_config_entry_route_only")
        # Keep each PathMap and each nested alternative in AppleALC source order.
        profile["pathmapIds"].sort()
        profile["layoutSources"].sort()
        profile["platformSources"].sort()
        profile["pinEntries"].sort()
        profile["unsupported"].sort(key=lambda item: item["code"])

    sorted_profiles = [profiles[key] for key in sorted(profiles)]
    report_profiles = []
    for profile in sorted_profiles:
        report_profiles.append({
            "codecId": f"0x{profile['codecId']:08x}",
            "layoutId": profile["layoutId"],
            "functionGroup": profile["functionGroup"],
            "pathmapIds": profile["pathmapIds"],
            "routes": profile["routes"],
            "routeCount": len(profile["routes"]),
            "pinCount": len(profile["pins"]),
            "bootVerbCount": len(profile["bootVerbs"]),
            "wakeVerbCount": len(profile["wakeVerbs"]),
            "pinConfigStatus": profile["pinConfigStatus"],
            "pinConfigEntryIndices": profile["pinEntries"],
            "source": {"layouts": profile["layoutSources"],
                       "platforms": profile["platformSources"]},
            "unsupported": profile["unsupported"],
        })

    route_only_count = sum(1 for profile in sorted_profiles if profile["pinConfigStatus"] == "absent")
    unsafe_pin_count = sum(1 for profile in sorted_profiles
                           if profile["pinConfigStatus"] in {"unsupported", "ambiguous"})
    report = {
        "schemaVersion": 1,
        "profileAbi": PROFILE_ABI,
        "source": "AppleALC Resources",
        "darwinKernelMajor": darwin_major,
        "compiledScope": [
            "full codec ID, layout ID and function group selector rows",
            "ordered PathMap node chains from layout XML and platform XML",
            "standard SET_CONFIG_DEFAULT bytes and EAPD init verbs only",
            "wake verbs only when WakeVerbReinit is enabled",
        ],
        "notCompiled": [
            "AppleHDA binary patches",
            "PathMap mixer, boost, mute, volume, mux, DSP and endpoint control semantics",
            "other non-standard PinConfig verbs and AFG power-state metadata",
        ],
        "summary": {
            "codecInfoFileCount": len(infos),
            "generatedProfileCount": len(sorted_profiles),
            "routeOnlyProfileCount": route_only_count,
            "unsafeOrAmbiguousPinConfigProfileCount": unsafe_pin_count,
            "profilesWithPinConfigCount": sum(1 for profile in sorted_profiles
                                               if profile["pinConfigStatus"] == "present"),
            "activeAppleHdaPatchRecordsNotEmitted": patch_summary["active"],
            "inactiveAppleHdaPatchRecords": patch_summary["inactive"],
            "catalogIssues": len(issues),
        },
        "appleHdaPatchInventory": patch_summary,
        "catalogIssues": sorted(issues, key=lambda item: json.dumps(item, sort_keys=True)),
        "profiles": report_profiles,
    }

    return {"profiles": sorted_profiles, "report": report}


def c_identifier(prefix: str, index: int) -> str:
    return f"{prefix}_{index}"


def c_verb_rows(verbs: Sequence[Dict[str, int]]) -> str:
    return "\n".join(
        f"\t{{ 0x{verb['nid']:02x}, 0x{verb['command']:03x}, 0x{verb['payload']:02x} }},"
        for verb in verbs
    )


def emit_header(profiles: Sequence[Dict[str, Any]]) -> str:
    lines = [
        "/* Generated from original AppleALC Resources; do not edit. */",
        "/* Only routes and standard reversible pin/EAPD verbs are compiled. */",
        "#ifndef NMX_NATIVE_PROFILES_GENERATED_H",
        "#define NMX_NATIVE_PROFILES_GENERATED_H",
        "#include <stddef.h>",
        "#include <stdint.h>",
        "",
        "struct nmx_native_pin { uint8_t nid; uint32_t config; };",
        "struct nmx_native_verb { uint8_t nid; uint16_t command; uint8_t payload; };",
        "struct nmx_native_route { uint8_t length; const uint8_t *nodes; };",
        "struct nmx_native_profile {",
        "\tuint32_t codec_id, layout_id;",
        "\tuint8_t function_group;",
        "\tconst struct nmx_native_pin *pins; uint16_t pin_count;",
        "\tconst struct nmx_native_verb *boot_verbs; uint16_t boot_count;",
        "\tconst struct nmx_native_verb *wake_verbs; uint16_t wake_count;",
        "\tconst struct nmx_native_route *routes; uint16_t route_count;",
        "};",
        "",
    ]

    for index, profile in enumerate(profiles):
        require(len(profile["pins"]) <= 0xFFFF and len(profile["bootVerbs"]) <= 0xFFFF and
                len(profile["wakeVerbs"]) <= 0xFFFF and len(profile["routes"]) <= 0xFFFF,
                f"profile counts exceed uint16 range for {profile['codecId']:#x}/"
                f"{profile['layoutId']}/{profile['functionGroup']}")
        if profile["pins"]:
            name = c_identifier("nmx_native_pins", index)
            lines.append(f"static const struct nmx_native_pin {name}[] = {{")
            lines.extend(f"\t{{ 0x{pin['nid']:02x}, 0x{pin['config']:08x}U }},"
                         for pin in profile["pins"])
            lines.extend(["};", ""])
        for prefix, key in (("boot_verbs", "bootVerbs"), ("wake_verbs", "wakeVerbs")):
            if profile[key]:
                name = c_identifier(f"nmx_native_{prefix}", index)
                lines.append(f"static const struct nmx_native_verb {name}[] = {{")
                lines.append(c_verb_rows(profile[key]))
                lines.extend(["};", ""])
        if profile["routes"]:
            for route_index, route in enumerate(profile["routes"]):
                require(0 < len(route["nodes"]) <= 0xFF,
                        f"route length is outside uint8 range for {profile['codecId']:#x}/"
                        f"{profile['layoutId']}/{profile['functionGroup']}")
                name = f"nmx_native_route_nodes_{index}_{route_index}"
                lines.append(f"static const uint8_t {name}[] = {{ "
                             + ", ".join(f"0x{node:02x}" for node in route["nodes"]) + " };")
            route_name = c_identifier("nmx_native_routes", index)
            lines.append(f"static const struct nmx_native_route {route_name}[] = {{")
            for route_index, route in enumerate(profile["routes"]):
                node_name = f"nmx_native_route_nodes_{index}_{route_index}"
                lines.append(f"\t{{ {len(route['nodes'])}, {node_name} }},")
            lines.extend(["};", ""])

    lines.append("static const struct nmx_native_profile nmx_native_profiles[] = {")
    for index, profile in enumerate(profiles):
        fields = [
            f"0x{profile['codecId']:08x}U",
            f"0x{profile['layoutId']:08x}U",
            f"{profile['functionGroup']}U",
            c_identifier("nmx_native_pins", index) if profile["pins"] else "NULL",
            f"{len(profile['pins'])}U",
            c_identifier("nmx_native_boot_verbs", index) if profile["bootVerbs"] else "NULL",
            f"{len(profile['bootVerbs'])}U",
            c_identifier("nmx_native_wake_verbs", index) if profile["wakeVerbs"] else "NULL",
            f"{len(profile['wakeVerbs'])}U",
            c_identifier("nmx_native_routes", index) if profile["routes"] else "NULL",
            f"{len(profile['routes'])}U",
        ]
        lines.append("\t{ " + ", ".join(fields) + " },")
    lines.extend([
        "};",
        "#define NMX_NATIVE_PROFILE_COUNT \\",
        "\t(sizeof(nmx_native_profiles) / sizeof(nmx_native_profiles[0]))",
        "#endif /* NMX_NATIVE_PROFILES_GENERATED_H */",
        "",
    ])
    return "\n".join(lines)


def write_output(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    require(not path.is_dir(), f"output path is a directory: {path}")
    path.write_text(content, encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resources", type=Path, help="original AppleALC Resources directory")
    parser.add_argument("header", type=Path, help="generated C header output path")
    parser.add_argument("--darwin-major", type=int, default=25,
                        help="active Darwin kernel major (default: 25 for macOS 26)")
    parser.add_argument("--report", type=Path, required=True,
                        help="JSON report for generated rows and unsupported source semantics")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        darwin_major = integer(args.darwin_major, "--darwin-major", 0, 255)
        compiled = compile_catalog(args.resources, darwin_major)
        header = emit_header(compiled["profiles"])
        report = json.dumps(compiled["report"], ensure_ascii=False, indent=2,
                            sort_keys=True) + "\n"
        write_output(args.header, header)
        write_output(args.report, report)
    except (CompileError, OSError) as error:
        print(f"generate-native-profiles: error: {error}", file=sys.stderr)
        return 2

    summary = compiled["report"]["summary"]
    print(
        f"Generated {summary['generatedProfileCount']} native selector rows for Darwin {darwin_major}; "
        f"{summary['routeOnlyProfileCount']} route-only, "
        f"{summary['unsafeOrAmbiguousPinConfigProfileCount']} unsafe/ambiguous PinConfigs, "
        f"{summary['catalogIssues']} catalog issues. See {args.report}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
