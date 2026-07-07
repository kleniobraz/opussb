"use client";

import { useParams } from "next/navigation";
import { HookApproval } from "@/components/podcast/hook-approval";

export default function PodcastAprovacaoPage() {
  const { id } = useParams<{ id: string }>();
  return <HookApproval jobId={id} />;
}
