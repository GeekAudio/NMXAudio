#!/bin/bash
set -euo pipefail

# Rebuild the patched Liberator NMXAudio kext from pinned local source repos.
# Usage: build.sh <liberator-git> <freebsd-git> <nvidia-macos-driver-git> <empty-output-dir>
# No system installation is performed. PCM diagnostic capture is intentionally disabled.

readonly LIBERATOR_REV=edd4489e4ab62dbdbdda5ab593efc1ffeb382d9f
readonly FREEBSD_REV=628422c36c6f88bf36992fc117fbb8f79a50647a
readonly XNU_BRIDGE_REV=c104f584b71515c8e30855e9048a902a012c3131
readonly XNU_BRIDGE_PATH=gpu-port/xkpi/xnu/nmx_mm.cpp
readonly NATIVE_PROFILE_DARWIN=25

die() { printf 'NMXAudio build: %s\n' "$*" >&2; exit 1; }

if [[ $# -ne 4 ]]; then
  die "usage: $0 <liberator-git> <freebsd-git> <nvidia-macos-driver-git> <empty-output-dir>"
fi

liberator_repo=$1
freebsd_repo=$2
driver_repo=$3
output=$4
script_dir=$(cd "$(dirname "$0")" && pwd)
patch_file="$script_dir/connected-display-audio.patch"
port_patch="$script_dir/bridge/nmx-port-audio-only.patch"

for repo in "$liberator_repo" "$freebsd_repo" "$driver_repo"; do
  [[ -d "$repo" ]] || die "source directory not found: $repo"
  git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || die "not a Git checkout: $repo"
done

git -C "$liberator_repo" cat-file -e "${LIBERATOR_REV}^{commit}" 2>/dev/null || die "Liberator commit $LIBERATOR_REV is missing"
[[ "$(git -C "$freebsd_repo" rev-parse HEAD)" == "$FREEBSD_REV" ]] || die "FreeBSD checkout must be at pinned commit $FREEBSD_REV"
git -C "$driver_repo" cat-file -e "${XNU_BRIDGE_REV}^{commit}" 2>/dev/null || die "XNU bridge commit $XNU_BRIDGE_REV is missing"
[[ -s "$patch_file" ]] || die "feature patch not found"
[[ -s "$port_patch" ]] || die "audio-only port bridge patch not found"

# This checkout has two known local edits to HDAA files. They are not inputs:
# the build uses Liberator's pinned HDAA sources and only FreeBSD's hdac/hdacc.
# Reject every other FreeBSD sys change without touching the caller's checkout.
freebsd_dirty=$(git -C "$freebsd_repo" diff --name-only -- sys | LC_ALL=C sort)
while IFS= read -r changed_path; do
  case "$changed_path" in
    "sys/dev/sound/pci/hda/hdaa.c"|"sys/dev/sound/pci/hda/hdaa_patches.c") ;;
    "") ;;
    *) die "unexpected tracked change under FreeBSD sys: $changed_path" ;;
  esac
done <<< "$freebsd_dirty"
[[ -z "$(git -C "$freebsd_repo" diff --cached --name-only -- sys)" ]] || die "staged FreeBSD sys changes are not allowed"
[[ -z "$(git -C "$freebsd_repo" ls-files --others --exclude-standard -- sys)" ]] || die "untracked files under FreeBSD sys are not allowed"

parent=$(cd "$(dirname "$output")" && pwd)
output="$parent/$(basename "$output")"
[[ ! -e "$output" ]] || die "output already exists; choose a new empty output path"

work=$(mktemp -d "${TMPDIR:-/private/var/tmp}/nmxaudio-build.XXXXXX")
cleanup() { rm -rf "$work"; }
trap cleanup EXIT HUP INT TERM

