"""Extract the Rockchip RKnano container used by SNOWSKY ECHO V2.0.0."""

import argparse
from array import array
import csv
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import tempfile


HEADER_SIZE = 0x200
MODULE_RECORD = struct.Struct("<8I")
IMAGE_RECORD = struct.Struct("<4H2I")
LOCALES = (
    "zh-CN", "zh-TW", "en", "ja", "ko", "fr", "de", "it", "es", "pt", "ru",
    "sv", "th", "pl", "da", "nl", "el", "cs", "tr", "he", "ar",
)


def u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def span(data, offset, length, limit):
    if offset < 0 or length < 0 or offset + length > limit or limit > len(data):
        raise ValueError(f"Byte range {offset:#x}+{length:#x} exceeds {limit:#x}")
    return memoryview(data)[offset:offset + length]


def rockchip_crc32(data):
    table = []
    for byte in range(256):
        value = byte << 24
        for _ in range(8):
            polynomial = 0x04C10DB7 if value & 0x80000000 else 0
            value = ((value << 1) ^ polynomial) & 0xFFFFFFFF
        table.append(value)

    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ table[(crc >> 24) ^ byte]
    return crc


def parse_header(data):
    if len(data) < 2 * HEADER_SIZE + 4 or data[0x10:0x18] != b"Rockchip":
        raise ValueError("Expected a Rockchip firmware header")
    if data[0x1F8:0x200] != b"RKnanoFW":
        raise ValueError("Expected RKnanoFW at file offset 0x1f8")

    header_copy_offset = u32(data, 0x1F4)
    if header_copy_offset < HEADER_SIZE or data[:HEADER_SIZE] != span(
        data, header_copy_offset, HEADER_SIZE, len(data) - 4
    ):
        raise ValueError("The two RKnano headers differ")
    stored_crc = u32(data, len(data) - 4)
    computed_crc = rockchip_crc32(memoryview(data)[:-4])
    if stored_crc != computed_crc:
        raise ValueError(f"Rockchip CRC mismatch ({stored_crc:08x} versus {computed_crc:08x})")

    year, month_day = struct.unpack_from("<HH", data)
    build = f"{year:04x}-{month_day >> 8:02x}-{month_day & 0xFF:02x}"
    return header_copy_offset, stored_crc, date.fromisoformat(build).isoformat()


def resource_sections(data, header_copy_offset):
    settings_start = u32(data, 0x70)
    sections = [{
        "name": "settings",
        "offset": settings_start,
        "size": u32(data, 0x78) - settings_start,
    }]
    descriptors = (
        ("font_1", 0x78), ("font_2", 0x80), ("codepages", 0xCC),
        ("languages", 0xF8), ("images", 0x14C),
    )
    for name, descriptor in descriptors:
        offset, size = struct.unpack_from("<II", data, descriptor)
        sections.append({"name": name, "offset": offset, "size": size})
    previous_end = HEADER_SIZE
    for section in sections:
        offset, size = section["offset"], section["size"]
        if offset < previous_end or not size:
            raise ValueError("Unexpected RKnano resource layout")
        content = span(data, offset, size, header_copy_offset)
        section["sha256"] = hashlib.sha256(content).hexdigest()
        previous_end = offset + size
    return sections


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def extract_modules(data, module_limit, out):
    load_base, count = struct.unpack_from("<II", data, HEADER_SIZE)
    table_start = HEADER_SIZE + 8
    table_end = table_start + count * MODULE_RECORD.size
    if not count or table_end > module_limit:
        raise ValueError("Invalid RKnano module table")

    (out / "modules").mkdir()
    regions = []
    modules = []
    table = span(data, table_start, table_end - table_start, module_limit)
    for index, record in enumerate(MODULE_RECORD.iter_unpack(table)):
        code_load, code_exec, code_len, data_load, data_exec, data_len, bss_exec, bss_len = record
        code_kind = "code" if code_exec else "blob"
        entries = (
            (code_kind, code_load, code_exec, code_len),
            ("data", data_load, data_exec, data_len),
            ("bss", None, bss_exec, bss_len),
        )
        module = {"index": index, "record_offset": table_start + index * MODULE_RECORD.size}
        for kind, load, runtime, size in entries:
            entry = {"file_offset": None, "load_address": load, "runtime_address": runtime, "size": size}
            if size and load is not None:
                offset = load - load_base + HEADER_SIZE
                if offset < table_end:
                    raise ValueError(f"Module {index} {kind} overlaps the module table")
                payload = span(data, offset, size, module_limit)
                file = f"modules/{index:03d}.{kind}.bin"
                (out / file).write_bytes(payload)
                entry.update(file_offset=offset, file=file, sha256=hashlib.sha256(payload).hexdigest())
            if size:
                regions.append({
                    "module": index,
                    "kind": kind,
                    "file_offset": "" if entry["file_offset"] is None else f"0x{entry['file_offset']:08X}",
                    "runtime_address": f"0x{runtime:08X}" if runtime else "",
                    "size": size,
                    "file": entry.get("file", ""),
                    "sha256": entry.get("sha256", ""),
                })
            module[kind] = entry
        modules.append(module)

    write_csv(out / "module-regions.csv", regions)
    return load_base, modules


