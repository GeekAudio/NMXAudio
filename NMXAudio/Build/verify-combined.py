#!/usr/bin/env python3
"""Check a single NMXAudio bundle against the AppleALC source it contains."""
import argparse
from pathlib import Path
import plistlib
import struct
import subprocess

APPLEALC_ID = "as.vit9696.AppleALC"


def command(*args):
    return subprocess.check_output(args, text=True)



def verify_module_entrypoints(executable):
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

    for stub, expected in (
        (start, ("<_nmx_audio_kmod_start>", "<_AppleALC_kern_start>")),
        (stop, ("<_nmx_audio_kmod_stop>",)),
    ):
        # The standard x86 libkmod stub loads the real entry from a RIP-relative slot.
        instructions = read_at(stub, 24)
        relative_load = instructions.find(b"\x48\x8d\x05")
        if relative_load < 0:
            raise ValueError("Unrecognized x86_64 libkmod entry stub")
        displacement, = struct.unpack_from("<i", instructions, relative_load + 3)
        slot = stub + relative_load + 7 + displacement
        entry, = struct.unpack("<Q", read_at(slot, 8))
        assembly = command("/usr/bin/xcrun", "llvm-objdump", "--disassemble",
                           f"--start-address={entry}", f"--stop-address={entry + 96}", str(executable))
        assert all(name in assembly for name in expected), expected

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kext", type=Path)
    parser.add_argument("applealc_source", type=Path)
    args = parser.parse_args()
    contents = args.kext / "Contents"
    assert args.kext.name == "NMXAudio.kext", args.kext
    assert not list(contents.rglob("*.kext")), "the bundle must not embed a second kext"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == APPLEALC_ID, "the AppleALC bundle ABI must remain available"
    assert info["CFBundleExecutable"] == "NMXAudio"
    executable = contents / "MacOS" / info["CFBundleExecutable"]
    assert sorted(path.name for path in executable.parent.iterdir()) == [executable.name]
    assert "as.vit9696.Lilu" in info["OSBundleLibraries"]
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
    hda = [entry for entry in personalities.values() if entry.get("IOClass") == "NMXAudio"]
    assert len(hda) == 1 and hda[0]["IOProviderClass"] == "IOPCIDevice"
    for entry in (personalities[APPLEALC_ID], alc, hda[0]):
        assert entry["CFBundleIdentifier"] == APPLEALC_ID, entry["IOClass"]
    command("/usr/bin/codesign", "--verify", "--strict", "--deep", str(args.kext))
    command("/usr/bin/xcrun", "lipo", str(executable), "-verify_arch", "x86_64")
    assert "KEXTBUNDLE" in command("/usr/bin/xcrun", "otool", "-hv", str(executable))
    symbols = command("/usr/bin/xcrun", "nm", "-jU", str(executable)).splitlines()
    verify_module_entrypoints(executable)
    for name in ("ALCUserClientProvider", "ALCUserClient", "_AppleALC_kern_start",
                 "NMXAudioEngine", "_ZN8NMXAudio5probe"):
        assert any(name in symbol for symbol in symbols), f"missing {name}"
    undefined = command("/usr/bin/xcrun", "nm", "-ju", str(executable)).splitlines()
    assert not any(symbol.startswith("_nmx_") or "x86_return_thunk" in symbol
                   for symbol in undefined), "unresolved combined driver symbols"
    print(f"Verified one signed x86_64 kernel executable, AppleALC ABI and {len(pins)} pin configurations.")


if __name__ == "__main__":
    main()
