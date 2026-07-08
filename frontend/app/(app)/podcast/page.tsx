"use client";

import { PodcastUpload } from "@/components/podcast/podcast-upload";
import { PodcastList } from "@/components/podcast/podcast-list";

export default function PodcastPage() {
  return (
    <>
      <section className="page-hero">
        <div className="hero-glows" aria-hidden="true">
          <div className="glow glow-b" style={{ top: "-20%", left: "30%" }} />
        </div>
        <div className="page-hero-inner">
          <div className="page-hero-row">
            <div>
              <p className="eyebrow">Biblioteca</p>
              <h1 className="page-title">Podcasts</h1>
            </div>
          </div>
        </div>
      </section>

      <div className="view-pad space-y-8">
        <PodcastUpload />
        <PodcastList />
      </div>
    </>
  );
}
