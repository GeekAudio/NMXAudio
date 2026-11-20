#!/bin/bash
set -euo pipefail

if [[ $# != 6 ]]; then
  printf 'Usage: %s <Lilu.kext> <MacKernelSDK> <liberator-git> <freebsd-git> <nvidia-driver-git> <new-output-dir>\n' "$0" >&2
  exit 2
fi

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
/usr/bin/ditto "$kernel_sdk/Headers" "$source/MacKernelSDK/Headers"
/usr/bin/ditto "$kernel_sdk/Library" "$source/MacKernelSDK/Library"

# Recompile the pinned NMXAudio source. The archive contains intermediate
# compiler objects consumed by the same kernel target's final link.
NMXAUDIO_BUILD_MODE=objects "$repo/NMXAudio/Driver/build.sh" \
  "$liberator" "$freebsd" "$nvidia_driver" "$work/nmx" >"$output/nmxaudio-build.log" 2>&1
xcodebuild -project "$source/AppleALC.xcodeproj" -scheme AppleALC \
  -configuration Release -sdk macosx -arch x86_64 -derivedDataPath "$work/DerivedData" \
  CODE_SIGNING_ALLOWED=NO MACOSX_DEPLOYMENT_TARGET=15.0 \
  NMXAUDIO_DEFINITIONS=NMXAUDIO_COMBINED=1 \
  NMXAUDIO_MODULE_START=NMXAudio_combined_start \
  NMXAUDIO_MODULE_STOP=NMXAudio_combined_stop \
  "NMXAUDIO_LDFLAGS=-Wl,-force_load,$work/nmx/libNMXAudio.a" \
  build >"$output/xcodebuild.log" 2>&1

python3 "$repo/NMXAudio/Build/package-combined.py" \
  "$work/DerivedData/Build/Products/Release/AppleALC.kext" "$work/nmx/Info.plist" \
  "$repo/Resources/PinConfigs.kext/Contents/Info.plist" "$output"
python3 "$repo/NMXAudio/Build/verify-combined.py" "$output/NMXAudio.kext" "$repo"
(
  cd "$output"
  shasum -a 256 NMXAudio.kext/Contents/{Info.plist,MacOS/NMXAudio} > SHA256SUMS
)
