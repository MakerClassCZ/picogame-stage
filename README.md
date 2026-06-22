# picogame-stage - run existing `stage`/`ugame` games on picogame

A drop-in **`stage` + `ugame` compatibility layer**: existing
[python-ugame/`stage`](https://github.com/python-ugame/circuitpython-stage) games run on the
**picogame** engine **unmodified** - same `code.py`, same 16-colour `.bmp` assets - just by putting
these modules on the import path instead of the originals. No `_stage` C module needed; the rendering
is reimplemented on top of the picogame C engine.

**Why bother:** the shim is **fully compatible**, **fullscreen**, and **~2x faster** than the
original `_stage` on the same game (measured on a PicoPad - see [`benchmark/`](benchmark)).

## Files
| File | What it is |
|---|---|
| `stage.py` | The `stage` API on picogame: `Bank` / `Grid` / `WallGrid` / `Sprite` / `Text` / `Stage` / `collide` (+ `BMP16`/`PNG16` loaders). Sprite `rotation` 0..7 maps to picogame flip/transpose. |
| `ugame.py` | The board half: `display`, `buttons.get_pressed()` + `K_*` constants, `audio` (best-effort). |
| `picogame_bitfont.py` | The 8x8 outlined font `stage`'s `Text` renders with (1:1 game glyphs + ASCII). Bundled so the kit is self-contained; also usable standalone for picogame HUDs. |
| `mpy/` | Pre-compiled `.mpy` of each module (smaller, faster import). |

## Requirements
- A board running **CircuitPython with the `picogame` C module built in** (the engine).
- Nothing else - `stage.py` only imports `picogame`, `picogame_bitfont` and the standard
  `board`/`time`/`array`/`struct` modules. It does **not** depend on the `picogame_*` helper library.

## Use
1. Build/flash a picogame-enabled CircuitPython firmware on the board.
2. Copy `stage.py`, `ugame.py`, `picogame_bitfont.py` (or their `mpy/` builds) into **`CIRCUITPY/lib/`**.
3. Drop an existing `stage`/`ugame` game's `code.py` + `.bmp` assets in the CIRCUITPY root. It runs
   unmodified.

## Notes
- `Text` is rendered with the bundled 8x8 bitfont (stage's own glyphs), scaled to match the screen -
  content is faithful; exact glyph shapes differ slightly from the original `_stage` font.
- `Sprite.rotation` (0..7) maps onto picogame's flip/transpose; the 4 diagonal values use transpose.
- See [`benchmark/`](benchmark) for the head-to-head `_stage`-vs-shim FPS test.

## License
MIT. `stage.py` is a compatibility reimplementation derived from
[python-ugame/circuitpython-stage](https://github.com/python-ugame/circuitpython-stage)
(MIT, © Radomir Dopieralski) - see the header in `stage.py`. See [`LICENSE`](LICENSE).
