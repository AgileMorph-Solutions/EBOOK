import { NextResponse } from "next/server";

// Evaluate env at request time so one production build works across
// dev/staging/prod without a rebuild (API_URL is read per request).
export const dynamic = "force-dynamic";

export function GET() {
  const apiUrl =
    process.env.API_URL ??
    process.env.NEXT_PUBLIC_API_URL ??
    "http://localhost:8000";

  return NextResponse.json({
    apiUrl,
    authEnabled: process.env.AUTH_ENABLED === "true",
    trimWidthIn: parseFloat(process.env.TRIM_WIDTH_IN ?? "8.5"),
    trimHeightIn: parseFloat(process.env.TRIM_HEIGHT_IN ?? "11"),
    contentModel: process.env.CONTENT_MODEL ?? "claude-opus-4-8",
    draftModel: process.env.DRAFT_MODEL ?? "claude-haiku-4-5-20251001",
    imagePrimaryModel:
      process.env.IMAGE_PRIMARY_MODEL ?? "imagen-4.0-generate-001",
    imagePrimaryProvider: process.env.IMAGE_PRIMARY_PROVIDER ?? "gemini",
  });
}
