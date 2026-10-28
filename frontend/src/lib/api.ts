export class ApiError extends Error {
  code: string;
  status: number;
  field?: string;
  constructor(status: number, code: string, message: string, field?: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.field = field;
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const s = qs.toString();
  return s ? `${path}?${s}` : path;
}

async function toError(res: Response): Promise<ApiError> {
  let detail: { code?: string; message?: string; field?: string } = {};
  try {
    const body = await res.json();
    detail = typeof body.detail === "object" ? body.detail : { message: String(body.detail ?? "") };
  } catch {
    /* non-JSON error body */
  }
  if (res.status === 401) window.dispatchEvent(new Event("zr:unauthorized"));
  if (detail.code === "db_unavailable") window.dispatchEvent(new Event("zr:db-down"));
  return new ApiError(res.status, detail.code ?? "server_error", detail.message ?? res.statusText, detail.field);
}

export async function api<T = unknown>(
  path: string,
  opts: { method?: string; body?: unknown; query?: Query; signal?: AbortSignal } = {},
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(withQuery(path, opts.query), {
      method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
      credentials: "same-origin",
      headers: {
        "X-Requested-With": "zaho",
        ...(opts.body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network", "Network error");
  }
  if (!res.ok) throw await toError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface UploadHandle {
  promise: Promise<unknown>;
  abort: () => void;
}

/** Streams the raw file body (no multipart) so a 1 GB upload never buffers in the browser. */
export function uploadFile(file: File, onProgress: (loaded: number, total: number) => void): UploadHandle {
  const xhr = new XMLHttpRequest();
  const promise = new Promise((resolve, reject) => {
    xhr.open("PUT", withQuery("/api/datasets/upload", { filename: file.name }));
    xhr.setRequestHeader("X-Requested-With", "zaho");
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => onProgress(e.loaded, e.lengthComputable ? e.total : file.size);
    xhr.onload = () => {
      let body: { detail?: { code?: string; message?: string } } = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* keep empty */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body);
      else reject(new ApiError(xhr.status, body.detail?.code ?? "server_error", body.detail?.message ?? xhr.statusText));
    };
    xhr.onerror = () => reject(new ApiError(0, "network", "Network error"));
    xhr.onabort = () => reject(new ApiError(0, "aborted", "Upload cancelled"));
    xhr.send(file);
  });
  return { promise, abort: () => xhr.abort() };
}

export function downloadUrl(exportId: string): string {
  return `/api/exports/${exportId}/download`;
}
