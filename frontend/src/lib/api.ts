export type HealthResponse = {
  status: "ok";
  service: "ai-data-analyst-api";
};

export type DatasetUploadResponse = {
  dataset_id: string;
  filename: string;
  size_bytes: number;
  content_type: string | null;
  status: "uploaded";
};

export type DatasetPreviewResponse = {
  dataset_id: string;
  filename: string;
  row_count: number;
  column_count: number;
  columns: string[];
  rows: Array<Record<string, string | null>>;
  page: number;
  page_size: number;
  total_pages: number;
};

export class ApiRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiRequestError";
  }
}

export class DatasetUploadError extends ApiRequestError {
  readonly statusCode?: number;

  constructor(message: string, statusCode?: number) {
    super(message);
    this.name = "DatasetUploadError";
    this.statusCode = statusCode;
  }
}

export class DatasetPreviewError extends ApiRequestError {
  readonly statusCode?: number;

  constructor(message: string, statusCode?: number) {
    super(message);
    this.name = "DatasetPreviewError";
    this.statusCode = statusCode;
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

export function getConfiguredMaxUploadSizeBytes(): number | undefined {
  const configured = process.env.NEXT_PUBLIC_MAX_UPLOAD_SIZE_BYTES?.trim();
  if (!configured) {
    return undefined;
  }

  const parsed = Number(configured);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : undefined;
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

function isDatasetUploadResponse(
  payload: unknown,
  expectedFilename: string,
  expectedSizeBytes: number,
): payload is DatasetUploadResponse {
  if (typeof payload !== "object" || payload === null) {
    return false;
  }

  const response = payload as Record<string, unknown>;
  return (
    typeof response.dataset_id === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      response.dataset_id,
    ) &&
    response.filename === expectedFilename &&
    typeof response.size_bytes === "number" &&
    Number.isSafeInteger(response.size_bytes) &&
    response.size_bytes === expectedSizeBytes &&
    (typeof response.content_type === "string" ||
      response.content_type === null) &&
    response.status === "uploaded"
  );
}

function getUploadErrorMessage(
  statusCode: number,
  responseText: string,
): string {
  if (statusCode === 413) {
    return "This file exceeds the upload or CSV validation limits. Choose a smaller CSV file.";
  }

  let payload: unknown;
  try {
    payload = JSON.parse(responseText);
  } catch {
    payload = null;
  }

  if (
    typeof payload === "object" &&
    payload !== null &&
    "detail" in payload &&
    typeof payload.detail === "string" &&
    statusCode >= 400 &&
    statusCode < 500
  ) {
    return payload.detail;
  }

  if (statusCode === 400 || statusCode === 422) {
    return "The server could not accept this file. Check that it is a valid CSV and try again.";
  }
  if (statusCode >= 500) {
    return "The server could not store the file. Try again later.";
  }
  return `The upload failed with HTTP ${statusCode}.`;
}

export function uploadDataset(
  file: File,
  onProgress: (percent: number | undefined) => void,
): Promise<DatasetUploadResponse> {
  const url = `${getApiBaseUrl()}/api/v1/datasets/upload`;

  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", url);
    request.setRequestHeader("Accept", "application/json");

    request.upload.addEventListener("progress", (event) => {
      onProgress(
        event.lengthComputable && event.total > 0
          ? Math.min(100, Math.round((event.loaded / event.total) * 100))
          : undefined,
      );
    });

    request.addEventListener("error", () => {
      reject(
        new DatasetUploadError(
          `Could not reach the backend at ${url}. Check that it is running and allows this frontend origin.`,
        ),
      );
    });
    request.addEventListener("abort", () => {
      reject(new DatasetUploadError("The upload was interrupted."));
    });
    request.addEventListener("load", () => {
      if (request.status < 200 || request.status >= 300) {
        reject(
          new DatasetUploadError(
            getUploadErrorMessage(request.status, request.responseText),
            request.status,
          ),
        );
        return;
      }

      let payload: unknown;
      try {
        payload = JSON.parse(request.responseText);
      } catch {
        reject(
          new DatasetUploadError(
            "The backend returned an invalid response. The upload could not be confirmed.",
          ),
        );
        return;
      }

      if (!isDatasetUploadResponse(payload, file.name, file.size)) {
        reject(
          new DatasetUploadError(
            "The backend response did not match the expected upload format. The upload could not be confirmed.",
          ),
        );
        return;
      }

      resolve(payload);
    });

    const formData = new FormData();
    formData.append("file", file);
    request.send(formData);
  });
}

