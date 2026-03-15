# Spec: Windows 95 Web Experience

A nostalgic Windows 95 desktop recreation in the browser. Interactive, not just visuals.

## Features
- Desktop with classic teal/cyan background and shortcut icons (My Computer, Recycle Bin, Notepad, Calculator)
- Double-click icons to launch apps in draggable windows
- Windows have title bars with working minimize, maximize, close buttons
- Start menu opens on click with nested items (Programs, Settings, Shut Down)
- Taskbar at bottom with Start button, open window buttons, and a live clock
- Working Notepad: a real text editor with File menu (New clears text)
- Working Calculator: basic calculator with +, -, ×, ÷, =, clear
- Minimize puts window in taskbar, click taskbar button to restore
- Multiple windows can be open at once, clicking a window brings it to front
- "Shut Down" shows the classic "It is now safe to turn off your computer" screen

## Design
- Pixel-perfect Windows 95 look: beveled 3D borders, system gray (#c0c0c0), sunken panels
- Classic title bar gradient (navy blue for active, gray for inactive)
- Pixelated system font feel (use monospace or a chunky sans-serif)
- 16x16 and 32x32 style icons (built with CSS/SVG, no external images)
- Classic cursor styles (default arrow, pointer on clickable elements)
- Window drop shadow or border to separate from desktop

## Tech
- Vanilla HTML, CSS, JavaScript
- Single page, no build step, no frameworks
- All icons and graphics made with pure CSS or inline SVG
- Just open index.html in a browser
