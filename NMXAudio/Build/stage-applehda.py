#!/usr/bin/env python3
"""Stage a locally supplied AppleHDA stack for an isolated AuxKC link check."""

import argparse
import struct
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile


APPLE_HDA_ID = "com.apple.driver.AppleHDA"
APPLE_MODULES = {
    "AppleHDAController.kext": "com.apple.driver.AppleHDAController",
    "AppleHDAHardwareConfigDriver.kext": "com.apple.driver.AppleHDAHardwareConfigDriver",
    "AppleMikeyDriver.kext": "com.apple.driver.AppleMikeyDriver",
    "DspFuncLib.kext": "com.apple.driver.DspFuncLib",
    "IOHDAFamily.kext": "com.apple.iokit.IOHDAFamily",
}
APPLE_BUNDLES = {
    "AppleHDAHALPlugIn.bundle": "com.apple.audio.AppleHDAHALPlugIn",
}
NATIVE_ID = "com.nullmoth.NMXAudio"
COMBINED_ID = "as.vit9696.AppleALC"
LILU_ID = "as.vit9696.Lilu"
CONTROLLER_READY = "NMXAudioEmbeddedControllerReady"
CODEC_READY = "NMXAudioEmbeddedCodecReady"

CPU_TYPE_X86_64 = 0x01000007
LC_SYMTAB = 0x2
N_STAB = 0xE0
N_TYPE = 0x0E
N_UNDF = 0x00
N_EXT = 0x01
APPLE_IMPORT_REDIRECTS = {
    b"_OSKextRequestResource": b"_NMXHDARequestResource",
    b"_OSKextCancelRequest": b"_NMXHDACancelRequest",
}


def _macho_slice(data, offset, size):
    """Return the byte order and symbol-table metadata for one x86_64 slice."""
    if offset < 0 or size < 32 or offset + size > len(data):
        raise ValueError("Invalid x86_64 Mach-O slice bounds")
    magic = bytes(data[offset:offset + 4])
    if magic == b"\xcf\xfa\xed\xfe":
        endian = "<"
    elif magic == b"\xfe\xed\xfa\xcf":
        endian = ">"
    else:
        raise ValueError("AppleHDA x86_64 slice is not a 64-bit Mach-O")

    _, cpu_type, _, _, command_count, commands_size, _, _ = struct.unpack_from(
        endian + "IiiIIIII", data, offset,
    )
    if cpu_type != CPU_TYPE_X86_64:
        raise ValueError("Mach-O slice table selected a non-x86_64 binary")
    commands_start = offset + 32
    commands_end = commands_start + commands_size
    slice_end = offset + size
    if commands_end > slice_end:
        raise ValueError("Mach-O load commands exceed the x86_64 slice")

    symtabs = []
    cursor = commands_start
    for _ in range(command_count):
        if cursor + 8 > commands_end:
            raise ValueError("Truncated Mach-O load command")
        command, command_size = struct.unpack_from(endian + "II", data, cursor)
        if command_size < 8 or cursor + command_size > commands_end:
            raise ValueError("Invalid Mach-O load command size")
        if command == LC_SYMTAB:
            if command_size < 24:
                raise ValueError("Truncated LC_SYMTAB command")
            symtabs.append(struct.unpack_from(endian + "IIII", data, cursor + 8))
        cursor += command_size
    if cursor != commands_end or len(symtabs) != 1:
        raise ValueError("Expected exactly one complete LC_SYMTAB command")

    symbol_offset, symbol_count, string_offset, string_size = symtabs[0]
    symbol_end = symbol_offset + symbol_count * 16
    string_end = string_offset + string_size
    if symbol_end > size or string_end > size or string_size == 0:
        raise ValueError("Mach-O symbol or string table exceeds the x86_64 slice")
    return endian, symbol_offset, symbol_count, string_offset, string_size


