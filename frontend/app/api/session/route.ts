import { NextRequest } from "next/server";
import {
  backend,
  clearTokens,
  json,
  sameOrigin,
  setTokens,
} from "../../../lib/server-api";

export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) return json({ error: "invalid_origin" }, 403);
  try {
    const body = await request.json();
    if (
      typeof body.username !== "string" ||
      typeof body.password !== "string" ||
      body.username.length > 150 ||
      body.password.length > 256
    )
      return json({ error: "invalid_credentials" }, 400);
    const result = await backend("/api/auth/token/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: body.username,
        password: body.password,
      }),
    });
    if (!result.ok)
      return json(
        {
          error:
            result.status === 429 ? "too_many_attempts" : "invalid_credentials",
        },
        result.status,
      );
    const response = json({ authenticated: true });
    setTokens(response, await result.json());
    return response;
  } catch {
    return json(
      {
        error: "api_unavailable",
        detail: "The API is unavailable. Your credentials were not saved.",
      },
      503,
    );
  }
}
export async function DELETE(request: NextRequest) {
  if (!sameOrigin(request)) return json({ error: "invalid_origin" }, 403);
  const response = json({ authenticated: false });
  clearTokens(response);
  return response;
}
