"""Cross-platform color-scheme palette for the chat window and theme picker.

Each scheme is a full set — background/foreground/cursor plus the 16 ANSI colors
— so any platform can recolor the embedded chat/terminal without depending on
Windows Terminal's built-in schemes or iTerm `.itermcolors` presets. Keys mirror
Windows Terminal's scheme JSON (``purple`` = magenta, ``cursorColor`` = cursor)
so the same normalizer handles these built-ins and user-defined WT schemes.

`normalize()` maps a WT-style scheme dict to the chat window's color keys;
`colors_for(name)` resolves a scheme name (case-insensitive) to those colors;
`names()` lists every built-in scheme.
"""
from __future__ import annotations

PALETTE: dict[str, dict] = {
    "Campbell": {
        "background": "#0C0C0C", "foreground": "#CCCCCC", "cursorColor": "#FFFFFF",
        "black": "#0C0C0C", "red": "#C50F1F", "green": "#13A10E", "yellow": "#C19C00",
        "blue": "#0037DA", "purple": "#881798", "cyan": "#3A96DD", "white": "#CCCCCC",
        "brightBlack": "#767676", "brightRed": "#E74856", "brightGreen": "#16C60C",
        "brightYellow": "#F9F1A5", "brightBlue": "#3B78FF", "brightPurple": "#B4009E",
        "brightCyan": "#61D6D6", "brightWhite": "#F2F2F2",
    },
    "Campbell Powershell": {
        "background": "#012456", "foreground": "#CCCCCC", "cursorColor": "#FFFFFF",
        "black": "#0C0C0C", "red": "#C50F1F", "green": "#13A10E", "yellow": "#C19C00",
        "blue": "#0037DA", "purple": "#881798", "cyan": "#3A96DD", "white": "#CCCCCC",
        "brightBlack": "#767676", "brightRed": "#E74856", "brightGreen": "#16C60C",
        "brightYellow": "#F9F1A5", "brightBlue": "#3B78FF", "brightPurple": "#B4009E",
        "brightCyan": "#61D6D6", "brightWhite": "#F2F2F2",
    },
    "Vintage": {
        "background": "#000000", "foreground": "#C0C0C0", "cursorColor": "#FFFFFF",
        "black": "#000000", "red": "#800000", "green": "#008000", "yellow": "#808000",
        "blue": "#000080", "purple": "#800080", "cyan": "#008080", "white": "#C0C0C0",
        "brightBlack": "#808080", "brightRed": "#FF0000", "brightGreen": "#00FF00",
        "brightYellow": "#FFFF00", "brightBlue": "#0000FF", "brightPurple": "#FF00FF",
        "brightCyan": "#00FFFF", "brightWhite": "#FFFFFF",
    },
    "One Half Dark": {
        "background": "#282C34", "foreground": "#DCDFE4", "cursorColor": "#DCDFE4",
        "black": "#282C34", "red": "#E06C75", "green": "#98C379", "yellow": "#E5C07B",
        "blue": "#61AFEF", "purple": "#C678DD", "cyan": "#56B6C2", "white": "#DCDFE4",
        "brightBlack": "#5A6374", "brightRed": "#E06C75", "brightGreen": "#98C379",
        "brightYellow": "#E5C07B", "brightBlue": "#61AFEF", "brightPurple": "#C678DD",
        "brightCyan": "#56B6C2", "brightWhite": "#DCDFE4",
    },
    "One Half Light": {
        "background": "#FAFAFA", "foreground": "#383A42", "cursorColor": "#383A42",
        "black": "#383A42", "red": "#E45649", "green": "#50A14F", "yellow": "#C18401",
        "blue": "#0184BC", "purple": "#A626A4", "cyan": "#0997B3", "white": "#FAFAFA",
        "brightBlack": "#4F525D", "brightRed": "#E06C75", "brightGreen": "#98C379",
        "brightYellow": "#E5C07B", "brightBlue": "#61AFEF", "brightPurple": "#C678DD",
        "brightCyan": "#56B6C2", "brightWhite": "#FFFFFF",
    },
    "Solarized Dark": {
        "background": "#002B36", "foreground": "#839496", "cursorColor": "#839496",
        "black": "#002B36", "red": "#DC322F", "green": "#859900", "yellow": "#B58900",
        "blue": "#268BD2", "purple": "#D33682", "cyan": "#2AA198", "white": "#EEE8D5",
        "brightBlack": "#073642", "brightRed": "#CB4B16", "brightGreen": "#586E75",
        "brightYellow": "#657B83", "brightBlue": "#839496", "brightPurple": "#6C71C4",
        "brightCyan": "#93A1A1", "brightWhite": "#FDF6E3",
    },
    "Solarized Light": {
        "background": "#FDF6E3", "foreground": "#657B83", "cursorColor": "#657B83",
        "black": "#073642", "red": "#DC322F", "green": "#859900", "yellow": "#B58900",
        "blue": "#268BD2", "purple": "#D33682", "cyan": "#2AA198", "white": "#EEE8D5",
        "brightBlack": "#002B36", "brightRed": "#CB4B16", "brightGreen": "#586E75",
        "brightYellow": "#657B83", "brightBlue": "#839496", "brightPurple": "#6C71C4",
        "brightCyan": "#93A1A1", "brightWhite": "#FDF6E3",
    },
    "Tango Dark": {
        "background": "#000000", "foreground": "#D3D7CF", "cursorColor": "#FFFFFF",
        "black": "#000000", "red": "#CC0000", "green": "#4E9A06", "yellow": "#C4A000",
        "blue": "#3465A4", "purple": "#75507B", "cyan": "#06989A", "white": "#D3D7CF",
        "brightBlack": "#555753", "brightRed": "#EF2929", "brightGreen": "#8AE234",
        "brightYellow": "#FCE94F", "brightBlue": "#729FCF", "brightPurple": "#AD7FA8",
        "brightCyan": "#34E2E2", "brightWhite": "#EEEEEC",
    },
    "Tango Light": {
        "background": "#FFFFFF", "foreground": "#555753", "cursorColor": "#000000",
        "black": "#000000", "red": "#CC0000", "green": "#4E9A06", "yellow": "#C4A000",
        "blue": "#3465A4", "purple": "#75507B", "cyan": "#06989A", "white": "#D3D7CF",
        "brightBlack": "#555753", "brightRed": "#EF2929", "brightGreen": "#8AE234",
        "brightYellow": "#FCE94F", "brightBlue": "#729FCF", "brightPurple": "#AD7FA8",
        "brightCyan": "#34E2E2", "brightWhite": "#EEEEEC",
    },
    "Dracula": {
        "background": "#282A36", "foreground": "#F8F8F2", "cursorColor": "#F8F8F2",
        "black": "#21222C", "red": "#FF5555", "green": "#50FA7B", "yellow": "#F1FA8C",
        "blue": "#BD93F9", "purple": "#FF79C6", "cyan": "#8BE9FD", "white": "#F8F8F2",
        "brightBlack": "#6272A4", "brightRed": "#FF6E6E", "brightGreen": "#69FF94",
        "brightYellow": "#FFFFA5", "brightBlue": "#D6ACFF", "brightPurple": "#FF92DF",
        "brightCyan": "#A4FFFF", "brightWhite": "#FFFFFF",
    },
    "Nord": {
        "background": "#2E3440", "foreground": "#D8DEE9", "cursorColor": "#D8DEE9",
        "black": "#3B4252", "red": "#BF616A", "green": "#A3BE8C", "yellow": "#EBCB8B",
        "blue": "#81A1C1", "purple": "#B48EAD", "cyan": "#88C0D0", "white": "#E5E9F0",
        "brightBlack": "#4C566A", "brightRed": "#BF616A", "brightGreen": "#A3BE8C",
        "brightYellow": "#EBCB8B", "brightBlue": "#81A1C1", "brightPurple": "#B48EAD",
        "brightCyan": "#8FBCBB", "brightWhite": "#ECEFF4",
    },
    "Gruvbox Dark": {
        "background": "#282828", "foreground": "#EBDBB2", "cursorColor": "#EBDBB2",
        "black": "#282828", "red": "#CC241D", "green": "#98971A", "yellow": "#D79921",
        "blue": "#458588", "purple": "#B16286", "cyan": "#689D6A", "white": "#A89984",
        "brightBlack": "#928374", "brightRed": "#FB4934", "brightGreen": "#B8BB26",
        "brightYellow": "#FABD2F", "brightBlue": "#83A598", "brightPurple": "#D3869B",
        "brightCyan": "#8EC07C", "brightWhite": "#EBDBB2",
    },
    "Gruvbox Light": {
        "background": "#FBF1C7", "foreground": "#3C3836", "cursorColor": "#3C3836",
        "black": "#FBF1C7", "red": "#CC241D", "green": "#98971A", "yellow": "#D79921",
        "blue": "#458588", "purple": "#B16286", "cyan": "#689D6A", "white": "#7C6F64",
        "brightBlack": "#928374", "brightRed": "#9D0006", "brightGreen": "#79740E",
        "brightYellow": "#B57614", "brightBlue": "#076678", "brightPurple": "#8F3F71",
        "brightCyan": "#427B58", "brightWhite": "#3C3836",
    },
    "Monokai": {
        "background": "#272822", "foreground": "#F8F8F2", "cursorColor": "#F8F8F2",
        "black": "#272822", "red": "#F92672", "green": "#A6E22E", "yellow": "#F4BF75",
        "blue": "#66D9EF", "purple": "#AE81FF", "cyan": "#A1EFE4", "white": "#F8F8F2",
        "brightBlack": "#75715E", "brightRed": "#F92672", "brightGreen": "#A6E22E",
        "brightYellow": "#F4BF75", "brightBlue": "#66D9EF", "brightPurple": "#AE81FF",
        "brightCyan": "#A1EFE4", "brightWhite": "#F9F8F5",
    },
    "Tokyo Night": {
        "background": "#1A1B26", "foreground": "#C0CAF5", "cursorColor": "#C0CAF5",
        "black": "#15161E", "red": "#F7768E", "green": "#9ECE6A", "yellow": "#E0AF68",
        "blue": "#7AA2F7", "purple": "#BB9AF7", "cyan": "#7DCFFF", "white": "#A9B1D6",
        "brightBlack": "#414868", "brightRed": "#F7768E", "brightGreen": "#9ECE6A",
        "brightYellow": "#E0AF68", "brightBlue": "#7AA2F7", "brightPurple": "#BB9AF7",
        "brightCyan": "#7DCFFF", "brightWhite": "#C0CAF5",
    },
    "Catppuccin Mocha": {
        "background": "#1E1E2E", "foreground": "#CDD6F4", "cursorColor": "#F5E0DC",
        "black": "#45475A", "red": "#F38BA8", "green": "#A6E3A1", "yellow": "#F9E2AF",
        "blue": "#89B4FA", "purple": "#F5C2E7", "cyan": "#94E2D5", "white": "#BAC2DE",
        "brightBlack": "#585B70", "brightRed": "#F38BA8", "brightGreen": "#A6E3A1",
        "brightYellow": "#F9E2AF", "brightBlue": "#89B4FA", "brightPurple": "#F5C2E7",
        "brightCyan": "#94E2D5", "brightWhite": "#A6ADC8",
    },
    "Night Owl": {
        "background": "#011627", "foreground": "#D6DEEB", "cursorColor": "#80A4C2",
        "black": "#011627", "red": "#EF5350", "green": "#22DA6E", "yellow": "#ADDB67",
        "blue": "#82AAFF", "purple": "#C792EA", "cyan": "#21C7A8", "white": "#FFFFFF",
        "brightBlack": "#575656", "brightRed": "#EF5350", "brightGreen": "#22DA6E",
        "brightYellow": "#FFEB95", "brightBlue": "#82AAFF", "brightPurple": "#C792EA",
        "brightCyan": "#7FDBCA", "brightWhite": "#FFFFFF",
    },
    "GitHub Dark": {
        "background": "#0D1117", "foreground": "#C9D1D9", "cursorColor": "#C9D1D9",
        "black": "#484F58", "red": "#FF7B72", "green": "#3FB950", "yellow": "#D29922",
        "blue": "#58A6FF", "purple": "#BC8CFF", "cyan": "#39C5CF", "white": "#B1BAC4",
        "brightBlack": "#6E7681", "brightRed": "#FFA198", "brightGreen": "#56D364",
        "brightYellow": "#E3B341", "brightBlue": "#79C0FF", "brightPurple": "#D2A8FF",
        "brightCyan": "#56D4DD", "brightWhite": "#F0F6FC",
    },
    "GitHub Light": {
        "background": "#FFFFFF", "foreground": "#24292F", "cursorColor": "#24292F",
        "black": "#24292F", "red": "#CF222E", "green": "#116329", "yellow": "#4D2D00",
        "blue": "#0969DA", "purple": "#8250DF", "cyan": "#1B7C83", "white": "#6E7781",
        "brightBlack": "#57606A", "brightRed": "#A40E26", "brightGreen": "#1A7F37",
        "brightYellow": "#633C01", "brightBlue": "#218BFF", "brightPurple": "#A475F9",
        "brightCyan": "#3192AA", "brightWhite": "#8C959F",
    },
}


