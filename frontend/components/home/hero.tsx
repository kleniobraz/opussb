"use client";

import { useAuth } from "@/providers/auth-provider";
import { firstNameFromEmail, greetingForHour } from "@/lib/format";

function Eq() {
  return (
    <div className="hero-eq">
      {Array.from({ length: 11 }).map((_, i) => (
        <i key={i} />
      ))}
    </div>
  );
}

export function Hero() {
  const { user } = useAuth();
  const name = firstNameFromEmail(user?.email);
  const greeting = greetingForHour(new Date().getHours());

  return (
    <div className="hero-inner">
      <div className="hero-text">
        <p className="eyebrow hero-eyebrow">Central de clipes</p>
        <h1
          className="page-title hero-greeting"
          style={{ fontSize: "clamp(40px,5.6vw,72px)", letterSpacing: "-0.035em" }}
        >
          {greeting},
          <br />
          <span className="grad-text">{name}</span>.
        </h1>
        <p className="page-sub">
          Solte um vídeo longo abaixo e o SB Clips devolve os melhores momentos cortados,
          reformatados e legendados.
        </p>
      </div>
      <div className="hero-visual" aria-hidden="true">
        <div className="hero-clip hero-clip--mid">
          <div className="hero-clip-cover">
            <Eq />
          </div>
          <span className="score-badge score-high hero-clip-badge">8.1</span>
          <span className="hero-clip-dur">0:42</span>
        </div>
        <div className="hero-clip hero-clip--back">
          <div className="hero-clip-cover">
            <Eq />
          </div>
          <span className="score-badge score-mid hero-clip-badge">7.3</span>
          <span className="hero-clip-dur">1:05</span>
        </div>
        <div className="hero-clip hero-clip--front">
          <div className="hero-clip-cover">
            <Eq />
          </div>
          <span className="score-badge score-high hero-clip-badge">9.2</span>
          <span className="hero-clip-dur">0:28</span>
        </div>
      </div>
    </div>
  );
}
