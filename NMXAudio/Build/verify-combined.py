#!/usr/bin/env python3
"""Check a single NMXAudio bundle against the AppleALC source it contains."""
import argparse
from pathlib import Path
import plistlib
import struct
import subprocess

APPLEALC_ID = "as.vit9696.AppleALC"
APPLE_HDA_ID = "com.apple.driver.AppleHDA"
APPLE_HDA_CHILDREN = {
    "AppleHDAController.kext": "com.apple.driver.AppleHDAController",
    "AppleHDAHardwareConfigDriver.kext": "com.apple.driver.AppleHDAHardwareConfigDriver",
    "AppleMikeyDriver.kext": "com.apple.driver.AppleMikeyDriver",
    "DspFuncLib.kext": "com.apple.driver.DspFuncLib",
    "IOHDAFamily.kext": "com.apple.iokit.IOHDAFamily",
}
APPLE_HDA_BUNDLES = {
    "AppleHDAHALPlugIn.bundle": "com.apple.audio.AppleHDAHALPlugIn",
}
CONTROLLER_READY = "NMXAudioEmbeddedControllerReady"
CODEC_READY = "NMXAudioEmbeddedCodecReady"
RESOURCE_IMPORTS = {"_NMXHDARequestResource", "_NMXHDACancelRequest"}
SYSTEM_RESOURCE_IMPORTS = {"_OSKextRequestResource", "_OSKextCancelRequest"}
HAL_SETTER = "_NMXHDASetEmbeddedRegistryCStringProperty"


def command(*args):
    return subprocess.check_output(args, text=True)


def verify_apple_bundle(bundle, identifier, require_auxiliary=False):
    info = plistlib.loads((bundle / "Contents/Info.plist").read_bytes())
    executable = bundle.stem
    assert info.get("CFBundleIdentifier") == identifier, (bundle, info.get("CFBundleIdentifier"))
    assert info.get("CFBundleExecutable") == executable, (bundle, info.get("CFBundleExecutable"))
    if require_auxiliary:
        assert info.get("OSBundleRequired") == "Auxiliary", (bundle, info.get("OSBundleRequired"))
    command("/usr/bin/xcrun", "lipo", str(bundle / "Contents/MacOS" / executable),
            "-verify_arch", "x86_64")


def verify_embedded_applehda(contents):
    plugins = contents / "PlugIns"
    assert sorted(path.name for path in plugins.iterdir()) == ["AppleHDA.kext"], (
        "AppleHDA must be the only outer plug-in; child kexts cannot be siblings"
    )
    applehda = plugins / "AppleHDA.kext"
    verify_apple_bundle(applehda, APPLE_HDA_ID, require_auxiliary=True)
    apple_plugins = applehda / "Contents/PlugIns"
    assert {path.name for path in apple_plugins.glob("*.kext")} == set(APPLE_HDA_CHILDREN)
    assert {path.name for path in apple_plugins.glob("*.bundle")} == set(APPLE_HDA_BUNDLES)
    for name, identifier in APPLE_HDA_CHILDREN.items():
        verify_apple_bundle(apple_plugins / name, identifier, require_auxiliary=True)
    for name, identifier in APPLE_HDA_BUNDLES.items():
        verify_apple_bundle(apple_plugins / name, identifier)

    expected_kexts = {
        "PlugIns/AppleHDA.kext",
        *(f"PlugIns/AppleHDA.kext/Contents/PlugIns/{name}" for name in APPLE_HDA_CHILDREN),
    }
    actual_kexts = {path.relative_to(contents).as_posix() for path in contents.rglob("*.kext")}
    assert actual_kexts == expected_kexts, ("unexpected or misplaced embedded kexts", actual_kexts)
    command("/usr/bin/codesign", "--verify", "--strict", "--deep", str(applehda))

    controller = plistlib.loads(
        (apple_plugins / "AppleHDAController.kext/Contents/Info.plist").read_bytes()
    )
    controller_personalities = controller.get("IOKitPersonalities", {}).values()
    controller_personalities = list(controller_personalities)
    assert controller_personalities, "AppleHDAController has no match personalities"
    for personality in controller_personalities:
        match = personality.get("IOPropertyMatch", {})
        assert match.get(CONTROLLER_READY) is True, "nested AppleHDAController is not gated"

    applehda_info = plistlib.loads((applehda / "Contents/Info.plist").read_bytes())
    outer_info = plistlib.loads((contents / "Info.plist").read_bytes())
    assert applehda_info["OSBundleLibraries"][APPLEALC_ID] == outer_info["CFBundleVersion"]
    imports = set(command("/usr/bin/xcrun", "nm", "-arch", "x86_64", "-ju",
                          str(applehda / "Contents/MacOS/AppleHDA")).splitlines())
    assert RESOURCE_IMPORTS <= imports, "AppleHDA must import both resource functions from NMXAudio"
    assert not SYSTEM_RESOURCE_IMPORTS & imports, "unredirected AppleHDA resource imports"
    codec_personalities = applehda_info.get("IOKitPersonalities", {}).values()
    codec_personalities = list(codec_personalities)
    assert codec_personalities, "AppleHDA has no codec match personalities"
    for personality in codec_personalities:
        parent_match = personality.get("IOParentMatch", {})
        match = parent_match.get("IOPropertyMatch", {}) if isinstance(parent_match, dict) else {}
        assert match.get(CODEC_READY) is True, "AppleHDA codec personality is not gated"



