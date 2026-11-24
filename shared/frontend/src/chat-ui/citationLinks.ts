export function officialCitationUrl(value?: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol === "https:" && !url.username && !url.password
      && (url.hostname === "gov.tw" || url.hostname.endsWith(".gov.tw"))) return url.href;
  } catch { /* Display the citation as text when its source URL is invalid. */ }
  return null;
}

/** Repair a historical autolink only when its prefix is an actual approved destination. */
export function historicalLinkSuffix(href: string, display: string, trustedUrls: readonly string[]) {
  for (const url of trustedUrls) {
    if (!href.startsWith(url) || !display.startsWith(url)) continue;
    const suffix = display.slice(url.length);
    if (/^[\p{Script=Han}。，、；！？]/u.test(suffix)) return { url, suffix };
  }
  return null;
}