def _macho_architectures(data):
    """Return validated (cpu_type, offset, size) records from thin or fat Mach-O."""
    if len(data) < 4:
        raise ValueError("AppleHDA executable is truncated")
    magic = bytes(data[:4])
    fat_formats = {
        b"\xca\xfe\xba\xbe": (">", False),
        b"\xbe\xba\xfe\xca": ("<", False),
        b"\xca\xfe\xba\xbf": (">", True),
        b"\xbf\xba\xfe\xca": ("<", True),
    }
    if magic not in fat_formats:
        if magic in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"):
            endian = "<" if magic == b"\xcf\xfa\xed\xfe" else ">"
            cpu_type = struct.unpack_from(endian + "i", data, 4)[0]
            if cpu_type != CPU_TYPE_X86_64:
                raise ValueError("AppleHDA thin Mach-O is not x86_64")
            return [(cpu_type, 0, len(data))]
        raise ValueError("AppleHDA executable is not a supported Mach-O binary")

    endian, fat64 = fat_formats[magic]
    if len(data) < 8:
        raise ValueError("Truncated fat Mach-O header")
    architecture_count = struct.unpack_from(endian + "I", data, 4)[0]
    record_size = 32 if fat64 else 20
    table_end = 8 + architecture_count * record_size
    if architecture_count == 0 or table_end > len(data):
        raise ValueError("Invalid fat Mach-O architecture table")

    slices = []
    intervals = []
    for index in range(architecture_count):
        record = 8 + index * record_size
        if fat64:
            cpu_type, _, slice_offset, slice_size, _, _ = struct.unpack_from(
                endian + "iiQQII", data, record,
            )
        else:
            cpu_type, _, slice_offset, slice_size, _ = struct.unpack_from(
                endian + "iiIII", data, record,
            )
        if slice_offset < table_end or slice_size == 0 or slice_offset + slice_size > len(data):
            raise ValueError("Invalid fat Mach-O slice bounds")
        intervals.append((slice_offset, slice_offset + slice_size))
        slices.append((cpu_type, slice_offset, slice_size))
    intervals.sort()
    if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:])):
        raise ValueError("Overlapping fat Mach-O slices")
    if sum(cpu_type == CPU_TYPE_X86_64 for cpu_type, _, _ in slices) != 1:
        raise ValueError("Expected exactly one x86_64 AppleHDA slice")
    return slices


def _x86_64_symbols(data, architecture):
    """Parse every nlist_64 name/type in a validated x86_64 slice."""
    cpu_type, slice_offset, slice_size = architecture
    if cpu_type != CPU_TYPE_X86_64:
        raise ValueError("Requested symbol parse for a non-x86_64 slice")
    endian, symbol_offset, symbol_count, string_offset, string_size = _macho_slice(
        data, slice_offset, slice_size,
    )
    symbols = []
    for index in range(symbol_count):
        entry = slice_offset + symbol_offset + index * 16
        string_index, symbol_type = struct.unpack_from(endian + "IB", data, entry)
        if string_index >= string_size:
            raise ValueError("Mach-O symbol name index exceeds its string table")
        if string_index == 0:
            name = b""
        else:
            name_start = slice_offset + string_offset + string_index
            string_end = slice_offset + string_offset + string_size
            name_end = data.find(b"\0", name_start, string_end)
            if name_end < 0:
                raise ValueError("Unterminated Mach-O symbol name")
            name = bytes(data[name_start:name_end])
        symbols.append((index, entry, string_index, symbol_type, name))
    return symbols, (slice_offset, slice_size, string_offset, string_size)


