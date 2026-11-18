import argparse
import hashlib
from pathlib import Path
import re
from struct import pack, pack_into, unpack_from
import sys

from extract_echo import rockchip_crc32
from patch_echo_uac24 import build_image


MODULE_RECORD = 0x208 + 11 * 32
CODE_OFFSET = 0x7FBA0
CODE_BASE = 0x0307F938
CODE_SIZE = 2972
EXTENSION_BASE = CODE_BASE + CODE_SIZE
CONFIG_BASE = EXTENSION_BASE + 0x800
RELOCATED_OFFSET = 0x560BC
PADDING_END = 0x590BC
USB_STORAGE_CODE_END = 0x03081340
USB_CODE_OFFSET = 0x7EAA4
USB_CODE_BASE = 0x0307E8C0

FORMATS = ((16, 48_000), (24, 48_000), (24, 96_000))
DIAGNOSTIC_FORMATS = ((16, 48_000), (24, 48_000), (16, 96_000), (24, 96_000))
CLOCK_TEST_FORMATS = DIAGNOSTIC_FORMATS[:3]
FORMATS_192 = DIAGNOSTIC_FORMATS + ((16, 192_000), (24, 192_000))
DMA_BUFFER_192 = 0x03030000
PACKED_192_WRITER = 0x0307F95C
PACKED_192_WRITER_END = 0x0307F9A8


def assemble(text, address):
    from keystone import Ks, KS_ARCH_ARM, KS_MODE_THUMB

    compiled, _ = Ks(KS_ARCH_ARM, KS_MODE_THUMB).asm(
        text, addr=address, as_bytes=True
    )
    if not compiled:
        raise ValueError(f"Empty assembly at 0x{address:08X}")
    return compiled


def assemble_diagnostics(text, address=EXTENSION_BASE):
    # Keystone can calculate numeric Thumb branch targets from an
    # intermediate layout when the full source shrinks or expands. Resolve
    # external branches at their final addresses instead.
    relocations = {}

    def reserve(match):
        marker = 0xE7F0D000 + len(relocations)
        relocations[pack("<I", marker)] = match.group(1), int(match.group(2), 16)
        return f".word 0x{marker:08x}"

    text = re.sub(
        r"(?m)^[ \t]*(bl|b\.w)[ \t]+(0x[0-9a-fA-F]+)[ \t]*$", reserve, text
    )
    compiled = bytearray(assemble(text, address))
    for marker, (operation, destination) in relocations.items():
        offset = compiled.find(marker)
        if offset == -1:  # An assembly conditional can remove this branch.
            continue
        if compiled.find(marker, offset + 4) != -1:
            raise ValueError("Duplicated external branch marker")
        branch = assemble(f"{operation} 0x{destination:08x}", address + offset)
        if len(branch) != 4:
            raise ValueError("Invalid Thumb branch width")
        compiled[offset:offset + 4] = branch
    return compiled


def mode_record(bits, frequency, endpoint, size=32):
    frame_size = bits // 8 * 2
    packet_size = unpack_from("<H", endpoint, 4)[0]
    record = pack("<IHBB", frequency, packet_size, frame_size, bits)
    if size == 16:
        endpoint = endpoint[:7]
    return (record + endpoint).ljust(size, b"\0")


def make_descriptors(previous, formats=FORMATS, record_size=32):
    original = previous[0x80744:0x80744 + 144]
    config = bytearray(original[:0x43])
    table = bytearray(pack("<IHBB", 48_000, 0, 4, 16).ljust(record_size, b"\0"))
    for alternate, (bits, frequency) in enumerate(formats, start=1):
        frame_size = bits // 8 * 2
        packet_size = frequency // 1000 * frame_size
        interface = bytearray(original[0x43:0x6E])
        interface[3] = alternate
        interface[0x15] = bits // 8
        interface[0x16] = bits
        interface[0x18:0x1b] = frequency.to_bytes(3, "little")
        pack_into("<H", interface, 0x1f, packet_size)
        endpoint = interface[0x1b:0x24]
        config.extend(interface)
        table.extend(mode_record(bits, frequency, endpoint, record_size))

    config.extend(original[0x6E:])
    pack_into("<H", config, 2, len(config))
    if len(config) > 512:
        raise ValueError("Configuration exceeds the USB control buffer")
    return config, table


