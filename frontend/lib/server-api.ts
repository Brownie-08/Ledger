import { NextRequest, NextResponse } from "next/server";

const cookieOptions = {
  httpOnly: true,
  sameSite: "strict" as const,
  secure:
    process.env.NODE_ENV === "production" &&
    process.env.SESSION_COOKIE_SECURE !== "0",
  path: "/",
};
export function sameOrigin(request: NextRequest): boolean {
  const expected =
    process.env.PUBLIC_APP_URL ||
    process.env.RENDER_EXTERNAL_URL ||
    new URL(request.url).origin;
  return request.headers.get("origin") === expected;
}
export async function backend(path: string, init: RequestInit = {}) {
  const base = new URL(process.env.API_BASE_URL || "http://127.0.0.1:8000");
  if (
    base.protocol !== "https:" &&
    !["localhost", "127.0.0.1"].includes(base.hostname) &&
    process.env.ALLOW_INTERNAL_HTTP !== "1"
  )
    throw new Error(
      "API_BASE_URL requires HTTPS or an explicitly configured private HTTP network",
    );
  return fetch(new URL(path, base), {
    ...init,
    signal: AbortSignal.timeout(12000),
    cache: "no-store",
    redirect: "error",
  });
}
export function setTokens(
  response: NextResponse,
  tokens: { access: string; refresh?: string },
) {
  response.cookies.set("ledger_access", tokens.access, {
    ...cookieOptions,
    maxAge: 15 * 60,
  });
  if (tokens.refresh)
    response.cookies.set("ledger_refresh", tokens.refresh, {
      ...cookieOptions,
      maxAge: 86400,
    });
}
export function clearTokens(response: NextResponse) {
  response.cookies.set("ledger_access", "", { ...cookieOptions, maxAge: 0 });
  response.cookies.set("ledger_refresh", "", { ...cookieOptions, maxAge: 0 });
}
export const json = (body: unknown, status = 200) =>
  NextResponse.json(body, { status, headers: { "Cache-Control": "no-store" } });
