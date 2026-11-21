#!/bin/bash
set -euo pipefail

usage() {
  cat >&2 <<EOF
Usage: $0 <Lilu-source-repo> <MacKernelSDK> <workspace-dir>

Build the private, x86_64 AuxKC patcher core as libNMXAuxPatcher.a.
The workspace receives the archive and defines.txt; temporary source edits
and compiler objects remain in a private temporary directory and are removed.
EOF
}

if [[ $# -ne 3 ]]; then
  usage
  exit 2
fi

lilu_repo=$(cd "$1" && pwd -P)
kernel_sdk=$(cd "$2" && pwd -P)
workspace_input=$3
mkdir -p "$workspace_input"
workspace=$(cd "$workspace_input" && pwd -P)

if [[ ! -d "$lilu_repo/Lilu/Sources" || ! -d "$lilu_repo/Lilu/Headers" || \
      ! -d "$lilu_repo/Lilu/PrivateHeaders" || ! -d "$lilu_repo/hde" || \
      ! -d "$lilu_repo/lzvn" ]]; then
  echo "error: '$lilu_repo' is not a Lilu source repository" >&2
  exit 2
fi
if [[ ! -f "$kernel_sdk/Headers/stdint.h" ]]; then
  echo "error: '$kernel_sdk' does not contain MacKernelSDK/Headers/stdint.h" >&2
  exit 2
fi
if [[ "$(uname -m)" != x86_64 ]]; then
  echo "error: this AuxKC patcher build currently targets x86_64 only" >&2
  exit 2
fi

macos_sdk=$(xcrun --sdk macosx --show-sdk-path)
kernel_headers="$macos_sdk/System/Library/Frameworks/Kernel.framework/Headers"
kernel_private_headers="$macos_sdk/System/Library/Frameworks/Kernel.framework/PrivateHeaders"
if [[ ! -d "$kernel_headers" ]]; then
  echo "error: Xcode macOS SDK is missing Kernel.framework/Headers" >&2
  exit 2
fi

for output in "$workspace/libNMXAuxPatcher.a" "$workspace/defines.txt"; do
  if [[ -e "$output" && ! -f "$output" ]]; then
    echo "error: expected output path to be a regular file: '$output'" >&2
    exit 2
  fi
done

clang=$(xcrun --find clang)
clangxx=$(xcrun --find clang++)
libtool=$(xcrun --find libtool)
tmp=$(mktemp -d "$workspace/.aux-patcher-build.XXXXXXXX")
cleanup() {
  /usr/bin/python3 - "$tmp" "$workspace" <<'PY'
import pathlib
import shutil
import sys

temporary = pathlib.Path(sys.argv[1]).resolve()
workspace = pathlib.Path(sys.argv[2]).resolve()
if temporary.parent != workspace or not temporary.name.startswith(".aux-patcher-build."):
    raise SystemExit("refusing to remove an unexpected temporary path")
shutil.rmtree(temporary)
PY
}
trap cleanup EXIT

source_root="$tmp/source"
mkdir -p "$source_root/Lilu/Sources" "$source_root/Lilu/Headers" \
  "$source_root/Lilu/PrivateHeaders" "$source_root/hde" "$source_root/lzvn" "$tmp/obj"

/usr/bin/ditto "$lilu_repo/Lilu/Headers" "$source_root/Lilu/Headers"
/usr/bin/ditto "$lilu_repo/Lilu/PrivateHeaders" "$source_root/Lilu/PrivateHeaders"
/usr/bin/ditto "$lilu_repo/Lilu/Sources" "$source_root/Lilu/Sources"
/usr/bin/ditto "$lilu_repo/hde" "$source_root/hde"
/usr/bin/ditto "$lilu_repo/lzvn" "$source_root/lzvn"

# These changes are applied only to the temporary build copy. A standalone
# AuxKC host has no Lilu Configuration object, and AppleALC's plugin_start.cpp
# owns the AppleALC_debugEnabled/debugPrintDelay symbols.
/usr/bin/python3 - "$lilu_repo/Lilu/Sources/kern_mach.cpp" \
  "$source_root/Lilu/Sources/kern_mach.cpp" \
  "$lilu_repo/Lilu/Sources/kern_util.cpp" \
  "$source_root/Lilu/Sources/kern_util.cpp" <<'PY'
import pathlib
import sys

mach_source, mach_target, util_source, util_target = map(pathlib.Path, sys.argv[1:])
mach = mach_source.read_text()
old_mach = "\tallow_decompress = ADDPR(config).allowDecompress;"
if mach.count(old_mach) != 1:
    raise SystemExit("unexpected Lilu kern_mach.cpp: allowDecompress access was not unique")
mach_target.write_text(mach.replace(old_mach, "\tallow_decompress = true; // NMXAudio AuxKC host has no Lilu Configuration."))

util = util_source.read_text()
old_util = "bool ADDPR(debugEnabled) = false;\nuint32_t ADDPR(debugPrintDelay) = 0;\n"
if util.count(old_util) != 1:
    raise SystemExit("unexpected Lilu kern_util.cpp: debug globals were not unique")
util_target.write_text(util.replace(old_util, ""))
PY

# Keep this list in sync with AppleALC's Aux host translation units. PRODUCT_NAME
# is intentionally absent from defines.txt: the archive is compiled as AppleALC
# to use the debug state exported by its vendored plugin_start.cpp.
defines=(
  -DKernelPatcher=NMXKernelPatcher
  -DMachInfo=NMXMachInfo
  -DDeviceInfo=NMXDeviceInfo
  -DBaseDeviceInfo=NMXBaseDeviceInfo
  -DCPUInfo=NMXCPUInfo
  -DWIOKit=NMXWIOKit
  -DCompression=NMXCompression
  -DBuffer=NMXBuffer
  -DFileIO=NMXFileIO
  -DDisassembler=NMXDisassembler
  -DPage=NMXPage
  # IOKit registers metaclass names globally, including Lilu's private wrapper.
  -DOSObjectWrapper=NMXAudioOSObjectWrapper
  -DglobalBaseDeviceInfo=NMXGlobalBaseDeviceInfo
  -DglobalDeviceInfo=NMXGlobalDeviceInfo
  -Dlilu_os_log=nmx_lilu_os_log
  -Dlilu_os_free=nmx_lilu_os_free
)

defines_text="${defines[*]}"
printf '%s\n' "$defines_text" >"$tmp/defines.txt"

common_flags=(
  -target x86_64-apple-macos15.0
  -isysroot "$macos_sdk"
  -nostdinc
  -fno-cxx-modules
  -fno-builtin
  -fno-exceptions
  -fno-rtti
  -fno-sanitize=vptr
  -O3
  -fno-common
  -DKERNEL
  -DKERNEL_PRIVATE
  -DDRIVER_PRIVATE
  -DAPPLE
  -DNeXT
  -DPRODUCT_NAME=AppleALC
  -DLILU_CUSTOM_KMOD_INIT=1
  -fapple-kext
  -fasm-blocks
  -funroll-loops
  -fvisibility=hidden
  -fno-lto
  -mmmx
  -msse
  -msse2
  -msse3
  -mfpmath=sse
  -mssse3
  -ftree-vectorize
  -fno-non-call-exceptions
  -fno-asynchronous-unwind-tables
  -Wall
  -Wextra
  -Wno-unused-parameter
  -Wno-deprecated-register
  -Wno-unknown-pragmas
  -Wno-unknown-warning-option
  -Wno-ossharedptr-misuse
  -Wno-vla
  -Wno-implicit-int-conversion
  -Wno-sign-conversion
  -Wno-shadow
  -Werror
  -Wno-stdlibcxx-not-found
)

include_flags=(
  -I"$source_root/Lilu"
  -I"$source_root/Lilu/Headers"
  -I"$source_root/Lilu/PrivateHeaders"
  -I"$source_root/hde"
  -I"$source_root/lzvn"
  -I"$kernel_sdk/Headers"
  -I"$kernel_headers"
)
if [[ -d "$kernel_private_headers" ]]; then
  include_flags+=( -I"$kernel_private_headers" )
fi

cpp_sources=(
  kern_patcher.cpp
  kern_mach.cpp
  kern_devinfo.cpp
  kern_cpu.cpp
  kern_iokit.cpp
  kern_disasm.cpp
  kern_compression.cpp
  kern_file.cpp
  kern_util.cpp
)
objects=()
for source in "${cpp_sources[@]}"; do
  object="$tmp/obj/${source%.cpp}.o"
  "$clangxx" "${common_flags[@]}" -std=c++1y "${include_flags[@]}" \
    "${defines[@]}" -c "$source_root/Lilu/Sources/$source" -o "$object"
  objects+=("$object")
done

c_flags=(
  -target x86_64-apple-macos15.0
  -isysroot "$macos_sdk"
  -nostdinc
  -fno-builtin
  -O3
  -fno-common
  -DKERNEL
  -DKERNEL_PRIVATE
  -DDRIVER_PRIVATE
  -DAPPLE
  -DNeXT
  -fvisibility=hidden
  -fno-lto
  -Wall
  -Wextra
  -Wno-implicit-int-conversion
  -Wno-sign-conversion
  -Werror
)
for source in hde64.c lzvn.c; do
  object="$tmp/obj/${source%.c}.o"
  case "$source" in
    hde64.c) source_path="$source_root/hde/$source" ;;
    lzvn.c) source_path="$source_root/lzvn/$source" ;;
  esac
  "$clang" "${c_flags[@]}" -std=c11 "${include_flags[@]}" \
    "${defines[@]}" -c "$source_path" -o "$object"
  objects+=("$object")