function isDatasetPreviewResponse(
  payload: unknown,
  datasetId: string,
  requestedPage: number,
  requestedPageSize: number,
): payload is DatasetPreviewResponse {
  if (typeof payload !== "object" || payload === null) {
    return false;
  }
  const response = payload as Record<string, unknown>;
  if (
    response.dataset_id !== datasetId ||
    typeof response.filename !== "string" ||
    typeof response.row_count !== "number" ||
    !Number.isSafeInteger(response.row_count) ||
    response.row_count < 0 ||
    typeof response.column_count !== "number" ||
    !Number.isSafeInteger(response.column_count) ||
    response.column_count < 1 ||
    !Array.isArray(response.columns) ||
    !Array.isArray(response.rows) ||
    typeof response.page !== "number" ||
    response.page !== requestedPage ||
    typeof response.page_size !== "number" ||
    response.page_size !== requestedPageSize ||
    typeof response.total_pages !== "number" ||
    !Number.isSafeInteger(response.total_pages) ||
    response.total_pages < 1
  ) {
    return false;
  }

  const columns: unknown[] = response.columns;
  if (
    columns.length !== response.column_count ||
    !columns.every((column: unknown) => typeof column === "string")
  ) {
    return false;
  }

  const columnNames = columns as string[];
  return response.rows.every(
    (row) =>
      typeof row === "object" &&
      row !== null &&
      columnNames.every(
        (column) =>
          column in row &&
          (typeof (row as Record<string, unknown>)[column] === "string" ||
            (row as Record<string, unknown>)[column] === null),
      ),
  );
}

function getPreviewErrorMessage(
  statusCode: number,
  responseText: string,
): string {
  if (statusCode === 404) {
    return "This dataset could not be found or has expired. Upload it again to continue.";
  }

  let payload: unknown;
  try {
    payload = JSON.parse(responseText);
  } catch {
    payload = null;
  }
  if (
    typeof payload === "object" &&
    payload !== null &&
    "detail" in payload &&
    typeof payload.detail === "string" &&
    statusCode >= 400 &&
    statusCode < 500
  ) {
    return payload.detail;
  }
  if (statusCode >= 500) {
    return "The dataset was uploaded, but the server could not read its preview.";
  }
  return `The preview request failed with HTTP ${statusCode}.`;
}

export async function getDatasetPreview(
  datasetId: string,
  page: number,
  pageSize = 10,
): Promise<DatasetPreviewResponse> {
  const path =
    `/api/v1/datasets/${encodeURIComponent(datasetId)}/preview` +
    `?page=${page}&page_size=${pageSize}`;
  const url = `${getApiBaseUrl()}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
  } catch {
    throw new DatasetPreviewError(
      "The dataset was uploaded, but the backend could not be reached to load its preview.",
    );
  }

  if (!response.ok) {
    throw new DatasetPreviewError(
      getPreviewErrorMessage(response.status, await response.text()),
      response.status,
    );
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new DatasetPreviewError(
      "The dataset was uploaded, but the backend returned an invalid preview response.",
    );
  }
  if (!isDatasetPreviewResponse(payload, datasetId, page, pageSize)) {
    throw new DatasetPreviewError(
      "The dataset was uploaded, but its preview response was invalid.",
    );
  }
  return payload;
}
