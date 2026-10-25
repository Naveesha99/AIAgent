# AI Data Analyst frontend

The frontend is a Next.js App Router application using TypeScript, Tailwind CSS, and ESLint. Its current screen is a non-functional preview; upload, analysis, and backend integration are not implemented.

## Development

From this directory:

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Checks

```bash
npm run lint
npm run build
npm run start
```

The production server command requires a successful build. The root `.env.example` contains the placeholder frontend API URL; frontend-local values belong in an untracked `.env.local` file. No API integration reads the URL yet.
