# Rive Unity Fixup

Makes an exported Rive (`.riv`) file render in Unity the way it does in the Rive editor and the web runtime.

The Rive Unity runtime loads layer masks but does not draw them. This was verified on runtime 0.5.2 and 0.5.3-canary.201 with D3D11, D3D12, Vulkan and OpenGL; the web runtime (`@rive-app/webgl2` 2.54) draws them. Anything a designer hid behind a layer mask therefore shows in Unity. Switching the runtime version or graphics API does not help.

The tool converts each layer mask into a clipping shape with the same source, which every Rive runtime draws, and hides the mask source's fills and strokes, because a default layer-mask source is not drawn either.

## Requirements

Python 3.8 or newer, standard library only. Keep `riv_property_kinds.json` next to the script. Check the setup with:

```
python -I riv_unity_fixup.py --check
```

## Use

Keep the designer's export unchanged and write the Unity copy to a new file:

```
python -I riv_unity_fixup.py --scan exported.riv
python -I riv_unity_fixup.py exported.riv Assets/Rive/exported.riv
```

`--scan` lists the layer masks per artboard and whether they can be converted. Run the conversion again on every new export and commit both files.

Options:

- `--disable-mask ARTBOARD`: that artboard's masks become disabled clips (nothing is masked, the source still does not draw). Use it for a feathered mask whose hard-edged replacement looks wrong.
- `--bind-solid ARTBOARD RRGGBB`: every unbound solid colour of that value in that artboard gets the data binding of a bound solid colour of the same value elsewhere in the file, for a nested artboard whose fixed colour should follow its host's data-bound colour.

Both options can be repeated.

## Limits

- Only layer masks with default settings are converted: alpha mode, source not drawn, no custom bounds. Any other mask stops the tool without writing a file.
- A clipping shape has a hard edge, so a feathered mask loses its softness. Check the result in Unity.
- The output is checked by parsing it again. Confirm it visually in actual Unity captures of the affected states.
- A property the runtime added after the pinned rive-runtime commit is unknown to the tool and stops it. Regenerate the property table then (see below).

## How it works

A `.riv` file is a header followed by objects, each a type key and a list of properties. Component ids such as `parentId` and a mask's source id are artboard-local indices: the artboard and every object with a `parentId` count, DataBind objects do not. The file's table of contents gives field kinds only for properties the runtime does not already know, so `riv_property_kinds.json` supplies the rest.

Regenerate it from a sparse rive-runtime checkout:

```
git clone --filter=blob:none --no-checkout https://github.com/rive-app/rive-runtime.git
git -C rive-runtime sparse-checkout set --no-cone "include/rive/generated/*"
git -C rive-runtime checkout <commit>
python -I tools/generate_property_kinds.py rive-runtime
```

The generator reads the base headers and `CoreRegistry::propertyFieldId`, and stops if they disagree or a field type is unknown.

## Tests

```
python -I -m unittest discover -s tests
```

## Provenance

Extracted on 2026-11-21 from Color Tap Away's art tooling, where it made the Rive collectors and reserve slots match their design in Unity. On those files the extracted tool produces byte-identical output. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for rive-runtime attribution.
