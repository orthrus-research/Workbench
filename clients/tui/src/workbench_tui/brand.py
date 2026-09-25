"""Compact terminal projection of the Workbench mark.

The source artwork is clients/tui/assets/workbench-mark.jpg. The masks use
area-sampled square crops at 20 and 14 pixels per side. Half-block glyphs
preserve both vertical halves of a terminal cell without a runtime image codec.
"""

MARK = (
    "  ▄▄            ▄▄  ",
    "  ███▄▄      ▄▄███  ",
    "  ██ ▀▀█▄  ▄█▀▀ ██  ",
    "  ▀█▄   ▀▄█▀   ▄█▀  ",
    "    ▀█▄▄▀▀ █▄▄▀▀    ",
    "    ▄▄▀▀█ ▄▄▀▀█▄    ",
    "  ▄█▀   ▄█▀▄   ▀█▄  ",
    "  ██ ▄▄█▀  ▀█▄▄ ██  ",
    "  ███▀▀      ▀▀███  ",
    "  ▀▀            ▀▀  ",
)

COMPACT_MARK = (
    " █▄▄      ▄▄█ ",
    " █ ▀█▄  ▄█▀ █ ",
    " ▀█▄ ███▀ ▄█▀ ",
    "   ███  ███   ",
    " ▄█▀ ▄██▄ ▀█▄ ",
    " █ ▄█▀  ▀█▄ █ ",
    " █▀▀      ▀▀█ ",
)
