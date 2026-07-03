# ugame shim -> picogame. Lets unmodified python-ugame/stage games run on picogame hardware
# by import alone (board abstraction half). Pair with the stage.py shim.
#
# Provides: ugame.display, ugame.buttons.get_pressed() + K_* constants, ugame.audio.

import board

try:
    import picogame_input
except ImportError:
    picogame_input = None

display = board.DISPLAY

# Button bits. Values are arbitrary (games use the K_* names symbolically); get_pressed()
# returns these same bits, so any value set is internally consistent.
K_X = 0x01
K_DOWN = 0x02
K_LEFT = 0x04
K_RIGHT = 0x08
K_UP = 0x10
K_O = 0x20
K_START = 0x40
K_SELECT = 0x80


class _Buttons:
    def __init__(self):
        self._b = picogame_input.Buttons() if picogame_input else None

    def get_pressed(self):
        b = self._b
        if b is None:
            return 0
        b.poll()
        out = 0
        if b.is_pressed(b.UP):
            out |= K_UP
        if b.is_pressed(b.DOWN):
            out |= K_DOWN
        if b.is_pressed(b.LEFT):
            out |= K_LEFT
        if b.is_pressed(b.RIGHT):
            out |= K_RIGHT
        if b.is_pressed(b.A):
            out |= K_X            # PicoPad A -> ugame X (primary action)
        if b.is_pressed(b.B):
            out |= K_O            # PicoPad B -> ugame O (secondary)
        if b.is_pressed(b.X):
            out |= K_START
        if b.is_pressed(b.Y):
            out |= K_SELECT
        return out


buttons = _Buttons()


class _Audio:
    """Best-effort WAV playback; a no-op where audio isn't wired (e.g. the simulator)."""

    def __init__(self):
        self._a = None
        try:
            import picogame_audio
            self._a = picogame_audio.Audio()
        except Exception:
            self._a = None

    def mute(self, *a):
        pass

    def play(self, f, *a, **k):
        if self._a is None:
            return
        try:
            self._a.sfx(self._a.load(f) if hasattr(self._a, "load") else f)
        except Exception:
            pass

    def stop(self, *a):
        pass


audio = _Audio()
