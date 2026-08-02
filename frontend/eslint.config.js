import js from "@eslint/js";
import globals from "globals";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";

export default [
  { ignores: ["dist/**", "node_modules/**"] },
  js.configs.recommended,
  {
    files: ["src/**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: "module",
      globals: { ...globals.browser, ...globals.vitest },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    settings: { react: { version: "detect" } },
    plugins: { react, "react-hooks": reactHooks },
    rules: {
      ...react.configs.flat.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      // The automatic JSX runtime means React need not be in scope.
      "react/react-in-jsx-scope": "off",
      // Props are documented by the components themselves; PropTypes would be
      // noise in an app this size with no external consumers.
      "react/prop-types": "off",
      "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
  {
    // Tests reach for node globals when stubbing browser APIs jsdom lacks.
    files: ["src/**/*.test.jsx", "src/test/**"],
    languageOptions: { globals: { ...globals.node } },
  },
];
