"use client";

import { ChangeEvent, DragEvent, useRef, useState } from "react";

import {
  DatasetPreviewError,
  DatasetPreviewResponse,
  DatasetUploadError,
  DatasetUploadResponse,
  getDatasetPreview,
  getConfiguredMaxUploadSizeBytes,
  uploadDataset,
} from "@/lib/api";

type UploadState = "idle" | "uploading" | "success" | "error";

const MAX_UPLOAD_SIZE_BYTES = getConfiguredMaxUploadSizeBytes();

function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024) {
    return `${sizeBytes} bytes`;
  }
  if (sizeBytes < 1024 * 1024) {
    return `${(sizeBytes / 1024).toFixed(1)} KB`;
  }
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

function validateFile(file: File): string | undefined {
  if (!file.name.toLocaleLowerCase().endsWith(".csv")) {
    return "Choose a file with a .csv extension.";
  }
  if (file.size === 0) {
    return "This file is empty. Choose a CSV that contains data.";
  }
  if (MAX_UPLOAD_SIZE_BYTES && file.size > MAX_UPLOAD_SIZE_BYTES) {
    return `This file is larger than the configured upload limit of ${formatFileSize(MAX_UPLOAD_SIZE_BYTES)}.`;
  }
  return undefined;
}

function formatApiError(error: unknown): string {
  if (error instanceof DatasetUploadError) {
    return error.message;
  }
  return "The upload failed unexpectedly. Your file is still selected, so you can retry.";
}

