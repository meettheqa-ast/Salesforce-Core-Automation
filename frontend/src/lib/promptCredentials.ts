/**
 * Extract test-user credentials embedded in a free-text generate prompt.
 * Both username and password must be present or the result is null
 * (workspace login stays authoritative).
 */

export type PromptCredentials = { username: string; password: string };

const PLACEHOLDER_RE =
  /^(\*+|\$\{.*\}|<[^>]+>|your[_-]?password|changeme|xxx+|password)$/i;

const LABELED_USER_RE =
  /\b(?:user\s*name|username|login|email|test\s*user|test\s*account)\s*[:=]\s*["']?([^\s"',;]+@?[^\s"',;]*)["']?/i;

const LABELED_PASS_RE =
  /\b(?:password|passwd|pwd|pass)\s*[:=]\s*["']?([^\s"',;]+)["']?/i;

const COMBINED_RE =
  /\b(?:as|with)\s+(?:user(?:name)?|login|account)?\s*["']?([^\s"']+@[^\s"']+)["']?\s+(?:and\s+)?(?:with\s+)?(?:password|passwd|pwd|pass)\s+["']?([^\s"',;]+)["']?/i;

const USER_THEN_PASS_RE =
  /\b(?:user(?:name)?|login|account)\s+["']?([^\s"']+@[^\s"']+)["']?[\s\S]{0,80}?\b(?:password|passwd|pwd|pass)\s+["']?([^\s"',;]+)["']?/i;

function clean(value: string): string {
  return (value || "").trim().replace(/^["']|["']$/g, "").trim();
}

function isPlaceholder(value: string): boolean {
  const v = clean(value);
  if (!v || v.length < 2) return true;
  return PLACEHOLDER_RE.test(v);
}

export function extractCredentialsFromPrompt(prompt: string): PromptCredentials | null {
  const text = prompt || "";
  if (!text.trim()) return null;

  let username = "";
  let password = "";

  for (const re of [COMBINED_RE, USER_THEN_PASS_RE]) {
    const m = re.exec(text);
    if (m) {
      username = clean(m[1]);
      password = clean(m[2]);
      break;
    }
  }

  if (!username) {
    const um = LABELED_USER_RE.exec(text);
    if (um) username = clean(um[1]);
  }
  if (!password) {
    const pm = LABELED_PASS_RE.exec(text);
    if (pm) password = clean(pm[1]);
  }

  if (isPlaceholder(username) || isPlaceholder(password)) return null;
  return { username, password };
}
