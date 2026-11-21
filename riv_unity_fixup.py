"""Make a Unity-ready copy of an exported Rive (.riv) file.

1. Every layer mask becomes a clipping shape. The Rive Unity runtime (verified on 0.5.2 and
   0.5.3-canary.201 with D3D11, D3D12, Vulkan and OpenGL) reads layer masks but does not draw
   them, so content hidden behind one shows in Unity. A ClippingShape with the same source
   draws the same alpha mask with a hard edge (a feather on the source is ignored), and every
   Rive runtime supports it. Only masks with default settings are converted (alpha mode,
   source not drawn, no custom bounds); anything else stops the tool. The source's fills and
   strokes are hidden, because a layer mask source with default settings does not draw.
   With --disable-mask ARTBOARD that artboard's masks become disabled clips instead (nothing
   is masked; the source still does not draw), for soft masks a hard edge cannot replace.
2. Optionally (--bind-solid ARTBOARD RRGGBB), every unbound solid colour of that value in
   that artboard gets the data binding of a bound solid colour of the same value elsewhere in
   the file. Use it when a nested artboard draws a fixed colour that should follow a
   data-bound colour of its host.

Usage:
  python -I riv_unity_fixup.py <in.riv> <out.riv> [--disable-mask ARTBOARD]...
         [--bind-solid ARTBOARD RRGGBB]...
  python -I riv_unity_fixup.py --scan <in.riv>     list layer masks per artboard
  python -I riv_unity_fixup.py --check             check prerequisites only
"""
import json
import os
import struct
import sys

RIVE_MAGIC = b"RIVE"
ARTBOARD = 1
COMPONENT_NAME = 4
COMPONENT_PARENT_ID = 5
FILL = 20
STROKE = 24
SHAPE_PAINT_IS_VISIBLE = 41
SOLID_COLOR = 18
SOLID_COLOR_VALUE = 37
DATA_BIND_CONTEXT = 447
LAYER_MASK = 154
LAYER_MASK_SOURCE_ID = 459
LAYER_MASK_FLAGS = 460
LAYER_MASK_DEFAULT_FLAGS = 4  # alpha mode, visible, source not drawn, no custom bounds
CLIPPING_SHAPE = 42
CLIPPING_SHAPE_SOURCE_ID = 92
CLIPPING_SHAPE_IS_VISIBLE = 94

# Field kinds of the runtime's own properties, which the file's table of contents leaves out
# (generated from rive-runtime's headers; see riv_property_kinds.json).
KINDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "riv_property_kinds.json")
KNOWN_KINDS = {}


