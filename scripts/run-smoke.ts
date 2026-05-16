import { readdirSync, existsSync, statSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const smokePaths: string[] = [];

// src/tests/*.ts
const TESTS_DIR = join(process.cwd(), "src", "tests");
if (existsSync(TESTS_DIR)) {
  for (const entry of readdirSync(TESTS_DIR)) {
    if (entry.endsWith(".ts")) {
      smokePaths.push(join(TESTS_DIR, entry));
    }
  }
}

// Legacy: src/features/<slice>/smoke.ts
const FEATURES_DIR = join(process.cwd(), "src", "features");
if (existsSync(FEATURES_DIR)) {
  for (const entry of readdirSync(FEATURES_DIR)) {
    const p = join(FEATURES_DIR, entry, "smoke.ts");
    if (statSync(join(FEATURES_DIR, entry)).isDirectory() && existsSync(p)) {
      smokePaths.push(p);
    }
  }
}

if (smokePaths.length === 0) {
  console.log("no smoke tests found — nothing to smoke");
  process.exit(0);
}

let failed = 0;
for (const path of smokePaths) {
  const label = path.replace(process.cwd() + "/", "");
  process.stdout.write(`smoke: ${label} ... `);
  try {
    await import(pathToFileURL(path).href);
    console.log("ok");
  } catch (err) {
    failed += 1;
    console.log("FAIL");
    console.error(err);
  }
}

if (failed > 0) {
  console.error(`\n${failed} of ${smokePaths.length} smoke tests failed`);
  process.exit(1);
}

console.log(`\nall ${smokePaths.length} smoke tests passed`);
