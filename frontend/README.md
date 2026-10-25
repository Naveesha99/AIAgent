# AI Data Analyst frontend

The frontend is a Next.js App Router application using TypeScript, Tailwind CSS, and ESLint. It includes a real backend health-check control. Dataset upload, analysis, and backend analytics are not implemented.

## Development

From this directory:

```bash
npm install
cp .env.example .env.local
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

`NEXT_PUBLIC_API_BASE_URL` is the base URL for the FastAPI backend and defaults to `http://127.0.0.1:8000` in `.env.example`. Update it in `.env.local` if needed, then restart Next.js for environment changes to take effect. Only public, non-secret configuration belongs in variables prefixed with `NEXT_PUBLIC_`.

## Check the backend connection

Start the backend separately using the instructions in [`../backend/README.md`](../backend/README.md). With both servers running, open the frontend and select **Check backend connection**. The status reports success only after the real health endpoint returns the expected response. If it fails, confirm the backend is running at the configured URL and that its CORS origins include the frontend origin.

There is no frontend unit-test runner configured in this phase. The API client and button can be manually checked through the running application.

## Checks

```bash
npm run lint
npm run build
npm run start
```

The production server command requires a successful build.
