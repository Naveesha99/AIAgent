"use client";

import { useState } from "react";

import { ApiRequestError, getHealthStatus } from "@/lib/api";

type ConnectionState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "success"; message: string }
  | { status: "error"; message: string };

export default function ConnectionStatus() {
  const [connection, setConnection] = useState<ConnectionState>({
    status: "idle",
  });

  async function checkConnection() {
    setConnection({ status: "loading" });

    try {
      const health = await getHealthStatus();
      setConnection({
        status: "success",
        message: `Connected to ${health.service}.`,
      });
    } catch (error) {
      setConnection({
        status: "error",
        message:
          error instanceof ApiRequestError
            ? error.message
            : "The backend connection check failed unexpectedly.",
      });
    }
  }

  return (
    <section
      aria-labelledby="connection-heading"
      className="mt-6 rounded-xl border border-slate-200 bg-white px-5 py-4 shadow-sm sm:px-6"
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2
            className="text-sm font-semibold text-slate-900"
            id="connection-heading"
          >
            Backend connection
          </h2>
          <p
            aria-live="polite"
            className={`mt-1 text-sm ${
              connection.status === "error"
                ? "text-red-700"
                : connection.status === "success"
                  ? "text-emerald-700"
                  : "text-slate-600"
            }`}
            role={connection.status === "error" ? "alert" : "status"}
          >
            {connection.status === "idle" &&
              "Check whether the API is reachable."}
            {connection.status === "loading" && "Checking backend connection…"}
            {connection.status === "success" && connection.message}
            {connection.status === "error" && connection.message}
          </p>
        </div>
        <button
          className="min-h-11 rounded-lg bg-indigo-700 px-4 text-sm font-medium text-white transition-colors hover:bg-indigo-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-700 disabled:cursor-wait disabled:opacity-60"
          disabled={connection.status === "loading"}
          onClick={checkConnection}
          type="button"
        >
          {connection.status === "loading"
            ? "Checking…"
            : "Check backend connection"}
        </button>
      </div>
    </section>
  );
}