src="$work/liberator"
mkdir -p "$src"
git -C "$liberator_repo" archive "$LIBERATOR_REV" LICENSE realmac27/audio | tar -x -C "$src"
bsd_sys="$freebsd_repo/sys"
[[ -d "$bsd_sys/dev/sound/pci/hda" ]] || die "FreeBSD sys tree is incomplete"
patch -p1 -d "$src" -f -s <"$patch_file" || die "feature patch does not apply to pinned Liberator source"
patch -p1 -d "$src" -f -s <"$port_patch" || die "audio-only port bridge patch does not apply to pinned Liberator source"

mode=${NMXAUDIO_BUILD_MODE:-kext}
case "$mode" in
  objects)
    patch -p1 -d "$src" -f -s <"$script_dir/combined-driver.patch" ||
      die "combined driver entry-point patch failed"
    ;;
  kext|aux-objects)
    overlay="$src/realmac27/audio/NMXAudio/include/dev/sound/pci/hda"
    mkdir -p "$overlay"
    git -C "$freebsd_repo" show "$FREEBSD_REV:sys/dev/sound/pci/hda/hdaa.h" >"$overlay/hdaa.h" ||
      die "cannot stage the pinned HDA header"
    patch -p1 -d "$src" -f -s <"$script_dir/system-driver.patch" ||
      die "native system driver patch failed"
    patch -p1 -d "$src" -f -s <"$script_dir/native-route-selection.patch" ||
      die "native HDA route selection patch failed"
    patch -p1 -d "$src" -f -s <"$script_dir/native-verb-client.patch" ||
      die "native codec user-client patch failed"
    if [[ "$mode" == aux-objects ]]; then
      patch -p1 -d "$src" -f -s <"$script_dir/startup-selection.patch" ||
        die "embedded AppleHDA startup-selection patch failed"
    fi
    ;;
  *) die "expected NMXAUDIO_BUILD_MODE=kext, objects or aux-objects" ;;
esac

audio_root="$src/realmac27/audio"
audio="$audio_root/NMXAudio"
bsdcompat="$audio_root/bsdcompat"
kernel_sources=(
  "$bsd_sys/dev/sound/pci/hda/hdac.c"
  "$bsd_sys/dev/sound/pci/hda/hdacc.c"
  "$audio/freebsd-hda/hdaa.c"
  "$audio/freebsd-hda/hdaa_patches.c"
  "$audio/nmx_snd_compat.c"
  "$audio/nmx_pcm.c"
  "$audio/nmx_display_eld.c"
  "$audio/nmx_audio_stubs.c"
)
for source in "${kernel_sources[@]}" "$audio/nmx_audio_kext.cpp" "$audio/Info.plist"; do
  [[ -f "$source" ]] || die "pinned source is missing a required NMXAudio file"
done

sdk=$(xcrun --sdk macosx --show-sdk-path)
kernel_headers="$sdk/System/Library/Frameworks/Kernel.framework/Headers"
if [[ ! -d "$kernel_headers" ]]; then die "macOS Kernel.framework headers are unavailable"; fi

mkdir -p "$work/srcobj" "$work/audioobj" "$work/xnuobj" "$work/xkpi" "$audio/gen"
applealc_resources="$script_dir/../../Resources"
if [[ "$mode" != objects ]]; then
  python3 "$script_dir/generate-native-profiles.py" "$applealc_resources" \
    "$audio/gen/nmx_native_profiles.h" --darwin-major "$NATIVE_PROFILE_DARWIN" \
    --report "$work/native-profiles.json" || die "cannot compile AppleALC native profiles"
  cp -X "$script_dir/bridge/nmx_hda_native_routes.inc" "$audio/gen/"
  kernel_sources+=("$script_dir/bridge/nmx_hda_codec_verb.c")
fi
export XKPI="$work/xkpi"
export FREEBSD_SYS="$bsd_sys"
export SRC_ROOT="$src"
ln -s "$audio" "$XKPI/audio"
ln -s "$bsdcompat" "$XKPI/bsdcompat"

