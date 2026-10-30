const backend = process.env.BACKEND_URL || "http://127.0.0.1:8000";
/** @type {import('next').NextConfig} */
export default {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    // same-origin API: session cookies + CSRF; business logic lives only in the backend
    return [{ source: "/api/v1/:path*", destination: `${backend}/api/v1/:path*` }];
  },
};