export default function CsvUpload() {
  const inputRef = useRef<HTMLInputElement>(null);
  const uploadingRef = useRef(false);
  const previewLoadingRef = useRef(false);
  const [selectedFile, setSelectedFile] = useState<File>();
  const [uploadState, setUploadState] = useState<UploadState>("idle");
  const [validationError, setValidationError] = useState<string>();
  const [uploadError, setUploadError] = useState<string>();
  const [progress, setProgress] = useState<number>();
  const [uploadedDataset, setUploadedDataset] =
    useState<DatasetUploadResponse>();
  const [preview, setPreview] = useState<DatasetPreviewResponse>();
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string>();
  const [dragActive, setDragActive] = useState(false);

  function acceptFile(file: File | undefined) {
    if (!file || uploadingRef.current || previewLoadingRef.current) {
      return;
    }
    const error = validateFile(file);
    setUploadError(undefined);
    setUploadedDataset(undefined);
    setPreview(undefined);
    setPreviewError(undefined);
    setValidationError(error);
    setSelectedFile(error ? undefined : file);
    setUploadState(error ? "error" : "idle");
    setProgress(undefined);
    if (inputRef.current) {
      inputRef.current.value = "";
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    acceptFile(event.currentTarget.files?.[0]);
  }

  function handleDragOver(event: DragEvent<HTMLButtonElement>) {
    event.preventDefault();
    if (!uploadingRef.current && !previewLoadingRef.current) {
      setDragActive(true);
    }
  }

  function handleDragLeave(event: DragEvent<HTMLButtonElement>) {
    event.preventDefault();
    if (
      !event.currentTarget.contains(event.relatedTarget as Node | null)
    ) {
      setDragActive(false);
    }
  }

  function handleDrop(event: DragEvent<HTMLButtonElement>) {
    event.preventDefault();
    setDragActive(false);
    if (uploadingRef.current || previewLoadingRef.current) {
      return;
    }
    const files = Array.from(event.dataTransfer.files);
    if (files.length > 1) {
      setSelectedFile(undefined);
      setUploadedDataset(undefined);
      setValidationError("Choose one CSV file at a time.");
      setUploadState("error");
      return;
    }
    acceptFile(files[0]);
  }

  async function loadPreview(datasetId: string, page: number) {
    if (previewLoadingRef.current) {
      return;
    }
    previewLoadingRef.current = true;
    setPreviewLoading(true);
    setPreviewError(undefined);
    try {
      const response = await getDatasetPreview(datasetId, page);
      setPreview(response);
    } catch (error) {
      setPreviewError(
        error instanceof DatasetPreviewError
          ? error.message
          : "The file was uploaded, but its preview could not be loaded.",
      );
    } finally {
      previewLoadingRef.current = false;
      setPreviewLoading(false);
    }
  }

  async function handleUpload() {
    if (!selectedFile || uploadingRef.current || validateFile(selectedFile)) {
      return;
    }

    uploadingRef.current = true;
    setUploadState("uploading");
    setUploadError(undefined);
    setValidationError(undefined);
    setProgress(undefined);

    try {
      const result = await uploadDataset(selectedFile, setProgress);
      setUploadedDataset(result);
      setUploadState("success");
      setSelectedFile(undefined);
      setProgress(undefined);
      void loadPreview(result.dataset_id, 1);
      if (inputRef.current) {
        inputRef.current.value = "";
      }
    } catch (error) {
      setUploadError(formatApiError(error));
      setUploadState("error");
      setProgress(undefined);
    } finally {
      uploadingRef.current = false;
    }
  }

  const isUploading = uploadState === "uploading";
  const isBusy = isUploading || previewLoading;
  const currentError = validationError ?? uploadError;

  return (
    <section
      aria-labelledby="upload-heading"
      className="mt-8 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-7"
    >
      <div className="max-w-2xl">
        <h2
          className="text-lg font-semibold text-slate-950"
          id="upload-heading"
        >
          Upload a CSV dataset
        </h2>
        <p className="mt-1 text-sm leading-6 text-slate-600">
          Choose a CSV file to validate and securely store it for a future
          analysis session.
        </p>
      </div>

      <input
        accept=".csv,text/csv"
        aria-label="Choose a CSV file"
        className="hidden"
        disabled={isBusy}
        onChange={handleFileChange}
        ref={inputRef}
        tabIndex={-1}
        type="file"
      />

      <button
        aria-describedby="upload-help"
        aria-label="Choose a CSV file, or drag and drop one here"
        className={`mt-6 flex min-h-48 w-full flex-col items-center justify-center rounded-xl border-2 border-dashed px-5 py-8 text-center transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-700 ${
          dragActive
            ? "border-indigo-600 bg-indigo-50"
            : "border-slate-300 bg-slate-50 hover:border-indigo-400 hover:bg-indigo-50/50"
        } disabled:cursor-wait disabled:opacity-70`}
        disabled={isBusy}
        onClick={() => inputRef.current?.click()}
        onDragEnter={handleDragOver}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
        type="button"
      >
        <span
          aria-hidden="true"
          className="flex h-12 w-12 items-center justify-center rounded-xl bg-white text-indigo-700 shadow-sm"
        >
          <svg
            className="h-6 w-6"
            fill="none"
            viewBox="0 0 24 24"
            xmlns="http://www.w3.org/2000/svg"
          >
            <path
              d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 14.5v3A2.5 2.5 0 0 0 7.5 20h9a2.5 2.5 0 0 0 2.5-2.5v-3"
              stroke="currentColor"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="1.7"
            />
          </svg>
        </span>
        <span className="mt-4 text-sm font-semibold text-slate-900">
          {dragActive ? "Drop your CSV file here" : "Drag and drop your file"}
        </span>
        <span className="mt-1 text-sm text-slate-600">
          or <span className="font-semibold text-indigo-700">browse files</span>
        </span>
        <span className="mt-3 text-xs text-slate-500" id="upload-help">
          CSV files only
          {MAX_UPLOAD_SIZE_BYTES
            ? ` · Maximum ${formatFileSize(MAX_UPLOAD_SIZE_BYTES)}`
            : ""}
        </span>
      </button>

      {selectedFile && (
        <div className="mt-4 flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-slate-900">
              {selectedFile.name}
            </p>
            <p className="mt-1 text-xs text-slate-600">
              {formatFileSize(selectedFile.size)}
            </p>
          </div>
          <button
            className="min-h-10 shrink-0 rounded-md border border-slate-300 px-3 text-sm font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={isBusy}
            onClick={() => {
              setSelectedFile(undefined);
              setUploadState("idle");
              setUploadError(undefined);
              setProgress(undefined);
            }}
            type="button"
          >
            Remove file
          </button>
        </div>
      )}

      {selectedFile && (
        <button
          className="mt-4 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-indigo-700 px-5 text-sm font-semibold text-white transition-colors hover:bg-indigo-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-700 disabled:cursor-wait disabled:opacity-60 sm:w-auto"
          disabled={isBusy}
          onClick={handleUpload}
          type="button"
        >
          {isUploading && (
            <span
              aria-hidden="true"
              className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white"
            />
          )}
          {isUploading ? "Uploading…" : "Upload CSV"}
        </button>
      )}

      {isUploading && (
        <div aria-live="polite" className="mt-4" role="status">
          {progress === undefined ? (
            <p className="text-sm text-slate-700">
              Uploading {selectedFile?.name}. Progress is not available yet.
            </p>
          ) : (
            <>
              <div className="flex items-center justify-between text-sm text-slate-700">
                <span>Sending {selectedFile?.name}</span>
                <span>{progress}%</span>
              </div>
              <progress
                aria-label="File upload progress"
                className="mt-2 h-2 w-full accent-indigo-700"
                max={100}
                value={progress}
              />
              <p className="mt-1 text-xs text-slate-500">
                The server will confirm the upload after validating and storing
                the file.
              </p>
            </>
          )}
        </div>
      )}

      {currentError && (
        <p
          aria-live="assertive"
          className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
          role="alert"
        >
          {currentError}
        </p>
      )}

      {uploadedDataset && (
        <div
          aria-live="polite"
          className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-950"
          role="status"
        >
          <p className="font-semibold">CSV uploaded and stored successfully.</p>
          <p className="mt-1">
            {uploadedDataset.filename} ·{" "}
            {formatFileSize(uploadedDataset.size_bytes)}
          </p>
          <p className="mt-2 break-all text-xs text-emerald-900">
            Dataset ID: {uploadedDataset.dataset_id}
          </p>
          <button
            className="mt-3 min-h-10 rounded-md border border-emerald-700 px-3 font-medium text-emerald-900 hover:bg-emerald-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-800"
            disabled={isBusy}
            onClick={() => {
              setPreview(undefined);
              setPreviewError(undefined);
              setUploadedDataset(undefined);
              setUploadState("idle");
              inputRef.current?.click();
            }}
            type="button"
          >
            Upload another CSV
          </button>
        </div>
      )}

      {uploadedDataset && (
        <section
          aria-labelledby="dataset-preview-heading"
          className="mt-6 rounded-xl border border-slate-200 bg-white"
        >
          <div className="border-b border-slate-100 p-4 sm:p-5">
            <h3
              className="text-base font-semibold text-slate-900"
              id="dataset-preview-heading"
            >
              Dataset preview
            </h3>
            {preview && (
              <>
                <p className="mt-2 break-words text-sm font-medium text-slate-800">
                  {preview.filename}
                </p>
                <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-sm text-slate-600">
                  <div>
                    <dt className="inline">Rows: </dt>
                    <dd className="inline font-medium text-slate-900">
                      {preview.row_count.toLocaleString()}
                    </dd>
                  </div>
                  <div>
                    <dt className="inline">Columns: </dt>
                    <dd className="inline font-medium text-slate-900">
                      {preview.column_count.toLocaleString()}
                    </dd>
                  </div>
                </dl>
                <p className="mt-2 break-words text-xs text-slate-600">
                  <span className="font-medium text-slate-700">
                    Column names:
                  </span>{" "}
                  {preview.columns.join(", ")}
                </p>
              </>
            )}
          </div>

          {previewLoading && (
            <p aria-live="polite" className="p-4 text-sm text-slate-700" role="status">
              Loading dataset preview…
            </p>
          )}

          {previewError && (
            <div
              aria-live="assertive"
              className="m-4 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800"
              role="alert"
            >
              <p>
                The file was uploaded successfully, but the preview could not
                be loaded. {previewError}
              </p>
              <button
                className="mt-3 min-h-10 rounded-md border border-red-700 px-3 font-medium hover:bg-red-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-800 disabled:cursor-wait disabled:opacity-60"
                disabled={isBusy}
                onClick={() =>
                  loadPreview(uploadedDataset.dataset_id, preview?.page ?? 1)
                }
                type="button"
              >
                Retry preview
              </button>
            </div>
          )}

          {preview && !previewLoading && (
            <>
              {preview.rows.length === 0 ? (
                <p className="p-4 text-sm text-slate-600">
                  There are no rows on this page.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full border-collapse text-left text-sm">
                    <thead className="bg-slate-50">
                      <tr>
                        {preview.columns.map((column, index) => (
                          <th
                            className="max-w-64 border-b border-slate-200 px-4 py-3 font-semibold text-slate-800"
                            key={`${column}-${index}`}
                            scope="col"
                          >
                            <span className="break-words">{column}</span>
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {preview.rows.map((row, rowIndex) => (
                        <tr className="odd:bg-white even:bg-slate-50/70" key={rowIndex}>
                          {preview.columns.map((column, columnIndex) => (
                            <td
                              className="max-w-64 border-b border-slate-100 px-4 py-3 align-top text-slate-700"
                              key={`${column}-${columnIndex}`}
                            >
                              <span className="block whitespace-pre-wrap break-words">
                                {row[column] === null || row[column] === ""
                                  ? "—"
                                  : row[column]}
                              </span>
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <nav
                aria-label="Dataset preview pages"
                className="flex flex-col gap-3 border-t border-slate-100 p-4 sm:flex-row sm:items-center sm:justify-between"
              >
                <p aria-live="polite" className="text-sm text-slate-600" role="status">
                  Page {preview.page} of {preview.total_pages}
                </p>
                <div className="flex gap-2">
                  <button
                    className="min-h-10 rounded-md border border-slate-300 px-3 text-sm font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={isBusy || preview.page <= 1}
                    onClick={() =>
                      loadPreview(uploadedDataset.dataset_id, preview.page - 1)
                    }
                    type="button"
                  >
                    Previous
                  </button>
                  <button
                    className="min-h-10 rounded-md border border-slate-300 px-3 text-sm font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={isBusy || preview.page >= preview.total_pages}
                    onClick={() =>
                      loadPreview(uploadedDataset.dataset_id, preview.page + 1)
                    }
                    type="button"
                  >
                    Next
                  </button>
                </div>
              </nav>
            </>
          )}
        </section>
      )}
    </section>
  );
}
