import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    files: ["src/lib/**/*.ts"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
  {
    files: ["src/app/**/*.tsx", "src/components/**/*.tsx"],
    rules: {
      "@typescript-eslint/no-explicit-any": "warn",
      // ``react-hooks/set-state-in-effect`` (new in React 19 / Next 16)
      // flags the pattern ``useEffect(() => { fetchX().then(setX); }, [...])``
      // which is the dominant data-fetch-on-mount pattern used across
      // this portal. Refactoring every page to use Suspense / TanStack
      // Query is a much larger migration; until that happens we keep
      // the rule as a *warning* so it still surfaces in the Problems
      // panel for awareness, but doesn't mark every page red in the
      // file tree. Real bugs in new code will still appear; just not
      // as a blocking-style ``error``.
      "react-hooks/set-state-in-effect": "warn",
    },
  },
  globalIgnores([
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
