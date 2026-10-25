# AI Data Analyst frontend

The frontend is a Next.js App Router application using TypeScript, Tailwind CSS, and ESLint. It includes a real backend health-check control and CSV upload interface. Dataset preview, analysis, and backend analytics are not implemented.

## Development

From this directory:

```bash
npm install
cp .env.example .env.local
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

`NEXT_PUBLIC_API_BASE_URL` is the base URL for the FastAPI backend and defaults to `http://127.0.0.1:8000` in `.env.example`. `NEXT_PUBLIC_MAX_UPLOAD_SIZE_BYTES` configures an optional client-side size check and defaults to `10485760` (10 MiB). Keep this value aligned with the backend's `MAX_UPLOAD_SIZE_BYTES`; the backend remains authoritative and may also reject a CSV for validation resource limits. If the public size setting is unset or invalid, the frontend does not impose a size check. Update values in `.env.local` if needed, then restart Next.js for environment changes to take effect. Only public, non-secret configuration belongs in variables prefixed with `NEXT_PUBLIC_`.

## Check the backend connection

Start the backend separately using the instructions in [`../backend/README.md`](../backend/README.md). With both servers running, open the frontend and select **Check backend connection**. The status reports success only after the real health endpoint returns the expected response. If it fails, confirm the backend is running at the configured URL and that its CORS origins include the frontend origin.

## Upload a CSV

Start the backend separately using [`../backend/README.md`](../backend/README.md), then open the frontend and choose **Browse files** or drag one `.csv` file into the upload area. The UI provides immediate extension, empty-file, and configured-size feedback. On upload it sends the file in a `file` multipart field to `POST /api/v1/datasets/upload`. The backend validates and stores it in private temporary storage before confirming success; the UI retains and displays the returned dataset ID and requests the first page from `GET /api/v1/datasets/{dataset_id}/preview`. The preview shows the filename, total rows and columns, ordered column names, and page rows. Use **Previous** and **Next** to request more rows. Empty CSV cells are shown as an em dash. Dataset deletion, analysis, and chat are not available yet.

Upload progress is shown only when the browser reports measurable XMLHttpRequest upload events. If those events do not include a computable total, the UI shows an indeterminate uploading state rather than inventing a percentage. Even when byte transfer reaches 100%, success is not shown until the backend confirms validation and storage.

There is no frontend unit-test runner configured. Upload and preview behavior can be manually checked against the running backend.

## Checks

```bash
npm run lint
npm run build
npm run start
```

The production server command requires a successful build.
