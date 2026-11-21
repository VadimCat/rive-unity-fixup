# Rive Unity Fixup instructions

Game-independent tool that makes exported Rive (`.riv`) files render in Unity as they do in other Rive runtimes. Keep the code and tests free of game assets, artboard names and colours; those belong in the consuming project.

- Write documentation in English. Python 3.8+ standard library only; no third-party dependencies.
- Run `python -I -m unittest discover -s tests` and `python -I riv_unity_fixup.py --check` before committing. Tests build synthetic `.riv` files; do not add game assets.
- After updating the rive-runtime pin, regenerate `riv_property_kinds.json` with `tools/generate_property_kinds.py` and keep `THIRD_PARTY_NOTICES.md` current.
- A converted file must stay byte-identical for unchanged input and options. Note any intentional output change in `CHANGELOG.md`, because consumers commit the converted files.