# FreeBSD-on-XNU compatibility objects. Keep these flags in sync with the
# confirmed local build; source paths are remapped to avoid embedding a home path.
shim_flags=(
  -arch x86_64 -mmacosx-version-min=15.0 -mkernel -fapple-kext -nostdinc -std=gnu11
  -fno-builtin -fno-common -fno-strict-aliasing -fms-extensions -O2 -g0
  -Wno-pointer-sign -Wno-unused-parameter -Wno-microsoft-anon-tag
  -DNMX_AUDIO_ONLY=1 -DNMXKPI=1 -D_KERNEL -DKERNEL -DKERNEL_PRIVATE
  "-ffile-prefix-map=$src=/src" -include "$bsdcompat/include/nmxkpi_prelude.h"
  -I"$bsdcompat/include" -I"$kernel_headers" -idirafter "$bsd_sys"
)
for name in bus busdma kernel lock malloc misc page pctrie port sbuf sleep sysctl task time; do
  xcrun clang "${shim_flags[@]}" -c "$bsdcompat/src/nmx_$name.c" -o "$work/srcobj/nmx_$name.o" \
    >"$work/shim-$name.log" 2>&1 || {
      tail -n 25 "$work/shim-$name.log" >&2
      die "failed compiling compatibility object nmx_$name"
    }
done

# Generate the FreeBSD interface headers and compile the controller, codec,
# mixer, display ELD and IOAudio front-end objects without PCM diagnostic taps.
for spec in pcm/channel_if.m pcm/mixer_if.m pci/hda/hdac_if.m; do
  python3 "$audio/mkif.py" "$bsd_sys/dev/sound/$spec" "$audio/gen/$(basename "$spec" .m).h"
done
for source in "${kernel_sources[@]}"; do
  name=$(basename "$source" .c)
  NMX_OBJ="$work/audioobj/$name.o" /bin/bash "$audio/cc-snd.sh" "$source" \
    -I"$bsd_sys/dev/sound/pci/hda" >"$work/audio-$name.log" 2>&1 || {
      tail -n 30 "$work/audio-$name.log" >&2
      die "failed compiling audio object $name"
    }
done

kext_flags=(
  -arch x86_64 -mmacosx-version-min=15.0 -mkernel -fapple-kext -nostdinc -isysroot "$sdk"
  "-ffile-prefix-map=$src=/src" -I"$kernel_headers"
  -I"$bsdcompat/include/nmxxnu" -I"$audio/xnuinc"
  -DKERNEL -DKERNEL_PRIVATE -DDRIVER_PRIVATE -DAPPLE -DNeXT
  -std=c++17 -fno-rtti -fno-exceptions -fno-builtin -fno-common -O2 -g0
  -Wall -Wextra -Werror -Wno-unused-parameter -Wno-deprecated-declarations
  -Wno-inconsistent-missing-override -Wno-\#warnings
)
xcrun clang++ "${kext_flags[@]}" -c "$audio/nmx_audio_kext.cpp" -o "$work/audioobj/nmx_audio_kext.o" \
  >"$work/audio-kext.log" 2>&1 || {
    tail -n 30 "$work/audio-kext.log" >&2
    die "failed compiling NMXAudio front end"
  }

# nmx_mm.cpp is kept byte-for-byte from the pinned public driver revision;
# the PCI and memory adapters are the reviewed local bridge implementations.
git -C "$driver_repo" show "$XNU_BRIDGE_REV:$XNU_BRIDGE_PATH" >"$work/nmx_mm.cpp" || die "pinned nmx_mm.cpp is missing"
actual_mm_sha=$(shasum -a 256 "$work/nmx_mm.cpp" | awk '{print $1}')
expected_mm_sha=ad9e4e8e2335ee5bc0987999bc0cef83741d32ce67e2830e5735acc72bdc7fa5
[[ "$actual_mm_sha" == "$expected_mm_sha" ]] || die "historical nmx_mm.cpp does not match the reviewed source"
bridge_sources=(
  "$work/nmx_mm.cpp"
  "$script_dir/bridge/nmx_xnu_audio_memory.cpp"
  "$script_dir/bridge/nmx_xnu_audio_pci.cpp"
)
if [[ "$mode" != objects ]]; then
  kext_flags+=(-DNMX_NATIVE_PROFILE_DARWIN="$NATIVE_PROFILE_DARWIN")
  bridge_sources+=("$script_dir/bridge/nmx_xnu_audio_layout.cpp")