def redirect_applehda_resource_imports(executable):
    """Rename only AppleHDA's two external undefined imports, preserving Mach-O layout."""
    executable = Path(executable)
    if executable.is_symlink() or not executable.is_file():
        raise ValueError("AppleHDA executable must be a regular file")
    original = executable.read_bytes()
    patched = bytearray(original)
    slices = _macho_architectures(original)
    x86_64 = next(item for item in slices if item[0] == CPU_TYPE_X86_64)
    symbols, (slice_offset, slice_size, string_offset, string_size) = _x86_64_symbols(
        original, x86_64,
    )

    references = {}
    found = {name: [] for name in APPLE_IMPORT_REDIRECTS}
    for symbol in symbols:
        _, _, string_index, symbol_type, name = symbol
        if name in APPLE_IMPORT_REDIRECTS:
            if symbol_type & N_STAB or symbol_type & N_TYPE != N_UNDF or not symbol_type & N_EXT:
                raise ValueError(f"Refusing to redirect non-external undefined symbol {name!r}")
            found[name].append(symbol)
        elif name in APPLE_IMPORT_REDIRECTS.values():
            raise ValueError(f"AppleHDA already contains a redirected symbol {name!r}")
        if string_index:
            references.setdefault(string_index, []).append(symbol)

    for original_name, replacement in APPLE_IMPORT_REDIRECTS.items():
        entries = found[original_name]
        if not entries:
            raise ValueError(f"Missing AppleHDA import {original_name.decode()}")
        if len(original_name) != len(replacement):
            raise ValueError(f"Import redirect changes symbol-string length for {original_name!r}")
        for _, _, string_index, _, _ in entries:
            # String-table entries may be shared. If so, every symbol that
            # references the bytes must be one of the exact imports redirected.
            shared = references.get(string_index, [])
            if not shared or any(entry not in entries for entry in shared):
                raise ValueError(f"AppleHDA shares {original_name.decode()} with another symbol")
            name_start = slice_offset + string_offset + string_index
            if name_start + len(original_name) >= slice_offset + string_offset + string_size:
                raise ValueError("AppleHDA import string exceeds the string table")
            if bytes(original[name_start:name_start + len(original_name)]) != original_name:
                raise ValueError("AppleHDA import string bytes do not match its symbol entry")
            patched[name_start:name_start + len(replacement)] = replacement

    # Prove every symbol name changed only when it was the target undefined
    # import. Also compare the entire file bytewise so FAT non-x86_64 slices,
    # load commands, symbol records and all unrelated bytes remain untouched.
    after_symbols, _ = _x86_64_symbols(patched, x86_64)
    for before, after in zip(symbols, after_symbols):
        expected_name = APPLE_IMPORT_REDIRECTS.get(before[4], before[4])
        if before[:4] != after[:4] or after[4] != expected_name:
            raise ValueError("Unexpected Mach-O symbol-table change during import redirect")
    expected = bytearray(original)
    for _, _, string_index, _, name in symbols:
        if name in APPLE_IMPORT_REDIRECTS:
            start = slice_offset + string_offset + string_index
            expected[start:start + len(name)] = APPLE_IMPORT_REDIRECTS[name]
    if patched != expected:
        raise ValueError("Import redirect modified bytes outside the two target symbol strings")
    executable.write_bytes(patched)
    return {old.decode(): len(entries) for old, entries in found.items()}


def bind_applehda_to_outer_applealc(applehda, outer_info):
    """Declare the exact AppleALC host version required by the embedded HDA kext."""
    if outer_info.get("CFBundleIdentifier") != COMBINED_ID:
        raise ValueError("Embedded AppleHDA host is not the AppleALC-compatible bundle")
    version = outer_info.get("CFBundleVersion")
    compatible_version = outer_info.get("OSBundleCompatibleVersion")
    if not isinstance(version, str) or not version or not isinstance(compatible_version, str) or not compatible_version:
        raise ValueError("The outer AppleALC bundle must declare current and compatible versions")
    plist = Path(applehda) / "Contents/Info.plist"
    info = plistlib.loads(plist.read_bytes())
    if info.get("CFBundleIdentifier") != APPLE_HDA_ID:
        raise ValueError("Cannot bind an unexpected nested kext to AppleALC")
    libraries = info.get("OSBundleLibraries")
    if not isinstance(libraries, dict):
        raise ValueError("Embedded AppleHDA has no OSBundleLibraries dictionary")
    libraries[COMBINED_ID] = version
    architecture_libraries = info.get("OSBundleLibraries_x86_64")
    if architecture_libraries is not None:
        if not isinstance(architecture_libraries, dict):
            raise ValueError("Embedded AppleHDA has an invalid x86_64 dependency dictionary")
        architecture_libraries[COMBINED_ID] = version
    plist.write_bytes(plistlib.dumps(info, sort_keys=False))
    return version


