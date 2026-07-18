export async function onRequest(context) {
  const origin = context.env.API_ORIGIN;
  if (!origin) {
    return new Response("API_ORIGIN is not configured", { status: 500 });
  }

  const incoming = new URL(context.request.url);
  const target = new URL("/iframely", origin);
  target.search = incoming.search;

  const headers = new Headers(context.request.headers);
  headers.delete("host");
  headers.set("x-forwarded-host", incoming.host);
  return fetch(target, { headers, redirect: "manual" });
}