fi
if [[ "$mode" == kext ]]; then
  bridge_sources+=("$script_dir/bridge/nmx_xnu_alc_verbs.cpp")
elif [[ "$mode" == aux-objects ]]; then
  bridge_sources+=("$script_dir/../../AppleALC/kern_aux_host.cpp"
                    "$script_dir/../../AppleALC/kern_aux_resources.cpp")
fi
for source in "${bridge_sources[@]}"; do
  name=$(basename "$source" .cpp)
  xcrun clang++ "${kext_flags[@]}" -c "$source" -o "$work/xnuobj/$name.o" \
    >"$work/bridge-$name.log" 2>&1 || {
      tail -n 30 "$work/bridge-$name.log" >&2
      die "failed compiling XNU bridge object $name"
    }
done


if [[ "$mode" != kext ]]; then
  xcrun libtool -static -o "$work/libNMXAudio.a" "$work"/audioobj/*.o "$work"/srcobj/*.o "$work"/xnuobj/*.o
  mkdir "$output"
  cp -X "$work/libNMXAudio.a" "$audio/Info.plist" "$output/"
  printf 'Built x86_64 NMXAudio link objects in %s\n' "$output"
  exit 0
fi

kext="$work/NMXAudio.kext"
binary="$kext/Contents/MacOS/NMXAudio"
mkdir -p "$(dirname "$binary")"
xcrun clang++ -arch x86_64 -mmacosx-version-min=15.0 -fapple-kext -nostdlib -Wl,-kext,-dead_strip \
  "$work"/audioobj/*.o "$work"/srcobj/*.o "$work"/xnuobj/*.o \
  -lkmodc++ -lkmod -lcc_kext -o "$binary" 2>"$work/link.log" || {
    tail -n 40 "$work/link.log" >&2
    die "failed linking NMXAudio"
  }
xcrun nm -ju "$binary" | LC_ALL=C sort -u >"$work/imports.txt"
if grep -E '^(_nmx_|_bus_(alloc_resource|dma)|_device_get_softc|_pci_read_config|_rman_get_start)|x86_return_thunk|__cxa_' "$work/imports.txt"; then
  die "NMXAudio has unresolved driver-owned imports"
fi
if grep -Ei 'applehda|iohdafamily|iohdacodecdevice|lilu|simplehda' "$work/imports.txt"; then
  die "the system build imports symbols from an external audio patcher or driver"
fi
cp -X "$audio/Info.plist" "$kext/Contents/Info.plist"
plutil -lint "$kext/Contents/Info.plist" >/dev/null
python3 - "$kext/Contents/Info.plist" <<'PY' || die "unexpected system kext dependency"
import plistlib
import sys

with open(sys.argv[1], "rb") as stream:
    info = plistlib.load(stream)
for key in ("OSBundleLibraries", "OSBundleLibraries_x86_64"):
    libraries = info.get(key, {})
    for library in libraries:
        if any(name in library.lower() for name in ("applehda", "iohdafamily", "lilu", "simplehda")):
            raise SystemExit(f"Unsupported dependency for the native build: {library}")
PY
catalog="$kext/Contents/Resources/AppleALC"
mkdir -p "$(dirname "$catalog")"
python3 "$script_dir/package-applealc-resources.py" "$applealc_resources" "$catalog" ||
  die "cannot package the AppleALC resource catalog"
codesign --force --sign - --timestamp=none "$kext" >/dev/null
codesign --verify --deep --strict "$kext" || die "kext signature verification failed"
lipo "$binary" -verify_arch x86_64 || die "kext architecture verification failed"

bundle_version=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$kext/Contents/Info.plist")
uuid=$(dwarfdump --uuid "$binary" | awk 'NR == 1 { print $2 }')
binary_sha=$(shasum -a 256 "$binary" | awk '{print $1}')
patch_sha=$(shasum -a 256 "$patch_file" | awk '{print $1}')
port_patch_sha=$(shasum -a 256 "$port_patch" | awk '{print $1}')
memory_sha=$(shasum -a 256 "$script_dir/bridge/nmx_xnu_audio_memory.cpp" | awk '{print $1}')
pci_sha=$(shasum -a 256 "$script_dir/bridge/nmx_xnu_audio_pci.cpp" | awk '{print $1}')

mkdir -p "$output"
cp -R "$kext" "$output/NMXAudio.kext"
cp -X "$work/native-profiles.json" "$output/NATIVE-PROFILES.json"
python3 "$script_dir/package-applealc-resources.py" "$applealc_resources" \
  "$output/NMXAudio.kext/Contents/Resources/AppleALC" --verify ||
  die "copied AppleALC catalog does not match the source resources"
codesign --verify --deep --strict "$output/NMXAudio.kext" || die "copied kext signature verification failed"
cat >"$output/BUILD-INFO.txt" <<EOF
NMXAudio build
Version: $bundle_version
Architecture: x86_64
PCM diagnostic capture: disabled
Liberator source: $LIBERATOR_REV (PolyForm Noncommercial 1.0.0; source LICENSE retained)
FreeBSD sound source: $FREEBSD_REV (BSD-2-Clause notices retained in source files)
NMX XNU memory bridge: local reviewed source, SHA-256 $memory_sha
NMX XNU PCI bridge: adapted from NullMoth public bridge, SHA-256 $pci_sha
Historical XNU VM bridge: NullMoth nvidia-macos-driver $XNU_BRIDGE_REV:$XNU_BRIDGE_PATH
Historical nmx_mm.cpp SHA-256: $actual_mm_sha
Feature patch SHA-256: $patch_sha
System driver patch SHA-256: $(shasum -a 256 "$script_dir/system-driver.patch" | awk '{print $1}')
Codec user-client patch SHA-256: $(shasum -a 256 "$script_dir/native-verb-client.patch" | awk '{print $1}')
Codec command bridge SHA-256: $(shasum -a 256 "$script_dir/bridge/nmx_hda_codec_verb.c" | awk '{print $1}')
Codec user-client SHA-256: $(shasum -a 256 "$script_dir/bridge/nmx_xnu_alc_verbs.cpp" | awk '{print $1}')
Native profile catalog: AppleALC resources for Darwin $NATIVE_PROFILE_DARWIN
Native route patch SHA-256: $(shasum -a 256 "$script_dir/native-route-selection.patch" | awk '{print $1}')
Native profile report SHA-256: $(shasum -a 256 "$work/native-profiles.json" | awk '{print $1}')
AppleALC resource catalog SHA-256: $(shasum -a 256 "$catalog/Resources.zip" | awk '{print $1}')
Audio-only port bridge patch SHA-256: $port_patch_sha
Kernel binary UUID: $uuid
Kernel binary SHA-256: $binary_sha
EOF
(cd "$output" && shasum -a 256 NMXAudio.kext/Contents/MacOS/NMXAudio \
  NMXAudio.kext/Contents/Resources/AppleALC/Resources.zip NATIVE-PROFILES.json BUILD-INFO.txt >SHA256SUMS)
printf 'Built NMXAudio %s (x86_64)\n' "$bundle_version"
printf 'UUID %s\nSHA-256 %s\n' "$uuid" "$binary_sha"
printf 'Output: %s\n' "$output"
