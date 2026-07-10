import type { NextConfig } from "next";
import { execSync } from "node:child_process";

// O front conversa com a API Flask em 127.0.0.1:5001. Em dev, o rewrite faz o proxy de
// /api pro Flask (mesma origem do dev server, entao o cookie de sessao funciona). Em
// producao o Nginx ja roteia /api pro Flask antes de chegar no Next (rewrite inofensivo la).
const FLASK = process.env.FLASK_ORIGIN ?? "http://127.0.0.1:5001";

// Versao exibida no login: puxa a tag mais proxima via `git describe`, sem precisar editar
// nada na mao a cada deploy (roda de novo toda vez que "next build"/"next dev" sobe).
// Na propria tag vira "v2.0.0"; com commits depois dela vira "v2.0.0+3" (3 commits a mais).
function gitVersion(): string {
  try {
    const raw = execSync("git describe --tags --long", { stdio: ["ignore", "pipe", "ignore"] })
      .toString()
      .trim();
    const m = raw.match(/^v?(\d+\.\d+\.\d+)-(\d+)-g[0-9a-f]+$/);
    if (!m) return raw || "v0.0.0";
    const [, base, count] = m;
    return count === "0" ? `v${base}` : `v${base}+${count}`;
  } catch {
    // Sem tag ainda, sem .git ou git indisponivel no ambiente de build: nao quebra o build.
    return "v0.0.0";
  }
}

const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_APP_VERSION: gitVersion(),
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${FLASK}/api/:path*` }];
  },
  // Sem servidor de otimizacao de imagem (thumbnails vem da API, mesma origem).
  images: { unoptimized: true },
};

export default nextConfig;
