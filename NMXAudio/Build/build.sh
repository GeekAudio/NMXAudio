#!/bin/bash
set -euo pipefail

usage() {
  printf 'Usage: %s [--aux-host <Lilu-source> <AppleHDA.kext> | --aux-host-base <Lilu-source>] <Lilu.kext> <MacKernelSDK> <liberator-git> <freebsd-git> <nvidia-driver-git> <new-output-dir>\n' "$0" >&2
  exit 2
}

mode=objects
applehda=
case "${1:-}" in
  --aux-host)
    [[ $# == 9 ]] || usage
    mode=aux-objects
    lilu_source=$2
    applehda=$3
    shift 3
    ;;
  --aux-host-base)
    [[ $# == 8 ]] || usage
    mode=aux-objects
    lilu_source=$2
    shift 2
    ;;
  *) [[ $# == 6 ]] || usage ;;
esac

repo=$(cd "$(dirname "$0")/../.." && pwd)
lilu=$1
kernel_sdk=$2
liberator=$3
freebsd=$4
nvidia_driver=$5
output=$6
[[ -f "$lilu/Contents/Resources/Library/plugin_start.cpp" ]]
[[ -f "$kernel_sdk/Headers/stdint.h" ]]

# ResourceConverter rewrites its input XML. Run it in a private source copy.
work=$(mktemp -d "${TMPDIR:-/private/var/tmp}/geekaudio-nmxaudio.XXXXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir "$output"
output=$(cd "$output" && pwd)
source="$work/source"
mkdir "$source"
for file in AppleALC AppleALC.xcodeproj Resources ResourceConverter Tools alc-verb Changelog.md; do
  /usr/bin/ditto "$repo/$file" "$source/$file"
done
/usr/bin/ditto "$lilu/Contents/Resources" "$source/Lilu.kext/Contents/Resources"
if [[ "$mode" == aux-objects ]]; then
  /bin/cp "$lilu_source"/Lilu/Headers/*.hpp "$lilu_source"/hde/hde{32,64}.h \
    "$source/Lilu.kext/Contents/Resources/Headers"
fi
/usr/bin/ditto "$kernel_sdk/Headers" "$source/MacKernelSDK/Headers"
/usr/bin/ditto "$kernel_sdk/Library" "$source/MacKernelSDK/Library"

# Recompile the pinned NMXAudio source. The archive contains intermediate
# compiler objects consumed by the same kernel target's final link.
NMXAUDIO_BUILD_MODE="$mode" "$repo/NMXAudio/Driver/build.sh" \
  "$liberator" "$freebsd" "$nvidia_driver" "$work/nmx" >"$output/nmxaudio-build.log" 2>&1
native_archive="$work/nmx/libNMXAudio.a"
xcode_options=()
package_options=()
verify_options=()
definition=NMXAUDIO_COMBINED=1
bundle_output="$output"
if [[ "$mode" == aux-objects ]]; then
  "$repo/NMXAudio/Build/build-aux-patcher.sh" "$lilu_source" "$kernel_sdk" "$work/patcher" \
    >"$output/patcher-build.log" 2>&1
  xcode_options+=("OTHER_CPLUSPLUSFLAGS=$(cat "$work/patcher/defines.txt") -DLILU_CUSTOM_KMOD_INIT=1")
  definition=NMXAUDIO_AUX_HOST=1
  package_options+=(--aux-host)
  if [[ -n "$applehda" ]]; then
    verify_options+=(--aux-host)
  else
    verify_options+=(--aux-host-base)
  fi
  bundle_output="$work"
  native_archive="$work/libNMXAuxAudio.a"
  xcrun libtool -static -o "$native_archive" "$work/nmx/libNMXAudio.a" "$work/patcher/libNMXAuxPatcher.a"
fi

xcodebuild -project "$source/AppleALC.xcodeproj" -scheme AppleALC \
  -configuration Release -sdk macosx -arch x86_64 -derivedDataPath "$work/DerivedData" \
  CODE_SIGNING_ALLOWED=NO MACOSX_DEPLOYMENT_TARGET=15.0 \
  "NMXAUDIO_DEFINITIONS=$definition" \
  NMXAUDIO_MODULE_START=NMXAudio_combined_start \
  NMXAUDIO_MODULE_STOP=NMXAudio_combined_stop \
  "NMXAUDIO_LDFLAGS=-Wl,-force_load,$native_archive" \
  ${xcode_options[@]+"${xcode_options[@]}"} \
  build >"$output/xcodebuild.log" 2>&1

python3 "$repo/NMXAudio/Build/package-combined.py" \
  ${package_options[@]+"${package_options[@]}"} \
  "$work/DerivedData/Build/Products/Release/AppleALC.kext" "$work/nmx/Info.plist" \
  "$repo/Resources/PinConfigs.kext/Contents/Info.plist" "$bundle_output"
if [[ -n "$applehda" ]]; then
  python3 "$repo/NMXAudio/Build/stage-applehda.py" --gated "$work/NMXAudio.kext" "$applehda" "$work/embedded"
  /usr/bin/ditto "$work/embedded/NMXAudio.kext" "$output/NMXAudio.kext"
  /usr/bin/codesign --verify --strict --deep "$output/NMXAudio.kext"
elif [[ "$mode" == aux-objects ]]; then
  /usr/bin/ditto "$work/NMXAudio.kext" "$output/NMXAudio.kext"
fi
python3 "$repo/NMXAudio/Build/verify-combined.py" ${verify_options[@]+"${verify_options[@]}"} "$output/NMXAudio.kext" "$repo"
(
  cd "$output"
  if [[ "$mode" == aux-objects ]]; then
    find NMXAudio.kext -type f -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256 > SHA256SUMS
  else
    shasum -a 256 NMXAudio.kext/Contents/{Info.plist,MacOS/NMXAudio} > SHA256SUMS
  fi
)
