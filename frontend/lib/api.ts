export class ApiError extends Error {
  status: number;
  code: string;
  constructor(message: string, status: number, code: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}
export async function api<T>(
  path: string,
  init: RequestInit = {},
): Promise<{ data: T; replayed: boolean }> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init.headers },
    cache: "no-store",
  });
  const body = await response.json();
  if (!response.ok)
    throw new ApiError(
      body.detail || body.error || "The request failed.",
      response.status,
      body.error || "request_failed",
    );
  return {
    data: body,
    replayed: response.headers.get("Idempotent-Replayed") === "true",
  };
}
