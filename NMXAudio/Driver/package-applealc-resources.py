#!/usr/bin/env python3
"""Package or verify the byte-exact AppleALC Resources catalog for NMXAudio."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import shutil
import stat
import sys
import tempfile
import zipfile

from applealc_plist import load_plist

ARCHIVE = "Resources.zip"
LICENSE = "LICENSE.txt"
MANIFEST = "AppleALC-Resources.manifest.json"
OUTPUT_FILES = {ARCHIVE, LICENSE, MANIFEST}
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
ZIP_MODE = (stat.S_IFREG | 0o644) << 16


class PackageError(Exception):
    pass


def die(message):
    raise PackageError(message)


def finder_metadata(parts):
    return any(p == ".DS_Store" or p.startswith("._") or p == "__MACOSX" for p in parts)


def safe_path(value):
    path = PurePosixPath(str(value))
    text = path.as_posix()
    if not text or text.startswith("/") or "\\" in text or "\x00" in text:
        die(f"unsafe resource path: {value!r}")
    if any(part in ("", ".", "..") for part in path.parts) or text != str(value):
        die(f"non-canonical resource path: {value!r}")
    return text


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path):
    try:
        data = path.read_bytes()
    except OSError as error:
        die(f"cannot read {path}: {error}")
    return len(data), sha256(data)


def validate_payload(path):
    """Parse AppleALC plists and XML plist fragments without rewriting them."""
    if path.suffix not in (".plist", ".xml"):
        return
    try:
        data = path.read_bytes()
        parsed = load_plist(data, allow_xml_fragment=path.suffix == ".xml")
    except Exception as error:
        die(f"cannot parse AppleALC payload {path}: {error}")
    if path.suffix == ".xml" and not isinstance(parsed, dict):
        die(f"AppleALC XML payload must have a dict root: {path}")


def scan_resources(root):
    if root.is_symlink() or not root.is_dir():
        die(f"AppleALC Resources directory missing or is a symlink: {root}")
    records = []
    for current, directories, files in os.walk(root, followlinks=False):
        current = Path(current)
        keep_dirs = []
        for name in sorted(directories):
            path = current / name
            relative = PurePosixPath(path.relative_to(root).as_posix())
            if path.is_symlink():
                die(f"symlink in AppleALC Resources: {relative}")
            if finder_metadata(relative.parts):
                continue
            if not path.is_dir():
                die(f"unsupported object in AppleALC Resources: {relative}")
            keep_dirs.append(name)
        directories[:] = keep_dirs
        for name in sorted(files):
            path = current / name
            relative = PurePosixPath(path.relative_to(root).as_posix())
            rel = safe_path(relative.as_posix())
            if path.is_symlink():
                die(f"symlink in AppleALC Resources: {rel}")
            if finder_metadata(relative.parts):
                continue
            if not path.is_file():
                die(f"unsupported object in AppleALC Resources: {rel}")
            validate_payload(path)
            size, digest = file_hash(path)
            records.append({"path": rel, "sizeBytes": size, "sha256": digest})
    records.sort(key=lambda item: item["path"])
    if not records:
        die("AppleALC Resources is empty")
    return records


def read_plist(path):
    try:
        return load_plist(path.read_bytes())
    except (OSError, plistlib.InvalidFileException, ValueError) as error:
        die(f"cannot parse {path}: {error}")


def validate_reference(codec_dir, record, plist_path, kind):
    if not isinstance(record, dict) or not isinstance(record.get("Path"), str):
        die(f"invalid {kind} reference in {plist_path}")
    value = record["Path"]
    path = PurePosixPath(value)
    if path.is_absolute() or "\\" in value or any(part == ".." for part in path.parts):
        die(f"unsafe {kind} reference {value!r} in {plist_path}")
    if value.endswith(".xml.zlib"):
        source_name = value[:-5]
    elif value.endswith(".xml"):
        source_name = value
    else:
        die(f"unexpected {kind} reference {value!r} in {plist_path}")
    source = codec_dir.joinpath(*PurePosixPath(source_name).parts)
    if source.is_symlink() or not source.is_file():
        die(f"unresolved {kind} reference {value!r} in {plist_path}")


def inspect_catalog(root, records):
    codec_plists = sorted(p for p in root.glob("*/Info.plist") if p.parent.name != "PinConfigs.kext")
    layouts = platforms = patches = 0
    for path in codec_plists:
        info = read_plist(path)
        if not isinstance(info.get("CodecName"), str) or not isinstance(info.get("CodecID"), int):
            die(f"missing codec identity in {path}")
        files = info.get("Files", {})
        if not isinstance(files, dict):
            die(f"missing Files dictionary in {path}")
        for key, kind in (("Layouts", "layout"), ("Platforms", "platform")):
            references = files.get(key, [])
            if not isinstance(references, list):
                die(f"invalid {key} references in {path}")
            for ref in references:
                validate_reference(path.parent, ref, path, kind)
            if key == "Layouts":
                layouts += len(references)
            else:
                platforms += len(references)
        codec_patches = info.get("Patches", [])
        if not isinstance(codec_patches, list):
            die(f"invalid Patches array in {path}")
        patches += len(codec_patches)

    controllers = read_plist(root / "Controllers.plist")
    pinconfigs = read_plist(root / "PinConfigs.kext/Contents/Info.plist")
    if not isinstance(controllers, list):
        die("Controllers.plist must contain a list")
    personalities = pinconfigs.get("IOKitPersonalities", {})
    pin_entries = sum(
        len(personality.get("HDAConfigDefault", []))
        for personality in personalities.values()
        if isinstance(personality, dict)
    )
    return {
        "resourceFileCount": len(records),
        "resourceBytes": sum(item["sizeBytes"] for item in records),
        "codecCount": len(codec_plists),
        "layoutReferenceCount": layouts,
        "platformReferenceCount": platforms,
        "controllerEntryCount": len(controllers),
        "pinConfigEntryCount": pin_entries,
        "codecPatchRecordCount": patches,
    }


def manifest_bytes(records, summary, archive_size, archive_sha, license_size, license_sha):
    document = {
        "schemaVersion": 1,
        "catalog": "AppleALC Resources",
        "archive": {"file": ARCHIVE, "sizeBytes": archive_size, "sha256": archive_sha},
        "license": {"file": LICENSE, "sizeBytes": license_size, "sha256": license_sha},
        "summary": summary,
        "resourceFiles": records,
    }
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def create_archive(root, records, target):
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.comment = b""
        for record in records:
            name = record["path"]
            source = root.joinpath(*PurePosixPath(name).parts)
            data = source.read_bytes()
            if len(data) != record["sizeBytes"] or sha256(data) != record["sha256"]:
                die(f"source changed while packaging: {name}")
            info = zipfile.ZipInfo(name, ZIP_DATE)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = ZIP_MODE
            info.extra = b""
            info.comment = b""
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def package_inventory(root):
    if root.is_symlink() or not root.is_dir():
        die(f"package directory missing or is a symlink: {root}")
    found = []
    for current, directories, files in os.walk(root, followlinks=False):
        current = Path(current)
        for name in directories:
            path = current / name
            relative = safe_path(path.relative_to(root).as_posix())
            if path.is_symlink():
                die(f"symlink in package: {relative}")
            found.append((relative, "dir"))
        for name in files:
            path = current / name
            relative = safe_path(path.relative_to(root).as_posix())
            if path.is_symlink():
                die(f"symlink in package: {relative}")
            if not path.is_file():
                die(f"unsupported object in package: {relative}")
            found.append((relative, "file"))
    return sorted(found)


def verify(root, destination, license_path, records, summary):
    expected = sorted((name, "file") for name in OUTPUT_FILES)
    found = package_inventory(destination)
    if found != expected:
        die(f"package must contain exactly {sorted(OUTPUT_FILES)}; found {found}")

    license_size, license_sha = file_hash(license_path)
    packaged_license_size, packaged_license_sha = file_hash(destination / LICENSE)
    if (license_size, license_sha) != (packaged_license_size, packaged_license_sha):
        die("bundled LICENSE.txt differs from the AppleALC source license")

    try:
        with zipfile.ZipFile(destination / ARCHIVE) as archive:
            if archive.comment:
                die("unexpected ZIP comment")
            infos = archive.infolist()
            names = [info.filename for info in infos]
            expected_names = [item["path"] for item in records]
            if names != expected_names:
                die("Resources.zip inventory or ordering differs from current AppleALC Resources")
            for info, record in zip(infos, records):
                safe_path(info.filename)
                mode = info.external_attr
                if (
                    info.is_dir()
                    or mode != ZIP_MODE
                    or info.date_time != ZIP_DATE
                    or info.create_system != 3
                    or info.compress_type != zipfile.ZIP_DEFLATED
                    or info.extra
                    or info.comment
                ):
                    die(f"unexpected ZIP metadata: {info.filename}")
                data = archive.read(info)
                if len(data) != record["sizeBytes"] or sha256(data) != record["sha256"]:
                    die(f"resource hash/size mismatch: {info.filename}")
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        die(f"cannot verify Resources.zip: {error}")

    archive_size, archive_sha = file_hash(destination / ARCHIVE)
    expected_manifest = manifest_bytes(records, summary, archive_size, archive_sha, license_size, license_sha)
    if (destination / MANIFEST).read_bytes() != expected_manifest:
        die("manifest differs from the current source inventory or package hashes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("applealc_resources", type=Path, help="AppleALC source Resources directory")
    parser.add_argument("destination", type=Path, help="NMXAudio.kext/Contents/Resources/AppleALC")
    parser.add_argument("--license", type=Path, help="license path (default: LICENSE.txt beside Resources)")
    parser.add_argument("--verify", action="store_true", help="verify an existing package; do not modify it")
    args = parser.parse_args()

    root = args.applealc_resources
    destination = args.destination
    license_path = args.license or root.parent / "LICENSE.txt"
    if license_path.is_symlink() or not license_path.is_file():
        die(f"AppleALC LICENSE.txt missing or is a symlink: {license_path}")
    records = scan_resources(root)
    summary = inspect_catalog(root, records)

    if args.verify:
        verify(root, destination, license_path, records, summary)
        print(
            "Verified: {codecCount} codecs, {layoutReferenceCount} layout refs, "
            "{platformReferenceCount} platform refs, {resourceFileCount} files".format(**summary)
        )
        return

    if destination.exists() or destination.is_symlink():
        die(f"destination already exists: {destination}")
    if not destination.parent.is_dir():
        die(f"destination parent does not exist: {destination.parent}")
    try:
        destination.resolve().relative_to(root.resolve())
    except ValueError:
        pass
    else:
        die("destination must be outside the source Resources directory")

    stage = Path(tempfile.mkdtemp(prefix=".applealc-resources-", dir=str(destination.parent)))
    try:
        create_archive(root, records, stage / ARCHIVE)
        shutil.copyfile(license_path, stage / LICENSE)
        archive_size, archive_sha = file_hash(stage / ARCHIVE)
        license_size, license_sha = file_hash(stage / LICENSE)
        (stage / MANIFEST).write_bytes(
            manifest_bytes(records, summary, archive_size, archive_sha, license_size, license_sha)
        )
        verify(root, stage, license_path, records, summary)
        for item in stage.iterdir():
            item.chmod(0o644)
        stage.chmod(0o755)
        os.rename(stage, destination)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    print(
        "Packaged {resourceFileCount} AppleALC resource files ({codecCount} codecs): {destination}".format(
            destination=destination, **summary
        )
    )


if __name__ == "__main__":
    try:
        main()
    except PackageError as error:
        print(f"AppleALC resource package: {error}", file=sys.stderr)
        sys.exit(1)