def verify_applehda_host_binding(applehda, expected_version, expected_imports):
    """Verify the staged host dependency and redirected nlist_64 imports."""
    plist = Path(applehda) / "Contents/Info.plist"
    info = plistlib.loads(plist.read_bytes())
    if info.get("OSBundleLibraries", {}).get(COMBINED_ID) != expected_version:
        raise ValueError("Embedded AppleHDA does not depend on the exact outer AppleALC version")
    architecture_libraries = info.get("OSBundleLibraries_x86_64")
    if architecture_libraries is not None and architecture_libraries.get(COMBINED_ID) != expected_version:
        raise ValueError("Embedded AppleHDA x86_64 dependency does not match the outer host version")
    executable = info.get("CFBundleExecutable")
    if not isinstance(executable, str) or not executable:
        raise ValueError("Embedded AppleHDA has no executable name")
    binary = Path(applehda) / "Contents/MacOS" / executable
    data = binary.read_bytes()
    architecture = next(item for item in _macho_architectures(data) if item[0] == CPU_TYPE_X86_64)
    symbols, _ = _x86_64_symbols(data, architecture)
    names = [symbol[4] for symbol in symbols]
    for original_name, replacement in APPLE_IMPORT_REDIRECTS.items():
        if original_name in names:
            raise ValueError(f"Unredirected AppleHDA import remains: {original_name.decode()}")
        redirected = [symbol for symbol in symbols if symbol[4] == replacement]
        if len(redirected) != expected_imports[original_name.decode()] or any(
            symbol[3] & N_STAB or symbol[3] & N_TYPE != N_UNDF or not symbol[3] & N_EXT
            for symbol in redirected
        ):
            raise ValueError(f"Expected external undefined host import {replacement.decode()}")


def verify_bundle(bundle, identifier, apple_anchor=True):
    command = ["/usr/bin/codesign", "--verify", "--strict", "--deep"]
    if apple_anchor:
        command.append(f'-R=anchor apple and identifier "{identifier}"')
    command.append(str(bundle))
    subprocess.run(command, check=True)
    contents = bundle / "Contents"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    executable = info["CFBundleExecutable"]
    if info["CFBundleIdentifier"] != identifier or executable != bundle.stem:
        raise ValueError(f"Unexpected AppleHDA bundle metadata for {bundle.name}")
    subprocess.run(
        ["/usr/bin/lipo", str(contents / "MacOS" / executable), "-verify_arch", "x86_64"], check=True,
    )


def verify_apple_stack(applehda, apple_anchor):
    """Validate the original Apple stack before edits, or the sealed staged copy."""
    verify_bundle(applehda, APPLE_HDA_ID, apple_anchor)

    child_dir = applehda / "Contents/PlugIns"
    expected_kexts = set(APPLE_MODULES)
    expected_bundles = set(APPLE_BUNDLES)
    actual_kexts = {path.name for path in child_dir.glob("*.kext")}
    actual_bundles = {path.name for path in child_dir.glob("*.bundle")}
    if actual_kexts != expected_kexts:
        raise ValueError(f"Unexpected AppleHDA child kexts: {sorted(actual_kexts)}")
    if actual_bundles != expected_bundles:
        raise ValueError(f"Unexpected AppleHDA child bundles: {sorted(actual_bundles)}")

    for name, identifier in {**APPLE_MODULES, **APPLE_BUNDLES}.items():
        bundle = child_dir / name
        verify_bundle(bundle, identifier, apple_anchor)


def restrict_matching(applehda):
    controller = applehda / "Contents/PlugIns/AppleHDAController.kext"
    codec = applehda
    gated_bundles = [applehda / "Contents/PlugIns" / name for name in APPLE_MODULES] + [applehda]
    for bundle in gated_bundles:
        plist = bundle / "Contents/Info.plist"
        info = plistlib.loads(plist.read_bytes())
        info["OSBundleRequired"] = "Auxiliary"
        plist.write_bytes(plistlib.dumps(info, sort_keys=False))

    for bundle, match_key, requirement in (
        (controller, "IOPropertyMatch", {CONTROLLER_READY: True}),
        (codec, "IOParentMatch", {"IOPropertyMatch": {CODEC_READY: True}}),
    ):
        plist = bundle / "Contents/Info.plist"
        info = plistlib.loads(plist.read_bytes())
        for personality in info["IOKitPersonalities"].values():
            if match_key in personality:
                raise ValueError(f"{bundle.name} already defines {match_key}")
            personality[match_key] = requirement
        plist.write_bytes(plistlib.dumps(info, sort_keys=False))
    return gated_bundles