def make_high_speed_descriptors(previous, revised=False, rate_192=False):
    config = bytearray(previous[0x80744:0x80744 + 9])
    # IAD and AudioControl interface. The clock, USB input terminal and
    # headphone output terminal share entity IDs 4, 1 and 2 respectively.
    config.extend(bytes((8, 11, 0, 2, 1, 0, 0x20, 0)))
    config.extend(bytes((9, 4, 0, 0, 0, 1, 1, 0x20, 0)))
    control = bytes((
        8, 0x24, 0x0A, 4, 3, 7, 0, 0,
        17, 0x24, 2, 1, 1, 1, 0, 4, 2, 3, 0, 0, 0, 0, 0, 0, 0,
        12, 0x24, 3, 2, 2, 3, 0, 1, 4, 0, 0, 0,
    ))
    config.extend(pack("<BBBHBHB", 9, 0x24, 1, 0x0200, 3, 9 + len(control), 0))
    config.extend(control)
    config.extend(bytes((9, 4, 1, 0, 0, 1, 2, 0x20, 0)))

    endpoints = {}
    packet_limits = [0]
    interval = 3 if revised else 1
    alternates = [(16, 96_000, interval, 1), (24, 96_000, interval, 1)]
    if rate_192:
        # Keep the existing alternates for the lower rates. Packed 24-bit
        # needs a shorter interval at 192 kHz to fit the fixed RX FIFO.
        # One extra frame above the old 294-byte limit gives the host an
        # unambiguous smaller alternate to select for 48 and 96 kHz.
        alternates.extend(((16, 192_000, 3, 1), (24, 192_000, 2, 2)))
    for alternate, (bits, ceiling, interval, padding_frames) in enumerate(alternates, start=1):
        subslot_size = bits // 8
        transfers_per_second = 8_000 >> (interval - 1)
        packet_size = (ceiling // transfers_per_second + padding_frames) * 2 * subslot_size
        endpoint = pack("<BBBBHB", 7, 5, 2, 0x09, packet_size, interval)
        packet_limits.append(packet_size)
        endpoints.setdefault(bits, endpoint)
        config.extend(bytes((9, 4, 1, alternate, 1, 1, 2, 0x20, 0)))
        config.extend(bytes((16, 0x24, 1, 1, 0, 1, 1, 0, 0, 0, 2, 3, 0, 0, 0, 0)))
        config.extend(bytes((6, 0x24, 2, 1, subslot_size, bits)))
        config.extend(endpoint)
        config.extend(bytes((8, 0x25, 1, 0, 0, 0, 0, 0)))

    # Reuse the media-key report and keep its 2 ms polling period at HS.
    hid = bytearray(previous[0x80744 + 0x6E:0x80744 + 0x88])
    hid[4] = 1
    hid.pop()  # The stock interrupt endpoint has an extra padding byte.
    hid[-7] = 7
    hid[-1] = 5
    config.extend(hid)
    pack_into("<H", config, 2, len(config))
    if rate_192:
        # USB limits are indexed by host alternate, PCM setup by format.
        table = bytearray(pack("<IHBB", 48_000, 0, 4, 16))
        for bits, frequency in FORMATS_192:
            table.extend(pack("<IHBB", frequency, 0, bits // 4, bits))
        return config, table, packet_limits

    record_size = 16 if revised else 32
    table = bytearray(pack("<IHBB", 48_000, 0, 4, 16).ljust(record_size, b"\0"))
    for bits, frequency in DIAGNOSTIC_FORMATS:
        table.extend(mode_record(bits, frequency, endpoints[bits], record_size))
    return config, table, packet_limits


def build_multiformat(source, assembly, *, diagnostics=False, clock_test=False,
                      dma_test=False, high_speed=False, revised=False, recovery=False,
                      rate_192=False, packed_192=False):
    image, _ = build_image(source, packed_usb=True)
    image_base, module_count = unpack_from("<II", image, 0x200)
    load_address, run_address, length = unpack_from("<III", image, MODULE_RECORD)
    if (
        module_count != 91
        or load_address != image_base + CODE_OFFSET - 0x200
        or run_address != CODE_BASE
        or length != CODE_SIZE
    ):
        raise ValueError("Unexpected module 11 layout")

    rate_192 = rate_192 or packed_192
    recovery = recovery or rate_192
    revised = revised or recovery
    high_speed = high_speed or revised
    dma_pipeline = dma_test or high_speed
    if clock_test or dma_pipeline:
        formats = CLOCK_TEST_FORMATS
    else:
        formats = DIAGNOSTIC_FORMATS if diagnostics else FORMATS
    telemetry = diagnostics or clock_test or dma_pipeline
    config, table = make_descriptors(image, formats, 16 if revised else 32)
    config_base = CONFIG_BASE
    if recovery:
        config_base = EXTENSION_BASE + 0xBE0
    elif revised:
        config_base = EXTENSION_BASE + 0xBC0
    elif high_speed:
        config_base = EXTENSION_BASE + 0xA00
    config_alignment = 4 if recovery else 64
    format_table = config_base + ((len(config) + config_alignment - 1) & -config_alignment)
    config_hs_base = config_base
    format_hs_table = format_table
    hs_packet_limits = []
    if high_speed:
        config_hs, table_hs, hs_packet_limits = make_high_speed_descriptors(image, revised, rate_192)
        config_hs_base = format_table + len(table)
        hs_alignment = 4 if recovery else 32
        format_hs_table = config_hs_base + ((len(config_hs) + hs_alignment - 1) & -hs_alignment)
    assembly = (
        assembly.replace("__MODE_TABLE__", f"0x{format_table:08x}")
        .replace("__HS_MODE_TABLE__", f"0x{format_hs_table:08x}")
        .replace("__MAX_ALTERNATE__", str(len(formats)))
        .replace("__DIAGNOSTICS__", str(int(telemetry)))
        .replace("__CLOCK_FIX__", str(int(clock_test)))
        .replace("__HIGH_SPEED__", str(int(high_speed)))
        .replace("__DMA_PIPELINE__", str(int(dma_pipeline)))
        .replace("__REVISED__", str(int(revised)))
        .replace("__RECOVERY__", str(int(recovery)))
        .replace("__RATE_192__", str(int(rate_192)))
        .replace("__PACKED_192__", str(int(packed_192)))
        .replace("__PACKED_192_WRITER__", f"0x{PACKED_192_WRITER:08x}")
        .replace("__HS_PACKET_LIMITS__", ", ".join(map(str, hs_packet_limits)))
        .replace("__DMA_BUFFER__", hex(DMA_BUFFER_192 if rate_192 else 0x0302DD88))
        .replace("__DMA_HALF_SHIFT__", "12" if rate_192 else "11")
        .replace("__MODE_SHIFT__", "4" if revised else "5")
    )
    extension = (
        assemble_diagnostics(assembly) if telemetry else assemble(assembly, EXTENSION_BASE)
    )
    if len(extension) > config_base - EXTENSION_BASE:
        raise ValueError("UAC extension overlaps its USB descriptor")
    overlay = bytearray(image[CODE_OFFSET:CODE_OFFSET + CODE_SIZE])

    def hook(address, size, target, call=False):
        operation = "bl" if call else "b.w"
        branch = assemble(f"{operation} 0x{target:08x}", address)
        if len(branch) != 4 or size < 4 or size % 2:
            raise ValueError(f"Bad hook span at 0x{address:08X}")
        p = address - CODE_BASE
        overlay[p:p + size] = branch + b"\x00\xbf" * ((size - 4) // 2)

    # Entry slots emitted by uac_multiformat.S, each exactly four bytes.
    hook(0x030801FC, 4, EXTENSION_BASE + 16)
    hook(0x03080460, 4, EXTENSION_BASE + 20)
    hook(0x0307FF76, 0x18, EXTENSION_BASE + 24, call=True)
    hook(0x03080358, 0x14, EXTENSION_BASE + 28)
    hook(0x03080116, 4, EXTENSION_BASE + 32)
    hook(0x0307FE6E, 4, EXTENSION_BASE + 36)
    if clock_test:
        hook(0x030800A6, 6, EXTENSION_BASE + 40)
    if dma_pipeline:
        hook(0x0307FEDE, 4, EXTENSION_BASE + 40)
        hook(0x03080408, 4, EXTENSION_BASE + 44, call=True)
    if revised:
        hook(0x0307FE2E, 4, EXTENSION_BASE + 48)
    if rate_192:
        bss, bss_size = unpack_from("<2I", image, MODULE_RECORD + 24)
        if not bss <= DMA_BUFFER_192 < DMA_BUFFER_192 + 0x2000 <= bss + bss_size:
            raise ValueError("192 kHz DMA buffers exceed module 11 BSS")
        widen_dma_buffers(overlay)
    if packed_192:
        writer_source = Path(__file__).with_name("uac_packed192.S").read_text(encoding="utf-8")
        writer = assemble(writer_source, PACKED_192_WRITER)
        if len(writer) > PACKED_192_WRITER_END - PACKED_192_WRITER:
            raise ValueError("Packed PCM writer overlaps live module 11 code")
        # The revised OUT callback and recovery code replace every caller
        # of the stock ring writer in this overlay.
        start = PACKED_192_WRITER - CODE_BASE
        overlay[start:start + len(writer)] = writer
    if recovery:
        recovery_source = Path(__file__).with_name("uac_recovery.S").read_text(encoding="utf-8")
        recovery_code = assemble_diagnostics(recovery_source, 0x0307FBD4)
        second_offset = 0x0307FE72 - 0x0307FBD4
        if len(recovery_code) > second_offset + 0x6C:
            raise ValueError("Recovery code overlaps the original DMA callback")
        # Both overwritten blocks are unreachable stock receive/fill bodies.
        # Their live dispatch entries are redirected to the extension above.
        first_start = 0x0307FBD4 - CODE_BASE
        second_start = 0x0307FE72 - CODE_BASE
        second_body = recovery_code[second_offset:]
        overlay[first_start:first_start + 0xA4] = recovery_code[:0xA4]
        overlay[second_start:second_start + len(second_body)] = second_body

    overlay.extend(extension)
    overlay.extend(bytes(config_base - CODE_BASE - len(overlay)))
    overlay.extend(config)
    overlay.extend(bytes(format_table - CODE_BASE - len(overlay)))
    overlay.extend(table)
    if high_speed:
        overlay.extend(config_hs)
        overlay.extend(bytes(format_hs_table - CODE_BASE - len(overlay)))
        overlay.extend(table_hs)

    # The firmware's two USB modes load module 11 or 12 in isolation.
    # Keep all original entry addresses and bound the extended UAC code
    # to the end of the USB storage overlay. The storage record is unchanged.
    if CODE_BASE + len(overlay) > USB_STORAGE_CODE_END:
        raise ValueError("UAC extension exceeds the USB overlay range")
    end = RELOCATED_OFFSET + len(overlay)
    if end > PADDING_END or any(image[RELOCATED_OFFSET:end]):
        raise ValueError("Module 11 relocation requires zero padding")

    image[RELOCATED_OFFSET:end] = overlay
    pack_into("<III", image, MODULE_RECORD,
              image_base + RELOCATED_OFFSET - 0x200, CODE_BASE, len(overlay))

    # Module 11 USB context: configuration length and FS/HS pointers.
    pack_into("<III", image, 0x80804, len(config), config_hs_base, config_base)
    pack_into("<II", image, 0x80810, EXTENSION_BASE | 1, (EXTENSION_BASE + 8) | 1)
    pack_into("<II", image, 0x8081C, (EXTENSION_BASE + 12) | 1, (EXTENSION_BASE + 4) | 1)
    revision = 0x0201
    if high_speed:
        enable_high_speed(image, EXTENSION_BASE + 52 if revised else None)
        revision = 0x0205
        if revised:
            revision = 0x0206
        if recovery:
            revision = 0x0207
        if rate_192:
            revision = 0x0208
        if packed_192:
            revision = 0x0209
    elif dma_test:
        revision = 0x0204
    elif clock_test:
        revision = 0x0203
    elif diagnostics:
        # The four-alternate configuration has a 16-bit length. The stock
        # descriptor reader uses just its low byte.
        address = 0x0307F7EC
        offset = USB_CODE_OFFSET + (address - USB_CODE_BASE)
        old = assemble("ldrb r0, [r7, #0xc]", address)
        new = assemble("ldrh r0, [r7, #0xc]", address)
        if len(old) != 2 or len(new) != 2 or image[offset:offset + 2] != old:
            raise ValueError("Unexpected configuration-length instruction")
        image[offset:offset + 2] = new
        revision = 0x0202
    elif len(config) > 255:
        raise ValueError("The stock Rockchip descriptor reader uses an 8-bit length")
    pack_into("<H", image, 0x8084C, revision)

    crc = rockchip_crc32(memoryview(image)[:-4])
    pack_into("<I", image, len(image) - 4, crc)
    return image, crc


def widen_dma_buffers(overlay):
    def replace(address, old, new):
        offset = address - CODE_BASE
        if len(old) != len(new) or overlay[offset:offset + len(old)] != old:
            raise ValueError(f"Unexpected DMA instruction or literal at 0x{address:08X}")
        overlay[offset:offset + len(old)] = new

    # The module 11 BSS has room for two aligned 4 KiB halves after staging
    # and diagnostics. Startup, underrun recovery and the legacy DMA callback
    # must address the same halves as the extension.
    for address in (0x03080164, 0x030804C0):
        replace(address, pack("<I", 0x0302DD88), pack("<I", DMA_BUFFER_192))
    for address in (0x0307FEF4, 0x030800E0, 0x03080414):
        replace(address, assemble("add.w r0, r1, r0, lsl #11", address),
                assemble("add.w r0, r1, r0, lsl #12", address))
    for address in (0x0307FF48, 0x0307FF6C):
        replace(address, assemble("mov.w r1, #0x1000", address),
                assemble("mov.w r1, #0x2000", address))


def enable_high_speed(image, out_token_handler=None):
    def replace(address, original, updated):
        offset = USB_CODE_OFFSET + address - USB_CODE_BASE
        old = assemble(original, address)
        new = assemble_diagnostics(updated.replace("; ", "\n"), address)
        if len(old) != len(new) or image[offset:offset + len(old)] != old:
            raise ValueError(f"Unexpected USB driver instruction at 0x{address:08X}")
        image[offset:offset + len(old)] = new

    # Pass the stock storage mode's high-speed negotiation argument to the
    # USB core for DAC mode as well. Hosts without HS can still select FS.
    replace(0x0307E994, "ands r0,r5,#4; it ne; movne r0,#1",
            "movs r0,#0; nop; nop; nop")

    # Read wTotalLength from whichever speed's descriptor was selected.
    replace(
        0x0307F7EC,
        "ldrb r0,[r7,#12]; ldrh r4,[r5,#6]; cmp r0,r4; it lo; movlo r4,r0; "
        "cmp.w r8,#3; ite eq; ldreq r1,[r7,#16]; ldrne r1,[r7,#20]",
        "cmp.w r8,#3; ite eq; ldreq r1,[r7,#16]; ldrne r1,[r7,#20]; "
        "ldrh r0,[r1,#2]; ldrh r4,[r5,#6]; cmp r0,r4; it lo; movlo r4,r0",
    )
    if out_token_handler is not None:
        # OUTTKNEPDIS is enabled only by the revised audio endpoint. Other
        # classes keep their original interrupt mask and acknowledgement.
        replace(
            0x0307F710,
            "tst.w r2,#2; it ne; strne.w r4,[r0,#0x308]; "
            "tst.w r2,#4; it ne; strne.w r5,[r0,#0x308]; "
            "tst.w r2,#8; it ne; strne.w r6,[r0,#0x308]",
            "ands lr,r2,#14; it ne; strne.w lr,[r0,#0x308]; "
            "tst.w r2,#16; beq no_out_token; "
            f"b.w 0x{out_token_handler:08x}; "
            "no_out_token:; nop; nop; nop; nop; nop",
        )


def main():
    parser = argparse.ArgumentParser(
        description="Build the ECHO V2.0.0 USB DAC multi-format test image."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    variant = parser.add_mutually_exclusive_group()
    variant.add_argument(
        "--diagnostics",
        action="store_true",
        help="Include the 16-bit/96 kHz comparison and USB receive status",
    )
    variant.add_argument(
        "--clock-test",
        action="store_true",
        help="Use the verified 48 kHz clock source with a doubled I2S rate for 96 kHz",
    )
    variant.add_argument(
        "--dma-test",
        action="store_true",
        help="Test 96 kHz with each DMA block ready before the previous block completes",
    )
    variant.add_argument(
        "--high-speed-192",
        action="store_true",
        help="Add 16- and 24-bit stereo PCM at 192 kHz over high-speed USB",
    )
    variant.add_argument(
        "--high-speed-192-packed",
        action="store_true",
        help="Write packed 24-bit/192 kHz samples directly to the playback ring",
    )
    variant.add_argument(
        "--high-speed-recovery",
        action="store_true",
        help="Recover a stopped high-speed isochronous OUT transfer in the audio task",
    )
    variant.add_argument(
        "--high-speed-revised",
        action="store_true",
        help="Reduce high-speed IRQ frequency and defer DMA stop waits",
    )
    variant.add_argument(
        "--high-speed",
        action="store_true",
        help="Use USB Audio Class 2 at high speed and USB Audio Class 1 at full speed",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / ".toolchain" / "python"))
    asm = (root / "uac_multiformat.S").read_text(encoding="utf-8")

    image, crc = build_multiformat(
        args.source.read_bytes(), asm, diagnostics=args.diagnostics,
        clock_test=args.clock_test, dma_test=args.dma_test, high_speed=args.high_speed,
        revised=args.high_speed_revised,
        recovery=args.high_speed_recovery,
        rate_192=args.high_speed_192,
        packed_192=args.high_speed_192_packed,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    target = args.output.open("xb")
    try:
        with target:
            target.write(image)
    except (OSError, KeyboardInterrupt):
        args.output.unlink(missing_ok=True)
        raise
    print(args.output.resolve())
    print(f"Rockchip CRC32 0x{crc:08X}")
    print(f"SHA-256 {hashlib.sha256(image).hexdigest()}")


if __name__ == "__main__":
    main()

