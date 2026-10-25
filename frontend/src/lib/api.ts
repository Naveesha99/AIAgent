export type HealthResponse = {
  status: "ok";
  service: "ai-data-analyst-api";
};

export class ApiRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiRequestError";
  }
}

function getApiBaseUrl(): string {
  const configuredUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();

  if (!configuredUrl) {
    throw new ApiRequestError(
      "NEXT_PUBLIC_API_BASE_URL is not set. Copy frontend/.env.example to frontend/.env.local and restart Next.js.",
    );
  }

  let url: URL;
  try {
    url = new URL(configuredUrl);
  } catch {
    throw new ApiRequestError(
      "NEXT_PUBLIC_API_BASE_URL must be a valid absolute HTTP or HTTPS URL.",
    );
  }

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new ApiRequestError(
      "NEXT_PUBLIC_API_BASE_URL must use HTTP or HTTPS.",
    );
  }

  return configuredUrl.replace(/\/+$/, "");
}

function isHealthResponse(payload: unknown): payload is HealthResponse {
  return (
    typeof payload === "object" &&
    payload !== null &&
    "status" in payload &&
    payload.status === "ok" &&
    "service" in payload &&
    payload.service === "ai-data-analyst-api"
  );
}

async function getJson<T>(
  path: string,
  isExpectedResponse: (payload: unknown) => payload is T,
): Promise<T> {
  const url = `${getApiBaseUrl()}${path}`;
  let response: Response;

  try {
    response = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
  } catch (error) {
    const reason =
      error instanceof Error ? ` Details: ${error.message}.` : "";
    throw new ApiRequestError(
      `Could not reach the backend at ${url}.${reason} Check that it is running and allows this frontend origin.`,
    );
  }

  if (!response.ok) {
    throw new ApiRequestError(
      `The backend returned HTTP ${response.status} for ${path}.`,
    );
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiRequestError(
      `The backend returned an invalid JSON response for ${path}.`,
    );
  }

  if (!isExpectedResponse(payload)) {
    throw new ApiRequestError(
      `The backend response for ${path} did not match the expected schema.`,
    );
  }

  return payload;
}

export function getHealthStatus(): Promise<HealthResponse> {
  return getJson("/api/v1/health", isHealthResponse);
}
