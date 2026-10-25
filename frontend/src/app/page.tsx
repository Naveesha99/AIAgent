import ConnectionStatus from "./ConnectionStatus";

function DataMark() {
  return (
    <svg
      aria-hidden="true"
      className="h-5 w-5"
      fill="none"
      viewBox="0 0 24 24"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M4 19V5m0 14h16M8 15v-4m4 4V7m4 8v-6m4 10V9"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  );
}

function FileMark() {
  return (
    <svg
      aria-hidden="true"
      className="h-7 w-7"
      fill="none"
      viewBox="0 0 24 24"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M13.5 3.75H7.75A1.75 1.75 0 0 0 6 5.5v13A1.75 1.75 0 0 0 7.75 20.25h8.5A1.75 1.75 0 0 0 18 18.5V8.25l-4.5-4.5Z"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="1.5"
      />
      <path
        d="M13.25 4v4.5h4.5M9 13h6m-6 3.25h6"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.5"
      />
    </svg>
  );
}

export default function Home() {
  return (
    <main className="min-h-screen px-5 py-6 sm:px-8 sm:py-8">
      <div className="mx-auto max-w-5xl">
        <header className="flex items-center justify-between border-b border-slate-200 pb-5">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-slate-950 text-white">
              <DataMark />
            </span>
            <span className="text-base font-semibold tracking-tight text-slate-950">
              AI Data Analyst
            </span>
          </div>
          <span className="inline-flex items-center gap-2 rounded-full border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-800">
            <span
              aria-hidden="true"
              className="h-1.5 w-1.5 rounded-full bg-amber-500"
            />
            In development
          </span>
        </header>

        <section className="py-12 sm:py-16">
          <p className="text-sm font-semibold uppercase tracking-[0.16em] text-indigo-700">
            Your data workspace
          </p>
          <h1 className="mt-3 max-w-2xl text-3xl font-semibold tracking-tight text-slate-950 sm:text-5xl">
            Make sense of your data, one question at a time.
          </h1>
          <p className="mt-4 max-w-2xl text-base leading-7 text-slate-600 sm:text-lg">
            Upload a dataset and ask questions in plain language. Your analysis
            workspace is getting ready.
          </p>
        </section>

        <ConnectionStatus />

        <section
          aria-labelledby="workspace-heading"
          className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
        >
          <div className="flex flex-col gap-2 border-b border-slate-100 px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-7">
            <div>
              <h2
                className="text-base font-semibold text-slate-900"
                id="workspace-heading"
              >
                Analysis workspace
              </h2>
              <p className="mt-1 text-sm text-slate-500">
                A preview of where your data work will happen
              </p>
            </div>
            <span className="w-fit rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
              No dataset loaded
            </span>
          </div>

          <div className="px-5 py-8 sm:px-7 sm:py-10">
            <div className="flex flex-col items-center rounded-xl border border-dashed border-slate-300 bg-slate-50/70 px-5 py-10 text-center sm:py-12">
              <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-indigo-50 text-indigo-700">
                <FileMark />
              </span>
              <h3 className="mt-5 text-lg font-semibold text-slate-900">
                Start with a dataset
              </h3>
              <p className="mt-2 max-w-md text-sm leading-6 text-slate-600">
                Your dataset and future analysis results will appear here.
                Uploads are not available yet.
              </p>
              <button
                className="mt-6 inline-flex min-h-11 items-center justify-center rounded-lg bg-slate-200 px-4 text-sm font-medium text-slate-500 disabled:cursor-not-allowed"
                disabled
                type="button"
              >
                Choose a CSV file
                <span className="ml-2 text-xs font-normal">(coming soon)</span>
              </button>
              <p className="mt-3 text-xs text-slate-500">
                CSV upload will be available in a future update.
              </p>
            </div>

            <div className="mt-7">
              <label
                className="text-sm font-medium text-slate-700"
                htmlFor="analysis-question"
              >
                Ask a question about your data
              </label>
              <div className="mt-2 flex flex-col gap-3 sm:flex-row sm:items-end">
                <textarea
                  aria-describedby="question-help"
                  className="min-h-12 flex-1 resize-none rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-500 placeholder:text-slate-400 disabled:cursor-not-allowed"
                  disabled
                  id="analysis-question"
                  placeholder="Questions will be available when dataset analysis is ready."
                  rows={2}
                />
                <button
                  className="min-h-11 rounded-lg bg-indigo-300 px-5 text-sm font-medium text-white disabled:cursor-not-allowed sm:min-w-28"
                  disabled
                  type="button"
                >
                  Ask question
                </button>
              </div>
              <p className="mt-2 text-xs leading-5 text-slate-500" id="question-help">
                Question submission is disabled until dataset upload and backend
                analysis are implemented.
              </p>
            </div>
          </div>
        </section>

        <footer className="py-6 text-center text-xs text-slate-500">
          A work in progress for curious minds and better data questions.
        </footer>
      </div>
    </main>
  );
}
