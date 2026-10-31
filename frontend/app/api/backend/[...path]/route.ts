import { NextRequest } from "next/server";
import {
  backend,
  clearTokens,
  json,
  sameOrigin,
  setTokens,
} from "../../../../lib/server-api";

const allowed = {
  GET: [
    /^wallets\/$/,
    /^wallets\/\d+\/$/,
    /^transactions\/$/,
    /^transactions\/\d+\/$/,
    /^kyc\/$/,
    /^fx-rates\/$/,
  ],
  POST: [/^transfers\/$/, /^wallets\/$/],
};
async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const method = request.method as keyof typeof allowed;
  if (method !== "GET" && !sameOrigin(request))
    return json({ error: "invalid_origin" }, 403);
  const path = (await context.params).path.join("/") + "/";
  if (!allowed[method]?.some((pattern) => pattern.test(path)))
    return json({ error: "not_found" }, 404);
  let access = request.cookies.get("ledger_access")?.value;
  const refresh = request.cookies.get("ledger_refresh")?.value;
  if (!access && !refresh)
    return json({ error: "authentication_required" }, 401);
  try {
    const body = method === "POST" ? await request.text() : undefined;
    if (body && body.length > 16384)
      return json({ error: "request_too_large" }, 413);
    let renewed: { access: string; refresh?: string } | undefined;
    async function renew() {
      const response = await backend("/api/auth/token/refresh/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh }),
      });
      if (response.ok) {
        renewed = await response.json();
        access = renewed!.access;
      }
      return response.ok;
    }
    if (!access && refresh) await renew();
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      Authorization: `Bearer ${access || ""}`,
    };
    const key = request.headers.get("Idempotency-Key");
    if (key) headers["Idempotency-Key"] = key;
    const run = () =>
      backend(`/api/${path}${request.nextUrl.search}`, {
        method,
        headers,
        body,
      });
    let result = await run();
    if (result.status === 401 && refresh && !renewed && (await renew())) {
      headers.Authorization = `Bearer ${access}`;
      result = await run();
    }
    const response = json(await result.json(), result.status);
    for (const header of [
      "Idempotent-Replayed",
      "X-Request-ID",
      "Retry-After",
    ]) {
      const value = result.headers.get(header);
      if (value) response.headers.set(header, value);
    }
    if (renewed) setTokens(response, renewed);
    if (result.status === 401) clearTokens(response);
    return response;
  } catch {
    return json(
      {
        error: "api_unavailable",
        detail:
          "The API could not be reached. Retry with the same idempotency key to check whether the transfer posted.",
      },
      503,
    );
  }
}
export const GET = proxy;
export const POST = proxy;
