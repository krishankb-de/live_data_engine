import type { DetectResult, SourceFixture } from "./types";

export function detect(fixture: SourceFixture): DetectResult {
  // "offline" is a sentinel string set by the fetcher when the source returns non-2xx
  if (fixture.etag_new === "offline") {
    return { changed: false, signal: "offline" };
  }
  // sitemap lastmod is the strongest signal — checked before ETag
  if (fixture.sitemap_lastmod_changed) {
    return { changed: true, signal: "sitemap" };
  }
  if (fixture.etag_old !== fixture.etag_new) {
    return { changed: true, signal: "etag" };
  }
  if (fixture.last_modified_old !== fixture.last_modified_new) {
    return { changed: true, signal: "last_modified" };
  }
  return { changed: false, signal: "no_change" };
}