done

"$libtool" -static -o "$tmp/libNMXAuxPatcher.a" "${objects[@]}"

symbol_table=$(nm -g "$tmp/libNMXAuxPatcher.a")
for symbol in _AppleALC_debugEnabled _AppleALC_debugPrintDelay; do
  if ! awk -v symbol="$symbol" '$NF == symbol && $(NF-1) == "U" { found = 1 } END { exit !found }' <<<"$symbol_table"; then
    echo "error: archive must reference, not define, $symbol" >&2
    exit 1
  fi
  if awk -v symbol="$symbol" '$NF == symbol && $(NF-1) != "U" { found = 1 } END { exit !found }' <<<"$symbol_table"; then
    echo "error: archive unexpectedly defines $symbol" >&2
    exit 1
  fi
done
if grep -Eq 'LiluAPI|_AppleALC_kern_(start|stop)' <<<"$symbol_table"; then
  echo "error: private core unexpectedly includes Lilu startup/API symbols" >&2
  exit 1
fi
if ! grep -q '_lzvn_decode_buffer' <<<"$symbol_table"; then
  echo "error: archive does not contain the LZVN decoder object" >&2
  exit 1
fi

mv -f "$tmp/libNMXAuxPatcher.a" "$workspace/libNMXAuxPatcher.a"
mv -f "$tmp/defines.txt" "$workspace/defines.txt"

printf 'Built %s\n' "$workspace/libNMXAuxPatcher.a"
printf 'Wrote %s\n' "$workspace/defines.txt"
printf 'Private-core source files: %s C++ and %s C\n' "${#cpp_sources[@]}" 2
