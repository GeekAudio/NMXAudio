"""Shared plist byte parsing for the AppleALC catalog tools."""

import plistlib


def load_plist(data: bytes, *, allow_xml_fragment: bool = False):
    """Parse a plist, optionally wrapping AppleALC's fragment-style XML files."""
    probe = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    if allow_xml_fragment and not probe.startswith(b"bplist") and b"<plist" not in probe[:1024]:
        data = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<plist version="1.0">\n' + data + b"\n</plist>"
        )
    return plistlib.loads(data)
