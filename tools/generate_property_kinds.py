"""Regenerate riv_property_kinds.json from a rive-runtime checkout.

A .riv file's table of contents lists field kinds only for properties the runtime does not
already know. The fixup tool must skip every field it reads, so it needs the kinds of the
runtime's own properties. They come from the generated headers: each *_base.hpp declares
`<name>PropertyKey = <key>` and reads it with a Core<Type>Type in deserialize(), and
CoreRegistry::propertyFieldId in core_registry.hpp maps `<Class>Base::<name>PropertyKey` to
its type. Each source misses a few keys the other has, so both are read and must agree.

Usage: python -I tools/generate_property_kinds.py <rive-runtime checkout> [out.json]

Only include/rive/generated is read, so a sparse checkout is enough:
    git clone --filter=blob:none --no-checkout https://github.com/rive-app/rive-runtime.git
    git -C rive-runtime sparse-checkout set --no-cone "include/rive/generated/*"
    git -C rive-runtime checkout <commit>
"""
import json
import os
import re
import subprocess
import sys

# Field kinds as the .riv table of contents encodes them.
KIND_BY_TYPE = {
    "CoreUintType": 0,
    "CoreBoolType": 0,
    "CoreIdType": 0,
    "CoreIntType": 0,  # zigzag-encoded varuint
    "CoreStringType": 1,
    "CoreBytesType": 1,
    "CoreDoubleType": 2,
    "CoreColorType": 3,
}
CLASS_PATTERN = re.compile(r"^class (\w+Base)\b", re.MULTILINE)
KEY_PATTERN = re.compile(r"static const uint16_t (\w+)PropertyKey = (\d+);")
CASE_PATTERN = re.compile(r"case (\w+)PropertyKey:\s*\n(?:#\w+.*\n)?\s*m_\w+ = (Core\w+Type)::")
FIELD_ID_START = "static int propertyFieldId(int propertyKey)"
FIELD_ID_TOKEN = re.compile(r"case (\w+Base)::(\w+)PropertyKey:|return (Core\w+Type)::id;|^  }$",
                            re.MULTILINE)
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "riv_property_kinds.json")


def fail(message):
    print(f"generate_property_kinds: {message}", file=sys.stderr)
    raise SystemExit(1)


def runtime_commit(root):
    try:
        result = subprocess.run(["git", "-C", root, "log", "-1", "--format=%h %cs"],
                                capture_output=True, text=True, check=True)
    except FileNotFoundError:
        fail("git is required to record the rive-runtime commit; install Git and put it on PATH")
    except subprocess.CalledProcessError as error:
        fail(f"{root} is not a git checkout of rive-runtime: {error.stderr.strip()}")
    short, date = result.stdout.split()
    return f"{short} ({date})"


def add_kind(kinds, key, type_name):
    if type_name not in KIND_BY_TYPE:
        fail(f"unknown field type {type_name}; extend KIND_BY_TYPE")
    kind = KIND_BY_TYPE[type_name]
    if kinds.get(key, kind) != kind:
        fail(f"property {key} has conflicting field kinds")
    kinds[key] = kind


def collect(generated):
    keys, kinds = {}, {}
    for folder, _, files in os.walk(generated):
        for name in files:
            if not name.endswith("_base.hpp"):
                continue
            text = open(os.path.join(folder, name), encoding="utf-8").read()
            own = {key_name: int(key) for key_name, key in KEY_PATTERN.findall(text)}
            match = CLASS_PATTERN.search(text)
            if match:
                keys[match.group(1)] = own
            for key_name, type_name in CASE_PATTERN.findall(text):
                if key_name in own:  # inherited keys are read in their own header
                    add_kind(kinds, own[key_name], type_name)
    registry_path = os.path.join(generated, "core_registry.hpp")
    try:
        registry = open(registry_path, encoding="utf-8").read()
    except OSError as error:
        fail(f"cannot read {registry_path}: {error}")
    start = registry.find(FIELD_ID_START)
    if start < 0:
        fail(f"{registry_path} has no propertyFieldId; the header layout changed")
    pending = []
    for token in FIELD_ID_TOKEN.finditer(registry, start):
        class_name, key_name, type_name = token.groups()
        if class_name:
            if key_name not in keys.get(class_name, {}):
                fail(f"{class_name}::{key_name}PropertyKey not found in the *_base.hpp headers")
            pending.append(keys[class_name][key_name])
        elif type_name:
            for key in pending:
                add_kind(kinds, key, type_name)
            pending = []
        else:
            break  # end of propertyFieldId
    if pending:
        fail("propertyFieldId ended with keys that have no field type")
    return kinds


def main():
    if sys.version_info < (3, 8):
        fail(f"Python 3.8 or newer is required (found {sys.version.split()[0]})")
    if len(sys.argv) not in (2, 3):
        fail("usage: python -I tools/generate_property_kinds.py <rive-runtime checkout> [out.json]")
    root = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) == 3 else DEFAULT_OUT
    generated = os.path.join(root, "include", "rive", "generated")
    if not os.path.isdir(generated):
        fail(f"{generated} not found; check out rive-runtime's include/rive/generated (see --help text)")
    kinds = collect(generated)
    if not kinds:
        fail(f"no property keys found under {generated}")
    table = {
        "source": f"rive-runtime include/rive/generated @ {runtime_commit(root)}",
        "kinds": "0 varuint, 1 bytes, 2 float32, 3 color",
        "properties": {str(key): kinds[key] for key in sorted(kinds)},
    }
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(table, indent=0) + "\n")
    print(f"{len(kinds)} property kinds -> {out}")


if __name__ == "__main__":
    main()
