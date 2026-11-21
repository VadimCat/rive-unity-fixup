"""Tests on small synthetic .riv files. Run: python -I -m unittest discover -s tests"""
import importlib.util
import os
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "riv_unity_fixup.py")
spec = importlib.util.spec_from_file_location("riv_unity_fixup", SCRIPT)
fixup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixup)
fixup.load_kinds()

SHAPE = 3
RED = 0xFFC2130F


def uint(key, value):
    return (key, fixup.varuint(value))


def name(value):
    raw = value.encode("utf-8")
    return (fixup.COMPONENT_NAME, fixup.varuint(len(raw)) + raw)


def color(value):
    return (fixup.SOLID_COLOR_VALUE, struct.pack("<I", value))


def riv(objects):
    header = b"RIVE" + fixup.varuint(7) + fixup.varuint(0) + fixup.varuint(0) + fixup.varuint(0)
    return fixup.serialize(header, objects)


def masked_artboard(artboard_name, flags=fixup.LAYER_MASK_DEFAULT_FLAGS):
    # Component ids: artboard 0, shape 1, fill 2, solid colour 3, layer mask 4.
    return [
        [fixup.ARTBOARD, [name(artboard_name)]],
        [SHAPE, [uint(fixup.COMPONENT_PARENT_ID, 0)]],
        [fixup.FILL, [uint(fixup.COMPONENT_PARENT_ID, 1)]],
        [fixup.SOLID_COLOR, [uint(fixup.COMPONENT_PARENT_ID, 2), color(RED)]],
        [fixup.LAYER_MASK, [uint(fixup.COMPONENT_PARENT_ID, 0), uint(fixup.LAYER_MASK_SOURCE_ID, 1),
                            uint(fixup.LAYER_MASK_FLAGS, flags)]],
    ]


def coloured_artboard(artboard_name, bound):
    objects = [
        [fixup.ARTBOARD, [name(artboard_name)]],
        [SHAPE, [uint(fixup.COMPONENT_PARENT_ID, 0)]],
        [fixup.FILL, [uint(fixup.COMPONENT_PARENT_ID, 1)]],
        [fixup.SOLID_COLOR, [uint(fixup.COMPONENT_PARENT_ID, 2), color(RED)]],
    ]
    if bound:
        objects.append([fixup.DATA_BIND_CONTEXT, []])
    return objects


class FixupTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def run_tool(self, objects, *options):
        source = os.path.join(self.folder.name, "in.riv")
        target = os.path.join(self.folder.name, "out.riv")
        with open(source, "wb") as handle:
            handle.write(riv(objects))
        result = subprocess.run([sys.executable, "-I", SCRIPT, source, target, *options],
                                capture_output=True, text=True)
        if result.returncode != 0:
            return result, None
        with open(target, "rb") as handle:
            return result, fixup.parse(handle.read())[1]

    def test_layer_mask_becomes_clipping_shape_and_source_paint_is_hidden(self):
        result, objects = self.run_tool(masked_artboard("main"))
        self.assertEqual(result.returncode, 0, result.stderr)
        clip = objects[4]
        self.assertEqual(clip[0], fixup.CLIPPING_SHAPE)
        self.assertEqual(fixup.uint_field(clip, fixup.CLIPPING_SHAPE_SOURCE_ID), 1)
        self.assertIsNone(fixup.field(clip, fixup.LAYER_MASK_FLAGS))
        self.assertIsNone(fixup.field(clip, fixup.CLIPPING_SHAPE_IS_VISIBLE))
        self.assertEqual(fixup.uint_field(objects[2], fixup.SHAPE_PAINT_IS_VISIBLE), 0)

    def test_disable_mask_adds_an_invisible_clip(self):
        result, objects = self.run_tool(masked_artboard("main"), "--disable-mask", "main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(fixup.uint_field(objects[4], fixup.CLIPPING_SHAPE_IS_VISIBLE), 0)

    def test_mask_with_other_settings_stops_the_tool(self):
        result, objects = self.run_tool(masked_artboard("main", flags=5))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not supported", result.stderr)
        self.assertIsNone(objects)

    def test_bind_solid_copies_the_binding_into_the_artboard(self):
        source = coloured_artboard("host", bound=True) + coloured_artboard("nested", bound=False)
        result, objects = self.run_tool(source, "--bind-solid", "nested", "C2130F")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(objects), len(source) + 1)
        self.assertEqual(objects[-1][0], fixup.DATA_BIND_CONTEXT)

    def test_unknown_artboard_is_reported(self):
        result, _ = self.run_tool(masked_artboard("main"), "--disable-mask", "missing")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not found", result.stderr)

    def test_refuses_to_overwrite_the_source(self):
        source = os.path.join(self.folder.name, "in.riv")
        with open(source, "wb") as handle:
            handle.write(riv(masked_artboard("main")))
        result = subprocess.run([sys.executable, "-I", SCRIPT, source, source], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_check_reports_prerequisites(self):
        result = subprocess.run([sys.executable, "-I", SCRIPT, "--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("property kinds", result.stdout)


if __name__ == "__main__":
    unittest.main()
