import argparse
import hashlib
import struct
from pathlib import Path

from extract_echo import rockchip_crc32


STOCK_SHA256 = "c6a3b6c86b141c0a428771f8a611fbd44cc12d561acb033a98640b0bf24f8d57"

# In-place changes to module 11 of the stock ECHOV200.IMG. Every tuple has
# the image offset, original bytes, and replacement bytes. Module placement
# and payload lengths are fixed.
#
# The USB Type I format uses four bytes per channel with 24 valid bits.
# The stock I2S high-resolution path takes the same left-justified layout.
# DMA still reads 512 words per half-buffer, now holding 256 stereo frames.
#
# The volume routine at 0x0307FD7C receives a DMA buffer and a pointer
# to its word count. For each word it removes the pad byte, applies the
# signed UAC gain over 60 steps, and restores the pad byte. Its mute and
# silence detection use the same state as the stock routine.
EDITS = (
    # Rearm EP2 OUT for one 384-byte packet at 0307FC1C.
    (0x0007FE84, "d0f8501341f0c00141f40021", "40f28011c0f20801aff30080"),
    # Queue complete eight-byte stereo frames at 0307FC38.
    (
        0x0007FEA0,
        "d6e90501f36c48f6ff7288423cbf091a4a1e84bf411a521aba4238bfbde8f081",
        "d6e90501f36c0a1a01dc02f5104227f00707974204d3bde8f08100bf00bf00bf",
    ),
    # Keep the descriptor's packet size on full-speed enumeration at 0307FC8E.
    (0x0007FEF6, "2c48c021a0f86210", "02e000bf00bf00bf"),
    # Scale left-justified 24-bit PCM samples at 0307FD7C.
    (
        0x0007FFE4,
        "2de9f0010968f44d4fea410cf34900226a630b7849781446012908bf002302d0"
        "3c2bc8bf3c23bcf1000fc4bfec4eed4f1addb17c002908bf20f812400bd030f9"
        "1210a0465943c7fb01814fea6118a8ebe17120f8121030f8121011b9696b491c"
        "6963521c6245e4db686bacf1010188422ebfac630120a863bde8f0017047",
        "f0b4f54c0a680025f4490b783c2b00d93c234978012902d0f149897c01b90023"
        "3c2617465fb101680912594391fbf6f10902016001b96d1c001d7f1ef3d16563"
        "521b0020022a00d30120a063f0bc704700bf00bf00bf00bf00bf00bf00bf00bf"
        "00bf00bf00bf00bf00bf00bf00bf00bf00bf00bf00bf00bf00bf00bf00bf",
    ),
    # Set PCM valid bits in playback context at 0307FF7E.
    (0x000801E6, "1021", "1821"),
    # Derive 48 kHz from eight bytes per frame at 0307FF84.
    (0x000801EC, "9a10", "da10"),
    # Select stock high-resolution I2S playback at 03080098.
    (0x00080300, "0f20", "1720"),
    # Zero full startup DMA block at 030800EE.
    (0x00080356, "4900", "8900"),
    # Set PCM byte rate to 384,000 B/s at 03080178.
    (0x000803E0, "00ee0200", "00dc0500"),
    # Convert DMA words to milliseconds at 0308035A.
    (0x000805C2, "4ff47a71", "4ff4fa71"),
    # Zero full restart DMA block at 03080422.
    (0x0008068A, "4900", "8900"),
    # Advertise 24 valid bits in four bytes at 03024BC0.
    (0x0008079C, "0210", "0418"),
    # Set EP2 maximum packet to 384 bytes at 03024BCA.
    (0x000807A6, "c000", "8001"),
)


# Windows test variant applied over the regular 24-bit edits.
# The endpoint receives packed samples and calls the stock PCM widening
# helper in place before queuing complete left-justified stereo frames.
PACKED_USB_EDITS = (
    # Rearm EP2 OUT for a packed 288-byte packet at 0307FC1C.
    (0x0007FE84, "40f28011c0f20801aff30080", "40f22011c0f20801aff30080"),
    # Expand USB PCM before the existing ring copy at 0307FC38.
    (
        0x0007FEA0,
        "d6e90501f36c0a1a01dc02f5104227f00707974204d3bde8f08100bf00bf00bf",
        "c8e0d6e90501f36c0a1a01dc02f51042974205d3bde8f08100bf00bf00bf00bf",
    ),
    # Use the stock left-justified PCM packer for whole stereo frames at 0307FDCC.
    (
        0x00080034,
        "00bf00bf00bf00bf00bf00bf00bf00bf00bf00bf00bf00bf00bf",
        "82b00620b7fbf0f70097f06c69461822ff00e6f7adf902b029e7",
    ),
    # Advertise three-byte packed PCM at 03024BC0.
    (0x0008079C, "04", "03"),
    # Set EP2 maximum packet to 288 bytes at 03024BCA.
    (0x000807A6, "8001", "2001"),
    # Declare the sampling rate fixed on this endpoint at 03024BD2.
    (0x000807AE, "01", "00"),
    # Use port-based enumeration for this UAC identity at 03024C74.
    (0x00080850, "03", "00"),
)


def build_image(source, *, packed_usb=False):
    if hashlib.sha256(source).hexdigest() != STOCK_SHA256:
        raise ValueError("Expected the stock SNOWSKY ECHO V2.0.0 image")

    patched = bytearray(source)
    edits = EDITS + (PACKED_USB_EDITS if packed_usb else ())
    for offset, old_hex, new_hex in edits:
        original = bytes.fromhex(old_hex)
        replacement = bytes.fromhex(new_hex)
        end = offset + len(original)
        if len(original) != len(replacement) or patched[offset:end] != original:
            raise ValueError(f"Patch precondition failed at 0x{offset:08X}")
        patched[offset:end] = replacement

    checksum = rockchip_crc32(memoryview(patched)[:-4])
    struct.pack_into("<I", patched, len(patched) - 4, checksum)
    return patched, checksum


def main():
    parser = argparse.ArgumentParser(
        description="Build the ECHO V2.0.0 USB DAC 24-bit/48 kHz test image."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--packed-usb", action="store_true", help="Build the packed 24-bit Windows test variant"
    )
    args = parser.parse_args()

    patched, checksum = build_image(args.source.read_bytes(), packed_usb=args.packed_usb)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output_file = args.output.open("xb")
    try:
        with output_file:
            output_file.write(patched)
    except (OSError, KeyboardInterrupt):
        args.output.unlink(missing_ok=True)
        raise

    print(args.output.resolve())
    print(f"Rockchip CRC32 0x{checksum:08X}")
    print(f"SHA-256 {hashlib.sha256(patched).hexdigest()}")


if __name__ == "__main__":
    main()

