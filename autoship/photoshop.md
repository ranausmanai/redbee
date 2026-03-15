# Spec: PixelForge — Browser-Based Image Editor

A fully functional image editor in the browser. Think Photoshop lite, built entirely in vanilla JS with Canvas API.

## Core Features
- Open images from local file system (drag & drop or file picker)
- Canvas-based editing with real pixel manipulation
- Undo/redo with full history stack (at least 20 steps)
- Export/save as PNG or JPEG with quality slider
- Zoom in/out with mousewheel, pan with spacebar + drag

## Tools (left toolbar, icon-based)
- Brush: variable size and opacity, smooth drawing with interpolation between points
- Eraser: same as brush but removes pixels
- Rectangle select: marching ants selection, move/delete/copy selected region
- Color picker / eyedropper: click canvas to sample a color
- Text tool: click canvas to place text, choose font size
- Fill bucket: flood fill a region with current color
- Line tool: click and drag to draw straight lines
- Crop tool: select region and crop canvas to it

## Layers Panel (right side)
- Multiple layers with add/delete/reorder
- Layer visibility toggle (eye icon)
- Layer opacity slider
- Active layer highlight
- Layers composite in order for final render

## Filters Menu (top menu bar)
- Grayscale
- Sepia
- Blur (gaussian-style box blur)
- Sharpen
- Brightness / Contrast sliders
- Invert colors
- Apply filter to active layer only

## UI Layout
- Top: menu bar (File, Edit, Filters, View) with dropdown menus
- Left: vertical tool palette with icons, active tool highlighted
- Center: canvas workspace with checkerboard transparency background
- Right: layers panel + color picker panel
- Bottom: status bar showing cursor position, canvas size, zoom level, active tool name

## Color Picker Panel (right side, below layers)
- Large color spectrum square (saturation/brightness) with hue slider
- Current foreground/background color swatches (click to swap)
- Hex input field
- RGB sliders
- Recent colors row (last 12 used)

## Design
- Dark UI theme (like actual Photoshop — dark gray panels, subtle borders)
- Crisp iconography using inline SVG for all tool icons
- Smooth, responsive — no lag on brush strokes
- Resizable panels would be nice but not required
- Canvas should fill available workspace

## Tech
- Vanilla HTML, CSS, JavaScript only
- Canvas API for all pixel operations
- No frameworks, no build step
- Single page — just open index.html
- All icons and UI elements in pure CSS/SVG
