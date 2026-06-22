# SPDX-License-Identifier: MIT
# Compatibility shim derived from python-ugame's `stage` library
# (https://github.com/python-ugame/circuitpython-stage, MIT, (c) Radomir Dopieralski).
# The image loaders + class API mirror stage; the rendering is reimplemented on picogame.
# Ships in the ecosystem layer (attribute in THIRD_PARTY.md), NOT the pristine picogame-libs bundle.
#
# stage shim -> picogame. A drop-in reimplementation of python-ugame's `stage` library on top of
# the picogame engine - so EXISTING stage games run UNMODIFIED (same code.py, same .bmp assets),
# just by importing this `stage` (+ `ugame`) instead of the originals. No `_stage` C module needed.
#
# How it matches the original:
#  * `Stage(display, fps)` auto-scales like stage: SCALE = display.width // 128 (=2 on a 320-wide
#    PicoPad) → a 160x120 logical playfield pixel-doubled to fill the screen (that's how dinorun is
#    "fullscreen", not 128-limited). The shim does the 2x INTERNALLY in RAM from the loaded art -
#    assets are used AS-IS, never edited.
#  * `Bank` loads the original 16-colour 16x256 BMP/PNG and builds a picogame PAL8 Bitmap (16 frames
#    of 16x16, upscaled to 32x32 when SCALE==2). Transparency = the palette index whose colour is
#    stage's TRANSPARENT marker 0x1ff8 (exactly the _stage rule).
#  * Sprite rotation 0..7 maps 1:1 to picogame flip_x/flip_y/transpose.
#  * Scene has no remove(); each `render_block()` builds a fresh Scene reusing the strip buffers.

import time
import array
import struct
try:
    import zlib
except ImportError:
    zlib = None

import board
import picogame as pg

