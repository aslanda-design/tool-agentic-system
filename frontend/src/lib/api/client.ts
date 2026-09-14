// Thin fetch wrapper. Requests go to /api/* which the Vite dev server
// proxies to the backend (see vite.config.ts); in the Docker/nginx build
// nginx proxies the same path — see frontend/nginx.conf.

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData bodies (file uploads) must NOT get an explicit Content-Type —
  // fetch sets multipart/form-data with the correct boundary itself only
  // when the header is left unset. Setting it to application/json here (as
  // this used to do unconditionally) silently strips the boundary, so the
  // backend sees no multipart parts at all and 422s with "file: Field required".
  const isFormData = init?.body instanceof FormData
  const res = await fetch(`/api${path}`, {
    headers: init?.body && !isFormData ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `${path} failed: ${res.status}`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body !== undefined ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', body: body !== undefined ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  postForm: <T>(path: string, form: FormData) => request<T>(path, { method: 'POST', body: form }),
}
