/** Colour tokens. Exported so a test can prove every text pair meets WCAG AA (4.5:1). */
export const tokens = {
  light: {
    surface: "#F6F9F8", // ledger paper, cool
    card: "#FFFFFF",
    text: "#161D1C",
    textMuted: "#3F4947",
    outline: "#BFC9C7",
    primary: "#1F6B67", // ink teal
    onPrimary: "#FFFFFF",
    primaryContainer: "#C2EBE6",
    onPrimaryContainer: "#00201E",
    secondary: "#3B5560",
    brass: "#8A6A1F", // due soon
    brassContainer: "#FFE08F",
    onBrassContainer: "#2B1F00",
    error: "#B3261E",
    success: "#2E6B3A",
  },
  dark: {
    surface: "#0E1514",
    card: "#141D1C",
    text: "#DDE4E2",
    textMuted: "#BEC9C6",
    outline: "#3F4947",
    primary: "#7FD6CE",
    onPrimary: "#003734",
    primaryContainer: "#00504B",
    onPrimaryContainer: "#9EF2EA",
    secondary: "#B7CBD3",
    brass: "#E6C26B",
    brassContainer: "#4B3A00",
    onBrassContainer: "#FFE08F",
    error: "#F2B8B5",
    success: "#8FD39B",
  },
} as const;
