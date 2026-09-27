# Mastrao Meet

## UI icons

- Use the Mastrao platform icon family everywhere, including meeting controls, menus and settings: Heroicons 20 solid (`@heroicons/react/20/solid`).
- Import icons only from `@/icons` (`src/frontend/src/icons`). Add a new icon there by wrapping the Heroicons 20 solid glyph; when Heroicons has no equivalent, draw it in `customIcons.tsx` on the same 20px solid grid in `currentColor`.
- Buttons mirror the Mastrao platform `Button`: use `Button`, `LinkButton` or `ToggleButton` from `@/primitives` with the platform variants only (`default`, `outline`, `secondary`, `ghost`, `destructive`, `invert`, `link`) and sizes (`default`, `xs`, `sm`, `lg`, `icon`, `icon-xs`, `icon-sm`, `icon-lg`). Call controls alone may use `shape="circle"`, `round` and the media variants (`hangup`, `warning`, `whiteCircle`, `errorCircle`, `bigSquare`, `permission`). Do not hand-roll `<button>` styles.
- Never use outline icons or simulate a solid variant with a `fill`/`filled` prop, CSS fill override, or stroke manipulation. Preserve accessible labels and distinct on/off states when replacing icons.
