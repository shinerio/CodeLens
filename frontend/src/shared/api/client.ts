type ApiErrorBody = {
  code?: unknown;
  detail?: unknown;
  message?: unknown;
};

function extractMessage(payload: unknown): string | undefined {
  if (payload === null || typeof payload !== "object") {
    return undefined;
  }
  const body = payload as ApiErrorBody;
  const detail = body.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (typeof body.message === "string") {
    return body.message;
  }
  if (typeof body.code === "string") {
    return body.code;
  }
  return undefined;
}

async function readErrorMessage(response: Response): Promise<string> {
  try {
    const payload: unknown = await response.json();
    const message = extractMessage(payload);
    if (message !== undefined) {
      return message;
    }
  } catch {
    // Fall back to a status-only error message when the body is not JSON.
  }
  return `HTTP ${response.status}`;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