_DISP = board.DISPLAY
SCALE = max(1, _DISP.width // 128)          # pixel-doubling factor, like stage
TILE = 16 * SCALE                            # on-screen tile size (32 at SCALE 2)
TRANSPARENT = 0x1ff8                          # stage's transparent colour marker (wire-order)


def color565(r, g, b):
    return (r & 0xf8) << 8 | (g & 0xfc) << 3 | b >> 3


def collide(ax0, ay0, ax1, ay1, bx0, by0, bx1=None, by1=None):
    if bx1 is None:
        bx1 = bx0
    if by1 is None:
        by1 = by0
    return not (ax1 < bx0 or ay1 < by0 or ax0 > bx1 or ay0 > by1)


# --- image loaders (pure-Python; same format as stage, no _stage needed) -----------------

class BMP16:
    def __init__(self, filename):
        self.filename = filename
        self.colors = 0

    def read_header(self):
        with open(self.filename, 'rb') as f:
            f.seek(10)
            self.data = int.from_bytes(f.read(4), 'little')
            f.seek(18)
            self.width = int.from_bytes(f.read(4), 'little')
            self.height = int.from_bytes(f.read(4), 'little')
            f.seek(46)
            self.colors = int.from_bytes(f.read(4), 'little')

    def read_palette(self):
        palette = array.array('H', (0 for _ in range(16)))
        with open(self.filename, 'rb') as f:
            f.seek(self.data - self.colors * 4)
            for color in range(self.colors):
                buf = f.read(4)
                c = color565(buf[2], buf[1], buf[0])
                palette[color] = ((c << 8) | (c >> 8)) & 0xffff
        return palette

    def read_data(self):
        line_size = (self.width + 1) >> 1
        buffer = bytearray(line_size * self.height)
        with open(self.filename, 'rb') as f:
            f.seek(self.data)
            index = (self.height - 1) * line_size
            for _ in range(self.height):
                buffer[index:index + line_size] = f.read(line_size)
                index -= line_size
        return buffer


class PNG16:
    def __init__(self, filename):
        self.filename = filename

    def read_header(self):
        with open(self.filename, 'rb') as f:
            assert f.read(8) == b'\x89PNG\r\n\x1a\n'
            (size, chunk, self.width, self.height, self.depth, self.mode,
             self.compression, self.filters, self.interlaced, crc) = struct.unpack(
                ">I4sIIBBBBB4s", f.read(25))
            if self.depth not in (4, 8) or self.mode != 3 or self.interlaced:
                raise ValueError("16-color non-interlaced PNG expected")

    def read_palette(self):
        palette = array.array('H', (0 for _ in range(16)))
        with open(self.filename, 'rb') as f:
            f.seek(8 + 25)
            while True:
                size, chunk = struct.unpack(">I4s", f.read(8))
                if chunk == b'PLTE':
                    break
                f.seek(size + 4, 1)
            for color in range(min(16, size // 3)):
                c = color565(*struct.unpack("BBB", f.read(3)))
                palette[color] = ((c << 8) | (c >> 8)) & 0xffff
        return palette

    def read_data(self):
        data = bytearray()
        with open(self.filename, 'rb') as f:
            f.seek(8 + 25)
            while True:
                size, chunk = struct.unpack(">I4s", f.read(8))
                if chunk == b'IEND':
                    break
                if chunk != b'IDAT':
                    f.seek(size + 4, 1)
                    continue
                data.extend(f.read(size))
                f.seek(4, 1)
        data = zlib.decompress(data)
        line_size = (self.width + 1) >> 1
        buffer = bytearray(line_size * self.height)
        if self.depth == 4:
            for line in range(self.height):
                a = line * line_size
                b = line * (line_size + 1)
                buffer[a:a + line_size] = data[b + 1:b + 1 + line_size]
        else:  # depth 8 -> repack to 4-bit
            for line in range(self.height):
                a = line * line_size
                b = line * (self.width + 1) + 1
                for _ in range(line_size):
                    v = (data[b] & 0x0f) << 4
                    b += 1
                    if b - line * (self.width + 1) - 1 <= self.width:
                        try:
                            v |= data[b] & 0x0f
                        except IndexError:
                            pass
                    b += 1
                    buffer[a] = v
                    a += 1
        return buffer


# --- the stage classes, picogame-backed --------------------------------------------------

class Bank:
    """16 tiles of 16x16, 16-colour. Backed by a picogame PAL8 Bitmap (frames upscaled to TILE)."""

    def __init__(self, buffer, palette):
        self.palette = palette
        # transparent = the index whose colour is stage's 0x1ff8 marker (else opaque)
        transp = None
        for i in range(len(palette)):
            if palette[i] == TRANSPARENT:
                transp = i
                break
        # unpack the 4-bit 16x256 buffer into a horizontal 16-frame atlas, upscaled x SCALE
        nframes = 16
        atlas_w = nframes * TILE
        data = bytearray(atlas_w * TILE)
        for f in range(nframes):
            base = f * 128                      # 128 bytes/tile (16 rows x 8 bytes)
            fx = f * TILE
            for sy in range(16):
                row = base + sy * 8
                for sx in range(16):
                    byte = buffer[row + (sx >> 1)]
                    idx = (byte >> 4) if (sx & 1) == 0 else (byte & 0x0f)   # high nibble = left px
                    for dy in range(SCALE):
                        dst = (sy * SCALE + dy) * atlas_w + fx + sx * SCALE
                        for dx in range(SCALE):
                            data[dst + dx] = idx
        self.bitmap = pg.Bitmap(data, TILE, TILE, format=pg.PAL8, palette=palette,
                                frames=nframes, stride=atlas_w, transparent=transp)

    @classmethod
    def from_image(cls, filename):
        img = BMP16(filename) if filename.lower().endswith(".bmp") else PNG16(filename)
        img.read_header()
        if img.width != 16 or img.height != 256:
            raise ValueError("Image size not 16x256")
        return cls(img.read_data(), img.read_palette())

    @classmethod
    def from_bmp16(cls, filename):
        return cls.from_image(filename)


class Grid:
    def __init__(self, bank, width=8, height=8, palette=None, buffer=None):
        self.x = 0
        self.y = 0
        self.z = 0
        self.width = width
        self.height = height
        self.bank = bank
        self._tm = pg.Tilemap(bank.bitmap, width, height)

    def tile(self, x, y, tile=None):
        if not 0 <= x < self.width or not 0 <= y < self.height:
            return 0
        if tile is None:
            return self._tm.tile(x, y)
        self._tm.tile(x, y, tile)

    def move(self, x, y, z=None):
        self.x = x
        self.y = y
        if z is not None:
            self.z = z
        self._tm.move(int(x) * SCALE, int(y) * SCALE)


class WallGrid(Grid):
    def __init__(self, grid, walls, bank, palette=None):
        super().__init__(bank, grid.width + 1, grid.height + 1, palette)
        self.grid = grid
        self.walls = walls
        self.update()
        self.move(self.x - 8, self.y - 8)

    def update(self):
        for y in range(self.height):
            for x in range(self.width):
                t = 0
                bit = 1
                for dy in (-1, 0):
                    for dx in (-1, 0):
                        if self.grid.tile(x + dx, y + dy) in self.walls:
                            t |= bit
                        bit <<= 1
                self.tile(x, y, t)


# stage rotation 0..7 -> (flip_x, flip_y, transpose)
_ROT = (
    (False, False, False),   # 0 none
    (False, True, True),     # 1 90 CW   (transpose + flip_y)
    (True, True, False),     # 2 180
    (True, False, True),     # 3 90 CCW  (transpose + flip_x)
    (True, False, False),    # 4 mirror
    (True, True, True),      # 5 90 CW + mirror
    (False, True, False),    # 6 180 + mirror
    (False, False, True),    # 7 90 CCW + mirror
)


class Sprite:
    def __init__(self, bank, frame, x, y, z=0, rotation=0, palette=None):
        self.bank = bank
        self.frame = frame
        self.rotation = rotation
        self.x = x
        self.y = y
        self.z = z
        self.px = x
        self.py = y
        self._spr = pg.Sprite(bank.bitmap, int(x) * SCALE, int(y) * SCALE, frame=frame)
        self._apply_rot()

    def _apply_rot(self):
        fx, fy, tr = _ROT[self.rotation & 7]
        self._spr.flip_x = fx
        self._spr.flip_y = fy
        self._spr.transpose = tr

    def move(self, x, y, z=None):
        self.x = x
        self.y = y
        if z is not None:
            self.z = z
        self._spr.move(int(x) * SCALE, int(y) * SCALE)

    def set_frame(self, frame=None, rotation=None):
        if frame is not None:
            self.frame = frame
            self._spr.frame = frame
        if rotation is not None:
            self.rotation = rotation
            self._apply_rot()

    def update(self):
        pass


class Text:
    """Text layer. Rendered with the bundled terminalio font (content faithful; glyphs differ)."""

    def __init__(self, width, height, font=None, palette=None, buffer=None):
        self.width = width
        self.height = height
        self.x = 0
        self.y = 0
        self.z = 0
        self.column = 0
        self.row = 0
        self._lines = [""]
        self._fg = pg.rgb565(255, 255, 255)
        self._spr = pg.Sprite(pg.Bitmap(bytearray(2), 1, 1, format=pg.PAL8,
                                        palette=array.array('H', (0, 0)), transparent=0), 0, 0)
        self._spr.visible = False

    def _render(self):
        s = "\n".join(self._lines).rstrip("\n")
        if not s:
            self._spr.visible = False
            return
        try:
            # the 8x8 bitfont = stage's own glyphs (1:1), scaled to match the rest of the screen
            import picogame_bitfont
            bmp, w, h = picogame_bitfont.render_text(pg, s, fg=self._fg)
            self._spr.bitmap = bmp
            self._spr.scale = SCALE
            self._spr.visible = True
        except Exception:
            self._spr.visible = False

    def char(self, x, y, c=None, highlight=False):
        return None

    def move(self, x, y, z=None):
        self.x = x
        self.y = y
        if z is not None:
            self.z = z
        self._spr.move(int(x) * SCALE, int(y) * SCALE)

    def cursor(self, x=None, y=None):
        if x is not None:
            self.column = x
        if y is not None:
            self.row = y

    def text(self, text, highlight=False):
        self._lines = text.split("\n")
        self._render()
        return len(text) * 8, 8

    def clear(self):
        self._lines = [""]
        self._render()


class Stage:
    def __init__(self, display, fps=6, scale=None):
        self.display = display
        self.layers = []
        self.scale = SCALE if scale is None else scale
        self.width = display.width // self.scale
        self.height = display.height // self.scale
        self.vx = 0
        self.vy = 0
        self.last_tick = time.monotonic()
        self.tick_delay = 1 / fps
        # take over the display + alloc strip buffers once (reused across rounds)
        display.auto_refresh = False
        try:
            display.root_group = None
        except Exception:
            pass
        w = display.width
        self._bufA = bytearray(w * 24 * 2)
        self._bufB = bytearray(w * 24 * 2)
        self._backend = pg.Display(display) if hasattr(pg, "Display") else display
        self._scene = None

    def _pgitem(self, layer):
        return getattr(layer, "_tm", None) or getattr(layer, "_spr", None)

    def render_block(self, x0=None, y0=None, x1=None, y1=None):
        # Scene has no remove(); build a fresh one (reusing the buffers) with the current layers.
        # stage layer order is front->back; picogame adds back->front, so add reversed.
        self._scene = pg.Scene(self._backend, self._bufA, self._bufB, background=pg.rgb565(0, 0, 0))
        for layer in reversed(self.layers):
            item = self._pgitem(layer)
            if item is not None:
                self._scene.add(item)
        self._scene.invalidate()
        self._scene.refresh()

    def render_sprites(self, sprites):
        if self._scene is not None:
            self._scene.refresh()

    def tick(self):
        self.last_tick += self.tick_delay
        wait = self.last_tick - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        else:
            self.last_tick = time.monotonic()
