# Factorio mod tools

Shared packaging for my Factorio 2.0 mods. Each mod lives in its own
repository; this one holds the script that builds them.

## Usage

Place this alongside the mod folders:

```
factorio mods/
  AmbientLife/
  TreeRegrow/
  BotStart/
  tools/          <- this repo
```

Then:

```
python tools/pack.py                    # build every mod into dist/
python tools/pack.py --deploy           # ...and install into the mods folder
python tools/pack.py AmbientLife        # just one
```

`--deploy` removes older builds of the same mod first, so Factorio can never
see two versions at once. If the game is running it holds its mod zips open;
the script says so rather than failing obscurely.

## What it checks

Zips built on Windows can fail on macOS and Linux in ways that never show up
locally, so the build refuses to produce one that would:

- **Case-mismatched resource paths.** Windows filesystems are
  case-insensitive, so a `__Mod__/Graphics/bird.png` reference against a
  `graphics/` folder loads fine here and fails everywhere else.
- **Missing resource paths.** Any `__Mod__/...` reference that resolves to no
  file at all.
- **Invented event names.** `defines.events.on_something_wrong` is not a Lua
  error — it evaluates to `nil`, and `script.on_event` then fails at load with
  a traceback that only names a line number. Every `defines.events.*`
  reference is validated against the `defines.html` shipped with the installed
  game.

Archives are written with forward-slash entry names, fixed permission bits and
a fixed timestamp, so they behave identically on every platform and are
byte-reproducible between builds.

## Configuration

The event check reads the API docs shipped with Factorio. It looks in the
default Steam location; override with `FACTORIO_DEFINES_HTML` if your install
is elsewhere. Without those docs the check is skipped rather than guessed at.

## License

MIT — see [LICENSE](LICENSE).