def fail(message):
    print(f"riv_unity_fixup: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_varuint(data, pos):
    result, shift = 0, 0
    while True:
        if pos >= len(data):
            fail("unexpected end of file while reading a number")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return result, pos


def varuint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def parse(data):
    if data[:4] != RIVE_MAGIC:
        fail("not a .riv file (missing RIVE header)")
    pos = 4
    major, pos = read_varuint(data, pos)
    minor, pos = read_varuint(data, pos)
    _, pos = read_varuint(data, pos)  # file id
    keys = []
    while True:
        key, pos = read_varuint(data, pos)
        if key == 0:
            break
        keys.append(key)
    toc = {}
    for i in range(0, len(keys), 4):
        word = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        for j in range(4):
            if i + j < len(keys):
                toc[keys[i + j]] = (word >> (j * 2)) & 3
    header = data[:pos]
    objects = []
    while pos < len(data):
        type_key, pos = read_varuint(data, pos)
        fields = []
        while True:
            key, pos = read_varuint(data, pos)
            if key == 0:
                break
            kind = KNOWN_KINDS.get(key, toc.get(key))
            if kind is None:
                fail(f"property {key} of object type {type_key} has no known field type")
            start = pos
            if kind == 0:
                _, pos = read_varuint(data, pos)
            elif kind == 1:
                length, pos = read_varuint(data, pos)
                pos += length
            else:
                pos += 4
            fields.append((key, data[start:pos]))
        objects.append([type_key, fields])
    return header, objects, (major, minor)


def field(obj, key):
    for field_key, raw in obj[1]:
        if field_key == key:
            return raw
    return None


def uint_field(obj, key):
    raw = field(obj, key)
    return None if raw is None else read_varuint(raw, 0)[0]


def color_field(obj):
    raw = field(obj, SOLID_COLOR_VALUE)
    return None if raw is None else struct.unpack("<I", raw)[0] & 0xFFFFFF


def string_field(obj, key):
    raw = field(obj, key)
    if raw is None:
        return None
    length, pos = read_varuint(raw, 0)
    return raw[pos:pos + length].decode("utf-8")


class Artboard:
    """An artboard's objects; component ids count the artboard and every object with a parent."""

    def __init__(self, objects, start, end):
        self.objects = objects
        self.positions = list(range(start, end))
        self.name = string_field(objects[start], COMPONENT_NAME)
        self.by_id = {}
        for position in self.positions:
            obj = objects[position]
            if obj[0] == ARTBOARD or field(obj, COMPONENT_PARENT_ID) is not None:
                self.by_id[len(self.by_id)] = position
        # Sanity check of the id scheme: every solid colour must sit on a fill or stroke.
        for position in self.positions:
            obj = objects[position]
            if obj[0] == SOLID_COLOR:
                parent = self.by_id.get(uint_field(obj, COMPONENT_PARENT_ID))
                if parent is None or objects[parent][0] not in (FILL, STROKE):
                    fail(f"artboard '{self.name}': component ids could not be resolved")

    def parent_of(self, component_id):
        return uint_field(self.objects[self.by_id[component_id]], COMPONENT_PARENT_ID)


def split_artboards(objects):
    starts = [i for i, obj in enumerate(objects) if obj[0] == ARTBOARD]
    if not starts:
        fail("no artboards found")
    return [Artboard(objects, start, starts[n + 1] if n + 1 < len(starts) else len(objects))
            for n, start in enumerate(starts)]


def convert_layer_masks(objects, artboards, disabled):
    converted = 0
    for artboard in artboards:
        sources = set()
        for position in artboard.positions:
            obj = objects[position]
            if obj[0] != LAYER_MASK:
                continue
            flags = uint_field(obj, LAYER_MASK_FLAGS)
            if flags is not None and flags != LAYER_MASK_DEFAULT_FLAGS:
                fail(f"artboard '{artboard.name}': layer mask with flags {flags} is not supported")
            extra = [key for key, _ in obj[1] if key not in (COMPONENT_PARENT_ID, LAYER_MASK_SOURCE_ID, LAYER_MASK_FLAGS)]
            if extra:
                fail(f"artboard '{artboard.name}': layer mask with unsupported properties {extra}")
            source = uint_field(obj, LAYER_MASK_SOURCE_ID)
            if source is None or source not in artboard.by_id:
                fail(f"artboard '{artboard.name}': layer mask without a valid source")
            obj[0] = CLIPPING_SHAPE
            obj[1] = [(CLIPPING_SHAPE_SOURCE_ID if key == LAYER_MASK_SOURCE_ID else key, raw)
                      for key, raw in obj[1] if key != LAYER_MASK_FLAGS]
            if artboard.name in disabled:
                obj[1].append((CLIPPING_SHAPE_IS_VISIBLE, varuint(0)))
            sources.add(source)
            converted += 1
        if not sources:
            continue
        for component_id, position in artboard.by_id.items():
            obj = objects[position]
            if obj[0] not in (FILL, STROKE):
                continue
            ancestor, seen = artboard.parent_of(component_id), set()
            while ancestor is not None and ancestor not in seen and ancestor not in sources:
                seen.add(ancestor)
                ancestor = artboard.parent_of(ancestor) if ancestor in artboard.by_id else None
            if ancestor in sources:
                obj[1] = [(key, raw) for key, raw in obj[1] if key != SHAPE_PAINT_IS_VISIBLE]
                obj[1].append((SHAPE_PAINT_IS_VISIBLE, varuint(0)))
    return converted


def bind_solid_colors(objects, artboards, artboard_name, color):
    """Gives unbound solid colours of `color` in the artboard the binding of a bound one."""
    binding = None
    for position, obj in enumerate(objects[:-1]):
        if obj[0] == SOLID_COLOR and color_field(obj) == color and objects[position + 1][0] == DATA_BIND_CONTEXT:
            binding = objects[position + 1]
            break
    if binding is None:
        fail(f"no bound solid colour #{color:06X} to copy the binding from")
    targets = [artboard for artboard in artboards if artboard.name == artboard_name]
    if len(targets) != 1:
        fail(f"artboard '{artboard_name}' not found (or not unique)")
    inserts = [position for position in targets[0].positions
               if objects[position][0] == SOLID_COLOR and color_field(objects[position]) == color
               and (position + 1 == len(objects) or objects[position + 1][0] != DATA_BIND_CONTEXT)]
    if not inserts:
        fail(f"artboard '{artboard_name}' has no unbound solid colour #{color:06X}")
    # Data binds do not take component ids, so inserting them keeps every id valid.
    for position in reversed(inserts):
        objects.insert(position + 1, [binding[0], list(binding[1])])
    return len(inserts)


def scan_layer_masks(objects, artboards):
    found = 0
    for artboard in artboards:
        masks = [objects[position] for position in artboard.positions if objects[position][0] == LAYER_MASK]
        for mask in masks:
            flags = uint_field(mask, LAYER_MASK_FLAGS)
            flags = LAYER_MASK_DEFAULT_FLAGS if flags is None else flags
            extra = [key for key, _ in mask[1] if key not in (COMPONENT_PARENT_ID, LAYER_MASK_SOURCE_ID, LAYER_MASK_FLAGS)]
            state = "convertible" if flags == LAYER_MASK_DEFAULT_FLAGS and not extra else "unsupported settings"
            print(f"  artboard '{artboard.name}': layer mask, source {uint_field(mask, LAYER_MASK_SOURCE_ID)}, "
                  f"flags {flags} ({state})")
        found += len(masks)
    return found


def load_kinds():
    """Prerequisite check: the Python version and the property kind table."""
    if sys.version_info < (3, 8):
        fail(f"Python 3.8 or newer is required (found {sys.version.split()[0]}); install it from python.org")
    try:
        with open(KINDS_PATH, encoding="utf-8") as handle:
            table = json.load(handle)
        KNOWN_KINDS.update({int(key): kind for key, kind in table["properties"].items()})
    except (OSError, ValueError, KeyError, AttributeError) as error:
        fail(f"property kind table {KINDS_PATH} is missing or invalid ({error}); keep "
             "riv_property_kinds.json next to this script or regenerate it with tools/generate_property_kinds.py")
    return table.get("source", "unknown source")


def read_file(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as error:
        fail(f"cannot read {path}: {error}")


def serialize(header, objects):
    output = bytearray(header)
    for type_key, fields in objects:
        output += varuint(type_key)
        for key, raw in fields:
            output += varuint(key) + raw
        output += varuint(0)
    return bytes(output)


def main():
    source_note = load_kinds()
    args = sys.argv[1:]
    usage = ("usage: python -I riv_unity_fixup.py <in.riv> <out.riv> [--disable-mask ARTBOARD]... "
             "[--bind-solid ARTBOARD RRGGBB]...\n"
             "       python -I riv_unity_fixup.py --scan <in.riv>\n"
             "       python -I riv_unity_fixup.py --check")
    if args in (["-h"], ["--help"]):
        print(__doc__)
        return
    if args == ["--check"]:
        print(f"ok: Python {sys.version.split()[0]}, {len(KNOWN_KINDS)} property kinds ({source_note})")
        return
    if len(args) == 2 and args[0] == "--scan":
        header, objects, version = parse(read_file(args[1]))
        print(f"{args[1]}: Rive {version[0]}.{version[1]}")
        found = scan_layer_masks(objects, split_artboards(objects))
        print(f"{found} layer mask(s); the Rive Unity runtime does not draw layer masks")
        return
    if len(args) < 2 or args[0].startswith("--") or args[1].startswith("--"):
        fail(usage)
    source, target = args[0], args[1]
    disabled, bindings, rest = set(), [], args[2:]
    while rest:
        if rest[0] == "--disable-mask" and len(rest) >= 2:
            disabled.add(rest[1])
            rest = rest[2:]
        elif rest[0] == "--bind-solid" and len(rest) >= 3:
            try:
                bindings.append((rest[1], int(rest[2].lstrip("#"), 16)))
            except ValueError:
                fail(f"--bind-solid needs a hex colour like FF8800, not '{rest[2]}'")
            rest = rest[3:]
        else:
            fail(usage)
    if os.path.abspath(source) == os.path.abspath(target):
        fail("write to a new file; keep the designer's export unchanged")
    header, objects, version = parse(read_file(source))
    artboards = split_artboards(objects)
    missing = disabled - {artboard.name for artboard in artboards}
    if missing:
        fail(f"artboard(s) not found: {sorted(missing)}")
    converted = convert_layer_masks(objects, artboards, disabled)
    bound = 0
    for artboard_name, color in bindings:
        bound += bind_solid_colors(objects, split_artboards(objects), artboard_name, color)
    output = serialize(header, objects)
    _, reparsed, _ = parse(output)
    if len(reparsed) != len(objects):
        fail("round trip check failed")
    split_artboards(reparsed)
    try:
        with open(target, "wb") as handle:
            handle.write(output)
    except OSError as error:
        fail(f"cannot write {target}: {error}")
    print(f"{source} -> {target}: Rive {version[0]}.{version[1]}, "
          f"{converted} layer mask(s) converted, {bound} colour binding(s) added")


if __name__ == "__main__":
    main()