def stage_bundle(source, applehda, output, gated):
    if source.name != "NMXAudio.kext" or applehda.name != "AppleHDA.kext":
        raise ValueError("Expected NMXAudio.kext and AppleHDA.kext")
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    subprocess.run(["/usr/bin/codesign", "--verify", "--strict", "--deep", str(source)], check=True)
    # Prove the supplied Apple payload still has Apple's original signatures
    # before copying or changing any matching property.
    verify_apple_stack(applehda, apple_anchor=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as work:
        staging = Path(work) / "auxkc-link-check"
        staging.mkdir()
        candidate = staging / source.name
        shutil.copytree(source, candidate, symlinks=True)
        info = plistlib.loads((candidate / "Contents/Info.plist").read_bytes())
        identifier = info["CFBundleIdentifier"]
        if info["CFBundleExecutable"] != "NMXAudio" or identifier not in (NATIVE_ID, COMBINED_ID):
            raise ValueError("Expected a native or AppleALC-combined NMXAudio build")
        aux_host = (identifier == COMBINED_ID and
                    info.get("NMXAudioEmbeddedAppleHDAHost") is True)
        needs_lilu = identifier == COMBINED_ID and not aux_host
        if (LILU_ID in info["OSBundleLibraries"]) != needs_lilu:
            raise ValueError("Unexpected Lilu dependency for this NMXAudio build")
        if aux_host and not gated:
            raise ValueError("The standalone AppleALC host requires gated AppleHDA matching")
        plugins = candidate / "Contents/PlugIns"
        plugins.mkdir(exist_ok=True)
        if any(plugins.iterdir()):
            raise ValueError("NMXAudio already contains plug-ins; refusing to create AppleHDA siblings")
        nested = plugins / applehda.name
        shutil.copytree(applehda, nested, symlinks=True)
        verify_apple_stack(nested, apple_anchor=True)
        subprocess.run(["/usr/bin/xattr", "-cr", str(candidate)], check=True)
        changed_bundles = []
        redirected_imports = None
        outer_host_version = None
        if aux_host:
            # AppleHDA first passed Apple's original signature check above.
            # Redirect its two dynamic imports to the already-loaded outer
            # AppleALC bundle, then declare the exact provider version before
            # any nested code is re-signed.
            applehda_executable = nested / "Contents/MacOS/AppleHDA"
            redirected_imports = redirect_applehda_resource_imports(applehda_executable)
            outer_host_version = bind_applehda_to_outer_applealc(nested, info)
        if gated:
            changed_bundles = restrict_matching(nested)
        # In gated mode, all six Apple kext Info.plists were changed. Re-sign
        # the five children first, then AppleHDA, then NMXAudio. The HAL bundle
        # is unchanged and retains its verified Apple signature.
        for bundle in changed_bundles:
            subprocess.run(
                ["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(bundle)], check=True,
            )
        subprocess.run(
            ["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(candidate)], check=True,
        )
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", "--deep", str(candidate)], check=True)
        verify_apple_stack(nested, apple_anchor=False)
        if aux_host:
            verify_applehda_host_binding(nested, outer_host_version, redirected_imports)
        staging.rename(output)
    return needs_lilu


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nmxaudio", type=Path)
    parser.add_argument("applehda", type=Path)
    parser.add_argument("output", type=Path, help="new directory for the offline link check")
    parser.add_argument("--gated", action="store_true",
                        help="gate IOKit matching for standalone startup tests")
    args = parser.parse_args()
    try:
        needs_lilu = stage_bundle(args.nmxaudio, args.applehda, args.output, args.gated)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")
    print(f"Staged {args.output / 'NMXAudio.kext'} for an offline link check.")
    if needs_lilu:
        print(f"The AppleALC build requires {LILU_ID} visible to the collection linker.")


if __name__ == "__main__":
    main()
