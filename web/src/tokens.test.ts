import { tokens } from "./tokens";

function luminance(hex: string): number {
  const channel = (i: number) => {
    const c = parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

describe.each(["light", "dark"] as const)("%s colours meet WCAG AA (4.5:1)", (mode) => {
  const c = tokens[mode];
  it.each([
    ["text", "surface"],
    ["textMuted", "surface"],
    ["text", "card"],
    ["primary", "surface"],
    ["onPrimary", "primary"],
    ["onPrimaryContainer", "primaryContainer"],
    ["brass", "surface"],
    ["onBrassContainer", "brassContainer"],
    ["error", "surface"],
    ["success", "surface"],
    ["secondary", "surface"],
  ] as const)("%s on %s", (fg, bg) => {
    expect(contrast(c[fg], c[bg])).toBeGreaterThanOrEqual(4.5);
  });
});
