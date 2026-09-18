import { createTheme, rem } from "@mantine/core";

/** Shared visual foundation for both Mantine color schemes. */
const appTheme = createTheme({
  primaryColor: "indigo",
  primaryShade: { light: 6, dark: 5 },
  fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif",
  headings: {
    fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif",
    fontWeight: "650",
  },
  defaultRadius: "md",
  focusRing: "always",
  cursorType: "pointer",
  respectReducedMotion: true,
  spacing: { xs: rem(6), sm: rem(10), md: rem(16), lg: rem(24), xl: rem(32) },
  components: {
    Button: { defaultProps: { size: "sm" } },
    ActionIcon: { defaultProps: { size: "lg" } },
    TextInput: { defaultProps: { size: "sm" } },
    PasswordInput: { defaultProps: { size: "sm" } },
    Select: { defaultProps: { size: "sm" } },
  },
});

export default appTheme;
