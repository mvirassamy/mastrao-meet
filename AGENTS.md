# Mastrao Meet

## UI icons

- Use native solid icon components throughout the application, including meeting controls, menus and settings. Prefer `Ri…Fill` exports from `@remixicon/react`; for missing solid equivalents, use an official solid glyph (for example the vendored Heroicons `HandRaisedFill`) with its license.
- Never use outline/`Ri…Line` icons or simulate a solid variant with a `fill`/`filled` prop, CSS fill override, or stroke manipulation. Preserve accessible labels and distinct on/off states when replacing icons.