def normalize(scheme: dict) -> dict:
    """Map a Windows-Terminal-style scheme dict to the chat window's color keys
    (purple→magenta, cursorColor→cursor)."""
    if not isinstance(scheme, dict):
        return {}

    def g(*keys):
        for k in keys:
            v = scheme.get(k)
            if isinstance(v, str) and v:
                return v
        return ""

    return {
        "background": g("background"), "foreground": g("foreground"),
        "cursor": g("cursorColor", "cursor"),
        "selection": g("selectionBackground", "selection"),
        "black": g("black"), "red": g("red"), "green": g("green"),
        "yellow": g("yellow"), "blue": g("blue"),
        "magenta": g("purple", "magenta"), "cyan": g("cyan"), "white": g("white"),
        "brightBlack": g("brightBlack"), "brightRed": g("brightRed"),
        "brightGreen": g("brightGreen"), "brightYellow": g("brightYellow"),
        "brightBlue": g("brightBlue"),
        "brightMagenta": g("brightPurple", "brightMagenta"),
        "brightCyan": g("brightCyan"), "brightWhite": g("brightWhite"),
    }


def names() -> list[str]:
    return sorted(PALETTE, key=str.lower)


def colors_for(name: str) -> dict:
    if not name:
        return {}
    for key, val in PALETTE.items():
        if key.lower() == name.lower():
            return normalize(val)
    return {}
