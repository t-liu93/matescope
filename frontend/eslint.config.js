import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tsParser from "@typescript-eslint/parser";

export default [
  { ignores: ["dist", "src/api/schema.d.ts"] },
  js.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
      parser: tsParser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { "react-hooks": reactHooks, "react-refresh": reactRefresh },
    rules: { ...reactHooks.configs.recommended.rules, "react-refresh/only-export-components": "warn" },
  },
  {
    files: ["playwright.config.ts"],
    languageOptions: { globals: globals.node },
  },
];
