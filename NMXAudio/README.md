# Build NMXAudio

The AppleALC source, codec layouts, pin configurations, and user-client headers
are kept in this repository. `Driver/` contains the pinned NMXAudio source
patches and FreeBSD-to-XNU compatibility build. The build compiles both sources
and links them once using the AppleALC Xcode target. [AppleALC-IMPORT.json](AppleALC-IMPORT.json)
records the imported PR commits, resolutions and resource validation. It gives the linked
extension the `NMXAudio.kext` filename and preserves AppleALC's bundle ID
and IOKit ABI. The pin tables are installed in the same bundle's Info.plist.

Use Xcode with macOS Kernel.framework headers, Git and Python 3. You also
need a Lilu developer bundle with the headers and plugin source, MacKernelSDK,
and local repositories containing the pinned Liberator, FreeBSD and historical
NVIDIA driver revisions listed in [the NMXAudio driver recipe](Driver/build.sh).
The development build uses Lilu 1.7.3 and MacKernelSDK at
`3f750085caa17ec3a7880f11c11bf4f48cd6a164`.

```sh
NMXAudio/Build/build.sh \
  /path/to/Lilu.kext /path/to/MacKernelSDK /path/to/liberator \
  /path/to/freebsd-src /path/to/nvidia-macos-driver "$HOME/Downloads/NMXAudio-build"
```

The build generates the resource database in a temporary source copy and
writes the signed development kext, build logs and checksums to the output
directory. Use a local output directory because File Provider storage can
add Finder metadata to a signed kext. The verifier checks architecture, one kernel executable, AppleALC
and NMXAudio symbols and personalities, and the complete pin table.

For a hardware test, use the integrated bundle in place of both standalone
audio kexts. Keep Lilu and the system's current AppleHDA compatibility path in
the test configuration. On macOS 26 the analog AppleALC path needs AppleHDA
classes supplied by the system's existing audio stack.

AppleALC's BSD 3-Clause license is in [LICENSE.txt](../LICENSE.txt).
The NMXAudio source is covered by the [Liberator license](licenses/Liberator-LICENSE.txt)
and the [FreeBSD HD Audio license](licenses/FreeBSD-snd_hda-LICENSE.txt).