def verify_module_entrypoints(executable, aux_host):
    """Follow the x86_64 kmod metadata through the libkmod entry stubs."""
    image = executable.read_bytes()
    header = struct.Struct("<8I")
    magic, _, _, _, count, _, _, _ = header.unpack_from(image)
    assert magic == 0xFEEDFACF
    segments = []
    offset = header.size
    for _ in range(count):
        opcode, size = struct.unpack_from("<II", image, offset)
        if opcode == 0x19:  # LC_SEGMENT_64
            _, address, _, file_offset, file_size = struct.unpack_from("<16s4Q", image, offset + 8)
            if file_size:
                segments.append((address, file_offset, file_size))
        offset += size

    def read_at(address, size):
        for base, file_offset, length in segments:
            delta = address - base
            if 0 <= delta and delta + size <= length:
                start = file_offset + delta
                return image[start:start + size]
        raise ValueError(f"Module entry address outside the image: {address:#x}")

    listing = command("/usr/bin/xcrun", "nm", "-nm", str(executable))
    kmod_line, = [line for line in listing.splitlines() if line.endswith(" _kmod_info")]
    kmod_address = int(kmod_line.split()[0], 16)
    metadata = struct.Struct("<QiI64s64si6Q")  # XNU kmod_info_64_v1, packed to four bytes
    _, version, _, identifier, *_, start, stop = metadata.unpack(read_at(kmod_address, metadata.size))
    assert version == 1 and identifier.rstrip(b"\0").decode() == APPLEALC_ID

    start_hook = "<_nmx_audio_embedded_init>" if aux_host else "<_AppleALC_kern_start>"
    stop_hooks = () if aux_host else ("<_nmx_audio_kmod_stop>",)
    for stub, expected in ((start, ("<_nmx_audio_kmod_start>", start_hook)), (stop, stop_hooks)):
        # The standard x86 libkmod stub loads the real entry from a RIP-relative slot.
        instructions = read_at(stub, 24)
        relative_load = instructions.find(b"\x48\x8d\x05")
        if relative_load < 0:
            raise ValueError("Unrecognized x86_64 libkmod entry stub")
        displacement, = struct.unpack_from("<i", instructions, relative_load + 3)
        slot = stub + relative_load + 7 + displacement
        entry, = struct.unpack("<Q", read_at(slot, 8))
        assembly = command("/usr/bin/xcrun", "llvm-objdump", "--disassemble",
                           f"--start-address={entry}", f"--stop-address={entry + 512}", str(executable))
        assert all(name in assembly for name in expected), (expected, assembly)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kext", type=Path)
    parser.add_argument("applealc_source", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--aux-host", action="store_true")
    mode.add_argument("--aux-host-base", action="store_true")
    args = parser.parse_args()
    standalone = args.aux_host or args.aux_host_base
    contents = args.kext / "Contents"
    assert args.kext.name == "NMXAudio.kext", args.kext
    if args.aux_host:
        verify_embedded_applehda(contents)
    else:
        assert not list(contents.rglob("*.kext")), "the bundle must not embed a second kext"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == APPLEALC_ID, "the AppleALC bundle ABI must remain available"
    assert info["CFBundleExecutable"] == "NMXAudio"
    executable = contents / "MacOS" / info["CFBundleExecutable"]
    assert sorted(path.name for path in executable.parent.iterdir()) == [executable.name]
    assert ("as.vit9696.Lilu" in info["OSBundleLibraries"]) != standalone
    assert bool(info.get("NMXAudioEmbeddedAppleHDAHost")) == standalone
    assert "com.apple.iokit.IOAudioFamily" in info["OSBundleLibraries"]
    assert not any(library.startswith("com.apple.driver.AppleHDA") for library in info["OSBundleLibraries"])
    pins_source = args.applealc_source / "Resources/PinConfigs.kext/Contents/Info.plist"
    pins = plistlib.loads(pins_source.read_bytes())["IOKitPersonalities"][APPLEALC_ID]["HDAConfigDefault"]
    personalities = info["IOKitPersonalities"]
    assert personalities[APPLEALC_ID]["HDAConfigDefault"] == pins, "codec pin data differs from source"
    assert personalities[APPLEALC_ID]["IOClass"] == "AppleALC"
    alc = personalities["ALCUserClientProvider"]
    assert alc["IOProviderClass"] == "IOHDACodecDevice"
    assert alc["IOUserClientClass"] == "ALCUserClient"
    if standalone:
        native = personalities["ALCUserClientProviderNative"]
        assert native["IOProviderClass"] == "NMXAudio"
        assert native["IOUserClientClass"] == alc["IOUserClientClass"]
        assert native["IOClass"] == alc["IOClass"]
    hda = [entry for entry in personalities.values() if entry.get("IOClass") == "NMXAudio"]
    assert len(hda) == 1 and hda[0]["IOProviderClass"] == "IOPCIDevice"
    for entry in (personalities[APPLEALC_ID], alc, hda[0]):
        assert entry["CFBundleIdentifier"] == APPLEALC_ID, entry["IOClass"]
    command("/usr/bin/codesign", "--verify", "--strict", "--deep", str(args.kext))
    command("/usr/bin/xcrun", "lipo", str(executable), "-verify_arch", "x86_64")
    assert "KEXTBUNDLE" in command("/usr/bin/xcrun", "otool", "-hv", str(executable))
    symbols = command("/usr/bin/xcrun", "nm", "-jU", str(executable)).splitlines()
    verify_module_entrypoints(executable, standalone)
    bootstrap = "_nmx_audio_embedded_init" if standalone else "_AppleALC_kern_start"
    for name in ("ALCUserClientProvider", "ALCUserClient", bootstrap,
                 "NMXAudioEngine", "_ZN8NMXAudio5probe"):
        assert any(name in symbol for symbol in symbols), f"missing {name}"
    undefined = command("/usr/bin/xcrun", "nm", "-ju", str(executable)).splitlines()
    assert not any(symbol.startswith("_nmx_") or "x86_return_thunk" in symbol
                   for symbol in undefined), "unresolved combined driver symbols"
    if standalone:
        assert RESOURCE_IMPORTS <= set(symbols), "missing NMXAudio resource exports"
        assert HAL_SETTER in symbols, "missing NMXAudio HAL path setter export"
        assert not any("LiluAPI" in symbol or symbol in ("_lilu", "_Lilu_config")
                       for symbol in undefined), "standalone host imports external Lilu"
        metaclass_name = "NMXAudioOSObjectWrapper"
        assert f"__ZN{len(metaclass_name)}{metaclass_name}10gMetaClassE" in symbols, (
            "missing private Lilu wrapper metaclass"
        )
        names = set(command("/usr/bin/strings", "-a", str(executable)).splitlines())
        assert metaclass_name in names and "OSObjectWrapper" not in names, (
            "the wrapper metaclass name must coexist with the EFI Lilu instance"
        )
    print(f"Verified one signed x86_64 kernel executable, AppleALC ABI and {len(pins)} pin configurations.")


if __name__ == "__main__":
    main()
