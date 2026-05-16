# Alpha-OSK Branding & Asset Guide

## Logo Concept

Alpha-OSK is an AI-powered on-screen keyboard for people with disabilities. The logo should convey:
- **Accessibility** — inclusive, assistive technology
- **Intelligence** — AI-powered prediction, not just a dumb keyboard
- **Simplicity** — clean, modern, not clinical or patronizing
- **Trust** — this is a tool people depend on every day

The name "Alpha" references both the alphabet and "first/primary" — this is the user's primary way of communicating.

## Required Assets

| Asset | Size | Format | Notes |
|-------|------|--------|-------|
| App icon (Windows) | 256x256 | .ico (multi-res) | Windows taskbar, Start Menu, desktop shortcut |
| App icon (macOS) | 16→1024 (10 sizes) | .icns | Dock, Finder, Cmd+Tab switcher. Regen recipe in `docs/MACOS.md` § *Regenerating `alpha-osk.icns`* — `sips` + `iconutil`, both built-in. |
| App icon (Linux) | 1024x1024 | .png | AppImage / .desktop file (`hicolor` theme dir) |
| Tray icon | 16x16, 32x32 | .ico | System tray, must read at tiny size |
| Installer header | 150x57 | .bmp | NSIS installer banner |
| Installer sidebar | 164x314 | .bmp | NSIS welcome/finish page |
| Store tile | 300x300 | .png | Microsoft Store (future) |
| Banner | 1240x600 | .png | GitHub repo / website hero |

## Midjourney Prompts

### App Icon — Primary

These prompts target a clean, recognizable app icon. Run at `--ar 1:1`.

**Option A — Abstract letterform:**
```
Minimalist app icon, stylized letter "A" formed from keyboard keys, soft gradients, 
deep navy to electric blue, rounded corners, subtle glow effect, flat design with 
depth, white negative space, accessible technology aesthetic --ar 1:1 --s 200 --v 6.1
```

**Option B — Key + brain/spark:**
```
Modern app icon, single rounded keyboard key with a small neural spark or pulse 
emanating from center, dark background with luminous blue-purple accent, minimal 
flat design, clean vector style, technology meets accessibility --ar 1:1 --s 200 --v 6.1
```

**Option C — Constellation / connected dots:**
```
App icon, constellation pattern forming the shape of a keyboard key, connected dots 
and lines, dark navy background, glowing cyan-blue nodes, minimalist geometric, 
premium software aesthetic, no text --ar 1:1 --s 200 --v 6.1
```

### Tray Icon — Must Read at 16x16

The tray icon needs to be dead simple — recognizable at 16x16 pixels.

```
Ultra-minimal icon, single keyboard key silhouette with small pulse dot, 
white on transparent, no gradients, pixel-perfect at 16px, flat vector, 
monochrome --ar 1:1 --s 50 --v 6.1
```

```
Tiny minimal icon, letter "A" in a rounded square, clean sans-serif, 
thick stroke weight, white on transparent background, designed for 16x16 pixels, 
no detail, maximum legibility --ar 1:1 --s 50 --v 6.1
```

### Installer Banner

```
Wide horizontal banner, abstract keyboard keys dissolving into particles of light, 
deep blue-to-purple gradient, clean and modern, no text, technology accessibility 
software aesthetic, subtle depth --ar 5:2 --s 200 --v 6.1
```

### GitHub / Website Hero

```
Wide banner, on-screen keyboard interface floating above a soft gradient background, 
AI prediction suggestions glowing above the keys, accessible technology, modern UI 
design showcase, blue-purple color palette, editorial photography style, depth of field 
--ar 2:1 --s 250 --v 6.1
```

## Color Palette

Derived from the app's default "Dark" theme and Ocean/Amethyst themes:

| Role | Hex | Usage |
|------|-----|-------|
| Primary | `#4A90D9` | Links, interactive elements, icon accent |
| Background | `#1E1E2E` | Dark surfaces |
| Surface | `#2D2D3D` | Cards, key backgrounds |
| Accent | `#7C5CBF` | Highlights, AI/prediction elements |
| Text | `#FFFFFF` | Primary text |
| Success | `#4CAF50` | Confirmation states |

## Icon Generation Workflow

1. Generate candidates in Midjourney using the prompts above
2. Upscale the winner (`U1`-`U4`)
3. Clean up in Figma or Photoshop — remove artifacts, ensure clean edges
4. Export multi-resolution `.ico` using [RealFaviconGenerator](https://realfavicongenerator.net/) or ImageMagick:
   ```bash
   magick icon-256.png -define icon:auto-resize=256,128,64,48,32,16 alpha-osk.ico
   ```
5. Replace `build/windows/alpha-osk.ico` with the new icon
6. Rebuild: `python build/windows/build.py`

## Tray Icon Notes

Windows tray icons render at 16x16 (normal DPI) or 32x32 (high DPI). The icon must:
- Be recognizable without squinting
- Work on both light and dark taskbars
- Not look like a generic keyboard — differentiate from Windows OSK

Consider a two-variant approach: the full app icon for shortcuts/taskbar, and a stripped-down glyph for the tray.