def extract_languages(section, file_offset, out):
    count, = struct.unpack_from("<H", section)
    if count != len(LOCALES):
        raise ValueError(f"Unexpected language count {count}")
    offsets = list(struct.unpack_from(f"<{count}I", section, 2))
    ends = offsets[1:] + [len(section)]
    if offsets[0] != 2 + 4 * count:
        raise ValueError("Unexpected language index length")

    rows = []
    for locale, start, end in zip(LOCALES, offsets, ends, strict=True):
        if end <= start or (end - start) % 258:
            raise ValueError(f"Unexpected string record size for {locale}")
        for index, offset in enumerate(range(start, end, 258)):
            string_id, = struct.unpack_from("<H", section, offset)
            if string_id != index:
                raise ValueError(f"Unexpected string ID in {locale} at {offset:#x}")
            text = section[offset + 58:offset + 258].tobytes().decode("utf-16-le").split("\0", 1)[0]
            rows.append({
                "locale": locale,
                "string_id": f"0x{string_id:03X}",
                "file_offset": f"0x{file_offset + offset:08X}",
                "text": text,
            })
    write_csv(out / "localizations.csv", rows)
    return len(rows)


def pixel_rgb(pixel):
    red = pixel & 31
    green = (pixel >> 5) & 63
    blue = pixel >> 11
    return bytes(((red << 3) | (red >> 2), (green << 2) | (green >> 4), (blue << 3) | (blue >> 2)))


def extract_images(section, file_offset, out):
    local_packages = Path(__file__).resolve().parent / ".toolchain" / "python"
    if local_packages.is_dir():
        sys.path.insert(0, str(local_packages))
    from PIL import Image

    if section[:16] != b"ROCK26IMAGERES  ":
        raise ValueError("Expected a ROCK26IMAGERES section")
    count = u32(section, 16)
    index_end = 0x20 + count * IMAGE_RECORD.size
    table = list(IMAGE_RECORD.iter_unpack(span(section, 0x20, index_end - 0x20, len(section))))
    if not table:
        raise ValueError("Image table is empty")
    names_start = max(offset + length for _, _, _, _, length, offset in table)
    span(section, names_start, 108 * count, len(section))

    (out / "images").mkdir()
    lookup = tuple(pixel_rgb(pixel) for pixel in range(0x10000))
    rows = []
    previous_end = table[0][-1]
    for index, (width, height, x, y, length, offset) in enumerate(table):
        if not width or not height or length != width * height * 2:
            raise ValueError(f"Unexpected pixel length for image {index}")
        if offset < index_end or offset != previous_end:
            raise ValueError(f"Unexpected pixel offset for image {index}")
        raw = span(section, offset, length, names_start)
        previous_end = offset + length
        name_record = names_start + index * 108
        if struct.unpack_from("<3I", section, name_record + 96) != (offset, width, height):
            raise ValueError(f"Image {index} disagrees with its name record")
        name = section[name_record:name_record + 45].tobytes().split(b"\0", 1)[0].decode("ascii")
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", name.rsplit(".", 1)[0]).strip("_")
        file = f"images/{index:03d}-{stem}.png"

        pixels = array("H")
        pixels.frombytes(raw)
        if sys.byteorder == "little":
            pixels.byteswap()
        rgb = b"".join(lookup[pixel] for pixel in pixels)
        Image.frombytes("RGB", (width, height), rgb).save(out / file)
        rows.append({
            "index": index, "name": name, "width": width, "height": height, "x": x, "y": y,
            "file_offset": f"0x{file_offset + offset:08X}", "size": length,
            "raw_sha256": hashlib.sha256(raw).hexdigest(), "file": file,
        })

    write_csv(out / "images.csv", rows)
    return count, file_offset + names_start


def extract(source, output):
    data = source.read_bytes()
    header_copy, crc, build_date = parse_header(data)
    sections = resource_sections(data, header_copy)
    with tempfile.TemporaryDirectory(prefix="rknano-", dir=output.parent) as temp:
        out = Path(temp) / "output"
        out.mkdir()
        (out / "resources").mkdir()
        # Module 82's stored data includes the 512 bytes before the font section.
        load_base, modules = extract_modules(data, sections[1]["offset"], out)
        for section in sections:
            name, offset, length = section["name"], section["offset"], section["size"]
            raw = span(data, offset, length, header_copy)
            if name == "images":
                images, names_offset = extract_images(raw, offset, out)
                section.update(count=images, names_offset=names_offset, pixel_format="BGR565 big-endian")
            else:
                file = f"resources/{name}.bin"
                (out / file).write_bytes(raw)
                section["file"] = file
                if name == "languages":
                    section["string_count"] = extract_languages(raw, offset, out)

        manifest = {
            "source": source.name,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "build_date": build_date,
            "header_copy_offset": header_copy,
            "rockchip_crc32": f"0x{crc:08X}",
            "module_load_base": load_base,
            "modules": modules,
            "resources": sections,
        }
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        out.rename(output)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path, help="New output directory")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error(f"Output directory already exists: {output}")
    try:
        manifest = extract(args.image, output)
    except (OSError, ValueError, struct.error) as exc:
        parser.exit(1, f"Extraction failed: {exc}\n")
    print(f"Extracted {len(manifest['modules'])} module records and {manifest['resources'][-1]['count']} images")


if __name__ == "__main__":
    main()
