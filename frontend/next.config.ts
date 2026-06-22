import type { NextConfig } from "next";

// O front conversa com a API Flask em 127.0.0.1:5001. Em dev, o rewrite faz o proxy de
// /api pro Flask (mesma origem do dev server, entao o cookie de sessao funciona). Em
// producao o Nginx ja roteia /api pro Flask antes de chegar no Next (rewrite inofensivo la).
const FLASK = process.env.FLASK_ORIGIN ?? "http://127.0.0.1:5001";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${FLASK}/api/:path*` }];
  },
  // Sem servidor de otimizacao de imagem (thumbnails vem da API, mesma origem).
  images: { unoptimized: true },
};

export default nextConfig;
