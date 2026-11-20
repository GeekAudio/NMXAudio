#!/usr/bin/env python3
"""Package the linked AppleALC and NMXAudio image as NMXAudio.kext."""
import argparse
from pathlib import Path
import plistlib
import re
import shutil
import subprocess

APPLEALC_ID = "as.vit9696.AppleALC"


def dependency_version(version):
    match = re.fullmatch(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:(d|a|b|fc)(\d+))?", version)
    if match is None:
        raise ValueError(f"Invalid kext dependency version {version}")
    stages = {"d": 0, "a": 1, "b": 2, "fc": 3, None: 4}
    major, minor, patch, stage, build = match.groups()
    return int(major), int(minor or 0), int(patch or 0), stages[stage], int(build or 0)


def plist(path):
    return plistlib.loads(path.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("linked_kext", type=Path)
    parser.add_argument("nmx_info", type=Path)
    parser.add_argument("pins_info", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    combined = args.output / "NMXAudio.kext"
    original = args.linked_kext / "Contents"
    info = plist(original / "Info.plist")
    if info["CFBundleIdentifier"] != APPLEALC_ID:
        raise ValueError("Expected the AppleALC bundle identifier from the linked image")
    personalities = info["IOKitPersonalities"]
    pins = plist(args.pins_info)["IOKitPersonalities"][APPLEALC_ID]
    alc = personalities[APPLEALC_ID]
    for key, value in pins.items():
        if key in alc and alc[key] != value:
            raise ValueError(f"Incompatible AppleALC pin personality property {key}")
        alc[key] = value

    nmx = plist(args.nmx_info)
    for name, entry in nmx["IOKitPersonalities"].items():
        if name in personalities:
            raise ValueError(f"Conflicting IOKit personality name {name}")
        personalities[name] = dict(entry, CFBundleIdentifier=APPLEALC_ID)
    libraries = info["OSBundleLibraries"]
    for library, version in nmx["OSBundleLibraries"].items():
        if library not in libraries or dependency_version(libraries[library]) < dependency_version(version):
            libraries[library] = version

    executable_name = info["CFBundleExecutable"]
    info.update(CFBundleName="NMXAudio", CFBundleExecutable="NMXAudio")
    shutil.copytree(args.linked_kext, combined, symlinks=True)
    contents = combined / "Contents"
    pin_plugin = contents / "PlugIns/PinConfigs.kext"
    if pin_plugin.is_dir():
        if plist(pin_plugin / "Contents/Info.plist")["CFBundleIdentifier"] != "as.vit9696.PinConfigs":
            raise ValueError("Unexpected nested PinConfigs bundle")
        shutil.rmtree(pin_plugin)
        pin_plugin.parent.rmdir()
    binary = contents / "MacOS" / executable_name
    binary.rename(binary.with_name("NMXAudio"))
    (contents / "Info.plist").write_bytes(plistlib.dumps(info, sort_keys=False))
    subprocess.run(["/usr/bin/xattr", "-cr", str(combined)], check=True)
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(combined)], check=True)
    print(combined)


if __name__ == "__main__":
    main()
