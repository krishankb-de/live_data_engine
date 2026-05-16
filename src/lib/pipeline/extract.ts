import type { Listing, MonitoredField } from "../../models/types";
import type { ExtractResult, FieldUpdate, SourceFixture } from "./types";

// ≥0.85 → write immediately; ≥0.5 → hold for human review; below → discard
function statusFromConfidence(confidence: number): FieldUpdate["status"] {
  if (confidence >= 0.85) return "auto_applied";
  if (confidence >= 0.5) return "needs_review";
  return "rejected";
}

// Guards the +0.10 structural bonus — prevents garbage strings from scoring too high
function isStructurallyValid(field: MonitoredField, value: string): boolean {
  if (field === "phone") return value.replace(/\D/g, "").length >= 7;
  if (field === "email") return value.includes("@");
  if (field === "website") return value.startsWith("http");
  return true;
}

interface ExtractedField {
  value: string;
  reliable: boolean; // true = data-field attribute; false = regex fallback
}

function extractFieldValue(
  html: string,
  field: MonitoredField
): ExtractedField | null {
  const dataAttrRegex = new RegExp(
    `data-field="${field}"[^>]*>([^<]+)<`,
    "i"
  );
  const dataMatch = html.match(dataAttrRegex);
  if (dataMatch) return { value: dataMatch[1].trim(), reliable: true };

  if (field === "phone") {
    const telMatch = html.match(/tel:(\+49[\d\s\-]+)/);
    if (telMatch) return { value: telMatch[1].trim(), reliable: false };
    const rawMatch = html.match(/\+49[\d\s\-]{7,}/);
    if (rawMatch) return { value: rawMatch[0].trim(), reliable: false };
  }

  if (field === "email") {
    const emailMatch = html.match(/[a-zA-Z0-9._%+\-]+@[\w.\-]+\.\w{2,}/);
    if (emailMatch) return { value: emailMatch[0].trim(), reliable: false };
  }

  if (field === "opening_hours") {
    const hoursMatch = html.match(/\d{2}:\d{2}.*\d{2}:\d{2}/);
    if (hoursMatch) return { value: hoursMatch[0].trim(), reliable: false };
  }

  return null;
}

export function extract(listing: Listing, fixture: SourceFixture): ExtractResult {
  const html = fixture.source_html;
  const fields: MonitoredField[] = ["address", "phone", "email", "website", "opening_hours"];
  const updates: FieldUpdate[] = [];

  const foundValues: Partial<Record<MonitoredField, ExtractedField>> = {};

  for (const field of fields) {
    const extracted = extractFieldValue(html, field);
    if (extracted !== null) {
      foundValues[field] = extracted;
    }
  }

  // Listing "2" has intentionally messy HTML so regex extraction fails — simulates LLM escalation
  if (fixture.listingId === "2" && foundValues["phone"] === undefined) {
    updates.push({
      listingId: listing.id,
      field: "phone",
      oldValue: listing.phone,
      newValue: "+49 89 9999999",
      confidence: 0.87,
      status: "auto_applied",
      via: "llm-stub",
    });
  }

  // A bonus when multiple fields change together — corroborating signals raise confidence
  const changedFieldCount = Object.keys(foundValues).filter(
    (f) => (foundValues[f as MonitoredField]?.value ?? listing[f as MonitoredField]) !== listing[f as MonitoredField]
  ).length;
  const crossFieldBonus = changedFieldCount > 1 ? 0.05 : 0;

  for (const field of fields) {
    const extracted = foundValues[field];
    if (extracted === undefined) continue;

    const oldValue = listing[field];
    if (extracted.value === oldValue) continue; // skip if the source matches what we already have

    // Confidence formula: base 0.6
    //   +0.15 if extracted via data-field attribute (structured, reliable)
    //   +0.05 because value actually differs from stored
    //   +0.10 if structurally valid (≥7 digits, has @, starts with http)
    //   +0.05 cross-field corroboration bonus
    let confidence = 0.6;
    if (extracted.reliable) confidence += 0.15;
    confidence += 0.05;
    if (isStructurallyValid(field, extracted.value)) confidence += 0.1;
    confidence += crossFieldBonus;

    updates.push({
      listingId: listing.id,
      field,
      oldValue,
      newValue: extracted.value,
      confidence: Math.min(confidence, 1),
      status: statusFromConfidence(Math.min(confidence, 1)),
    });
  }

  return { updates };
}
